"""Custom exception hierarchy for the trading lab."""

from __future__ import annotations


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str = "Application error") -> None:
        self.message = message
        super().__init__(message)


class ConfigError(AppError):
    """Invalid or missing configuration."""


class DatabaseError(AppError):
    """Database connectivity or schema error."""


class SafetyError(AppError):
    """Hard safety guard failure (e.g. live trading path invoked).

    Fail closed: any attempt to enable or execute live trading must raise this.
    """


class ValidationError(AppError):
    """Input / domain validation failure."""


class NotImplementedPhaseError(AppError):
    """Feature reserved for a later phase."""

