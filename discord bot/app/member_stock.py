"""Fetch and persist live stock for the independent Discord member supplier."""
import httpx


STOCK_TYPES = ('online', 'offline')


def _stock_count(value, kind):
    """Accept non-negative integer stock counts, including numeric strings."""
    if isinstance(value, dict):
        for name in ('available', 'stock', 'count', 'quantity', 'total'):
            if name in value:
                return _stock_count(value[name], kind)
    if type(value) is int:
        count = value
    elif isinstance(value, str) and value.strip().isdecimal():
        count = int(value.strip())
    else:
        raise ValueError(f'Invalid {kind} stock count (type={type(value).__name__})')
    if count < 0 or count > 2_147_483_647:
        raise ValueError(f'Invalid {kind} stock count (out of range)')
    return count


def parse_stock(payload):
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise ValueError('Stock API did not confirm success')
    values = payload
    for name in ('data', 'stock', 'inventory'):
        nested = payload.get(name)
        if isinstance(nested, dict) and all(kind in nested for kind in STOCK_TYPES):
            values = nested
            break
    return {kind: _stock_count(values.get(kind), kind) for kind in STOCK_TYPES}


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
