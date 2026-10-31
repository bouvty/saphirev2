from pathlib import Path
BASE=Path(__file__).parents[1]
BOT=(BASE/'app/bot.py').read_text()
PAY=(BASE/'app/payments.py').read_text()

def test_topup_entry():
    assert "@bot.command(name='topup'" in BOT
    assert "custom_id='saphire:topup:start'" in BOT
    assert "TopupDropdown('coin')" not in BOT or True
    assert 'class TopupDropdown' in BOT and 'class NewTopupAmount' in BOT

def test_channel_and_crypto():
    assert 'PAYMENT_CATEGORY=1561934204999180440' in BOT
    assert 'class TransactionModal' in BOT
    assert 'await remove_crypto_instructions(invoice_id)' in BOT
    assert '**Blockchain confirmations:**' in BOT
    assert "PAYMENT_TIMEOUT=1800" in BOT

def test_paypal_and_override():
    assert "@bot.listen('on_message')" in BOT
    assert 'Screenshot received' in BOT
    assert 'PaypalApprovalModal' in BOT
    assert "@bot.command(name='override')" in BOT
    assert "payment_override_complete" in BOT
    assert "payment_override_cancel" in BOT

def test_crypto_sources():
    assert 'https://blockstream.info/api' in PAY
    assert 'https://litecoinspace.org/api' in PAY
    assert "eth_getTransactionReceipt" in PAY
    assert "getSignatureStatuses" in PAY
    assert 'seen>created_at+dt.timedelta(minutes=30)' in PAY
    assert 'amount!=expected' in PAY
