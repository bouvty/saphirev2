-- Non-destructive: optional discount codes for the bot checkout.
CREATE TABLE IF NOT EXISTS public.discount_codes (
  code text PRIMARY KEY,
  percent_off numeric(5,2) NOT NULL CHECK (percent_off>=0 AND percent_off<=100),
  active boolean NOT NULL DEFAULT true,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.discount_codes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.discount_codes FROM anon, authenticated;
-- Insert a code only after staff approve a promotion. Example:
-- INSERT INTO public.discount_codes(code,percent_off,active) VALUES ('WELCOME10',10,true);
