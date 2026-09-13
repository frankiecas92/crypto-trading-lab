"""Shared fixtures."""

from __future__ import annotations

import os

import pytest

from crypto_lab.config.settings import get_settings
from crypto_lab.data.database import reset_engine


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_engine(tmp_path, monkeypatch):
    """Isolate settings/engine per test; use temp sqlite DB."""
    db_path = tmp_path / "test_lab.db"
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("SYMBOLS", "BTCUSDT,ETHUSDT")
    get_settings.cache_clear()
    reset_engine()
    yield
    get_settings.cache_clear()
    reset_engine()

