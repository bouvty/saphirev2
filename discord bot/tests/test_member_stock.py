import pytest
from app.member_stock import parse_stock


def test_parse_expected_response():
    assert parse_stock({'offline': 10242, 'online': 372, 'success': True}) == {'online': 372, 'offline': 10242}


@pytest.mark.parametrize('payload', [None, [], {}, {'success': False, 'online': 1, 'offline': 2},
    {'success': True, 'online': -1, 'offline': 2},
    {'success': True, 'online': '2', 'offline': 1},
    {'success': True, 'online': True, 'offline': 1}])
def test_reject_invalid(payload):
    with pytest.raises(ValueError):
        parse_stock(payload)
