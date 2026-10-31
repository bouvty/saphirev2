import asyncio, hashlib, hmac, io, json, logging, os, random, re, time
import secrets
import qrcode
import datetime as dt
from .service_text import format_service_description
from urllib.parse import urlsplit, urlunsplit
import asyncpg
from decimal import Decimal, ROUND_HALF_UP
import discord
from discord.ext import commands, tasks
from discord import app_commands
from .config import settings
from . import db, provider, discord_members, discord_delivery_test, payments, member_stock
from .giveaways import GiveawayManager, parse_duration
from .registration_links import create_registration_link

log=logging.getLogger('saphire')
intents=discord.Intents.default(); intents.message_content=True; intents.members=True
bot=commands.Bot(command_prefix=(';', ':'), intents=intents, help_command=None)
giveaway_manager = GiveawayManager(bot, db)

ASCII=r''' 
,e,            ,e,                     d8
 "  888-~88e    "   e88~~8e   e88~~ _d88__
888 888  888   888 d888  88b d888     888
888 888  888   888 8888__888 8888     888
888 888  888   888 Y888    , Y888     888
888 888  888   88P  "88___/   "88__/  "88_/
            \_8"
'''

EMBED_COLOR = 0x000000

def emb(title, desc='', color=EMBED_COLOR):
    # Enforce a consistent black colour for every Saphire embed, including
    # previously colour-coded success/error messages.
    e = discord.Embed(title=f'<:saphire:1561917579126251560> {title}', description=desc, color=EMBED_COLOR)
    e.set_footer(text='Saphire • Secure social services')
    return e

class SaphireLayout(discord.ui.LayoutView):
    def __init__(self, legacy_embed, controls=None, *, qr_filename=None):
        super().__init__(timeout=getattr(controls, 'timeout', None))
        self.legacy_controls = controls
        title = str(legacy_embed.title or '').strip()[:240]
        description = str(legacy_embed.description or '').strip()
        accent = discord.Colour(EMBED_COLOR)
        container = discord.ui.Container(accent_colour=accent)
        if title:
            container.add_item(discord.ui.TextDisplay('# ' + title))
        if description:
            if qr_filename:
                # Components V2 Section accessory appears to the RIGHT of the text.
                # TextDisplay + Thumbnail is preferable to an image below the card.
                container.add_item(discord.ui.Section(
                    discord.ui.TextDisplay(description[:3800]),
                    accessory=discord.ui.Thumbnail(media=f'attachment://{qr_filename}'),
                ))
            else:
                for i in range(0, min(len(description), 12000), 3800):
                    container.add_item(discord.ui.TextDisplay(description[i:i+3800]))
        for field in legacy_embed.fields:
            container.add_item(discord.ui.TextDisplay('**' + field.name + '**\n' + field.value))
        if controls is not None:
            items = list(controls.children)
            controls.clear_items()
            buttons = []
            for item in items:
                if isinstance(item, discord.ui.Button):
                    buttons.append(item)
                    if len(buttons) == 5:
                        container.add_item(discord.ui.ActionRow(*buttons))
                        buttons = []
                else:
                    if buttons:
                        container.add_item(discord.ui.ActionRow(*buttons))
                        buttons = []
                    container.add_item(discord.ui.ActionRow(item))
            if buttons:
                container.add_item(discord.ui.ActionRow(*buttons))
        footer = legacy_embed.footer.text if legacy_embed.footer else None
        if footer:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay('-# ' + footer))
        self.add_item(container)

    async def interaction_check(self, inter):
        if not isinstance(self.legacy_controls, (ReferralView, AuthChoiceView, RegistrationStatusView, TicketPanel, CloseTicketView, OrderStart, OrderWizard, InvoiceControls, TopupStart, TopupSelection, TopupAmountButton, OverrideStart, OverrideActions, ProfileStart, ProfileBrowser, AdminStart, AdminBrowser, StockPanelStart, StockBrowser, RestockRoleView)):
            if not await registered(inter):
                return False
        if self.legacy_controls is not None:
            return await self.legacy_controls.interaction_check(inter)
        return True

def cv2_layout(embed, controls=None, *, qr_filename=None):
    return SaphireLayout(embed, controls, qr_filename=qr_filename)


def make_crypto_payment_qr(coin, wallet, amount, invoice_id):
    """Create a wallet payment URI QR, attached to the invoice instructions only."""
    amount = Decimal(str(amount))
    if amount <= 0 or not amount.is_finite():
        raise ValueError('Invalid crypto payment amount')
    text_amount = format(amount, 'f')
    if coin == 'BTC':
        uri = f'bitcoin:{wallet}?amount={text_amount}'
    elif coin == 'LTC':
        uri = f'litecoin:{wallet}?amount={text_amount}'
    elif coin == 'ETH':
        # EIP-681: native ETH transfer value is in wei.
        wei = int(amount * Decimal(10) ** 18)
        if Decimal(wei) != amount * Decimal(10) ** 18:
            raise ValueError('Ethereum payment has sub-wei precision')
        uri = f'ethereum:{wallet}@1?value={wei}'
    elif coin == 'SOL':
        # Solana Pay transfer URL; wallet compatibility depends on the app.
        uri = f'solana:{wallet}?amount={text_amount}'
    else:
        raise ValueError('Unsupported cryptocurrency')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=8, border=3)
    qr.add_data(uri)
    qr.make(fit=True)
    output = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(output, format='PNG')
    output.seek(0)
    filename = f'payment-qr-{invoice_id}.png'
    return discord.File(output, filename=filename), filename

async def send_private_registration(user):
    """Prefix commands cannot be ephemeral: send the registration card by DM."""
    try:
        await user.send(view=cv2_layout(
            emb('Registration required',
                'Your Saphire account is not verified yet. Complete registration below '
                'before using commands and panels. Only you can see this message.'),
            ReferralView()))
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.info('Cannot DM registration prompt to user %s', user.id)
        return False


@bot.check
async def account_command_guard(ctx):
    if ctx.command and ctx.command.qualified_name in ('register', 'verify', 'ticketcreate', 'orderpanel', 'stockpanel', 'profilepanel', 'adminpanel', 'profile', 'profile panel', 'admin', 'admin panel', 'giveaway', 'discordservicle', 'discordservice', 'discordservicepanel', 'discordcredits', 'discord', 'discord order', 'discord order test', 'stopsearch', 'restock'):
        return True
    try:
        if await is_verified(ctx.author.id):
            return True
    except Exception:
        log.exception('Account lookup failed during command guard for %s', ctx.author.id)
        try:
            await ctx.author.send('Saphire was unable to verify your account')
        except (discord.Forbidden, discord.HTTPException):
            pass
        return False
    await send_private_registration(ctx.author)
    return False

def owner(user): return user.id in settings.owners
def role(user,rid): return bool(rid and isinstance(user,discord.Member) and any(r.id==rid for r in user.roles))
def support(user): return owner(user) or role(user,settings.support_role_id) or role(user,settings.founder_role_id) or role(user,settings.community_manager_role_id)
def founder(user): return owner(user) or role(user,settings.founder_role_id)

async def registered(interaction):
    """Allow verified users; privately prompt anyone else, failing closed on DB errors."""
    try:
        if await is_verified(interaction.user.id):
            return True
        view = cv2_layout(emb('Registration required',
            'To use saphire you need to create an account follow the steps below to continue. '
            ), ReferralView())
    except Exception:
        log.exception('Registration lookup failed for interaction user %s', interaction.user.id)
        view = cv2_layout(emb('Verification unavailable',
            'Saphire could not verify your account right now. Try again shortly.', 0xF1AD60))
    try:
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        log.warning('Unable to send private verification prompt for %s', interaction.user.id)
    return False

class ReferralModal(discord.ui.Modal,title='Affiliator code'):
    code=discord.ui.TextInput(label='Referral code',max_length=64)
    async def on_submit(self,inter):
        async with db.pool.acquire() as conn:
            exists=await conn.fetchval('select 1 from affiliate_codes where code=$1',str(self.code.value).strip())
        if not exists:
            await inter.response.send_message(view=cv2_layout(emb('Inavlid referral code','referral code was invalid.'), None),ephemeral=True);return
        await inter.response.send_message(view=cv2_layout(emb('Choose registration','Continue with Discord to verify your account.'), AuthChoiceView(str(self.code.value).strip())),ephemeral=True)

class ReferralView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180)
    @discord.ui.button(label='Continue',style=discord.ButtonStyle.primary)
    async def skip(self,inter,button):
        await inter.response.edit_message(view=cv2_layout(emb('Registration','Continue by pressing the button below and authorizing through discord.'), AuthChoiceView()))
    @discord.ui.button(label='Affiliator code',style=discord.ButtonStyle.secondary)
    async def refer(self,inter,button): await inter.response.send_modal(ReferralModal())

# OAuth is authoritative: only the Railway callback can mark accounts as verified.
# The bot watches that database record and edits the exact private registration embed.
REGISTRATION_TIMEOUT = 600
REGISTRATION_CHECK_INTERVAL = 5
registration_watchers = set()


def registration_success_embed(user):
    return emb('Registration successful',
               'Thank you for choosing to use saphire \n'
               'Your account has been made, you can now use saphire. Get start by checking our <#1561935586561105992>',
               color=0xF1AD60)


async def is_verified(user_id):
    account = await db.account(user_id)
    return bool(account and account['oauth_verified'])


class RegistrationStatusView(discord.ui.View):
    def __init__(self, user_id, url):
        super().__init__(timeout=REGISTRATION_TIMEOUT)
        self.user_id = user_id
        self.verified_displayed = False
        self.add_item(discord.ui.Button(label='Authorization', url=url))

    async def interaction_check(self, inter):
        if inter.user.id != self.user_id:
            await inter.response.send_message('This registration belongs to someone else.`.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Check status', style=discord.ButtonStyle.primary)
    async def check(self, inter, button):
        try:
            complete = await is_verified(self.user_id)
        except Exception:
            log.exception('Could not authorize status for %s', self.user_id)
            await inter.response.send_message('The registration service is temporarily unavailable. Try again.', ephemeral=True)
            return
        if complete:
            self.verified_displayed = True
            self.stop()
            await inter.response.edit_message(view=cv2_layout(registration_success_embed(inter.user), None))
        else:
            await inter.response.send_message(
                view=cv2_layout(emb('Authorization still not completed', 'Authorize via the "Open Discord authorization" button.'
                          'It updates automatically when your account is authorized.'), None), ephemeral=True)


async def monitor_registration(message, user, view):
    deadline = time.monotonic() + REGISTRATION_TIMEOUT
    failures = 0
    while time.monotonic() < deadline and not view.is_finished():
        await asyncio.sleep(REGISTRATION_CHECK_INTERVAL)
        try:
            complete = await is_verified(user.id)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            failures += 1
            if failures in (1, 6, 12):
                log.exception('Registration status polling failed for %s', user.id)
            continue
        if complete:
            view.verified_displayed = True
            view.stop()
            try:
                await message.edit(view=cv2_layout(registration_success_embed(user), None))
            except (discord.NotFound, discord.HTTPException):
                log.warning('Verified user %s; could not edit original registration embed.', user.id)
            return
    if view.verified_displayed:
        return 
    view.stop()
    try:
        await message.edit(view=cv2_layout(emb('Authorization timed out',
              'Authorization was not confirmed within 10 minutes. '
              'If you finished OAuth, and it still didnt authorize you open a support ticket.'
              'Otherwise start a new registration by using our services. It will promp you to make an account if not already made', color=0xF1AD60), None))
    except (discord.NotFound, discord.HTTPException):
        pass


class AuthChoiceView(discord.ui.View):
    def __init__(self, referral=None):
        super().__init__(timeout=300)
        self.referral = referral

    @discord.ui.button(label='Auth on Discord', style=discord.ButtonStyle.primary)
    async def oauth(self, inter, button):
        # A component defer (without thinking=True) acknowledges a message update.
        # edit_original_response therefore edits the SAME private registration card,
        # rather than sending a second ephemeral follow-up.
        await inter.response.defer()
        try:
            if await is_verified(inter.user.id):
                await inter.edit_original_response(
                    view=cv2_layout(registration_success_embed(inter.user)))
                return
            if self.referral:
                await db.register(inter.user.id, self.referral, False)
        except Exception:
            log.exception('Could not check account before OAuth for %s', inter.user.id)
            await inter.edit_original_response(view=cv2_layout(
                emb('Service unavailable',
                    'Registration could not start. Please retry shortly.'),
                AuthChoiceView(self.referral)))
            return
        try:
            url = await create_registration_link(inter.user.id, self.referral)
        except Exception:
            log.error('Could not create Discord registration link for %s', inter.user.id)
            await inter.edit_original_response(view=cv2_layout(
                emb('Service unavailable', 'Registration could not start. Please retry shortly.'),
                AuthChoiceView(self.referral)))
            return
        view = RegistrationStatusView(inter.user.id, url)
        msg = await inter.edit_original_response(view=cv2_layout(
            emb('Registration',
                'Press **Authorization** below to continue.\n'
                'Once you authorize, this message updates automatically. '
                'If needed, press **Check status** to check manually.'), view))
        task = asyncio.create_task(monitor_registration(msg, inter.user, view))
        registration_watchers.add(task)
        task.add_done_callback(registration_watchers.discard)

class TicketModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title=kind.replace('-', ' ').title(), timeout=300)
        self.kind = kind
        # Each component is created ONCE per modal instance. Class-level TextInput
        # declarations are collected automatically by discord.py, so also adding
        # them in __init__ caused HTTP 50035: duplicate custom_id.
        if kind == 'payment-support':
            self.method = discord.ui.TextInput(label='Payment method', max_length=60)
            self.invoice = discord.ui.TextInput(label='Invoice ID', max_length=80)
            self.add_item(self.method)
            self.add_item(self.invoice)
        elif kind == 'order-support':
            self.order = discord.ui.TextInput(label='Order ID', max_length=80)
            self.add_item(self.order)
        self.reason = discord.ui.TextInput(label='Reason', style=discord.TextStyle.paragraph,
                                           max_length=1000, required=True)
        self.add_item(self.reason)
    async def on_submit(self,inter):
        # Support tickets are explicitly available to guests / unverified users.
        guild=inter.guild
        if guild is None: return await inter.response.send_message('Use this in a server.',ephemeral=True)
        rid=settings.founder_role_id if self.kind=='business-enquiries' else settings.support_role_id
        staff=guild.get_role(rid)
        me=guild.me or guild.get_member(bot.user.id)
        overrides={guild.default_role:discord.PermissionOverwrite(view_channel=False),inter.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True,read_message_history=True)}
        if staff:overrides[staff]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        details={'reason':str(self.reason.value)}
        if hasattr(self,'method'):details.update(method=str(self.method.value),invoice_id=str(self.invoice.value))
        if hasattr(self,'order'):details['order_id']=str(self.order.value)
        await inter.response.defer(ephemeral=True,thinking=True)
        async with db.pool.acquire() as conn:
            ticket_id=await conn.fetchval('insert into tickets(discord_id,kind,details) values($1,$2,$3::jsonb) returning id',inter.user.id,self.kind,json.dumps(details))
        try:
            category=guild.get_channel(settings.ticket_category_id)
            channel=await guild.create_text_channel(f'{self.kind}-{ticket_id}',category=category,overwrites=overrides,reason=f'Saphire ticket {ticket_id}')
            async with db.pool.acquire() as conn: await conn.execute('update tickets set channel_id=$1 where id=$2',channel.id,ticket_id)
            await channel.send(view=cv2_layout(emb('Support ticket', (staff.mention + '\n' if staff else '') + 'Please wait for a team member to assist you.'), None), allowed_mentions=discord.AllowedMentions(roles=True))
            fields='\n'.join(f'{k}: {v}' for k,v in details.items())
            await channel.send(view=cv2_layout(emb(f'Ticket #{ticket_id}',f'```\n{discord.utils.escape_markdown(fields)[:3800]}\n```'), CloseTicketView()))
            await inter.followup.send(view=cv2_layout(emb('Ticket created',channel.mention), None),ephemeral=True)
        except Exception:
            log.exception('Unable to create support ticket %s', ticket_id)
            async with db.pool.acquire() as conn:
                await conn.execute("update tickets set status='error' where id=$1",ticket_id)
            await inter.followup.send(
                view=cv2_layout(emb('Ticket creation failed',
                    'We could not open your private support channel. Please contact staff or try again.'), None),
                ephemeral=True)

class TicketSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id='saphire:ticket:select',placeholder='Select ticket type',options=[discord.SelectOption(label='Support',value='support'),discord.SelectOption(label='Payment support',value='payment-support'),discord.SelectOption(label='Order support',value='order-support'),discord.SelectOption(label='Business enquiries',value='business-enquiries')])
    async def callback(self,inter):
        # Do not gate support interactions behind account registration.
        await inter.response.send_modal(TicketModal(self.values[0]))
class TicketPanel(discord.ui.View):
    def __init__(self):super().__init__(timeout=None);self.add_item(TicketSelect())

class CloseTicketView(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='Close ticket',style=discord.ButtonStyle.danger,custom_id='saphire:ticket:close')
    async def close(self,inter,button):
        # Support staff can close guest tickets without registering first.
        if not support(inter.user):return await inter.response.send_message('Support only.',ephemeral=True)
        async with db.pool.acquire() as conn:
            row=await conn.fetchrow("select * from tickets where channel_id=$1 and status='open'",inter.channel.id)
            if not row:return await inter.response.send_message('This is not an open ticket.',ephemeral=True)
        await inter.response.defer(ephemeral=True)
        messages=[m async for m in inter.channel.history(limit=None,oldest_first=True)]
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                changed=await conn.fetchval("update tickets set status='closed',closed_at=now() where id=$1 and status='open' returning id",row['id'])
                if not changed:return
                await conn.executemany('''insert into ticket_messages(ticket_id,author_id,author_name,content,created_at,metadata)
                    values($1,$2,$3,$4,$5,$6::jsonb)''', [(
                    row['id'],m.author.id,str(m.author),m.content[:10000],m.created_at,
                    json.dumps({
                        'username':m.author.name,'display_name':m.author.display_name,
                        'avatar':str(m.author.display_avatar.url),
                        'embeds':[e.to_dict() for e in m.embeds],
                        'components':[{'type':'button','label':getattr(c,'label','Button')} for row_ in m.components for c in (getattr(row_,'children',None) or [row_]) if getattr(c,'label',None)]
                    })
                ) for m in messages])
        log.info('[INFO] ticket transcript made %s %s/tickets/%s',row['id'],'https://tickets.saphire.rip',row['id'])
        log_channel=bot.get_channel(settings.ticket_log_channel_id)
        if log_channel:await log_channel.send(view=cv2_layout(emb('Ticket closed',f'ID: `{row["id"]}`\nStaff: {inter.user.mention}\nTranscript: https://tickets.saphire.rip/tickets/{row["id"]} (staff login required)'), None))
        await inter.channel.delete(reason=f'Ticket {row["id"]} closed by {inter.user.id}')

# Invoice channels are private to the payer, staff and bot. Never settle an invoice
# just because a member presses a button or provides a screenshot.
PAYMENT_TIMEOUT=1800
PAYMENT_CONFIRM_POLL=60


def usd_text(cents):
    return f'${Decimal(cents)/100:,.2f}'


def payment_description(invoice):
    coin=invoice['currency']
    if coin=='PAYPAL':
        email=settings.paypal_email
        return (f'**Invoice:** `{invoice["id"]}`\n**Payment:** PayPal\n'
                f'**Amount:** **{usd_text(invoice["amount_cents"])} USD**\n'
                f'**PayPal email:** `{email}`\n\n'
                'Use the link button to visit the payment profile. Once paid, post '
                'the payment confirmation screenshot here, then wait for a support '
                'team member to **independently verify** the payment in PayPal.\n\n'
                '**Safety:** Friends & Family payments do not include PayPal Purchase Protection. '
                'Use the payment type permitted for your transaction. Refund rights '
                'depend on applicable law and PayPal policies; screenshots alone do not confirm payment.')
    return (f'**Invoice ID:** `{invoice["id"]}`\n**Network:** {coin} mainnet\n'
            f'**USD requested:** {usd_text(invoice["amount_cents"])}\n'
            f'**Send exactly:** `{invoice["crypto_amount"]} {coin}`\n'
            f'**Recipient wallet:** `{invoice["recipient_address"]}`\n\n'
            '**You have 5 minutes to submit your transaction ID.** After submitting, '
            'the bot checks the network, exact amount and recipient, transaction freshness, '
            'then waits for the required confirmations. Never send on another network.')


async def settle_invoice(invoice_id, *,paypal_reference=None,actor_id=None):
    # One database transaction prevents retries, simultaneous staff approvals or
    # blockchain polling from crediting the same invoice twice.
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('select * from invoices where id=$1 for update',invoice_id)
            if not row or row['status'] in ('cancelled','expired','credited'):return False
            if paypal_reference is None and row['status']!='confirming':return False
            if paypal_reference is not None:
                if row['currency']!='PAYPAL' or row['status']!='staff_review':return False
                if not paypal_reference.strip() or len(paypal_reference)>120:return False
            ref='invoice:'+invoice_id
            created=await conn.fetchval('''insert into ledger(discord_id,delta_cents,kind,reference,actor_id)
                values($1,$2,$3,$4,$5) on conflict(reference) do nothing returning id''',
                row['discord_id'],row['amount_cents'],'payment',ref,actor_id)
            if not created:return False
            await conn.execute('update accounts set balance_cents=balance_cents+$2 where discord_id=$1',row['discord_id'],row['amount_cents'])
            if paypal_reference is not None:
                await conn.execute('update invoices set paypal_reference=$2 where id=$1',invoice_id,paypal_reference)
            await conn.execute("update invoices set status='credited',credited_at=now() where id=$1",invoice_id)
            await conn.execute('''insert into audit_log(actor_id,action,subject,detail)
                values($1,$2,$3,$4::jsonb)''',actor_id,'payment_credited',invoice_id,
                json.dumps({'source':'paypal_staff' if paypal_reference else 'blockchain',
                            'payment_reference':paypal_reference if paypal_reference else row['txid']}))
    return True


async def cancel_invoice(invoice_id, reason='cancelled'):
    async with db.pool.acquire() as conn:
        row=await conn.fetchrow('''update invoices set status=$2 where id=$1 and status in ('pending','staff_review')
                                   returning id,channel_id''',invoice_id,reason)
    return row


async def invoice_row(invoice_id):
    async with db.pool.acquire() as conn:
        return await conn.fetchrow('select * from invoices where id=$1',invoice_id)


async def invoice_channel_close(channel_id, delay):
    await asyncio.sleep(delay)
    channel=bot.get_channel(channel_id)
    if channel:
        try:await channel.delete(reason='Saphire invoice completed/cancelled')
        except (discord.NotFound,discord.Forbidden,discord.HTTPException):log.warning('Cannot close payment channel %s',channel_id)


async def on_invoice_verified(row):
    # Edit the existing payment panel rather than posting another public message.
    # PayPal approval has the same success state but remains staff-verified.
    await update_invoice_panel(row, completed=True)
    await send_history_event('topup',row['discord_id'],row['id'],row['amount_cents'],row['currency'],row['credited_at'],row['currency'])
    log.info('PAYMENT COMPLETED invoice=%s method=%s credited=%s customer=%s',row['id'],row['currency'],usd_text(row['amount_cents']),row['discord_id'])
    if row['channel_id']:asyncio.create_task(invoice_channel_close(row['channel_id'],60))


async def notify_invoice(row,title,description,color=0x9D65EF):
    channel=bot.get_channel(row['channel_id']) if row['channel_id'] else None
    if channel:
        try:await channel.send(view=cv2_layout(emb(title,description,color)))
        except discord.HTTPException:log.exception('Could not send invoice update %s',row['id'])


async def update_invoice_panel(row,completed=False):
    """Edit the main payment status without publishing extra messages."""
    if not row['channel_id'] or not row['status_message_id']:return
    channel=bot.get_channel(row['channel_id'])
    if not channel:return
    try:
        original=await channel.fetch_message(row['status_message_id'])
        if completed or row['status']=='credited':
            view=cv2_layout(emb('Transaction confirmed and completed ✅',
                f'**Invoice:** `{row["id"]}`\n**Amount credited:** {usd_text(row["amount_cents"])}\n'
                '**Status:** Completed and funds added to your Saphire balance.\n\n'
                'Payment channel closes in **60 seconds**.',0x57C28D))
        elif row['currency'] != 'PAYPAL' and row['status']=='confirming' and row['txid']:
            required=payments.COINS[row['currency']][2]
            view=cv2_layout(emb('Transaction found ⏳',
                f'**Invoice:** `{row["id"]}`\n**Blockchain confirmations:** {row["confirmations"]}/{required}\n\n'
                '**Waiting for required confirmations…**'))
        elif row['status'] in ('cancelled','expired'):
            view=cv2_layout(emb('Payment cancelled',f'Invoice `{row["id"]}` is closed.',0xE45A71))
        else:
            view=cv2_layout(emb('Payment pending',
                f'**Invoice:** `{row["id"]}`\n**Total:** {usd_text(row["amount_cents"])}\n'
                'Follow the payment instructions below.'))
        await original.edit(view=view)
    except (discord.NotFound,discord.Forbidden,discord.HTTPException):
        log.warning('Could not edit payment status message %s',row['id'])

 # Shared short interval between explorer/RPC calls to avoid bursts.
_rpc_lock = asyncio.Lock()
_rpc_next = 0.0
async def _reserve_utxo_check():
    global _rpc_next
    async with _rpc_lock:
        now=time.monotonic()
        if now < _rpc_next:return False
        _rpc_next=now+5.0
        return True

async def verify_crypto_invoice(invoice_id, *, update_on_found=False):
    # Invalid IDs are recoverable: release the hash and keep the invoice open.
    row=await invoice_row(invoice_id)
    if not row or row['status'] != 'confirming' or not row['txid']:
        return ('unavailable', None)
    # Never poll a public UTXO API every 25s for each outstanding invoice.
    # A queued attempt leaves its submitted TX intact for the next scan.
    if row['currency'] in ('BTC', 'LTC', 'ETH') and not await _reserve_utxo_check():
        return ('queued', None)
    try:
        observation=await payments.check_transaction(row['currency'],row['txid'],row['recipient_address'],
            row['crypto_amount'],row['created_at'],eth_rpc=settings.eth_rpc_url,sol_rpc=settings.sol_rpc_url,
            blockcypher_token=settings.blockcypher_api_token)
    except payments.InvalidTransaction as exc:
        # Reset only the failed transaction. Compare the hash to avoid wiping
        # a newer submission; no public rejection message is posted.
        async with db.pool.acquire() as conn:
            await conn.execute('''UPDATE invoices SET txid=NULL, status='pending', confirmations=0
                WHERE id=$1 AND txid=$2 AND status='confirming' ''', invoice_id, row['txid'])
        return ('invalid', str(exc))
    except payments.VerificationUnavailable as exc:
        # Transient errors must NEVER be interpreted as payment received.
        if 'Transaction found in mempool' in str(exc):
            # Not mined; the block time cannot yet prove the invoice age.
            # Keep the submitted hash and wait for a block before settlement.
            log.info('Invoice %s: transaction detected in mempool; awaiting first confirmation',invoice_id)
            return ('mempool', None)
        log.info('Blockchain verifier not ready for invoice %s: %s',invoice_id,exc)
        return ('unavailable', None)
    if observation.confirmed:
        credited=await settle_invoice(invoice_id)
        if credited:
            latest=await invoice_row(invoice_id)
            await remove_crypto_instructions(invoice_id)
            await on_invoice_verified(latest)
            return ('credited', None)
        return ('unavailable', None)
    else:
        if observation.confirmations!=row['confirmations']:
            async with db.pool.acquire() as conn:
                await conn.execute("update invoices set confirmations=$2 where id=$1 and status='confirming'",invoice_id,observation.confirmations)
        # A correct payment is reflected immediately in the original invoice
        # message (and both action buttons disappear), including 0 confirmations.
        if update_on_found or observation.confirmations != row['confirmations']:
            await update_invoice_panel(await invoice_row(invoice_id))
        await remove_crypto_instructions(invoice_id)
        return ('confirming', None)


class TransactionModal(discord.ui.Modal):
    def __init__(self,invoice_id):
        super().__init__(title='Check cryptocurrency transaction')
        self.invoice_id=invoice_id
        self.transaction=discord.ui.TextInput(label='Transaction ID',min_length=16,max_length=128)
        self.add_item(self.transaction)

    async def on_submit(self,inter):
        await inter.response.defer(ephemeral=True,thinking=True)
        txid=str(self.transaction.value).strip()
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                row=await conn.fetchrow('select * from invoices where id=$1 for update',self.invoice_id)
                if not row or row['discord_id']!=inter.user.id or row['currency']=='PAYPAL':
                    return await inter.followup.send('You cannot submit a transaction for this invoice.',ephemeral=True)
                if row['status'] not in ('pending','confirming'):
                    return await inter.followup.send('This invoice is no longer accepting transactions.',ephemeral=True)
                if row['txid'] and row['txid']!=txid:
                    return await inter.followup.send('A different transaction has already been submitted.',ephemeral=True)
                if row['status']=='pending' and (dt.datetime.now(dt.timezone.utc)-row['created_at']).total_seconds()>PAYMENT_TIMEOUT:
                    await conn.execute("update invoices set status='expired' where id=$1",self.invoice_id)
                    return await inter.followup.send('This invoice has expired. Start a new top-up.',ephemeral=True)
                try:
                    await conn.execute("update invoices set txid=$2,status='confirming' where id=$1",self.invoice_id,txid)
                except asyncpg.UniqueViolationError:
                    return await inter.followup.send('That transaction is already attached to another invoice.',ephemeral=True)
        result, reason = await verify_crypto_invoice(self.invoice_id, update_on_found=True)
        if result in ('confirming','credited'):
            await remove_crypto_instructions(self.invoice_id)

        if result == 'invalid':
            await inter.followup.send(view=cv2_layout(emb(
                'Invalid transaction ID',
                'That transaction is not valid for this invoice. Press **Check transaction** '
                'to enter a new ID and try again.\n\n'
                f'**Reason:** {discord.utils.escape_markdown(reason)[:750]}',
                0xE45A71)), ephemeral=True)
        elif result == 'credited':
            await inter.followup.send(view=cv2_layout(emb(
                'Payment verified', 'Your funds have been added to your balance.', 0x57C28D)), ephemeral=True)
        elif result == 'confirming':
            await inter.followup.send(view=cv2_layout(emb(
                'Transaction found ✅', 'Your transaction was found and passed payment checks. '
                'The main payment panel is now tracking blockchain confirmations.')), ephemeral=True)
        elif result == 'mempool':
            await inter.followup.send(view=cv2_layout(emb(
                'Transaction detected', 'Your transaction has been detected in the mempool. '
                'It must be mined before the bot can verify its block timestamp, '
                'recipient, exact amount, and confirmations. No further action is needed.')),
                ephemeral=True)
        elif result == 'queued':
            await inter.followup.send(view=cv2_layout(emb(
                'Transaction queued', 'Your transaction ID is saved. The blockchain '
                'provider is being checked at a safe rate to avoid API limits. '
                'The bot will verify it automatically and update this panel.')), ephemeral=True)
        else:
            await inter.followup.send(view=cv2_layout(emb(
                'Checking transaction', 'Verification is temporarily unavailable. '
                'Your submitted ID is saved and will be checked again automatically.')), ephemeral=True)


class PaypalApprovalModal(discord.ui.Modal):
    def __init__(self,invoice_id):
        super().__init__(title='Confirm verified PayPal payment')
        self.invoice_id=invoice_id
        self.reference=discord.ui.TextInput(label='PayPal transaction reference',min_length=8,max_length=120)
        self.add_item(self.reference)

    async def on_submit(self,inter):
        if not support(inter.user):return await inter.response.send_message('Support role required.',ephemeral=True)
        await inter.response.defer(ephemeral=True)
        ref=str(self.reference.value).strip()
        # The staff member must verify the payment outside of Discord in PayPal itself.
        try:
            done=await settle_invoice(self.invoice_id,paypal_reference=ref,actor_id=inter.user.id)
        except asyncpg.UniqueViolationError:
            return await inter.followup.send('That PayPal reference was already used for another invoice.',ephemeral=True)
        if not done:return await inter.followup.send('Payment already handled or invoice unavailable.',ephemeral=True)
        row=await invoice_row(self.invoice_id)
        await on_invoice_verified(row)
        await inter.followup.send('Payment settled and audited.',ephemeral=True)


class InvoiceControls(discord.ui.View):
    def __init__(self,invoice_id,method):
        super().__init__(timeout=None)
        self.invoice_id=invoice_id
        self.method=method
        if method=='PAYPAL':
            if settings.paypal_profile_url:
                self.add_item(discord.ui.Button(label='Open PayPal profile',emoji='💳',url=settings.paypal_profile_url))
            approve=discord.ui.Button(label='Staff: Confirm PayPal',style=discord.ButtonStyle.success,custom_id=f'saphire:invoice:approve:{invoice_id}')
            approve.callback=self.approve
            self.add_item(approve)
        else:
            submit=discord.ui.Button(label='Check transaction',emoji='🔎',style=discord.ButtonStyle.primary,custom_id=f'saphire:invoice:check:{invoice_id}')
            submit.callback=self.check
            self.add_item(submit)
        cancel=discord.ui.Button(label='Cancel payment',emoji='✖️',style=discord.ButtonStyle.danger,custom_id=f'saphire:invoice:cancel:{invoice_id}')
        cancel.callback=self.cancel
        self.add_item(cancel)

    async def interaction_check(self,inter):
        row=await invoice_row(self.invoice_id)
        if not row:
            await inter.response.send_message('Invoice not found.',ephemeral=True)
            return False
        if inter.user.id!=row['discord_id'] and not support(inter.user):
            await inter.response.send_message('This invoice belongs to another user.',ephemeral=True)
            return False
        return True

    async def check(self,inter):
        row=await invoice_row(self.invoice_id)
        if row['status'] != 'pending':
            return await inter.response.send_message(
                'A transaction is already being checked or this invoice is closed.', ephemeral=True)
        if row['discord_id']!=inter.user.id:
            return await inter.response.send_message('Only the invoice owner can submit a transaction.',ephemeral=True)
        await inter.response.send_modal(TransactionModal(self.invoice_id))

    async def cancel(self,inter):
        row=await invoice_row(self.invoice_id)
        if row['discord_id']!=inter.user.id and not support(inter.user):
            return await inter.response.send_message('Not authorised.',ephemeral=True)
        if row['status']=='confirming':
            return await inter.response.send_message('Transaction already submitted; contact support to resolve this payment.',ephemeral=True)
        result=await cancel_invoice(self.invoice_id)
        if not result:return await inter.response.send_message('This invoice cannot be cancelled now.',ephemeral=True)
        await inter.response.send_message('Invoice cancelled. Closing the channel.',ephemeral=True)
        if row['channel_id']:asyncio.create_task(invoice_channel_close(row['channel_id'],2))

    async def approve(self,inter):
        if not support(inter.user):return await inter.response.send_message('Support only.',ephemeral=True)
        await inter.response.send_modal(PaypalApprovalModal(self.invoice_id))


# New 2026 top-up workflow. All panel selectors are ephemeral; invoices remain auditable.
PAYMENT_CATEGORY=1561934204999180440

async def remove_crypto_instructions(invoice_id):
    row=await invoice_row(invoice_id)
    if not row or not row['channel_id']:return
    channel=bot.get_channel(row['channel_id'])
    if not channel:return
    # The instructions are the bot's second message; search a bounded channel
    # history rather than add an ephemeral message ID DB column.
    try:
        async for msg in channel.history(limit=8):
            if msg.author.id==bot.user.id and msg.id!=row['status_message_id'] and msg.components:
                await msg.delete()
                break
    except (discord.HTTPException,discord.Forbidden):
        log.warning('Could not remove instructions for invoice %s',invoice_id)

class NewTopupAmount(discord.ui.Modal):
    def __init__(self,method):
        super().__init__(title='Add amount to deposit')
        self.method=method
        self.amount=discord.ui.TextInput(label='USD deposit amount',placeholder='10.00',max_length=15)
        self.add_item(self.amount)
    async def on_submit(self,inter):
        if not await registered(inter):return
        await inter.response.defer(ephemeral=True,thinking=True)
        try:
            value=Decimal(str(self.amount.value).strip())
            if not value.is_finite() or value<1 or value>100000 or value.as_tuple().exponent < -2:
                raise ValueError('Invalid amount')
            cents=int(value*100)
        except (ValueError,ArithmeticError):
            return await inter.followup.send('Enter an amount between $1 and $100,000 (maximum 2 decimals).',ephemeral=True)
        await create_new_topup(inter,self.method,cents)

async def create_new_topup(inter,coin,cents):
    guild=inter.guild
    if not guild:return await inter.followup.send('Use this in the Saphire server.',ephemeral=True)
    category=guild.get_channel(PAYMENT_CATEGORY)
    if not isinstance(category,discord.CategoryChannel):
        return await inter.followup.send('Payment category is unavailable. Contact staff.',ephemeral=True)
    if coin=='PAYPAL':
        if not settings.paypal_email or not settings.paypal_profile_url:
            return await inter.followup.send('PayPal payment settings are missing.',ephemeral=True)
        wallet='';amount=None
    else:
        wallet=getattr(settings,coin.lower()+'_wallet','')
        if not wallet:return await inter.followup.send('Payment wallet is unavailable.',ephemeral=True)
        if coin=='ETH' and not settings.eth_rpc_url:
            return await inter.followup.send('Ethereum RPC endpoint is not configured.',ephemeral=True)
        if coin=='SOL' and not settings.sol_rpc_url:
            return await inter.followup.send('Solana RPC endpoint is not configured.',ephemeral=True)
        try:amount=await payments.quote(coin,cents)
        except payments.VerificationUnavailable:
            return await inter.followup.send('Live crypto pricing is unavailable; retry shortly.',ephemeral=True)
        if amount<=0:return await inter.followup.send('Quoted amount is too small.',ephemeral=True)
    me=guild.me or guild.get_member(bot.user.id)
    can_post=coin=='PAYPAL'
    payer=discord.PermissionOverwrite(view_channel=True,read_message_history=True,
        send_messages=can_post,attach_files=can_post,embed_links=can_post,add_reactions=False)
    staff_perms=discord.PermissionOverwrite(view_channel=True,read_message_history=True,
        send_messages=can_post,attach_files=can_post,embed_links=can_post)
    bot_perms=discord.PermissionOverwrite(view_channel=True,send_messages=True,
        manage_channels=True,read_message_history=True,manage_messages=True,attach_files=True)
    overrides={guild.default_role:discord.PermissionOverwrite(view_channel=False),inter.user:payer,me:bot_perms}
    for rid in (settings.support_role_id,settings.community_manager_role_id,settings.founder_role_id):
        role_obj=guild.get_role(rid) if rid else None
        if role_obj:overrides[role_obj]=staff_perms
    invoice_id=secrets.token_hex(8)
    async with db.pool.acquire() as conn:
        await conn.execute('''insert into invoices(id,discord_id,currency,amount_cents,crypto_amount,recipient_address,status,expires_at)
            values($1,$2,$3,$4,$5,$6,$7,now()+interval '30 minutes')''',
            invoice_id,inter.user.id,coin,cents,amount,wallet,
            'staff_review' if coin=='PAYPAL' else 'pending')
    try:
        channel=await guild.create_text_channel('payment-'+invoice_id,category=category,
            overwrites=overrides,reason='Private payment '+invoice_id)
    except Exception:
        await cancel_invoice(invoice_id)
        log.exception('New payment channel creation failed')
        return await inter.followup.send('Unable to create private payment channel.',ephemeral=True)
    async with db.pool.acquire() as conn:
        await conn.execute('update invoices set channel_id=$2 where id=$1',invoice_id,channel.id)
    if coin=='PAYPAL':
        desc=(f'**Invoice:** `{invoice_id}`\n**Amount due:** {usd_text(cents)}\n'
              f'**PayPal email:** `{settings.paypal_email}`\n\n'
              'Pay this exact amount to the PayPal account above, then upload '
              '**a screenshot of your payment confirmation** to this channel. '
              'Staff will independently confirm the transaction in PayPal before funds are added.')
        main=await channel.send(view=cv2_layout(emb('PayPal payment',desc),InvoiceControls(invoice_id,'PAYPAL')))
    else:
        main=await channel.send(view=cv2_layout(emb('Crypto payment',
            f'**Invoice:** `{invoice_id}`\n**Deposit:** {usd_text(cents)}\n'
            '**Status:** Waiting for a transaction ID.\n\n'
            'Send a transaction within **30 minutes** of creating this invoice.')))
        instr=emb('Payment instructions',
            f'**Send exactly:**\n```{amount} {coin}```\n'
            f'**Recipient wallet:**\n```{wallet}```\n'
            'Press **Check transaction** after sending the funds. '
            'Only confirmed payments matching the exact amount and recipient are credited.')
        qr_file, qr_filename = make_crypto_payment_qr(coin, wallet, amount, invoice_id)
        await channel.send(view=cv2_layout(
            instr, InvoiceControls(invoice_id,coin), qr_filename=qr_filename),
            file=qr_file)
    async with db.pool.acquire() as conn:
        await conn.execute('update invoices set status_message_id=$2 where id=$1',invoice_id,main.id)
    await inter.followup.send(view=cv2_layout(emb('Payment channel created',channel.mention)),ephemeral=True)
    log.info('Payment channel opened invoice=%s currency=%s amount_usd=%s',invoice_id,coin,usd_text(cents))

class TopupSelection(discord.ui.View):
    def __init__(self,step='method'):
        super().__init__(timeout=300)
        self.add_item(TopupDropdown(step))

class TopupDropdown(discord.ui.Select):
    def __init__(self,step):
        self.step=step
        opts=([discord.SelectOption(label='Cryptocurrency',value='crypto'),
               discord.SelectOption(label='PayPal',value='PAYPAL')]
              if step=='method' else [discord.SelectOption(label=x,value=x) for x in ('LTC','BTC','ETH','SOL')])
        super().__init__(placeholder='Choose payment method' if step=='method' else 'Choose cryptocurrency',options=opts)
    async def callback(self,inter):
        if not await registered(inter):return
        picked=self.values[0]
        if picked=='crypto':
            return await inter.response.edit_message(view=cv2_layout(emb('Select cryptocurrency',
                'Choose Litecoin, Bitcoin, Ethereum or Solana.'),TopupSelection('coin')))
        await inter.response.edit_message(view=cv2_layout(emb('Add deposit amount',
            f'**Payment method:** {picked}\nClick **Add amount** to continue.'),TopupAmountButton(picked)))

class TopupAmountButton(discord.ui.View):
    def __init__(self,coin):
        super().__init__(timeout=300)
        self.coin=coin
    @discord.ui.button(label='Add amount',style=discord.ButtonStyle.primary,emoji='💰')
    async def add_amount(self,inter,button):
        await inter.response.send_modal(NewTopupAmount(self.coin))

class TopupStart(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='Start',style=discord.ButtonStyle.primary,custom_id='saphire:topup:start')
    async def start(self,inter,button):
        if not await registered(inter):return
        await inter.response.send_message(view=cv2_layout(emb('Choose payment method',
            'Select cryptocurrency or PayPal.'),TopupSelection()),ephemeral=True)

# Compatibility for existing /topup and ;addcredit entry points.
PaymentPanel=TopupStart

# --- Interactive Components V2 order panel -------------------------------------
# The in-memory wizard contains no payment authority. Every price, service,
# discount and available balance is rechecked in Postgres at checkout.
PLATFORMS = ('Instagram', 'TikTok', 'YouTube', 'Facebook', 'X', 'Twitch', 'Kick',
             'Telegram', 'Spotify', 'SoundCloud', 'Reddit', 'Pinterest', 'Snapchat',
             'Other')
SERVICE_GROUPS = ('Followers', 'Likes & reactions', 'Comments', 'Views', 'Auto likes',
                  'Subscribers', 'Shares & reposts', 'Saves', 'Streams & plays',
                  'Members', 'Watch time', 'Other')


def service_platform(service):
    value=(str(service['name'])+' '+str(service['category'])).lower()
    matches={'Instagram':('instagram',' ig '),'TikTok':('tiktok','tik tok'),
        'YouTube':('youtube','youtu.be'),'Facebook':('facebook',' fb '),
        'X':('twitter',' x (',' x '),'Twitch':('twitch',),'Kick':('kick ',),
        'Telegram':('telegram',),'Spotify':('spotify',),'SoundCloud':('soundcloud',),
        'Reddit':('reddit',),'Pinterest':('pinterest',),'Snapchat':('snapchat',),
        'Discord':('discord',)}
    for platform,patterns in matches.items():
        if any(p in value for p in patterns):return platform
    return 'Other'


def service_group(service):
    value=(str(service['name'])+' '+str(service['category'])).lower()
    if 'auto like' in value:return 'Auto likes'
    for group,words in (
        ('Followers',('follower','follow ')),('Comments',('comment','reply')),
        ('Views',('view','impression')),('Likes & reactions',('like','reaction')),
        ('Subscribers',('subscriber','subscription')),('Shares & reposts',('share','repost','retweet')),
        ('Saves',('save','bookmark')),('Streams & plays',('stream','play ','listener')),
        ('Members',('member','joiner')),('Watch time',('watch time','hours','minutes'))):
        if any(w in value for w in words):return group
    return 'Other'


def discount_amount(cents, percent):
    pct=Decimal(str(percent))
    if not (Decimal('0')<=pct<=Decimal('100')):raise ValueError('Invalid discount')
    return max(1, int((Decimal(cents)*(100-pct)/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))


async def applicable_discount(code):
    async with db.pool.acquire() as conn:
        return await conn.fetchrow('''select code,percent_off from discount_codes
            where lower(code)=lower($1) and active=true
            and (expires_at is null or expires_at>now())''',code)


async def send_history_event(kind, user_id, reference, amount_cents, label, placed_at=None, method=None):
    channel=bot.get_channel(settings.transaction_log_channel_id)
    if not channel:return
    guild_id=settings.discord_guild_id or channel.guild.id
    placed_at=placed_at or dt.datetime.now(dt.timezone.utc)
    if placed_at.tzinfo is None:placed_at=placed_at.replace(tzinfo=dt.timezone.utc)
    stamp=int(placed_at.timestamp())
    if kind=='order':
        title=f'Order #{reference} created ✅'
        info=(f'**Order:** `#{reference}`\n**Discord user:** <@{user_id}> (`{user_id}`)\n**Placed:** <t:{stamp}:R> (<t:{stamp}:f>)\n'
              f'**Platform & service:** {label}\n**Charged:** {usd_text(amount_cents)}')
    else:
        title=f'Top-up {reference} completed ✅'
        info=(f'**Invoice:** `{reference}`\n**Discord user:** <@{user_id}> (`{user_id}`)\n**Credited:** <t:{stamp}:R> (<t:{stamp}:f>)\n'
              f'**Method:** {method or label}\n**Added to balance:** {usd_text(amount_cents)}')
    view=discord.ui.View(timeout=None)
    if settings.order_panel_channel_id:
        view.add_item(discord.ui.Button(label='Make an order',emoji='🛒',style=discord.ButtonStyle.link,
            url=f'https://discord.com/channels/{guild_id}/{settings.order_panel_channel_id}'))
    if settings.topup_panel_channel_id:
        view.add_item(discord.ui.Button(label='Add credit to balance',emoji='💳',style=discord.ButtonStyle.link,
            url=f'https://discord.com/channels/{guild_id}/{settings.topup_panel_channel_id}'))
    try:await channel.send(view=cv2_layout(emb(title,info,0x57C28D),view if view.children else None))
    except discord.HTTPException:log.exception('Could not publish %s history for %s',kind,reference)


_INSTAGRAM_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_instagram_follower_target(value):
    """Normalize a profile target, never silently reinterpret other websites."""
    raw = str(value).strip()
    if not raw or any(c.isspace() for c in raw):
        raise ValueError('Enter a valid Instagram username or profile URL.')
    candidate = raw.lstrip('@') if raw.startswith('@') else raw
    if _INSTAGRAM_USERNAME.fullmatch(candidate):
        return f'https://www.instagram.com/{candidate}/'
    if raw.lower().startswith(('instagram.com/', 'www.instagram.com/')):
        candidate = 'https://www.instagram.com/' + raw.split('/', 1)[1]
    else:
        candidate = raw
    from urllib.parse import urlsplit
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in ('http', 'https') or (parsed.hostname or '').lower() not in ('instagram.com', 'www.instagram.com'):
        raise ValueError('Enter an Instagram username or Instagram profile URL.')
    # A complete, correctly formed profile URL is preserved as requested.
    segments = [x for x in parsed.path.split('/') if x]
    if len(segments) != 1 or not _INSTAGRAM_USERNAME.fullmatch(segments[0]) or segments[0].lower() in ('p','reel','reels','stories','explore','accounts'):
        raise ValueError('Instagram followers require a profile URL, not a post or Reel link.')
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError('Enter a clean Instagram profile URL without query parameters.')
    return candidate


def is_instagram_follower_service(service):
    return service_platform(service) == 'Instagram' and service_group(service) == 'Followers'


async def submit_order(user_id, service_id, quantity, link, extras, code=None):
    # Check the current listing and available discount under the same DB lock
    # used to debit balances; the provider is only called after debit succeeds.
    async with db.pool.acquire() as conn:
        service=await conn.fetchrow('select * from services where id=$1',service_id)
        if not service:raise ValueError('This service is no longer available.')
        if is_instagram_follower_service(service):
            link = normalize_instagram_follower_target(link)
        if not (service['min_order']<=quantity<=service['max_order']):
            raise ValueError('Quantity no longer falls within service limits.')
        total=max(1,provider.cents_for(quantity,service['retail_rate']))
        applied=None
        if code:
            applied=await applicable_discount(code)
            if not applied:raise ValueError('Discount code is invalid or expired.')
            total=discount_amount(total,applied['percent_off'])
    order_id=await db.spend_and_create_order(user_id,service_id,quantity,link,total,json.dumps(extras))
    try:
        result=await provider.call('new_order',service_id=service_id,count=quantity,link=link,**extras)
        provider_id=str(result.get('order') or result.get('order_id') or '')
        if not provider_id: raise provider.ProviderError('Top4SMM did not return an order ID')
        async with db.pool.acquire() as conn:
            await conn.execute('''update orders set provider_order_id=$1,status='pending',provider_cost_cents=$2 where id=$3 and discord_id=$4''',
                provider_id,int(Decimal(str(result.get('charge',result.get('sum',0))))*100),order_id,user_id)
        await send_history_event('order',user_id,order_id,total,
            f'{service_platform(service)} • {service_group(service)}')
        return order_id,total,'pending'
    except Exception:
        # Timeouts may occur AFTER provider accepted the order. Do not automatically refund.
        async with db.pool.acquire() as conn:
            await conn.execute("update orders set status='needs_review' where id=$1 and discord_id=$2",order_id,user_id)
        log.exception('Provider order %s requires reconciliation',order_id)
        return order_id,total,'needs_review'


class OrderButton(discord.ui.Button):
    def __init__(self,wizard,action,label,style=discord.ButtonStyle.secondary,disabled=False):
        super().__init__(label=label,style=style,disabled=disabled)
        self.wizard=wizard;self.action=action
    async def callback(self,inter):
        await self.wizard.action(inter,self.action)


class OrderSelect(discord.ui.Select):
    def __init__(self,wizard,mode,options,placeholder):
        super().__init__(placeholder=placeholder,options=options,min_values=1,max_values=1)
        self.wizard=wizard;self.mode=mode
    async def callback(self,inter):
        await self.wizard.select(inter,self.mode,self.values[0])


class OrderModal(discord.ui.Modal):
    def __init__(self,wizard,kind):
        super().__init__(title={'quantity':'Set order quantity','link':'Set target username / URL',
            'comments':'Enter custom comments','discount':'Apply discount code','options':'Advanced service options'}[kind])
        self.wizard=wizard;self.kind=kind
        hints={'quantity':('Quantity','e.g. 1000',20),
               'link':('Profile / post link or username','https://... or @username',512),
               'comments':('Comments (one per line)','One unique comment per line',1800),
               'discount':('Discount code','Your code',80),
               'options':('Options (key=value; one per line)','sending_delay=5',500)}
        label,placeholder,maximum=hints[kind]
        self.value_input=discord.ui.TextInput(label=label,placeholder=placeholder,max_length=maximum,
            style=discord.TextStyle.paragraph if kind in ('comments','options') else discord.TextStyle.short)
        self.add_item(self.value_input)
    async def on_submit(self,inter):
        if inter.user.id!=self.wizard.user_id:
            return await inter.response.send_message('This order belongs to another user.',ephemeral=True)
        value=str(self.value_input.value).strip()
        try:
            if self.kind=='options':
                data={}
                allowed={'likes','sending_delay','drip_feed_likes','drip_feed_interval'}
                for line in value.splitlines():
                    if not line.strip():continue
                    if '=' not in line:raise ValueError('Use key=value for each option.')
                    key,val=(part.strip() for part in line.split('=',1))
                    if key not in allowed:raise ValueError(f'Unsupported option: {key}')
                    number=int(val)
                    if not (0<=number<=1000000):raise ValueError('Option values must be between 0 and 1000000.')
                    data[key]=number
                self.wizard.extra=data
            elif self.kind=='quantity':
                count=int(value)
                if not self.wizard.selected or not (self.wizard.selected['min_order']<=count<=self.wizard.selected['max_order']):
                    raise ValueError(f'Quantity must be between {self.wizard.selected["min_order"]} and {self.wizard.selected["max_order"]}.')
                self.wizard.quantity=count
            elif self.kind=='link':
                if not value or len(value)>512 or '\n' in value:raise ValueError('Enter a valid username or URL.')
                self.wizard.target=(normalize_instagram_follower_target(value)
                    if self.wizard.selected and is_instagram_follower_service(self.wizard.selected) else value)
            elif self.kind=='comments':
                lines=[line.strip() for line in value.splitlines() if line.strip()]
                if not lines or len(lines)>100:raise ValueError('Enter 1–100 comments, one per line.')
                self.wizard.comments='\n'.join(lines)
            else:
                if not await applicable_discount(value):raise ValueError('Invalid or expired discount code.')
                self.wizard.code=value
        except (ValueError,asyncpg.PostgresError) as exc:
            return await inter.response.send_message(str(exc),ephemeral=True)
        await self.wizard.render(inter)


class OrderWizard(discord.ui.View):
    def __init__(self,user_id):
        super().__init__(timeout=900)
        self.user_id=user_id;self.stage='home';self.platform=None;self.group=None
        self.selected=None;self.quantity=None;self.target=None;self.comments=None;self.code=None
        self.page=0;self.listing=[];self.extra={};self.working=False
    async def interaction_check(self,inter):
        if inter.user.id!=self.user_id:
            await inter.response.send_message('This order belongs to someone else.',ephemeral=True)
            return False
        return True
    def price(self):
        if not self.selected or not self.quantity:return None
        total=max(1,provider.cents_for(self.quantity,self.selected['retail_rate']))
        return total
    async def load(self):
        async with db.pool.acquire() as conn:
            rows=await conn.fetch('select * from services order by category,name,id')
        if self.stage=='group':return [r for r in rows if service_platform(r)==self.platform]
        if self.stage=='service':return [r for r in rows if service_platform(r)==self.platform and service_group(r)==self.group]
        return rows
    def option(self,label,value,description=None):
        return discord.SelectOption(label=str(label)[:100],value=str(value)[:100],description=(description or '')[:100] or None)
    async def build(self):
        self.clear_items()
        if self.stage=='home':
            title='Start your order';detail='Choose an order category to continue.'
            self.add_item(OrderButton(self,'social','🌐 Social media',discord.ButtonStyle.primary))
            self.add_item(OrderButton(self,'discord','Discord (coming soon)',disabled=True))
            return emb(title,detail)
        if self.stage=='platform':
            title='Choose a platform';detail='Select a social platform. Only matching services will be displayed.'
            rows=await self.load()
            available=set(service_platform(s) for s in rows)
            options=[self.option(x,x) for x in PLATFORMS if x in available]
            if options:self.add_item(OrderSelect(self,'platform',options,'Select platform'))
            else:detail='No services are synced. Ask staff to run `;syncservices`.'
        elif self.stage=='group':
            title=f'{self.platform} • Choose service category';detail='Choose followers, views, comments, likes, or another supported category.'
            rows=await self.load()
            available=set(service_group(s) for s in rows)
            options=[self.option(x,x) for x in SERVICE_GROUPS if x in available]
            if options:self.add_item(OrderSelect(self,'group',options,'Choose service type'))
            else:detail='No services currently available for this platform.'
        elif self.stage=='service':
            title=f'{self.platform} • {self.group}';detail='Choose the specific service you want to order.'
            rows=await self.load()
            self.listing=rows
            pages=max(1,(len(rows)+19)//20)
            self.page=min(self.page,pages-1)
            chunk=rows[self.page*20:(self.page+1)*20]
            if chunk:
                options=[self.option(r['name'],str(i+self.page*20),
                    f'ID {r["id"]} • {r["min_order"]}–{r["max_order"]}')
                    for i,r in enumerate(chunk)]
                self.add_item(OrderSelect(self,'service',options,f'Services • page {self.page+1}/{pages}'))
                if self.page:self.add_item(OrderButton(self,'prev','◀ Previous page'))
                if self.page+1<pages:self.add_item(OrderButton(self,'next','Next page ▶'))
                detail+=f'\n**Page:** {self.page+1}/{pages} • **Available services:** {len(rows)}'
            else:detail='No matching services.'
        else:
            title='Review your order';service=self.selected
            detail=(f'**Platform:** {self.platform}\n**Category:** {self.group}\n'
                    f'**Service:** {service["name"]} (`{service["id"]}`)\n'
                    f'**Quantity:** {self.quantity or "Not set"} ({service["min_order"]}–{service["max_order"]})\n'
                    f'**Target link/username:** {discord.utils.escape_markdown(self.target or "Not set")}\n'
                    f'**Price per 1,000:** ${Decimal(str(service["retail_rate"])):,.6f}\n')
            if service['description']:
                detail+=f'**Service information:**\n{format_service_description(service["description"], limit=950)}\n'
            if self.comments:detail+=f'**Custom comments:** {len(self.comments.splitlines())} entered\n'
            if self.extra:detail+=f'**Additional options:** {len(self.extra)} set\n'
            if self.code:detail+=f'**Discount code:** `{self.code}` (validated at checkout)\n'
            if self.price() is not None:
                raw=self.price()
                if self.code:
                    d=await applicable_discount(self.code)
                    if d:
                        detail+=f'**Estimated total:** ~~{usd_text(raw)}~~ {usd_text(discount_amount(raw,d["percent_off"]))}\n'
                    else:detail+='**Discount expired:** remove or replace code.\n'
                else:detail+=f'**Estimated total:** {usd_text(raw)}\n'
            self.add_item(OrderButton(self,'quantity','🔢 Quantity',discord.ButtonStyle.primary))
            self.add_item(OrderButton(self,'link','🔗 Username / link'))
            self.add_item(OrderButton(self,'discount','🏷️ Discount code'))
            self.add_item(OrderButton(self,'options','⚙️ Options'))
            if 'comment' in (str(service['name'])+' '+str(service['category'])).lower():
                self.add_item(OrderButton(self,'comments','💬 Custom comments'))
            ready=bool(self.quantity and self.target)
            self.add_item(OrderButton(self,'place','✅ Place order',discord.ButtonStyle.success,disabled=not ready or self.working))
        self.add_item(OrderButton(self,'back','← Back'))
        self.add_item(OrderButton(self,'home','⌂ Main menu'))
        return emb(title,detail)
    async def render(self,inter):
        e=await self.build()
        if not inter.response.is_done():
            await inter.response.edit_message(view=cv2_layout(e,self))
        else:
            await inter.followup.send(view=cv2_layout(e,self),ephemeral=True)
    async def select(self,inter,mode,value):
        if mode=='platform':self.platform=value;self.group=None;self.stage='group'
        elif mode=='group':self.group=value;self.stage='service';self.page=0
        else:
            i=int(value)
            if i<0 or i>=len(self.listing):return await inter.response.send_message('Selection expired. Try again.',ephemeral=True)
            self.selected=self.listing[i];self.quantity=None;self.target=None;self.comments=None;self.code=None;self.extra={}
            self.stage='review'
        await self.render(inter)
    async def action(self,inter,action):
        if action=='social':self.stage='platform'
        elif action=='home':self.stage='home'
        elif action=='back':
            self.stage={'platform':'home','group':'platform','service':'group','review':'service'}.get(self.stage,'home')
        elif action=='next':self.page+=1
        elif action=='prev':self.page=max(0,self.page-1)
        elif action in ('quantity','link','comments','discount','options'):
            return await inter.response.send_modal(OrderModal(self,action))
        elif action=='place':return await self.place(inter)
        await self.render(inter)
    async def place(self,inter):
        if self.working:return await inter.response.send_message('Your order is already processing.',ephemeral=True)
        if not await is_verified(inter.user.id):return await registered(inter)
        if not self.selected or not self.quantity or not self.target:
            return await inter.response.send_message('Enter quantity and target before ordering.',ephemeral=True)
        if self.comments and self.group!='Comments':return await inter.response.send_message('Custom comments are only supported for comment services.',ephemeral=True)
        self.working=True
        await inter.response.defer(ephemeral=True,thinking=True)
        try:
            extra=dict(self.extra)
            if self.comments:extra['comment']=self.comments
            oid,total,state=await submit_order(inter.user.id,str(self.selected['id']),
                    self.quantity,self.target,extra,self.code)
            title='Order placed ✅' if state=='pending' else 'Order requires review'
            message=(f'**Order:** `#{oid}`\n**Service:** {self.selected["name"]}\n'
                    f'**Charged:** {usd_text(total)}\n**Status:** {state}\n'
                    'If the provider confirmation is uncertain, staff will reconcile this order before any retry or refund.')
            await inter.followup.send(view=cv2_layout(emb(title,message,0x57C28D if state=='pending' else 0xF1AD60)),ephemeral=True)
            self.stage='home';self.selected=None
        except (ValueError,asyncpg.PostgresError) as exc:
            await inter.followup.send(str(exc),ephemeral=True)
        except Exception:
            log.exception('Unexpected error placing order')
            await inter.followup.send('Order unavailable. Please contact support before retrying.',ephemeral=True)
        finally:self.working=False


class OrderStart(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='Start order',emoji='🛒',style=discord.ButtonStyle.primary,custom_id='saphire:order:start')
    async def start(self,inter,button):
        if not await registered(inter):return
        wizard=OrderWizard(inter.user.id)
        view=cv2_layout(await wizard.build(),wizard)
        await inter.response.send_message(view=view,ephemeral=True)



@bot.command(name='discordcredits')
async def discord_provider_credits(ctx):
    """Owner-only private credits_left history; never post reseller credits in public."""
    if not owner(ctx.author): return
    async with db.pool.acquire() as conn:
        rows=await conn.fetch('''SELECT l.order_id,l.credits_left,l.observed_at
                                 FROM discord_member_provider_credit_log l
                                 ORDER BY l.observed_at DESC LIMIT 10''')
    lines=[f'`{r["order_id"]}` — credits left: `{r["credits_left"]}` (<t:{int(r["observed_at"].timestamp())}:R>)' for r in rows]
    try:
        await ctx.author.send(view=cv2_layout(emb('Discord member provider credits',
            '\n'.join(lines) if lines else 'No reseller orders recorded yet.')))
    except (discord.Forbidden, discord.HTTPException):
        log.warning('Could not DM owner their reseller credits')


# The V2 key-based checkout supersedes legacy wallet and top-up chat commands.
for obsolete_command in ('balance','bal','topup','addcredit','creditadd','topuphistory','invoicehistory'):
    bot.remove_command(obsolete_command)


class MemberInviteModal(discord.ui.Modal, title='Complete your member order'):
    def __init__(self, key, user_id):
        super().__init__(timeout=300)
        self.key=key
        self.user_id=user_id
        self.invite=discord.ui.TextInput(label='Discord server invite',placeholder='https://discord.gg/example',max_length=250)
        self.add_item(self.invite)

    async def on_submit(self, inter):
        if inter.user.id!=self.user_id:
            return await inter.response.send_message('This redemption belongs to another user.',ephemeral=True)
        try:
            invite=discord_members.validate_invite(str(self.invite.value))
        except ValueError as exc:
            return await inter.response.send_message(str(exc),ephemeral=True)
        await inter.response.defer(ephemeral=True,thinking=True)
        secret=os.getenv('SAPHIRE_V2_API_KEY','')
        if not secret:
            return await inter.followup.send('Redeem API is not configured. Contact staff.',ephemeral=True)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                response=await client.post('https://saphire.rip/v2/api/members/order',
                    headers={'x-api-key':secret},json={'key':self.key,'discord_id':str(self.user_id),'invite_url':invite})
            if response.status_code==200 and response.json().get('success') is True:
                result=response.json()
                return await inter.followup.send(view=cv2_layout(emb('Member order submitted',
                    f'**Service:** {result["type"].title()} members\\n'
                    f'**Quantity:** {int(result["amount"]):,}\\n'
                    f'**Order ID:** \`{result["order_id"]}\`\\n'
                    f'**Provider key:** \`{result["provider_key"]}\`')),ephemeral=True)
            await inter.followup.send('The order could not be confirmed. Contact support before retrying; the key may require reconciliation.',ephemeral=True)
        except Exception:
            log.exception('Member order failed for user %s',self.user_id)
            await inter.followup.send('Could not confirm the provider order. Contact support; do not retry automatically.',ephemeral=True)


class MemberInviteNext(discord.ui.View):
    def __init__(self,key,user_id):
        super().__init__(timeout=900)
        self.key=key
        self.user_id=user_id

    @discord.ui.button(label='Continue to server invite',style=discord.ButtonStyle.primary)
    async def next(self,inter,button):
        if inter.user.id!=self.user_id:
            return await inter.response.send_message('This redemption belongs to another user.',ephemeral=True)
        await inter.response.send_modal(MemberInviteModal(self.key,self.user_id))


class MemberKeyRedeemModal(discord.ui.Modal, title='Redeem your Saphire key'):
    def __init__(self):
        super().__init__(timeout=300)
        self.key_input=discord.ui.TextInput(label='Redemption key',placeholder='SAPH-...',max_length=150)
        self.add_item(self.key_input)

    async def on_submit(self,inter):
        await inter.response.defer(ephemeral=True,thinking=True)
        key=str(self.key_input.value).strip()
        secret=os.getenv('SAPHIRE_V2_API_KEY','')
        if not secret:
            return await inter.followup.send('Redeem API is not configured. Contact staff.',ephemeral=True)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response=await client.post('https://saphire.rip/v2/members/order/redeem',
                    headers={'x-api-key':secret},json={'key':key,'discord_id':str(inter.user.id)})
            if response.status_code!=200:
                return await inter.followup.send('Redemption service unavailable. Contact staff.',ephemeral=True)
            result=response.json()
            if result.get('valid') is not True:
                return await inter.followup.send('Invalid key.',ephemeral=True)
            when=result.get('created_at','Unknown')
            details=(f'**Key:** \`{key}\`\\n**Status:** Valid • Claimed\\n'
                     f'**Members:** {int(result["amount"]):,}\\n'
                     f'**Type:** {result["type"].title()}\\n'
                     f'**Created:** {when}\\n\\n'
                     'Select **Continue to server invite** to complete your order.')
            await inter.followup.send(view=cv2_layout(emb('Key verified',details),
                MemberInviteNext(key,inter.user.id)),ephemeral=True)
        except Exception:
            log.exception('Member key check failed for user %s',inter.user.id)
            await inter.followup.send('Could not verify this key. Contact support before trying again.',ephemeral=True)


class MemberKeyRedeemView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Redeem',style=discord.ButtonStyle.secondary,custom_id='saphire:members:redeem')
    async def redeem(self,inter:discord.Interaction,button:discord.ui.Button):
        await inter.response.send_modal(MemberKeyRedeemModal())


@bot.command(name='discordservicle',aliases=['discordservice','discordservicepanel'])
async def discord_service_panel(ctx, first: str = '', second: str = '', third: str = ''):
    """Owner: ;discordservicle [panel CHANNEL_ID] or ;discordservicle min online|offline AMOUNT."""
    if not owner(ctx.author): return
    if first.lower()=='min':
        kind=second.lower()
        if kind not in ('online','offline') or not third.isdecimal() or not 1<=int(third)<=100000:
            return await ctx.send('Usage: \\x60;discordservicle min online|offline AMOUNT\\x60')
        async with db.pool.acquire() as conn:
            await conn.execute('INSERT INTO public.member_order_settings(member_type,min_amount) VALUES($1,$2) ON CONFLICT(member_type) DO UPDATE SET min_amount=$2',kind,int(third))
        return await ctx.send(f'Minimum {kind} member order changed to **{int(third):,}**.')
    channel_arg=second if first.lower() in ('panel','panal') else first
    if not channel_arg:
        channel=ctx.channel
    elif channel_arg.isdecimal():
        channel=bot.get_channel(int(channel_arg))
    else:
        return await ctx.send('Usage: \\x60;discordservicle [panel CHANNEL_ID]\\x60 or \\x60;discordservicle min online|offline AMOUNT\\x60')
    if not isinstance(channel,discord.TextChannel):
        return await ctx.send('Invalid text channel.')
    await channel.send(view=cv2_layout(emb('Discord member services',
        'Have a Saphire member key? Press **Redeem key** to submit your key and Discord server invite. '
        'Your code is single-use. Keep it private.'),MemberKeyRedeemView()))
    await ctx.send(f'Redeem panel published in {channel.mention}.')

@bot.group(name='discord', invoke_without_command=True)
async def discord_commands(ctx):
    await ctx.send('Usage: `;discord order test`')


@discord_commands.group(name='order', invoke_without_command=True)
async def discord_order_commands(ctx):
    await ctx.send('Usage: `;discord order test`')


@discord_order_commands.command(name='test')
async def discord_delivery_order_test(ctx):
    """Private experimental member delivery checkout; requires verified registration."""
    if not await is_verified(ctx.author.id):
        await send_private_registration(ctx.author)
        return
    wizard = discord_delivery_test.DeliveryWizard(ctx.author.id, bot, db, settings, emb, cv2_layout)
    try:
        await ctx.author.send(view=wizard.layout())
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send('Enable DMs from this server to open your private order panel.', delete_after=20)
        return
    await ctx.send('Your private Discord delivery test panel was sent in DMs.', delete_after=20)



@bot.command(name='orderpanel')
async def orderpanel(ctx, channel_id:int):
    if not owner(ctx.author):return
    channel=bot.get_channel(channel_id)
    if not isinstance(channel,discord.TextChannel):return await ctx.send('Invalid channel ID.')
    await channel.send(view=cv2_layout(emb('Saphire order panel',
        'Create your next order in a private guided checkout. Select platform, service and quantity, then confirm the final price.'),OrderStart()))
    await ctx.send(f'Order panel created in {channel.mention}.')



# The stock browser shares the platform/category classifiers and live services table
# with the order wizard; it never initiates purchases or exposes private balances.
class StockButton(discord.ui.Button):
    def __init__(self, browser, action, label, *, disabled=False, primary=False):
        super().__init__(label=label, disabled=disabled,
                         style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary)
        self.browser, self.action = browser, action

    async def callback(self, inter):
        await self.browser.action(inter, self.action)


class StockSelect(discord.ui.Select):
    def __init__(self, browser, mode, options, placeholder):
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)
        self.browser, self.mode = browser, mode

    async def callback(self, inter):
        await self.browser.select(inter, self.mode, self.values[0])


class StockBrowser(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.stage = 'home'
        self.platform = None
        self.group = None
        self.selected = None
        self.page = 0
        self.listing = []

    async def interaction_check(self, inter):
        if inter.user.id != self.user_id:
            await inter.response.send_message('This private browser belongs to another user.', ephemeral=True)
            return False
        return True

    @staticmethod
    def option(label, value, description=None):
        return discord.SelectOption(label=str(label)[:100], value=str(value)[:100],
            description=(str(description)[:100] if description else None))

    async def fetch_services(self):
        async with db.pool.acquire() as conn:
            rows = await conn.fetch('select * from services order by category,name,id')
        return rows

    async def build(self):
        self.clear_items()
        if self.stage == 'home':
            title = 'Saphire • Service stock'
            detail = 'Browse our current social media service catalog. Select a category below.'
            self.add_item(StockButton(self, 'social', '🌐 Social media', primary=True))
            self.add_item(StockButton(self, 'discord', '💬 Discord services'))
            return emb(title, detail)

        if self.stage in ('discord_menu', 'discord_members'):
            if self.stage == 'discord_menu':
                self.add_item(StockButton(self, 'discord_members', '👥 Discord members', primary=True))
                self.add_item(StockButton(self, 'discord_boosts', 'Server boosts (coming soon)', disabled=True))
                self.add_item(StockButton(self, 'back', '← Back'))
                return emb('Stock • Discord services',
                           'Choose a Discord product below to view current availability.')
            async with db.pool.acquire() as conn:
                stock_rows = await conn.fetch(
                    'SELECT member_type, available, refreshed_at FROM discord_member_stock ORDER BY member_type')
            stock = {r['member_type']: r for r in stock_rows}
            if len(stock) < 2:
                detail = 'Stock information is currently unavailable. The bot refreshes it every 10 minutes.'
            else:
                latest = min(r['refreshed_at'] for r in stock.values())
                age = (dt.datetime.now(dt.timezone.utc) - latest).total_seconds()
                stamp = int(latest.timestamp())
                warning = '\n⚠️ Last update is over 30 minutes old.' if age > 1800 else ''
                detail = (f'**Online members:** {stock["online"]["available"]:,} available\n'
                          f'**Offline members:** {stock["offline"]["available"]:,} available\n\n'
                          f'**Updated:** <t:{stamp}:R>{warning}')
            self.add_item(StockButton(self, 'back', '← Back'))
            return emb('Stock • Discord members', detail)

        rows = await self.fetch_services()
        if self.stage == 'platform':
            title = 'Stock • Choose platform'
            detail = 'Select a platform to see its available service types.'
            platforms = sorted({service_platform(r) for r in rows})
            # Discord menus permit at most 25 options; PLATFORMS is a known
            # curated ordering, so page any additional platforms.
            ordered = [p for p in PLATFORMS if p in platforms] + [p for p in platforms if p not in PLATFORMS]
            self.listing = ordered
            chunk = ordered[self.page * 20:(self.page + 1) * 20]
            if chunk:
                self.add_item(StockSelect(self, 'platform', [self.option(p, p) for p in chunk], 'Choose social platform'))
                detail += f'\n**Platforms:** {len(ordered)} • Page {self.page + 1}/{max(1,(len(ordered)+19)//20)}'
            else:
                detail = 'No services are currently synced. Contact support.'
        elif self.stage == 'group':
            title = f'{self.platform} • Service types'
            filtered = [r for r in rows if service_platform(r) == self.platform]
            groups = {service_group(r) for r in filtered}
            ordered = [g for g in SERVICE_GROUPS if g in groups] + sorted(groups - set(SERVICE_GROUPS))
            self.listing = ordered
            chunk = ordered[self.page * 20:(self.page + 1) * 20]
            detail = f'**Platform:** {self.platform}\nChoose followers, comments, views, auto likes and other categories.\n**Available service types:** {len(ordered)}'
            if chunk:
                self.add_item(StockSelect(self, 'group', [self.option(g, g) for g in chunk], 'Choose service category'))
            else:
                detail += '\nNo matching services found.'
        elif self.stage == 'service':
            title = f'{self.platform} • {self.group}'
            filtered = [r for r in rows if service_platform(r) == self.platform and service_group(r) == self.group]
            self.listing = filtered
            pages = max(1, (len(filtered) + 19) // 20)
            self.page = min(self.page, pages - 1)
            chunk = filtered[self.page * 20:(self.page + 1) * 20]
            detail = f'**Available services:** {len(filtered)}\n**Page:** {self.page + 1}/{pages}\nChoose a service to see details and price.'
            if chunk:
                opts = [self.option(r['name'], str(self.page*20+i),
                        f'ID {r["id"]} • Min {r["min_order"]} / Max {r["max_order"]}')
                        for i, r in enumerate(chunk)]
                self.add_item(StockSelect(self, 'service', opts, 'Select a service'))
            else:
                detail += '\nNo matching services found.'
        else:
            row = self.selected
            title = 'Service details'
            if not row:
                detail = 'This service is no longer available. Return to the list.'
            else:
                description = format_service_description(row['description'], limit=1700)
                detail = (f'**Service:** {discord.utils.escape_markdown(str(row["name"]))[:180]}\n'
                    f'**Service ID:** `{row["id"]}`\n'
                    f'**Platform:** {self.platform}\n'
                    f'**Category:** {self.group}\n'
                    f'**Rate per 1,000:** ${Decimal(str(row["retail_rate"])):,.6f}\n'
                    f'**Minimum order:** {row["min_order"]:,}\n'
                    f'**Maximum order:** {row["max_order"]:,}\n\n'
                    f'**Information**\n{description}')

        if self.stage in ('platform', 'group', 'service'):
            pages = max(1, (len(self.listing) + 19) // 20)
            if self.page:
                self.add_item(StockButton(self, 'prev', '◀ Previous page'))
            if self.page + 1 < pages:
                self.add_item(StockButton(self, 'next', 'Next page ▶'))
        self.add_item(StockButton(self, 'back', '← Back'))
        return emb(title, detail)

    async def render(self, inter):
        view = cv2_layout(await self.build(), self)
        if not inter.response.is_done():
            await inter.response.edit_message(view=view)
        else:
            await inter.followup.send(view=view, ephemeral=True)

    async def select(self, inter, mode, value):
        if mode == 'platform':
            if value not in self.listing:
                return await inter.response.send_message('Selection expired.', ephemeral=True)
            self.platform = value
            self.group = None
            self.stage = 'group'
            self.page = 0
        elif mode == 'group':
            if value not in self.listing:
                return await inter.response.send_message('Selection expired.', ephemeral=True)
            self.group = value
            self.stage = 'service'
            self.page = 0
        else:
            try:
                i = int(value)
            except ValueError:
                return await inter.response.send_message('Selection expired.', ephemeral=True)
            if i < 0 or i >= len(self.listing):
                return await inter.response.send_message('Selection expired.', ephemeral=True)
            self.selected = self.listing[i]
            self.stage = 'details'
        await self.render(inter)

    async def action(self, inter, action):
        if action == 'social':
            self.stage = 'platform'
            self.page = 0
        elif action == 'discord':
            self.stage = 'discord_menu'
            self.page = 0
        elif action == 'discord_members':
            self.stage = 'discord_members'
        elif action == 'home':
            self.stage = 'home'
            self.page = 0
            self.platform = self.group = self.selected = None
        elif action == 'back':
            self.stage = {'platform': 'home', 'group': 'platform',
                          'service': 'group', 'details': 'service',
                          'discord_menu': 'home', 'discord_members': 'discord_menu'}.get(self.stage, 'home')
            self.page = 0
        elif action == 'prev':
            self.page = max(0, self.page - 1)
        elif action == 'next':
            self.page += 1
        await self.render(inter)


class StockPanelStart(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Social media', emoji='🌐', style=discord.ButtonStyle.primary,
                       custom_id='saphire:stock:social')
    async def social(self, inter, button):
        if not await registered(inter):
            return
        browser = StockBrowser(inter.user.id)
        browser.stage = 'platform'
        await inter.response.send_message(view=cv2_layout(await browser.build(), browser), ephemeral=True)

    @discord.ui.button(label='Discord services', emoji='💬', style=discord.ButtonStyle.secondary,
                       custom_id='saphire:stock:discord')
    async def discord_services(self, inter, button):
        if not await registered(inter):
            return
        browser = StockBrowser(inter.user.id)
        browser.stage = 'discord_menu'
        await inter.response.send_message(view=cv2_layout(await browser.build(), browser), ephemeral=True)


@bot.command(name='stockpanel')
async def stockpanel(ctx, channel_id: int):
    if not owner(ctx.author):
        return
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return await ctx.send('Invalid channel ID.')
    await channel.send(view=cv2_layout(emb('Saphire • Service stock',
        'Explore social media services, live pricing, minimum and maximum quantities.\n'
        'Choose a service type below to browse the catalog privately.'), StockPanelStart()))
    await ctx.send(f'Stock panel created in {channel.mention}.')



# PRIVATE PROFILE + OWNER ADMIN PANEL
PROFILE_PAGE_SIZE = 5

async def profile_info(user_id):
    async with db.pool.acquire() as conn:
        return await conn.fetchrow('''SELECT a.discord_id,a.created_at,a.oauth_verified,a.balance_cents,
            (SELECT count(*) FROM orders o WHERE o.discord_id=a.discord_id) AS order_count,
            (SELECT coalesce(sum(o.cost_cents),0) FROM orders o
                WHERE o.discord_id=a.discord_id AND o.status <> 'refunded') AS total_spent,
            (SELECT coalesce(sum(i.amount_cents),0) FROM invoices i
                WHERE i.discord_id=a.discord_id AND i.credited_at IS NOT NULL) AS total_deposited
            FROM accounts a WHERE a.discord_id=$1''',user_id)

async def profile_history(user_id, kind, page):
    table='orders' if kind=='orders' else 'invoices'
    async with db.pool.acquire() as conn:
        total=await conn.fetchval(f'SELECT count(*) FROM {table} WHERE discord_id=$1',user_id)
        if table=='orders':
            rows=await conn.fetch('''SELECT id,service_id,quantity,cost_cents,status,created_at
                 FROM orders WHERE discord_id=$1 ORDER BY created_at DESC,id DESC LIMIT 5 OFFSET $2''',
                 user_id,page*PROFILE_PAGE_SIZE)
        else:
            rows=await conn.fetch('''SELECT id,currency,amount_cents,status,created_at
                 FROM invoices WHERE discord_id=$1 ORDER BY created_at DESC,id DESC LIMIT 5 OFFSET $2''',
                 user_id,page*PROFILE_PAGE_SIZE)
    return rows,total

async def admin_adjust_balance(user_id, cents, actor_id):
    # Single transactional debit/credit with durable audit, no negative balances.
    import secrets
    reference='admin:profile:'+secrets.token_hex(14)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            account=await conn.fetchrow('SELECT balance_cents FROM accounts WHERE discord_id=$1 FOR UPDATE',user_id)
            if not account:raise ValueError('This Discord ID does not have an account.')
            if account['balance_cents']+cents<0:raise ValueError('Insufficient account balance.')
            await conn.execute('''INSERT INTO ledger(discord_id,delta_cents,kind,reference,actor_id)
                VALUES ($1,$2,$3,$4,$5)''',user_id,cents,
                'admin_add' if cents>0 else 'admin_remove',reference,actor_id)
            await conn.execute('UPDATE accounts SET balance_cents=balance_cents+$2 WHERE discord_id=$1',user_id,cents)
            await conn.execute('''INSERT INTO audit_log(actor_id,action,subject,detail)
                VALUES($1,$2,$3,$4::jsonb)''',actor_id,
                'admin_balance_add' if cents>0 else 'admin_balance_remove',str(user_id),
                json.dumps({'cents':cents,'reference':reference}))
    return reference

class ProfileNav(discord.ui.Button):
    """Button-driven private profile navigation, matching the admin panel."""
    def __init__(self,browser,action,label,disabled=False,primary=False):
        super().__init__(label=label,
            style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary,
            disabled=disabled)
        self.browser,self.action=browser,action

    async def callback(self,inter):
        if self.action=='back':self.browser.section='menu';self.browser.page=0
        elif self.action=='previous':self.browser.page=max(0,self.browser.page-1)
        elif self.action=='next':self.browser.page+=1
        else:self.browser.section=self.action;self.browser.page=0
        await self.browser.render(inter)

class ProfileBrowser(discord.ui.View):
    def __init__(self,user_id):
        super().__init__(timeout=600)
        self.user_id=user_id;self.section='menu';self.page=0
    async def interaction_check(self,inter):
        if inter.user.id!=self.user_id:
            await inter.response.send_message('This profile panel is private.',ephemeral=True)
            return False
        return True
    async def build(self):
        self.clear_items()
        if self.section=='menu':
            for action,label in (
                ('balance','💰 Balance'),
                ('orders','🛒 Order history'),
                ('invoices','💳 Top-up history'),
                ('info','👤 Account information'),
            ):
                self.add_item(ProfileNav(self,action,label,primary=action=='balance'))
            return emb('Your Saphire profile',
                       'Choose an option below to view your account details privately.')
        info=await profile_info(self.user_id)
        if not info:
            self.add_item(ProfileNav(self,'back','← Back to menu'))
            return emb('Account not found','Register using `/register` to view your account.')
        if self.section=='balance':
            desc=f'**Available balance:** {usd_text(info["balance_cents"])}'
            title='Your balance'
        elif self.section=='info':
            created=info['created_at']
            stamp=int(created.timestamp()) if created else 0
            desc=(f'**Discord user:** <@{self.user_id}> (`{self.user_id}`)\n'
                  f'**Registered:** <t:{stamp}:f> (<t:{stamp}:R>)\n'
                  f'**Verified:** {"Yes ✅" if info["oauth_verified"] else "No"}\n'
                  f'**Total orders:** {info["order_count"]}\n'
                  f'**Total spent:** {usd_text(info["total_spent"])}\n'
                  f'**Total deposited:** {usd_text(info["total_deposited"])}')
            title='Account information'
        else:
            rows,total=await profile_history(self.user_id,self.section,self.page)
            last=max(0,(total-1)//PROFILE_PAGE_SIZE)
            self.page=min(self.page,last)
            if self.page>0 and not rows:rows,total=await profile_history(self.user_id,self.section,self.page)
            lines=[]
            for r in rows:
                stamp=int(r['created_at'].timestamp()) if r['created_at'] else 0
                if self.section=='orders':
                    lines.append(f'**#{r["id"]}** · `{r["service_id"]}` × {r["quantity"]}\n'
                                 f'{usd_text(r["cost_cents"])} · {r["status"]} · <t:{stamp}:R>')
                else:
                    lines.append(f'**{r["id"]}** · {r["currency"]}\n'
                                 f'{usd_text(r["amount_cents"])} · {r["status"]} · <t:{stamp}:R>')
            title='Order history' if self.section=='orders' else 'Top-up history'
            desc=('\n\n'.join(lines) if lines else 'No records yet.')+f'\n\n**Page {self.page+1} of {last+1}**'
            self.add_item(ProfileNav(self,'previous','← Previous',self.page==0))
            self.add_item(ProfileNav(self,'next','Next →',self.page>=last))
        self.add_item(ProfileNav(self,'back','← Back to menu'))
        return emb(title,desc)
    async def render(self,inter):
        await inter.response.edit_message(view=cv2_layout(await self.build(),self))

class ProfileStart(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='Start',emoji='👤',style=discord.ButtonStyle.primary,custom_id='saphire:profile:start')
    async def start(self,inter,button):
        if not await registered(inter):return
        browser=ProfileBrowser(inter.user.id)
        await inter.response.send_message(view=cv2_layout(await browser.build(),browser),ephemeral=True)

class AdminUserModal(discord.ui.Modal):
    def __init__(self,browser):
        super().__init__(title='Find Discord account')
        self.browser=browser
        self.user_id_input=discord.ui.TextInput(label='Discord user ID',placeholder='123456789012345678',min_length=15,max_length=22)
        self.add_item(self.user_id_input)
    async def on_submit(self,inter):
        if not owner(inter.user) or inter.user.id!=self.browser.owner_id:
            return await inter.response.send_message('Owner only.',ephemeral=True)
        value=str(self.user_id_input.value).strip()
        if not value.isdigit():return await inter.response.send_message('Invalid Discord ID.',ephemeral=True)
        user_id=int(value)
        if not await db.account(user_id):
            return await inter.response.send_message('No registered Saphire account matches this ID.',ephemeral=True)
        self.browser.target_id=user_id;self.browser.section='menu';self.browser.page=0
        await inter.response.send_message(view=cv2_layout(await self.browser.build(),self.browser),ephemeral=True)

class AdminAmountModal(discord.ui.Modal):
    def __init__(self,browser,mode):
        super().__init__(title='Add balance' if mode=='add' else 'Remove balance')
        self.browser,self.mode=browser,mode
        self.amount=discord.ui.TextInput(label='USD amount',placeholder='10.00',max_length=15)
        self.add_item(self.amount)
    async def on_submit(self,inter):
        if not owner(inter.user) or inter.user.id!=self.browser.owner_id:
            return await inter.response.send_message('Owner only.',ephemeral=True)
        try:
            amount=Decimal(str(self.amount.value).strip())
            if not amount.is_finite() or amount<=0 or amount>100000:raise ValueError()
            cents=int(amount*100)
            if Decimal(cents)/100!=amount:raise ValueError()
            if self.mode=='remove':cents=-cents
            reference=await admin_adjust_balance(self.browser.target_id,cents,inter.user.id)
        except ValueError as exc:return await inter.response.send_message(f'Invalid balance adjustment: {exc}',ephemeral=True)
        except Exception:
            log.exception('Admin balance adjustment failed')
            return await inter.response.send_message('Balance adjustment failed. Check server logs.',ephemeral=True)
        self.browser.section='balance'
        await inter.response.send_message(view=cv2_layout(emb('Balance updated',
            f'**Target:** <@{self.browser.target_id}>\n**Change:** {usd_text(cents)}\n**Reference:** `{reference}`'),
            AdminBrowser(self.browser.owner_id,self.browser.target_id,section='balance')),ephemeral=True)

class AdminButton(discord.ui.Button):
    def __init__(self,browser,action,label,*,disabled=False,primary=False):
        super().__init__(label=label,disabled=disabled,
            style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary)
        self.browser,self.action=browser,action
    async def callback(self,inter):
        if self.action=='user':return await inter.response.send_modal(AdminUserModal(self.browser))
        if self.action in ('add','remove'):
            return await inter.response.send_modal(AdminAmountModal(self.browser,self.action))
        if self.action=='back':self.browser.section='menu';self.browser.page=0
        elif self.action=='previous':self.browser.page=max(0,self.browser.page-1)
        elif self.action=='next':self.browser.page+=1
        else:self.browser.section=self.action;self.browser.page=0
        await self.browser.render(inter)

class AdminBrowser(discord.ui.View):
    def __init__(self,owner_id,target_id=None,section='menu'):
        super().__init__(timeout=600)
        self.owner_id,self.target_id,self.section=owner_id,target_id,section
        self.page=0
    async def interaction_check(self,inter):
        if not owner(inter.user) or inter.user.id!=self.owner_id:
            await inter.response.send_message('Only the bot owner can access this panel.',ephemeral=True)
            return False
        return True
    async def build(self):
        self.clear_items()
        if self.target_id is None:
            self.add_item(AdminButton(self,'user','Enter Discord user ID',primary=True))
            return emb('Saphire administrator','Choose a Discord user ID to manage their account.')
        if self.section=='menu':
            self.add_item(AdminButton(self,'user','Change user ID'))
            for action,label in [('balance','Balance'),('invoices','Top-up history'),('orders','Order history'),
                                 ('add','Add balance'),('remove','Remove balance'),('reviews','Orders needing review')]:
                self.add_item(AdminButton(self,action,label))
            return emb('Admin · Account management',f'**Selected user:** <@{self.target_id}> (`{self.target_id}`)\nChoose an action.')
        if self.section=='balance':
            info=await profile_info(self.target_id)
            body=f'**User:** <@{self.target_id}>\n**Balance:** {usd_text(info["balance_cents"])}' if info else 'Account not found.'
            title='Balance'
        else:
            async with db.pool.acquire() as conn:
                if self.section=='reviews':
                    total=await conn.fetchval("SELECT count(*) FROM orders WHERE status='needs_review'")
                    rows=await conn.fetch('''SELECT id,discord_id,service_id,cost_cents,status,created_at
                        FROM orders WHERE status='needs_review' ORDER BY created_at DESC,id DESC
                        LIMIT 5 OFFSET $1''',self.page*PROFILE_PAGE_SIZE)
                else:
                    rows,total=await profile_history(self.target_id,self.section,self.page)
            last=max(0,(total-1)//PROFILE_PAGE_SIZE)
            self.page=min(self.page,last)
            if self.section=='reviews':
                lines=[f'**Order #{r["id"]}** · <@{r["discord_id"]}>\n'
                       f'`{r["service_id"]}` · {usd_text(r["cost_cents"])} · <t:{int(r["created_at"].timestamp())}:R>' for r in rows]
            elif self.section=='orders':
                lines=[f'**#{r["id"]}** · `{r["service_id"]}` × {r["quantity"]}\n'
                           f'{usd_text(r["cost_cents"])} · {r["status"]}' for r in rows]
            else:
                lines=[f'**{r["id"]}** · {r["currency"]} · '
                           f'{usd_text(r["amount_cents"])} · {r["status"]}' for r in rows]
            body=('\n\n'.join(lines) or 'No records found.')+f'\n\nPage {self.page+1} of {last+1}'
            title={'orders':'Order history','invoices':'Top-up history','reviews':'Needs staff review'}[self.section]
            self.add_item(AdminButton(self,'previous','← Previous',disabled=self.page==0))
            self.add_item(AdminButton(self,'next','Next →',disabled=self.page>=last))
        self.add_item(AdminButton(self,'back','← Back to admin menu'))
        return emb(title,body)
    async def render(self,inter):
        await inter.response.edit_message(view=cv2_layout(await self.build(),self))

class AdminStart(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='Start admin panel',emoji='🔐',style=discord.ButtonStyle.primary,custom_id='saphire:admin:start')
    async def start(self,inter,button):
        if not owner(inter.user):
            return await inter.response.send_message('Owner only.',ephemeral=True)
        panel=AdminBrowser(inter.user.id)
        await inter.response.send_message(view=cv2_layout(await panel.build(),panel),ephemeral=True)

@bot.command(name='profilepanel')
async def profilepanel(ctx,channel_id:int):
    if not owner(ctx.author):return
    channel=bot.get_channel(channel_id)
    if not isinstance(channel,discord.TextChannel):return await ctx.send('Invalid channel ID.')
    await channel.send(view=cv2_layout(emb('Saphire • Your account',
        'Access your balance, order history, top-up history and account statistics privately.'),ProfileStart()))
    await ctx.send(f'Profile panel posted in {channel.mention}.')

@bot.command(name='adminpanel')
async def adminpanel(ctx,channel_id:int):
    if not owner(ctx.author):return
    channel=bot.get_channel(channel_id)
    if not isinstance(channel,discord.TextChannel):return await ctx.send('Invalid channel ID.')
    await channel.send(view=cv2_layout(emb('Saphire • Administration',
        'Owner-only account management. All interactions remain private.'),AdminStart()))
    await ctx.send(f'Admin panel posted in {channel.mention}.')

@bot.group(name='profile',invoke_without_command=True)
async def profile_group(ctx):
    if owner(ctx.author):
        await ctx.send('Use `;profile panel CHANNEL_ID` to post a profile panel.')

@profile_group.command(name='panel')
async def profile_group_panel(ctx,channel_id:int):
    await profilepanel.callback(ctx,channel_id)

@bot.group(name='admin',invoke_without_command=True)
async def admin_group(ctx):
    if owner(ctx.author):
        await ctx.send('Use `;admin panel CHANNEL_ID` to post an admin panel.')

@admin_group.command(name='panel')
async def admin_group_panel(ctx,channel_id:int):
    await adminpanel.callback(ctx,channel_id)

@bot.event
async def on_member_join(member):
    # Each welcome mention is deleted after one second. Channels are explicitly
    # configured to avoid accidentally pinging the user throughout the server.
    if settings.discord_guild_id and member.guild.id!=settings.discord_guild_id:return
    for channel_id in settings.welcome_channel_ids:
        channel=member.guild.get_channel(channel_id)
        if not isinstance(channel,discord.TextChannel):continue
        try:
            msg=await channel.send(f'{member.mention} welcome!',
                allowed_mentions=discord.AllowedMentions(users=[member],roles=False,everyone=False))
            await msg.delete(delay=1)
        except discord.HTTPException:
            log.exception('Could not send/delete welcome ping in channel %s',channel_id)


# Persist stock panel message references so existing panels survive bot restarts.
_RESTOCK_PANELS_FILE = os.path.join(os.path.dirname(__file__), 'restock_panels.json')
_restock_panel_refs = None

def _restock_refs():
    global _restock_panel_refs
    if _restock_panel_refs is None:
        try:
            with open(_RESTOCK_PANELS_FILE, encoding='utf-8') as handle:
                items = json.load(handle)
            _restock_panel_refs = [[int(a), int(b)] for a, b in items if int(a) > 0 and int(b) > 0] if isinstance(items, list) else []
        except (OSError, ValueError, TypeError):
            _restock_panel_refs = []
    return _restock_panel_refs

def _save_restock_refs():
    filename = _RESTOCK_PANELS_FILE + '.tmp'
    with open(filename, 'w', encoding='utf-8') as handle:
        json.dump(_restock_refs(), handle)
    os.replace(filename, _RESTOCK_PANELS_FILE)

async def _restock_panel_content():
    stock = {}
    try:
        async with db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT member_type,available,refreshed_at FROM public.discord_member_stock "
                "WHERE member_type = ANY($1::text[])", ['online', 'offline'])
        stock = {r['member_type']: r for r in rows}
    except Exception:
        log.exception('Unable to read Discord member stock for restock panel')
    now = discord.utils.utcnow()
    fresh = all(k in stock and stock[k]['refreshed_at'] is not None
                and (now - stock[k]['refreshed_at']).total_seconds() < 180
                for k in ('online', 'offline'))
    def formatted(kind):
        row = stock.get(kind)
        return f"{int(row['available']):,}" if fresh and row and row['available'] is not None else 'Unavailable'
    if fresh:
        last = min(stock[k]['refreshed_at'] for k in ('online', 'offline'))
        next_at = last + dt.timedelta(seconds=60)
        when = int(next_at.timestamp())
        updated = int(last.timestamp())
        info = (f"Last updated: <t:{updated}:R>\n"
                f"Next stock check: <t:{when}:R> (<t:{when}:T>)")
    else:
        info = 'Stock data is unavailable or outdated. Next check: within 60 seconds if automatic sync is running.'
    return ('**Online members**\n' + formatted('online') + ' available\n\n'
            '**Offline members**\n' + formatted('offline') + ' available\n\n' + info)

async def _refresh_restock_panels():
    refs = _restock_refs()
    if not refs:
        return
    content = await _restock_panel_content()
    stale = []
    for channel_id, message_id in list(refs):
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await message.edit(view=cv2_layout(emb('Discord members stock', content)))
        except (discord.NotFound, discord.Forbidden):
            stale.append([channel_id, message_id])
        except discord.HTTPException:
            log.exception('Could not update restock panel message %s', message_id)
    if stale:
        for ref in stale:
            if ref in refs:
                refs.remove(ref)
        try:
            _save_restock_refs()
        except OSError:
            log.exception('Could not persist restock panel references')

# Cache Discord members inventory separately from Top4SMM service catalog.
@tasks.loop(seconds=60, reconnect=True)
async def discord_member_stock_maintenance():
    try:
        snapshot = await member_stock.sync_stock(db, settings.discord_member_stock_url)
        log.info('Discord member stock refreshed: online=%s offline=%s',
                 snapshot['online'], snapshot['offline'])
        await _refresh_restock_panels()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Discord member stock sync failed; retaining last known values; retrying in 60 seconds')

@discord_member_stock_maintenance.before_loop
async def before_discord_member_stock_maintenance():
    await bot.wait_until_ready()

# Service prices and availability are refreshed on startup and every 60 seconds.
# Failures are isolated so the last known-good catalog remains usable.
_service_sync_lock = asyncio.Lock()

async def refresh_service_catalog():
    async with _service_sync_lock:
        count = await provider.sync_services(db)
        log.info('Service catalog refreshed: %s services', count)
        return count

@tasks.loop(seconds=60, reconnect=True)
async def service_catalog_maintenance():
    try:
        await refresh_service_catalog()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Automatic service sync failed; retrying in 60 seconds')

@service_catalog_maintenance.before_loop
async def before_service_catalog_maintenance():
    await bot.wait_until_ready()


@tasks.loop(seconds=PAYMENT_CONFIRM_POLL)
async def invoice_maintenance():
    now=dt.datetime.now(dt.timezone.utc)
    async with db.pool.acquire() as conn:
        expired=await conn.fetch('''update invoices set status='expired'
          where status='pending' and expires_at<now() returning id,channel_id''')
        pending=await conn.fetch('''select id from invoices where status='confirming' and txid is not null''')
    for row in expired:
        if row['channel_id']:asyncio.create_task(invoice_channel_close(row['channel_id'],1))
    for row in pending:
        try:await verify_crypto_invoice(row['id'])
        except Exception:log.exception('Invoice maintenance failed for %s',row['id'])

@bot.listen('on_message')
async def paypal_receipt_listener(message):
    if message.author.bot or not isinstance(message.channel,discord.TextChannel):return
    if not message.channel.name.startswith('payment-'):return
    async with db.pool.acquire() as conn:
        row=await conn.fetchrow("select id,discord_id from invoices where channel_id=$1 and currency='PAYPAL' and status='staff_review'",message.channel.id)
    if not row or row['discord_id']!=message.author.id:return
    if not any(att.content_type and att.content_type.startswith('image/') for att in message.attachments):
        # Limit customer posting to screenshot images; no unrelated chat.
        try:await message.delete()
        except discord.HTTPException:pass
        return
    try:
        await message.channel.set_permissions(message.author,view_channel=True,read_message_history=True,
             send_messages=False,attach_files=False,embed_links=False,reason='PayPal screenshot submitted')
        await message.channel.send(view=cv2_layout(emb('Screenshot received ✅',
            'Thank you. Please wait for a staff member to review your screenshot and '
            'independently verify the payment in PayPal.')))
    except discord.HTTPException:log.exception('Failed to lock PayPal user after screenshot %s',row['id'])

@bot.event
async def on_ready():
    if not getattr(bot,'db_ready',False):
        bot.db_ready=True
        bot.add_view(TicketPanel());bot.add_view(CloseTicketView());bot.add_view(OrderStart());bot.add_view(StockPanelStart());bot.add_view(ProfileStart());bot.add_view(AdminStart());bot.add_view(RestockRoleView());bot.add_view(MemberKeyRedeemView())
        async with db.pool.acquire() as conn:
            running=await conn.fetch("select id,currency from invoices where channel_id is not null and status in ('pending','confirming','staff_review')")
        for active in running:
            bot.add_view(InvoiceControls(active['id'],active['currency']))
        if not giveaway_manager.tick.is_running():giveaway_manager.tick.start()
        if not invoice_maintenance.is_running():invoice_maintenance.start()
        if not service_catalog_maintenance.is_running():service_catalog_maintenance.start()
        if not discord_member_stock_maintenance.is_running():discord_member_stock_maintenance.start()
        try:
            await bot.tree.sync()
        except discord.HTTPException:
            log.exception('Failed to sync /register and /verify commands')
        rotate_status.start()
    os.system('cls' if os.name=='nt' else 'clear')
    print(ASCII)
    users = 0
    balance = 0
    try:
        async with db.pool.acquire() as conn:
            stats = await conn.fetchrow(
                'SELECT COUNT(*) AS users, COALESCE(SUM(balance_cents), 0) AS balance '
                'FROM accounts'
            )
            users = stats['users']
            balance = stats['balance']
    except asyncpg.exceptions.UndefinedTableError as exc:
        # Startup display only: missing schema is not a valid empty database
        # for registration, orders or payment operations.
        if exc.table_name and exc.table_name != 'accounts':
            raise
        log.warning('Accounts table does not exist. Displaying zero startup totals. Apply sql/schema.sql to enable user accounts and orders.')
    log.info('[INFO] %s has been launched online',bot.user)
    log.info('[INFO] Users registered - %s',users)
    log.info('[INFO] Total wallet balance - $%.2f',balance/100)
    try:
        p=await provider.call('balance');log.info('[INFO] Provider balance - %s',p.get('balance','unknown'))
    except Exception as exc:
        log.warning('Provider balance unavailable: %s: %s', type(exc).__name__, exc)

@tasks.loop(seconds=10)
async def rotate_status():
    names=[f'saphire -_-']
    n=getattr(bot,'presence_idx',0);bot.presence_idx=n+1
    await bot.change_presence(activity=discord.Streaming(name=names[n%len(names)],url='https://www.twitch.tv/discord'))

@bot.command(name='register')
async def register_command(ctx):
    try:
        if await is_verified(ctx.author.id):
            return await ctx.author.send(view=cv2_layout(registration_success_embed(ctx.author)))
    except Exception:
        log.exception('Could not check account for ;register')
        try:
            await ctx.author.send(view=cv2_layout(emb('Service unavailable',
                'Cannot check registration status at the moment.')))
        except (discord.Forbidden, discord.HTTPException):
            pass
        return
    await send_private_registration(ctx.author)


@bot.group(name='verify', aliases=['verified'], invoke_without_command=True)
async def verify_command(ctx):
    try:
        if await is_verified(ctx.author.id):
            await ctx.author.send(view=cv2_layout(registration_success_embed(ctx.author)))
        else:
            await send_private_registration(ctx.author)
    except (discord.Forbidden, discord.HTTPException):
        pass
    except Exception:
        log.exception('Could not check ;verify status')


@bot.tree.command(name='register', description='Privately register or check your Saphire account')
async def slash_register(interaction: discord.Interaction):
    try:
        if await is_verified(interaction.user.id):
            return await interaction.response.send_message(
                view=cv2_layout(registration_success_embed(interaction.user)), ephemeral=True)
    except Exception:
        log.exception('Could not check account for /register')
        return await interaction.response.send_message(
            view=cv2_layout(emb('Service unavailable', 'Verification is temporarily unavailable.')),
            ephemeral=True)
    await interaction.response.send_message(view=cv2_layout(
        emb('Saphire registration', 'Choose a referral code or skip to verify with Discord.'),
        ReferralView()), ephemeral=True)


@bot.tree.command(name='verify', description='Privately check your Saphire registration status')
async def slash_verify(interaction: discord.Interaction):
    try:
        if await is_verified(interaction.user.id):
            return await interaction.response.send_message(
                view=cv2_layout(registration_success_embed(interaction.user)), ephemeral=True)
    except Exception:
        log.exception('Could not check account for /verify')
        return await interaction.response.send_message(
            view=cv2_layout(emb('Verification unavailable', 'Try again shortly.')), ephemeral=True)
    await registered(interaction)

@bot.command(name='balance',aliases=['bal'])
async def balance(ctx):
    a=await db.account(ctx.author.id)
    if not a or not a['oauth_verified']:return await send_private_registration(ctx.author)
    await ctx.send(view=cv2_layout(emb('Balance',f'Available: **${a["balance_cents"]/100:.2f}**'), None))

@bot.command(name='services',aliases=['service'])
async def services(ctx,*,search=''):
    async with db.pool.acquire() as conn:
        rows=await conn.fetch('select * from services where lower(name) like $1 order by category,name limit 15',f'%{search.lower()}%')
    if not rows:return await ctx.send('No matching services. Owner: run `;syncservices`.')
    await ctx.send(view=cv2_layout(emb('Services', '\n'.join(f'`{r["id"]}` {discord.utils.escape_markdown(r["name"])[:65]} — **${r["retail_rate"]}/1k** ({r["min_order"]}-{r["max_order"]})' for r in rows)), None))

@bot.command(name='order',aliases=['createorder'])
async def order(ctx,service_id:str=None,quantity:int=None,link:str=None,*,options:str=''):
    a=await db.account(ctx.author.id)
    if not a or not a['oauth_verified']:return await send_private_registration(ctx.author)
    if not service_id or not quantity or not link:return await ctx.send('Usage: `;order SERVICE_ID QUANTITY https://target-url` (optional JSON options: comment, likes, sending_delay, drip_feed_likes, drip_feed_interval).')
    if not link.startswith(('https://','http://')) or len(link)>2048:return await ctx.send('Invalid URL.')
    try: extra=json.loads(options) if options else {}
    except json.JSONDecodeError:return await ctx.send('Options must be valid JSON.')
    allowed={'comment','likes','sending_delay','drip_feed_likes','drip_feed_interval'}
    if not isinstance(extra,dict) or (extra.keys()-allowed):return await ctx.send('Invalid options.')
    async with db.pool.acquire() as conn: s=await conn.fetchrow('select * from services where id=$1',service_id)
    if not s:return await ctx.send('Unknown service ID.')
    if not s['min_order']<=quantity<=s['max_order']:return await ctx.send(f'Quantity must be {s["min_order"]}–{s["max_order"]}.')
    price=max(1,provider.cents_for(quantity,s['retail_rate']))
    try:oid=await db.spend_and_create_order(ctx.author.id,service_id,quantity,link,price,json.dumps(extra))
    except ValueError as e:return await ctx.send(str(e))
    try:
        result=await provider.call('new_order',service_id=service_id,count=quantity,link=link,**extra)
        provider_id=str(result.get('order') or result.get('order_id') or '')
        if not provider_id: raise provider.ProviderError('Top4SMM did not return an order ID')
        async with db.pool.acquire() as conn:await conn.execute('update orders set provider_order_id=$1,status=$2,provider_cost_cents=$3 where id=$4 and discord_id=$5',provider_id,'pending',int(Decimal(str(result.get('charge',result.get('sum',0))))*100),oid,ctx.author.id)
        log.info('[INFO] order placed %s quantity=%s charged=$%.2f provider=$%s',service_id,quantity,price/100,result.get('charge',result.get('sum','unknown')))
        await send_history_event('order',ctx.author.id,oid,price,f'{service_platform(s)} • {service_group(s)}')
        await ctx.send(view=cv2_layout(emb('Order placed',f'Order **#{oid}**\nQuantity: {quantity}\nCharged: ${price/100:.2f}'), None))
    except Exception:
        # Network timeouts may mean provider accepted order. Do NOT blindly refund/retry.
        async with db.pool.acquire() as conn:await conn.execute("update orders set status='needs_review' where id=$1 and discord_id=$2",oid,ctx.author.id)
        await ctx.send(view=cv2_layout(emb('Order under review',f'Order #{oid} requires staff reconciliation before retry or refund.'), None))

@bot.command(name='orderhistory')
async def history(ctx):
    async with db.pool.acquire() as conn:rows=await conn.fetch('select * from orders where discord_id=$1 order by id desc limit 15',ctx.author.id)
    await ctx.send(view=cv2_layout(emb('Order history','\n'.join(f'`#{r["id"]}` {r["service_id"]} ×{r["quantity"]} — {r["status"]} (${r["cost_cents"]/100:.2f})' for r in rows) or 'No orders yet.'), None))

@bot.command(name='orderstatus',aliases=['checkorder','ordercheck'])
async def order_status(ctx,oid:int):
    async with db.pool.acquire() as conn:row=await conn.fetchrow('select * from orders where id=$1',oid)
    if not row or (row['discord_id']!=ctx.author.id and not support(ctx.author)):return await ctx.send('Order not found.')
    status=row['status']
    if row['provider_order_id']:
        try:
            info=await provider.call('order_info',id=row['provider_order_id'])
            status=str(info.get('status',status))
            async with db.pool.acquire() as conn:await conn.execute('update orders set status=$1 where id=$2 and discord_id=$3',status,oid,row['discord_id'])
        except Exception:pass
    await ctx.send(view=cv2_layout(emb(f'Order #{oid}',f'**Owner:** <@{row["discord_id"]}> (`{row["discord_id"]}`)\nStatus: **{status}**'), None))

@bot.command(name='topuphistory', aliases=['invoicehistory'])
async def topup_history(ctx):
    # Only the account owner can read their invoice history.
    async with db.pool.acquire() as conn:
        rows=await conn.fetch('''select id,currency,amount_cents,status,created_at
            from invoices where discord_id=$1 order by created_at desc,id desc limit 15''',ctx.author.id)
    desc='\n'.join(f'`{r["id"]}` • {r["currency"]} • {usd_text(r["amount_cents"])} • {r["status"]}'
                    for r in rows) or 'No top-ups yet.'
    try:
        await ctx.author.send(view=cv2_layout(emb('Your top-up history',desc)))
    except (discord.Forbidden, discord.HTTPException):
        log.info('Could not privately deliver invoice history for %s',ctx.author.id)


@bot.tree.command(name='topuphistory',description='Privately view your own payment invoices')
async def slash_topup_history(inter:discord.Interaction):
    if not await registered(inter):return
    async with db.pool.acquire() as conn:
        rows=await conn.fetch('''select id,currency,amount_cents,status
            from invoices where discord_id=$1 order by created_at desc,id desc limit 15''',inter.user.id)
    desc='\n'.join(f'`{r["id"]}` • {r["currency"]} • {usd_text(r["amount_cents"])} • {r["status"]}'
                    for r in rows) or 'No top-ups yet.'
    await inter.response.send_message(view=cv2_layout(emb('Your top-up history',desc)),ephemeral=True)


@bot.tree.command(name='topupstatus',description='Privately check an invoice you own')
@app_commands.describe(invoice_id='Your Saphire invoice ID')
async def slash_topup_status(inter:discord.Interaction,invoice_id:str):
    if not await registered(inter):return
    async with db.pool.acquire() as conn:
        row=await conn.fetchrow('''select id,currency,amount_cents,status,confirmations,created_at
            from invoices where id=$1 and discord_id=$2''',invoice_id.strip(),inter.user.id)
    if not row:
        return await inter.response.send_message('Invoice not found for your account.',ephemeral=True)
    desc=(f'**Invoice:** `{row["id"]}`\n**Payment:** {row["currency"]}\n'
          f'**Amount:** {usd_text(row["amount_cents"])}\n**Status:** {row["status"]}\n'
          f'**Confirmations:** {row["confirmations"]}')
    await inter.response.send_message(view=cv2_layout(emb('Your invoice status',desc)),ephemeral=True)


@bot.tree.command(name='topup',description='Privately open the Saphire top-up panel')
async def slash_topup(interaction: discord.Interaction):
    if not await registered(interaction):return
    await interaction.response.send_message(view=cv2_layout(emb('Top up balance',
        'Select PayPal or cryptocurrency to start your private payment invoice.'),PaymentPanel()),ephemeral=True)

@bot.command(name='topup',aliases=['addcredit','creditadd'])
async def topup(ctx,action:str='panel'):
    if action.lower()!='panel':return await ctx.send('Usage: `;topup panel`')
    if not support(ctx.author):return await ctx.send('Staff only: ask a team member to publish the top-up panel.')
    await ctx.send(view=cv2_layout(emb('Saphire top up',
        'Add funds securely using cryptocurrency or PayPal. Press **Start** to open your private checkout.'),TopupStart()))


class OverrideLookup(discord.ui.Modal,title='Invoice override lookup'):
    invoice_id=discord.ui.TextInput(label='Invoice ID',min_length=8,max_length=80)
    async def on_submit(self,inter):
        if not support(inter.user):return await inter.response.send_message('Staff only.',ephemeral=True)
        row=await invoice_row(str(self.invoice_id.value).strip())
        if not row:return await inter.response.send_message('Invoice not found.',ephemeral=True)
        await inter.response.send_message(view=cv2_layout(emb('Invoice override',
            f'**Invoice:** `{row["id"]}`\n**Method:** {row["currency"]}\n'
            f'**Amount:** {usd_text(row["amount_cents"])}\n**Status:** {row["status"]}\n\n'
            'Completion is a manual financial override. Verify the payment independently.'),
            OverrideActions(row['id'])),ephemeral=True)

class OverrideActions(discord.ui.View):
    def __init__(self,invoice_id):
        super().__init__(timeout=300);self.invoice_id=invoice_id
    async def interaction_check(self,inter):
        if not support(inter.user):
            await inter.response.send_message('Staff only.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Cancel payment',style=discord.ButtonStyle.danger)
    async def cancel(self,inter,button):
        async with db.pool.acquire() as conn:
            # Preserve ledger and audit evidence; scrub rejected tx details and
            # unlink channel so poller stops checking it.
            row=await conn.fetchrow('''update invoices set status='cancelled',txid=null,confirmations=0
                where id=$1 and status not in ('credited','cancelled') returning *''',self.invoice_id)
            if row:
                await conn.execute('''insert into audit_log(actor_id,action,subject,detail)
                    values($1,'payment_override_cancel',$2,$3::jsonb)''',inter.user.id,self.invoice_id,
                    json.dumps({'previous_status':'unsettled'}))
        if not row:return await inter.response.send_message('Cannot cancel an already credited or cancelled invoice.',ephemeral=True)
        await update_invoice_panel(row)
        if row['channel_id']:asyncio.create_task(invoice_channel_close(row['channel_id'],1))
        await inter.response.edit_message(view=cv2_layout(emb('Invoice cancelled',
            f'`{self.invoice_id}` cancelled and checks stopped.',0xE45A71)))
    @discord.ui.button(label='Complete payment',style=discord.ButtonStyle.success)
    async def complete(self,inter,button):
        # Manual approval uses the SAME unique-reference ledger settlement as
        # normal verification, with a recorded approving staff member.
        row=await invoice_row(self.invoice_id)
        if not row or row['status'] in ('credited','cancelled','expired'):
            return await inter.response.send_message('Invoice cannot be completed.',ephemeral=True)
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                locked=await conn.fetchrow('select * from invoices where id=$1 for update',self.invoice_id)
                if locked['status'] not in ('pending','confirming','staff_review'):
                    return await inter.response.send_message('Invoice is no longer active.',ephemeral=True)
                reference='invoice:'+self.invoice_id
                inserted=await conn.fetchval('''insert into ledger(discord_id,delta_cents,kind,reference,actor_id)
                    values($1,$2,'payment',$3,$4) on conflict(reference) do nothing returning id''',
                    locked['discord_id'],locked['amount_cents'],reference,inter.user.id)
                if not inserted:return await inter.response.send_message('Already credited.',ephemeral=True)
                await conn.execute('update accounts set balance_cents=balance_cents+$2 where discord_id=$1',locked['discord_id'],locked['amount_cents'])
                await conn.execute("update invoices set status='credited',credited_at=now() where id=$1",self.invoice_id)
                await conn.execute('''insert into audit_log(actor_id,action,subject,detail)
                    values($1,'payment_override_complete',$2,$3::jsonb)''',inter.user.id,self.invoice_id,
                    json.dumps({'manual_override':True,'verified_externally_by_staff':True}))
        latest=await invoice_row(self.invoice_id)
        if latest['currency']!='PAYPAL':await remove_crypto_instructions(self.invoice_id)
        await on_invoice_verified(latest)
        await inter.response.edit_message(view=cv2_layout(emb('Manual payment completed',
            f'Invoice `{self.invoice_id}` credited and logged.',0x57C28D)))

class OverrideStart(discord.ui.View):
    def __init__(self):super().__init__(timeout=300)
    @discord.ui.button(label='Search invoice ID',style=discord.ButtonStyle.primary)
    async def lookup(self,inter,button):
        if not support(inter.user):return await inter.response.send_message('Staff only.',ephemeral=True)
        await inter.response.send_modal(OverrideLookup())

@bot.command(name='override')
async def override_panel(ctx):
    if not support(ctx.author):return
    await ctx.send(view=cv2_layout(emb('Staff invoice override',
        'Search an invoice to cancel it or manually approve the payment. Every action is audited.'),OverrideStart()))

@bot.command(name='ticketcreate')
async def ticketcreate(ctx,channel_id:int):
    if not owner(ctx.author):return
    channel=bot.get_channel(channel_id)
    if not isinstance(channel,discord.TextChannel):return await ctx.send('Invalid channel.')
    await channel.send(view=cv2_layout(emb('Saphire support','Choose a topic below to open a private ticket.'), TicketPanel()))

@bot.command(name='panelcreate')
async def panelcreate(ctx,channel_id:int):
    if not owner(ctx.author):return
    channel=bot.get_channel(channel_id)
    if not isinstance(channel,discord.TextChannel):return await ctx.send('Invalid channel.')
    await channel.send(view=cv2_layout(emb('Top up balance','Choose a payment method below.'), PaymentPanel()))

@bot.command(name='hide')
async def hide(ctx):
    if not owner(ctx.author):return
    await ctx.channel.set_permissions(ctx.guild.default_role,view_channel=False)
    await ctx.send('Channel hidden.')
@bot.command(name='unhide')
async def unhide(ctx):
    if not owner(ctx.author):return
    await ctx.channel.set_permissions(ctx.guild.default_role,overwrite=None)
    await ctx.send('Default-role channel override removed (category permissions still apply).')
@bot.command(name='delete')
async def delete(ctx):
    if owner(ctx.author):await ctx.channel.delete(reason=f'Owner request {ctx.author.id}')

@bot.group(name='user',invoke_without_command=True)
async def user_group(ctx):await ctx.send('Commands: `;user search ID`, `;user manage ID`, `;user manage ID credit AMOUNT`')
@user_group.command(name='search')
async def user_search(ctx,user_id:int):
    if not support(ctx.author):return
    a=await db.account(user_id)
    await ctx.send(view=cv2_layout(emb('User search',f'User: `{user_id}`\nBalance: ${a["balance_cents"]/100:.2f}\nVerified: {a["oauth_verified"]}' if a else 'No account.'), None))
@user_group.command(name='manage')
async def user_manage(ctx,user_id:int,action:str='',amount:str=''):
    if not owner(ctx.author):return
    if action!='credit':return await user_search(ctx,user_id)
    try:cents=int(Decimal(amount)*100)
    except Exception:return await ctx.send('Usage: `;user manage USER_ID credit 10.00`')
    if cents<=0:return await ctx.send('Amount must be positive.')
    import secrets
    ref='admin:'+secrets.token_hex(12)
    await db.credit(user_id,cents,ref,'admin',ctx.author.id)
    async with db.pool.acquire() as conn:await conn.execute('insert into audit_log(actor_id,action,subject) values($1,$2,$3)',ctx.author.id,'admin_credit',f'{user_id}:{cents}')
    await ctx.send(f'Credited ${cents/100:.2f} to {user_id}. Ref `{ref}`.')

@bot.command(name='ordersearch')
async def order_search(ctx,order_id:int):
    if not support(ctx.author):return
    async with db.pool.acquire() as conn:r=await conn.fetchrow('select * from orders where id=$1',order_id)
    await ctx.send(view=cv2_layout(emb('Order lookup',f'Order #{r["id"]}\nBuyer: `{r["discord_id"]}`\nStatus: {r["status"]}\nService: {r["service_id"]}\nQuantity: {r["quantity"]}\nCharged: ${r["cost_cents"]/100:.2f}' if r else 'Order not found.'), None))

@bot.command(name='syncservices')
async def sync_services(ctx):
    if not owner(ctx.author):
        return
    async with ctx.typing():
        try:
            count = await refresh_service_catalog()
        except provider.ProviderError as exc:
            log.warning('Manual catalog sync failed: %s', exc)
            return await ctx.send(f'Service sync failed: {exc}')
        except Exception:
            log.exception('Unexpected catalog sync failure')
            return await ctx.send('Service sync failed. Check the bot console for details.')
    await ctx.send(f'Synced {count} services from Top4SMM.')

@bot.command(name='giveaway')
@commands.guild_only()
async def giveaway_command(ctx, prize: str, duration: str, winners: int):
    """Owner-only: ;giveaway "Prize name" <t:UNIX:F> 2 (also accepts 1h)."""
    if not owner(ctx.author):
        return
    try:
        seconds = parse_duration(duration)
        giveaway_msg = await giveaway_manager.create(ctx.channel, ctx.author.id, prize, seconds, winners)
    except ValueError as exc:
        return await ctx.send(str(exc), delete_after=20)
    except (discord.HTTPException, asyncpg.PostgresError):
        log.exception('Failed creating giveaway for %s', ctx.author.id)
        return await ctx.send('Unable to create the giveaway. Check permissions and the giveaways schema.', delete_after=20)
    await ctx.send(f'Giveaway started: {giveaway_msg.jump_url}', delete_after=15)


@bot.event
async def on_raw_reaction_add(payload):
    try:
        await giveaway_manager.on_reaction(payload, True)
    except Exception:
        log.exception('Could not record giveaway reaction add')


@bot.event
async def on_raw_reaction_remove(payload):
    try:
        await giveaway_manager.on_reaction(payload, False)
    except Exception:
        log.exception('Could not record giveaway reaction remove')




@bot.command(name='clear')
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True, read_message_history=True)
async def clear_messages(ctx, amount: int):
    """Delete the N most recent messages before this command, plus the command itself."""
    if not 1 <= amount <= 100:
        return await ctx.send('Usage: `;clear 1-100` (number of messages to delete).', delete_after=8)
    try:
        # Channel history is explicitly bounded before the invoking message.
        # This ensures the requested count excludes the ;clear command itself.
        messages = [m async for m in ctx.channel.history(limit=amount, before=ctx.message)]
        if messages:
            await ctx.channel.delete_messages(messages) if len(messages) > 1 else await messages[0].delete()
        await ctx.message.delete()
    except discord.Forbidden:
        return await ctx.send('I need Manage Messages and Read Message History permissions.', delete_after=8)
    except discord.HTTPException:
        return await ctx.send('Could not delete some messages. Messages older than 14 days cannot be bulk-deleted.', delete_after=8)


@bot.command(name='kick')
@commands.guild_only()
@commands.has_guild_permissions(kick_members=True)
@commands.bot_has_guild_permissions(kick_members=True)
async def kick_members_command(ctx, *, target: str):
    """`:kick 5` removes newest eligible joins; `;kick Display Name` matches exact display names."""
    guild = ctx.guild
    me = guild.me or guild.get_member(bot.user.id)
    if me is None:
        return await ctx.send('Could not verify my server role. No members were kicked.')
    def eligible(member):
        return (member.id not in (ctx.author.id, bot.user.id, guild.owner_id)
                and member.top_role < ctx.author.top_role
                and member.top_role < me.top_role)
    if ctx.prefix == ':':
        if not target.isdecimal():
            return await ctx.send('Usage: `:kick 5` (number of most recent members).')
        amount = int(target)
        if not 1 <= amount <= 100:
            return await ctx.send('Choose a number between 1 and 100.')
    else:
        if not target.strip() or len(target) > 100:
            return await ctx.send('Usage: `;kick Exact Display Name`')
    # fetch_members retrieves the full member list; cache alone can miss recent joins.
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except discord.HTTPException:
        return await ctx.send('Could not fetch server members. Check the Server Members intent.')
    if ctx.prefix == ':':
        # Choose by join time BEFORE permission filtering: never silently replace
        # a protected recent member with an older one to reach the count.
        selected = sorted((m for m in members if m.joined_at is not None and not m.bot),
                          key=lambda m: m.joined_at, reverse=True)[:amount]
    else:
        selected = [m for m in members if m.display_name.casefold() == target.strip().casefold()]
        if len(selected) > 100:
            return await ctx.send('More than 100 members match that name; no members were kicked.')
    kicked = 0
    skipped = 0
    failed = 0
    for member in selected:
        if not eligible(member):
            skipped += 1
            continue
        try:
            await member.kick(reason=f'Moderation command by {ctx.author} ({ctx.author.id})')
            kicked += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
    await ctx.send(f'Kick complete: **{kicked}** kicked, **{skipped}** protected/skipped, '
                   f'**{failed}** failed. Matches considered: **{len(selected)}**.')


# Owner configuration commands and public announcement panels.
class RestockRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Claim role', style=discord.ButtonStyle.secondary,
                       custom_id='saphire:restock:claim')
    async def claim(self, inter: discord.Interaction, button: discord.ui.Button):
        if not inter.guild or not isinstance(inter.user, discord.Member):
            return await inter.response.send_message('Use this in the Saphire server.', ephemeral=True)
        if settings.discord_guild_id and inter.guild.id != settings.discord_guild_id:
            return await inter.response.send_message('This panel is only available in the official server.', ephemeral=True)
        if not settings.restock_role_id:
            return await inter.response.send_message('The restock role is not configured.', ephemeral=True)
        target = inter.guild.get_role(settings.restock_role_id)
        if target is None:
            return await inter.response.send_message('The configured restock role was not found.', ephemeral=True)
        if target in inter.user.roles:
            return await inter.response.send_message('You already have the Restock Alerts role.', ephemeral=True)
        me = inter.guild.me or inter.guild.get_member(bot.user.id)
        if me is None or not me.guild_permissions.manage_roles or target >= me.top_role or target.managed:
            return await inter.response.send_message('I cannot assign this role. Check my Manage Roles permission and role position.', ephemeral=True)
        try:
            await inter.user.add_roles(target, reason='Saphire restock alert opt-in')
        except (discord.Forbidden, discord.HTTPException):
            log.exception('Failed to grant restock role to %s', inter.user.id)
            return await inter.response.send_message('Could not assign your role. Please contact staff.', ephemeral=True)
        await inter.response.send_message('You now have the Restock Alerts role. You will be eligible for restock notifications.', ephemeral=True)


def owner_panel_context(ctx):
    return (ctx.guild is not None and owner(ctx.author)
            and (not settings.discord_guild_id or ctx.guild.id == settings.discord_guild_id))


@bot.command(name='stopsearch')
@commands.guild_only()
async def stopsearch_command(ctx):
    """Owner: stop both automatic stock and service catalogue refresh jobs until restart."""
    if not owner_panel_context(ctx):
        return
    stopped = []
    for task, label in ((service_catalog_maintenance, 'service catalogue'),
                        (discord_member_stock_maintenance, 'Discord member stock')):
        if task.is_running():
            task.cancel()
            stopped.append(label)
    log.info('Automatic refresh disabled by owner %s; stopped: %s', ctx.author.id, ', '.join(stopped) or 'none')
    await ctx.send(view=cv2_layout(emb('Automatic refresh stopped',
        ('Stopped: **' + ', '.join(stopped) + '**.' if stopped else 'Both automatic refresh jobs are already stopped.')
        + '\n\nManual `;syncservices` still works. Automatic refresh resumes after the bot is restarted.')))


@bot.group(name='restock', invoke_without_command=True)
@commands.guild_only()
async def restock_command(ctx):
    """Owner: `;restock panel` or `;restock panel role`."""
    if owner_panel_context(ctx):
        await ctx.send('Usage: `;restock panel` or `;restock panel role`')


@restock_command.group(name='panel', invoke_without_command=True)
async def restock_panel_command(ctx):
    """Owner: post an automatically updating member-stock panel."""
    if not owner_panel_context(ctx):
        return
    content = await _restock_panel_content()
    message = await ctx.send(view=cv2_layout(emb('Discord members stock', content)))
    refs = _restock_refs()
    ref = [message.channel.id, message.id]
    if ref not in refs:
        refs.append(ref)
        try:
            _save_restock_refs()
        except OSError:
            log.exception('Unable to save restock panel ID: updates will stop after bot restart')


@restock_panel_command.command(name='role')
async def restock_role_panel_command(ctx):
    """Owner: post the persistent Restock Alerts role-claim button."""
    if not owner_panel_context(ctx):
        return
    if not settings.restock_role_id or not ctx.guild.get_role(settings.restock_role_id):
        return await ctx.send('Set `RESTOCK_ROLE_ID` in `.env` to the server Restock role ID first.')
    await ctx.send(view=cv2_layout(emb('Restocks notifications.',
        'Get notified whenever new products are restocked. Click the button below to receive '
        'the Restock Alerts role and stay up to date with the latest availability.'), RestockRoleView()))


@verify_command.command(name='panel')
@commands.guild_only()
async def verify_panel_command(ctx):
    """Owner: post a verification link panel using VERIFY_PANEL_URL."""
    if not owner_panel_context(ctx):
        return
    from urllib.parse import urlsplit
    url = settings.verify_panel_url.strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        return await ctx.send('Set `VERIFY_PANEL_URL` in `.env` to a valid HTTPS link first.')
    link_view = discord.ui.View(timeout=None)
    link_view.add_item(discord.ui.Button(label='Verify now', url=url, style=discord.ButtonStyle.link))
    await ctx.send(view=cv2_layout(emb('Account verification',
        'Verify your account by clicking the button below and following the instructions.'), link_view))


@bot.command(name='help')
async def help_command(ctx):
    """List every registered command and slash command, with access hints."""
    owner_names = {'orderpanel','stockpanel','profilepanel','adminpanel',
                   'ticketcreate','panelcreate','syncservices','giveaway','discordcredits',
                   'hide','unhide','delete','stopsearch','restock','verify panel'}
    staff_names = {'override','topup','ordersearch','user'}
    groups = {'Accounts': [], 'Orders & stock': [], 'Top-ups': [],
              'Support & administration': [], 'Other': []}
    for command in sorted(bot.walk_commands(), key=lambda c: c.qualified_name):
        if command.hidden:
            continue
        name = command.qualified_name
        first = name.split()[0]
        if first in ('register','verify','balance','profile'):
            section = 'Accounts'
        elif first in ('order','orderhistory','orderstatus','orderpanel', 'services',
                       'stockpanel','discord','discordcredits', 'discord', 'discord order', 'discord order test'):
            section = 'Orders & stock'
        elif first in ('topup','topuphistory','override','panelcreate'):
            section = 'Top-ups'
        elif first in ('ticketcreate','user','admin','adminpanel','ordersearch',
                       'syncservices','giveaway','hide','unhide','delete','stopsearch','restock'):
            section = 'Support & administration'
        else:
            section = 'Other'
        required = ' · owner' if first in owner_names else ' · staff' if first in staff_names else ''
        summary = (command.help or '').strip().split('\n')[0].strip()
        desc = f' — {summary[:90]}' if summary else ''
        groups[section].append(f'`;{name}`{required}{desc}')
    text = ['All available prefix commands and slash commands. Owner/staff functions are labelled.']
    for group, items in groups.items():
        if items:
            text.extend(['', f'**{group}**', *items])
    slash = sorted({f'/{c.name}' for c in bot.tree.get_commands()})
    if slash:
        text.extend(['', '**Slash commands**', ' · '.join(f'`{c}`' for c in slash)])
    await ctx.send(view=cv2_layout(emb('Saphire · Command directory', '\n'.join(text))))

@bot.event
async def on_command_error(ctx,error):
    if isinstance(error,(commands.CommandNotFound, commands.CheckFailure)):
        return
    if isinstance(error,commands.MissingRequiredArgument):return await ctx.send(f'Missing argument: `{error.param.name}`')
    log.exception('Command failed: %s',type(error).__name__)
    await ctx.send(view=cv2_layout(emb('Error','Could not complete this request. Please contact support.'), None))

# Retire legacy wallet/payment commands without removing historical financial records.
for _legacy in ('balance','topup','topuphistory'):
    bot.remove_command(_legacy)
bot.tree.remove_command('topuphistory')

async def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    # BlockCypher credentials travel in URL query parameters; never log the URL.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    await db.start()
    try:
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        await db.stop()

if __name__=='__main__':asyncio.run(main())
