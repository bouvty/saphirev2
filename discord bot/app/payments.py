"""Read-only, independently verified blockchain payments.

Never credit funds using user-supplied data alone. A TX must have a fresh block/network
observation, correct recipient, locked exact quote, and chain-specific confirmations.
All API failures are explicitly *unverified*.
"""
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import httpx

COINS={'BTC':('bitcoin',8,1),'LTC':('litecoin',8,3),'ETH':('ethereum',18,6),'SOL':('solana',9,16)}

@dataclass(frozen=True)
class Observation:
    amount: Decimal
    confirmations: int
    created_at: dt.datetime
    confirmed: bool

class VerificationUnavailable(Exception): pass
class InvalidTransaction(Exception): pass

async def quote(coin: str, usd_cents: int) -> Decimal:
    if coin not in COINS or usd_cents<100:raise InvalidTransaction('Unsupported currency/amount')
    slug,decimals,_=COINS[coin]
    async with httpx.AsyncClient(timeout=12) as client:
        try:
            r=await client.get('https://api.coingecko.com/api/v3/simple/price',params={'ids':slug,'vs_currencies':'usd'})
            r.raise_for_status()
            price=Decimal(str(r.json()[slug]['usd']))
            if price<=0:raise ValueError('nonpositive price')
        except Exception as e:raise VerificationUnavailable('Live exchange quote unavailable; retry later') from e
    return (Decimal(usd_cents)/Decimal(100)/price).quantize(Decimal(1).scaleb(-decimals),rounding=ROUND_DOWN)

def parse_time(value):
    if isinstance(value,(int,float)):return dt.datetime.fromtimestamp(value,dt.timezone.utc)
    if isinstance(value,str):return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(dt.timezone.utc)
    raise InvalidTransaction('Transaction timestamp missing')

async def check_transaction(coin,txid,wallet,expected,created_at,*,eth_rpc='',sol_rpc='',blockcypher_token=''):
    if coin not in COINS or not txid or not wallet:raise InvalidTransaction('Unsupported or incomplete invoice')
    if not isinstance(expected,Decimal):expected=Decimal(str(expected))
    if expected<=0:raise InvalidTransaction('Invalid payment amount')
    if isinstance(created_at,str):created_at=parse_time(created_at)
    async with httpx.AsyncClient(timeout=16) as client:
        try:
            if coin in ('BTC', 'LTC'):
                # Esplora-compatible explorer APIs, no BlockCypher hourly quota.
                if len(txid) != 64 or any(c not in '0123456789abcdefABCDEF' for c in txid):
                    raise InvalidTransaction('Invalid transaction hash')
                origin = 'https://blockstream.info/api' if coin == 'BTC' else 'https://litecoinspace.org/api'
                async def esplora(path):
                    response = await client.get(origin + path)
                    if response.status_code == 404:
                        raise InvalidTransaction('Transaction not found on selected blockchain')
                    if response.status_code in (429, 500, 502, 503, 504):
                        raise VerificationUnavailable(f'{coin} explorer temporarily unavailable (HTTP {response.status_code})')
                    response.raise_for_status()
                    return response.json() if path != '/blocks/tip/height' else int(response.text.strip())
                tx = await esplora('/tx/' + txid)
                if tx.get('txid','').lower() != txid.lower():
                    raise VerificationUnavailable('Explorer transaction ID mismatch')
                output_units = sum(int(out['value']) for out in tx.get('vout', [])
                    if out.get('scriptpubkey_address') == wallet)
                amount = Decimal(output_units) / Decimal(10**8)
                status = tx.get('status') or {}
                if status.get('confirmed'):
                    tip = await esplora('/blocks/tip/height')
                    confirmations = max(0, int(tip) - int(status['block_height']) + 1)
                    seen = parse_time(status['block_time'])
                else:
                    # Explorer mempool time isn't necessarily authenticated; do not
                    # accept unknown-time payments as valid until mined.
                    raise VerificationUnavailable('Transaction found in mempool; waiting for block timestamp')
            elif coin == 'ETH':
                if not eth_rpc: raise VerificationUnavailable('ETH_RPC_URL required for Ethereum verification')
                if len(txid.removeprefix('0x')) != 64:
                    raise InvalidTransaction('Invalid Ethereum transaction hash')
                async def ethereum_rpc(method, params):
                    response = await client.post(eth_rpc, json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get('error'):raise VerificationUnavailable('Ethereum RPC rejected request')
                    return payload.get('result')
                tx = await ethereum_rpc('eth_getTransactionByHash',[txid])
                if tx is None:raise InvalidTransaction('Ethereum transaction not found')
                if not tx.get('blockNumber'):
                    raise VerificationUnavailable('Ethereum transaction waiting to be mined')
                receipt=await ethereum_rpc('eth_getTransactionReceipt',[txid])
                if not receipt or receipt.get('status') != '0x1':
                    raise InvalidTransaction('Ethereum transaction failed') if receipt and receipt.get('status') == '0x0' else VerificationUnavailable('Receipt pending')
                if (tx.get('to') or '').lower() != wallet.lower():
                    raise InvalidTransaction('Ethereum transaction recipient does not match invoice wallet')
                amount=Decimal(int(tx['value'],16))/Decimal(10**18)
                block=await ethereum_rpc('eth_getBlockByNumber',[tx['blockNumber'],False])
                head=await ethereum_rpc('eth_blockNumber',[])
                if not block or not block.get('timestamp'):raise VerificationUnavailable('Block timestamp unavailable')
                seen=parse_time(int(block['timestamp'],16))
                confirmations=max(0,int(head,16)-int(tx['blockNumber'],16)+1)
            else:
                if not sol_rpc:raise VerificationUnavailable('SOL_RPC_URL must be configured for Solana verification')
                async def rpc(method,params):
                    r=await client.post(sol_rpc,json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
                    r.raise_for_status();v=r.json()
                    if v.get('error'):raise VerificationUnavailable('Solana RPC verification failed')
                    return v.get('result')
                tx=await rpc('getTransaction',[txid,{'encoding':'jsonParsed','maxSupportedTransactionVersion':0,'commitment':'confirmed'}])
                if not tx:raise InvalidTransaction('Solana transaction not found/confirmed')
                if (tx.get('meta') or {}).get('err') is not None:raise InvalidTransaction('Solana transaction failed')
                seen=parse_time(tx['blockTime'])
                instructions=tx['transaction']['message'].get('instructions',[])
                lamports=0
                for item in instructions:
                    parsed=item.get('parsed') or {}; info=parsed.get('info') or {}
                    if item.get('program')=='system' and parsed.get('type')=='transfer' and info.get('destination')==wallet:
                        lamports+=int(info.get('lamports',0))
                amount=Decimal(lamports)/Decimal(10**9)
                statuses=await rpc('getSignatureStatuses',[[txid],{'searchTransactionHistory':True}])
                status=(statuses or {}).get('value',[None])[0]
                confirmations=int(status.get('confirmations') or 0) if status else 0
                # Solana finalized status is the only accepted final settlement.
                if status and status.get('confirmationStatus')=='finalized':confirmations=32
        except (InvalidTransaction, VerificationUnavailable):
            raise
        except httpx.TimeoutException as exc:
            raise VerificationUnavailable('Blockchain verification request timed out') from exc
        except httpx.RequestError as exc:
            raise VerificationUnavailable(f'Blockchain network request failed ({type(exc).__name__})') from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise VerificationUnavailable(f'Blockchain provider returned unexpected data ({type(exc).__name__})') from exc
        except Exception as exc:
            raise VerificationUnavailable(f'Blockchain verification service unavailable ({type(exc).__name__})') from exc
    now=dt.datetime.now(dt.timezone.utc)
    if seen<created_at-dt.timedelta(seconds=90) or seen>created_at+dt.timedelta(minutes=30):
        raise InvalidTransaction('Transaction must be within 30 minutes of invoice creation')
    if seen>now+dt.timedelta(seconds=90):raise InvalidTransaction('Invalid blockchain timestamp')
    if amount!=expected:raise InvalidTransaction(f'Exact amount mismatch: expected {expected}, chain shows {amount}')
    _,_,required=COINS[coin]
    return Observation(amount,confirmations,seen,confirmations>=required)
