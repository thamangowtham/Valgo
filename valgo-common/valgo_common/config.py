"""Centralized configuration. Loads from .env locally, AWS Secrets Manager in prod.

Usage:
    from valgo_common.config import settings
    settings.kite_api_key
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    env: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"
    aws_region: str = "ap-south-1"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # DynamoDB
    dynamodb_endpoint: str | None = None       # None = real AWS, set for local
    dynamodb_table_prefix: str = "valgo_local_"

    # Kite Connect
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    kite_enctoken: str = ""        # browser enctoken — alternative to api_key+access_token
    kite_user_id: str = ""
    kite_password: str = ""
    kite_totp_seed: str = ""

    # Fyers
    fyers_app_id: str = ""
    fyers_secret_id: str = ""
    fyers_user_id: str = ""
    fyers_pin: str = ""
    fyers_totp_seed: str = ""

    # Shoonya (Finvasia) — Retail Client API April 2026
    shoonya_user_id: str = ""
    shoonya_vendor_code: str = ""     # Client Id (e.g. FN213657_U)
    shoonya_api_secret: str = ""      # API Key shown once at creation
    shoonya_access_token: str = ""    # OAuth access_token from GenAcsTok

    # Webhook
    tradingview_shared_secret: str = ""

    # Operator notifications (Telegram)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Admin API
    admin_api_port: int = 8080
    admin_api_token: str = "change-me-in-prod"

    # Internal services
    execution_router_url: str = "http://localhost:8090"
    decision_engine_url: str = "http://localhost:8091"

    @property
    def is_local(self) -> bool:
        return self.env == "local"

    @property
    def is_production(self) -> bool:
        return self.env == "prod"

    def table_name(self, table: str) -> str:
        """Prefixed DynamoDB table name."""
        return f"{self.dynamodb_table_prefix}{table}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton for ergonomic imports
settings = get_settings()
