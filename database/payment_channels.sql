-- Safe, non-destructive: run once against Saphire V2 Supabase.
ALTER TABLE public.invoices ADD COLUMN IF NOT EXISTS channel_id bigint;
ALTER TABLE public.invoices ADD COLUMN IF NOT EXISTS paypal_reference text;
ALTER TABLE public.invoices ADD COLUMN IF NOT EXISTS status_message_id bigint;
CREATE UNIQUE INDEX IF NOT EXISTS invoices_paypal_reference_unique
 ON public.invoices(paypal_reference) WHERE paypal_reference IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS invoices_channel_id_unique
 ON public.invoices(channel_id) WHERE channel_id IS NOT NULL;
-- Existing invoice and transaction data is preserved.

ALTER TABLE public.invoices ALTER COLUMN crypto_amount TYPE numeric(40,18) USING crypto_amount::numeric(40,18);
