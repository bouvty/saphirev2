from decimal import Decimal
from app.provider import retail_rate, cents_for

def test_markup():
    assert retail_rate('0.15')==Decimal('1.240000')
    assert cents_for(1000,retail_rate('0.15'))==124
    assert cents_for(500,retail_rate('0.15'))==62
