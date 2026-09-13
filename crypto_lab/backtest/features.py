"""Causal feature helpers (no future peeks). Prefer DataView methods.

These operate on already-visible prefixes only.
"""

from __future__ import annotations

from typing import List, Sequence

from crypto_lab.backtest.data_view import DataView
from crypto_lab.exceptions import LookAheadError


def causal_sma(values: Sequence[float], period: int) -> List[float | None]:
    if period <= 0:
        raise ValueError("period must be > 0")
    out: List[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / float(period)
    return out


def causal_momentum(values: Sequence[float], lookback: int) -> List[float | None]:
    if lookback <= 0:
        raise ValueError("lookback must be > 0")
    out: List[float | None] = [None] * len(values)
    for i in range(len(values)):
        j = i - lookback
        if j < 0 or values[j] == 0:
            continue
        out[i] = values[i] / values[j] - 1.0
    return out


def sma_from_view(view: DataView, period: int, *, field: str = "close") -> float | None:
    return view.sma(period, field=field)


def momentum_from_view(view: DataView, lookback: int, *, field: str = "close") -> float | None:
    return view.momentum(lookback, field=field)


def reject_future_field(name: str) -> None:
    raise LookAheadError(f"UNKNOWN AT T: refused access to future field {name!r}")
