# Saphire V2

Modern Next.js website for Saphire: coming-soon home page, custom 404, Discord OAuth, staff-only ticket transcript viewer, and a registration guard example for the Discord bot.

The complete initial source package is being prepared separately. **No secrets should ever be committed.**

## Local start
`npm install && npm run dev`

Required environment: `NEXT_PUBLIC_SITE_URL`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `SUPPORT_ROLE_ID`, `FOUNDER_ROLE_ID`, `OWNER_IDS`, `SESSION_SECRET`, `RAILWAY_API_URL`, `BOT_INTERNAL_TOKEN`.

Discord redirect: `https://saphire.rip/api/auth/callback`.

Ticket backend contract: `GET /internal/tickets/{id}` on Railway authenticated with an internal Bearer token. Never return transcripts without staff authorization.
