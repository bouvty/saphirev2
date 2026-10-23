# Discord registration v2

The bot creates an opaque random link at `https://saphire.rip/auth/v2/discord/<key>`. Only an HMAC hash of the key is stored, tied to the Discord user and a 10-minute expiry. Opening it starts Discord's identify authorization flow and sets a secure, host-only browser state cookie.

Discord continues to return to the already registered `https://saphire.rip/auth/callback`; no Developer Portal redirect change is needed. V2 state is dispatched to the Railway v2 callback. The API atomically consumes the state, exchanges the code, and compares Discord's returned user ID with the account that requested the link. Cancellation, wrong identity, replay, expiry, and provider errors cannot set an account as verified.

Only after verification succeeds does the API create a separate random success receipt and update `accounts.oauth_verified` in the same database transaction. The website sets a secure host-only receipt cookie and redirects to `/auth/v2/discord/success/<key>`. The receipt expires in five minutes; the page requires both the matching browser cookie and a server-validated receipt. It is a confirmation page, not a staff session or ticket-access credential.

Failures redirect to `/auth/v2/discord/denied/`. Both result pages use the existing Saphire theme. Authentication routes have no-store and no-referrer headers; backend access logging is disabled to avoid recording secret URL paths in application logs.

Apply `database/discord_auth_v2.sql` before deploying this code. The database table is private, has RLS enabled, and grants no access to anon/authenticated. The bot and API must share DATABASE_URL and OAUTH_STATE_SECRET. Existing accounts and balances are unchanged.

Old bot `/auth/start` links are adapted to the new state and success flow while the bot server is updated. New bot files create only the requested v2 links. The old unvalidated `/auth/registered` page redirects to denied.

Run `python -m pytest railway-api/tests -q` and `npm run build` before release. The new bot files are delivered as a separate server update archive; preserve the bot server's existing `.env` when installing it.
