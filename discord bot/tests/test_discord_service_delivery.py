"""Contract checks for the public Saphire member panel's delivery backend."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / 'discord_members.py').read_text()
DELIVERY = (ROOT / 'discord_delivery_test.py').read_text()

def test_public_panel_uses_same_verified_delivery_request_as_test_checkout():
    ast.parse(SOURCE)
    assert 'from .discord_delivery_test import request_delivery, DeliveryRejected' in SOURCE
    assert 'await request_delivery(' in SOURCE
    assert 'self.settings.discord_delivery_api_key' in SOURCE
    assert 'response=await provider_request(' not in SOURCE
    assert 'dsc-' in SOURCE
    assert "'members_type': kind, 'quantity': quantity" in DELIVERY

def test_invite_only_stored_in_order_record():
    assert 'reserve_order(self.db,self.user_id,self.kind,self.quantity,self.invite)' in SOURCE
    assert 'invite_url,amount_cents,status' in SOURCE
    assert 'f\'**Invite:** {self.invite}' not in SOURCE
    assert 'mark_needs_review(self.db,order_id)' in SOURCE
    assert 'await mark_provider_success(self.db, order_id, response)' in SOURCE
