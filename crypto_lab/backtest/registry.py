"""Experiment registry: persist hypothesis, params, splits, costs, results.

Tracks experiments_run and strategy_variants_tested for later multiple-testing
awareness. Every report should include a reproducibility block.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Set

from sqlalchemy.orm import Session

from crypto_lab.data.models import Experiment

# Process-local counters (later multiple-testing awareness).
_experiments_run: int = 0
_strategy_variants_tested: int = 0
_seen_variants: Set[str] = set()


def reset_registry_counters() -> None:
    """Test helper — clear process counters."""
    global _experiments_run, _strategy_variants_tested, _seen_variants
    _experiments_run = 0
    _strategy_variants_tested = 0
    _seen_variants = set()


def registry_counters() -> Dict[str, int]:
    return {
        "experiments_run": _experiments_run,
        "strategy_variants_tested": _strategy_variants_tested,
    }


def note_strategy_variant(strategy_id: str, parameters: Dict[str, Any] | None = None) -> None:
    """Count distinct strategy_id+params variants (multiple-testing awareness)."""
    global _strategy_variants_tested, _seen_variants
    key = f"{strategy_id}:{json.dumps(parameters or {}, sort_keys=True, default=str)}"
    if key not in _seen_variants:
        _seen_variants.add(key)
        _strategy_variants_tested += 1


def current_git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except OSError:
        return None
    return None


def new_experiment_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"exp_{ts}_{uuid.uuid4().hex[:8]}"


def code_fingerprint(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def reproducibility_block(
    *,
    dataset_id: str | None = None,
    random_seed: int | None = None,
    strategy_version: str | None = None,
    parameters: Dict[str, Any] | None = None,
    cost_profile: str | None = None,
    execution_model: str = "OHLCV_NEXT_BAR_OPEN",
    timestamp: str | None = None,
    git_commit: str | None = None,
) -> Dict[str, Any]:
    """Fields required on every report for reproducibility."""
    return {
        "git_commit": git_commit if git_commit is not None else current_git_commit(),
        "dataset_id": dataset_id,
        "random_seed": random_seed,
        "strategy_version": strategy_version,
        "parameters": parameters or {},
        "cost_profile": cost_profile,
        "execution_model": execution_model,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }


def persist_experiment(
    session: Session,
    *,
    experiment_id: str | None = None,
    hypothesis: str,
    strategy_id: str,
    strategy_version: str,
    parameters: Dict[str, Any],
    dataset_id: str,
    dataset_version: str,
    symbol: str,
    timeframe: str,
    dates: Dict[str, Any],
    split_periods: Dict[str, Any],
    cost_assumptions: Dict[str, Any],
    results: Dict[str, Any],
    benchmark: Dict[str, Any] | None,
    conclusion: str,
    random_seed: int | None,
    extra: Dict[str, Any] | None = None,
    cost_profile: str | None = None,
    execution_model: str = "OHLCV_NEXT_BAR_OPEN",
) -> Experiment:
    global _experiments_run
    eid = experiment_id or new_experiment_id()
    note_strategy_variant(strategy_id, parameters)
    repo = reproducibility_block(
        dataset_id=dataset_id,
        random_seed=random_seed,
        strategy_version=strategy_version,
        parameters=parameters,
        cost_profile=cost_profile or (cost_assumptions or {}).get("profile_name"),
        execution_model=execution_model,
    )
    extra_payload = dict(extra or {})
    extra_payload["reproducibility"] = repo
    extra_payload["registry_counters"] = registry_counters()
    # Ensure counters include this persist (experiments_run incremented below).
    row = Experiment(
        experiment_id=eid,
        hypothesis=hypothesis,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        parameters_json=json.dumps(parameters, sort_keys=True, default=str),
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        symbol=symbol,
        timeframe=timeframe,
        dates_json=json.dumps(dates, sort_keys=True, default=str),
        split_json=json.dumps(split_periods, sort_keys=True, default=str),
        cost_json=json.dumps(cost_assumptions, sort_keys=True, default=str),
        results_json=json.dumps(results, sort_keys=True, default=str),
        benchmark_json=json.dumps(benchmark or {}, sort_keys=True, default=str),
        conclusion=conclusion,
        git_commit=repo["git_commit"],
        random_seed=random_seed,
        extra_json=json.dumps(extra_payload, sort_keys=True, default=str),
    )
    session.add(row)
    session.flush()
    _experiments_run += 1
    # Refresh counters in extra after increment (best-effort; already flushed).
    extra_payload["registry_counters"] = registry_counters()
    row.extra_json = json.dumps(extra_payload, sort_keys=True, default=str)
    session.flush()
    return row


def experiment_to_dict(row: Experiment) -> Dict[str, Any]:
    def _j(s: str | None) -> Any:
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s

    return {
        "experiment_id": row.experiment_id,
        "hypothesis": row.hypothesis,
        "strategy_id": row.strategy_id,
        "strategy_version": row.strategy_version,
        "parameters": _j(row.parameters_json),
        "dataset_id": row.dataset_id,
        "dataset_version": row.dataset_version,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "dates": _j(row.dates_json),
        "splits": _j(row.split_json),
        "costs": _j(row.cost_json),
        "results": _j(row.results_json),
        "benchmark": _j(row.benchmark_json),
        "conclusion": row.conclusion,
        "git_commit": row.git_commit,
        "random_seed": row.random_seed,
        "extra": _j(row.extra_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "registry_counters": registry_counters(),
    }
