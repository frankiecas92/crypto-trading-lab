"""Time sync utilities — local UTC helper + placeholder for exchange offset later.

No network calls required. Phase 1 tracks clock offset locally only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


@dataclass
class ClockOffsetTracker:
    """Track local vs reference clock offset (ms).

    Phase 1: local-only stub. Future phases may set offset from exchange server time
    without this module performing network I/O by itself.
    """

    offset_ms: float = 0.0
    last_sync_at: Optional[datetime] = field(default=None)
    source: str = "local"

    def set_offset(self, offset_ms: float, *, source: str = "manual") -> None:
        """Update known offset (e.g. after an external sync)."""
        self.offset_ms = float(offset_ms)
        self.last_sync_at = utc_now()
        self.source = source

    def corrected_utc(self) -> datetime:
        """UTC now adjusted by tracked offset (placeholder for exchange-aligned time)."""
        from datetime import timedelta

        return utc_now() + timedelta(milliseconds=self.offset_ms)

    def status(self) -> dict:
        return {
            "offset_ms": self.offset_ms,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "source": self.source,
            "local_utc": utc_now().isoformat(),
        }


# Module-level default tracker
default_clock = ClockOffsetTracker()

