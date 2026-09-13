"""Application settings with safe Phase 2 defaults (paper / public data only)."""

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

    mode: str = Field(default="PAPER", description="Operating mode (PAPER only)")
    live_trading: bool = Field(default=False, description="Must remain False")
    database_url: str = Field(
        default="sqlite:///./data/lab.db",
        description="SQLAlchemy database URL",
    )
    log_level: str = Field(default="INFO", description="Logging level")
    # NoDecode: allow comma-separated SYMBOLS=BTCUSDT,ETHUSDT (not JSON arrays)
    symbols: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
        description="Default symbols (Binance-style list; Coinbase translates for API only)",
    )

    # --- Phase 2 Data Engine (public market data, no secrets) ---
    primary_provider: str = Field(default="binance", description="Primary data provider")
    fallback_provider: str = Field(default="coinbase", description="Fallback data provider")
    enable_ws: bool = Field(default=True, description="Prefer WebSocket for realtime feeds")
    stale_threshold_seconds: float = Field(
        default=60.0, description="Mark feed/data stale after this age (seconds)"
    )
    max_spread_bps: float = Field(
        default=50.0, description="Reject quotes with spread above this (basis points)"
    )
    clock_skew_ms: int = Field(
        default=5000, description="Allowed event_time ahead of received_at (ms)"
    )
    future_skew_ms: int = Field(
        default=2000, description="Reject event_time > received_at + this skew (ms)"
    )
    binance_rest_base: str = Field(
        default="https://data-api.binance.vision",
        description="Binance public market-data REST host (no key)",
    )
    binance_rest_fallback: str = Field(
        default="https://api.binance.com",
        description="Binance REST fallback host",
    )
    binance_ws_base: str = Field(
        default="wss://data-stream.binance.vision",
        description="Binance public market-data WS host (no key)",
    )
    coinbase_rest_base: str = Field(
        default="https://api.exchange.coinbase.com",
        description="Coinbase Exchange public REST (no key)",
    )
    coinbase_ws_base: str = Field(
        default="wss://ws-feed.exchange.coinbase.com",
        description="Coinbase Exchange public WS (no key)",
    )
    http_timeout_seconds: float = Field(default=15.0, description="HTTP request timeout")
    http_max_retries: int = Field(default=4, description="HTTP retries on transient errors")
    rest_backoff_base_seconds: float = Field(default=0.5, description="Exponential backoff base")

    # --- Phase 3 research hardening (configurable gates — not universal truth) ---
    research_cost_profile: str = Field(
        default="BASE",
        description="COST_PROFILE_BASE / CONSERVATIVE / STRESS (alias BASE ok)",
    )
    research_min_bars: int = Field(default=200, description="Min bars before ADEQUATE_SAMPLE")
    research_min_trades: int = Field(default=30, description="Min closed trades for sample adequacy")
    research_min_period_bars: int = Field(default=50, description="Min bars spanning research period")
    research_min_oos_bars: int = Field(default=40, description="Min OOS/test bars")
    research_min_wf_windows: int = Field(default=3, description="Min walk-forward windows")

    # --- Phase 4A historical dataset pipeline (public data, safe small defaults) ---
    historical_max_bars: int = Field(
        default=200, description="Default max bars per historical download (small)"
    )
    historical_hard_max_bars: int = Field(
        default=2000, description="Refuse downloads requesting more bars than this (CLI/casual)"
    )
    historical_research_hard_max_bars: int = Field(
        default=30000,
        description=(
            "Elevated hard max for Phase 4B-REAL research downloads only "
            "(~2–3y of 1h bars). Casual CLI still uses historical_hard_max_bars."
        ),
    )
    historical_default_timeframe: str = Field(
        default="1h", description="Default historical download timeframe"
    )
    historical_data_version: str = Field(
        default="4A.1", description="Dataset content schema version"
    )

    def effective_historical_hard_max(self, *, research: bool = False) -> int:
        """Hard max for downloads: research path may use elevated cap."""
        if research:
            return int(self.historical_research_hard_max_bars)
        return int(self.historical_hard_max_bars)

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

    @field_validator("enable_ws", mode="before")
    @classmethod
    def parse_enable_ws(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        s = str(v).strip().upper()
        if s in {"1", "TRUE", "YES", "ON"}:
            return True
        if s in {"0", "FALSE", "NO", "OFF", ""}:
            return False
        return True

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

    @field_validator("primary_provider", "fallback_provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: object) -> str:
        if v is None:
            return "binance"
        return str(v).strip().lower()

    @field_validator("research_cost_profile", mode="before")
    @classmethod
    def normalize_cost_profile(cls, v: object) -> str:
        if v is None:
            return "BASE"
        return str(v).strip().upper()

    def sample_thresholds(self) -> "SampleThresholds":
        from crypto_lab.backtest.evidence import SampleThresholds

        return SampleThresholds(
            min_bars=int(self.research_min_bars),
            min_trades=int(self.research_min_trades),
            min_period_bars=int(self.research_min_period_bars),
            min_oos_bars=int(self.research_min_oos_bars),
            min_wf_windows=int(self.research_min_wf_windows),
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (reload by clearing cache in tests)."""
    return Settings()
