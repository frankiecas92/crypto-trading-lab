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


class DataError(AppError):
    """Data engine / provider failure (network, parse, stale feed)."""


class DataValidationError(ValidationError):
    """Market data quality rule failure (reject record)."""

    def __init__(
        self,
        message: str = "Data validation failed",
        *,
        rule: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.rule = rule
        self.details = details or {}


class RateLimitError(DataError):
    """HTTP 429 / rate limit from public market data API."""

    def __init__(
        self,
        message: str = "Rate limited",
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderError(DataError):
    """Upstream market-data provider failure."""


class NotImplementedPhaseError(AppError):
    """Feature reserved for a later phase."""
