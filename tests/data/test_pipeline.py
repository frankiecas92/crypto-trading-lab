"""Pipeline + mocked integration: receive → validate → store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from crypto_lab.config.settings import Settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from sqlalchemy import select

from crypto_lab.data.models import DataQualityEvent, MarketData
from crypto_lab.data.pipeline import DataPipeline
from crypto_lab.data.providers.base import DataProvider, ProviderHealth
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote
from crypto_lab.data.repositories import MarketDataRepository


def _settings() -> Settings:
    return Settings(mode="PAPER", live_trading=False, _env_file=None, stale_threshold_seconds=3600)


class FakeProvider(DataProvider):
    name = "fake"

    def __init__(self, candles=None, fail=False):
        self._candles = candles or []
        self._fail = fail

    def fetch_klines(self, symbol, *, timeframe="1m", limit=50):
        if self._fail:
            raise RuntimeError("boom")
        return list(self._candles)

    def fetch_ticker(self, symbol):
        now = datetime.now(timezone.utc)
        return CanonicalQuote(
            source="fake",
            symbol=symbol,
            event_time=now - timedelta(seconds=1),
            received_at=now,
            bid=100.0,
            ask=100.1,
            last=100.05,
        )

    def fetch_trades(self, symbol, *, limit=50):
        return []

    def server_time(self):
        return datetime.now(timezone.utc)

    def health(self):
        return ProviderHealth(name=self.name, connected=True)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'pipe.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from crypto_lab.config.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        yield session


def test_pipeline_stores_valid_rejects_invalid(db_session):
    now = datetime.now(timezone.utc)
    good = CanonicalCandle(
        source="fake",
        symbol="BTCUSDT",
        event_time=now - timedelta(seconds=10),
        received_at=now,
        timeframe="1m",
        open=100,
        high=110,
        low=90,
        close=105,
        volume=1,
    )
    bad = CanonicalCandle(
        source="fake",
        symbol="BTCUSDT",
        event_time=now - timedelta(seconds=5),
        received_at=now,
        timeframe="1m",
        open=100,
        high=80,
        low=90,
        close=105,
        volume=1,
    )
    pipe = DataPipeline(db_session, provider=FakeProvider(), settings=_settings())
    result = pipe.process_many([good, bad])
    assert result.stored == 1
    assert result.rejected == 1
    rows = list(db_session.scalars(select(MarketData)).all())
    assert len(rows) == 1
    assert rows[0].event_time is not None
    assert rows[0].received_at is not None
    qevents = list(db_session.scalars(select(DataQualityEvent)).all())
    assert any(e.rule == "invalid_ohlc" for e in qevents)


def test_mocked_integration_fetch_validate_store(db_session):
    now = datetime.now(timezone.utc)
    candles = [
        CanonicalCandle(
            source="binance",
            symbol="BTCUSDT",
            event_time=now - timedelta(minutes=i),
            received_at=now,
            timeframe="1m",
            open=100 + i,
            high=110 + i,
            low=90 + i,
            close=105 + i,
            volume=1.0,
        )
        for i in range(3, 0, -1)
    ]
    provider = FakeProvider(candles=candles)
    pipe = DataPipeline(db_session, provider=provider, settings=_settings())
    result = pipe.fetch_and_store_klines(["BTCUSDT"], limit=3)
    assert result.stored == 3
    repo = MarketDataRepository(db_session)
    stored = repo.list_candles(symbol="BTCUSDT", timeframe="1m", source="binance")
    assert len(stored) == 3
    health = pipe.health()
    assert "metrics" in health
    assert health["metrics"]["stored"] == 3


def test_duplicate_storage_idempotent(db_session):
    now = datetime.now(timezone.utc)
    c = CanonicalCandle(
        source="binance",
        symbol="ETHUSDT",
        event_time=now - timedelta(seconds=30),
        received_at=now,
        timeframe="1m",
        open=1,
        high=1,
        low=1,
        close=1,
        volume=1,
    )
    pipe = DataPipeline(db_session, provider=FakeProvider(), settings=_settings())
    # bypass in-memory duplicate detector by using fresh validators... use storage twice
    pipe.storage.store_candle(c)
    pipe.storage.store_candle(c)
    db_session.commit()
    assert len(list(db_session.scalars(select(MarketData)).all())) == 1
