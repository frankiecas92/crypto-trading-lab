"""Dataset identity: content hash, venue-native, different content → different id."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crypto_lab.data.historical.identity import compute_dataset_identity, candle_checksum
from crypto_lab.data.records import CanonicalCandle


def _c(
    *,
    source="binance",
    symbol="BTCUSDT",
    i=0,
    tf="1h",
    o=100.0,
    h=110.0,
    l=90.0,
    c=105.0,
    v=1.0,
    t0=None,
):
    t0 = t0 or datetime(2024, 1, 1, tzinfo=timezone.utc)
    et = t0 + timedelta(hours=i)
    return CanonicalCandle(
        source=source,
        symbol=symbol,
        source_symbol=symbol,
        event_time=et,
        received_at=et + timedelta(seconds=2),
        timeframe=tf,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        base_asset="BTC" if "BTC" in symbol else "ETH",
        quote_asset="USDT" if "USDT" in symbol else "USD",
        canonical_asset="BTC" if "BTC" in symbol else "ETH",
    )


def test_same_content_same_id():
    candles = [_c(i=i) for i in range(5)]
    a = compute_dataset_identity(
        candles, source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit=None
    )
    b = compute_dataset_identity(
        list(candles), source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit=None
    )
    assert a.dataset_id == b.dataset_id
    assert a.checksum == b.checksum
    assert a.record_count == 5


def test_different_content_different_id():
    a = compute_dataset_identity(
        [_c(i=i) for i in range(5)],
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    b = compute_dataset_identity(
        [_c(i=i, c=200.0) for i in range(5)],
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    assert a.checksum != b.checksum
    assert a.dataset_id != b.dataset_id


def test_record_count_changes_id():
    a = compute_dataset_identity(
        [_c(i=i) for i in range(4)],
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    b = compute_dataset_identity(
        [_c(i=i) for i in range(5)],
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    assert a.dataset_id != b.dataset_id


def test_binance_and_coinbase_never_same_market_id():
    bn = compute_dataset_identity(
        [_c(source="binance", symbol="BTCUSDT", i=i) for i in range(3)],
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    cb = compute_dataset_identity(
        [_c(source="coinbase", symbol="BTC-USD", i=i) for i in range(3)],
        source="coinbase",
        source_symbol="BTC-USD",
        timeframe="1h",
        git_commit=None,
    )
    assert bn.source != cb.source
    assert bn.quote_asset == "USDT"
    assert cb.quote_asset == "USD"
    assert bn.dataset_id != cb.dataset_id
    assert bn.source_symbol == "BTCUSDT"
    assert cb.source_symbol == "BTC-USD"


def test_checksum_ignores_received_at():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    a = _c(i=0, t0=t0)
    b = CanonicalCandle(
        source=a.source,
        symbol=a.symbol,
        source_symbol=a.source_symbol,
        event_time=a.event_time,
        received_at=a.received_at + timedelta(hours=3),
        timeframe=a.timeframe,
        open=a.open,
        high=a.high,
        low=a.low,
        close=a.close,
        volume=a.volume,
    )
    assert candle_checksum([a]) == candle_checksum([b])
