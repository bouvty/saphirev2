import asyncpg
from .config import settings
pool=None
async def start():
    global pool
    pool=await asyncpg.create_pool(settings.database_url,min_size=1,max_size=8)
async def stop():
    global pool
    if pool:
        await pool.close()
        pool=None
