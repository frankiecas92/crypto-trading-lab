"""Dataset-level TRAIN / VALIDATION / TEST roles with TEST LOCKED.

Extends Phase 3 ResearchSplit conceptually (same confirm='EVALUATE_OOS',
same SplitLeakageError, same ResearchPhase). Does not change the Phase 3 API.

Param selection cannot use locked TEST.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

from sqlalchemy.orm import Session

from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, SplitSpec, chronological_split
from crypto_lab.backtest.types import Bar
from crypto_lab.data.historical.constants import (
    EVALUATE_OOS,
    SPLIT_TEST,
    SPLIT_TRAIN,
    SPLIT_VALIDATION,
)
from crypto_lab.data.models import HistoricalDataset
from crypto_lab.data.records import CanonicalCandle
from crypto_lab.data.repositories import HistoricalDatasetRepository
from crypto_lab.exceptions import SplitLeakageError


def candles_to_bars(candles: Sequence[CanonicalCandle]) -> List[Bar]:
    """Map canonical candles to Phase 3 Bar (event_time preserved)."""
    out: List[Bar] = []
    for c in candles:
        out.append(
            Bar(
                symbol=c.symbol,
                event_time=c.event_time,
                timeframe=c.timeframe,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                source=c.source,
                received_at=c.received_at,
            )
        )
    return out


def research_split_for_candles(
    candles: Sequence[CanonicalCandle],
    *,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
    test_frac: float = 0.2,
) -> ResearchSplit:
    """Reuse Phase 3 ResearchSplit on a dataset series (TEST locked)."""
    bars = candles_to_bars(candles)
    spec = chronological_split(len(bars), train_frac=train_frac, val_frac=val_frac, test_frac=test_frac)
    return ResearchSplit(bars, spec)


class DatasetSplitCatalog:
    """Attach / enforce TRAIN/VALIDATION/TEST roles on a catalogued dataset."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = HistoricalDatasetRepository(session)
        self._runtime: dict[str, ResearchSplit] = {}

    def _row(self, dataset_id: str) -> HistoricalDataset:
        row = self.repo.find_by_dataset_id(dataset_id)
        if row is None:
            raise KeyError(f"unknown dataset_id={dataset_id!r}")
        return row

    def attach_roles(
        self,
        dataset_id: str,
        candles: Sequence[CanonicalCandle],
        *,
        train_frac: float = 0.6,
        val_frac: float = 0.2,
        test_frac: float = 0.2,
        spec: SplitSpec | None = None,
    ) -> dict:
        """Store split roles. TEST remains locked until EVALUATE_OOS."""
        bars = candles_to_bars(candles)
        split_spec = spec or chronological_split(
            len(bars), train_frac=train_frac, val_frac=val_frac, test_frac=test_frac
        )
        rs = ResearchSplit(bars, split_spec)
        self._runtime[dataset_id] = rs
        row = self._row(dataset_id)
        row.split_json = json.dumps(rs.period_dict(), default=str)
        row.test_locked = True
        self.session.flush()
        return rs.period_dict()

    def _split(self, dataset_id: str) -> ResearchSplit:
        if dataset_id in self._runtime:
            return self._runtime[dataset_id]
        raise SplitLeakageError(
            f"No in-memory split attached for {dataset_id!r}; call attach_roles first"
        )

    def is_test_locked(self, dataset_id: str) -> bool:
        if dataset_id in self._runtime:
            return self._runtime[dataset_id].test_locked
        row = self._row(dataset_id)
        return bool(row.test_locked)

    def set_phase(self, dataset_id: str, phase: ResearchPhase | str) -> None:
        self._split(dataset_id).set_phase(phase)

    def candles_for_role(
        self,
        dataset_id: str,
        role: str,
        candles: Sequence[CanonicalCandle] | None = None,
        *,
        confirm: str | None = None,
        current_parameters: Dict[str, Any] | None = None,
    ) -> List[CanonicalCandle]:
        """Return candles for TRAIN / VALIDATION / TEST.

        TEST requires unlock (confirm='EVALUATE_OOS'). Param selection / TRAIN
        phase cannot read TEST.
        """
        role_u = role.strip().upper()
        rs = self._split(dataset_id)
        row = self._row(dataset_id)

        if role_u == SPLIT_TEST:
            if rs.test_locked or row.test_locked:
                if confirm != EVALUATE_OOS:
                    raise SplitLeakageError(
                        "TEST split is locked. Call unlock_test(confirm='EVALUATE_OOS') "
                        "only for final OOS eval."
                    )
                if rs.phase in {ResearchPhase.TRAIN, ResearchPhase.PARAM_SELECTION}:
                    raise SplitLeakageError(
                        f"TEST is locked and cannot be used during {rs.phase.value} "
                        "(param selection / train)"
                    )
                self.unlock_test(dataset_id, confirm=EVALUATE_OOS)
            elif rs.phase in {ResearchPhase.TRAIN, ResearchPhase.PARAM_SELECTION}:
                raise SplitLeakageError(
                    f"TEST cannot be used during {rs.phase.value} (param selection / train)"
                )
            bars = rs.test_bars(current_parameters=current_parameters)
        elif role_u == SPLIT_TRAIN:
            bars = rs.train_bars()
        elif role_u in {SPLIT_VALIDATION, "VAL"}:
            bars = rs.validation_bars()
        else:
            raise ValueError(f"Unknown split role {role!r}")

        wanted = {b.event_time for b in bars}
        src = list(candles) if candles is not None else []
        if src:
            return [c for c in src if c.event_time in wanted]
        # Reconstruct minimal candles from bars
        return [
            CanonicalCandle(
                source=b.source,
                symbol=b.symbol,
                event_time=b.event_time,
                received_at=b.received_at or b.event_time,
                timeframe=b.timeframe,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars
        ]

    def select_parameters(
        self,
        dataset_id: str,
        *,
        using_role: str = SPLIT_TRAIN,
    ) -> List[CanonicalCandle]:
        """Parameter selection may use TRAIN (or VALIDATION), never TEST."""
        role_u = using_role.strip().upper()
        if role_u == SPLIT_TEST:
            raise SplitLeakageError("Param selection cannot use locked TEST data")
        rs = self._split(dataset_id)
        rs.set_phase(ResearchPhase.PARAM_SELECTION)
        return self.candles_for_role(dataset_id, role_u)

    def unlock_test(
        self,
        dataset_id: str,
        *,
        confirm: str,
        locked_parameters: Dict[str, Any] | None = None,
        allow_during_development: bool = False,
    ) -> None:
        rs = self._split(dataset_id)
        rs.unlock_test(
            confirm=confirm,
            locked_parameters=locked_parameters,
            allow_during_development=allow_during_development,
        )
        row = self._row(dataset_id)
        row.test_locked = False
        row.split_json = json.dumps(rs.period_dict(), default=str)
        self.session.flush()

    def status(self, dataset_id: str) -> dict:
        row = self._row(dataset_id)
        payload: Dict[str, Any] = {
            "dataset_id": dataset_id,
            "test_locked": bool(row.test_locked),
            "split": json.loads(row.split_json) if row.split_json else None,
        }
        if dataset_id in self._runtime:
            payload["phase"] = self._runtime[dataset_id].phase.value
            payload["test_locked"] = self._runtime[dataset_id].test_locked
            payload["split"] = self._runtime[dataset_id].period_dict()
        return payload
