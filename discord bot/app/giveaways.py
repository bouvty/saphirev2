"""Persistent reaction giveaways for Saphire V2.

Entry source: Discord emoji reactions. Raw events are persisted for immediate
tracking, and finalization re-reads the reaction to reconcile missed events.
"""
import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

log = logging.getLogger('saphire.giveaways')
ENTRY_EMOJI = '🎉'
MAX_WINNERS = 20
MAX_DURATION_SECONDS = 60 * 60 * 24 * 30


def parse_duration(text: str) -> int:
    """Accept a duration (1h) or an absolute UTC Unix/Discord timestamp.

    Examples: 1h, 1792526400, <t:1792526400:F>, <t:1792526400:R>.
    Returns remaining seconds; always uses an absolute UTC timestamp for embeds.
    """
    text = text.strip()
    timestamp_match = re.fullmatch(r'<t:(\d{10,13})(?::[tTdDfFR])?>', text)
    if timestamp_match or re.fullmatch(r'\d{10,13}', text):
        unix = int(timestamp_match.group(1) if timestamp_match else text)
        if unix >= 10**12:  # Also allow millisecond epoch inputs.
            unix /= 1000
        seconds = round(unix - datetime.now(timezone.utc).timestamp())
        if not 10 <= seconds <= MAX_DURATION_SECONDS:
            raise ValueError('The end timestamp must be between 10 seconds and 30 days in the future.')
        return seconds
    parts = list(re.finditer(r'(\d+)([smhd])', text.lower().strip()))
    if not parts or ''.join(p.group() for p in parts) != text.lower().strip():
        raise ValueError('Duration must be like `30m`, `2h`, `1d`, or `1h30m`.')
    unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    seconds = sum(int(p.group(1)) * unit[p.group(2)] for p in parts)
    if not 10 <= seconds <= MAX_DURATION_SECONDS:
        raise ValueError('Duration must be between 10 seconds and 30 days.')
    return seconds


def giveaway_embed(prize: str, ends_at: datetime, winners: int, creator_id: int, *, ended=False, winner_ids=(), entrants=0):
    if ended:
        chosen = ', '.join(f'<@{uid}>' for uid in winner_ids) or 'No valid entries'
        body = (f'**Prize:** {discord.utils.escape_markdown(prize)}\n'
                f'**Winners:** {chosen}\n'
                f'**Entries:** {entrants}\n'
                f'**Ended:** <t:{int(ends_at.timestamp())}:F> ')
        color = 0x59bc89
        title = '🎉 Giveaway ended'
    else:
        body = (f'**Prize:** {discord.utils.escape_markdown(prize)}\n'
                f'**Winners to draw:** {winners}\n'
                f'**Ends:** <t:{int(ends_at.timestamp())}:F>\n'
                f'**Time remaining:** <t:{int(ends_at.timestamp())}:R>\n\n'
                f'React with {ENTRY_EMOJI} below to enter. Remove your reaction to withdraw.\n'
                f'**Hosted by:** <@{creator_id}>')
        color = 0x9D65EF
        title = '🎁 Saphire giveaway'
    embed = discord.Embed(title=title, description=body, color=color)
    embed.set_footer(text='Saphire • Giveaway')
    return embed


class GiveawayManager:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self._running = set()

    async def create(self, channel, owner_id: int, prize: str, seconds: int, winners: int):
        if not 1 <= winners <= MAX_WINNERS:
            raise ValueError(f'Winner count must be between 1 and {MAX_WINNERS}.')
        prize = prize.strip()
        if not 1 <= len(prize) <= 200:
            raise ValueError('Prize must contain 1–200 characters.')
        ends = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        message = await channel.send(embed=giveaway_embed(prize, ends, winners, owner_id))
        try:
            await message.add_reaction(ENTRY_EMOJI)
            async with self.db.pool.acquire() as conn:
                await conn.execute('''INSERT INTO giveaways
                    (message_id,channel_id,guild_id,creator_id,prize,emoji,ends_at,winner_count)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''',
                    message.id, channel.id, channel.guild.id, owner_id, prize,
                    ENTRY_EMOJI, ends, winners)
        except Exception:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            raise
        return message

    async def on_reaction(self, payload, added: bool):
        if not payload.guild_id or str(payload.emoji) != ENTRY_EMOJI:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        async with self.db.pool.acquire() as conn:
            # Only track live giveaways with a matching posted reaction.
            row = await conn.fetchrow('''SELECT message_id FROM giveaways
                WHERE message_id=$1 AND channel_id=$2 AND guild_id=$3
                  AND ended_at IS NULL AND ends_at > now() AND emoji=$4''',
                payload.message_id, payload.channel_id, payload.guild_id, ENTRY_EMOJI)
            if not row:
                return
            if added:
                await conn.execute('''INSERT INTO giveaway_entries(message_id,discord_id)
                    VALUES ($1,$2) ON CONFLICT DO NOTHING''', payload.message_id,payload.user_id)
            else:
                await conn.execute('''DELETE FROM giveaway_entries
                    WHERE message_id=$1 AND discord_id=$2''',payload.message_id,payload.user_id)

    async def finish(self, giveaway):
        message_id = giveaway['message_id']
        if message_id in self._running:
            return
        self._running.add(message_id)
        try:
            channel = self.bot.get_channel(giveaway['channel_id'])
            if channel is None:
                channel = await self.bot.fetch_channel(giveaway['channel_id'])
            message = await channel.fetch_message(message_id)
            reaction = next((r for r in message.reactions if str(r.emoji) == giveaway['emoji']), None)
            entrants = set()
            if reaction:
                async for member in reaction.users(limit=None):
                    if not member.bot:
                        entrants.add(member.id)
            # Store one draw and retry posting the same winners after restarts.
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    locked = await conn.fetchrow('SELECT * FROM giveaways WHERE message_id=$1 FOR UPDATE', message_id)
                    if not locked or locked['announced_at'] is not None:
                        return
                    if locked['ended_at'] is None:
                        await conn.execute('DELETE FROM giveaway_entries WHERE message_id=$1', message_id)
                        if entrants:
                            await conn.executemany('INSERT INTO giveaway_entries(message_id,discord_id) VALUES($1,$2) ON CONFLICT DO NOTHING',
                                [(message_id,uid) for uid in entrants])
                        chosen = random.SystemRandom().sample(sorted(entrants),min(len(entrants), locked['winner_count']))
                        await conn.execute('UPDATE giveaways SET ended_at=now(),winner_ids=$2::bigint[] WHERE message_id=$1',
                            message_id,chosen)
                    else:
                        chosen = list(locked['winner_ids'])
                        previous = await conn.fetch('SELECT discord_id FROM giveaway_entries WHERE message_id=$1',message_id)
                        entrants = {row['discord_id'] for row in previous}
            await message.edit(embed=giveaway_embed(locked['prize'],locked['ends_at'],locked['winner_count'],
                locked['creator_id'],ended=True,winner_ids=chosen,entrants=len(entrants)))
            async with self.db.pool.acquire() as conn:
                await conn.execute('UPDATE giveaways SET announced_at=now() WHERE message_id=$1',message_id)
            if chosen:
                try:
                    await channel.send('🎉 Congratulations ' + ', '.join(f'<@{u}>' for u in chosen) +
                        f'! You won **{discord.utils.escape_markdown(locked["prize"])}**!',
                        allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
                except discord.HTTPException:
                    log.warning('Winner announcement could not be sent for giveaway %s',message_id)
            log.info('Giveaway %s completed: %s entrants, %s winners', message_id,len(entrants),len(chosen))
        except (discord.NotFound, discord.Forbidden):
            # Keep giveaway for a later attempt if Discord access is temporarily unavailable;
            # a permanently deleted message needs a manual database cleanup.
            log.exception('Cannot fetch/edit giveaway message %s',message_id)
        except Exception:
            log.exception('Failed to finalize giveaway %s (will retry if not marked ended)',message_id)
        finally:
            self._running.discard(message_id)

    @tasks.loop(seconds=15, reconnect=True)
    async def tick(self):
        async with self.db.pool.acquire() as conn:
            due = await conn.fetch('''SELECT * FROM giveaways
                WHERE announced_at IS NULL AND ends_at <= now() ORDER BY ends_at LIMIT 25''')
        for item in due:
            await self.finish(item)

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()
