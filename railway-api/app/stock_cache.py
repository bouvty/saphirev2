"""Supplier stock cache: DB is served to clients; one supplier fetch per 10 minutes."""
import asyncio,logging
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException
import httpx
from . import db
router=APIRouter()
log=logging.getLogger('saphire.stock')
SUPPLIER='https://176.100.37.219/api/v2/stock'
INTERVAL=600

async def refresh_once():
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        response=await client.get(SUPPLIER)
        response.raise_for_status()
        data=response.json()
    if not isinstance(data,dict) or data.get('success') is not True:raise ValueError('Supplier did not confirm success')
    values={}
    for kind in ('online','offline'):
        value=data.get(kind+'_members')
        if type(value) is not int or value<0 or value>2147483647:raise ValueError('Supplier invalid '+kind+' count')
        values[kind]=value
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""INSERT INTO public.discord_member_stock(member_type,available,refreshed_at)
            VALUES($1,$2,now()) ON CONFLICT(member_type) DO UPDATE SET
            available=EXCLUDED.available,refreshed_at=EXCLUDED.refreshed_at""",
            [(kind,values[kind]) for kind in ('online','offline')])
    return values

async def schedule():
    while True:
        try:await refresh_once()
        except asyncio.CancelledError:raise
        except Exception:log.exception('Supplier stock refresh failed; cached database records retained')
        await asyncio.sleep(INTERVAL)

@router.get('/members/stock')
async def get_stock():
    async with db.pool.acquire() as conn:
        rows=await conn.fetch("SELECT member_type,available,refreshed_at FROM public.discord_member_stock WHERE member_type=ANY($1::text[])",['online','offline'])
    data={r['member_type']:r for r in rows}
    if any(k not in data for k in ('online','offline')):raise HTTPException(503,'Stock has not been initialized')
    last=min(data[k]['refreshed_at'] for k in ('online','offline'))
    return {'success':True,'online_members':int(data['online']['available']),'offline_members':int(data['offline']['available']),
      'updated_at':last.isoformat(),'next_refresh_at':datetime.fromtimestamp(last.timestamp()+INTERVAL,timezone.utc).isoformat(),
      'stale':(datetime.now(timezone.utc)-last).total_seconds()>INTERVAL*2}
