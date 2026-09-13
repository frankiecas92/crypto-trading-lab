"""Config defaults tests."""

from crypto_lab.config import Settings, get_settings


def test_defaults_mode_paper_and_live_false():
    s = get_settings()
    assert s.mode == "PAPER"
    assert s.live_trading is False


def test_default_symbols():
    s = get_settings()
    assert s.symbols == ["BTCUSDT", "ETHUSDT"]


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    get_settings.cache_clear()
    s = get_settings()
    assert s.mode == "PAPER"
    assert s.live_trading is False
    assert "SOLUSDT" in s.symbols


def test_live_trading_string_true_parses(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "TRUE")
    get_settings.cache_clear()
    s = get_settings()
    assert s.live_trading is True


def test_settings_instance_defaults_without_env(monkeypatch):
    for key in ("MODE", "LIVE_TRADING", "DATABASE_URL", "LOG_LEVEL", "SYMBOLS"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    # Construct fresh without relying on cached env from fixture after clear
    s = Settings(_env_file=None)
    assert s.mode == "PAPER"
    assert s.live_trading is False
    assert s.symbols == ["BTCUSDT", "ETHUSDT"]

