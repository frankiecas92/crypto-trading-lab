"""Buy-and-hold experimental controls (not an edge search)."""

from __future__ import annotations

from typing import Any, Dict

from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.types import StrategySignal
from crypto_lab.strategies.base import Strategy


class BuyAndHold(Strategy):
    """BUY on the first visible bar; HOLD thereafter. Experimental control."""

    _version_num = 1

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self.strategy_id = f"BUY_AND_HOLD_{self.symbol.replace('USDT', '')}"

    def required_symbol(self) -> str:
        return self.symbol

    def parameters(self) -> Dict[str, Any]:
        return {"symbol": self.symbol}

    def generate_signals(self, data_view: DataView) -> StrategySignal:
        if data_view.symbol != self.symbol:
            return StrategySignal(side="HOLD", reason="wrong_symbol", symbol=self.symbol)
        if data_view.decision_index == 0:
            return StrategySignal(
                side="BUY",
                reason="initial_buy_and_hold",
                symbol=self.symbol,
            )
        return StrategySignal(side="HOLD", reason="hold", symbol=self.symbol)


class BuyAndHoldBTC(BuyAndHold):
    def __init__(self) -> None:
        super().__init__("BTCUSDT")
        self.strategy_id = "BUY_AND_HOLD_BTC"


class BuyAndHoldETH(BuyAndHold):
    def __init__(self) -> None:
        super().__init__("ETHUSDT")
        self.strategy_id = "BUY_AND_HOLD_ETH"
