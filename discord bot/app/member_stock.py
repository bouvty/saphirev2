"""Fetch and persist live stock for the independent Discord member supplier."""
import httpx


STOCK_TYPES = ('online', 'offline')


def parse_stock(payload):
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise ValueError('Stock API did not confirm success')
    result = {}
    for kind in STOCK_TYPES:
        value = payload.get(kind)
        if type(value) is not int or value < 0:
            raise ValueError(f'Invalid {kind} stock count')
        result[kind] = value
    return result


async def sync_stock(database, endpoint):
    if not endpoint.startswith('https://'):
        raise ValueError('Stock URL must use HTTPS')
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.get(endpoint)
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError('Stock API redirected unexpectedly')
        try:
            stock = parse_stock(response.json())
        except ValueError as exc:
            raise ValueError('Stock API returned invalid inventory JSON') from exc
    # Both values are validated before touching the database, and committed atomically.
    async with database.pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""INSERT INTO public.discord_member_stock(member_type,available,refreshed_at)
                VALUES($1,$2,now())
                ON CONFLICT(member_type) DO UPDATE SET
                    available=EXCLUDED.available, refreshed_at=EXCLUDED.refreshed_at""",
                [(kind, stock[kind]) for kind in STOCK_TYPES])
    return stock
