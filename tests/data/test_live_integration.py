"""Optional live public REST integration (no API keys).

Enable with: RUN_LIVE_DATA_TESTS=1 pytest -m live
"""

from __future__ import annotations

import os

import pytest

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.pipeline import DataPipeline
from crypto_lab.data.providers.binance import BinanceSpotProvider
from crypto_lab.data.providers.coinbase import CoinbaseExchangeProvider
from crypto_lab.data.providers.router import FallbackProvider
from crypto_lab.data.repositories import MarketDataRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
]

live_enabled = os.environ.get("RUN_LIVE_DATA_TESTS") == "1"


@pytest.mark.skipif(not live_enabled, reason="Set RUN_LIVE_DATA_TESTS=1 for live public REST")
def test_live_binance_btc_eth_receive_validate_store(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'live.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    settings = Settings(_env_file=None)
    assert settings.mode == "PAPER"
    assert settings.live_trading is False
    Session = get_session_factory(url)
    with Session() as session:
        provider = BinanceSpotProvider(settings)
        try:
            pipe = DataPipeline(session, provider=provider, settings=settings)
            result = pipe.fetch_and_store_klines(["BTCUSDT", "ETHUSDT"], timeframe="1m", limit=5)
            assert result.stored >= 2
            assert result.rejected == 0 or result.stored > 0
            repo = MarketDataRepository(session)
            btc = repo.list_candles(symbol="BTCUSDT", timeframe="1m", source="binance", limit=5)
            eth = repo.list_candles(symbol="ETHUSDT", timeframe="1m", source="binance", limit=5)
            assert len(btc) >= 1
            assert len(eth) >= 1
            assert btc[0].symbol == "BTCUSDT"
            assert btc[0].quote_asset == "USDT"
            assert btc[0].canonical_asset == "BTC"
            assert eth[0].canonical_asset == "ETH"
            health = pipe.health()
            assert health["metrics"]["stored"] >= 2
        finally:
            provider.close()


@pytest.mark.skipif(not live_enabled, reason="Set RUN_LIVE_DATA_TESTS=1 for live public REST")
def test_live_coinbase_btc_eth_stores_native_symbols(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'live_cb.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    settings = Settings(_env_file=None)
    assert settings.mode == "PAPER"
    assert settings.live_trading is False
    Session = get_session_factory(url)
    with Session() as session:
        provider = CoinbaseExchangeProvider(settings)
        try:
            pipe = DataPipeline(session, provider=provider, settings=settings)
            # Settings-style symbols: translated to BTC-USD/ETH-USD for API; stored native
            result = pipe.fetch_and_store_klines(["BTCUSDT", "ETHUSDT"], timeframe="1m", limit=5)
            assert result.stored >= 2
            repo = MarketDataRepository(session)
            btc = repo.list_candles(symbol="BTC-USD", timeframe="1m", source="coinbase", limit=5)
            eth = repo.list_candles(symbol="ETH-USD", timeframe="1m", source="coinbase", limit=5)
            assert len(btc) >= 1
            assert len(eth) >= 1
            assert btc[0].symbol == "BTC-USD"
            assert btc[0].source_symbol == "BTC-USD"
            assert btc[0].base_asset == "BTC"
            assert btc[0].quote_asset == "USD"
            assert btc[0].canonical_asset == "BTC"
            assert eth[0].symbol == "ETH-USD"
            assert eth[0].quote_asset == "USD"
            # Must not have stored as Binance USDT markets
            assert (
                len(repo.list_candles(symbol="BTCUSDT", timeframe="1m", source="coinbase", limit=5))
                == 0
            )
        finally:
            provider.close()


@pytest.mark.skipif(not live_enabled, reason="Set RUN_LIVE_DATA_TESTS=1 for live public REST")
def test_live_fallback_router_ping():
    settings = Settings(_env_file=None)
    router = FallbackProvider.from_settings(settings)
    try:
        t = router.server_time()
        assert t.tzinfo is not None
    finally:
        router.close_ws()
