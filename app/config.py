from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.currency import SUPPORTED_CURRENCIES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str
    jwt_secret: SecretStr
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30
    verify_token_ttl_hours: int = 24
    reset_token_ttl_minutes: int = 30
    email_change_ttl_hours: int = 24
    idempotency_ttl_hours: int = 24
    require_email_verification: bool = True
    default_currency: str = "EUR"
    frontend_url: str = "http://localhost:3000"
    cookie_secure: bool = True
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60
    cleanup_interval_seconds: int = 600
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender: str = "no-reply@example.com"
    smtp_starttls: bool = True

    @field_validator("default_currency")
    @classmethod
    def check_currency(cls, value: str) -> str:
        if value not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported DEFAULT_CURRENCY: {value}")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def check_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
