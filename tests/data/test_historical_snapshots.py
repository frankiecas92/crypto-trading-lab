"""Snapshots are reproducible and name the exact dataset."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.historical.identity import compute_dataset_identity
from crypto_lab.data.historical.snapshots import create_snapshot, snapshot_payload, snapshot_to_dict
from crypto_lab.data.records import CanonicalCandle


T0 = datetime(2024, 2, 1, tzinfo=timezone.utc)


def _candles(n=8, close_shift=0.0):
    out = []
    for i in range(n):
        et = T0 + timedelta(hours=i)
        out.append(
            CanonicalCandle(
                source="binance",
                symbol="BTCUSDT",
                source_symbol="BTCUSDT",
                event_time=et,
                received_at=et + timedelta(seconds=2),
                timeframe="1h",
                open=100.0,
                high=110.0,
                low=90.0,
                close=105.0 + close_shift,
                volume=1.0,
                base_asset="BTC",
                quote_asset="USDT",
                canonical_asset="BTC",
            )
        )
    return out


def test_snapshot_fields_and_no_claims(tmp_path):
    url = f"sqlite:///{tmp_path / 'snap.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    candles = _candles()
    ident = compute_dataset_identity(
        candles, source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit="deadbeef"
    )
    payload = snapshot_payload(ident)
    for key in (
        "dataset_id",
        "data_version",
        "source",
        "symbol",
        "timeframe",
        "start",
        "end",
        "record_count",
        "checksum",
    ):
        assert key in payload
    assert payload["EDGE_CONFIRMED"] is False
    assert payload["PROFITABLE"] is False
    assert payload["evidence"]["EDGE_CONFIRMED"] is False
    with Session() as session:
        row = create_snapshot(session, ident)
        session.commit()
        d = snapshot_to_dict(row)
        assert d["dataset_id"] == ident.dataset_id
        assert d["checksum"] == ident.checksum
        assert d["record_count"] == 8
        assert d["source"] == "binance"
        assert d["symbol"] == "BTCUSDT"
        assert d["timeframe"] == "1h"


def test_reproducible_same_content_same_checksum():
    a = compute_dataset_identity(
        _candles(), source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit=None
    )
    b = compute_dataset_identity(
        _candles(), source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit=None
    )
    assert a.checksum == b.checksum
    assert a.dataset_id == b.dataset_id
    c = compute_dataset_identity(
        _candles(close_shift=1.0),
        source="binance",
        source_symbol="BTCUSDT",
        timeframe="1h",
        git_commit=None,
    )
    assert c.checksum != a.checksum
    assert c.dataset_id != a.dataset_id
