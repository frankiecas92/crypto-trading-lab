"""Time sync stub tests (no network)."""

from crypto_lab.time_sync import ClockOffsetTracker, utc_now


def test_utc_now_aware():
    now = utc_now()
    assert now.tzinfo is not None


def test_offset_tracker():
    clock = ClockOffsetTracker()
    assert clock.offset_ms == 0.0
    clock.set_offset(150.0, source="test")
    assert clock.offset_ms == 150.0
    assert clock.source == "test"
    status = clock.status()
    assert "local_utc" in status
    corrected = clock.corrected_utc()
    assert corrected.tzinfo is not None

