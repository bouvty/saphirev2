# Saphire V2

Next.js website on Vercel, FastAPI service on Railway, and a shared Supabase Postgres database. The Discord bot runs on its separate server.

## Website

Install dependencies with `npm ci`, then `npm run build`. Configure the server environment using `.env.example`. `RAILWAY_API_URL` must be the actual deployed API origin. Keep all credentials out of Git.

## Railway API

Connect this repository's `main` branch, set the service root to `/railway-api`, and use its `railway.json`. Set the variables listed in `railway-api/.env.example`, using the same database and internal/OAuth state secrets as the existing bot. The health check is `/health` and returns 503 when required database tables are unavailable.

## Database

For a new empty V2 database, apply the SQL files in this order: `schema.sql`, `payment_channels.sql`, `discount_codes.sql`, `giveaways.sql`, `discord_member_stock.sql`, `discord_member_orders.sql`, `sellauth_member_keys.sql`. These scripts reconstruct structure; they do not recover deleted customer records or invent balances or stock.

The October 2026 recovery preserved the incompatible empty schema under `recovery_before_v2_20261023`. Keep this archive until recovery is confirmed. Public Data API access to V2 tables is revoked; the bot and backend use Postgres credentials.

## Validation

`python -m pytest railway-api/tests` checks backend readiness failures, internal authorization, and ticket metadata. The separate bot must be restarted on its own host after database recovery and then refresh actual provider stock and services.
