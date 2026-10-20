import hashlib,secrets,urllib.parse,hmac
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import RedirectResponse,HTMLResponse
import httpx
from . import db
from .config import settings
@asynccontextmanager
async def lifespan(app):
    await db.start()
    try: yield
    finally: await db.stop()
app=FastAPI(title='Saphire V2 API',docs_url=None,redoc_url=None,lifespan=lifespan)
def state_hash(value):return hashlib.sha256((settings.oauth_state_secret+value).encode()).hexdigest()
@app.get('/health')
async def health():return {'status':'ok'}
@app.get('/auth/start')
async def auth_start(discord_id:int,token:str):
    expected=hmac.new(settings.bot_internal_token.encode(),str(discord_id).encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,token):raise HTTPException(403,'Invalid invitation')
    state=secrets.token_urlsafe(36)
    async with db.pool.acquire() as c:
        await c.execute("INSERT INTO oauth_states(state_hash,discord_id,expires_at) VALUES($1,$2,now()+interval '10 minutes')",state_hash(state),discord_id)
    u='https://discord.com/oauth2/authorize?'+urllib.parse.urlencode({'client_id':settings.discord_client_id,'redirect_uri':settings.public_base_url.rstrip('/')+'/auth/callback','response_type':'code','scope':'identify','state':state})
    return RedirectResponse(u)
@app.get('/auth/callback')
async def callback(code:str,state:str):
    async with db.pool.acquire() as c:
        async with c.transaction():
            row=await c.fetchrow("SELECT discord_id FROM oauth_states WHERE state_hash=$1 AND consumed_at IS NULL AND expires_at>now() FOR UPDATE",state_hash(state))
            if not row:raise HTTPException(400,'Invalid or expired authorization state')
            async with httpx.AsyncClient(timeout=20) as client:
                resp=await client.post('https://discord.com/api/v10/oauth2/token',data={'client_id':settings.discord_client_id,'client_secret':settings.discord_client_secret,'grant_type':'authorization_code','code':code,'redirect_uri':settings.public_base_url.rstrip('/')+'/auth/callback'})
                if not resp.is_success:raise HTTPException(400,'Discord authorization failed')
                token=resp.json().get('access_token')
                ident=await client.get('https://discord.com/api/v10/users/@me',headers={'Authorization':'Bearer '+str(token)})
                if not ident.is_success or str(ident.json().get('id'))!=str(row['discord_id']):raise HTTPException(403,'Discord user mismatch')
            await c.execute('UPDATE oauth_states SET consumed_at=now() WHERE state_hash=$1',state_hash(state))
            await c.execute('INSERT INTO accounts(discord_id,oauth_verified) VALUES($1,true) ON CONFLICT(discord_id) DO UPDATE SET oauth_verified=true',row['discord_id'])
    return HTMLResponse('<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body style="background:#0a0612;color:#fff;font:18px system-ui;display:grid;place-items:center;min-height:90vh"><main style="background:#20112e;border-radius:20px;padding:45px;text-align:center"><h1>Authenticated successfully</h1><p>You can close this tab and return to Discord.</p></main></body></html>')
@app.get('/internal/tickets/{ticket_id}')
async def get_ticket(ticket_id:int,request:Request):
    expected='Bearer '+settings.bot_internal_token
    if not hmac.compare_digest(request.headers.get('authorization',''),expected):raise HTTPException(403)
    async with db.pool.acquire() as c:
        ticket=await c.fetchrow("SELECT * FROM tickets WHERE id=$1 AND status='closed'",ticket_id)
        if not ticket:raise HTTPException(404)
        msgs=await c.fetch('SELECT * FROM ticket_messages WHERE ticket_id=$1 ORDER BY created_at,id',ticket_id)
    def meta(m):
        val=m.get('metadata') if 'metadata' in m else {}
        return val if isinstance(val,dict) else {}
    return {'id':str(ticket['id']),'status':ticket['status'],'type':ticket['kind'],'title':ticket['kind'].replace('-',' ').title(),'owner_id':str(ticket['discord_id']),'fields':ticket['details'],'messages':[{'id':str(m['id']),'author_id':str(m['author_id']),'username':meta(m).get('username',m['author_name']),'display_name':meta(m).get('display_name',m['author_name']),'avatar':meta(m).get('avatar'),'content':m['content'],'embeds':meta(m).get('embeds',[]),'components':meta(m).get('components',[])} for m in msgs]}
