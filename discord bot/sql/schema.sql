-- Run against a NEW, dedicated Supabase project. Never run an unreviewed DROP CASCADE.
create table if not exists accounts (
 discord_id bigint primary key, created_at timestamptz not null default now(),
 oauth_verified boolean not null default false, referred_by text,
 balance_cents bigint not null default 0 check(balance_cents>=0)
);
create table if not exists affiliate_codes (
 code text primary key, owner_id bigint not null, created_at timestamptz default now()
);
create table if not exists oauth_states (
 state_hash text primary key, discord_id bigint not null, referral_code text,
 expires_at timestamptz not null, consumed_at timestamptz
);
create table if not exists services (
 id text primary key, name text not null, category text not null,
 description text not null default '', provider_rate numeric(14,6) not null,
 retail_rate numeric(14,6) not null, min_order integer not null, max_order integer not null,
 refreshed_at timestamptz not null default now()
);
create table if not exists orders (
 id bigint generated always as identity primary key, discord_id bigint not null references accounts(discord_id),
 provider_order_id text unique, service_id text not null references services(id),
 quantity integer not null, target_url text not null, cost_cents bigint not null,
 provider_cost_cents bigint, status text not null default 'submitting',
 extra jsonb not null default '{}', created_at timestamptz not null default now()
);
create table if not exists invoices (
 id text primary key, discord_id bigint not null references accounts(discord_id),
 currency text not null, amount_cents bigint not null check(amount_cents>0),
 crypto_amount numeric(30,12), recipient_address text, txid text unique,
 status text not null default 'pending', confirmations integer not null default 0,
 required_confirmations integer, credited_at timestamptz, expires_at timestamptz,
 created_at timestamptz default now()
);
create table if not exists ledger (
 id bigint generated always as identity primary key, discord_id bigint not null references accounts(discord_id),
 delta_cents bigint not null, kind text not null, reference text not null unique,
 actor_id bigint, created_at timestamptz not null default now()
);
create table if not exists tickets (
 id bigint generated always as identity primary key, discord_id bigint not null,
 channel_id bigint unique, kind text not null, details jsonb not null,
 status text not null default 'open', created_at timestamptz default now(), closed_at timestamptz
);
create table if not exists ticket_messages (
 id bigint generated always as identity primary key, ticket_id bigint not null references tickets(id),
 author_id bigint not null, author_name text not null, content text not null, created_at timestamptz not null
);
create table if not exists audit_log (
 id bigint generated always as identity primary key, actor_id bigint, action text not null,
 subject text, detail jsonb not null default '{}', created_at timestamptz not null default now()
);
-- Discord bot and backend use the direct Postgres connection with secret DB credentials.
-- Lock Data API access down; public HTTP routes always enforce authorization in Python.
do $$ declare t text; begin
 foreach t in array array['accounts','affiliate_codes','oauth_states','services','orders','invoices','ledger','tickets','ticket_messages','audit_log'] loop
  execute format('alter table public.%I enable row level security', t);
  execute format('revoke all on public.%I from anon, authenticated', t);
 end loop;
end $$;

-- Optional non-destructive migration for rich ticket transcript content.
alter table public.ticket_messages add column if not exists metadata jsonb not null default '{}'::jsonb;
