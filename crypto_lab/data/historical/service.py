"""Facade: download → validate → register → snapshot / status.

Public history only. Never EDGE_CONFIRMED or PROFITABLE.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

from sqlalchemy.orm import Session

from crypto_lab.backtest.evidence import build_historical_download_evidence
from crypto_lab.backtest.registry import current_git_commit
from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.data.historical.catalog import DatasetSplitCatalog
from crypto_lab.data.historical.constants import DATA_VERSION, DEFAULT_TIMEFRAME
from crypto_lab.data.historical.downloader import DownloadResult, HistoricalDownloader
from crypto_lab.data.historical.identity import DatasetIdentity, compute_dataset_identity
from crypto_lab.data.historical.quality import DatasetQualityReport, evaluate_dataset_quality
from crypto_lab.data.historical.snapshots import (
    create_snapshot,
    identity_from_dataset_row,
    snapshot_to_dict,
)
from crypto_lab.data.models import DatasetGap, DatasetQualitySummary, HistoricalDataset
from crypto_lab.data.providers.base import DataProvider
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.repositories import (
    DatasetGapRepository,
    DatasetQualitySummaryRepository,
    DatasetSnapshotRepository,
    HistoricalDatasetRepository,
    MarketDataRepository,
)
from crypto_lab.data.symbols import parse_instrument
from crypto_lab.exceptions import ValidationError

logger = logging.getLogger(__name__)


def _ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _rows_to_candles(rows) -> List[CanonicalCandle]:
    out: List[CanonicalCandle] = []
    for r in rows:
        et = _ensure_utc(r.event_time or r.ts)
        ra = _ensure_utc(r.received_at or r.created_at or et)
        out.append(
            CanonicalCandle(
                source=r.source,
                symbol=r.symbol,
                event_time=et,
                received_at=ra,
                timeframe=r.timeframe,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                source_symbol=r.source_symbol or r.symbol,
                base_asset=r.base_asset,
                quote_asset=r.quote_asset,
                canonical_asset=r.canonical_asset,
            )
        )
    return out


class HistoricalDataService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider: DataProvider | None = None,
        source: str = "binance",
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.source = source.strip().lower()
        self.downloader = HistoricalDownloader(
            session, settings=self.settings, provider=provider, source=self.source
        )
        self.datasets = HistoricalDatasetRepository(session)
        self.gaps = DatasetGapRepository(session)
        self.quality_repo = DatasetQualitySummaryRepository(session)
        self.snapshots = DatasetSnapshotRepository(session)
        self.candles = MarketDataRepository(session)
        self.catalog = DatasetSplitCatalog(session)

    def close(self) -> None:
        self.downloader.close()

    def download(
        self,
        symbol: str,
        *,
        timeframe: str = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
        max_bars: int | None = None,
        resume: bool = True,
        register: bool = True,
    ) -> Dict[str, Any]:
        result = self.downloader.download(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            max_bars=max_bars,
            resume=resume,
        )
        inst = parse_instrument(result.source, result.source_symbol)
        stored = self.candles.list_candles_range(
            source=result.source,
            symbol=inst.source_symbol,
            timeframe=result.timeframe,
            start=result.start,
            end=result.end,
        )
        candles = _rows_to_candles(stored)
        payload: Dict[str, Any] = {
            "download": result.to_dict(),
            "MODE": self.settings.mode,
            "LIVE_TRADING": self.settings.live_trading,
        }
        if register and candles:
            identity, quality, row = self._register(
                candles,
                source=result.source,
                source_symbol=inst.source_symbol,
                timeframe=result.timeframe,
                start=result.start,
                end=result.end,
            )
            payload["dataset"] = identity.to_dict()
            payload["quality"] = quality.to_dict()
            payload["evidence"] = json.loads(row.evidence_json or "{}")
        elif register and not candles:
            payload["dataset"] = None
            payload["note"] = "nothing stored — no dataset registered"
        return payload

    def validate(
        self,
        *,
        dataset_id: str | None = None,
        source: str | None = None,
        symbol: str | None = None,
        timeframe: str = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Dict[str, Any]:
        candles, meta = self._load_candles(
            dataset_id=dataset_id,
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        if not candles:
            raise ValidationError("no candles found to validate")
        tf = meta["timeframe"]
        quality = evaluate_dataset_quality(candles, timeframe=tf)
        src = meta["source"]
        sym = meta["source_symbol"]
        identity = compute_dataset_identity(
            candles,
            source=src,
            source_symbol=sym,
            timeframe=tf,
            start=meta["start"],
            end=meta["end"],
            data_version=getattr(self.settings, "historical_data_version", DATA_VERSION),
            git_commit=None,
        )
        # If a catalog row exists, persist quality against its dataset_id
        row = None
        if dataset_id:
            row = self.datasets.find_by_dataset_id(dataset_id)
        if row is None:
            row = self.datasets.find_by_dataset_id(identity.dataset_id)
        if row is not None:
            self._persist_quality(row.dataset_id, quality)
            row.quality_status = quality.status
            row.quality_json = json.dumps(quality.to_dict(), default=str)
            self.session.flush()
        return {
            "dataset_id": row.dataset_id if row else identity.dataset_id,
            "identity": identity.to_dict(),
            "quality": quality.to_dict(),
            "record_count": len(candles),
            "evidence": build_historical_download_evidence(
                dataset_id=row.dataset_id if row else identity.dataset_id
            ),
        }

    def status(self, dataset_id: str | None = None) -> Dict[str, Any]:
        if dataset_id:
            row = self.datasets.find_by_dataset_id(dataset_id)
            if row is None:
                raise ValidationError(f"unknown dataset_id={dataset_id!r}")
            return self._status_one(row)
        rows = self.datasets.list_datasets(limit=50)
        return {
            "count": len(rows),
            "datasets": [self._status_one(r) for r in rows],
            "MODE": self.settings.mode,
            "LIVE_TRADING": self.settings.live_trading,
        }

    def snapshot(self, dataset_id: str) -> Dict[str, Any]:
        row = self.datasets.find_by_dataset_id(dataset_id)
        if row is None:
            raise ValidationError(f"unknown dataset_id={dataset_id!r}")
        identity = identity_from_dataset_row(row)
        # Recompute checksum from stored candles to confirm reproducibility
        stored = self.candles.list_candles_range(
            source=row.source,
            symbol=row.symbol,
            timeframe=row.timeframe,
            start=row.start_time,
            end=row.end_time,
        )
        candles = _rows_to_candles(stored)
        live = compute_dataset_identity(
            candles,
            source=row.source,
            source_symbol=row.source_symbol,
            timeframe=row.timeframe,
            start=row.start_time,
            end=row.end_time,
            data_version=row.data_version,
            git_commit=row.git_commit,
        )
        snap = create_snapshot(
            self.session,
            identity,
            extra={"recomputed_checksum": live.checksum, "match": live.checksum == identity.checksum},
        )
        self.session.commit()
        return snapshot_to_dict(snap)

    def _register(
        self,
        candles: Sequence[CanonicalCandle],
        *,
        source: str,
        source_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[DatasetIdentity, DatasetQualityReport, HistoricalDataset]:
        version = getattr(self.settings, "historical_data_version", DATA_VERSION)
        identity = compute_dataset_identity(
            candles,
            source=source,
            source_symbol=source_symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            data_version=version,
            git_commit=current_git_commit(),
        )
        quality = evaluate_dataset_quality(candles, timeframe=timeframe)
        evidence = build_historical_download_evidence(dataset_id=identity.dataset_id)
        existing = self.datasets.find_by_dataset_id(identity.dataset_id)
        now = datetime.now(timezone.utc)
        if existing is None:
            existing = HistoricalDataset(
                dataset_id=identity.dataset_id,
                source=identity.source,
                source_symbol=identity.source_symbol,
                symbol=identity.source_symbol,
                base_asset=identity.base_asset,
                quote_asset=identity.quote_asset,
                canonical_asset=identity.canonical_asset,
                timeframe=identity.timeframe,
                start_time=identity.start,
                end_time=identity.end,
                record_count=identity.record_count,
                data_version=identity.data_version,
                git_commit=identity.git_commit,
                checksum=identity.checksum,
                quality_status=quality.status,
                quality_json=json.dumps(quality.to_dict(), default=str),
                test_locked=True,
                evidence_json=json.dumps(evidence, default=str),
                created_at=now,
                updated_at=now,
            )
            self.datasets.add(existing)
        else:
            existing.record_count = identity.record_count
            existing.checksum = identity.checksum
            existing.quality_status = quality.status
            existing.quality_json = json.dumps(quality.to_dict(), default=str)
            existing.evidence_json = json.dumps(evidence, default=str)
            existing.updated_at = now
        self._persist_quality(identity.dataset_id, quality)
        self.session.commit()
        logger.info(
            "Registered dataset %s status=%s count=%s (TEST locked, no edge claim)",
            identity.dataset_id,
            quality.status,
            identity.record_count,
        )
        return identity, quality, existing

    def _persist_quality(self, dataset_id: str, quality: DatasetQualityReport) -> None:
        # replace gaps for this dataset
        for g in list(self.gaps.list_for_dataset(dataset_id)):
            self.session.delete(g)
        for g in quality.gaps:
            self.gaps.add(
                DatasetGap(
                    dataset_id=dataset_id,
                    gap_start=g.gap_start,
                    gap_end=g.gap_end,
                    expected_records=g.expected_records,
                    actual_records=g.actual_records,
                    duration_seconds=g.duration_seconds,
                    severity=g.severity,
                    message=g.message,
                )
            )
        self.quality_repo.add(
            DatasetQualitySummary(
                dataset_id=dataset_id,
                status=quality.status,
                critical_errors=quality.critical_errors,
                warnings=quality.warnings,
                gap_count=quality.gap_count,
                summary_json=json.dumps(quality.to_dict(), default=str),
            )
        )
        self.session.flush()

    def _status_one(self, row: HistoricalDataset) -> Dict[str, Any]:
        gap_rows = self.gaps.list_for_dataset(row.dataset_id)
        q = self.quality_repo.latest_for_dataset(row.dataset_id)
        snaps = self.snapshots.list_for_dataset(row.dataset_id)
        return {
            "dataset_id": row.dataset_id,
            "source": row.source,
            "symbol": row.symbol,
            "source_symbol": row.source_symbol,
            "base_asset": row.base_asset,
            "quote_asset": row.quote_asset,
            "canonical_asset": row.canonical_asset,
            "timeframe": row.timeframe,
            "start": row.start_time.isoformat() if row.start_time else None,
            "end": row.end_time.isoformat() if row.end_time else None,
            "record_count": row.record_count,
            "data_version": row.data_version,
            "checksum": row.checksum,
            "git_commit": row.git_commit,
            "quality_status": row.quality_status,
            "test_locked": bool(row.test_locked),
            "gap_count": len(list(gap_rows)),
            "snapshots": len(list(snaps)),
            "quality_summary_status": q.status if q else None,
            "evidence": json.loads(row.evidence_json) if row.evidence_json else None,
        }

    def _load_candles(
        self,
        *,
        dataset_id: str | None,
        source: str | None,
        symbol: str | None,
        timeframe: str,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[List[CanonicalCandle], dict]:
        if dataset_id:
            row = self.datasets.find_by_dataset_id(dataset_id)
            if row is None:
                raise ValidationError(f"unknown dataset_id={dataset_id!r}")
            stored = self.candles.list_candles_range(
                source=row.source,
                symbol=row.symbol,
                timeframe=row.timeframe,
                start=row.start_time,
                end=row.end_time,
            )
            return _rows_to_candles(stored), {
                "source": row.source,
                "source_symbol": row.source_symbol,
                "timeframe": row.timeframe,
                "start": row.start_time,
                "end": row.end_time,
            }
        if not source or not symbol:
            raise ValidationError("provide dataset_id or source+symbol")
        inst = parse_instrument(source, symbol)
        if start is None or end is None:
            raise ValidationError("start and end required when validating without dataset_id")
        stored = self.candles.list_candles_range(
            source=inst.source,
            symbol=inst.source_symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        return _rows_to_candles(stored), {
            "source": inst.source,
            "source_symbol": inst.source_symbol,
            "timeframe": timeframe,
            "start": start,
            "end": end,
        }
