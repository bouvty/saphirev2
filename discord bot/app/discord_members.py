"""Private Discord member reseller checkout (separate from Top4SMM).

Provider requests have unknown outcomes on timeout: those reservations require
staff reconciliation and are NEVER automatically retried/refunded.
"""
import asyncio
import json
import logging
import math
import re
import secrets
import string
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

import discord
import httpx

log = logging.getLogger('saphire.discord_members')
RATES = {'online': Decimal('1.10'), 'offline': Decimal('0.80')}
MIN_ORDER = {'online': 400, 'offline': 800}
REDEEM_URL = 'https://memberix.shop/'


def price_cents(kind: str, quantity: int) -> int:
    if kind not in RATES:
        raise ValueError('Select Online or Offline members.')
    if type(quantity) is not int or not MIN_ORDER[kind] <= quantity <= 100000:
        raise ValueError(f'{kind.title()} member orders must be between {MIN_ORDER[kind]:,} and the available stock')
    return int((RATES[kind] * Decimal(quantity) / Decimal(1000) * Decimal(100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) or 1


async def available_stock(conn, kind, *, lock=False):
    if kind not in MIN_ORDER:
        raise ValueError('Choose an online or offline service.')
    query = ("SELECT available, refreshed_at, "
             "refreshed_at >= now() - interval '3 minutes' AS fresh "
             "FROM public.discord_member_stock WHERE member_type=$1" +
             (" FOR UPDATE" if lock else ""))
    row = await conn.fetchrow(query, kind)
    if not row or not row['fresh']:
        raise ValueError('Stock unavailable or outdated. Try again after the next stock refresh.')
    used = await conn.fetchval("SELECT COALESCE(SUM(quantity),0) FROM public.discord_member_orders "
        "WHERE member_type=$1 AND created_at > $2 AND status IN ('reserved','accepted','needs_review')",kind,row['refreshed_at'])
    return max(0,int(row['available'])-int(used or 0))


def ensure_stock(kind, quantity, stock):
    price_cents(kind, quantity)
    if stock < MIN_ORDER[kind]:
        raise ValueError(f'Only {stock:,} {kind} members in stock; minimum is {MIN_ORDER[kind]:,}.')
    if quantity > stock:
        raise ValueError(f'Only {stock:,} {kind} members are available.')


def validate_invite(raw: str) -> str:
    raw = raw.strip()
    if len(raw) > 250 or not raw.startswith('https://'):
        raise ValueError('Enter a complete HTTPS Discord invite link.')
    u = urlsplit(raw)
    if u.username or u.password or u.port or u.query or u.fragment:
        raise ValueError('Use a standard Discord invite URL without extra parameters.')
    if u.hostname not in ('discord.gg', 'discord.com', 'www.discord.com', 'discordapp.com'):
        raise ValueError('Link must be a Discord server invitation.')
    path = u.path.strip('/')
    if u.hostname == 'discord.gg':
        code = path
    elif path.startswith('invite/'):
        code = path[7:]
    else:
        raise ValueError('Link must be a Discord server invitation.')
    if not re.fullmatch(r'[A-Za-z0-9-]{2,64}', code):
        raise ValueError('Discord invite code is invalid.')
    return f'https://discord.gg/{code}'


async def provider_request(settings, order_id: str, kind: str, quantity: int) -> dict:
    base = settings.discord_member_api_base_url.strip().rstrip('/')
    key = settings.discord_member_api_key.strip()
    if not base.startswith('https://') or not urlsplit(base).hostname or not key:
        raise ValueError('Discord member provider API URL/key is not configured.')
    url = base + '/api/v2/custom/reseller/api/order'
    payload = {'order_id': order_id, 'type': kind, 'quantity': quantity}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20), follow_redirects=False) as client:
        response = await client.post(url, headers={'Authorization': key, 'Accept': 'application/json'}, json=payload)
    # Never expose Authorization, provider key, response URL or HTTP request headers.
    if response.status_code != 200:
        raise RuntimeError(f'Provider returned HTTP {response.status_code}; reconciliation required.')
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError('Provider returned a non-JSON response; reconciliation required.') from exc
    if not isinstance(data, dict) or data.get('success') is not True:
        raise RuntimeError('Provider did not confirm order acceptance; reconciliation required.')
    if str(data.get('order_id')) != order_id or data.get('type') != kind or data.get('quantity') != quantity:
        raise RuntimeError('Provider order confirmation did not match the request; reconciliation required.')
    supplied_key = data.get('key')
    if not isinstance(supplied_key, str) or not supplied_key.strip() or len(supplied_key) > 2048:
        raise RuntimeError('Provider did not supply a usable access key; reconciliation required.')
    credits = data.get('credits_left')
    if isinstance(credits, bool) or not isinstance(credits, (int, float, str)):
        raise RuntimeError('Provider credits_left is invalid; reconciliation required.')
    try:
        credits = Decimal(str(credits))
        if not credits.is_finite() or credits < 0: raise ValueError()
    except Exception as exc:
        raise RuntimeError('Provider credits_left is invalid; reconciliation required.') from exc
    return {'key': supplied_key.strip(), 'credits_left': credits}


async def reserve_order(db, user_id: int, kind: str, quantity: int, invite: str) -> tuple[str, int]:
    cents = price_cents(kind, quantity)
    order_id = 'dm_' + secrets.token_hex(10)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            stock = await available_stock(conn, kind, lock=True)
            ensure_stock(kind, quantity, stock)
            acct = await conn.fetchrow('SELECT balance_cents,oauth_verified FROM accounts WHERE discord_id=$1 FOR UPDATE', user_id)
            if not acct or not acct['oauth_verified']:
                raise ValueError('You must register your Discord account first.')
            if acct['balance_cents'] < cents:
                raise ValueError(f'Insufficient balance. Order requires ${cents / 100:.2f}.')
            await conn.execute('''INSERT INTO discord_member_orders(id,discord_id,member_type,quantity,invite_url,amount_cents,status)
                                  VALUES($1,$2,$3,$4,$5,$6,'reserved')''', order_id, user_id, kind, quantity, invite, cents)
            await conn.execute('UPDATE accounts SET balance_cents=balance_cents-$2 WHERE discord_id=$1', user_id, cents)
            await conn.execute('''INSERT INTO ledger(discord_id,delta_cents,kind,reference)
                                  VALUES($1,$2,'discord_members',$3)''', user_id, -cents, f'discord_members:{order_id}')
    return order_id, cents


async def mark_provider_success(db, order_id: str, result: dict) -> bool:
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Locks prevent duplicate settlements if multiple workers race.
            row = await conn.fetchrow('SELECT * FROM discord_member_orders WHERE id=$1 FOR UPDATE', order_id)
            if not row or row['status'] != 'reserved': return False
            await conn.execute('''UPDATE discord_member_orders SET status='accepted',provider_key=$2,
                     provider_credits_left=$3,provider_order_id=$4,accepted_at=now() WHERE id=$1''',
                     order_id, result['key'], result['credits_left'], result['order_id'])
            await conn.execute('''INSERT INTO discord_member_provider_credit_log(order_id,credits_left)
                                  VALUES($1,$2) ON CONFLICT(order_id) DO NOTHING''', order_id, result['credits_left'])
    return True


async def mark_needs_review(db, order_id: str):
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE discord_member_orders SET status='needs_review' WHERE id=$1 AND status='reserved'", order_id)


async def notify_logs(bot, settings, db, order_id, user_id, kind, quantity, cents, credits):
    channel = bot.get_channel(settings.transaction_log_channel_id)
    if channel:
        view = discord.ui.View(timeout=None)
        if settings.order_panel_channel_id:
            view.add_item(discord.ui.Button(label='Make an order', style=discord.ButtonStyle.link,
                        url=f'https://discord.com/channels/{settings.discord_guild_id}/{settings.order_panel_channel_id}'))
        if settings.topup_panel_channel_id:
            view.add_item(discord.ui.Button(label='Add credit to balance',style=discord.ButtonStyle.link,
                        url=f'https://discord.com/channels/{settings.discord_guild_id}/{settings.topup_panel_channel_id}'))
        embed = discord.Embed(title='Discord member order accepted', color=0x9D65EF,
            description=f'**Order:** `{order_id}`\n**Customer:** <@{user_id}>\n**Service:** {kind.title()} members\n**Quantity:** {quantity:,}\n**Charged:** ${cents/100:.2f}\n**Created:** <t:{int(discord.utils.utcnow().timestamp())}:R>')
        try: await channel.send(embed=embed, view=view)
        except discord.HTTPException:log.exception('Discord member history log failed')
    # Credits remaining are provider-only data, never shown in public purchase logs.
    owner_channel = bot.get_channel(settings.discord_member_owner_log_channel_id)
    if owner_channel:
        # Fail closed if the owner log is visible to the @everyone role.
        if isinstance(owner_channel, discord.TextChannel) and owner_channel.permissions_for(owner_channel.guild.default_role).view_channel:
            log.error('Owner API credits log channel is visible to @everyone; refusing to post provider credits.')
            return
        try:
            await owner_channel.send(embed=discord.Embed(title='Reseller credit balance (owner only)',color=0x9D65EF,
                description=f'**Order:** `{order_id}`\n**API credits left:** `{credits}`\n**Customer:** <@{user_id}>'))
        except discord.HTTPException:log.exception('Provider credit owner log failed')


class MemberAmountModal(discord.ui.Modal, title='Configure Discord member order'):
    def __init__(self, wizard):
        super().__init__(timeout=300)
        self.wizard = wizard
        self.quantity = discord.ui.TextInput(label='Quantity of members',placeholder='amount of members',required=True,max_length=6)
        self.invite = discord.ui.TextInput(label='Discord server invite',placeholder='https://discord.gg/saphiree',required=True,max_length=250)
        self.add_item(self.quantity)
        self.add_item(self.invite)

    async def on_submit(self, interaction):
        if interaction.user.id != self.wizard.user_id:
            return await interaction.response.send_message('This checkout belongs to another user.',ephemeral=True)
        try:
            qty = int(str(self.quantity.value).strip())
            invite = validate_invite(str(self.invite.value))
            price_cents(self.wizard.kind, qty)
        except (ValueError, TypeError) as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        try:
            async with self.wizard.db.pool.acquire() as conn:
                stock = await available_stock(conn, self.wizard.kind)
            ensure_stock(self.wizard.kind, qty, stock)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        self.wizard.stock = stock
        self.wizard.quantity, self.wizard.invite = qty, invite
        self.wizard.stage = 'review'
        await interaction.response.edit_message(view=self.wizard.layout())


class MemberButton(discord.ui.Button):
    def __init__(self, wizard, action, label, *, disabled=False, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, disabled=disabled, style=style)
        self.wizard, self.action = wizard, action
    async def callback(self, interaction):
        await self.wizard.handle(interaction, self.action)


class MemberTypes(discord.ui.Select):
    def __init__(self, wizard):
        super().__init__(placeholder='Choose member type',options=[
            discord.SelectOption(label='Online members',value='online', description='$1.10 per 1k'),
            discord.SelectOption(label='Offline members',value='offline', description='$0.80 per 1k')])
        self.wizard = wizard
    async def callback(self, interaction):
        await self.wizard.handle(interaction, self.values[0])


class MemberOrderSuccess(discord.ui.View):
    """Build a private, immediate delivery confirmation from committed database data."""
    def __init__(self, user_id, db, order_id, layout_factory, embed_factory, bot):
        super().__init__(timeout=900)
        self.user_id, self.db, self.order_id = user_id, db, order_id
        self.bot = bot
        self.layout_factory, self.embed_factory = layout_factory, embed_factory

    async def layout(self):
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT discord_id,status,provider_order_id,provider_key,invite_url "
                "FROM discord_member_orders WHERE id=$1", self.order_id)
        if (not row or row['discord_id'] != self.user_id or
                row['status'] != 'accepted' or not row['provider_key']):
            return self.layout_factory(self.embed_factory(
                'Order requires review',
                f'Order `{self.order_id}` was accepted, but delivery details are unavailable. '
                'Contact staff. Do not submit another order.'))
        self.clear_items()
        self.add_item(discord.ui.Button(label='Redeem your order',
            style=discord.ButtonStyle.link, url=REDEEM_URL))
        # Resolve the guild ID from the invite saved on the customer's order.
        # Never show the invite URL in the success message.
        server_id = None
        if row['invite_url']:
            try:
                invite = await self.bot.fetch_invite(row['invite_url'])
                if invite.guild is not None:
                    server_id = str(invite.guild.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning('Unable to resolve server ID for member order %s', self.order_id)
        key = discord.utils.escape_markdown(str(row['provider_key'])).replace('@', '@\u200b')
        provider_order = discord.utils.escape_markdown(str(row['provider_order_id'] or self.order_id))
        description = (f'```Invoice ID - {provider_order}\n'
                       f'Delivery key - {key}\n'
                       f'Server ID - {server_id or "Unavailable"}\n\n'
                       'Use the button below to redeem your order. Keep your key private.```')
        return self.layout_factory(self.embed_factory('Order proccesed', description), self)


class MemberWizard(discord.ui.View):
    def __init__(self, user_id, bot, db, settings, layout_factory, embed_factory):
        super().__init__(timeout=900)
        self.user_id,self.bot,self.db,self.settings = user_id,bot,db,settings
        self.layout_factory,self.embed_factory = layout_factory,embed_factory
        self.stage = 'main'
        self.kind = None
        self.quantity = None
        self.invite = None
        self.working = False
        self.stock = None

    def layout(self):
        v = discord.ui.View(timeout=900)
        if self.stage == 'main':
            title = 'Discord services'
            description = 'Select **Server members** to order. Server boosts currently experiencing maintenance.'
            v.add_item(MemberButton(self,'members','Server members',style=discord.ButtonStyle.primary))
            v.add_item(MemberButton(self,'boosts','Server boosts',disabled=True))
        elif self.stage == 'type':
            title='Server members'
            description = 'Select an online or offline service.\n```Online - $1.10/1k``` ```Offline - $0.80/1k```'
            v.add_item(MemberTypes(self))
            v.add_item(MemberButton(self,'main','Back · Main menu'))
        elif self.stage == 'chosen':
            title=f'{self.kind.title()} Discord members'
            stock_text = f'{self.stock:,} members' if self.stock is not None else 'Unavailable — stock refresh required'
            description=(f'```Rate - ${RATES[self.kind]:.2f}/1k\n'
                         f'Minimum order - {MIN_ORDER[self.kind]:,} members\n'
                         f'Available stock - {stock_text} per server```\n'
                        )
            v.add_item(MemberButton(self,'configure','Configure order',style=discord.ButtonStyle.primary))
            v.add_item(MemberButton(self,'place','Place order',disabled=True))
            v.add_item(MemberButton(self,'type','Back'))
            v.add_item(MemberButton(self,'main','Main menu'))
        elif self.stage == 'review':
            cents=price_cents(self.kind,self.quantity)
            title='Discord member order'
            stock_text = f'{self.stock:,} members' if self.stock is not None else 'Unavailable'
            description=(f'```Service - {self.kind.title()} members\n'
                         f'Quantity - {self.quantity:,} members\n'
                         f'Rate - ${RATES[self.kind]:.2f}/1k\n'
                         f'Total order price - ${cents/100:.2f}```')
            v.add_item(MemberButton(self,'place','Place order',style=discord.ButtonStyle.success))
            v.add_item(MemberButton(self,'configure','Edit order'))
            v.add_item(MemberButton(self,'chosen','Back'))
            v.add_item(MemberButton(self,'main','Main menu'))
        else:
            title='Saphire order processor'
            description='<a:1378550084589064222:1562855413500747939> **Your order is being processed. please wait.**'
        return self.layout_factory(self.embed_factory(title,description),v)

    async def handle(self, inter, action):
        if inter.user.id!=self.user_id:
            return await inter.response.send_message('Start your own private order from the public panel.',ephemeral=True)
        if self.working:
            return await inter.response.send_message('Your order is currently processing.',ephemeral=True)
        if action in ('main','members','type','chosen','online','offline'):
            if action=='members':self.stage='type'
            elif action in RATES:
                self.kind=action;self.stage='chosen';self.quantity=None;self.invite=None
                try:
                    async with self.db.pool.acquire() as conn:
                        self.stock = await available_stock(conn, self.kind)
                except ValueError:
                    self.stock = None
            else:self.stage=action
            return await inter.response.edit_message(view=self.layout())
        if action=='configure' and self.kind:
            return await inter.response.send_modal(MemberAmountModal(self))
        if action=='place' and self.stage=='review' and self.kind and self.quantity and self.invite:
            if not self.settings.discord_delivery_api_key.strip():
                return await inter.response.send_message('The Discord delivery API is not configured. Contact staff.',ephemeral=True)
            self.working=True
            self.stage='processing'
            await inter.response.edit_message(view=self.layout())
            try:
                order_id,cents=await reserve_order(self.db,self.user_id,self.kind,self.quantity,self.invite)
            except ValueError as exc:
                self.stage='review';self.working=False
                return await inter.edit_original_response(view=self.layout_factory(self.embed_factory('Order not placed', str(exc))))
            except Exception:
                self.working=False
                log.exception('Could not reserve member order')
                return await inter.edit_original_response(view=self.layout_factory(self.embed_factory('Order unavailable', 'Could not reserve balance. Please contact support.')))
            # Match ;discord order test: Discorium v2 delivery endpoint with a
            # unique provider id, exact response validation, and no auto retry.
            # The invite was already stored privately in discord_member_orders.
            from .discord_delivery_test import request_delivery, DeliveryRejected
            provider_id = 'dsc-' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
            try:
                response = await request_delivery(
                    self.settings.discord_delivery_api_key, provider_id, self.kind, self.quantity)
                ok = await mark_provider_success(self.db, order_id, response)
                if not ok:
                    raise RuntimeError('Delivery was accepted but could not be recorded; reconciliation required.')
            except DeliveryRejected:
                log.warning('Member delivery explicitly rejected; order=%s requires reconciliation',order_id)
                try: await mark_needs_review(self.db,order_id)
                except Exception: log.exception('Could not mark member order %s for review',order_id)
                await inter.edit_original_response(view=self.layout_factory(self.embed_factory('Order requires review',
                    f'**Order:** `{order_id}`\nThe delivery provider rejected this request. '
                    'Your reserved balance requires staff reconciliation before retrying.')))
                return
            except Exception:
                # A timeout or malformed response might still mean the provider
                # accepted the order. Never automatically retry or refund.
                log.exception('Discord member delivery %s needs staff review',order_id)
                try: await mark_needs_review(self.db,order_id)
                except Exception: log.exception('Failed to flag order for review: %s',order_id)
                await inter.edit_original_response(view=self.layout_factory(self.embed_factory('Order under review',
                    f'**Order:** `{order_id}`\nThe delivery provider did not return a verified response. '
                    'Your funds remain reserved. Contact support before retrying.')))
                return
            try:
                await notify_logs(self.bot,self.settings,self.db,order_id,self.user_id,self.kind,self.quantity,cents,response['credits_left'])
            except Exception:log.exception('Failed to post order logs')
            success = MemberOrderSuccess(self.user_id, self.db, order_id,
                                         self.layout_factory, self.embed_factory, self.bot)
            await inter.edit_original_response(view=await success.layout())
            return
        await inter.response.send_message('Select an option from the current page.',ephemeral=True)


class MemberPanel(discord.ui.View):
    def __init__(self, bot, db, settings, layout_factory, embed_factory, is_verified, registration_prompt):
        super().__init__(timeout=None)
        self.bot,self.db,self.settings=bot,db,settings
        self.layout_factory,self.embed_factory=layout_factory,embed_factory
        self.is_verified,self.registration_prompt=is_verified,registration_prompt

    @discord.ui.button(label='Start',emoji='🛒',style=discord.ButtonStyle.primary,custom_id='saphire:discordmembers:start')
    async def start(self, inter, button):
        if not await self.is_verified(inter.user.id):
            return await self.registration_prompt(inter)
        wizard=MemberWizard(inter.user.id,self.bot,self.db,self.settings,self.layout_factory,self.embed_factory)
        await inter.response.send_message(view=wizard.layout(),ephemeral=True)
