import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

for key in ('DATABASE_URL','DISCORD_CLIENT_ID','DISCORD_CLIENT_SECRET','BOT_INTERNAL_TOKEN','OAUTH_STATE_SECRET'):
    os.environ[key] = 'test-only-placeholder'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import api, db

class Pool:
    def acquire(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def fetchval(self,query): return None
    async def fetchrow(self,query,*args):
        return {'id':1,'status':'closed','kind':'support','discord_id':123,'details':{'subject':'Example'}}
    async def fetch(self,query,*args):
        return [{'id':1,'author_id':123,'author_name':'Example','content':'Hello','metadata':{'display_name':'Member','embeds':[{'title':'Example'}]}}]

client=TestClient(api.app)

def test_health_checks_database(monkeypatch):
    monkeypatch.setattr(db,'pool',Pool())
    assert client.get('/health').json()=={'status':'ok'}

def test_health_fails_when_database_unavailable(monkeypatch):
    monkeypatch.setattr(db,'pool',None)
    assert client.get('/health').status_code==503

def test_ticket_requires_internal_token():
    assert client.get('/internal/tickets/1').status_code==403

def test_ticket_preserves_rich_metadata(monkeypatch):
    monkeypatch.setattr(db,'pool',Pool())
    response=client.get('/internal/tickets/1',headers={'Authorization':'Bearer test-only-placeholder'})
    assert response.status_code==200
    result=response.json()
    assert result['fields']=={'subject':'Example'}
    assert result['messages'][0]['display_name']=='Member'
    assert result['messages'][0]['embeds']==[{'title':'Example'}]

def test_registration_rejects_forged_invitation():
    assert client.get('/auth/start?discord_id=123&token=invalid').status_code==403

def test_connection_installs_json_codecs():
    import asyncio
    conn=AsyncMock()
    asyncio.run(db.configure_connection(conn))
    assert [call.args[0] for call in conn.set_type_codec.await_args_list]==['json','jsonb']
