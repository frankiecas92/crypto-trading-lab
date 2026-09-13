"""Market data providers (public endpoints only — no API keys)."""

from crypto_lab.data.providers.base import DataProvider, ProviderHealth
from crypto_lab.data.providers.binance import BinanceSpotProvider
from crypto_lab.data.providers.coinbase import CoinbaseExchangeProvider
from crypto_lab.data.providers.router import FallbackProvider, ProviderRouter

__all__ = [
    "DataProvider",
    "ProviderHealth",
    "BinanceSpotProvider",
    "CoinbaseExchangeProvider",
    "FallbackProvider",
    "ProviderRouter",
]
