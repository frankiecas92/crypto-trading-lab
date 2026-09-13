"""Coinbase Exchange public market data provider (NO API key) — fallback.

REST: https://api.exchange.coinbase.com
WS: wss://ws-feed.exchange.coinbase.com
Symbols: stores venue-native BTC-USD / ETH-USD (never mapped to BTCUSDT/ETHUSDT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, List, Sequence

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.http_client import ResilientHttpClient
from crypto_lab.data.normalizer import (
    normalize_coinbase_candle,
    normalize_coinbase_ticker,
    normalize_coinbase_trade,
)
from crypto_lab.data.providers.base import DataProvider, OnMessage, ProviderHealth
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade
from crypto_lab.data.symbols import to_coinbase_product
from crypto_lab.exceptions import ProviderError

logger = logging.getLogger(__name__)

_GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}


class CoinbaseExchangeProvider(DataProvider):
    name = "coinbase"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: ResilientHttpClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = http or ResilientHttpClient(self.settings)
        self._owns_http = http is None
        self._ws_thread: threading.Thread | None = None
        self._ws_stop = threading.Event()
        self._connected = False
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None
        self._reconnect_count = 0

    def close(self) -> None:
        self.close_ws()
        if self._owns_http:
            self._http.close()

    def _url(self, path: str) -> str:
        return f"{self.settings.coinbase_rest_base.rstrip('/')}{path}"

    def _get(self, path: str, params: dict | None = None) -> Any:
        return self._http.get_json(self._url(path), params=params)

    def server_time(self) -> datetime:
        data = self._get("/time")
        # {"iso":"...","epoch":...}
        if "epoch" in data:
            return datetime.fromtimestamp(float(data["epoch"]), tz=timezone.utc)
        return datetime.fromisoformat(str(data["iso"]).replace("Z", "+00:00"))

    def fetch_klines(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        limit: int = 50,
        start=None,
        end=None,
    ) -> List[CanonicalCandle]:
        product = to_coinbase_product(symbol)
        gran = _GRANULARITY.get(timeframe)
        if gran is None:
            raise ProviderError(f"Unsupported Coinbase timeframe: {timeframe}")
        # Coinbase returns newest first; optional start/end window for pagination
        params: dict = {"granularity": gran}
        if start is not None:
            params["start"] = start.astimezone(timezone.utc).isoformat()
        if end is not None:
            params["end"] = end.astimezone(timezone.utc).isoformat()
        rows = self._get(
            f"/products/{product}/candles",
            params=params,
        )
        received_at = datetime.now(timezone.utc)
        candles = [
            normalize_coinbase_candle(
                row, product_id=product, timeframe=timeframe, received_at=received_at
            )
            for row in rows
        ]
        candles.sort(key=lambda c: c.event_time)
        if start is not None:
            candles = [c for c in candles if c.event_time >= start]
        if end is not None:
            candles = [c for c in candles if c.event_time <= end]
        if start is None and end is None:
            return candles[-limit:]
        return candles[:limit]

    def fetch_ticker(self, symbol: str) -> CanonicalQuote:
        product = to_coinbase_product(symbol)
        data = self._get(f"/products/{product}/ticker")
        data = {**data, "product_id": product}
        received_at = datetime.now(timezone.utc)
        return normalize_coinbase_ticker(data, received_at=received_at)

    def fetch_trades(self, symbol: str, *, limit: int = 50) -> List[CanonicalTrade]:
        product = to_coinbase_product(symbol)
        rows = self._get(f"/products/{product}/trades")
        received_at = datetime.now(timezone.utc)
        out: List[CanonicalTrade] = []
        for row in rows[:limit]:
            payload = {**row, "product_id": product}
            out.append(normalize_coinbase_trade(payload, received_at=received_at))
        return out

    def health(self) -> ProviderHealth:
        stale = False
        if self._last_message_at is not None:
            age = (datetime.now(timezone.utc) - self._last_message_at).total_seconds()
            stale = age > self.settings.stale_threshold_seconds
        return ProviderHealth(
            name=self.name,
            connected=self._connected,
            last_message_at=self._last_message_at,
            last_error=self._last_error,
            stale=stale,
            details={
                "reconnect_count": self._reconnect_count,
                "rest_base": self.settings.coinbase_rest_base,
                "note": "API may receive settings BTCUSDT → translated to BTC-USD; storage stays BTC-USD",
            },
        )

    def connect_ws(self, symbols: Sequence[str], on_message: OnMessage) -> None:
        if not self.settings.enable_ws:
            raise ProviderError("WebSocket disabled (ENABLE_WS=false)")
        if self._ws_thread and self._ws_thread.is_alive():
            return
        products = [to_coinbase_product(s) for s in symbols]
        self._ws_stop.clear()

        def _run() -> None:
            try:
                asyncio.run(self._ws_loop(products, on_message))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.exception("Coinbase WS thread crashed: %s", exc)
                self._connected = False

        self._ws_thread = threading.Thread(target=_run, name="coinbase-ws", daemon=True)
        self._ws_thread.start()

    async def _ws_loop(self, products: List[str], on_message: OnMessage) -> None:
        import websockets

        url = self.settings.coinbase_ws_base
        backoff = self.settings.rest_backoff_base_seconds
        subscribe = {
            "type": "subscribe",
            "product_ids": products,
            "channels": ["ticker", "matches"],
        }
        while not self._ws_stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps(subscribe))
                    self._connected = True
                    self._last_error = None
                    logger.info("Coinbase WS connected")
                    while not self._ws_stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            if self._last_message_at:
                                age = (
                                    datetime.now(timezone.utc) - self._last_message_at
                                ).total_seconds()
                                if age > self.settings.stale_threshold_seconds:
                                    raise ProviderError("WS stale — forcing reconnect")
                            continue
                        self._handle_ws_message(raw, on_message)
                    break
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self._last_error = str(exc)
                self._reconnect_count += 1
                if self._ws_stop.is_set():
                    break
                logger.warning("Coinbase WS disconnect (%s); reconnect in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _handle_ws_message(self, raw: str | bytes, on_message: OnMessage) -> None:
        received_at = datetime.now(timezone.utc)
        payload = json.loads(raw)
        msg_type = payload.get("type")
        if msg_type in {"subscriptions", "heartbeat"}:
            return
        self._last_message_at = received_at
        try:
            if msg_type == "ticker":
                on_message(normalize_coinbase_ticker(payload, received_at=received_at))
            elif msg_type in {"match", "last_match"}:
                on_message(normalize_coinbase_trade(payload, received_at=received_at))
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("Failed to handle Coinbase WS message: %s", exc)

    def close_ws(self) -> None:
        self._ws_stop.set()
        self._connected = False
        t = self._ws_thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._ws_thread = None
