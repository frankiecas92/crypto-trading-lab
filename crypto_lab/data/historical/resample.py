"""Causal resample: 1m → 5m / 15m / 1h / 4h.

Only source bars with event_time <= output candle close are used.
A bar whose event_time equals the next bucket open belongs to that next bucket
(left-closed, right-open). Incomplete trailing buckets are omitted — never filled.

Tests that inject future bars must observe no look-ahead in earlier outputs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Sequence

from crypto_lab.backtest.timeframes import TIMEFRAME_MS, timeframe_ms
from crypto_lab.data.historical.constants import RESAMPLE_TARGETS
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.exceptions import LookAheadError


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _floor_bucket(ts: datetime, tf_ms: int) -> datetime:
    ts = _aware(ts)
    epoch_ms = int(ts.timestamp() * 1000)
    floored = (epoch_ms // tf_ms) * tf_ms
    return datetime.fromtimestamp(floored / 1000.0, tz=timezone.utc)


def resample_causal(
    candles: Sequence[CanonicalCandle],
    target_tf: str,
    *,
    asof: datetime | None = None,
    source_tf: str = "1m",
) -> List[CanonicalCandle]:
    """Aggregate source bars into ``target_tf`` without look-ahead.

    For an output candle with open T and close T+delta, include only bars with
    T <= event_time < T+delta  (hence event_time < close, so event_time <= close
    never includes the next bucket's open). If ``asof`` is set, also require
    event_time <= asof.

    Incomplete last buckets (no bar covering the last source step before close)
    are dropped — we do not invent closes.
    """
    target = target_tf.strip().lower()
    if target not in RESAMPLE_TARGETS and target != source_tf:
        raise ValueError(f"Unsupported resample target {target_tf!r}; allowed={RESAMPLE_TARGETS}")
    if target == source_tf.strip().lower():
        out = list(candles)
        if asof is not None:
            out = [c for c in out if c.event_time <= asof]
        return sorted(out, key=lambda c: c.event_time)

    tf_ms = timeframe_ms(target)
    src_ms = timeframe_ms(source_tf)
    delta = timedelta(milliseconds=tf_ms)
    src_step = timedelta(milliseconds=src_ms)

    buckets: dict[datetime, list[CanonicalCandle]] = defaultdict(list)
    for c in candles:
        et = _aware(c.event_time)
        if asof is not None and et > _aware(asof):
            continue
        open_t = _floor_bucket(et, tf_ms)
        close_t = open_t + delta
        # Causal: only bars with event_time <= output close, and in this bucket.
        if et > close_t:
            raise LookAheadError(
                f"bar event_time {et.isoformat()} is after output close {close_t.isoformat()}"
            )
        if et < open_t or et >= close_t:
            continue
        buckets[open_t].append(c)

    out: List[CanonicalCandle] = []
    expected_src = int(tf_ms // src_ms)
    for open_t in sorted(buckets):
        close_t = open_t + delta
        members = [c for c in buckets[open_t] if c.event_time <= close_t]
        # Reject any leaked future member (defensive)
        leaked = [c for c in members if c.event_time >= close_t]
        if leaked:
            raise LookAheadError("future bar leaked into resample bucket")
        if not members:
            continue
        members.sort(key=lambda c: c.event_time)
        # Incomplete bucket: omit rather than invent
        last_needed = close_t - src_step
        if members[-1].event_time < last_needed:
            continue
        first = members[0]
        received = max(c.received_at for c in members)
        out.append(
            CanonicalCandle(
                source=first.source,
                symbol=first.symbol,
                event_time=open_t,
                received_at=received,
                timeframe=target,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                volume=sum(c.volume for c in members),
                source_symbol=first.source_symbol or first.symbol,
                base_asset=first.base_asset,
                quote_asset=first.quote_asset,
                canonical_asset=first.canonical_asset,
            )
        )
    return out


def assert_no_lookahead(
    source: Sequence[CanonicalCandle],
    resampled: Sequence[CanonicalCandle],
    *,
    target_tf: str,
) -> None:
    """Fail if any output used a source bar after its own close."""
    tf_ms = timeframe_ms(target_tf)
    delta = timedelta(milliseconds=tf_ms)
    by_bucket: dict[datetime, list[CanonicalCandle]] = defaultdict(list)
    for c in source:
        by_bucket[_floor_bucket(c.event_time, tf_ms)].append(c)
    for bar in resampled:
        close_t = bar.event_time + delta
        for src in by_bucket.get(bar.event_time, []):
            if src.event_time >= close_t:
                raise LookAheadError(
                    f"look-ahead: {src.event_time.isoformat()} used in "
                    f"{bar.event_time.isoformat()} {target_tf} (close {close_t.isoformat()})"
                )
            if src.event_time > close_t:
                raise LookAheadError("future source bar in resample")
        # Output OHLC must be explainable by members with event_time < close
        members = [
            s
            for s in source
            if bar.event_time <= s.event_time < close_t
        ]
        if not members:
            continue
        if bar.high > max(m.high for m in members) + 1e-12:
            raise LookAheadError("resample high not explained by causal members")
        if bar.low < min(m.low for m in members) - 1e-12:
            raise LookAheadError("resample low not explained by causal members")
