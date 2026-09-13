"""Application settings with safe Phase 1 defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Central config. Defaults enforce paper / non-live mode."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mode: str = Field(default="PAPER", description="Operating mode (PAPER only in Phase 1)")
    live_trading: bool = Field(default=False, description="Must remain False in Phase 1")
    database_url: str = Field(
        default="sqlite:///./data/lab.db",
        description="SQLAlchemy database URL",
    )
    log_level: str = Field(default="INFO", description="Logging level")
    # NoDecode: allow comma-separated SYMBOLS=BTCUSDT,ETHUSDT (not JSON arrays)
    symbols: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
        description="Default market symbols",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: object) -> str:
        if v is None:
            return "PAPER"
        return str(v).strip().upper()

    @field_validator("live_trading", mode="before")
    @classmethod
    def parse_live_trading(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        s = str(v).strip().upper()
        if s in {"1", "TRUE", "YES", "ON"}:
            return True
        if s in {"0", "FALSE", "NO", "OFF", ""}:
            return False
        return False

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, v: object) -> List[str]:
        if v is None:
            return ["BTCUSDT", "ETHUSDT"]
        if isinstance(v, list):
            return [str(s).strip().upper() for s in v if str(s).strip()]
        if isinstance(v, str):
            parts = [p.strip().upper() for p in v.split(",") if p.strip()]
            return parts or ["BTCUSDT", "ETHUSDT"]
        return ["BTCUSDT", "ETHUSDT"]

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v: object) -> str:
        if v is None:
            return "INFO"
        return str(v).strip().upper()


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (reload by clearing cache in tests)."""
    return Settings()

