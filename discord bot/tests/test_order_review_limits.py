from decimal import Decimal
import pytest
from app.discord_members import RATES, MIN_ORDER, price_cents

def test_rates_and_minimums():
    assert RATES == {'online': Decimal('1.10'), 'offline': Decimal('0.80')}
    assert MIN_ORDER == {'online': 400, 'offline': 800}
    assert price_cents('online', 400) == 22
    assert price_cents('offline', 800) == 32
    assert price_cents('online', 1000) == 110
    assert price_cents('offline', 1000) == 80

@pytest.mark.parametrize('kind,amount',[('online',399),('offline',799),('online',0),('offline',100001),('other',200)])
def test_minimums_enforced(kind,amount):
    with pytest.raises(ValueError):
        price_cents(kind, amount)

def test_review_has_four_requested_labels():
    from pathlib import Path
    src=(Path(__file__).parents[1]/'app'/'discord_members.py').read_text()
    for label in ('**Service** -','**Quantity** -','**Rate** -','**Total order price** -'):
        assert label in src
