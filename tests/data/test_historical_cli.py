"""CLI historical-data group: help, safe defaults, status/snapshot on mocked data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from crypto_lab.cli import main
from crypto_lab.config.settings import get_settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.historical.identity import compute_dataset_identity
from crypto_lab.data.historical.service import HistoricalDataService
from crypto_lab.data.providers.base import DataProvider, ProviderHealth
from crypto_lab.data.records import CanonicalCandle


T0 = datetime(2024, 3, 1, tzinfo=timezone.utc)


class TinyProvider(DataProvider):
    name = "binance"

    def fetch_klines(self, symbol, *, timeframe="1h", limit=50, start=None, end=None):
        out = []
        for i in range(8):
            et = T0 + timedelta(hours=i)
            out.append(
                CanonicalCandle(
                    source="binance",
                    symbol="BTCUSDT",
                    source_symbol="BTCUSDT",
                    event_time=et,
                    received_at=et + timedelta(seconds=1),
                    timeframe="1h",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1.0,
                    base_asset="BTC",
                    quote_asset="USDT",
                    canonical_asset="BTC",
                )
            )
        return out

    def fetch_ticker(self, symbol):
        raise NotImplementedError

    def fetch_trades(self, symbol, *, limit=50):
        return []

    def server_time(self):
        return datetime.now(timezone.utc)

    def health(self):
        return ProviderHealth(name=self.name, connected=True)


def test_historical_help():
    runner = CliRunner()
    r = runner.invoke(main, ["historical-data", "--help"])
    assert r.exit_code == 0, r.output
    for sub in ("download", "validate", "status", "snapshot"):
        assert sub in r.output
    r2 = runner.invoke(main, ["historical-data", "download", "--help"])
    assert r2.exit_code == 0
    assert "max-bars" in r2.output
    assert "200" in r2.output  # safe default


def test_download_refuses_huge_max_bars(tmp_path, monkeypatch):
    db = tmp_path / "huge.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    get_settings.cache_clear()
    reset_engine()
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "historical-data",
            "download",
            "--database-url",
            f"sqlite:///{db}",
            "--max-bars",
            "99999",
        ],
    )
    assert r.exit_code != 0
    assert "hard max" in (r.output + str(r.exception)).lower() or r.exception is not None


def test_status_snapshot_validate_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "rt.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, provider=TinyProvider(), source="binance")
        payload = svc.download(
            "BTCUSDT",
            timeframe="1h",
            start=T0,
            end=T0 + timedelta(hours=7),
            max_bars=8,
        )
        did = payload["dataset"]["dataset_id"]
        session.commit()

    runner = CliRunner()
    st = runner.invoke(main, ["historical-data", "status", "--dataset-id", did, "--database-url", url])
    assert st.exit_code == 0, st.output
    assert did in st.output
    assert "test_locked" in st.output
    assert "true" in st.output.lower()

    val = runner.invoke(main, ["historical-data", "validate", "--dataset-id", did, "--database-url", url])
    assert val.exit_code == 0, val.output
    assert "VALID" in val.output
    assert "EDGE_CONFIRMED" in val.output
    assert "false" in val.output.lower()

    snap = runner.invoke(main, ["historical-data", "snapshot", "--dataset-id", did, "--database-url", url])
    assert snap.exit_code == 0, snap.output
    assert "checksum" in snap.output
    assert did in snap.output
