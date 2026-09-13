"""Optional experimental stops: stop-loss, take-profit, trailing.

OHLCV limitation: if SL and TP both trade in the same bar, we assume the
worse outcome (stop-loss first) — conservative, documented.
Gap through: fill at bar.open if open is already beyond the stop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from crypto_lab.backtest.types import Bar, PortfolioState


@dataclass(frozen=True)
class StopPolicy:
    stop_loss_pct: float | None = None  # e.g. 0.02 = 2% below entry (long)
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None

    def __post_init__(self) -> None:
        for name, val in (
            ("stop_loss_pct", self.stop_loss_pct),
            ("take_profit_pct", self.take_profit_pct),
            ("trailing_stop_pct", self.trailing_stop_pct),
        ):
            if val is not None and val <= 0:
                raise ValueError(f"{name} must be > 0 when set")

    def active(self) -> Dict[str, Any]:
        return {
            "stop_loss": self.stop_loss_pct is not None,
            "take_profit": self.take_profit_pct is not None,
            "trailing_stop": self.trailing_stop_pct is not None,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def stop_distance_for_price(self, price: float) -> float | None:
        if self.stop_loss_pct is None:
            return None
        return price * self.stop_loss_pct


@dataclass(frozen=True)
class StopHit:
    reason: str  # STOP_LOSS / TAKE_PROFIT / TRAILING_STOP
    fill_price: float
    limitation: str | None = None


def check_stops(portfolio: PortfolioState, bar: Bar, policy: StopPolicy) -> Optional[StopHit]:
    """Return a StopHit if a long (or short) stop is triggered on this bar."""
    if portfolio.qty == 0 or portfolio.avg_entry <= 0:
        return None

    same_bar_conflict = (
        "OHLCV same-bar SL+TP: conservative assumption — stop-loss fills first"
    )

    if portfolio.qty > 0:
        entry = portfolio.avg_entry
        sl = entry * (1.0 - policy.stop_loss_pct) if policy.stop_loss_pct else None
        tp = entry * (1.0 + policy.take_profit_pct) if policy.take_profit_pct else None
        trail = None
        if policy.trailing_stop_pct and portfolio.peak_price > 0:
            trail = portfolio.peak_price * (1.0 - policy.trailing_stop_pct)

        hit_sl = sl is not None and bar.low <= sl
        hit_tp = tp is not None and bar.high >= tp
        hit_tr = trail is not None and bar.low <= trail

        limitation = None
        if hit_sl and hit_tp:
            limitation = same_bar_conflict
            px = bar.open if bar.open <= sl else sl  # type: ignore[operator]
            return StopHit("STOP_LOSS", float(px), limitation)
        if hit_sl:
            px = bar.open if bar.open <= sl else sl  # type: ignore[operator]
            return StopHit("STOP_LOSS", float(px), limitation)
        if hit_tr:
            px = bar.open if bar.open <= trail else trail  # type: ignore[operator]
            return StopHit("TRAILING_STOP", float(px), limitation)
        if hit_tp:
            px = bar.open if bar.open >= tp else tp  # type: ignore[operator]
            return StopHit("TAKE_PROFIT", float(px), limitation)
        return None

    # Short (only if allow_short produced a short). Symmetric.
    entry = portfolio.avg_entry
    sl = entry * (1.0 + policy.stop_loss_pct) if policy.stop_loss_pct else None
    tp = entry * (1.0 - policy.take_profit_pct) if policy.take_profit_pct else None
    trail = None
    if policy.trailing_stop_pct and portfolio.trough_price > 0:
        trail = portfolio.trough_price * (1.0 + policy.trailing_stop_pct)
    hit_sl = sl is not None and bar.high >= sl
    hit_tp = tp is not None and bar.low <= tp
    hit_tr = trail is not None and bar.high >= trail
    if hit_sl and hit_tp:
        px = bar.open if bar.open >= sl else sl  # type: ignore[operator]
        return StopHit("STOP_LOSS", float(px), same_bar_conflict)
    if hit_sl:
        px = bar.open if bar.open >= sl else sl  # type: ignore[operator]
        return StopHit("STOP_LOSS", float(px), None)
    if hit_tr:
        px = bar.open if bar.open >= trail else trail  # type: ignore[operator]
        return StopHit("TRAILING_STOP", float(px), None)
    if hit_tp:
        px = bar.open if bar.open <= tp else tp  # type: ignore[operator]
        return StopHit("TAKE_PROFIT", float(px), None)
    return None
