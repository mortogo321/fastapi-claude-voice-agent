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

    # App
    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"

    # Anthropic
    anthropic_api_key: str = Field(default="", min_length=0)
    anthropic_model: str = "claude-opus-4-7"

    # Deepgram
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    deepgram_language: str = "multi"

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_turbo_v2_5"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Storage
    database_url: str = "postgresql+asyncpg://voice:voice@db:5432/voice"
    redis_url: str = "redis://redis:6379/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
