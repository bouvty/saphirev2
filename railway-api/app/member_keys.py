"""Shoppex signed dynamic fulfillment and one-time member key redemption."""
import os,hmac,hashlib,json,re,secrets,time,logging
from datetime import timezone
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field
from cryptography.fernet import Fernet
from . import db
router=APIRouter()
log=logging.getLogger("saphire.members.keys")

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

@router.post('/resellers/members/order/{kind}')
async def shoppex_delivery(kind:str,request:Request):
    if kind not in ('online','offline'):raise HTTPException(404,'Unknown member product')
    raw=await request.body()
    if len(raw)>32768:raise HTTPException(413,'Payload too large')
    delivery_id=verify_shoppex(request,raw,kind)
    try:payload=json.loads(raw)
    except ValueError:raise HTTPException(400,'Invalid payload')
    cipher_secret=os.getenv('SHOPPEX_KEY_ENCRYPTION_KEY','')
    if not cipher_secret:raise HTTPException(503,'Shoppex delivery encryption is not configured')
    try:cipher=Fernet(cipher_secret.encode())
    except Exception:raise HTTPException(503,'Shoppex delivery encryption is invalid')
    # Exact Shoppex paid order/product mapping must be configured and verified against test delivery.
    order=str(payload.get('order_id') or payload.get('order') or '')
    product=str(payload.get('product_id') or payload.get('product') or '')
    unit_count=payload.get('quantity')
    mapping=os.getenv('SHOPPEX_ONLINE_PRODUCT_ID' if kind=='online' else 'SHOPPEX_OFFLINE_PRODUCT_ID','')
    unit_size=int(os.getenv('SHOPPEX_ONLINE_MEMBERS_PER_UNIT' if kind=='online' else 'SHOPPEX_OFFLINE_MEMBERS_PER_UNIT','0'))
    if not mapping or mapping!=product or not order or len(order)>128 or len(product)>128 or type(unit_count)!=int or not 1<=unit_count<=100000 or unit_size<=0:
        log.warning('Shoppex product mismatch kind=%s mapped=%s match=%s quantity_valid=%s order_present=%s',kind,bool(mapping),mapping==product,type(unit_count) is int and 1<=unit_count<=100000,bool(order))
        raise HTTPException(400,'Shoppex product mapping or quantity invalid')
    amount=unit_count*unit_size
    async with db.pool.acquire() as conn:
        minimum=await conn.fetchval('SELECT min_amount FROM public.member_order_settings WHERE member_type=$1',kind)
        # A Shoppex unit is one member. Minimum provider order quantities apply at redemption, not paid-key delivery.
    if amount<1 or amount>100000:raise HTTPException(400,'Order quantity outside limits')
        async with conn.transaction():
            row=await conn.fetchrow('SELECT * FROM public.member_redeem_keys WHERE fulfillment_id=$1 OR (shoppex_order_id=$2 AND shoppex_product_id=$3) FOR UPDATE',delivery_id,order,product)
            if row:
                # Repeated deliveries cannot safely reconstruct the plaintext key.
                # Shoppex should accept pending fulfillment for a duplicate delivery.
                if row['key_ciphertext']:
                    saved=cipher.decrypt(row['key_ciphertext'].encode()).decode()
                    return {'deliver':saved,'key':saved,'amount':row['amount'],'type':row['member_type']}
                return {'status':'pending','message':'Existing fulfillment requires staff recovery'}
            key=generate_redeem_key()
            await conn.execute("""INSERT INTO public.member_redeem_keys(key_hash,amount,member_type,shoppex_order_id,shoppex_product_id,fulfillment_id,key_ciphertext)
                VALUES($1,$2,$3,$4,$5,$6,$7)""",make_hash(key),amount,kind,order,product,delivery_id,cipher.encrypt(key.encode()).decode())
    return {'deliver':key,'key':key,'amount':amount,'type':kind}

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
    discord_id:str=Field(pattern=r'^\d{15,22}$')
    invite_url:str=Field(min_length=18,max_length=250)

@router.post('/members/order')
async def order(payload:OrderBody,request:Request):
    require_internal(request)
    from urllib.parse import urlsplit
    u=urlsplit(payload.invite_url)
    if u.scheme!='https' or u.hostname not in ('discord.gg','discord.com','www.discord.com') or not re.fullmatch(r'/([a-zA-Z0-9-]{2,64}|invite/[a-zA-Z0-9-]{2,64})/?',u.path) or u.query or u.fragment:
        raise HTTPException(400,'Invalid Discord invite')
    # Check configuration before locking a paid key; do not strand it if not configured.
    import httpx
    base=os.getenv('DISCORD_MEMBER_API_BASE_URL','').rstrip('/')
    provider_key=os.getenv('DISCORD_MEMBER_API_KEY','')
    if not base.startswith('https://') or not provider_key:
        raise HTTPException(503,'Provider API is not configured; order not attempted')
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('SELECT * FROM public.member_redeem_keys WHERE key_hash=$1 FOR UPDATE',make_hash(payload.key))
            if not row or row['redeemed_by']!=payload.discord_id or row['status']!='reserved':
                raise HTTPException(409,'Key is invalid, already used or order is processing')
            await conn.execute("UPDATE public.member_redeem_keys SET status='processing',invite_url=$2 WHERE id=$1",row['id'],payload.invite_url)
    # Provider credentials remain server-side; no provider order is retried automatically on error.
    order_id='shoppex_'+str(row['id'])
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            result=await client.post(base+'/api/v2/custom/reseller/api/order',json={'order_id':order_id,'type':row['member_type'],'quantity':row['amount']},headers={'Authorization':provider_key,'Accept':'application/json'})
            result.raise_for_status()
            data=result.json()
        if not isinstance(data,dict) or data.get('success') is not True or str(data.get('order_id'))!=order_id or data.get('type')!=row['member_type'] or data.get('quantity')!=row['amount'] or not isinstance(data.get('key'),str) or not data['key'].strip():
            raise ValueError('Unconfirmed provider response')
    except Exception:
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE public.member_redeem_keys SET status='needs_review' WHERE id=$1 AND status='processing'",row['id'])
        log.exception('Provider outcome unknown for internal order %s; manual reconciliation required',row['id'])
        raise HTTPException(502,'Provider outcome unconfirmed; contact support, do not retry')
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE public.member_redeem_keys SET status='completed',provider_order_id=$2,provider_key=$3,provider_response=$4 WHERE id=$1",row['id'],order_id,data['key'],json.dumps({'success':True,'order_id':order_id}))
    return {'success':True,'type':row['member_type'],'amount':row['amount'],'order_id':order_id,'provider_key':data['key']}
