"""Server configuration using Pydantic Settings."""

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")

    # WhatsApp Business API
    whatsapp_verify_token: str = Field(
        default="invoice_agent_verify_token",
        description="Token for webhook verification",
    )
    whatsapp_api_token: Optional[str] = Field(
        default=None,
        description="WhatsApp Business API access token",
    )
    whatsapp_phone_number_id: Optional[str] = Field(
        default=None,
        description="WhatsApp Business phone number ID",
    )
    whatsapp_business_account_id: Optional[str] = Field(
        default=None,
        description="WhatsApp Business Account ID",
    )

    # Meta/Facebook
    meta_app_secret: Optional[str] = Field(
        default=None,
        description="Meta App Secret for signature verification",
    )

    # LLM
    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key for Claude",
    )

    # Database
    database_url: str = Field(
        default="sqlite:///./invoices.db",
        description="Database connection URL",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore unknown fields for backward compatibility


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
