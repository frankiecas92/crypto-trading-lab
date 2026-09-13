"""Phase 4A historical pipeline constants (safe defaults, no trading claims)."""

from __future__ import annotations

DATA_VERSION = "4A.1"
DEFAULT_MAX_BARS = 200
HARD_MAX_BARS = 2000
DEFAULT_TIMEFRAME = "1h"
DEFAULT_RANGE_DAYS = 3
PAGE_SLEEP_SECONDS = 0.05

# Provider page sizes (public REST limits)
BINANCE_PAGE_LIMIT = 1000
COINBASE_PAGE_LIMIT = 300

SUPPORTED_SOURCES = ("binance", "coinbase")
SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")
RESAMPLE_TARGETS = ("5m", "15m", "1h", "4h")

QUALITY_VALID = "VALID"
QUALITY_WARN = "VALID_WITH_WARNINGS"
QUALITY_INVALID = "INVALID"

CRITICAL_RULES = frozenset(
    {
        "invalid_ohlc",
        "impossible_price",
        "invalid_volume",
        "future_timestamp",
        "invalid_symbol",
        "duplicate_candle",
        "inconsistent_timestamps",
        "negative_price",
    }
)

# Expected for historical backfill (event_time << received_at). Not dataset-critical.
HISTORICAL_IGNORED_RULES = frozenset({"delayed_timestamp", "stale_data"})

SPLIT_TRAIN = "TRAIN"
SPLIT_VALIDATION = "VALIDATION"
SPLIT_TEST = "TEST"
EVALUATE_OOS = "EVALUATE_OOS"
