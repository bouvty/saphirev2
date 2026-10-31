from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/'app'/'bot.py'
CODE=SOURCE.read_text()

def test_public_button_enabled():
    assert "custom_id='saphire:stock:discord'" in CODE
    area=CODE[CODE.index("@discord.ui.button(label='Discord services'"):CODE.index("@bot.command(name='stockpanel')")]
    assert 'disabled=True' not in area
    assert "browser.stage = 'discord_menu'" in area

def test_private_discord_views_and_back():
    assert "self.stage in ('discord_menu', 'discord_members')" in CODE
    assert "'discord_members': 'discord_menu'" in CODE
    assert "'discord_menu': 'home'" in CODE
    assert "'discord_boosts', 'Server boosts (coming soon)', disabled=True" in CODE
    assert "SELECT member_type, available, refreshed_at FROM discord_member_stock" in CODE

def test_refresh_task():
    assert '@tasks.loop(seconds=60, reconnect=True)\nasync def discord_member_stock_maintenance' in CODE
    assert 'if not discord_member_stock_maintenance.is_running():discord_member_stock_maintenance.start()' in CODE
