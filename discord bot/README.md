# Saphire stock refresh fix

Replace `app/bot.py` and `app/discord_members.py` with these files, then restart the Discord bot.

Root cause: the previous background task synced stock every 10 minutes while checkout rejects stock older than 3 minutes.
Fix: refresh every 60 seconds while keeping the 3-minute fail-closed checkout freshness check.

If stock still becomes outdated, check the bot logs for `Discord member stock sync failed` and ensure the stock endpoint and Railway bot worker are running. Do not artificially update `refreshed_at` without fetching actual stock.

No SQL migration is required. No database contents were altered by this patch.
