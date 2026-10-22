"""Saphire-owned SellAuth keys. Disabled until real signed SellAuth callback is verified."""
import hashlib,hmac,json,logging,os,re,secrets
from datetime import datetime,timezone
from cryptography.fernet import Fernet
from fastapi import APIRouter,Request,HTTPException
from . import db

router=APIRouter()
log=logging.getLogger("saphire.sellauth")
MINS={"offline":800,"online":400}
MAX=100000

def verify(body,signature,secret):
    return bool(secret and re.fullmatch(r"[a-fA-F0-9]{64}",signature or "") and
        hmac.compare_digest(hmac.new(secret.encode(),body,hashlib.sha256).hexdigest(),signature.lower()))

def configuration():
    if os.environ.get("SELLAUTH_DELIVERY_ENABLED","false").lower()!="true":
        raise HTTPException(503,"Delivery not enabled")
    secret=os.environ.get("SELLAUTH_WEBHOOK_SECRET","")
    shop=os.environ.get("SELLAUTH_SHOP_ID","")
    encryption=os.environ.get("SAPHIRE_KEY_ENCRYPTION_KEY","")
    if len(secret)<32 or not shop or not encryption:
        raise HTTPException(503,"Delivery not configured")
    try:
        cipher=Fernet(encryption.encode())
        mapping=json.loads(os.environ["SELLAUTH_PRODUCT_MAP"])
        if not isinstance(mapping,dict) or not mapping:
            raise ValueError("No product mapping")
    except (KeyError,ValueError,TypeError):
        raise HTTPException(503,"Delivery mapping not configured")
    return secret,shop,cipher,mapping

def parse_delivery(payload,shop,mapping,kind):
    # This must be tested with actual signed SellAuth dynamic delivery fixture.
    if not isinstance(payload,dict) or payload.get("event")!="INVOICE.ITEM.DELIVER-DYNAMIC":
        raise ValueError("Unsupported delivery event")
    invoice,item=payload.get("invoice"),payload.get("item")
    if not isinstance(invoice,dict) or not isinstance(item,dict) or str(payload.get("shop_id"))!=shop:
        raise ValueError("Invalid shop/invoice/item")
    if invoice.get("status") not in ("completed","partially_completed") or item.get("status") not in ("paid","completed","delivering"):
        raise ValueError("Order not marked paid")
    ids=[str(invoice.get("id") or ""),str(item.get("id") or ""),str(item.get("product_id") or ""),str(item.get("variant_id") or "")]
    if not all(ids[:3]) or any(len(v)>128 for v in ids):
        raise ValueError("Invalid order IDs")
    units=item.get("quantity")
    if type(units)!=int or not 1<=units<=MAX:
        raise ValueError("Invalid purchased quantity")
    product=mapping.get(ids[2]+":"+ids[3])
    if not isinstance(product,dict) or product.get("member_type")!=kind:
        raise ValueError("Unmapped product")
    per=product.get("members_per_unit")
    if type(per)!=int or not 1<=per<=MAX:
        raise ValueError("Invalid product mapping")
    members=units*per
    if not MINS[kind]<=members<=MAX:
        raise ValueError("Member amount outside limits")
    return *ids,units,members

@router.post("/api/v2/resellers/custom/delivery/members/{kind}")
async def issue(request:Request,kind:str):
    if kind not in MINS:
        raise HTTPException(404,"Unknown service")
    body=await request.body()
    if len(body)>32768:
        raise HTTPException(413,"Payload too large")
    secret,shop,cipher,mapping=configuration()
    if not verify(body,request.headers.get("x-signature",""),secret):
        raise HTTPException(401,"Invalid signature")
    idem=request.headers.get("idempotency-key","")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,180}",idem):
        raise HTTPException(400,"Missing idempotency key")
    try:
        invoice,item,product,variant,units,members=parse_delivery(json.loads(body),shop,mapping,kind)
    except (ValueError,TypeError):
        raise HTTPException(422,"Invalid paid delivery request")
    digest=hashlib.sha256(json.dumps([shop,invoice,item,product,variant,kind,units,members],separators=(",",":")).encode()).hexdigest()
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            prev=await conn.fetchrow("""
                SELECT request_fingerprint,key_ciphertext,status FROM sellauth_member_keys
                WHERE shop_id=$1 AND invoice_id=$2 AND item_id=$3 FOR UPDATE
            """,shop,invoice,item)
            if prev:
                if prev["request_fingerprint"]!=digest or prev["status"]!="issued":
                    raise HTTPException(409,"Conflicting fulfillment retry")
                return {"keys":[cipher.decrypt(bytes(prev["key_ciphertext"])).decode()]}
            stock=await conn.fetchrow("""
                SELECT available,refreshed_at FROM discord_member_stock
                WHERE member_type=$1 FOR UPDATE
            """,kind)
            if not stock or not stock["refreshed_at"]:
                raise HTTPException(503,"Inventory unavailable")
            age=(datetime.now(timezone.utc)-stock["refreshed_at"]).total_seconds()
            if age < -30 or age>180:
                raise HTTPException(503,"Inventory stale")
            outstanding=await conn.fetchval("""
                SELECT COALESCE(SUM(remaining_members),0) FROM sellauth_member_keys
                WHERE member_type=$1 AND status='issued'
            """,kind)
            bot_used=await conn.fetchval("""
                SELECT COALESCE(SUM(quantity),0) FROM discord_member_orders
                WHERE member_type=$1 AND created_at>$2
                AND status IN ('reserved','accepted','needs_review')
            """,kind,stock["refreshed_at"])
            if int(stock["available"])-int(outstanding or 0)-int(bot_used or 0)<members:
                raise HTTPException(409,"Insufficient stock")
            key="SAPH-"+"-".join(secrets.token_hex(6).upper() for _ in range(4))
            await conn.execute("""
                INSERT INTO sellauth_member_keys
                (shop_id,invoice_id,item_id,product_id,variant_id,idempotency_key,
                 request_fingerprint,member_type,members,remaining_members,key_hash,key_ciphertext,status)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$10,$11,'issued')
            """,shop,invoice,item,product,variant,idem,digest,kind,members,
                hashlib.sha256(key.encode()).hexdigest(),cipher.encrypt(key.encode()))
    return {"keys":[key]}
