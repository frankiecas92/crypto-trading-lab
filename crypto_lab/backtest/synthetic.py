"""Synthetic datasets with known expected math (reality tests + demo)."""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import List

from crypto_lab.backtest.types import Bar


def _times(n: int, *, timeframe: str, start: datetime | None = None) -> List[datetime]:
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }[timeframe]
    return [start + i * step for i in range(n)]


def _bar(symbol: str, ts: datetime, px: float, *, tf: str, vol: float = 1.0, source: str = "synthetic") -> Bar:
    return Bar(
        symbol=symbol,
        event_time=ts,
        timeframe=tf,
        open=px,
        high=px,
        low=px,
        close=px,
        volume=vol,
        source=source,
    )


def flat_price(
    n: int = 64,
    *,
    price: float = 100.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
) -> List[Bar]:
    times = _times(n, timeframe=timeframe)
    return [_bar(symbol, t, price, tf=timeframe) for t in times]


def perfect_up(
    n: int = 64,
    *,
    start: float = 100.0,
    step: float = 1.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
) -> List[Bar]:
    times = _times(n, timeframe=timeframe)
    out: List[Bar] = []
    for i, t in enumerate(times):
        o = start + i * step
        c = start + (i + 1) * step
        # close > open; high/low = range
        out.append(
            Bar(
                symbol=symbol,
                event_time=t,
                timeframe=timeframe,
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                volume=1.0,
                source="synthetic",
            )
        )
    return out


def perfect_down(
    n: int = 64,
    *,
    start: float = 100.0,
    step: float = 1.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
) -> List[Bar]:
    times = _times(n, timeframe=timeframe)
    out: List[Bar] = []
    for i, t in enumerate(times):
        o = start - i * step
        c = start - (i + 1) * step
        out.append(
            Bar(
                symbol=symbol,
                event_time=t,
                timeframe=timeframe,
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                volume=1.0,
                source="synthetic",
            )
        )
    return out


def noisy_trend(
    n: int = 200,
    *,
    start: float = 100.0,
    drift: float = 0.001,
    vol: float = 0.01,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    seed: int = 7,
) -> List[Bar]:
    rng = random.Random(seed)
    times = _times(n, timeframe=timeframe)
    px = start
    out: List[Bar] = []
    for t in times:
        o = px
        ret = drift + rng.gauss(0.0, vol)
        c = max(0.01, o * (1.0 + ret))
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol / 3)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol / 3)))
        lo = max(0.01, lo)
        out.append(
            Bar(
                symbol=symbol,
                event_time=t,
                timeframe=timeframe,
                open=o,
                high=hi,
                low=lo,
                close=c,
                volume=1.0 + rng.random(),
                source="synthetic",
            )
        )
        px = c
    return out


def dataset_version(bars: List[Bar]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(
            f"{b.symbol}|{b.event_time.isoformat()}|{b.open}|{b.high}|{b.low}|{b.close}|{b.volume}".encode()
        )
    return h.hexdigest()[:16]
