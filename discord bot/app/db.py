import asyncpg
from .config import settings

pool = None

async def start():
    global pool
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=8)

async def stop():
    if pool: await pool.close()

async def register(user_id: int, referral=None, verified=False):
    async with pool.acquire() as conn:
        return await conn.fetchrow('''insert into accounts(discord_id,referred_by,oauth_verified)
         values($1,$2,$3) on conflict(discord_id) do update
         set oauth_verified=accounts.oauth_verified or excluded.oauth_verified
         returning *''', user_id, referral, verified)

async def account(user_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow('select * from accounts where discord_id=$1', user_id)

async def credit(user_id, cents, reference, kind, actor_id=None):
    if cents <= 0: raise ValueError('Credit must be positive')
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('insert into accounts(discord_id) values($1) on conflict do nothing', user_id)
            row = await conn.fetchrow('''insert into ledger(discord_id,delta_cents,kind,reference,actor_id)
             values($1,$2,$3,$4,$5) on conflict(reference) do nothing returning id''', user_id,cents,kind,reference,actor_id)
            if not row: return False
            await conn.execute('update accounts set balance_cents=balance_cents+$2 where discord_id=$1',user_id,cents)
            return True

async def spend_and_create_order(user_id, service_id, quantity, target, cents, extras):
    async with pool.acquire() as conn:
        async with conn.transaction():
            a=await conn.fetchrow('select balance_cents from accounts where discord_id=$1 for update', user_id)
            if not a or a['balance_cents'] < cents: raise ValueError('Insufficient credit')
            row=await conn.fetchrow('''insert into orders(discord_id,service_id,quantity,target_url,cost_cents,extra)
               values($1,$2,$3,$4,$5,$6::jsonb) returning id''', user_id,service_id,quantity,target,cents,extras)
            await conn.execute('update accounts set balance_cents=balance_cents-$2 where discord_id=$1',user_id,cents)
            await conn.execute('insert into ledger(discord_id,delta_cents,kind,reference) values($1,$2,$3,$4)',user_id,-cents,'order',f'order:{row["id"]}')
            return row['id']

async def refund_order(order_id):
    async with pool.acquire() as conn:
        async with conn.transaction():
            order=await conn.fetchrow('select * from orders where id=$1 for update',order_id)
            if not order or order['status'] not in ('submitting','failed'): return False
            row=await conn.fetchrow('insert into ledger(discord_id,delta_cents,kind,reference) values($1,$2,$3,$4) on conflict do nothing returning id',order['discord_id'],order['cost_cents'],'refund',f'refund:{order_id}')
            if not row: return False
            await conn.execute('update accounts set balance_cents=balance_cents+$2 where discord_id=$1',order['discord_id'],order['cost_cents'])
            await conn.execute("update orders set status='refunded' where id=$1",order_id)
            return True
