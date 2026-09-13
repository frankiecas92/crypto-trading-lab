"""Supported research timeframes (framework; experiments may use 1h/1m)."""

from __future__ import annotations

SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}

# Approximate bars per year (365.25 d) for annualization when applicable.
BARS_PER_YEAR: dict[str, float] = {
    "1m": 365.25 * 24 * 60,
    "5m": 365.25 * 24 * 12,
    "15m": 365.25 * 24 * 4,
    "1h": 365.25 * 24,
    "4h": 365.25 * 6,
}


def timeframe_ms(tf: str) -> int:
    key = tf.strip().lower()
    if key not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe {tf!r}; supported={SUPPORTED_TIMEFRAMES}")
    return TIMEFRAME_MS[key]


def bars_per_year(tf: str) -> float:
    key = tf.strip().lower()
    if key not in BARS_PER_YEAR:
        raise ValueError(f"Unsupported timeframe {tf!r}; supported={SUPPORTED_TIMEFRAMES}")
    return BARS_PER_YEAR[key]


def latency_ms_to_bars(latency_ms: float, timeframe: str) -> int:
    """Convert latency in ms to whole bars (ceil). 0 ms → 0 extra bars."""
    if latency_ms <= 0:
        return 0
    ms = timeframe_ms(timeframe)
    # ceil
    return int((latency_ms + ms - 1) // ms)
