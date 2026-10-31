import pytest
from decimal import Decimal
from app import provider


def test_provider_service_formats():
    a = provider.normalize_service({'service': '123', 'name': 'Instagram likes', 'category':'Instagram', 'rate': '0.50', 'min': '100', 'max': '9999'})
    b = provider.normalize_service({'id': '123', 'name': 'Instagram likes', 'category':'Instagram', 'rate': '0.50', 'min_order': '100', 'max_order': '9999'})
    assert a == b
    assert a[5] == Decimal('1.590000')


def test_invalid_services_rejected():
    with pytest.raises(ValueError):
        provider.normalize_service({'name':'bad', 'rate':'0.10', 'min':1, 'max':4})
    with pytest.raises(ValueError):
        provider.normalize_service({'service':9, 'name':'bad', 'rate':'nan', 'min':1, 'max':4})
    with pytest.raises(ValueError):
        provider.normalize_service({'service':9, 'name':'bad', 'rate':1, 'min':4, 'max':1})


class FakeConnection:
    def __init__(self): self.items=[]
    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def executemany(self,sql,rows): self.items.extend(rows)

class FakePool:
    def __init__(self): self.conn=FakeConnection()
    def acquire(self): return self.conn

class FakeDatabase:
    def __init__(self): self.pool=FakePool()


@pytest.mark.asyncio
async def test_sync_upserts_full_catalog(monkeypatch):
    async def fake_call(action):
        assert action == 'services'
        return [{'service':i,'name':f'Service {i}', 'rate':'0.25','min':10,'max':1000} for i in range(428)]
    monkeypatch.setattr(provider,'call',fake_call)
    db=FakeDatabase()
    assert await provider.sync_services(db)==428
    assert len(db.pool.conn.items)==428


@pytest.mark.asyncio
async def test_bad_catalog_does_not_write(monkeypatch):
    async def fake_call(action):
        return [{'service':1,'name':'OK','rate':1,'min':1,'max':10},{'bad':'service'}]
    monkeypatch.setattr(provider,'call',fake_call)
    db=FakeDatabase()
    with pytest.raises(provider.ProviderError): await provider.sync_services(db)
    assert db.pool.conn.items==[]
