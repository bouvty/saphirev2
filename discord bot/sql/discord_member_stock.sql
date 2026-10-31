CREATE TABLE IF NOT EXISTS public.discord_member_stock (
    member_type text PRIMARY KEY CHECK (member_type IN ('online','offline')),
    available integer NOT NULL CHECK (available >= 0),
    refreshed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.discord_member_stock ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.discord_member_stock FROM anon,authenticated;
