"""Private ;discord order test checkout for Discorium member delivery.

Keep delivery access keys private; do not log HTTP Authorization or response bodies.
Uncertain provider outcomes remain reserved for staff reconciliation.
"""
import logging
import re
import secrets
import string
from decimal import Decimal

import discord
import httpx

from . import discord_members

log = logging.getLogger('saphire.discord_delivery')
DELIVERY_URL = 'https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members'


async def request_delivery(api_key: str, order_id: str, kind: str, quantity: int) -> dict:
    """POST the v2 delivery contract. Log diagnostic metadata, never tokens or keys."""
    if kind not in ('online', 'offline') or not isinstance(quantity, int) or not 1 <= quantity <= 100000:
        raise ValueError('Invalid delivery member type or quantity.')
    if not api_key.strip():
        raise ValueError('Discord delivery API key is not configured.')
    if not re.fullmatch(r'dsc-[A-Za-z0-9]{6,12}', order_id):
        raise ValueError('Invalid delivery order ID format.')
    payload = {'order_id': order_id, 'members_type': kind, 'quantity': quantity}
    log.info('DELIVERY TEST request order=%s method=POST type=%s quantity=%s endpoint=/api/v2/reseller/custom/delivery/members',
             order_id, kind, quantity)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25), follow_redirects=False) as client:
            response = await client.post(
                DELIVERY_URL,
                headers={'Authorization': f'Bearer {api_key.strip()}', 'Accept': 'application/json',
                         'Content-Type': 'application/json'},
                json=payload,
            )
    except httpx.RequestError as exc:
        log.warning('DELIVERY TEST network failure order=%s exception=%s (outcome uncertain)',
                    order_id, type(exc).__name__)
        raise RuntimeError('Delivery network error; provider status must be reconciled.') from None
    log.info('DELIVERY TEST response order=%s http_status=%d', order_id, response.status_code)
    try:
        data = response.json()
    except ValueError:
        log.warning('DELIVERY TEST invalid JSON order=%s http_status=%s', order_id, response.status_code)
        raise RuntimeError('Delivery provider returned invalid JSON; staff review required.') from None
    if not isinstance(data, dict):
        raise RuntimeError('Delivery response must be a JSON object; staff review required.')
    if data.get('success') is False:
        # A provider-controlled error can contain user data: truncate and redact.
        reason = _safe_provider_error(data.get('error'))
        log.warning('DELIVERY TEST provider rejected order=%s http_status=%s reason=%s',
                    order_id, response.status_code, reason)
        raise DeliveryRejected(reason)
    if response.status_code != 200 or data.get('success') is not True:
        log.warning('DELIVERY TEST unexpected provider result order=%s http_status=%s success=%s',
                    order_id, response.status_code, data.get('success'))
        raise RuntimeError('Delivery provider outcome unknown; staff review required.')
    echoed_type = data.get('members_type')
    echoed_quantity = data.get('quantity')
    if (str(data.get('order_id')) != order_id or echoed_type != kind
            or type(echoed_quantity) is not int or echoed_quantity != quantity):
        log.error('DELIVERY TEST response mismatch order=%s: order/type/quantity did not match (redacted)', order_id)
        raise RuntimeError('Delivery confirmation mismatch; staff review required.')
    key = data.get('key')
    if not isinstance(key, str) or not key.strip() or len(key) > 2048:
        raise RuntimeError('Delivery response missing valid key; staff review required.')
    credits = data.get('credits_remaining')
    if type(credits) is not int or credits < 0:
        raise RuntimeError('Delivery response missing valid credits_remaining; staff review required.')
    log.info('DELIVERY TEST accepted order=%s members_type=%s quantity=%d credits_remaining=%d key_received=yes',
             order_id, kind, quantity, credits)
    return {'key': key.strip(), 'credits_left': Decimal(credits),
            'credits_remaining': credits, 'order_id': order_id,
            'members_type': kind, 'quantity': quantity}


class DeliveryRejected(Exception):
    """Provider explicitly rejected the order (not an unconfirmed timeout)."""


def _safe_provider_error(value):
    message = str(value or 'Unspecified provider error')[:160]
    # Never expose tokens or accidental returned credentials in console/errors.
    message = re.sub(r'dsc_live_[A-Za-z0-9]+', '[REDACTED]', message)
    message = re.sub(r'(?i)(bearer\s+)[^\s]+', r'\1[REDACTED]', message)
    return message.replace('\n', ' ').replace('\r', ' ')


class QuantityModal(discord.ui.Modal, title='Discord member quantity'):
    def __init__(self, wizard):
        super().__init__(timeout=300)
        self.wizard = wizard
        self.qty = discord.ui.TextInput(label='Number of members', placeholder='1000', max_length=6)
        self.add_item(self.qty)

    async def on_submit(self, inter):
        if inter.user.id != self.wizard.user_id:
            return await inter.response.send_message('This order belongs to another user.', ephemeral=True)
        try:
            count = int(str(self.qty.value).strip())
            # Validate all price, quantity and member selection before checkout.
            if self.wizard.kind is None:
                raise ValueError('Choose Online or Offline first.')
            discord_members.price_cents(self.wizard.kind, count)
        except (ValueError, TypeError) as exc:
            return await inter.response.send_message(str(exc), ephemeral=True)
        self.wizard.quantity = count
        await inter.response.edit_message(view=self.wizard.layout())


class DeliveryButton(discord.ui.Button):
    def __init__(self, wizard, action, label, *, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.wizard, self.action = wizard, action

    async def callback(self, inter):
        await self.wizard.handle(inter, self.action)


class DeliveryWizard(discord.ui.View):
    def __init__(self, user_id, bot, db, settings, emb, cv2_layout):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.bot, self.db, self.settings = bot, db, settings
        self.emb, self.cv2_layout = emb, cv2_layout
        self.kind = None
        self.quantity = None
        self.working = False

    def layout(self, title=None, message=None, *, final=False):
        if final:
            self.clear_items()
            return self.cv2_layout(self.emb(title, message))
        self.clear_items()
        rate = discord_members.RATES.get(self.kind)
        count = self.quantity
        amount = discord_members.price_cents(self.kind, count) if rate is not None and count else None
        description = ('**Choose a member type and quantity, then place the order.**\n\n'
                       f'**Type:** {self.kind.title() if self.kind else "Not selected"}\n'
                       f'**Quantity:** {count:,}' + '\n' if count is not None else
                       '**Choose a member type and quantity, then place the order.**\n\n'
                       f'**Type:** {self.kind.title() if self.kind else "Not selected"}\n'
                       '**Quantity:** Not selected\n')
        description += (f'**Rate:** ${rate} per 1,000\n' if rate is not None else
                        '**Online:** $1.10/1,000 · **Offline:** $0.80/1,000\n')
        description += f'**Total:** ${amount/100:.2f}\n' if amount is not None else '**Total:** Select both options\n'
        description += '\nThe order is charged from your Saphire balance only when you press **Order**.'
        self.add_item(DeliveryButton(self, 'online', 'Online', style=discord.ButtonStyle.primary))
        self.add_item(DeliveryButton(self, 'offline', 'Offline', style=discord.ButtonStyle.primary))
        self.add_item(DeliveryButton(self, 'quantity', 'Quantity'))
        self.add_item(DeliveryButton(self, 'order', 'Order', style=discord.ButtonStyle.success,
                                     disabled=not (self.kind and self.quantity) or self.working))
        return self.cv2_layout(self.emb('Discord member delivery · Test', description), self)

    async def handle(self, inter, action):
        if inter.user.id != self.user_id:
            return await inter.response.send_message('This order belongs to another user.', ephemeral=True)
        if self.working:
            return await inter.response.send_message('Order is already processing.', ephemeral=True)
        if action in ('online', 'offline'):
            self.kind = action
            return await inter.response.edit_message(view=self.layout())
        if action == 'quantity':
            return await inter.response.send_modal(QuantityModal(self))
        if action != 'order' or not self.kind or not self.quantity:
            return await inter.response.send_message('Select type and quantity first.', ephemeral=True)
        if not self.settings.discord_delivery_api_key.strip():
            return await inter.response.send_message('Delivery API key is not configured. Ask staff.', ephemeral=True)
        self.working = True
        await inter.response.edit_message(view=self.cv2_layout(self.emb(
            'Processing member order', 'Reserving your balance and contacting the delivery provider. Please wait.')))
        # Reuse atomic account lock, ledger, and order reservation. No invite URL is
        # needed by this API. The sentinel identifies the new delivery API workflow.
        try:
            order_id, cents = await discord_members.reserve_order(
                self.db, self.user_id, self.kind, self.quantity, 'delivery-api:test')
        except ValueError as exc:
            self.working = False
            return await inter.edit_original_response(view=self.cv2_layout(self.emb(
                'Order not placed', str(exc))))
        except Exception:
            log.exception('Could not reserve delivery member order')
            self.working = False
            return await inter.edit_original_response(view=self.cv2_layout(self.emb(
                'Order not placed', 'Could not reserve balance. Contact support.')))
        try:
            provider_id = 'dsc-' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
            log.info('DELIVERY TEST reserved internal_order=%s provider_order=%s user=%s type=%s quantity=%s charged_cents=%s',
                     order_id, provider_id, self.user_id, self.kind, self.quantity, cents)
            result = await request_delivery(self.settings.discord_delivery_api_key,
                                            provider_id, self.kind, self.quantity)
            if not await discord_members.mark_provider_success(self.db, order_id, result):
                raise RuntimeError('Order acceptance could not be recorded; staff review required.')
        except DeliveryRejected as exc:
            # The provider explicitly said success=false. Preserve the debit for
            # reconciliation; never assume a refund is safe without checking.
            log.warning('DELIVERY TEST rejected internal_order=%s reason=%s; staff review required', order_id, exc)
            await discord_members.mark_needs_review(self.db, order_id)
            return await inter.edit_original_response(view=self.cv2_layout(self.emb(
                'Delivery order rejected', f'**Order:** `{order_id}`\n'
                f'**Provider reason:** {discord.utils.escape_markdown(str(exc))}\n'
                'Your reserved balance needs staff review before retrying.')))
        except Exception:
            # Unknown provider outcome must never be refunded or retried automatically.
            log.exception('Delivery member order %s requires reconciliation', order_id)
            try:
                await discord_members.mark_needs_review(self.db, order_id)
            except Exception:
                log.exception('Could not mark delivery order %s for review', order_id)
            return await inter.edit_original_response(view=self.cv2_layout(self.emb(
                'Order requires staff review', f'**Order:** `{order_id}`\n'
                'Your balance was reserved, but provider acceptance could not be verified. '
                'Contact support before retrying.')))
        try:
            await discord_members.notify_logs(self.bot, self.settings, self.db,
                order_id, self.user_id, self.kind, self.quantity, cents, result['credits_left'])
        except Exception:
            log.exception('Delivery order %s succeeded but transaction log failed', order_id)
        log.info('DISCORD DELIVERY SUCCESS internal_order=%s provider_order=%s member_type=%s quantity=%s charged_cents=%s credits_remaining=%s user=%s',
                 order_id, result['order_id'], self.kind, self.quantity, cents, result['credits_remaining'], self.user_id)
        key = discord.utils.escape_markdown(result['key']).replace('`', 'ˋ')
        # DM-only: access key is never posted in a public channel or transaction log.
        return await inter.edit_original_response(view=self.cv2_layout(self.emb(
            'Discord member order successful',
            f'**Order ID:** `{result["order_id"]}`\n**Type:** {self.kind.title()}\n'
            f'**Quantity:** {self.quantity:,}\n**Charged:** ${cents/100:.2f}\n'
            f'**Delivery key:** `{key}`\n\nKeep this key private.')))
