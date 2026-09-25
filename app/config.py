from functools import lru_cache

from pydantic import field_validator
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
    paymongo_methods: str = "qrph"   # shown on PayMongo checkout; must be activated on your account (live mode: QR Ph only for now)

    @property
    def paymongo_method_list(self) -> list[str]:
        return [m.strip() for m in self.paymongo_methods.split(",") if m.strip()]

    usd_to_php: float = 62.0          # fallback only; live rate comes from app/fx.py
    fx_buffer_pct: float = 2.0        # added on top of the live rate for pricing
    topup_min_php: int = 100
    topup_max_php: int = 50000

    sync_interval_seconds: int = 180
    sync_enabled: bool = True
    refill_ignore_after_days: int = 5

    @field_validator("base_url", "frontend_origin")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        # CORS compares origins exactly: "https://x.com/" != "https://x.com"
        return v.strip().rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
