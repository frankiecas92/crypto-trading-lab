"""Safety guard tests."""

import pytest

from crypto_lab.config import Settings
from crypto_lab.exceptions import NotImplementedPhaseError, SafetyError
from crypto_lab.execution.interfaces import BlockedLiveGateway, OrderRequest
from crypto_lab.execution.safety import LiveTradingBlocked, assert_paper_only, guard_live_trading


def test_guard_passes_in_paper_mode():
    s = Settings(mode="PAPER", live_trading=False)
    guard_live_trading(s)
    assert_paper_only(s)


def test_guard_blocks_live_trading_flag():
    s = Settings(mode="PAPER", live_trading=True)
    with pytest.raises(LiveTradingBlocked) as exc:
        guard_live_trading(s)
    assert isinstance(exc.value, SafetyError)


def test_guard_blocks_non_paper_mode():
    s = Settings(mode="LIVE", live_trading=False)
    with pytest.raises(LiveTradingBlocked):
        guard_live_trading(s)


def test_blocked_gateway_raises():
    s = Settings(mode="PAPER", live_trading=False)
    # With paper settings, guard passes then NotImplementedPhaseError
    from crypto_lab.config.settings import get_settings

    get_settings.cache_clear()
    gw = BlockedLiveGateway()
    with pytest.raises((NotImplementedPhaseError, LiveTradingBlocked)):
        gw.place_order(OrderRequest(symbol="BTCUSDT", side="BUY", quantity=1.0))


def test_blocked_gateway_live_flag():
    from crypto_lab.config.settings import get_settings
    from crypto_lab.execution import safety as safety_mod

    s = Settings(mode="PAPER", live_trading=True)
    with pytest.raises(LiveTradingBlocked):
        # Call guard directly simulating gateway entry
        safety_mod.guard_live_trading(s)

