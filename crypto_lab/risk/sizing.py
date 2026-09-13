"""Position sizing: FIXED_NOTIONAL and FIXED_RISK. No leverage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal

from crypto_lab.backtest.types import PortfolioState


SizingMode = Literal["FIXED_NOTIONAL", "FIXED_RISK"]


@dataclass(frozen=True)
class SizingPolicy:
    mode: SizingMode = "FIXED_NOTIONAL"
    notional: float = 10_000.0
    risk_per_trade: float = 0.01  # fraction of equity (FIXED_RISK)
    max_position_qty: float | None = None
    max_exposure: float = 1.0  # fraction of equity; 1.0 = 100%, no leverage
    allow_short: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"FIXED_NOTIONAL", "FIXED_RISK"}:
            raise ValueError(f"Unknown sizing mode {self.mode!r}")
        if self.notional <= 0:
            raise ValueError("notional must be > 0")
        if not (0 < self.risk_per_trade <= 1):
            raise ValueError("risk_per_trade must be in (0, 1]")
        if not (0 < self.max_exposure <= 1.0):
            raise ValueError("max_exposure must be in (0, 1] (no leverage)")
        if self.max_position_qty is not None and self.max_position_qty <= 0:
            raise ValueError("max_position_qty must be > 0 when set")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def size_quantity(
    *,
    portfolio: PortfolioState,
    price: float,
    policy: SizingPolicy,
    fee_rate: float = 0.0,
    stop_distance: float | None = None,
) -> float:
    """Return a long qty that respects cash, max exposure, and no leverage.

    FIXED_RISK requires stop_distance (price units). qty = (equity * risk) / stop_distance.
    """
    if price <= 0:
        return 0.0
    equity = portfolio.equity(price)
    if equity <= 0:
        return 0.0

    if policy.mode == "FIXED_NOTIONAL":
        qty = policy.notional / price
    else:
        if stop_distance is None or stop_distance <= 0:
            raise ValueError("FIXED_RISK requires a positive stop_distance")
        risk_cash = equity * policy.risk_per_trade
        qty = risk_cash / stop_distance

    if policy.max_position_qty is not None:
        qty = min(qty, policy.max_position_qty)

    max_val = equity * policy.max_exposure
    qty = min(qty, max_val / price)

    # No leverage: affordability including fee buffer.
    # cash must cover fill * qty * (1 + fee_rate)
    denom = price * (1.0 + max(fee_rate, 0.0))
    if denom <= 0:
        return 0.0
    affordable = portfolio.cash / denom
    qty = min(qty, max(0.0, affordable))
    return qty
