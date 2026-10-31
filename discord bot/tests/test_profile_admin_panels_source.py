from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'app'/'bot.py').read_text()
CONFIG=(ROOT/'app'/'config.py').read_text()

def test_compiles():
    ast.parse(SOURCE)
    ast.parse(CONFIG)

def test_both_command_spellings():
    for phrase in ("name='profilepanel'", "name='adminpanel'", "name='profile'", "name='admin'", "@profile_group.command(name='panel')", "@admin_group.command(name='panel')"):
        assert phrase in SOURCE

def test_owner_permission():
    assert "if not owner(inter.user) or inter.user.id!=self.owner_id" in SOURCE
    assert "if not owner(ctx.author):return" in SOURCE

def test_history_filters_and_page_size():
    assert 'PROFILE_PAGE_SIZE = 5' in SOURCE
    assert "WHERE discord_id=$1" in SOURCE
    assert "LIMIT 5 OFFSET $2" in SOURCE

def test_balance_ledger_and_audit_atomic():
    assert 'async with conn.transaction()' in SOURCE
    assert 'SELECT balance_cents FROM accounts WHERE discord_id=$1 FOR UPDATE' in SOURCE
    assert 'INSERT INTO ledger(discord_id,delta_cents,kind,reference,actor_id)' in SOURCE
    assert 'INSERT INTO audit_log(actor_id,action,subject,detail)' in SOURCE
    assert "account['balance_cents']+cents<0" in SOURCE

def test_welcome_mention_cleanup():
    assert 'async def on_member_join(member)' in SOURCE
    assert 'await msg.delete(delay=1)' in SOURCE
    assert 'allowed_mentions=discord.AllowedMentions(users=[member],roles=False,everyone=False)' in SOURCE
    assert 'welcome_channel_ids' in CONFIG

def test_reusable_private_panel():
    assert 'ProfileBrowser(inter.user.id)' in SOURCE
    assert 'AdminBrowser(inter.user.id)' in SOURCE
    assert 'ephemeral=True' in SOURCE
    assert 'bot.add_view(ProfileStart());bot.add_view(AdminStart())' in SOURCE
