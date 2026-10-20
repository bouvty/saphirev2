from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env',extra='ignore')
    database_url:str
    discord_client_id:str
    discord_client_secret:str
    bot_internal_token:str
    oauth_state_secret:str
    public_base_url:str='https://saphire.rip'
settings=Settings()
