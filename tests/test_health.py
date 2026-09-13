"""Health monitoring tests."""

from crypto_lab.config import Settings
from crypto_lab.monitoring.health import run_health_checks


def test_health_passes_in_paper_mode(tmp_path, monkeypatch):
    db = tmp_path / "health.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    from crypto_lab.config.settings import get_settings
    from crypto_lab.data.database import reset_engine

    get_settings.cache_clear()
    reset_engine()
    status = run_health_checks()
    assert status.healthy is True
    assert status.mode == "PAPER"
    assert status.live_trading is False
    names = {c.name: c.ok for c in status.checks}
    assert names["mode_paper"] is True
    assert names["live_trading_false"] is True
    assert names["database_reachable"] is True
    assert names["schema_present"] is True


def test_health_fails_when_live_trading(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_TRADING", "TRUE")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h2.db'}")
    from crypto_lab.config.settings import get_settings
    from crypto_lab.data.database import reset_engine

    get_settings.cache_clear()
    reset_engine()
    status = run_health_checks()
    assert status.healthy is False
    assert status.live_trading is True

