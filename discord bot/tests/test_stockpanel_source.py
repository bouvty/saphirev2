import ast
import unittest
from pathlib import Path
source=(Path(__file__).resolve().parents[1]/'app'/'bot.py').read_text()
class StockPanelTests(unittest.TestCase):
    def test_syntax(self): ast.parse(source)
    def test_command(self): self.assertIn("@bot.command(name='stockpanel')",source)
    def test_owner_gate(self): self.assertIn('async def stockpanel(ctx, channel_id: int):\n    if not owner(ctx.author):',source)
    def test_persistent(self): self.assertIn("custom_id='saphire:stock:social'",source);self.assertIn('bot.add_view(StockPanelStart())',source)
    def test_disabled_discord(self): self.assertIn("disabled=True, custom_id='saphire:stock:discord'",source)
    def test_private(self): self.assertIn('await inter.response.send_message(view=cv2_layout(await browser.build(), browser), ephemeral=True)',source)
    def test_pagination(self): self.assertIn("self.page*20",source)
    def test_prices(self): self.assertIn('**Rate per 1,000:**',source)
    def test_back_navigation(self): self.assertIn("'details': 'service'",source)
if __name__=='__main__':unittest.main()
