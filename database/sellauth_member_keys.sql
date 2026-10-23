-- Additive, does not touch existing member orders, balances, or ledgers.
CREATE TABLE IF NOT EXISTS public.sellauth_member_keys (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 shop_id text NOT NULL, invoice_id text NOT NULL, item_id text NOT NULL,
 product_id text NOT NULL, variant_id text NOT NULL DEFAULT '',
 idempotency_key text NOT NULL, request_fingerprint text NOT NULL,
 member_type text NOT NULL CHECK (member_type IN ('online','offline')),
 members integer NOT NULL CHECK (members > 0 AND members <= 100000),
 remaining_members integer NOT NULL CHECK (remaining_members >= 0 AND remaining_members <= 100000),
 key_hash text NOT NULL UNIQUE,
 key_ciphertext bytea NOT NULL,
 status text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','redeemed','revoked')),
 created_at timestamptz NOT NULL DEFAULT now(),
 redeemed_at timestamptz,
 UNIQUE(shop_id,invoice_id,item_id),
 UNIQUE(shop_id,idempotency_key),
 CHECK (remaining_members <= members)
);
CREATE INDEX IF NOT EXISTS sellauth_keys_member_status_idx
 ON public.sellauth_member_keys(member_type,status);
ALTER TABLE public.sellauth_member_keys ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sellauth_member_keys FROM anon, authenticated;
