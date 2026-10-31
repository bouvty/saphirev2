import ast
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import unittest

BOT = Path(__file__).resolve().parents[1] / 'app' / 'bot.py'
CONFIG = BOT.with_name('config.py')
source = BOT.read_text()
root=ast.parse(source)
functions={n.name:n for n in root.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def pure(name):
    ns={'Decimal':Decimal,'ROUND_HALF_UP':ROUND_HALF_UP}
    exec(compile(ast.Module(body=[functions[name]],type_ignores=[]),str(BOT),'exec'),ns)
    return ns[name]

class OrderPanelTests(unittest.TestCase):
    def test_python_compiles(self):compile(source,str(BOT),'exec')
    def test_platform_classification(self):
        fn=pure('service_platform')
        self.assertEqual(fn({'name':'Instagram followers','category':'Followers'}),'Instagram')
        self.assertEqual(fn({'name':'TikTok Views','category':'Other'}),'TikTok')
    def test_service_group_auto_likes_not_generic(self):
        fn=pure('service_group')
        self.assertEqual(fn({'name':'IG Auto Likes','category':'Auto Likes'}),'Auto likes')
        self.assertEqual(fn({'name':'YouTube comments','category':'Other'}),'Comments')
    def test_discount_rounding(self):
        fn=pure('discount_amount')
        self.assertEqual(fn(1500,Decimal('10')),1350)
        self.assertEqual(fn(99,Decimal('50')),50)
    def test_persistent_start_button(self):
        self.assertIn("custom_id='saphire:order:start'",source)
        self.assertIn('bot.add_view(OrderStart())',source)
    def test_private_wizard(self):
        self.assertIn('await inter.response.send_message(view=view,ephemeral=True)',source)
    def test_history_events(self):
        self.assertIn("send_history_event('order'",source)
        self.assertIn("send_history_event('topup'",source)
    def test_order_permissions(self):
        self.assertIn('if not owner(ctx.author):return\n    channel=bot.get_channel(channel_id)',source)
    def test_order_no_double_provider_retry(self):
        self.assertIn("update orders set status='needs_review'",source)
    def test_config(self):
        contents=CONFIG.read_text()
        for v in ('transaction_log_channel_id','order_panel_channel_id','topup_panel_channel_id'):
            self.assertIn(v,contents)
if __name__=='__main__':unittest.main()
