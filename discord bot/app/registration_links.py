"""Create opaque, short-lived links for the Discord account requesting registration."""
import hashlib
import hmac
import secrets
from . import db
from .config import settings

async def create_registration_link(discord_id: int, referral=None):
    key=secrets.token_urlsafe(32)
    digest=hmac.new(settings.oauth_state_secret.encode(),('invite:'+key).encode(),hashlib.sha256).hexdigest()
    async with db.pool.acquire() as conn:
        await conn.execute('''INSERT INTO discord_auth_requests(token_hash,discord_id,referral_code)
            VALUES($1,$2,$3)''',digest,discord_id,referral)
    return settings.public_base_url.rstrip('/')+'/auth/v2/discord/'+key
