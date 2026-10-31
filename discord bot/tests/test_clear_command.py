from pathlib import Path
import ast

def test_clear_command_source():
    source=(Path(__file__).resolve().parents[1]/'app'/'bot.py').read_text()
    ast.parse(source)
    assert "@bot.command(name='clear')" in source
    assert 'manage_messages=True' in source
    assert 'before=ctx.message' in source
    assert 'await ctx.message.delete()' in source
