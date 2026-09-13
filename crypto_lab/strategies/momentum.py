"""Simple momentum experimental control. Not an edge search."""

from __future__ import annotations

from typing import Any, Dict

from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.types import StrategySignal
from crypto_lab.strategies.base import Strategy


class SimpleMomentum(Strategy):
    """BUY if lookback return > +threshold; FLAT if < -threshold; else HOLD."""

    strategy_id = "MOMENTUM"
    _version_num = 1

    def __init__(
        self,
        lookback: int = 10,
        threshold: float = 0.0,
        symbol: str | None = None,
    ) -> None:
        if lookback <= 0:
            raise ValueError("lookback must be > 0")
        self.lookback = int(lookback)
        self.threshold = float(threshold)
        self.symbol = symbol.upper() if symbol else None

    def required_symbol(self) -> str | None:
        return self.symbol

    def parameters(self) -> Dict[str, Any]:
        return {
            "lookback": self.lookback,
            "threshold": self.threshold,
            "symbol": self.symbol,
        }

    def generate_signals(self, data_view: DataView) -> StrategySignal:
        if self.symbol and data_view.symbol != self.symbol:
            return StrategySignal(side="HOLD", reason="wrong_symbol", symbol=self.symbol)
        mom = data_view.momentum(self.lookback)
        if mom is None:
            return StrategySignal(side="HOLD", reason="warmup", symbol=data_view.symbol)
        if mom > self.threshold:
            return StrategySignal(
                side="BUY",
                reason=f"momentum={mom:.6g} > {self.threshold}",
                symbol=data_view.symbol,
            )
        if mom < -self.threshold:
            return StrategySignal(
                side="FLAT",
                reason=f"momentum={mom:.6g} < -{self.threshold}",
                symbol=data_view.symbol,
            )
        return StrategySignal(side="HOLD", reason="in_band", symbol=data_view.symbol)
