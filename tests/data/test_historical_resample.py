"""Causal resample: no look-ahead from future 1m bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.data.historical.resample import assert_no_lookahead, resample_causal
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.exceptions import LookAheadError


def _m(i: int, *, close=None, high=None, t0=None) -> CanonicalCandle:
    t0 = t0 or datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    et = t0 + timedelta(minutes=i)
    px = 100.0 + i
    c = close if close is not None else px
    h = high if high is not None else max(px, c)
    return CanonicalCandle(
        source="binance",
        symbol="BTCUSDT",
        source_symbol="BTCUSDT",
        event_time=et,
        received_at=et + timedelta(seconds=1),
        timeframe="1m",
        open=px,
        high=h,
        low=min(px, c) - 0.5,
        close=c,
        volume=1.0,
        base_asset="BTC",
        quote_asset="USDT",
        canonical_asset="BTC",
    )


def test_1m_to_5m_ohlc():
    bars = [_m(i) for i in range(15)]
    out = resample_causal(bars, "5m")
    assert len(out) == 3
    first = out[0]
    assert first.timeframe == "5m"
    assert first.open == bars[0].open
    assert first.close == bars[4].close
    assert first.high == max(b.high for b in bars[:5])
    assert first.low == min(b.low for b in bars[:5])
    assert first.volume == pytest.approx(5.0)
    assert_no_lookahead(bars, out, target_tf="5m")


def test_future_bar_does_not_leak_into_earlier_bucket():
    bars = [_m(i) for i in range(10)]
    baseline = resample_causal(bars, "5m")
    assert len(baseline) >= 2
    poison = CanonicalCandle(
        source="binance",
        symbol="BTCUSDT",
        source_symbol="BTCUSDT",
        event_time=datetime(2024, 1, 1, 0, 20, tzinfo=timezone.utc),
        received_at=datetime(2024, 1, 1, 0, 21, tzinfo=timezone.utc),
        timeframe="1m",
        open=9999.0,
        high=9999.0,
        low=9999.0,
        close=9999.0,
        volume=99.0,
    )
    mixed = list(bars) + [poison]
    out = resample_causal(mixed, "5m")
    # First two 5m candles must match baseline (poison is at 00:20)
    assert out[0].high == baseline[0].high
    assert out[0].close == baseline[0].close
    assert out[1].high == baseline[1].high
    assert 9999.0 not in (out[0].high, out[0].close, out[1].high, out[1].close)
    assert_no_lookahead(mixed, out[:2], target_tf="5m")


def test_asof_excludes_future_source_bars():
    bars = [_m(i) for i in range(12)]
    asof = datetime(2024, 1, 1, 0, 4, tzinfo=timezone.utc)
    out = resample_causal(bars, "5m", asof=asof)
    # Only first 5m bucket can complete (minutes 0-4)
    assert len(out) == 1
    assert max(b.event_time for b in bars if b.event_time <= asof) <= asof
    assert out[0].close == bars[4].close


def test_incomplete_bucket_omitted():
    bars = [_m(i) for i in range(3)]  # not enough for a 5m close
    out = resample_causal(bars, "5m")
    assert out == []


def test_1m_to_1h_and_15m():
    bars = [_m(i) for i in range(60)]
    h = resample_causal(bars, "1h")
    assert len(h) == 1
    q = resample_causal(bars, "15m")
    assert len(q) == 4
    four = resample_causal(bars, "4h")
    assert four == []  # incomplete 4h
