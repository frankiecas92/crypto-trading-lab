"""Optional live public REST historical download (skipped by default)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.historical.service import HistoricalDataService

pytestmark = [pytest.mark.integration, pytest.mark.live]

live_enabled = os.environ.get("RUN_LIVE_DATA_TESTS") == "1"


@pytest.mark.skipif(not live_enabled, reason="Set RUN_LIVE_DATA_TESTS=1 for live public REST")
def test_live_small_1h_download(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'live_hist.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    settings = Settings(_env_file=None)
    assert settings.mode == "PAPER"
    assert settings.live_trading is False
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=12)
    Session = get_session_factory(url)
    with Session() as session:
        svc = HistoricalDataService(session, settings=settings, source="binance")
        try:
            payload = svc.download(
                "BTCUSDT",
                timeframe="1h",
                start=start,
                end=end,
                max_bars=12,
            )
            assert payload["download"]["stored"] >= 1 or payload.get("dataset")
            if payload.get("evidence"):
                assert payload["evidence"]["EDGE_CONFIRMED"] is False
                assert payload["evidence"]["PROFITABLE"] is False
        finally:
            svc.close()
