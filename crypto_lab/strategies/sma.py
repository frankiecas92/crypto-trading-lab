"""SMA crossover experimental control (fast/slow). Not an edge search."""

from __future__ import annotations

from typing import Any, Dict

from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.types import StrategySignal
from crypto_lab.strategies.base import Strategy


class SMACrossover(Strategy):
    """BUY when fast SMA crosses above slow; FLAT when it crosses below."""

    strategy_id = "SMA_CROSS"
    _version_num = 1

    def __init__(self, fast: int = 20, slow: int = 50, symbol: str | None = None) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError("SMA periods must be > 0")
        if fast >= slow:
            raise ValueError("fast SMA must be < slow SMA")
        self.fast = int(fast)
        self.slow = int(slow)
        self.symbol = symbol.upper() if symbol else None

    def required_symbol(self) -> str | None:
        return self.symbol

    def parameters(self) -> Dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "symbol": self.symbol}

    def generate_signals(self, data_view: DataView) -> StrategySignal:
        if self.symbol and data_view.symbol != self.symbol:
            return StrategySignal(side="HOLD", reason="wrong_symbol", symbol=self.symbol)
        fast_now = data_view.sma(self.fast)
        slow_now = data_view.sma(self.slow)
        if fast_now is None or slow_now is None:
            return StrategySignal(side="HOLD", reason="warmup", symbol=data_view.symbol)
        if data_view.decision_index < self.slow:
            return StrategySignal(side="HOLD", reason="warmup", symbol=data_view.symbol)

        # Previous SMAs from a prefix view (still no future).
        if data_view.decision_index < 1:
            return StrategySignal(side="HOLD", reason="warmup", symbol=data_view.symbol)
        prev = DataView(data_view.available_bars(), data_view.decision_index - 1)
        fast_prev = prev.sma(self.fast)
        slow_prev = prev.sma(self.slow)
        if fast_prev is None or slow_prev is None:
            return StrategySignal(side="HOLD", reason="warmup", symbol=data_view.symbol)

        if fast_prev <= slow_prev and fast_now > slow_now:
            return StrategySignal(
                side="BUY",
                reason=f"cross_up fast={fast_now:.6g} slow={slow_now:.6g}",
                symbol=data_view.symbol,
            )
        if fast_prev >= slow_prev and fast_now < slow_now:
            return StrategySignal(
                side="FLAT",
                reason=f"cross_down fast={fast_now:.6g} slow={slow_now:.6g}",
                symbol=data_view.symbol,
            )
        return StrategySignal(side="HOLD", reason="no_cross", symbol=data_view.symbol)
