"""No live Discord token/database required: verify guarded entry points statically."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1]/'app'/'bot.py').read_text()
TREE = ast.parse(SOURCE)


def function(name):
    return next(n for n in ast.walk(TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_prefix_commands_use_global_verification():
    fn = function('account_command_guard')
    code = ast.get_source_segment(SOURCE, fn)
    assert 'is_verified(ctx.author.id)' in code
    assert 'send_private_registration(ctx.author)' in code
    assert "'register', 'verify', 'ticketcreate'" in code
    assert 'return False' in code


def test_component_buttons_and_selects_use_global_verification():
    layout = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'SaphireLayout')
    fn = next(n for n in layout.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'interaction_check')
    code = ast.get_source_segment(SOURCE, fn)
    assert 'await registered(inter)' in code
    assert 'ReferralView, AuthChoiceView, RegistrationStatusView' in code


def test_payment_modals_require_registration_but_guest_ticket_is_allowed():
    ticket_cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'TicketModal')
    ticket_submit = next(n for n in ticket_cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'on_submit')
    assert 'await registered(inter)' not in ast.get_source_segment(SOURCE,ticket_submit)
    amount_cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'TopupAmountModal')
    amount_submit = next(n for n in amount_cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'on_submit')
    assert 'await registered(inter)' in ast.get_source_segment(SOURCE,amount_submit)


def test_private_replies_and_fail_closed():
    assert 'await user.send(view=cv2_layout(' in SOURCE
    assert 'await interaction.response.send_message(view=view, ephemeral=True)' in SOURCE
    assert 'await interaction.followup.send(view=view, ephemeral=True)' in SOURCE
    assert 'commands.CheckFailure' in SOURCE
    assert '@bot.tree.command(name=\'register\'' in SOURCE
    assert '@bot.tree.command(name=\'verify\'' in SOURCE
