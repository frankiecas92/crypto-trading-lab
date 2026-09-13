"""Content-addressed dataset identity.

dataset_id is derived from source, source_symbol, base, quote, canonical_asset,
timeframe, start, end, record_count, data_version, git_commit (if available),
and checksum of candle content. Different content → different id.

Venue-native: Binance BTCUSDT (USDT) is never the same market as Coinbase BTC-USD (USD).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from crypto_lab.backtest.registry import current_git_commit
from crypto_lab.data.historical.constants import DATA_VERSION
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.symbols import parse_instrument


def _aware_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def candle_checksum(candles: Sequence[CanonicalCandle]) -> str:
    """Stable SHA-256 of ordered OHLCV content (not received_at)."""
    h = hashlib.sha256()
    ordered = sorted(candles, key=lambda c: (c.event_time, c.source, c.symbol, c.timeframe))
    for c in ordered:
        line = (
            f"{c.source}|{c.symbol}|{c.timeframe}|{_aware_iso(c.event_time)}|"
            f"{c.open:.10g}|{c.high:.10g}|{c.low:.10g}|{c.close:.10g}|{c.volume:.10g}\n"
        )
        h.update(line.encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class DatasetIdentity:
    source: str
    source_symbol: str
    base_asset: str
    quote_asset: str
    canonical_asset: str
    timeframe: str
    start: datetime
    end: datetime
    record_count: int
    data_version: str
    checksum: str
    git_commit: str | None = None

    @property
    def dataset_id(self) -> str:
        payload = {
            "source": self.source,
            "source_symbol": self.source_symbol,
            "base": self.base_asset,
            "quote": self.quote_asset,
            "canonical_asset": self.canonical_asset,
            "timeframe": self.timeframe,
            "start": _aware_iso(self.start),
            "end": _aware_iso(self.end),
            "record_count": self.record_count,
            "data_version": self.data_version,
            "git_commit": self.git_commit or "",
            "checksum": self.checksum,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = _aware_iso(self.start)
        d["end"] = _aware_iso(self.end)
        d["dataset_id"] = self.dataset_id
        return d


def compute_dataset_identity(
    candles: Sequence[CanonicalCandle],
    *,
    source: str,
    source_symbol: str,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
    data_version: str = DATA_VERSION,
    git_commit: str | None | object = ...,
) -> DatasetIdentity:
    """Build identity from candles + venue-native instrument fields.

    ``git_commit=...`` (ellipsis) means "look up current HEAD if available".
    Pass ``None`` to omit commit from the hash (deterministic tests).
    """
    inst = parse_instrument(source, source_symbol)
    if git_commit is ...:
        git_commit = current_git_commit()
    checksum = candle_checksum(candles)
    if candles:
        c_start = min(c.event_time for c in candles)
        c_end = max(c.event_time for c in candles)
    else:
        c_start = start
        c_end = end
        if c_start is None or c_end is None:
            raise ValueError("empty candles require explicit start and end")
    return DatasetIdentity(
        source=inst.source,
        source_symbol=inst.source_symbol,
        base_asset=inst.base_asset,
        quote_asset=inst.quote_asset,
        canonical_asset=inst.canonical_asset,
        timeframe=timeframe,
        start=start or c_start,
        end=end or c_end,
        record_count=len(candles),
        data_version=data_version,
        checksum=checksum,
        git_commit=git_commit,  # type: ignore[arg-type]
    )


def identities_differ(a: DatasetIdentity, b: DatasetIdentity) -> bool:
    return a.dataset_id != b.dataset_id
