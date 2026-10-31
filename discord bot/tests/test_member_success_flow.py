"""Offline contract tests for the Saphire private member checkout success flow."""
import ast
from pathlib import Path
SRC = Path(__file__).parents[1] / 'app' / 'discord_members.py'
s = SRC.read_text()
def test_syntax(): ast.parse(s)
def test_delivery_and_private_db_storage():
    assert "from .discord_delivery_test import request_delivery, DeliveryRejected" in s
    assert "provider_order_id=$4" in s
    assert "result['order_id']" in s
    assert "provider_key=$2" in s
    assert "SELECT discord_id,status,provider_order_id,provider_key" in s
    assert "row['discord_id'] != interaction.user.id" in s
def test_same_message_and_timed_reveal():
    assert "await inter.edit_original_response(view=success.layout())" in s
    assert "await asyncio.sleep(10)" in s
    assert "await interaction.edit_original_response(view=success.layout())" in s
    assert "await interaction.response.edit_message(view=self.layout_factory(" in s
    assert "await inter.followup.send(view=self.layout_factory(self.embed_factory('Order accepted" not in s
def test_actions():
    assert "label='Add the Discord bot'" in s
    assert 'client_id=1557383236777152532' in s
    assert "label='Continue'" in s
    assert "label='Redeem your order'" in s
    assert "https://memberix.shop/" in s
