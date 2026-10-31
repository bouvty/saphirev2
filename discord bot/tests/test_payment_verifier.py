import asyncio
import datetime as dt
from decimal import Decimal
from unittest.mock import patch
import httpx
import pytest
from app import payments

class MockClient:
    def __init__(self, response):self.response=response
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
    async def get(self,*args,**kwargs):return self.response

def test_btc_exact_wallet_and_amount():
    now=dt.datetime.now(dt.timezone.utc)
    data={'outputs':[{'addresses':['correct'], 'value':12500000},{'addresses':['other'],'value':10000}],
          'received':now.isoformat(),'confirmations':2}
    with patch.object(payments.httpx,'AsyncClient',return_value=MockClient(httpx.Response(200,json=data,request=httpx.Request('GET','https://example.com')))):
        result=asyncio.run(payments.check_transaction('BTC','a'*64,'correct',Decimal('0.12500000'),now))
    assert result.confirmed and result.amount==Decimal('.125')

def test_btc_wrong_amount_fails_closed():
    now=dt.datetime.now(dt.timezone.utc)
    data={'outputs':[{'addresses':['correct'], 'value':10}], 'received':now.isoformat(),'confirmations':10}
    with patch.object(payments.httpx,'AsyncClient',return_value=MockClient(httpx.Response(200,json=data,request=httpx.Request('GET','https://example.com')))):
        with pytest.raises(payments.InvalidTransaction,match='Exact amount'):
            asyncio.run(payments.check_transaction('BTC','a'*64,'correct',Decimal('0.125'),now))

def test_old_tx_fails_closed():
    now=dt.datetime.now(dt.timezone.utc)
    data={'outputs':[{'addresses':['correct'], 'value':10000000}],
          'received':(now-dt.timedelta(hours=1)).isoformat(),'confirmations':100}
    with patch.object(payments.httpx,'AsyncClient',return_value=MockClient(httpx.Response(200,json=data,request=httpx.Request('GET','https://example.com')))):
        with pytest.raises(payments.InvalidTransaction,match='within five minutes'):
            asyncio.run(payments.check_transaction('BTC','a'*64,'correct',Decimal('.1'),now))

def test_eth_uses_blockcypher_and_checks_native_transfer():
    now=dt.datetime.now(dt.timezone.utc)
    address='b'*40
    tx={'outputs':[{'addresses':[address], 'value':10**17}],
        'confirmed':now.isoformat(),'confirmations':12,'block_height':100,'double_spend':False}
    client=MockClient(httpx.Response(200,json=tx,request=httpx.Request('GET','https://example.com')))
    with patch.object(payments.httpx,'AsyncClient',return_value=client):
        result=asyncio.run(payments.check_transaction('ETH','0x'+'a'*64,'0x'+address,Decimal('0.1'),now))
    assert result.confirmed and result.amount==Decimal('0.1')

def test_eth_unconfirmed_never_settles():
    now=dt.datetime.now(dt.timezone.utc)
    tx={'outputs':[{'addresses':['b'*40], 'value':10**17}],
        'received':now.isoformat(),'confirmations':0,'block_height':-1}
    with patch.object(payments.httpx,'AsyncClient',return_value=MockClient(httpx.Response(200,json=tx,request=httpx.Request('GET','https://example.com')))):
        with pytest.raises(payments.VerificationUnavailable,match='not yet mined'):
            asyncio.run(payments.check_transaction('ETH','0x'+'a'*64,'0x'+'b'*40,Decimal('0.1'),now))

def test_sol_never_calls_blockcypher():
    now=dt.datetime.now(dt.timezone.utc)
    with pytest.raises(payments.VerificationUnavailable,match='SOL_RPC_URL'):
        asyncio.run(payments.check_transaction('SOL','signature','wallet',Decimal('1'),now))

def test_static_flow_contract():
    from pathlib import Path
    src=(Path(__file__).parents[1]/'app'/'bot.py').read_text()
    assert 'for update' in src and 'on conflict(reference) do nothing' in src
    assert "status='confirming'" in src
    assert 'payment-{invoice_id}' in src
    assert 'status_message_id' in src
    assert 'PAYMENT_TIMEOUT=300' in src
