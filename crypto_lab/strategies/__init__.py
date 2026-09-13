"""Phase 3 benchmark strategies (experimental controls, not edge search)."""

from crypto_lab.strategies.base import Strategy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC, BuyAndHoldETH
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover

__all__ = [
    "Strategy",
    "BuyAndHoldBTC",
    "BuyAndHoldETH",
    "SMACrossover",
    "SimpleMomentum",
]
