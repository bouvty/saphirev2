-- Safe, additive schema: does not modify existing customer data.
CREATE TABLE IF NOT EXISTS public.giveaways (
    message_id bigint PRIMARY KEY,
    channel_id bigint NOT NULL,
    guild_id bigint NOT NULL,
    creator_id bigint NOT NULL,
    prize text NOT NULL,
    emoji text NOT NULL DEFAULT '🎉',
    ends_at timestamptz NOT NULL,
    winner_count integer NOT NULL CHECK (winner_count BETWEEN 1 AND 20),
    ended_at timestamptz,
    announced_at timestamptz,
    winner_ids bigint[] NOT NULL DEFAULT '{}'::bigint[],
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.giveaway_entries (
    message_id bigint NOT NULL REFERENCES public.giveaways(message_id) ON DELETE CASCADE,
    discord_id bigint NOT NULL,
    entered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id,discord_id)
);
CREATE INDEX IF NOT EXISTS giveaways_due_idx ON public.giveaways(ends_at) WHERE ended_at IS NULL;
ALTER TABLE public.giveaways ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.giveaway_entries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.giveaways,public.giveaway_entries FROM anon,authenticated;
