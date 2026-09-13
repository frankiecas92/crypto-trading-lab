"""Market data quality validator.

Rules reject bad records; Data Engine never makes trading decisions.
Distinguishes EVENT TIME (exchange) vs RECEIVED TIME (local).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Sequence

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.records import CanonicalCandle, CanonicalQuote, CanonicalTrade, MarketRecord
from crypto_lab.data.symbols import KNOWN_MARKET_SYMBOLS, is_valid_instrument
from crypto_lab.exceptions import DataValidationError


@dataclass
class ValidationIssue:
    rule: str
    message: str
    severity: str = "reject"  # reject | warn
    details: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def reject_issues(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "reject"]


class MarketDataValidator:
    """Validate canonical market records against configurable quality rules."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._last_event_times: dict[tuple[str, str, str], datetime] = {}
        self._seen_keys: set[tuple] = set()

    def reset_state(self) -> None:
        self._last_event_times.clear()
        self._seen_keys.clear()

    def validate(self, record: MarketRecord) -> ValidationResult:
        issues: List[ValidationIssue] = []
        issues.extend(self._check_symbol(record))
        issues.extend(self._check_times(record))
        if isinstance(record, CanonicalCandle):
            issues.extend(self._check_candle(record))
            issues.extend(self._check_duplicate_candle(record))
            issues.extend(self._check_out_of_order_candle(record))
        elif isinstance(record, CanonicalTrade):
            issues.extend(self._check_trade(record))
        elif isinstance(record, CanonicalQuote):
            issues.extend(self._check_quote(record))

        rejects = [i for i in issues if i.severity == "reject"]
        return ValidationResult(ok=len(rejects) == 0, issues=issues)

    def validate_or_raise(self, record: MarketRecord) -> None:
        result = self.validate(record)
        if not result.ok:
            first = result.reject_issues[0]
            raise DataValidationError(
                first.message, rule=first.rule, details=first.details
            )

    def detect_gaps(
        self,
        candles: Sequence[CanonicalCandle],
        *,
        timeframe: str = "1m",
    ) -> List[ValidationIssue]:
        """Detect missing candles in a sorted series for a timeframe."""
        step = _timeframe_to_delta(timeframe)
        if step is None or len(candles) < 2:
            return []
        ordered = sorted(candles, key=lambda c: c.event_time)
        issues: List[ValidationIssue] = []
        for prev, cur in zip(ordered, ordered[1:]):
            expected = prev.event_time + step
            if cur.event_time > expected + timedelta(milliseconds=1):
                issues.append(
                    ValidationIssue(
                        rule="missing_candles",
                        message=(
                            f"Gap between {prev.event_time.isoformat()} and "
                            f"{cur.event_time.isoformat()} (expected step {step})"
                        ),
                        severity="warn",
                        details={
                            "prev": prev.event_time.isoformat(),
                            "curr": cur.event_time.isoformat(),
                            "timeframe": timeframe,
                        },
                    )
                )
        return issues

    def check_clock_skew(
        self,
        *,
        exchange_time: datetime,
        local_time: datetime | None = None,
    ) -> ValidationIssue | None:
        local = local_time or datetime.now(timezone.utc)
        skew_ms = abs((exchange_time - local).total_seconds() * 1000.0)
        limit = float(self.settings.clock_skew_ms)
        if skew_ms > limit:
            return ValidationIssue(
                rule="clock_skew",
                message=f"Clock skew {skew_ms:.0f}ms exceeds {limit:.0f}ms",
                severity="warn",
                details={"skew_ms": skew_ms, "limit_ms": limit},
            )
        return None

    # --- private checks ---

    def _check_symbol(self, record: MarketRecord) -> List[ValidationIssue]:
        if not is_valid_instrument(record.source, record.symbol):
            return [
                ValidationIssue(
                    rule="invalid_symbol",
                    message=(
                        f"Invalid symbol {record.symbol!r} for source {record.source!r}"
                    ),
                    details={
                        "symbol": record.symbol,
                        "source": record.source,
                        "allowed": sorted(KNOWN_MARKET_SYMBOLS),
                    },
                )
            ]
        return []

    def _check_times(self, record: MarketRecord) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        et = record.event_time
        ra = record.received_at
        if et.tzinfo is None or ra.tzinfo is None:
            issues.append(
                ValidationIssue(
                    rule="inconsistent_timestamps",
                    message="event_time and received_at must be timezone-aware UTC",
                )
            )
            return issues

        skew = timedelta(milliseconds=self.settings.future_skew_ms)
        if et > ra + skew:
            issues.append(
                ValidationIssue(
                    rule="future_timestamp",
                    message=(
                        f"event_time {et.isoformat()} is after received_at "
                        f"{ra.isoformat()} + skew"
                    ),
                    details={
                        "event_time": et.isoformat(),
                        "received_at": ra.isoformat(),
                        "future_skew_ms": self.settings.future_skew_ms,
                    },
                )
            )

        age = (ra - et).total_seconds()
        # Delayed/lagging: warn if very old relative to receive (not necessarily reject)
        if age > self.settings.stale_threshold_seconds * 5:
            issues.append(
                ValidationIssue(
                    rule="delayed_timestamp",
                    message=f"event_time lags received_at by {age:.1f}s",
                    severity="warn",
                    details={"lag_seconds": age},
                )
            )

        # Stale: age of event vs now (or received) beyond threshold — reject for live ingest
        now_age = (datetime.now(timezone.utc) - et).total_seconds()
        if now_age > self.settings.stale_threshold_seconds and getattr(
            self, "_enforce_stale", True
        ):
            # Only reject as stale when explicitly checking live freshness via validate_stale
            pass

        return issues

    def check_stale(self, record: MarketRecord, *, now: datetime | None = None) -> ValidationIssue | None:
        ref = now or datetime.now(timezone.utc)
        age = (ref - record.event_time).total_seconds()
        if age > self.settings.stale_threshold_seconds:
            return ValidationIssue(
                rule="stale_data",
                message=f"Data age {age:.1f}s exceeds stale_threshold_seconds="
                f"{self.settings.stale_threshold_seconds}",
                details={"age_seconds": age},
            )
        return None

    def _check_candle(self, c: CanonicalCandle) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        for name, val in (
            ("open", c.open),
            ("high", c.high),
            ("low", c.low),
            ("close", c.close),
        ):
            if val is None or val <= 0 or val != val:  # NaN
                issues.append(
                    ValidationIssue(
                        rule="impossible_price",
                        message=f"Invalid {name} price: {val}",
                        details={"field": name, "value": val},
                    )
                )
        if c.volume < 0:
            issues.append(
                ValidationIssue(
                    rule="invalid_volume",
                    message=f"Volume < 0: {c.volume}",
                    details={"volume": c.volume},
                )
            )
        if c.high < c.low:
            issues.append(
                ValidationIssue(
                    rule="invalid_ohlc",
                    message=f"high ({c.high}) < low ({c.low})",
                )
            )
        if c.high >= c.low:
            if c.open > c.high or c.open < c.low:
                issues.append(
                    ValidationIssue(
                        rule="invalid_ohlc",
                        message=f"open {c.open} outside [{c.low}, {c.high}]",
                    )
                )
            if c.close > c.high or c.close < c.low:
                issues.append(
                    ValidationIssue(
                        rule="invalid_ohlc",
                        message=f"close {c.close} outside [{c.low}, {c.high}]",
                    )
                )
        return issues

    def _check_duplicate_candle(self, c: CanonicalCandle) -> List[ValidationIssue]:
        key = (c.source, c.symbol, c.timeframe, c.event_time.isoformat())
        if key in self._seen_keys:
            return [
                ValidationIssue(
                    rule="duplicate_candle",
                    message=f"Duplicate candle {key}",
                    details={"key": list(key)},
                )
            ]
        self._seen_keys.add(key)
        return []

    def _check_out_of_order_candle(self, c: CanonicalCandle) -> List[ValidationIssue]:
        key = (c.source, c.symbol, c.timeframe)
        last = self._last_event_times.get(key)
        if last is not None and c.event_time < last:
            return [
                ValidationIssue(
                    rule="out_of_order",
                    message=(
                        f"Out-of-order event_time {c.event_time.isoformat()} "
                        f"< last {last.isoformat()}"
                    ),
                    severity="warn",
                    details={
                        "event_time": c.event_time.isoformat(),
                        "last_event_time": last.isoformat(),
                    },
                )
            ]
        if last is None or c.event_time > last:
            self._last_event_times[key] = c.event_time
        return []

    def _check_trade(self, t: CanonicalTrade) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        if t.price is None or t.price <= 0 or t.price != t.price:
            issues.append(
                ValidationIssue(
                    rule="impossible_price",
                    message=f"Invalid trade price: {t.price}",
                )
            )
        if t.quantity < 0:
            issues.append(
                ValidationIssue(
                    rule="invalid_volume",
                    message=f"Trade quantity < 0: {t.quantity}",
                )
            )
        return issues

    def _check_quote(self, q: CanonicalQuote) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        for name, val in (("bid", q.bid), ("ask", q.ask), ("last", q.last)):
            if val is None:
                continue
            if val <= 0 or val != val:
                issues.append(
                    ValidationIssue(
                        rule="impossible_price",
                        message=f"Invalid {name}: {val}",
                    )
                )
        if q.bid is not None and q.ask is not None and q.bid > q.ask:
            issues.append(
                ValidationIssue(
                    rule="bid_gt_ask",
                    message=f"bid ({q.bid}) > ask ({q.ask})",
                    details={"bid": q.bid, "ask": q.ask},
                )
            )
        bps = q.spread_bps
        if bps is not None and bps > self.settings.max_spread_bps:
            issues.append(
                ValidationIssue(
                    rule="abnormal_spread",
                    message=f"Spread {bps:.2f} bps > max_spread_bps={self.settings.max_spread_bps}",
                    details={"spread_bps": bps},
                )
            )
        if q.volume_24h is not None and q.volume_24h < 0:
            issues.append(
                ValidationIssue(
                    rule="invalid_volume",
                    message=f"volume_24h < 0: {q.volume_24h}",
                )
            )
        return issues


def _timeframe_to_delta(tf: str) -> timedelta | None:
    mapping = {
        "1m": timedelta(minutes=1),
        "3m": timedelta(minutes=3),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }
    return mapping.get(tf.lower())
