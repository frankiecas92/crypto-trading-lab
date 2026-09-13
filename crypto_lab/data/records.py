"""Canonical market data records for the Data Engine.

EVENT TIME vs RECEIVED TIME
- event_time: exchange-reported event timestamp (UTC)
- received_at: local wall-clock when this process received the payload (UTC)

Never use event_time that is in the future relative to received_at (plus skew).

Symbol identity (venue-native):
- source: venue (binance | coinbase)
- symbol / source_symbol: venue-native market id (BTCUSDT, BTC-USD, …)
- base_asset / quote_asset: pair legs
- canonical_asset: asset-level ONLY (BTC | ETH) — not same-market across venues
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CanonicalCandle:
    """Normalized OHLCV candle."""

    source: str
    symbol: str  # venue-native e.g. BTCUSDT or BTC-USD
    event_time: datetime
    received_at: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    canonical_asset: str | None = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class CanonicalTrade:
    """Normalized public trade / match."""

    source: str
    symbol: str
    event_time: datetime
    received_at: datetime
    price: float
    quantity: float
    trade_id: str | None = None
    side: str | None = None  # BUY / SELL if known
    source_symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    canonical_asset: str | None = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class CanonicalQuote:
    """Normalized top-of-book / ticker quote."""

    source: str
    symbol: str
    event_time: datetime
    received_at: datetime
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume_24h: float | None = None
    source_symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    canonical_asset: str | None = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float | None:
        sp = self.spread
        if sp is None or self.bid is None or self.bid <= 0:
            return None
        mid = (self.bid + (self.ask or self.bid)) / 2.0
        if mid <= 0:
            return None
        return (sp / mid) * 10_000.0


MarketRecord = CanonicalCandle | CanonicalTrade | CanonicalQuote


def ms_to_datetime(ms: int | float) -> datetime:
    """Convert exchange millisecond epoch to aware UTC datetime."""
    from datetime import timezone

    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def datetime_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
