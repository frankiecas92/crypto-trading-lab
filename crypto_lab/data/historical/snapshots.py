"""Reproducible dataset snapshots.

A snapshot answers: exactly which dataset (id, version, source, symbol,
timeframe, range, count, checksum) was used. Not a profitability claim.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from crypto_lab.backtest.evidence import build_historical_download_evidence
from crypto_lab.data.historical.identity import DatasetIdentity
from crypto_lab.data.models import DatasetSnapshot, HistoricalDataset
from crypto_lab.data.repositories import DatasetSnapshotRepository, HistoricalDatasetRepository


def snapshot_payload(identity: DatasetIdentity, *, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    evidence = build_historical_download_evidence(dataset_id=identity.dataset_id)
    body = {
        "dataset_id": identity.dataset_id,
        "data_version": identity.data_version,
        "source": identity.source,
        "source_symbol": identity.source_symbol,
        "symbol": identity.source_symbol,
        "base_asset": identity.base_asset,
        "quote_asset": identity.quote_asset,
        "canonical_asset": identity.canonical_asset,
        "timeframe": identity.timeframe,
        "start": identity.start.isoformat() if identity.start.tzinfo else identity.start.replace(tzinfo=timezone.utc).isoformat(),
        "end": identity.end.isoformat() if identity.end.tzinfo else identity.end.replace(tzinfo=timezone.utc).isoformat(),
        "record_count": identity.record_count,
        "checksum": identity.checksum,
        "git_commit": identity.git_commit,
        "evidence": evidence,
        "EDGE_CONFIRMED": False,
        "PROFITABLE": False,
    }
    if extra:
        body["extra"] = extra
    return body


def create_snapshot(
    session: Session,
    identity: DatasetIdentity,
    *,
    extra: Dict[str, Any] | None = None,
) -> DatasetSnapshot:
    repo = DatasetSnapshotRepository(session)
    payload = snapshot_payload(identity, extra=extra)
    snap_id = f"snap_{identity.dataset_id[:12]}_{uuid.uuid4().hex[:8]}"
    row = DatasetSnapshot(
        snapshot_id=snap_id,
        dataset_id=identity.dataset_id,
        data_version=identity.data_version,
        source=identity.source,
        symbol=identity.source_symbol,
        timeframe=identity.timeframe,
        start_time=identity.start,
        end_time=identity.end,
        record_count=identity.record_count,
        checksum=identity.checksum,
        git_commit=identity.git_commit,
        payload_json=json.dumps(payload, sort_keys=True, default=str),
    )
    repo.add(row)
    session.flush()
    return row


def snapshot_to_dict(row: DatasetSnapshot) -> Dict[str, Any]:
    payload = {}
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            payload = {"raw": row.payload_json}
    return {
        "snapshot_id": row.snapshot_id,
        "dataset_id": row.dataset_id,
        "data_version": row.data_version,
        "source": row.source,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "start": row.start_time.isoformat() if row.start_time else None,
        "end": row.end_time.isoformat() if row.end_time else None,
        "record_count": row.record_count,
        "checksum": row.checksum,
        "git_commit": row.git_commit,
        "payload": payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def identity_from_dataset_row(row: HistoricalDataset) -> DatasetIdentity:
    return DatasetIdentity(
        source=row.source,
        source_symbol=row.source_symbol,
        base_asset=row.base_asset or "",
        quote_asset=row.quote_asset or "",
        canonical_asset=row.canonical_asset or "",
        timeframe=row.timeframe,
        start=row.start_time,
        end=row.end_time,
        record_count=row.record_count,
        data_version=row.data_version,
        checksum=row.checksum,
        git_commit=row.git_commit,
    )
