"""Provider router with primary → fallback on failure / stale."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Sequence

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.providers.base import DataProvider, OnMessage, ProviderHealth
from crypto_lab.data.providers.binance import BinanceSpotProvider
from crypto_lab.data.providers.coinbase import CoinbaseExchangeProvider
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade
from crypto_lab.exceptions import ProviderError

logger = logging.getLogger(__name__)


class FallbackProvider(DataProvider):
    """Try primary; on failure or explicit stale, use fallback."""

    name = "router"

    def __init__(
        self,
        primary: DataProvider,
        fallback: DataProvider,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.settings = settings or get_settings()
        self.active: DataProvider = primary
        self._last_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "FallbackProvider":
        cfg = settings or get_settings()
        providers: Dict[str, DataProvider] = {
            "binance": BinanceSpotProvider(cfg),
            "coinbase": CoinbaseExchangeProvider(cfg),
        }
        primary = providers.get(cfg.primary_provider)
        fallback = providers.get(cfg.fallback_provider)
        if primary is None or fallback is None:
            raise ProviderError(
                f"Unknown providers primary={cfg.primary_provider} "
                f"fallback={cfg.fallback_provider}"
            )
        if primary.name == fallback.name:
            # still wrap for uniform API
            pass
        return cls(primary, fallback, settings=cfg)

    def _use(self, prefer_fallback: bool = False) -> DataProvider:
        return self.fallback if prefer_fallback else self.active

    def _try(self, op_name: str, call_primary, call_fallback):
        try:
            result = call_primary()
            self.active = self.primary
            return result
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("Primary %s failed on %s: %s — falling back", self.primary.name, op_name, exc)
            try:
                result = call_fallback()
                self.active = self.fallback
                return result
            except Exception as exc2:  # noqa: BLE001
                self._last_error = str(exc2)
                raise ProviderError(
                    f"Primary and fallback failed for {op_name}: {exc} | {exc2}"
                ) from exc2

    def server_time(self) -> datetime:
        return self._try(
            "server_time",
            self.primary.server_time,
            self.fallback.server_time,
        )

    def fetch_klines(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        limit: int = 50,
    ) -> List[CanonicalCandle]:
        return self._try(
            "fetch_klines",
            lambda: self.primary.fetch_klines(symbol, timeframe=timeframe, limit=limit),
            lambda: self.fallback.fetch_klines(symbol, timeframe=timeframe, limit=limit),
        )

    def fetch_ticker(self, symbol: str) -> CanonicalQuote:
        return self._try(
            "fetch_ticker",
            lambda: self.primary.fetch_ticker(symbol),
            lambda: self.fallback.fetch_ticker(symbol),
        )

    def fetch_trades(self, symbol: str, *, limit: int = 50) -> List[CanonicalTrade]:
        return self._try(
            "fetch_trades",
            lambda: self.primary.fetch_trades(symbol, limit=limit),
            lambda: self.fallback.fetch_trades(symbol, limit=limit),
        )

    def health(self) -> ProviderHealth:
        p = self.primary.health()
        f = self.fallback.health()
        return ProviderHealth(
            name=self.name,
            connected=p.connected or f.connected,
            last_message_at=p.last_message_at or f.last_message_at,
            last_error=self._last_error or p.last_error or f.last_error,
            stale=p.stale and f.stale,
            details={
                "active": self.active.name,
                "primary": p.to_dict(),
                "fallback": f.to_dict(),
            },
        )

    def connect_ws(self, symbols: Sequence[str], on_message: OnMessage) -> None:
        try:
            self.primary.connect_ws(symbols, on_message)
            self.active = self.primary
        except Exception as exc:  # noqa: BLE001
            logger.warning("Primary WS failed: %s — using fallback WS", exc)
            self.fallback.connect_ws(symbols, on_message)
            self.active = self.fallback

    def close_ws(self) -> None:
        self.primary.close_ws()
        self.fallback.close_ws()


# Alias
ProviderRouter = FallbackProvider
