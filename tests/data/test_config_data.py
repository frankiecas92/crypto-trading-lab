"""Phase 2 settings defaults."""

from crypto_lab.config import Settings, get_settings


def test_data_engine_settings_defaults():
    s = get_settings()
    assert s.primary_provider == "binance"
    assert s.fallback_provider == "coinbase"
    assert s.enable_ws is True
    assert "data-api.binance.vision" in s.binance_rest_base
    assert s.live_trading is False
    assert s.mode == "PAPER"


def test_settings_no_secret_fields():
    fields = Settings.model_fields
    secretish = [k for k in fields if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower()]
    assert secretish == []
