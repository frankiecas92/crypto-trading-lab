"""Provider resilience tests (mocked WS disconnect/reconnect, REST fallback, 429)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from crypto_lab.config.settings import Settings
from crypto_lab.data.http_client import ResilientHttpClient
from crypto_lab.data.providers.binance import BinanceSpotProvider
from crypto_lab.data.providers.coinbase import CoinbaseExchangeProvider
from crypto_lab.data.providers.router import FallbackProvider
from crypto_lab.data.records import CanonicalCandle, ms_to_datetime
from crypto_lab.exceptions import ProviderError, RateLimitError


def _settings(**kwargs) -> Settings:
    base = dict(
        mode="PAPER",
        live_trading=False,
        http_max_retries=2,
        rest_backoff_base_seconds=0.01,
        binance_rest_base="https://data-api.binance.vision",
        binance_rest_fallback="https://api.binance.com",
        _env_file=None,
    )
    base.update(kwargs)
    return Settings(**base)


def _kline_row(ts_ms: int = 1_700_000_000_000):
    return [
        ts_ms,
        "100.0",
        "110.0",
        "90.0",
        "105.0",
        "12.5",
        ts_ms + 59_999,
        "1000",
        10,
        "5",
        "500",
        "0",
    ]


def test_websocket_disconnect_mocked():
    p = BinanceSpotProvider(_settings())
    p._connected = True
    p.simulate_disconnect()
    h = p.health()
    assert h.connected is False
    assert "disconnect" in (h.last_error or "")


def test_reconnect_mocked():
    p = BinanceSpotProvider(_settings())
    p.simulate_disconnect()
    p.simulate_reconnect()
    h = p.health()
    assert h.connected is True
    assert h.details["reconnect_count"] >= 1
    assert h.last_message_at is not None


def test_rest_fallback_mocked():
    primary = MagicMock()
    primary.name = "binance"
    primary.fetch_klines.side_effect = ProviderError("primary down")
    primary.health.return_value = MagicMock(
        to_dict=lambda: {}, connected=False, last_message_at=None, last_error="down", stale=True, name="binance"
    )

    now = datetime.now(timezone.utc)
    candle = CanonicalCandle(
        source="coinbase",
        symbol="BTC-USD",
        source_symbol="BTC-USD",
        base_asset="BTC",
        quote_asset="USD",
        canonical_asset="BTC",
        event_time=now,
        received_at=now,
        timeframe="1m",
        open=1,
        high=1,
        low=1,
        close=1,
        volume=1,
    )
    fallback = MagicMock()
    fallback.name = "coinbase"
    fallback.fetch_klines.return_value = [candle]
    fallback.health.return_value = MagicMock(
        to_dict=lambda: {}, connected=True, last_message_at=now, last_error=None, stale=False, name="coinbase"
    )

    router = FallbackProvider(primary, fallback, settings=_settings())
    rows = router.fetch_klines("BTCUSDT", limit=1)
    assert len(rows) == 1
    assert router.active.name == "coinbase"
    primary.fetch_klines.assert_called()
    fallback.fetch_klines.assert_called()


def test_rate_limiting_mocked_429():
    transport_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        transport_calls["n"] += 1
        if transport_calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"msg": "slow"})
        return httpx.Response(200, json={"serverTime": 1_700_000_000_000})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    http = ResilientHttpClient(_settings(http_max_retries=3), client=client)
    data = http.get_json("https://example.test/api/v3/time")
    assert data["serverTime"] == 1_700_000_000_000
    assert transport_calls["n"] == 2
    client.close()


def test_rate_limiting_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.01"}, json={})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    http = ResilientHttpClient(_settings(http_max_retries=1), client=client)
    with pytest.raises(RateLimitError):
        http.get_json("https://example.test/api/v3/time")
    client.close()


def test_binance_fetch_klines_mocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/klines"):
            return httpx.Response(200, json=[_kline_row()])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    http = ResilientHttpClient(_settings(), client=client)
    p = BinanceSpotProvider(_settings(), http=http)
    candles = p.fetch_klines("BTCUSDT", limit=1)
    assert len(candles) == 1
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].source_symbol == "BTCUSDT"
    assert candles[0].source == "binance"
    assert candles[0].base_asset == "BTC"
    assert candles[0].quote_asset == "USDT"
    assert candles[0].canonical_asset == "BTC"
    assert candles[0].event_time.tzinfo is not None
    assert candles[0].received_at.tzinfo is not None
    client.close()


def test_coinbase_symbol_identity_mocked():
    """Settings may pass BTCUSDT; Coinbase API uses BTC-USD; storage stays BTC-USD."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if "/candles" in request.url.path:
            # [time, low, high, open, close, volume]
            return httpx.Response(200, json=[[1_700_000_000, 90, 110, 100, 105, 3.0]])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    http = ResilientHttpClient(_settings(), client=client)
    p = CoinbaseExchangeProvider(_settings(), http=http)
    candles = p.fetch_klines("BTCUSDT", limit=1)
    assert any("/products/BTC-USD/candles" in pth for pth in seen_paths)
    assert candles[0].symbol == "BTC-USD"
    assert candles[0].source_symbol == "BTC-USD"
    assert candles[0].source == "coinbase"
    assert candles[0].base_asset == "BTC"
    assert candles[0].quote_asset == "USD"
    assert candles[0].canonical_asset == "BTC"
    # Never treat Coinbase as Binance USDT market
    assert candles[0].symbol != "BTCUSDT"
    client.close()
