from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    base_url: str = "http://localhost:8000"          # public URL of this service
    frontend_origin: str = "http://localhost:3000"   # CORS + PayMongo redirect target
    jwt_secret: str
    jwt_ttl_hours: int = 72
    cookie_secure: bool = True

    paymongo_secret_key: str = ""
    paymongo_webhook_secret: str = ""

    usd_to_php: float = 58.0
    topup_min_php: int = 100
    topup_max_php: int = 50000

    sync_interval_seconds: int = 180
    sync_enabled: bool = True
    refill_ignore_after_days: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
