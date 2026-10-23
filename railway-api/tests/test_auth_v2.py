import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from test_recovery import api, db
from app import auth_v2

INVITE='a'*43
USER=123456789012345678

class MemoryPool:
    def __init__(self):
        self.row={'token_hash':auth_v2.digest('invite',INVITE),'discord_id':USER,'referral_code':'friend',
                  'expires_at':datetime.now(timezone.utc)+timedelta(minutes=10),'finished_at':None,'oauth_state_hash':None}
        self.accounts={}
    def acquire(self): return self
    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def fetchrow(self,query,*args):
        sql=' '.join(query.split())
        row=self.row
        if sql.startswith('UPDATE discord_auth_requests SET oauth_state_hash'):
            if row['token_hash']!=args[0] or row['finished_at'] or row['expires_at']<=datetime.now(timezone.utc): return None
            row['oauth_state_hash']=args[1]
            return dict(row)
        if sql.startswith('UPDATE discord_auth_requests SET finished_at'):
            if row['oauth_state_hash']!=args[0] or row['finished_at'] or row['expires_at']<=datetime.now(timezone.utc): return None
            row['finished_at']=datetime.now(timezone.utc)
            row['oauth_state_hash']=None
            return dict(row)
        if sql.startswith('SELECT discord_id,authorized_at'):
            if row.get('success_hash')==args[0] and row.get('authorized_at') and row['success_expires_at']>datetime.now(timezone.utc): return dict(row)
        return None
    async def execute(self,query,*args):
        if 'INSERT INTO accounts' in query: self.accounts[args[0]]={'verified':True,'referral':args[1]}
        if 'SET authorized_at' in query:
            self.row.update(authorized_at=datetime.now(timezone.utc),success_hash=args[1],success_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))

@pytest.fixture
def flow(monkeypatch):
    pool=MemoryPool()
    monkeypatch.setattr(db,'pool',pool)
    return TestClient(api.app),pool

def start(client):
    from urllib.parse import urlsplit,parse_qs
    result=client.get('/auth/v2/discord/'+INVITE,follow_redirects=False)
    assert result.status_code==302
    params=parse_qs(urlsplit(result.headers['location']).query)
    assert params['scope']==['identify']
    return params['state'][0]

def discord(monkeypatch,user=USER,fail=False):
    class Discord:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            if fail: raise httpx.ConnectError('unavailable')
            return httpx.Response(200,json={'access_token':'test-token'})
        async def get(self,*args,**kwargs):return httpx.Response(200,json={'id':str(user)})
    monkeypatch.setattr(auth_v2.httpx,'AsyncClient',lambda **kwargs:Discord())

def test_unissued_and_malformed_links_are_denied(flow):
    client,pool=flow
    for key in ['invalid','b'*43]:
        response=client.get('/auth/v2/discord/'+key,follow_redirects=False)
        assert response.status_code==303 and response.headers['location'].endswith('/denied/')
    assert not pool.accounts

def test_expired_link_cannot_start(flow):
    client,pool=flow
    pool.row['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert client.get('/auth/v2/discord/'+INVITE,follow_redirects=False).status_code==303

def test_wrong_account_is_never_verified(flow,monkeypatch):
    client,pool=flow
    state=start(client)
    discord(monkeypatch,user=USER+1)
    assert client.get('/auth/v2/discord/callback',params={'state':state,'code':'code'}).status_code==403
    assert not pool.accounts and not pool.row.get('success_hash')
    assert client.get('/auth/v2/discord/callback',params={'state':state,'code':'code'}).status_code==400

def test_success_is_server_verified_and_callback_cannot_replay(flow,monkeypatch):
    client,pool=flow
    state=start(client)
    discord(monkeypatch)
    response=client.get('/auth/v2/discord/callback',params={'state':state,'code':'code','iss':'https://discord.com'})
    assert response.status_code==200
    receipt=response.json()['success_key']
    assert receipt!=INVITE and pool.row['success_hash']==auth_v2.digest('success',receipt)
    assert pool.accounts[USER]=={'verified':True,'referral':'friend'}
    assert client.get('/auth/v2/discord/callback',params={'state':state,'code':'code'}).status_code==400
    path='/internal/auth/v2/discord/success/'+receipt
    assert client.get(path).status_code==403
    headers={'Authorization':'Bearer test-only-placeholder'}
    assert client.get(path,headers=headers).json()=={'discord_id':str(USER),'authorized':True}
    pool.row['success_expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert client.get(path,headers=headers).status_code==404
    assert client.get('/auth/v2/discord/'+INVITE,follow_redirects=False).status_code==303

@pytest.mark.parametrize('extra',[{'error':'access_denied'},{'code':'code','iss':'https://attacker.invalid'}])
def test_cancelled_or_wrong_issuer_consumes_state_without_verifying(flow,extra):
    client,pool=flow
    state=start(client)
    assert client.get('/auth/v2/discord/callback',params={'state':state,**extra}).status_code==400
    assert pool.row['finished_at'] and not pool.accounts

def test_provider_outage_cannot_create_success(flow,monkeypatch):
    client,pool=flow
    state=start(client)
    discord(monkeypatch,fail=True)
    assert client.get('/auth/v2/discord/callback',params={'state':state,'code':'code'}).status_code==502
    assert not pool.accounts and not pool.row.get('success_hash')

def test_unknown_or_replaced_state_cannot_authorize(flow):
    client,pool=flow
    old=start(client)
    start(client)
    assert client.get('/auth/v2/discord/callback',params={'state':old,'code':'code'}).status_code==400
    assert not pool.accounts
