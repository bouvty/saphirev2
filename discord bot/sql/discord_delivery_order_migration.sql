-- Run before deploying the updated Saphire Discord member checkout.
-- Preserve existing order records; provider keys remain stored in the existing provider_key field.
ALTER TABLE public.discord_member_orders ADD COLUMN IF NOT EXISTS provider_order_id text;
CREATE UNIQUE INDEX IF NOT EXISTS discord_member_orders_provider_order_id_unique
 ON public.discord_member_orders(provider_order_id) WHERE provider_order_id IS NOT NULL;
