from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    discord_token: str
    discord_client_id: str
    discord_client_secret: str
    database_url: str
    top4smm_api_key: str
    top4smm_api_url: str = "https://top4smm.com/api.php"
    top4smm_action_field: str = "act"
    service_markup_usd: Decimal = Decimal('1.09')
    discord_member_api_base_url: str = ''
    discord_member_api_key: str = ''
    discord_delivery_api_key: str = ''
    discord_member_stock_url: str = 'https://members-api.discorium.cc/api/v2/stock'
    discord_member_owner_log_channel_id: int = 0
    public_base_url: str = 'https://saphire.rip'
    discord_guild_id: int = 0
    owner_ids: str = ''
    support_role_id: int = 0
    founder_role_id: int = 0
    welcome_channels: str = ""
    ticket_category_id: int = 0
    ticket_log_channel_id: int = 0
    transaction_log_channel_id: int = 0
    order_panel_channel_id: int = 0
    topup_panel_channel_id: int = 0
    bot_internal_token: str
    oauth_state_secret: str
    btc_wallet: str = ''
    ltc_wallet: str = ''
    eth_wallet: str = ''
    sol_wallet: str = ''
    payment_category_id: int = 1561934204999180440
    community_manager_role_id: int = 0
    paypal_email: str = ''
    paypal_profile_url: str = ''
    blockcypher_api_token: str = ''
    eth_rpc_url: str = ''
    sol_rpc_url: str = ''
    restock_role_id: int = 0
    verify_panel_url: str = ""
    port: int = 8080

    @property
    def welcome_channel_ids(self):
        return {int(x.strip()) for x in self.welcome_channels.split(',') if x.strip().isdigit()}

    @property
    def owners(self):
        return {int(x.strip()) for x in self.owner_ids.split(',') if x.strip()}

settings = Settings()
