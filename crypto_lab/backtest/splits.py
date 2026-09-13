"""TRAIN (IS) / VALIDATION / TEST (OOS) splits with leakage guards.

TEST is locked during strategy-development APIs. Unlock only with
confirm='EVALUATE_OOS'. Normalizers must be fit on TRAIN only.

Hardening: EVALUATE_OOS is not usable during TRAIN / PARAM_SELECTION.
If TEST is peeked then parameters change, re-TEST is rejected or a
VIOLATION is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.types import Bar
from crypto_lab.exceptions import SplitLeakageError


class ResearchPhase(str, Enum):
    """Development phases — EVALUATE_OOS is final only."""

    TRAIN = "TRAIN"
    PARAM_SELECTION = "PARAM_SELECTION"
    VALIDATION = "VALIDATION"
    EVALUATE_OOS = "EVALUATE_OOS"


@dataclass(frozen=True)
class SplitSpec:
    train_start: int
    train_end: int  # exclusive
    val_start: int
    val_end: int
    test_start: int
    test_end: int

    def validate(self, n: int) -> None:
        ranges = [
            ("train", self.train_start, self.train_end),
            ("val", self.val_start, self.val_end),
            ("test", self.test_start, self.test_end),
        ]
        for name, a, b in ranges:
            if not (0 <= a <= b <= n):
                raise SplitLeakageError(f"invalid {name} range [{a}, {b}) for n={n}")
        # TEST must not overlap TRAIN (VAL may sit between)
        if not (self.train_end <= self.val_start or self.val_end <= self.train_start):
            # allow empty val
            if self.val_start != self.val_end:
                if self.train_start < self.val_end and self.val_start < self.train_end:
                    raise SplitLeakageError("TRAIN overlaps VALIDATION")
        if self.train_start < self.test_end and self.test_start < self.train_end:
            if self.test_start != self.test_end and self.train_start != self.train_end:
                raise SplitLeakageError("TRAIN overlaps TEST — leakage")
        if self.val_start < self.test_end and self.test_start < self.val_end:
            if self.val_start != self.val_end and self.test_start != self.test_end:
                raise SplitLeakageError("VALIDATION overlaps TEST — leakage")


def chronological_split(
    n: int,
    *,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
    test_frac: float = 0.2,
) -> SplitSpec:
    if abs(train_frac + val_frac + test_frac - 1.0) > 1e-9:
        raise ValueError("fractions must sum to 1")
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    n_test = n - n_train - n_val
    spec = SplitSpec(0, n_train, n_train, n_train + n_val, n_train + n_val, n)
    spec.validate(n)
    return spec


def slice_bars(bars: Sequence[Bar], start: int, end: int) -> List[Bar]:
    return list(bars[start:end])


def _params_fingerprint(params: Dict[str, Any] | None) -> str:
    if params is None:
        return ""
    import json

    return json.dumps(params, sort_keys=True, default=str)


class ResearchSplit:
    """Holds a bar series and locked TEST partition with OOS hardening."""

    def __init__(self, bars: Sequence[Bar], spec: SplitSpec) -> None:
        spec.validate(len(bars))
        self._bars = list(bars)
        self.spec = spec
        self._test_locked = True
        self._phase: ResearchPhase = ResearchPhase.TRAIN
        self._locked_parameters: Dict[str, Any] | None = None
        self._locked_parameters_fp: str | None = None
        self._test_peeked = False
        self.violations: List[Dict[str, Any]] = []

    @property
    def n(self) -> int:
        return len(self._bars)

    @property
    def phase(self) -> ResearchPhase:
        return self._phase

    def set_phase(self, phase: ResearchPhase | str) -> None:
        p = phase if isinstance(phase, ResearchPhase) else ResearchPhase(str(phase))
        self._phase = p

    def train_bars(self) -> List[Bar]:
        return slice_bars(self._bars, self.spec.train_start, self.spec.train_end)

    def validation_bars(self) -> List[Bar]:
        return slice_bars(self._bars, self.spec.val_start, self.spec.val_end)

    def test_bars(self, *, current_parameters: Dict[str, Any] | None = None) -> List[Bar]:
        if self._test_locked:
            raise SplitLeakageError(
                "TEST split is locked during strategy development. "
                "Call unlock_test(confirm='EVALUATE_OOS') only for final OOS eval."
            )
        # After peek: reject or record VIOLATION if params changed.
        if self._test_peeked and self._locked_parameters_fp is not None:
            if current_parameters is not None:
                fp = _params_fingerprint(current_parameters)
                if fp != self._locked_parameters_fp:
                    violation = {
                        "type": "OOS_PARAM_CHANGE_VIOLATION",
                        "message": (
                            "Parameters changed after EVALUATE_OOS / TEST peek; "
                            "re-TEST rejected."
                        ),
                        "locked_parameters": self._locked_parameters,
                        "current_parameters": dict(current_parameters),
                    }
                    self.violations.append(violation)
                    raise SplitLeakageError(
                        "VIOLATION: parameters changed after EVALUATE_OOS — "
                        "re-TEST rejected (record VIOLATION)."
                    )
        self._test_peeked = True
        return slice_bars(self._bars, self.spec.test_start, self.spec.test_end)

    def unlock_test(
        self,
        *,
        confirm: str,
        locked_parameters: Dict[str, Any] | None = None,
        allow_during_development: bool = False,
    ) -> None:
        """Unlock TEST only for final OOS. Blocked during TRAIN/PARAM_SELECTION.

        Pass ``locked_parameters`` so later param changes after peek are detected.
        """
        if confirm != "EVALUATE_OOS":
            raise SplitLeakageError(
                "refuse to unlock TEST without confirm='EVALUATE_OOS'"
            )
        if (
            not allow_during_development
            and self._phase in {ResearchPhase.TRAIN, ResearchPhase.PARAM_SELECTION}
        ):
            raise SplitLeakageError(
                f"EVALUATE_OOS is not usable during {self._phase.value}; "
                "finish train/param selection, set_phase(EVALUATE_OOS), then unlock."
            )
        self._test_locked = False
        self._phase = ResearchPhase.EVALUATE_OOS
        if locked_parameters is not None:
            self._locked_parameters = dict(locked_parameters)
            self._locked_parameters_fp = _params_fingerprint(locked_parameters)

    def record_parameter_change(self, new_parameters: Dict[str, Any]) -> Dict[str, Any] | None:
        """If TEST already peeked, record VIOLATION when params change."""
        if not self._test_peeked:
            if self._locked_parameters_fp is not None:
                # Still update lock if not yet peeked? Keep original lock.
                pass
            return None
        if self._locked_parameters_fp is None:
            return None
        fp = _params_fingerprint(new_parameters)
        if fp == self._locked_parameters_fp:
            return None
        violation = {
            "type": "OOS_PARAM_CHANGE_VIOLATION",
            "message": "Parameters changed after TEST was peeked under EVALUATE_OOS",
            "locked_parameters": self._locked_parameters,
            "current_parameters": dict(new_parameters),
        }
        self.violations.append(violation)
        return violation

    @property
    def test_locked(self) -> bool:
        return self._test_locked

    def period_dict(self) -> dict:
        def _t(i: int) -> str | None:
            if i < 0 or i >= len(self._bars):
                return None
            return self._bars[i].event_time.isoformat()

        return {
            "train": {
                "start_index": self.spec.train_start,
                "end_index": self.spec.train_end,
                "start": _t(self.spec.train_start),
                "end": _t(self.spec.train_end - 1) if self.spec.train_end else None,
            },
            "validation": {
                "start_index": self.spec.val_start,
                "end_index": self.spec.val_end,
                "start": _t(self.spec.val_start),
                "end": _t(self.spec.val_end - 1) if self.spec.val_end else None,
            },
            "test": {
                "start_index": self.spec.test_start,
                "end_index": self.spec.test_end,
                "start": _t(self.spec.test_start),
                "end": _t(self.spec.test_end - 1) if self.spec.test_end else None,
                "locked": self._test_locked,
            },
            "phase": self._phase.value,
            "violations": list(self.violations),
        }


class CausalNormalizer:
    """Fit mean/std on TRAIN values only; transform any slice with those stats."""

    def __init__(self) -> None:
        self.mean: float | None = None
        self.std: float | None = None
        self._fitted = False

    def fit(self, values: Sequence[float]) -> "CausalNormalizer":
        if not values:
            raise SplitLeakageError("cannot fit normalizer on empty train values")
        self.mean = sum(values) / len(values)
        var = sum((v - self.mean) ** 2 for v in values) / len(values)
        self.std = var ** 0.5
        self._fitted = True
        return self

    def transform(self, values: Sequence[float]) -> List[float]:
        if not self._fitted or self.mean is None or self.std is None:
            raise SplitLeakageError("normalizer not fit on TRAIN")
        if self.std == 0:
            return [0.0 for _ in values]
        return [(v - self.mean) / self.std for v in values]

    def fit_transform_train(self, train_values: Sequence[float]) -> List[float]:
        return self.fit(train_values).transform(train_values)


def forbid_full_dataset_normalize(
    *,
    n_train: int,
    n_all: int,
    fitted_on: int,
) -> None:
    """Raise if a normalizer was fit on more bars than TRAIN."""
    if fitted_on > n_train:
        raise SplitLeakageError(
            f"normalize used {fitted_on} bars but TRAIN has {n_train} "
            f"(full dataset n={n_all}) — leakage"
        )
    if fitted_on == n_all and n_all > n_train:
        raise SplitLeakageError("normalize using full dataset for train metrics is forbidden")
