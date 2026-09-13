"""Dataset-level quality: VALID / VALID_WITH_WARNINGS / INVALID.

Reuses MarketDataValidator for per-candle rules. Critical errors
(invalid OHLC, future timestamps, negatives, duplicates, bad symbols)
never yield VALID. Gaps are recorded — never filled / imputed as market data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from crypto_lab.backtest.timeframes import TIMEFRAME_MS
from crypto_lab.data.historical.constants import (
    CRITICAL_RULES,
    HISTORICAL_IGNORED_RULES,
    QUALITY_INVALID,
    QUALITY_VALID,
    QUALITY_WARN,
)
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.validator import MarketDataValidator, ValidationIssue


@dataclass
class GapRecord:
    gap_start: datetime
    gap_end: datetime
    expected_records: int
    actual_records: int
    duration_seconds: float
    severity: str
    message: str

    def to_dict(self) -> dict:
        return {
            "gap_start": self.gap_start.isoformat(),
            "gap_end": self.gap_end.isoformat(),
            "expected_records": self.expected_records,
            "actual_records": self.actual_records,
            "duration_seconds": self.duration_seconds,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class DatasetQualityReport:
    status: str
    critical_errors: int
    warnings: int
    gap_count: int
    issues: List[ValidationIssue] = field(default_factory=list)
    gaps: List[GapRecord] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "critical_errors": self.critical_errors,
            "warnings": self.warnings,
            "gap_count": self.gap_count,
            "issues": [
                {"rule": i.rule, "severity": i.severity, "message": i.message}
                for i in self.issues
            ],
            "gaps": [g.to_dict() for g in self.gaps],
            "notes": list(self.notes),
            "imputation": False,
            "invented_fills": False,
        }


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _tf_delta(timeframe: str) -> timedelta:
    key = timeframe.strip().lower()
    if key not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    return timedelta(milliseconds=TIMEFRAME_MS[key])


def _collect_gaps(
    candles: Sequence[CanonicalCandle],
    *,
    timeframe: str,
) -> List[GapRecord]:
    if len(candles) < 2:
        return []
    step = _tf_delta(timeframe)
    ordered = sorted(candles, key=lambda c: _aware(c.event_time))
    gaps: List[GapRecord] = []
    for prev, cur in zip(ordered, ordered[1:]):
        prev_et = _aware(prev.event_time)
        cur_et = _aware(cur.event_time)
        expected = prev_et + step
        if cur_et > expected + timedelta(milliseconds=1):
            missing = int(round((cur_et - prev_et) / step)) - 1
            if missing < 1:
                missing = 1
            duration = (cur_et - prev_et).total_seconds()
            # Large holes are still warnings — not invented. Critical only if empty set.
            severity = "warn"
            gaps.append(
                GapRecord(
                    gap_start=prev_et + step,
                    gap_end=cur_et,
                    expected_records=missing,
                    actual_records=0,
                    duration_seconds=duration,
                    severity=severity,
                    message=(
                        f"Gap {prev_et.isoformat()} → {cur_et.isoformat()} "
                        f"(expected {missing} {timeframe} bars; not imputed)"
                    ),
                )
            )
    return gaps


def evaluate_dataset_quality(
    candles: Sequence[CanonicalCandle],
    *,
    timeframe: str,
    now: datetime | None = None,
    validator: MarketDataValidator | None = None,
) -> DatasetQualityReport:
    """Score a candle series. Does not write fills or interpolate gaps."""
    now = now or datetime.now(timezone.utc)
    v = validator or MarketDataValidator()
    v.reset_state()
    issues: List[ValidationIssue] = []
    notes = [
        "No invented fills or imputation are treated as real market data.",
        "event_time is exchange time; received_at is local receive time (kept distinct).",
    ]

    for c in candles:
        result = v.validate(c)
        for issue in result.issues:
            if issue.rule in HISTORICAL_IGNORED_RULES:
                continue
            # Historical: also reject event_time in the future vs wall clock
            issues.append(issue)
        et = _aware(c.event_time)
        ra = _aware(c.received_at)
        if et > _aware(now) + timedelta(seconds=2):
            issues.append(
                ValidationIssue(
                    rule="future_timestamp",
                    message=f"event_time {et.isoformat()} is in the future vs now",
                    severity="reject",
                    details={"event_time": et.isoformat()},
                )
            )
        if et == ra:
            # Distinct columns must exist; equality is allowed but noted.
            notes.append("event_time equals received_at on at least one bar (unusual for history)")

    gaps = _collect_gaps(candles, timeframe=timeframe)
    # Also reuse validator gap detector (warn issues)
    for gi in v.detect_gaps(list(candles), timeframe=timeframe):
        # already captured as GapRecord; keep issue list in sync
        if not any(i.rule == "missing_candles" and i.message == gi.message for i in issues):
            issues.append(gi)

    critical = [i for i in issues if i.rule in CRITICAL_RULES or i.severity == "reject"]
    # missing_candles is warn
    critical = [i for i in critical if i.rule != "missing_candles"]
    warns = [i for i in issues if i not in critical]
    warns += [
        ValidationIssue(
            rule="missing_candles",
            message=g.message,
            severity="warn",
            details=g.to_dict(),
        )
        for g in gaps
        if not any(i.message == g.message for i in issues)
    ]

    if not candles:
        critical.append(
            ValidationIssue(
                rule="empty_dataset",
                message="Dataset has zero candles",
                severity="reject",
            )
        )

    if critical:
        status = QUALITY_INVALID
    elif warns or gaps:
        status = QUALITY_WARN
    else:
        status = QUALITY_VALID

    return DatasetQualityReport(
        status=status,
        critical_errors=len(critical),
        warnings=len(warns) + len(gaps) if not any(i.rule == "missing_candles" for i in warns) else len(warns),
        gap_count=len(gaps),
        issues=issues,
        gaps=gaps,
        notes=notes,
    )
