"""Dataset quality: VALID / warnings / INVALID, gaps, dups, timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crypto_lab.data.historical.constants import QUALITY_INVALID, QUALITY_VALID, QUALITY_WARN
from crypto_lab.data.historical.quality import evaluate_dataset_quality
from crypto_lab.data.records import CanonicalCandle


def _now():
    return datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _c(*, i=0, tf="1h", **kwargs):
    t0 = kwargs.pop("t0", None) or datetime(2024, 1, 1, tzinfo=timezone.utc)
    et = kwargs.pop("event_time", t0 + timedelta(hours=i if tf == "1h" else 0, minutes=0 if tf == "1h" else i))
    ra = kwargs.pop("received_at", _now())
    defaults = dict(
        source="binance",
        symbol="BTCUSDT",
        source_symbol="BTCUSDT",
        event_time=et,
        received_at=ra,
        timeframe=tf,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=1.0,
    )
    defaults.update(kwargs)
    return CanonicalCandle(**defaults)


def test_valid_contiguous_series():
    candles = [_c(i=i) for i in range(6)]
    r = evaluate_dataset_quality(candles, timeframe="1h", now=_now())
    assert r.status == QUALITY_VALID
    assert r.critical_errors == 0
    assert r.gap_count == 0
    assert r.to_dict()["imputation"] is False
    assert r.to_dict()["invented_fills"] is False


def test_gap_is_warning_not_imputed():
    c1 = _c(i=0)
    c2 = _c(i=3)  # missing 1h and 2h
    r = evaluate_dataset_quality([c1, c2], timeframe="1h", now=_now())
    assert r.status == QUALITY_WARN
    assert r.gap_count >= 1
    g = r.gaps[0]
    assert g.expected_records >= 1
    assert g.actual_records == 0
    assert "not imputed" in g.message.lower() or g.actual_records == 0


def test_invalid_ohlc_never_valid():
    r = evaluate_dataset_quality([_c(i=0, high=80.0, low=90.0)], timeframe="1h", now=_now())
    assert r.status == QUALITY_INVALID
    assert r.critical_errors >= 1


def test_negative_price_never_valid():
    r = evaluate_dataset_quality([_c(i=0, open=-1.0, high=1.0, low=-2.0, close=0.5)], timeframe="1h", now=_now())
    assert r.status == QUALITY_INVALID


def test_negative_volume_never_valid():
    r = evaluate_dataset_quality([_c(i=0, volume=-3.0)], timeframe="1h", now=_now())
    assert r.status == QUALITY_INVALID


def test_future_timestamp_never_valid():
    future = _now() + timedelta(days=2)
    r = evaluate_dataset_quality(
        [_c(event_time=future, received_at=_now())],
        timeframe="1h",
        now=_now(),
    )
    assert r.status == QUALITY_INVALID
    assert any(i.rule == "future_timestamp" for i in r.issues)


def test_duplicates_never_valid():
    c = _c(i=0)
    r = evaluate_dataset_quality([c, c], timeframe="1h", now=_now())
    assert r.status == QUALITY_INVALID
    assert any(i.rule == "duplicate_candle" for i in r.issues)


def test_event_time_kept_distinct_from_received_at():
    c = _c(i=0)
    assert c.event_time != c.received_at
    r = evaluate_dataset_quality([c], timeframe="1h", now=_now())
    assert r.status == QUALITY_VALID
    assert any("event_time" in n for n in r.notes)


def test_empty_dataset_invalid():
    r = evaluate_dataset_quality([], timeframe="1h", now=_now())
    assert r.status == QUALITY_INVALID
