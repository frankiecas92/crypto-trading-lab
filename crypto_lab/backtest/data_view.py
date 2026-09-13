"""Anti look-ahead DataView.

DATA AVAILABLE AT T (decision_index i, T = bars[i].event_time):
  - Closed bars j with j <= i and event_time <= T
  - OHLCV of bar i (decision is at close of a candle-based series)
  - Features computed strictly from bars[0..i]

UNKNOWN AT T:
  - Any bar k with k > i (future candles)
  - Future open / high / low / close / volume
  - Next-bar fill price (simulator applies it AFTER T)
  - Any indicator that includes k > i

The view stores ONLY the visible prefix so strategies cannot index future bars
through this object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Sequence

from crypto_lab.backtest.types import Bar
from crypto_lab.exceptions import LookAheadError


class DataView:
    """Read-only prefix of a bar series at decision index i."""

    def __init__(self, bars: Sequence[Bar], decision_index: int) -> None:
        if decision_index < 0:
            raise LookAheadError("decision_index must be >= 0")
        if decision_index >= len(bars):
            raise LookAheadError(
                f"decision_index {decision_index} is past last bar {len(bars) - 1}"
            )
        # Copy only the visible prefix — future bars are not referenced.
        self._bars: tuple[Bar, ...] = tuple(bars[j] for j in range(decision_index + 1))
        self._i = decision_index
        last = self._bars[-1]
        self._decision_time: datetime = last.event_time
        self.symbol: str = last.symbol
        self.timeframe: str = last.timeframe

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def decision_index(self) -> int:
        return self._i

    @property
    def decision_time(self) -> datetime:
        return self._decision_time

    @property
    def current(self) -> Bar:
        return self._bars[-1]

    def available_bars(self) -> tuple[Bar, ...]:
        return self._bars

    def bar(self, index: int) -> Bar:
        """Absolute index from series start. Future indices raise LookAheadError."""
        if index < 0:
            index = len(self._bars) + index
        if index < 0:
            raise LookAheadError(f"Bar index {index} is before the series start")
        if index > self._i:
            raise LookAheadError(
                f"Look-ahead blocked: bar[{index}] is UNKNOWN AT T "
                f"(decision_index={self._i}, T={self._decision_time.isoformat()}). "
                "Future OHLCV is not available."
            )
        return self._bars[index]

    def __getitem__(self, index: int) -> Bar:
        return self.bar(index)

    def values(self, field: str = "close") -> List[float]:
        if field not in {"open", "high", "low", "close", "volume"}:
            raise ValueError(f"Unknown field {field!r}")
        return [getattr(b, field) for b in self._bars]

    def sma(self, period: int, *, field: str = "close") -> float | None:
        """Causal SMA ending at T. None if insufficient history."""
        if period <= 0:
            raise ValueError("SMA period must be > 0")
        vals = self.values(field)
        if len(vals) < period:
            return None
        window = vals[-period:]
        return sum(window) / float(period)

    def momentum(self, lookback: int, *, field: str = "close") -> float | None:
        """Causal close[T]/close[T-lookback] - 1. None if insufficient history."""
        if lookback <= 0:
            raise ValueError("momentum lookback must be > 0")
        vals = self.values(field)
        if len(vals) <= lookback:
            return None
        prev = vals[-1 - lookback]
        if prev == 0:
            return None
        return vals[-1] / prev - 1.0

    def return_at(self, offset: int = 0) -> float:
        """Simple return of bar at offset from T (0=current, -1=prev). offset>0 forbidden."""
        if offset > 0:
            raise LookAheadError(
                "UNKNOWN AT T: positive offset is a future bar return"
            )
        idx = self._i + offset
        if idx <= 0:
            return 0.0
        a = self._bars[idx - 1].close
        b = self._bars[idx].close
        if a == 0:
            return 0.0
        return b / a - 1.0


def assert_no_future_access(
    bars: Sequence[Bar],
    decision_index: int,
    accessed_indices: Iterable[int],
) -> None:
    """Test helper: raise if any accessed index is after the decision index."""
    bad = [i for i in accessed_indices if i > decision_index]
    if bad:
        raise LookAheadError(
            f"Future bar indices accessed at T={decision_index}: {bad}"
        )
