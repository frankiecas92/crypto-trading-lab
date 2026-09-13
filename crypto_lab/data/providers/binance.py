"""Binance Spot public market data provider (NO API key).

Primary REST host: https://data-api.binance.vision
WS: wss://data-stream.binance.vision
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.http_client import ResilientHttpClient
from crypto_lab.data.normalizer import (
    normalize_binance_book_ticker,
    normalize_binance_kline,
    normalize_binance_trade,
    normalize_binance_ws_kline,
)
from crypto_lab.data.providers.base import DataProvider, OnMessage, ProviderHealth
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade, ms_to_datetime
from crypto_lab.data.symbols import to_binance_symbol
from crypto_lab.exceptions import ProviderError

logger = logging.getLogger(__name__)


class BinanceSpotProvider(DataProvider):
    name = "binance"

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

    def _rest_bases(self) -> List[str]:
        return [self.settings.binance_rest_base, self.settings.binance_rest_fallback]

    def _get(self, path: str, params: dict | None = None) -> Any:
        last_exc: Exception | None = None
        for base in self._rest_bases():
            url = f"{base.rstrip('/')}{path}"
            try:
                return self._http.get_json(url, params=params)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Binance REST failed at %s: %s", url, exc)
        raise ProviderError(f"Binance REST failed for {path}: {last_exc}") from last_exc

    def server_time(self) -> datetime:
        data = self._get("/api/v3/time")
        return ms_to_datetime(int(data["serverTime"]))

    def fetch_klines(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        limit: int = 50,
    ) -> List[CanonicalCandle]:
        bsym = to_binance_symbol(symbol)
        rows = self._get(
            "/api/v3/klines",
            params={"symbol": bsym, "interval": timeframe, "limit": limit},
        )
        received_at = datetime.now(timezone.utc)
        return [
            normalize_binance_kline(
                row, symbol=bsym, timeframe=timeframe, received_at=received_at
            )
            for row in rows
        ]

    def fetch_ticker(self, symbol: str) -> CanonicalQuote:
        bsym = to_binance_symbol(symbol)
        data = self._get("/api/v3/ticker/bookTicker", params={"symbol": bsym})
        received_at = datetime.now(timezone.utc)
        return normalize_binance_book_ticker(data, received_at=received_at)

    def fetch_trades(self, symbol: str, *, limit: int = 50) -> List[CanonicalTrade]:
        bsym = to_binance_symbol(symbol)
        rows = self._get("/api/v3/trades", params={"symbol": bsym, "limit": limit})
        received_at = datetime.now(timezone.utc)
        out: List[CanonicalTrade] = []
        for row in rows:
            payload = {
                "s": bsym,
                "T": row.get("time"),
                "p": row["price"],
                "q": row["qty"],
                "t": row.get("id"),
                "m": row.get("isBuyerMaker"),
            }
            out.append(normalize_binance_trade(payload, received_at=received_at))
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
                "used_weight": self._http.last_used_weight,
                "rest_base": self.settings.binance_rest_base,
            },
        )

    def connect_ws(self, symbols: Sequence[str], on_message: OnMessage) -> None:
        if not self.settings.enable_ws:
            raise ProviderError("WebSocket disabled (ENABLE_WS=false)")
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_stop.clear()
        streams = []
        for sym in symbols:
            bsym = to_binance_symbol(sym).lower()
            streams.append(f"{bsym}@kline_1m")
            streams.append(f"{bsym}@trade")
            streams.append(f"{bsym}@bookTicker")
        stream_path = "/stream?streams=" + "/".join(streams)
        url = self.settings.binance_ws_base.rstrip("/") + stream_path

        def _run() -> None:
            try:
                asyncio.run(self._ws_loop(url, on_message))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.exception("Binance WS thread crashed: %s", exc)
                self._connected = False

        self._ws_thread = threading.Thread(target=_run, name="binance-ws", daemon=True)
        self._ws_thread.start()

    async def _ws_loop(self, url: str, on_message: OnMessage) -> None:
        import websockets

        backoff = self.settings.rest_backoff_base_seconds
        while not self._ws_stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._connected = True
                    self._last_error = None
                    logger.info("Binance WS connected")
                    while not self._ws_stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            # heartbeat / stale detection
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
                logger.warning("Binance WS disconnect (%s); reconnect in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _handle_ws_message(self, raw: str | bytes, on_message: OnMessage) -> None:
        received_at = datetime.now(timezone.utc)
        self._last_message_at = received_at
        payload = json.loads(raw)
        data = payload.get("data", payload)
        event = data.get("e")
        stream = str(payload.get("stream", ""))
        try:
            if event == "kline" or "@kline_" in stream:
                on_message(normalize_binance_ws_kline(payload, received_at=received_at))
            elif event == "trade" or stream.endswith("@trade"):
                on_message(normalize_binance_trade(payload, received_at=received_at))
            elif "bookTicker" in stream or ("b" in data and "a" in data and "s" in data):
                on_message(normalize_binance_book_ticker(payload, received_at=received_at))
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("Failed to handle Binance WS message: %s", exc)

    def close_ws(self) -> None:
        self._ws_stop.set()
        self._connected = False
        t = self._ws_thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._ws_thread = None

    # --- test helpers (mocked disconnect/reconnect) ---
    def simulate_disconnect(self) -> None:
        self._connected = False
        self._last_error = "simulated disconnect"

    def simulate_reconnect(self) -> None:
        self._connected = True
        self._last_error = None
        self._reconnect_count += 1
        self._last_message_at = datetime.now(timezone.utc)
