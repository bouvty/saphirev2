# New owner commands

- `;stopsearch` stops automatic Top4SMM catalogue and Discord member stock refresh tasks for this bot process. Restarting the bot re-enables them. `;syncservices` still works manually.
- `;restock panel role` sends a persistent Components V2 role-claim panel. Configure `RESTOCK_ROLE_ID` in `.env`, and give the bot Manage Roles and a role above Restock. Users do not need to be registered to claim the role.
- `;restock panel` posts online/offline stock from Supabase. Missing, stale (>3 min), or unreadable stock appears as 0. Snapshot is static, not auto-updating.
- `;verify panel` posts a button linking to `VERIFY_PANEL_URL` in `.env` (HTTPS required). This is separate from the existing `;verify` account check.

Use the files in this zip as your existing project layout, with all Python modules under `app/`. Place your own existing `.env` beside `requirements.txt`, never commit or share it. No SQL migrations required for these commands (stock table must already exist).
