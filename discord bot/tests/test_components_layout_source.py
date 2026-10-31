"""Fast sanity checks without the Discord gateway or credentials."""
from pathlib import Path
import ast

BOT = Path(__file__).resolve().parents[1] / 'app' / 'bot.py'

def test_components_v2_replaces_legacy_embeds():
    source = BOT.read_text()
    ast.parse(source)
    assert 'class SaphireLayout(discord.ui.LayoutView)' in source
    assert 'discord.ui.Container(' in source
    assert 'discord.ui.ActionRow(' in source
    assert source.count('view=cv2_layout(') >= 35  # some duplicate public registration prompts were removed
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('send', 'send_message', 'edit', 'edit_message'):
            keys = {x.arg for x in node.keywords}
            assert 'embed' not in keys
            assert not ('view' in keys and 'content' in keys)
