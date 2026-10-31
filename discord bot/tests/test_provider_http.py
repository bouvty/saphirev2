"""Top4SMM official API protocol tests; all requests mocked."""
import httpx
import pytest
from app import provider


def test_official_parameter_mapping():
    assert provider._request_data('services', {})['act']=='services'
    assert provider._request_data('balance', {})['act']=='balance'
    order=provider._request_data('new_order',{'service_id':'256','count':500,'link':'https://example.com','comment':'hello|world'})
    assert order['act']=='new_order'
    assert order['service_id']=='256' and order['count']==500 and order['comment']=='hello|world'
    status=provider._request_data('order_info',{'id':'5754'})
    assert status['id']=='5754' and status['act']=='order_info'


def test_safe_non_json_error():
    response=httpx.Response(200,headers={'content-type':'text/html'},text='<html>apikey secret</html>')
    assert 'HTML' in provider._response_description(response)
    assert 'secret' not in provider._response_description(response)


@pytest.mark.asyncio
async def test_get_services_from_documented_endpoint(monkeypatch):
    async def handler(request):
        assert request.method=='GET'
        assert request.url.path=='/api.php'
        assert request.url.params['act']=='services'
        assert request.url.params['key']
        return httpx.Response(200,json=[{'id':'256','name':'Instagram Views','rate':'0.15',
                                        'min_order':'500','max_order':'10000',
                                        'category':'Instagram Views','description':'Example'}])
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    result=await provider.call('services')
    assert result[0]['id']=='256'


@pytest.mark.asyncio
async def test_balance_and_order_response_unwrapped(monkeypatch):
    async def handler(request):
        act=request.url.params['act']
        if act=='balance': return httpx.Response(200,json={'res':{'balance':'50.00'}})
        if act=='new_order':
            assert request.url.params['service_id']=='256'
            assert request.url.params['count']=='500'
            return httpx.Response(200,json={'res':{'status':'ok','order_id':5754,'sum':1.1}})
        return httpx.Response(200,json={'res':{'status':'completed'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    assert (await provider.call('balance'))['balance']=='50.00'
    assert (await provider.call('new_order',service_id='256',count=500,link='https://example.com'))['order_id']==5754
    assert (await provider.call('order_info',id='5754'))['status']=='completed'


@pytest.mark.asyncio
async def test_rejected_api_response(monkeypatch):
    async def handler(request):return httpx.Response(200,json={'error':{'error_message':'Invalid API key'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(provider.ProviderError,match='API error'):
        await provider.call('services')
