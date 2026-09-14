"""Downloader: pagination, resume, no dups, safe caps (mocked network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.config.settings import Settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.historical.constants import HARD_MAX_BARS
from crypto_lab.data.historical.downloader import HistoricalDownloader, is_candle_closed
from crypto_lab.data.historical.service import HistoricalDataService
from crypto_lab.data.providers.base import DataProvider, ProviderHealth
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.repositories import MarketDataRepository
from crypto_lab.exceptions import ValidationError


T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _settings() -> Settings:
    return Settings(mode="PAPER", live_trading=False, _env_file=None, historical_hard_max_bars=2000)


def _candle(i: int, *, source="binance", symbol="BTCUSDT") -> CanonicalCandle:
    et = T0 + timedelta(hours=i)
    return CanonicalCandle(
        source=source,
        symbol=symbol,
        source_symbol=symbol,
        event_time=et,
        received_at=et + timedelta(seconds=5),
        timeframe="1h",
        open=100.0 + i,
        high=101.0 + i,
        low=99.0 + i,
        close=100.5 + i,
        volume=1.0,
        base_asset="BTC",
        quote_asset="USDT" if symbol.endswith("USDT") else "USD",
        canonical_asset="BTC",
    )


class PagingProvider(DataProvider):
    name = "binance"

    def __init__(self, n: int = 40, page: int = 10):
        self.n = n
        self.page = page
        self.calls: list[tuple] = []

    def fetch_klines(self, symbol, *, timeframe="1h", limit=50, start=None, end=None):
        self.calls.append((symbol, timeframe, limit, start, end))
        out = []
        for i in range(self.n):
            c = _candle(i)
            if start is not None and c.event_time < start:
                continue
            if end is not None and c.event_time > end:
                continue
            out.append(c)
            if len(out) >= min(limit, self.page):
                break
        return out

    def fetch_ticker(self, symbol):
        raise NotImplementedError

    def fetch_trades(self, symbol, *, limit=50):
        return []

    def server_time(self):
        return datetime.now(timezone.utc)

    def health(self):
        return ProviderHealth(name=self.name, connected=True)


@pytest.fixture
def session(tmp_path):
    url = f"sqlite:///{tmp_path / 'hist.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as s:
        yield s


def test_pagination_and_store(session):
    prov = PagingProvider(n=25, page=7)
    dl = HistoricalDownloader(session, settings=_settings(), provider=prov, source="binance")
    result = dl.download(
        "BTCUSDT",
        timeframe="1h",
        start=T0,
        end=T0 + timedelta(hours=24),
        max_bars=20,
        resume=False,
    )
    assert result.pages >= 2
    assert result.stored >= 15
    assert result.rejected == 0
    repo = MarketDataRepository(session)
    rows = repo.list_candles_range(
        source="binance", symbol="BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=24)
    )
    times = [r.ts for r in rows]
    assert times == sorted(times)
    assert len(times) == len(set(times))  # no dups


def test_resume_skips_existing(session):
    prov = PagingProvider(n=20, page=20)
    dl = HistoricalDownloader(session, settings=_settings(), provider=prov, source="binance")
    first = dl.download("BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=9), max_bars=10)
    assert first.stored == 10
    prov2 = PagingProvider(n=20, page=20)
    dl2 = HistoricalDownloader(session, settings=_settings(), provider=prov2, source="binance")
    second = dl2.download("BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=19), max_bars=20)
    assert second.stored == 10  # only the new hours
    repo = MarketDataRepository(session)
    rows = repo.list_candles_range(
        source="binance", symbol="BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=19)
    )
    assert len(rows) == 20
    assert len({r.ts for r in rows}) == 20


def test_refuses_huge_max_bars(session):
    dl = HistoricalDownloader(session, settings=_settings(), provider=PagingProvider(), source="binance")
    with pytest.raises(ValidationError, match="hard max"):
        dl.download("BTCUSDT", timeframe="1h", max_bars=HARD_MAX_BARS + 1)


def test_does_not_mix_coinbase_into_binance(session):
    class Mixed(PagingProvider):
        def fetch_klines(self, symbol, *, timeframe="1h", limit=50, start=None, end=None):
            return [
                _candle(0, source="coinbase", symbol="BTC-USD"),
                _candle(0, source="binance", symbol="BTCUSDT"),
            ]

    dl = HistoricalDownloader(session, settings=_settings(), provider=Mixed(), source="binance")
    result = dl.download("BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=2), max_bars=5)
    repo = MarketDataRepository(session)
    cb = repo.list_candles_range(
        source="coinbase", symbol="BTC-USD", timeframe="1h", start=T0, end=T0 + timedelta(hours=2)
    )
    assert len(cb) == 0
    bn = repo.list_candles_range(
        source="binance", symbol="BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=2)
    )
    assert len(bn) >= 1


def test_service_registers_dataset_no_edge_claim(session):
    svc = HistoricalDataService(
        session, settings=_settings(), provider=PagingProvider(n=12, page=12), source="binance"
    )
    payload = svc.download("BTCUSDT", timeframe="1h", start=T0, end=T0 + timedelta(hours=11), max_bars=12)
    assert payload["dataset"]["source"] == "binance"
    assert payload["dataset"]["source_symbol"] == "BTCUSDT"
    assert payload["evidence"]["EDGE_CONFIRMED"] is False
    assert payload["evidence"]["PROFITABLE"] is False
    assert payload["LIVE_TRADING"] is False
    st = svc.status(payload["dataset"]["dataset_id"])
    assert st["test_locked"] is True


def test_is_candle_closed_helper_open_vs_closed():
    now = datetime(2026, 9, 13, 7, 6, tzinfo=timezone.utc)
    forming = datetime(2026, 9, 13, 7, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    assert is_candle_closed(closed, "1h", now) is True
    assert is_candle_closed(forming, "1h", now) is False
    # Exactly at close instant: closed (period elapsed)
    assert is_candle_closed(closed, "1h", datetime(2026, 9, 13, 7, 0, tzinfo=timezone.utc)) is True


def test_skips_incomplete_open_candle(session):
    """Candle whose open is in the current incomplete period is not stored."""
    now = T0 + timedelta(hours=7, minutes=6)

    class OpenTail(PagingProvider):
        def fetch_klines(self, symbol, *, timeframe="1h", limit=50, start=None, end=None):
            # 06:00 closed at 07:00; 07:00 still forming at 07:06
            out = [_candle(6), _candle(7)]
            if start is not None:
                out = [c for c in out if c.event_time >= start]
            if end is not None:
                out = [c for c in out if c.event_time <= end]
            return out

    dl = HistoricalDownloader(session, settings=_settings(), provider=OpenTail(), source="binance")
    result = dl.download(
        "BTCUSDT",
        timeframe="1h",
        start=T0,
        end=T0 + timedelta(hours=8),
        max_bars=10,
        resume=False,
        now=now,
    )
    assert result.skipped_open == 1
    assert result.stored == 1
    assert any("open/incomplete" in n.lower() or "forming" in n.lower() for n in result.notes)
    repo = MarketDataRepository(session)
    rows = repo.list_candles_range(
        source="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        start=T0,
        end=T0 + timedelta(hours=8),
    )
    times = {r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts.astimezone(timezone.utc) for r in rows}
    assert (T0 + timedelta(hours=6)) in times
    assert (T0 + timedelta(hours=7)) not in times


def test_closed_candles_kept_when_period_elapsed(session):
    now = T0 + timedelta(hours=8)  # 07:00 bar closed exactly at now

    class TwoClosed(PagingProvider):
        def fetch_klines(self, symbol, *, timeframe="1h", limit=50, start=None, end=None):
            out = [_candle(6), _candle(7)]
            if start is not None:
                out = [c for c in out if c.event_time >= start]
            if end is not None:
                out = [c for c in out if c.event_time <= end]
            return out

    dl = HistoricalDownloader(session, settings=_settings(), provider=TwoClosed(), source="binance")
    result = dl.download(
        "BTCUSDT",
        timeframe="1h",
        start=T0,
        end=T0 + timedelta(hours=8),
        max_bars=10,
        resume=False,
        now=now,
    )
    assert result.skipped_open == 0
    assert result.stored == 2
    repo = MarketDataRepository(session)
    rows = repo.list_candles_range(
        source="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        start=T0,
        end=T0 + timedelta(hours=8),
    )
    assert len(rows) == 2
