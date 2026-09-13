"""Data Engine pipeline: RECEIVE → VALIDATE → NORMALIZE → STORE → MONITOR.

Does NOT make trading decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence

from sqlalchemy.orm import Session

from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.monitor import FeedMonitor
from crypto_lab.data.providers.base import DataProvider
from crypto_lab.data.providers.router import FallbackProvider
from crypto_lab.data.records import CanonicalCandle, MarketRecord
from crypto_lab.data.storage import MarketDataStorage
from crypto_lab.data.validator import MarketDataValidator, ValidationIssue

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    stored: int = 0
    rejected: int = 0
    warnings: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    records_stored: List[MarketRecord] = field(default_factory=list)


class DataPipeline:
    """Orchestrates receive → validate → store → monitor for public market data."""

    def __init__(
        self,
        session: Session,
        provider: DataProvider | None = None,
        *,
        settings: Settings | None = None,
        validator: MarketDataValidator | None = None,
        monitor: FeedMonitor | None = None,
        enforce_stale: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.session = session
        self.provider = provider or FallbackProvider.from_settings(self.settings)
        self.validator = validator or MarketDataValidator(self.settings)
        self.storage = MarketDataStorage(session)
        self.monitor = monitor or FeedMonitor(self.provider, settings=self.settings)
        self.enforce_stale = enforce_stale

    def process_record(self, record: MarketRecord) -> bool:
        """Validate + store one record. Returns True if stored."""
        self.monitor.record_received()
        result = self.validator.validate(record)

        for issue in result.issues:
            if issue.severity == "warn":
                self.monitor.record_warning(issue.rule)
                self.storage.log_quality_issue(issue, record=record)
            else:
                self.monitor.metrics.incr_rule(issue.rule)
                if issue.rule == "duplicate_candle":
                    self.monitor.metrics.duplicates += 1
                self.storage.log_quality_issue(issue, record=record)

        if self.enforce_stale:
            stale = self.validator.check_stale(record)
            if stale is not None:
                self.monitor.metrics.incr_rule(stale.rule)
                self.monitor.metrics.rejected += 1
                self.storage.log_quality_issue(stale, record=record)
                return False

        if not result.ok:
            self.monitor.metrics.rejected += 1
            return False

        self.storage.store(record)
        self.monitor.record_stored()
        return True

    def process_many(self, records: Iterable[MarketRecord]) -> PipelineResult:
        out = PipelineResult()
        for rec in records:
            before_rej = self.monitor.metrics.rejected
            before_warn = self.monitor.metrics.warnings
            ok = self.process_record(rec)
            if ok:
                out.stored += 1
                out.records_stored.append(rec)
            else:
                out.rejected += 1
            out.warnings += self.monitor.metrics.warnings - before_warn
            # collect latest issues from validator by re-validating lightly — skip
        self.session.commit()
        return out

    def fetch_and_store_klines(
        self,
        symbols: Sequence[str] | None = None,
        *,
        timeframe: str = "1m",
        limit: int = 50,
    ) -> PipelineResult:
        """RECEIVE (REST) → VALIDATE → STORE for OHLCV."""
        syms = list(symbols or self.settings.symbols)
        all_candles: List[CanonicalCandle] = []
        for sym in syms:
            candles = self.provider.fetch_klines(sym, timeframe=timeframe, limit=limit)
            all_candles.extend(candles)
            # gap detection (warn only)
            for issue in self.validator.detect_gaps(candles, timeframe=timeframe):
                self.monitor.record_warning(issue.rule)
                self.storage.log_quality_issue(issue, record_type="CanonicalCandle")

        # clock skew check vs exchange
        try:
            exch = self.provider.server_time()
            skew = self.validator.check_clock_skew(exchange_time=exch)
            if skew:
                self.monitor.record_warning(skew.rule)
                self.storage.log_quality_issue(skew, record_type="clock")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Clock skew check skipped: %s", exc)

        return self.process_many(all_candles)

    def fetch_and_store_tickers(self, symbols: Sequence[str] | None = None) -> PipelineResult:
        syms = list(symbols or self.settings.symbols)
        quotes = [self.provider.fetch_ticker(s) for s in syms]
        return self.process_many(quotes)

    def health(self) -> dict:
        return self.monitor.status().to_dict()
