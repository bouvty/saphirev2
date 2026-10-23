CREATE TABLE IF NOT EXISTS public.discord_auth_requests (
 token_hash text PRIMARY KEY,
 discord_id bigint NOT NULL,
 referral_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now() + interval '10 minutes',
 oauth_state_hash text UNIQUE,
 finished_at timestamptz,
 authorized_at timestamptz,
 success_hash text UNIQUE,
 success_expires_at timestamptz,
 CHECK (authorized_at IS NULL OR (finished_at IS NOT NULL AND success_hash IS NOT NULL AND success_expires_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS discord_auth_requests_expiry_idx ON public.discord_auth_requests(expires_at);
ALTER TABLE public.discord_auth_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.discord_auth_requests FROM anon,authenticated;
