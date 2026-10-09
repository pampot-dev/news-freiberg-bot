from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    deepl_api_key: str
    admin_ids: Annotated[list[int], NoDecode]

    poll_interval_min: int = Field(30, ge=1)
    quiet_hours: Annotated[tuple[int, int] | None, NoDecode] = (22, 7)
    timezone: str = "Europe/Berlin"
    stale_days: int = Field(7, ge=1)
    db_path: Path = Path("data/bot.db")

    batch_limit: int = Field(5, ge=1)
    contact: str = ""
    glossary_path: Path = Path("glossary.yaml")
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("quiet_hours", mode="before")
    @classmethod
    def _parse_quiet_hours(cls, value: object) -> object:
        """Accepts "22-7"; an empty string disables quiet hours."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            start, sep, end = value.partition("-")
            if not sep:
                raise ValueError("QUIET_HOURS must look like '22-7'")
            return int(start), int(end)
        return value

    @field_validator("quiet_hours")
    @classmethod
    def _check_quiet_hours(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and not all(0 <= hour <= 23 for hour in value):
            raise ValueError("QUIET_HOURS hours must be within 0..23")
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def user_agent(self) -> str:
        suffix = f" (+{self.contact})" if self.contact else ""
        return f"news-freiberg-bot/0.1{suffix}"
