CREATE TABLE IF NOT EXISTS public.discord_member_orders (
 id text PRIMARY KEY,
 discord_id bigint NOT NULL REFERENCES public.accounts(discord_id),
 member_type text NOT NULL CHECK (member_type IN ('online','offline')),
 quantity integer NOT NULL CHECK (quantity > 0 AND quantity <= 100000),
 invite_url text NOT NULL,
 amount_cents bigint NOT NULL CHECK (amount_cents > 0),
 status text NOT NULL DEFAULT 'reserved',
 provider_key text,
 provider_credits_left numeric(20,6),
 provider_order_id text,
 created_at timestamptz NOT NULL DEFAULT now(),
 accepted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS discord_member_orders_provider_order_id_unique
 ON public.discord_member_orders(provider_order_id) WHERE provider_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS discord_member_orders_stock_idx
 ON public.discord_member_orders(member_type,created_at) WHERE status IN ('reserved','accepted','needs_review');
CREATE TABLE IF NOT EXISTS public.discord_member_provider_credit_log (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 order_id text NOT NULL UNIQUE REFERENCES public.discord_member_orders(id),
 credits_left numeric(20,6) NOT NULL CHECK (credits_left >= 0),
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.discord_member_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discord_member_provider_credit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.discord_member_orders,public.discord_member_provider_credit_log FROM anon,authenticated;
