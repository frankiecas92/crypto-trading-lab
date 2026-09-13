"""Data quality validator tests — covers required Phase 2 cases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.config.settings import Settings
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade
from crypto_lab.data.validator import MarketDataValidator


def _settings(**kwargs) -> Settings:
    base = dict(
        mode="PAPER",
        live_trading=False,
        stale_threshold_seconds=60,
        max_spread_bps=50,
        future_skew_ms=2000,
        clock_skew_ms=5000,
        _env_file=None,
    )
    base.update(kwargs)
    return Settings(**base)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _candle(**kwargs) -> CanonicalCandle:
    now = _now()
    defaults = dict(
        source="binance",
        symbol="BTCUSDT",
        event_time=now - timedelta(seconds=5),
        received_at=now,
        timeframe="1m",
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=1.0,
    )
    defaults.update(kwargs)
    return CanonicalCandle(**defaults)


def test_valid_data():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle())
    assert r.ok


def test_duplicates():
    v = MarketDataValidator(_settings())
    c = _candle()
    assert v.validate(c).ok
    r2 = v.validate(c)
    assert not r2.ok
    assert any(i.rule == "duplicate_candle" for i in r2.issues)


def test_future_timestamps():
    v = MarketDataValidator(_settings())
    now = _now()
    c = _candle(event_time=now + timedelta(hours=1), received_at=now)
    r = v.validate(c)
    assert not r.ok
    assert any(i.rule == "future_timestamp" for i in r.issues)


def test_delayed_lagging_timestamps():
    v = MarketDataValidator(_settings(stale_threshold_seconds=10))
    now = _now()
    c = _candle(event_time=now - timedelta(hours=2), received_at=now)
    r = v.validate(c)
    assert any(i.rule == "delayed_timestamp" for i in r.issues)


def test_missing_candles_gaps():
    v = MarketDataValidator(_settings())
    now = _now().replace(second=0, microsecond=0)
    c1 = _candle(event_time=now, received_at=now + timedelta(seconds=1))
    c2 = _candle(event_time=now + timedelta(minutes=3), received_at=now + timedelta(minutes=3))
    gaps = v.detect_gaps([c1, c2], timeframe="1m")
    assert gaps
    assert gaps[0].rule == "missing_candles"


def test_invalid_ohlc():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(high=80.0, low=90.0))
    assert not r.ok
    assert any(i.rule == "invalid_ohlc" for i in r.issues)


def test_impossible_prices():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(open=-1.0, high=1.0, low=0.5, close=0.8))
    assert not r.ok
    assert any(i.rule == "impossible_price" for i in r.issues)


def test_invalid_volume():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(volume=-5.0))
    assert not r.ok
    assert any(i.rule == "invalid_volume" for i in r.issues)


def test_bid_gt_ask():
    v = MarketDataValidator(_settings())
    now = _now()
    q = CanonicalQuote(
        source="binance",
        symbol="ETHUSDT",
        event_time=now - timedelta(seconds=1),
        received_at=now,
        bid=2000.0,
        ask=1990.0,
    )
    r = v.validate(q)
    assert not r.ok
    assert any(i.rule == "bid_gt_ask" for i in r.issues)


def test_abnormal_spread():
    v = MarketDataValidator(_settings(max_spread_bps=10))
    now = _now()
    q = CanonicalQuote(
        source="binance",
        symbol="ETHUSDT",
        event_time=now - timedelta(seconds=1),
        received_at=now,
        bid=100.0,
        ask=102.0,  # 200 bps
    )
    r = v.validate(q)
    assert not r.ok
    assert any(i.rule == "abnormal_spread" for i in r.issues)


def test_stale_data():
    v = MarketDataValidator(_settings(stale_threshold_seconds=30))
    now = _now()
    c = _candle(event_time=now - timedelta(seconds=120), received_at=now)
    issue = v.check_stale(c, now=now)
    assert issue is not None
    assert issue.rule == "stale_data"


def test_out_of_order_data():
    v = MarketDataValidator(_settings())
    now = _now()
    c1 = _candle(event_time=now - timedelta(minutes=1), received_at=now)
    c2 = _candle(event_time=now - timedelta(minutes=2), received_at=now)
    assert v.validate(c1).ok
    r = v.validate(c2)
    assert any(i.rule == "out_of_order" for i in r.issues)


def test_invalid_symbol():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(symbol="DOGEUSDT"))
    assert not r.ok
    assert any(i.rule == "invalid_symbol" for i in r.issues)


def test_event_time_vs_received_time_handling():
    v = MarketDataValidator(_settings(future_skew_ms=1000))
    now = _now()
    # within skew — ok
    ok = _candle(event_time=now + timedelta(milliseconds=500), received_at=now)
    assert v.validate(ok).ok
    # beyond skew — reject
    bad = _candle(event_time=now + timedelta(seconds=10), received_at=now)
    r = v.validate(bad)
    assert not r.ok
    assert any(i.rule == "future_timestamp" for i in r.issues)


def test_close_outside_high_low():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(high=110, low=100, open=105, close=120))
    assert not r.ok
    assert any(i.rule == "invalid_ohlc" for i in r.issues)


def test_clock_skew():
    v = MarketDataValidator(_settings(clock_skew_ms=1000))
    now = _now()
    issue = v.check_clock_skew(
        exchange_time=now + timedelta(seconds=5),
        local_time=now,
    )
    assert issue is not None
    assert issue.rule == "clock_skew"


def test_coinbase_btc_usd_valid_symbol():
    v = MarketDataValidator(_settings())
    r = v.validate(
        _candle(
            source="coinbase",
            symbol="BTC-USD",
            source_symbol="BTC-USD",
            base_asset="BTC",
            quote_asset="USD",
            canonical_asset="BTC",
        )
    )
    assert r.ok


def test_binance_rejects_coinbase_product_symbol():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(source="binance", symbol="BTC-USD"))
    assert not r.ok
    assert any(i.rule == "invalid_symbol" for i in r.issues)


def test_coinbase_rejects_binance_usdt_symbol():
    v = MarketDataValidator(_settings())
    r = v.validate(_candle(source="coinbase", symbol="BTCUSDT"))
    assert not r.ok
    assert any(i.rule == "invalid_symbol" for i in r.issues)
