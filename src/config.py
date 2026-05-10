"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Settings for the Telegram → Bale mirror."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tg_api_id: int = Field(..., description="Telegram api_id from my.telegram.org")
    tg_api_hash: str = Field(..., min_length=1)
    tg_session_name: str = Field(default="tg-bale-mirror")
    tg_session_string: str | None = Field(
        default=None,
        description="Pre-generated session string (preferred for headless/VPS); "
        "if unset, pyrofork falls back to interactive login + .session file",
    )
    tg_bot_token: str | None = Field(
        default=None,
        description="Telegram bot token from @BotFather. When set, the listener "
        "runs in bot mode (preferred); the bot must be admin of the source "
        "channel to receive channel_post updates.",
    )
    tg_source_channel: int | str = Field(
        ...,
        description="Numeric channel ID (preferred) or @username",
    )

    bale_bot_token: str = Field(..., min_length=1)
    bale_channel_id: int | str = Field(...)

    temp_media_dir: Path = Field(default=Path("./tmp"))
    log_level: str = Field(default="INFO")

    @field_validator("tg_source_channel", mode="before")
    @classmethod
    def _coerce_source_channel(cls, v: object) -> int | str:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("@"):
                return s
            try:
                return int(s)
            except ValueError:
                return s
        raise TypeError(f"tg_source_channel must be int or str, got {type(v).__name__}")

    @field_validator("bale_channel_id", mode="before")
    @classmethod
    def _coerce_bale_channel(cls, v: object) -> int | str:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("@"):
                return s
            try:
                return int(s)
            except ValueError:
                return s
        raise TypeError(f"bale_channel_id must be int or str, got {type(v).__name__}")
