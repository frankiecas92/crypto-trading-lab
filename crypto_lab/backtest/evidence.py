"""Evidence status vs trading claims; sample-adequacy checks (configurable).

Synthetic / demo runs are ENGINE_VALIDATION_ONLY — never EDGE_CONFIRMED.
Scientific conclusions for current synthetic results stay NO_EVIDENCE_OF_EDGE
+ INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Sequence


class EvidenceStatus(str, Enum):
    """Separate engine validation from trading evidence."""

    ENGINE_VALIDATION_ONLY = "ENGINE_VALIDATION_ONLY"
    TRADING_EVIDENCE_CANDIDATE = "TRADING_EVIDENCE_CANDIDATE"
    # EDGE_CONFIRMED is reserved; never emitted for synthetic/demo runs.
    EDGE_CONFIRMED = "EDGE_CONFIRMED"


class ScientificConclusion(str, Enum):
    NO_EVIDENCE_OF_EDGE = "NO_EVIDENCE_OF_EDGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    # Positive edge claims are intentionally absent from Phase 3 synthetic demos.


class SampleStatus(str, Enum):
    ADEQUATE_SAMPLE = "ADEQUATE_SAMPLE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


SYNTHETIC_DATASET_PREFIXES = ("synthetic", "demo")


@dataclass(frozen=True)
class SampleThresholds:
    """Configurable insufficient-sample thresholds — not universal truth."""

    min_bars: int = 200
    min_trades: int = 30
    min_period_bars: int = 50
    min_oos_bars: int = 40
    min_wf_windows: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SampleAdequacy:
    status: SampleStatus
    reasons: List[str] = field(default_factory=list)
    thresholds: Dict[str, Any] = field(default_factory=dict)
    observed: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": list(self.reasons),
            "thresholds": dict(self.thresholds),
            "observed": dict(self.observed),
            "why_insufficient": list(self.reasons) if self.status == SampleStatus.INSUFFICIENT_SAMPLE else [],
        }


def is_synthetic_dataset(dataset_id: str | None) -> bool:
    if not dataset_id:
        return True
    d = dataset_id.lower()
    return any(d.startswith(p) or p in d for p in SYNTHETIC_DATASET_PREFIXES)


def evidence_status_for_dataset(dataset_id: str | None) -> EvidenceStatus:
    """Synthetic/demo → ENGINE_VALIDATION_ONLY. Never EDGE_CONFIRMED here."""
    if is_synthetic_dataset(dataset_id):
        return EvidenceStatus.ENGINE_VALIDATION_ONLY
    return EvidenceStatus.TRADING_EVIDENCE_CANDIDATE


def assert_not_edge_confirmed_for_synthetic(
    evidence_status: EvidenceStatus | str,
    dataset_id: str | None,
) -> None:
    status = (
        evidence_status
        if isinstance(evidence_status, EvidenceStatus)
        else EvidenceStatus(str(evidence_status))
    )
    if is_synthetic_dataset(dataset_id) and status == EvidenceStatus.EDGE_CONFIRMED:
        raise ValueError(
            "EDGE_CONFIRMED must never be emitted for synthetic/demo runs "
            f"(dataset_id={dataset_id!r})"
        )


def synthetic_scientific_conclusions() -> List[str]:
    """Canonical conclusions for current synthetic results."""
    return [
        ScientificConclusion.NO_EVIDENCE_OF_EDGE.value,
        ScientificConclusion.INSUFFICIENT_EVIDENCE.value,
    ]


def validate_sample_adequacy(
    *,
    thresholds: SampleThresholds,
    n_bars: int | None = None,
    n_trades: int | None = None,
    period_bars: int | None = None,
    oos_bars: int | None = None,
    wf_windows: int | None = None,
) -> SampleAdequacy:
    """Mark INSUFFICIENT_SAMPLE when observed counts fall below configured thresholds.

    Thresholds live in settings/config — they are research gates, not market truth.
    """
    reasons: List[str] = []
    observed: Dict[str, Any] = {}
    th = thresholds.to_dict()

    checks = [
        ("n_bars", n_bars, thresholds.min_bars, "too few bars"),
        ("n_trades", n_trades, thresholds.min_trades, "too few trades"),
        ("period_bars", period_bars, thresholds.min_period_bars, "period too short"),
        ("oos_bars", oos_bars, thresholds.min_oos_bars, "OOS length too short"),
        ("wf_windows", wf_windows, thresholds.min_wf_windows, "too few walk-forward windows"),
    ]
    for key, value, minimum, why in checks:
        if value is None:
            continue
        observed[key] = value
        if value < minimum:
            reasons.append(f"{why}: {key}={value} < min={minimum}")

    status = SampleStatus.INSUFFICIENT_SAMPLE if reasons else SampleStatus.ADEQUATE_SAMPLE
    return SampleAdequacy(status=status, reasons=reasons, thresholds=th, observed=observed)


def build_evidence_block(
    *,
    dataset_id: str | None,
    sample: SampleAdequacy | None = None,
    force_synthetic_conclusions: bool | None = None,
) -> Dict[str, Any]:
    """Report block: evidence status + scientific conclusions (no edge claims)."""
    synthetic = is_synthetic_dataset(dataset_id)
    status = evidence_status_for_dataset(dataset_id)
    assert_not_edge_confirmed_for_synthetic(status, dataset_id)
    use_synth = force_synthetic_conclusions if force_synthetic_conclusions is not None else synthetic
    conclusions = synthetic_scientific_conclusions() if use_synth else synthetic_scientific_conclusions()
    # Phase 3: even non-synthetic candidates do not auto-confirm edge.
    block: Dict[str, Any] = {
        "EVIDENCE_STATUS": status.value,
        "scientific_conclusions": conclusions,
        "scientific_conclusion": " + ".join(conclusions),
        "EDGE_CONFIRMED": False,
        "dataset_id": dataset_id,
        "synthetic": synthetic,
    }
    if sample is not None:
        block["sample_adequacy"] = sample.to_dict()
        if sample.status == SampleStatus.INSUFFICIENT_SAMPLE:
            # Reinforce insufficient evidence when sample is too small.
            if ScientificConclusion.INSUFFICIENT_EVIDENCE.value not in conclusions:
                conclusions = list(conclusions) + [ScientificConclusion.INSUFFICIENT_EVIDENCE.value]
            block["scientific_conclusions"] = conclusions
            block["scientific_conclusion"] = " + ".join(conclusions)
    return block
