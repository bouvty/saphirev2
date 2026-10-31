"""Top4SMM integration. Provider credentials and wholesale rates are server-only."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
import logging
import httpx
from .config import settings

log = logging.getLogger('saphire.provider')

class ProviderError(Exception):
    """API failure safe to display to staff without leaking the secret key."""

# Top4SMM's own API (api.php): GET ?key=...&act=...
# Keep the provider's documented field names unchanged.
ACTIONS = {'services':'services', 'balance':'balance', 'new_order':'new_order', 'order_info':'order_info'}
PARAMETERS = {}
# GET query strings contain the private API key. Disable httpx request URL INFO logs.
logging.getLogger('httpx').setLevel(logging.WARNING)

def endpoint():
    url = settings.top4smm_api_url.strip()
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError('TOP4SMM_API_URL must be a plain HTTPS API endpoint from your provider dashboard')
    # Never allow arbitrary servers to receive the merchant API key.
    if parsed.hostname not in ('top4smm.com', 'www.top4smm.com'):
        raise ProviderError('TOP4SMM_API_URL must point to the official top4smm.com domain')
    return url


def _request_data(action, params):
    key = settings.top4smm_api_key.strip()
    if not key or key.lower() in {'replace_me', 'your_api_key', 'your_key'}:
        raise ProviderError('TOP4SMM_API_KEY is missing or still a placeholder')
    action_field = settings.top4smm_action_field.strip().lower()
    if action_field not in {'action', 'act'}:
        raise ProviderError('TOP4SMM_ACTION_FIELD must be action or act')
    data = {'key':key, action_field:ACTIONS.get(action,action)}
    for k,v in params.items():
        new_key = PARAMETERS.get(k,k)
        if new_key in {'key', 'act', 'action'}:
            raise ProviderError('Reserved API parameter')
        data[new_key] = v
    return data


def _response_description(response):
    """Never include response bodies or URLs, which can contain credentials."""
    content_type = response.headers.get('content-type','').split(';')[0].strip().lower()
    content = response.content.lstrip()[:50].lower()
    if 'html' in content_type or content.startswith((b'<!doctype html',b'<html',b'<head',b'<body')):
        return ('Top4SMM returned an HTML page instead of JSON. Confirm TOP4SMM_API_URL '
                'in your Top4SMM API dashboard (HTTP '+str(response.status_code)+').')
    if not response.content.strip():
        return 'Top4SMM returned an empty response (HTTP '+str(response.status_code)+'). Check the API URL and access.'
    return ('Top4SMM returned a non-JSON response (HTTP '+str(response.status_code)+', '
            'Content-Type '+(content_type[:55] or 'unspecified')+'). Check your API URL or API access.')


def _classify_provider_error(error):
    """Return only a fixed safe code: never expose free-form provider responses."""
    if isinstance(error, dict):
        # Common API shapes: {error: {message: ...}} or {error_message: ...}.
        message = ' '.join(str(error.get(k, '')) for k in
                           ('error', 'message', 'error_message', 'description'))
    elif isinstance(error, str):
        message = error
    else:
        return 'unspecified'
    lowered = message.lower()
    if any(term in lowered for term in ('insufficient', 'not enough', 'low balance', 'balance too low')):
        return 'insufficient provider balance'
    if any(term in lowered for term in ('invalid key', 'wrong key', 'api key', 'unauthorized', 'permission')):
        return 'API access or key rejected'
    if 'service' in lowered and any(term in lowered for term in ('invalid', 'not found', 'disabled', 'unavailable')):
        return 'service unavailable or invalid'
    if 'quantity' in lowered or ('count' in lowered and 'min' in lowered):
        return 'quantity outside provider limits'
    if any(term in lowered for term in ('invalid link', 'incorrect link', 'bad link', 'invalid url')):
        return 'target link rejected'
    if any(term in lowered for term in ('parameter', 'required field', 'missing field', 'missing argument')):
        return 'order request parameters rejected'
    if any(term in lowered for term in ('rate limit', 'too many requests', 'try later')):
        return 'provider rate limit'
    return 'unspecified'


async def call(action, **params):
    url = endpoint()
    data = _request_data(action, params)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            response = await client.get(url, params=data, headers={'Accept':'application/json'})
            # A redirect commonly means the endpoint is a login/website route.
            if response.is_redirect:
                raise ProviderError('Top4SMM redirected the API request (HTTP '+str(response.status_code)+'). Confirm TOP4SMM_API_URL.')
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(_response_description(response)) from None
    except httpx.TimeoutException as exc:
        raise ProviderError('Top4SMM request timed out') from None
    except httpx.HTTPStatusError as exc:
        raise ProviderError('Top4SMM returned HTTP '+str(exc.response.status_code)+'. Check API access.') from None
    except httpx.HTTPError as exc:
        raise ProviderError('Top4SMM connection failed: '+type(exc).__name__) from None

    if isinstance(payload, dict) and payload.get('error'):
        # Classify common provider errors without logging or displaying arbitrary
        # provider text. Some APIs echo private credentials in their error payload.
        reason = _classify_provider_error(payload.get('error'))
        log.warning('Top4SMM %s rejected (reason=%s; response redacted)', action, reason)
        raise ProviderError(f'Top4SMM rejected {action} (API error: {reason}). '
                            'This order needs staff review before any retry.')
    if isinstance(payload, dict):
        if 'res' in payload:
            return payload['res']
        return payload
    return payload


def markup_amount():
    amount=Decimal(str(settings.service_markup_usd))
    if not amount.is_finite() or amount < 0 or amount > 100000:
        raise ValueError('SERVICE_MARKUP_USD must be between 0 and 100000')
    return amount


def retail_rate(rate):
    price=Decimal(str(rate))
    if not price.is_finite() or price < 0:
        raise ValueError('Invalid provider rate')
    return (price+markup_amount()).quantize(Decimal('0.000001'),rounding=ROUND_HALF_UP)


def cents_for(quantity,rate):
    return int((Decimal(quantity)*Decimal(str(rate))/1000*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def _field(item,*names,default=None):
    for name in names:
        value=item.get(name)
        if value is not None:return value
    return default


def normalize_service(item):
    if not isinstance(item,dict):raise ValueError('Service entry must be an object')
    identifier=_field(item,'id','service','service_id')
    if identifier is None or not str(identifier).strip():raise ValueError('Missing service ID')
    name=str(_field(item,'name',default='')).strip()
    if not name:raise ValueError('Missing service name')
    rate=Decimal(str(_field(item,'rate','price')))
    if not rate.is_finite() or rate<0:raise ValueError('Invalid provider rate')
    minimum=int(_field(item,'min_order','min'))
    maximum=int(_field(item,'max_order','max'))
    if minimum<1 or maximum<minimum:raise ValueError('Invalid service quantity limits')
    return (str(identifier),name,
            str(_field(item,'category',default='Other') or 'Other'),
            str(_field(item,'description','desc',default='') or ''),
            rate,retail_rate(rate),minimum,maximum)

UPSERT_SQL='''INSERT INTO services
 (id,name,category,description,provider_rate,retail_rate,min_order,max_order)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8)
 ON CONFLICT(id) DO UPDATE SET
 name=EXCLUDED.name,category=EXCLUDED.category,
 description=EXCLUDED.description,provider_rate=EXCLUDED.provider_rate,
 retail_rate=EXCLUDED.retail_rate,min_order=EXCLUDED.min_order,
 max_order=EXCLUDED.max_order,refreshed_at=now()'''

async def sync_services(db):
    items=await call('services')
    if isinstance(items,dict):items=_field(items,'services','data','items')
    if not isinstance(items,list) or not items:
        raise ProviderError('Top4SMM returned no services; existing catalog preserved')
    records=[];ids=set()
    for index,item in enumerate(items):
        try:record=normalize_service(item)
        except (TypeError,ValueError,InvalidOperation) as exc:
            raise ProviderError(f'Top4SMM service #{index+1} uses unexpected fields; catalog preserved') from exc
        if record[0] in ids:raise ProviderError('Top4SMM returned duplicate service IDs; catalog preserved')
        ids.add(record[0]);records.append(record)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(UPSERT_SQL,records)
    return len(records)
