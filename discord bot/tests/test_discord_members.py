import pytest
import httpx
from decimal import Decimal
from types import SimpleNamespace
from app.discord_members import price_cents, validate_invite, provider_request, MemberWizard


def test_rates_and_rounding():
    assert price_cents('online', 15000)==1650
    assert price_cents('offline', 15000)==1200
    assert price_cents('online', 1000)==110
    assert price_cents('offline', 1000)==80

@pytest.mark.parametrize('kind,qty', [('other',10),('online',0),('offline',100001)])
def test_reject_invalid_amount(kind,qty):
    with pytest.raises(ValueError):price_cents(kind,qty)


def test_invites():
    assert validate_invite('https://discord.gg/testServer')=='https://discord.gg/testServer'
    assert validate_invite('https://discord.com/invite/testServer')=='https://discord.gg/testServer'
    for value in ('http://discord.gg/test','https://evil.example/invite/abc','https://discord.gg/a?x=1', 'https://discord.gg/a@b'):
        with pytest.raises(ValueError):validate_invite(value)

@pytest.mark.asyncio
async def test_provider_request(monkeypatch):
    calls=[]
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            calls.append((url,headers,json))
            return httpx.Response(200,json={'success':True,'order_id':'dm_123', 'key':'private-key','type':'online','quantity':15000,'credits_left':87})
    import app.discord_members as mod
    monkeypatch.setattr(mod.httpx,'AsyncClient',FakeClient)
    r=await provider_request(SimpleNamespace(discord_member_api_base_url='https://provider.example',discord_member_api_key='secret'), 'dm_123','online',15000)
    assert r['credits_left']==Decimal(87)
    assert calls[0]==('https://provider.example/api/v2/custom/reseller/api/order',{'Authorization':'secret','Accept':'application/json'},{'order_id':'dm_123','type':'online','quantity':15000})

@pytest.mark.asyncio
async def test_provider_mismatch_fails_closed(monkeypatch):
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):return httpx.Response(200,json={'success':True,'order_id':'wrong','key':'secret','type':'online','quantity':15000,'credits_left':50})
    import app.discord_members as mod
    monkeypatch.setattr(mod.httpx,'AsyncClient',FakeClient)
    with pytest.raises(RuntimeError, match='did not match'):
        await provider_request(SimpleNamespace(discord_member_api_base_url='https://provider.example',discord_member_api_key='secret'), 'dm_123','online',15000)
