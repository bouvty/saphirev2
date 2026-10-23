"""User-bound Discord registration, separate from staff website sessions."""
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from . import db
from .config import settings

router = APIRouter()
KEY = re.compile(r'^[A-Za-z0-9_-]{43}$')
STATE = re.compile(r'^v2_[A-Za-z0-9_-]{43}$')
PRIVATE = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}

def digest(kind, value):
    return hmac.new(settings.oauth_state_secret.encode(), (kind + ':' + value).encode(), hashlib.sha256).hexdigest()

def denied():
    return RedirectResponse(settings.public_base_url.rstrip('/') + '/auth/v2/discord/denied/', status_code=303, headers=PRIVATE)

@router.get('/auth/v2/discord/callback')
async def callback(code: str = '', state: str = '', error: str = '', iss: str = ''):
    if not STATE.fullmatch(state):
        raise HTTPException(400, 'Invalid registration state')
    # Claim once before contacting Discord, including cancellation and failed exchanges.
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow('''UPDATE discord_auth_requests
            SET finished_at=now(),oauth_state_hash=NULL
            WHERE oauth_state_hash=$1 AND finished_at IS NULL AND expires_at>now()
            RETURNING token_hash,discord_id,referral_code''', digest('state', state))
    if not row or error or not code or len(code)>2048 or (iss and iss!='https://discord.com'):
        raise HTTPException(400, 'Registration denied or expired')
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post('https://discord.com/api/v10/oauth2/token', data={
                'client_id': settings.discord_client_id,
                'client_secret': settings.discord_client_secret,
                'grant_type': 'authorization_code', 'code': code,
                'redirect_uri': settings.public_base_url.rstrip('/') + '/auth/callback',
            })
            if not response.is_success:
                raise HTTPException(400, 'Discord authorization failed')
            access_token = response.json().get('access_token')
            if not isinstance(access_token, str) or not access_token:
                raise HTTPException(400, 'Discord authorization failed')
            profile = await client.get('https://discord.com/api/v10/users/@me', headers={'Authorization': 'Bearer '+access_token})
            if not profile.is_success or str(profile.json().get('id')) != str(row['discord_id']):
                raise HTTPException(403, 'Discord account does not match this link')
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(502, 'Discord authorization unavailable') from None
    receipt = secrets.token_urlsafe(32)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('''INSERT INTO accounts(discord_id,oauth_verified,referred_by) VALUES($1,true,$2)
                ON CONFLICT(discord_id) DO UPDATE SET oauth_verified=true,
                referred_by=COALESCE(accounts.referred_by,excluded.referred_by)''', row['discord_id'], row['referral_code'])
            await conn.execute('''UPDATE discord_auth_requests SET authorized_at=now(),success_hash=$2,
                success_expires_at=now()+interval '5 minutes' WHERE token_hash=$1''',row['token_hash'],digest('success',receipt))
    return JSONResponse({'success_key': receipt}, headers=PRIVATE)

@router.get('/internal/auth/v2/discord/success/{key}')
async def success(key: str, request: Request):
    if not hmac.compare_digest(request.headers.get('authorization',''), 'Bearer '+settings.bot_internal_token):
        raise HTTPException(403)
    if not KEY.fullmatch(key):
        raise HTTPException(404)
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow('''SELECT discord_id,authorized_at FROM discord_auth_requests
            WHERE success_hash=$1 AND authorized_at IS NOT NULL AND success_expires_at>now()''',digest('success',key))
    if not row:
        raise HTTPException(404, 'Verification receipt expired or invalid')
    return JSONResponse({'discord_id':str(row['discord_id']),'authorized':True},headers=PRIVATE)

@router.get('/auth/v2/discord/{key}')
async def start(key: str):
    if not KEY.fullmatch(key):
        return denied()
    state = 'v2_' + secrets.token_urlsafe(32)
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow('''UPDATE discord_auth_requests SET oauth_state_hash=$2
            WHERE token_hash=$1 AND finished_at IS NULL AND expires_at>now() RETURNING discord_id''',
            digest('invite',key),digest('state',state))
    if not row:
        return denied()
    params = {'client_id':settings.discord_client_id,'redirect_uri':settings.public_base_url.rstrip('/')+'/auth/callback',
              'response_type':'code','scope':'identify','state':state,'prompt':'consent'}
    return RedirectResponse('https://discord.com/oauth2/authorize?'+urlencode(params),status_code=302,headers=PRIVATE)
