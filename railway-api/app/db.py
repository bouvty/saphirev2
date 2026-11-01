import asyncpg
import json
import os
import hashlib
import logging
from cryptography.fernet import Fernet
from .config import settings
pool=None
async def configure_connection(connection):
    for name in ('json','jsonb'):
        await connection.set_type_codec(name,schema='pg_catalog',encoder=json.dumps,decoder=json.loads)
async def start():
    global pool
    # Avoid cached prepared statements becoming invalid after live Supabase schema migrations.
    pool=await asyncpg.create_pool(settings.database_url,min_size=1,max_size=8,init=configure_connection,statement_cache_size=0)
    await backfill_visible_shoppex_keys()
async def stop():
    global pool
    if pool:
        await pool.close()
        pool=None

async def backfill_visible_shoppex_keys():
    """Restore viewable key from encrypted ciphertext for administrator-only records."""
    secret=os.getenv('SHOPPEX_KEY_ENCRYPTION_KEY','')
    if not secret:
        logging.warning('Shoppex readable key backfill skipped: encryption key unavailable')
        return
    try:
        cipher=Fernet(secret.encode())
        async with pool.acquire() as conn:
            rows=await conn.fetch(
                "SELECT id,key_hash,key_ciphertext FROM public.member_redeem_keys "
                "WHERE key IS NULL AND key_ciphertext IS NOT NULL")
            updated=0
            for row in rows:
                try:
                    plain=cipher.decrypt(row['key_ciphertext'].encode()).decode('utf-8')
                    if hashlib.sha256(plain.encode()).hexdigest()!=row['key_hash']:
                        raise ValueError('Key hash mismatch')
                    await conn.execute(
                        "UPDATE public.member_redeem_keys SET key=$2 WHERE id=$1 AND key IS NULL",
                        row['id'],plain)
                    updated+=1
                except Exception:
                    logging.exception('Could not restore visible Shoppex key for record %s',row['id'])
            logging.info('Shoppex readable key backfill complete: %s records',updated)
    except Exception:
        logging.exception('Shoppex readable key backfill failed; continuing API startup')
