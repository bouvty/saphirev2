import asyncpg
import json
from .config import settings
pool=None
async def configure_connection(connection):
    for name in ('json','jsonb'):
        await connection.set_type_codec(name,schema='pg_catalog',encoder=json.dumps,decoder=json.loads)
async def start():
    global pool
    pool=await asyncpg.create_pool(settings.database_url,min_size=1,max_size=8,init=configure_connection)
async def stop():
    global pool
    if pool:
        await pool.close()
        pool=None
