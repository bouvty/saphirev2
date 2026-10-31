import hashlib, secrets, urllib.parse, html
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
from . import db
from .config import settings
from .auth_v2 import router as auth_v2_router, callback as auth_v2_callback, start as auth_v2_start
from .registration_links import create_registration_link
from .auth_log_filter import install as install_auth_log_filter
install_auth_log_filter()

@asynccontextmanager
async def lifespan(app):
    await db.start()
    yield
    await db.stop()

app = FastAPI(title='Saphire API', lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(auth_v2_router)

def digest(raw): return hashlib.sha256((settings.oauth_state_secret+raw).encode()).hexdigest()
def page(title,body):
    return HTMLResponse(f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
    <style>body{{background:#05070b;color:#d9eafa;font:16px system-ui;display:grid;place-items:center;min-height:90vh}}main{{background:#101923;padding:40px;border-radius:20px;max-width:560px;text-align:center}}a{{color:#8dd1ff}}</style></head><body><main><h1>{html.escape(title)}</h1><p>{body}</p></main></body></html>''')

@app.get('/health')
async def health(): return {'ok':True}

@app.get('/auth/start')
async def auth_start(discord_id: int, token: str):
    import hmac
    expected=hmac.new(settings.bot_internal_token.encode(),str(discord_id).encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token,expected): raise HTTPException(403)
    url=await create_registration_link(discord_id)
    return await auth_v2_start(url.rsplit('/',1)[-1])

@app.get('/auth/callback')
async def callback(code: str = '', state: str = '', error: str = '', iss: str = ''):
    if state.startswith('v2_'):
        return await auth_v2_callback(code=code,state=state,error=error,iss=iss)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow("select * from oauth_states where state_hash=$1 and consumed_at is null and expires_at>now() for update",digest(state))
            if not row: raise HTTPException(400,'Expired or invalid auth session')
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.post('https://discord.com/api/oauth2/token',data={'client_id':settings.discord_client_id,'client_secret':settings.discord_client_secret,'grant_type':'authorization_code','code':code,'redirect_uri':settings.public_base_url.rstrip('/')+'/auth/callback'})
                if r.status_code != 200: raise HTTPException(400,'Authentication failed')
                r=await client.get('https://discord.com/api/users/@me',headers={'Authorization':'Bearer '+r.json()['access_token']})
                if r.status_code != 200 or int(r.json()['id'])!=row['discord_id']: raise HTTPException(403,'Discord account mismatch')
            await conn.execute('update oauth_states set consumed_at=now() where state_hash=$1',digest(state))
            await conn.execute('insert into accounts(discord_id,oauth_verified) values($1,true) on conflict(discord_id) do update set oauth_verified=true',row['discord_id'])
    return page('Authentication complete','You are registered with Saphire. You may close this tab and return to Discord.')

@app.get('/discord/{ticket_id}')
async def transcript(ticket_id:int, request:Request):
    raise HTTPException(403,'Staff-authenticated transcript portal is not enabled yet')


@app.get('/internal/tickets/{ticket_id}')
async def internal_ticket(ticket_id: int, request: Request):
    """Service-to-service access only. Website MUST authenticate viewer before calling."""
    import hmac
    expected='Bearer '+settings.bot_internal_token
    if not settings.bot_internal_token or not hmac.compare_digest(request.headers.get('authorization',''), expected):
        raise HTTPException(403, 'Forbidden')
    async with db.pool.acquire() as conn:
        row=await conn.fetchrow('select * from tickets where id=$1', ticket_id)
        if not row or row['status']!='closed':
            raise HTTPException(404, 'Archived ticket not found')
        messages=await conn.fetch('select * from ticket_messages where ticket_id=$1 order by created_at,id',ticket_id)
    details=row['details'] if isinstance(row['details'],dict) else {}
    out=[]
    for m in messages:
        meta=m['metadata'] if isinstance(m['metadata'],dict) else {}
        out.append({'id':str(m['id']), 'author_id':str(m['author_id']),
            'username':meta.get('username') or m['author_name'],
            'display_name':meta.get('display_name') or m['author_name'],
            'avatar':meta.get('avatar'), 'content':m['content'],
            'created_at':m['created_at'].isoformat(),
            'embeds':meta.get('embeds',[]),'components':meta.get('components',[])})
    return {'id':str(row['id']),'title':row['kind'].replace('-',' ').title(),
        'type':row['kind'],'status':row['status'], 'created_at':row['created_at'].isoformat(),
        'owner_id':str(row['discord_id']),'fields':details,'messages':out}
