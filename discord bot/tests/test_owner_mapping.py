from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BOT=(ROOT/'app/bot.py').read_text()

def test_owner_is_attached_at_creation():
    source=(ROOT/'app/db.py').read_text()
    assert 'insert into orders(discord_id,service_id,quantity,target_url,cost_cents,extra)' in source
    assert 'values($1,$2,$3,$4,$5,$6::jsonb)' in source
    assert 'insert into ledger(discord_id,delta_cents,kind,reference)' in source
    assert "f'order:{row[\"id\"]}'" in source
    assert 'insert into invoices(id,discord_id,currency,amount_cents' in BOT

def test_history_and_self_serve_queries_include_owner():
    assert '**Discord user:** <@{user_id}>' in BOT
    assert 'from invoices where discord_id=$1 order by created_at desc' in BOT
    assert 'from invoices where id=$1 and discord_id=$2' in BOT
    assert 'from orders where discord_id=$1 order by id desc' in BOT
    assert 'where id=$3 and discord_id=$4' in BOT
    assert 'where id=$4 and discord_id=$5' in BOT
