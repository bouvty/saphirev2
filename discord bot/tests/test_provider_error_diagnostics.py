import httpx
import pytest
from app import provider

@pytest.mark.parametrize('payload,expected', [
    ({'error': {'message': 'Insufficient balance'}}, 'insufficient provider balance'),
    ({'error': {'error_message': 'Invalid API key'}}, 'API access or key rejected'),
    ({'error': 'Invalid service'}, 'service unavailable or invalid'),
    ({'error': 'Missing required field service_id'}, 'order request parameters rejected'),
    ({'error': 'something secret UNIQUE-PRIVATE-TOKEN'}, 'unspecified'),
])
@pytest.mark.asyncio
async def test_error_is_classified_without_exposing_raw_body(monkeypatch,payload,expected):
    original=httpx.AsyncClient
    async def handler(request):
        return httpx.Response(200,json=payload)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(provider.ProviderError) as error:
        await provider.call('new_order',service_id='1',count=10,link='https://example.org')
    assert expected in str(error.value)
    assert 'UNIQUE-PRIVATE-TOKEN' not in str(error.value)
