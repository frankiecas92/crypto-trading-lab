"""Hard safety guard: live trading paths fail closed with SafetyError."""

from __future__ import annotations

from crypto_lab.config import Settings, get_settings
from crypto_lab.exceptions import SafetyError


class LiveTradingBlocked(SafetyError):
    """Raised when any live-trading code path is attempted."""

    def __init__(
        self,
        message: str = "Live trading is blocked. PAPER mode only — live trading blocked.",
    ) -> None:
        super().__init__(message)


def guard_live_trading(settings: Settings | None = None) -> None:
    """Fail closed if LIVE_TRADING is True or MODE is not PAPER.

    Call this at the entry of any future live-order path.
    """
    cfg = settings or get_settings()
    if cfg.live_trading:
        raise LiveTradingBlocked(
            "LIVE_TRADING=True is not allowed. Hard SafetyError — fail closed."
        )
    if cfg.mode.upper() != "PAPER":
        raise LiveTradingBlocked(
            f"MODE={cfg.mode!r} is not allowed. Only PAPER mode is permitted."
        )


def assert_paper_only(settings: Settings | None = None) -> None:
    """Alias for guard_live_trading — explicit paper-only assertion."""
    guard_live_trading(settings)


def require_live_trading_disabled(settings: Settings | None = None) -> None:
    """Explicit check used by health / CLI — raises LiveTradingBlocked if unsafe."""
    guard_live_trading(settings)

