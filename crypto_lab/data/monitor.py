"""Feed monitor — connection, heartbeat, stale detection, metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.providers.base import DataProvider, ProviderHealth

logger = logging.getLogger(__name__)


@dataclass
class FeedMetrics:
    received: int = 0
    stored: int = 0
    rejected: int = 0
    duplicates: int = 0
    warnings: int = 0
    by_rule: Dict[str, int] = field(default_factory=dict)

    def incr_rule(self, rule: str) -> None:
        self.by_rule[rule] = self.by_rule.get(rule, 0) + 1

    def to_dict(self) -> dict:
        return {
            "received": self.received,
            "stored": self.stored,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "warnings": self.warnings,
            "by_rule": dict(self.by_rule),
        }


@dataclass
class FeedStatus:
    healthy: bool
    provider: dict
    metrics: dict
    checked_at: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "provider": self.provider,
            "metrics": self.metrics,
            "checked_at": self.checked_at,
            "notes": self.notes,
        }


class FeedMonitor:
    """MONITOR stage — track feed health and pipeline metrics."""

    def __init__(
        self,
        provider: DataProvider | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.metrics = FeedMetrics()
        self._errors: List[str] = []

    def record_received(self) -> None:
        self.metrics.received += 1

    def record_stored(self) -> None:
        self.metrics.stored += 1

    def record_rejected(self, rule: str) -> None:
        self.metrics.rejected += 1
        self.metrics.incr_rule(rule)
        if rule == "duplicate_candle":
            self.metrics.duplicates += 1

    def record_warning(self, rule: str) -> None:
        self.metrics.warnings += 1
        self.metrics.incr_rule(rule)

    def record_error(self, message: str) -> None:
        self._errors.append(message)
        logger.error("Feed error: %s", message)

    def status(self) -> FeedStatus:
        notes: List[str] = []
        provider_health: ProviderHealth
        if self.provider is not None:
            provider_health = self.provider.health()
        else:
            provider_health = ProviderHealth(name="none", connected=False)

        healthy = True
        if provider_health.stale:
            healthy = False
            notes.append("provider feed is stale")
        if provider_health.last_error:
            notes.append(f"last_error={provider_health.last_error}")
        if self._errors:
            notes.append(f"recent_errors={len(self._errors)}")

        # REST-only usage can be healthy without WS connected
        return FeedStatus(
            healthy=healthy,
            provider=provider_health.to_dict(),
            metrics=self.metrics.to_dict(),
            checked_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )
