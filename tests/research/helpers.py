from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from crypto_lab.backtest.types import Bar


def make_bars(
    n: int,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    start_px: float = 100.0,
    path: list[float] | None = None,
    volumes: list[float] | None = None,
    source: str = "synthetic",
) -> List[Bar]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = timedelta(hours=1) if timeframe == "1h" else timedelta(minutes=1)
    out: List[Bar] = []
    for i in range(n):
        px = path[i] if path is not None else start_px
        vol = volumes[i] if volumes is not None else 1.0
        out.append(
            Bar(
                symbol=symbol,
                event_time=t0 + i * step,
                timeframe=timeframe,
                open=px,
                high=px,
                low=px,
                close=px,
                volume=vol,
                source=source,
            )
        )
    return out


def ohlc_path(opens: list[float], closes: list[float], symbol: str = "BTCUSDT") -> List[Bar]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    for i, (o, c) in enumerate(zip(opens, closes)):
        out.append(
            Bar(
                symbol=symbol,
                event_time=t0 + timedelta(hours=i),
                timeframe="1h",
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                volume=1.0,
                source="synthetic",
            )
        )
    return out
