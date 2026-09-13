"""Abstract DataProvider — receive public market data only (no trading)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade


@dataclass
class ProviderHealth:
    name: str
    connected: bool = False
    last_message_at: datetime | None = None
    last_error: str | None = None
    stale: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "last_error": self.last_error,
            "stale": self.stale,
            "details": self.details,
        }


OnMessage = Callable[[CanonicalCandle | CanonicalTrade | CanonicalQuote], None]


class DataProvider(ABC):
    """ABC for market-data providers. No order / account methods."""

    name: str = "abstract"

    @abstractmethod
    def fetch_klines(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        limit: int = 50,
    ) -> List[CanonicalCandle]:
        """REST backfill / snapshot of OHLCV candles."""

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> CanonicalQuote:
        """REST book ticker / best bid-ask."""

    @abstractmethod
    def fetch_trades(self, symbol: str, *, limit: int = 50) -> List[CanonicalTrade]:
        """REST recent public trades."""

    @abstractmethod
    def server_time(self) -> datetime:
        """Exchange server time (UTC)."""

    def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name)

    # Optional WS interface — implementations may no-op if disabled
    def connect_ws(self, symbols: Sequence[str], on_message: OnMessage) -> None:
        raise NotImplementedError(f"{self.name} does not implement connect_ws")

    def close_ws(self) -> None:
        return None

    def ping(self) -> bool:
        try:
            self.server_time()
            return True
        except Exception:  # noqa: BLE001
            return False
