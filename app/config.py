"""Typed application settings with fail-fast validation.

The app reads a single `.env` file. Environment selection happens one step
earlier — the Dockerfile (or a local `make env` helper) copies the right
`.env.{development,staging,production}` template to `.env` at build/setup
time. Templates use `${VAR:-default}` interpolation so real environment
variables (from CI/CD, Secrets Manager, Kubernetes, etc.) always win over
the file default.

In `env=production` any missing external-service credential or an invalid
`public_base_url` raises at startup, not during a call. Non-prod envs keep
defaults tolerant so local runs, CI, and unit tests don't require secrets.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    env: Env = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    public_base_url: str = "http://localhost:8000"
    max_concurrent_calls: int = Field(default=10, ge=1, le=500)

    # Anthropic
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-4-7"
    anthropic_max_tokens: int = Field(default=1024, ge=64, le=8192)
    anthropic_timeout_s: float = Field(default=30.0, gt=0.0)
    anthropic_max_retries: int = Field(default=3, ge=0, le=6)

    # Deepgram
    deepgram_api_key: SecretStr = SecretStr("")
    deepgram_model: str = "nova-3"
    deepgram_language: str = "multi"
    deepgram_endpointing_ms: int = Field(default=300, ge=50, le=2000)

    # ElevenLabs
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_timeout_s: float = Field(default=30.0, gt=0.0)

    # Twilio
    twilio_account_sid: SecretStr = SecretStr("")
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_from_number: str = ""
    twilio_validate_signature: bool = True  # off is opt-in, not default

    # Storage
    database_url: str = "postgresql+asyncpg://voice:voice@db:5432/voice"
    redis_url: str = "redis://redis:6379/0"

    # --- Derived helpers ---

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def twilio_configured(self) -> bool:
        return bool(
            self.twilio_account_sid.get_secret_value()
            and self.twilio_auth_token.get_secret_value()
            and self.twilio_from_number
        )

    @model_validator(mode="after")
    def _validate_public_base_url(self) -> Settings:
        parsed = urlparse(self.public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                f"public_base_url must be an http(s) URL with a host, got {self.public_base_url!r}"
            )
        if self.is_production and parsed.scheme != "https":
            raise ValueError("public_base_url must use https in production")
        return self

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> Settings:
        if not self.is_production:
            return self
        missing: list[str] = []
        if not self.anthropic_api_key.get_secret_value():
            missing.append("ANTHROPIC_API_KEY")
        if not self.deepgram_api_key.get_secret_value():
            missing.append("DEEPGRAM_API_KEY")
        if not self.elevenlabs_api_key.get_secret_value():
            missing.append("ELEVENLABS_API_KEY")
        if not self.twilio_configured:
            missing.append("TWILIO_{ACCOUNT_SID,AUTH_TOKEN,FROM_NUMBER}")
        if missing:
            raise ValueError("production env requires these secrets: " + ", ".join(missing))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
