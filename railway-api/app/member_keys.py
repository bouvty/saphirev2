"""Shoppex signed dynamic fulfillment and one-time member key redemption.

Outbound delivery diagnostics use masked keys and a per-delivery trace ID.
"""
import os,hmac,hashlib,json,re,secrets,time,logging
from datetime import timezone
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel,Field
from cryptography.fernet import Fernet
from . import db
router=APIRouter()
log=logging.getLogger("saphire.members.keys")

def delivery_error(status, code, explanation, *, field=None):
    """Specific Shoppex-safe failure response, without exposing credentials or customer data."""
    detail={'code':code,'message':explanation}
    if field: detail['field']=field
    raise HTTPException(status_code=status,detail=detail)


def require_internal(req):
    expected=os.getenv('SAPHIRE_V2_API_KEY','')
    if not expected or not hmac.compare_digest(req.headers.get('x-api-key',''),expected):
        raise HTTPException(401,'API key required')

def verify_shoppex(req,raw,kind):
    secret=os.getenv('SHOPPEX_'+kind.upper()+'_SIGNING_SECRET','')
    signature=req.headers.get('x-shoppex-signature-v2','')
    delivery=req.headers.get('x-shoppex-delivery-id','')
    timestamp=req.headers.get('x-shoppex-timestamp','')
    if not secret or not delivery or not timestamp.isdecimal() or abs(time.time()-int(timestamp))>300:
        raise HTTPException(401,'Invalid Shoppex delivery authentication')
    # Shoppex signs deliveryId.timestamp.rawBody; never trust unsigned product or amount.
    expected=hmac.new(secret.encode(),delivery.encode()+b'.'+timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    if not re.fullmatch(r'v1,t=\d+,h=[a-fA-F0-9]{64}',signature):
        raise HTTPException(401,'Invalid Shoppex signature')
    fields=dict(part.split('=',1) for part in signature.split(',')[1:] if '=' in part)
    if fields.get('t')!=timestamp or not hmac.compare_digest(expected,fields.get('h','').lower()):
        raise HTTPException(401,'Invalid Shoppex signature')
    return delivery

def generate_redeem_key():
    alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'SAPH-'+'-'.join(''.join(secrets.choice(alphabet) for _ in range(5)) for _ in range(3))

def make_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()

def shoppex_key_response(kind, delivery_id, key, existing):
    """Make delivery bytes explicit and emit a safe response diagnostic to stdout."""
    body = key.encode('ascii')
    trace = hashlib.sha256(delivery_id.encode()).hexdigest()[:16]
    print(json.dumps({
        'event': 'SHOPPEX_OUTBOUND_RESPONSE',
        'trace': trace,
        'kind': kind,
        'status': 200,
        'body_bytes': len(body),
        'content_type': 'text/plain',
        'delivery_format': 'plain-key',
        'key_mask': key[:5] + '...' + key[-4:],
        'existing': existing
    }), flush=True)
    return Response(content=body, status_code=200, media_type='text/plain',
                    headers={'Cache-Control':'no-store',
                             'Content-Length':str(len(body)),
                             'X-Saphire-Trace':trace,
                             'X-Saphire-Delivery':'existing' if existing else 'issued'})

def shoppex_log_payload(kind, delivery_id, payload, raw):
    """Audit authenticated Shoppex payload fields, withholding customer personal data."""
    sensitive = ('email', 'ip', 'location', 'address', 'phone', 'name', 'token', 'secret', 'signature', 'authorization')
    def redact(obj, depth=0):
        if depth >= 8:
            return '[MAX_DEPTH]'
        if isinstance(obj, dict):
            return {str(k): '[REDACTED]' if any(word in str(k).lower() for word in sensitive)
                    else redact(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [redact(v, depth + 1) for v in obj[:30]]
        if isinstance(obj, str) and len(obj) > 300:
            return obj[:300] + '[TRUNCATED]'
        return obj
    print(json.dumps({
        'event': 'SHOPPEX_VERIFIED_REQUEST',
        'trace': hashlib.sha256(delivery_id.encode()).hexdigest()[:16],
        'kind': kind,
        'request_bytes': len(raw),
        'payload': redact(payload),
    }, separators=(',', ':'), default=str), flush=True)

@router.post('/resellers/members/order/{kind}')
async def shoppex_delivery(kind:str,request:Request):
    if kind not in ('online','offline'):raise HTTPException(404,'Unknown member product')
    raw=await request.body()
    if len(raw)>32768:raise HTTPException(413,'Payload too large')
    delivery_id=verify_shoppex(request,raw,kind)
    try:payload=json.loads(raw)
    except ValueError:delivery_error(400,'JSON_INVALID','Request body must be valid JSON.')
    cipher_secret=os.getenv('SHOPPEX_KEY_ENCRYPTION_KEY','')
    if not cipher_secret:raise HTTPException(503,'Shoppex delivery encryption is not configured')
    try:cipher=Fernet(cipher_secret.encode())
    except Exception:raise HTTPException(503,'Shoppex delivery encryption is invalid')
    # Exact Shoppex paid order/product mapping must be configured and verified against test delivery.
    if not isinstance(payload,dict):
        delivery_error(400,'PAYLOAD_TYPE_INVALID','Expected JSON object with order, product and quantity.')
    shoppex_log_payload(kind, delivery_id, payload, raw)
    order_value=payload.get('order_id') or payload.get('order')
    product_value=payload.get('product_id') or payload.get('product')
    if isinstance(order_value,dict):
        order_value=order_value.get('id') or order_value.get('order_id')
    if isinstance(product_value,dict):
        product_value=product_value.get('id') or product_value.get('product_id')
    order=str(order_value or '')
    product=str(product_value or '').removeprefix('#')
    unit_count=payload.get('quantity')
    if isinstance(unit_count,str) and unit_count.isdecimal():
        unit_count=int(unit_count)
    # Log field presence/types only; never log secrets, raw requests or customer details.
    log.info('Shoppex delivery fields kind=%s keys=%s order_present=%s product_present=%s quantity_type=%s',
             kind,sorted(payload.keys()),bool(order),bool(product),type(unit_count).__name__)
    mapping=os.getenv('SHOPPEX_ONLINE_PRODUCT_ID' if kind=='online' else 'SHOPPEX_OFFLINE_PRODUCT_ID','').strip()
    raw_units=os.getenv('SHOPPEX_ONLINE_MEMBERS_PER_UNIT' if kind=='online' else 'SHOPPEX_OFFLINE_MEMBERS_PER_UNIT','')
    if not mapping:
        delivery_error(503,'PRODUCT_NOT_CONFIGURED',f'SHOPPEX_{kind.upper()}_PRODUCT_ID is missing in Railway.')
    if not raw_units.isdecimal() or int(raw_units)<1:
        delivery_error(503,'UNITS_NOT_CONFIGURED',f'SHOPPEX_{kind.upper()}_MEMBERS_PER_UNIT must be a positive integer.')
    unit_size=int(raw_units)
    # Shoppex dynamic delivery may omit an order ID from the JSON body.
    # After validating HMAC, the signed delivery ID provides an idempotent fallback.
    if not order:
        order='delivery:'+hashlib.sha256(delivery_id.encode()).hexdigest()
        log.info('Shoppex %s body omitted order ID; using authenticated delivery identifier',kind)
    if len(order)>128:
        delivery_error(400,'ORDER_ID_INVALID','Order identifier exceeds 128 characters.',field='order')
    if not product or len(product)>128:
        delivery_error(400,'PRODUCT_ID_MISSING','Missing product ID: expected product/product_id in the signed delivery JSON.',field='product')
    # Shoppex product API exposes an internal id and a storefront uniqid.
    # Configure the internal id as canonical; accept the known public uniqid for compatibility.
    known_public_ids={'offline':'01a19330-dc05-73d9-a1a3-ca7d7e7425e8',
                      'online':'01a1932e-25e2-7a22-a94e-39d1b4ad1bfe'}
    matched=hmac.compare_digest(mapping,product) or (hmac.compare_digest(product,known_public_ids[kind]) and mapping in {
        'offline':'01a19330-dc09-76b4-8102-ce09e0b2b49d',
        'online':'01a1932e-25e6-7d39-b303-d9edaeb85b82'}.values())
    if not matched:
        log.warning('Shoppex %s dynamic delivery rejected due to product ID mismatch',kind)
        delivery_error(400,'PRODUCT_ID_MISMATCH',f'{kind.title()} product ID does not match the configured Shoppex product ID in Railway. Check the full product UUID, not its shortened suffix.',field='product')
    if type(unit_count) is not int or not 1<=unit_count<=100000:
        delivery_error(400,'QUANTITY_INVALID',f'Quantity must be an integer between 1 and 100000; received type {type(unit_count).__name__}.',field='quantity')
    amount=unit_count*unit_size
    if amount<1 or amount>100000:
        delivery_error(400,'AMOUNT_OUT_OF_RANGE','Calculated member quantity must be between 1 and 100000.',field='quantity')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('SELECT * FROM public.member_redeem_keys WHERE fulfillment_id=$1 OR (shoppex_order_id=$2 AND shoppex_product_id=$3) FOR UPDATE',delivery_id,order,product)
            if row:
                # Repeated deliveries cannot safely reconstruct the plaintext key.
                # Shoppex should accept pending fulfillment for a duplicate delivery.
                if row['key_ciphertext']:
                    saved=cipher.decrypt(row['key_ciphertext'].encode()).decode()
                    log.info('SHOPPEX_RESPONSE kind=%s status=200 delivery_ref=%s existing=true format=json content_type=application/json fields=deliver key_mask=%s body_bytes=%s',kind,hashlib.sha256(delivery_id.encode()).hexdigest()[:16],saved[:5]+'…'+saved[-4:],len(json.dumps({'deliver':saved}).encode()))
                    return shoppex_key_response(kind, delivery_id, saved, True)
                return {'status':'pending','message':'Existing fulfillment requires staff recovery'}
            key=generate_redeem_key()
            await conn.execute("""INSERT INTO public.member_redeem_keys(key_hash,amount,member_type,shoppex_order_id,shoppex_product_id,fulfillment_id,key_ciphertext)
                VALUES($1,$2,$3,$4,$5,$6,$7)""",make_hash(key),amount,kind,order,product,delivery_id,cipher.encrypt(key.encode()).decode())
    log.info('SHOPPEX_RESPONSE kind=%s status=200 delivery_ref=%s existing=false format=json content_type=application/json fields=deliver key_mask=%s body_bytes=%s',kind,hashlib.sha256(delivery_id.encode()).hexdigest()[:16],key[:5]+'…'+key[-4:],len(json.dumps({'deliver':key}).encode()))
    return shoppex_key_response(kind, delivery_id, key, False)

class RedeemBody(BaseModel):
    key:str=Field(min_length=15,max_length=150)
    discord_id:str=Field(pattern=r'^\d{15,22}$')

@router.post('/members/order/redeem')
async def redeem(payload:RedeemBody,request:Request):
    require_internal(request)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',make_hash(payload.key))
            if not row or row['redeemed'] or row['status']!='issued':
                return {'valid':False,'message':'Invalid or redeemed key'}
            await conn.execute("UPDATE public.member_redeem_keys SET redeemed=true,redeemed_at=now(),redeemed_by=$2,status='reserved' WHERE id=$1",row['id'],payload.discord_id)
    return {'valid':True,'key':payload.key,'amount':row['amount'],'type':row['member_type'],'created_at':row['created_at'].isoformat()}

class OrderBody(BaseModel):
    key:str=Field(min_length=15,max_length=150)
    discord_id:str=Field(pattern=r'^\d{15,22}

@router.post('/members/order')
async def order(payload:OrderBody, request:Request):
    """Convert an exclusively reserved Shoppex key into one Discorium order."""
    require_internal(request)
    import httpx
    provider_token=os.getenv('DISCORIUM_API_KEY','').strip()
    if not provider_token:
        raise HTTPException(503,'Discorium provider key not configured')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow(
                'SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',
                make_hash(payload.key))
            if not row or row['redeemed_by']!=payload.discord_id or row['status']!='reserved':
                raise HTTPException(409,'Key is not reserved to this user or already submitted')
            if row['member_type'] not in ('online','offline') or not 1<=row['amount']<=100000:
                raise HTTPException(409,'Invalid stored Shoppex member order')
            # Stable ID is saved before sending and never regenerated on a retry.
            order_id='dsc-'+secrets.token_hex(5)
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='processing',provider_order_id=$2 WHERE id=$1",
                row['id'],order_id)
    public_details={'id':row['id'],'type':row['member_type'],'amount':row['amount'],'order_id':order_id}
    log.info('DISCORIUM_ORDER_STARTED %s',json.dumps(public_details))
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response=await client.post(
                'https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members',
                headers={'Authorization':'Bearer '+provider_token,
                         'Content-Type':'application/json','Accept':'application/json'},
                json={'order_id':order_id,'members_type':row['member_type'],
                      'quantity':row['amount']})
        data=response.json()
        if response.status_code!=200 or not isinstance(data,dict) or data.get('success') is not True:
            # A negative response might be definitive, but a network failure is not.
            raise ValueError('Provider did not confirm success (HTTP %d)'%response.status_code)
        if (data.get('order_id')!=order_id or data.get('members_type')!=row['member_type']
            or type(data.get('quantity')) is not int or data['quantity']!=row['amount']
            or not isinstance(data.get('key'),str) or not data['key'].strip()):
            raise ValueError('Provider response order/type/quantity/key mismatch')
    except Exception:
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='needs_review' WHERE id=$1 AND status='processing'",
                row['id'])
        log.exception('DISCORIUM_ORDER_UNCONFIRMED internal_id=%s order_id=%s',row['id'],order_id)
        raise HTTPException(502,'Provider outcome unconfirmed; contact support. Do not retry.')
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.member_redeem_keys SET status='completed',provider_key=$2,provider_response=$3 WHERE id=$1 AND status='processing'",
            row['id'],data['key'],json.dumps({'success':True,'order_id':order_id,
            'members_type':row['member_type'],'quantity':row['amount'],
            'credits_remaining':data.get('credits_remaining')}))
    log.info('DISCORIUM_ORDER_COMPLETED internal_id=%s order_id=%s type=%s quantity=%s provider_key_present=true',
             row['id'],order_id,row['member_type'],row['amount'])
    return {'success':True,'type':row['member_type'],'amount':row['amount'],
            'order_id':order_id,'provider_key':data['key'],
            'credits_remaining':data.get('credits_remaining')}
)

@router.post('/members/order')
async def order(payload:OrderBody, request:Request):
    """Convert an exclusively reserved Shoppex key into one Discorium order."""
    require_internal(request)
    import httpx
    provider_token=os.getenv('DISCORIUM_API_KEY','').strip()
    if not provider_token:
        raise HTTPException(503,'Discorium provider key not configured')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow(
                'SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',
                make_hash(payload.key))
            if not row or row['redeemed_by']!=payload.discord_id or row['status']!='reserved':
                raise HTTPException(409,'Key is not reserved to this user or already submitted')
            if row['member_type'] not in ('online','offline') or not 1<=row['amount']<=100000:
                raise HTTPException(409,'Invalid stored Shoppex member order')
            # Stable ID is saved before sending and never regenerated on a retry.
            order_id='dsc-'+secrets.token_hex(5)
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='processing',provider_order_id=$2 WHERE id=$1",
                row['id'],order_id)
    public_details={'id':row['id'],'type':row['member_type'],'amount':row['amount'],'order_id':order_id}
    log.info('DISCORIUM_ORDER_STARTED %s',json.dumps(public_details))
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response=await client.post(
                'https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members',
                headers={'Authorization':'Bearer '+provider_token,
                         'Content-Type':'application/json','Accept':'application/json'},
                json={'order_id':order_id,'members_type':row['member_type'],
                      'quantity':row['amount']})
        data=response.json()
        if response.status_code!=200 or not isinstance(data,dict) or data.get('success') is not True:
            # A negative response might be definitive, but a network failure is not.
            raise ValueError('Provider did not confirm success (HTTP %d)'%response.status_code)
        if (data.get('order_id')!=order_id or data.get('members_type')!=row['member_type']
            or type(data.get('quantity')) is not int or data['quantity']!=row['amount']
            or not isinstance(data.get('key'),str) or not data['key'].strip()):
            raise ValueError('Provider response order/type/quantity/key mismatch')
    except Exception:
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='needs_review' WHERE id=$1 AND status='processing'",
                row['id'])
        log.exception('DISCORIUM_ORDER_UNCONFIRMED internal_id=%s order_id=%s',row['id'],order_id)
        raise HTTPException(502,'Provider outcome unconfirmed; contact support. Do not retry.')
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.member_redeem_keys SET status='completed',provider_key=$2,provider_response=$3 WHERE id=$1 AND status='processing'",
            row['id'],data['key'],json.dumps({'success':True,'order_id':order_id,
            'members_type':row['member_type'],'quantity':row['amount'],
            'credits_remaining':data.get('credits_remaining')}))
    log.info('DISCORIUM_ORDER_COMPLETED internal_id=%s order_id=%s type=%s quantity=%s provider_key_present=true',
             row['id'],order_id,row['member_type'],row['amount'])
    return {'success':True,'type':row['member_type'],'amount':row['amount'],
            'order_id':order_id,'provider_key':data['key'],
            'credits_remaining':data.get('credits_remaining')}
)

@router.post('/members/order')
async def order(payload:OrderBody, request:Request):
    """Convert an exclusively reserved Shoppex key into one Discorium order."""
    require_internal(request)
    import httpx
    provider_token=os.getenv('DISCORIUM_API_KEY','').strip()
    if not provider_token:
        raise HTTPException(503,'Discorium provider key not configured')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow(
                'SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',
                make_hash(payload.key))
            if not row or row['redeemed_by']!=payload.discord_id or row['status']!='reserved':
                raise HTTPException(409,'Key is not reserved to this user or already submitted')
            if row['member_type'] not in ('online','offline') or not 1<=row['amount']<=100000:
                raise HTTPException(409,'Invalid stored Shoppex member order')
            # Stable ID is saved before sending and never regenerated on a retry.
            order_id='dsc-'+secrets.token_hex(5)
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='processing',provider_order_id=$2 WHERE id=$1",
                row['id'],order_id)
    public_details={'id':row['id'],'type':row['member_type'],'amount':row['amount'],'order_id':order_id}
    log.info('DISCORIUM_ORDER_STARTED %s',json.dumps(public_details))
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response=await client.post(
                'https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members',
                headers={'Authorization':'Bearer '+provider_token,
                         'Content-Type':'application/json','Accept':'application/json'},
                json={'order_id':order_id,'members_type':row['member_type'],
                      'quantity':row['amount']})
        data=response.json()
        if response.status_code!=200 or not isinstance(data,dict) or data.get('success') is not True:
            # A negative response might be definitive, but a network failure is not.
            raise ValueError('Provider did not confirm success (HTTP %d)'%response.status_code)
        if (data.get('order_id')!=order_id or data.get('members_type')!=row['member_type']
            or type(data.get('quantity')) is not int or data['quantity']!=row['amount']
            or not isinstance(data.get('key'),str) or not data['key'].strip()):
            raise ValueError('Provider response order/type/quantity/key mismatch')
    except Exception:
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='needs_review' WHERE id=$1 AND status='processing'",
                row['id'])
        log.exception('DISCORIUM_ORDER_UNCONFIRMED internal_id=%s order_id=%s',row['id'],order_id)
        raise HTTPException(502,'Provider outcome unconfirmed; contact support. Do not retry.')
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.member_redeem_keys SET status='completed',provider_key=$2,provider_response=$3 WHERE id=$1 AND status='processing'",
            row['id'],data['key'],json.dumps({'success':True,'order_id':order_id,
            'members_type':row['member_type'],'quantity':row['amount'],
            'credits_remaining':data.get('credits_remaining')}))
    log.info('DISCORIUM_ORDER_COMPLETED internal_id=%s order_id=%s type=%s quantity=%s provider_key_present=true',
             row['id'],order_id,row['member_type'],row['amount'])
    return {'success':True,'type':row['member_type'],'amount':row['amount'],
            'order_id':order_id,'provider_key':data['key'],
            'credits_remaining':data.get('credits_remaining')}
)

@router.post('/members/order')
async def order(payload:OrderBody, request:Request):
    """Convert an exclusively reserved Shoppex key into one Discorium order."""
    require_internal(request)
    import httpx
    provider_token=os.getenv('DISCORIUM_API_KEY','').strip()
    if not provider_token:
        raise HTTPException(503,'Discorium provider key not configured')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow(
                'SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',
                make_hash(payload.key))
            if not row or row['redeemed_by']!=payload.discord_id or row['status']!='reserved':
                raise HTTPException(409,'Key is not reserved to this user or already submitted')
            if row['member_type'] not in ('online','offline') or not 1<=row['amount']<=100000:
                raise HTTPException(409,'Invalid stored Shoppex member order')
            # Stable ID is saved before sending and never regenerated on a retry.
            order_id='dsc-'+secrets.token_hex(5)
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='processing',provider_order_id=$2 WHERE id=$1",
                row['id'],order_id)
    public_details={'id':row['id'],'type':row['member_type'],'amount':row['amount'],'order_id':order_id}
    log.info('DISCORIUM_ORDER_STARTED %s',json.dumps(public_details))
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response=await client.post(
                'https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members',
                headers={'Authorization':'Bearer '+provider_token,
                         'Content-Type':'application/json','Accept':'application/json'},
                json={'order_id':order_id,'members_type':row['member_type'],
                      'quantity':row['amount']})
        data=response.json()
        if response.status_code!=200 or not isinstance(data,dict) or data.get('success') is not True:
            # A negative response might be definitive, but a network failure is not.
            raise ValueError('Provider did not confirm success (HTTP %d)'%response.status_code)
        if (data.get('order_id')!=order_id or data.get('members_type')!=row['member_type']
            or type(data.get('quantity')) is not int or data['quantity']!=row['amount']
            or not isinstance(data.get('key'),str) or not data['key'].strip()):
            raise ValueError('Provider response order/type/quantity/key mismatch')
    except Exception:
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.member_redeem_keys SET status='needs_review' WHERE id=$1 AND status='processing'",
                row['id'])
        log.exception('DISCORIUM_ORDER_UNCONFIRMED internal_id=%s order_id=%s',row['id'],order_id)
        raise HTTPException(502,'Provider outcome unconfirmed; contact support. Do not retry.')
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.member_redeem_keys SET status='completed',provider_key=$2,provider_response=$3 WHERE id=$1 AND status='processing'",
            row['id'],data['key'],json.dumps({'success':True,'order_id':order_id,
            'members_type':row['member_type'],'quantity':row['amount'],
            'credits_remaining':data.get('credits_remaining')}))
    log.info('DISCORIUM_ORDER_COMPLETED internal_id=%s order_id=%s type=%s quantity=%s provider_key_present=true',
             row['id'],order_id,row['member_type'],row['amount'])
    return {'success':True,'type':row['member_type'],'amount':row['amount'],
            'order_id':order_id,'provider_key':data['key'],
            'credits_remaining':data.get('credits_remaining')}
