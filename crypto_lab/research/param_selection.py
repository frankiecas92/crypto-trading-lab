"""Parameter selection on TRAIN/VALIDATION only — never TEST.

Simple grid neighborhood on VALIDATION; then PARAMETERS_FIXED.
Does not peek TEST during selection (SplitLeakageError if attempted).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence

from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.robustness import sma_neighborhood
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit
from crypto_lab.backtest.types import Bar
from crypto_lab.exceptions import SplitLeakageError
from crypto_lab.strategies.base import Strategy
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover


def momentum_neighborhood(
    *,
    lookback_min: int = 5,
    lookback_max: int = 15,
    lookback_step: int = 5,
    thresholds: Sequence[float] | None = None,
) -> List[Dict[str, Any]]:
    ths = list(thresholds) if thresholds is not None else [0.0]
    cells: List[Dict[str, Any]] = []
    for lb in range(lookback_min, lookback_max + 1, lookback_step):
        for th in ths:
            cells.append({"lookback": int(lb), "threshold": float(th)})
    return cells


def select_on_validation(
    split: ResearchSplit,
    factory: Callable[[Dict[str, Any]], Strategy],
    grid: Sequence[Dict[str, Any]],
    engine: BacktestEngine,
    *,
    metric: str = "net_return",
) -> Dict[str, Any]:
    """Grid-search on VALIDATION only. TEST must remain locked.

    Sets phase to PARAM_SELECTION. Raises SplitLeakageError if TEST is readable.
    """
    if not split.test_locked:
        raise SplitLeakageError(
            "TEST must stay locked during parameter selection; "
            "refuse selection after EVALUATE_OOS unlock"
        )
    split.set_phase(ResearchPhase.PARAM_SELECTION)
    # Explicitly refuse any accidental TEST access.
    try:
        split.test_bars()
        raise SplitLeakageError("TEST was readable during PARAM_SELECTION — leakage")
    except SplitLeakageError as exc:
        if "locked" not in str(exc).lower() and "EVALUATE_OOS" not in str(exc):
            raise

    val = split.validation_bars()
    if len(val) < 3:
        # Fall back to train if val is tiny (still never TEST).
        val = split.train_bars()
        used = "train"
    else:
        used = "validation"

    scored: List[Dict[str, Any]] = []
    for params in grid:
        strat = factory(params)
        res = engine.run(strat, val, split_name="param_selection_val")
        m = res.metrics
        scored.append(
            {
                "params": dict(params),
                "net_return": float(m.get("net_return") or 0.0),
                "max_dd": float(m.get("max_dd") or 0.0),
                "sharpe": m.get("sharpe"),
                "n_trades": int(m.get("n_trades") or 0),
                "profit_factor": m.get("profit_factor"),
                "metric_value": float(m.get(metric) or 0.0)
                if isinstance(m.get(metric), (int, float))
                else float(m.get("net_return") or 0.0),
            }
        )

    if not scored:
        raise SplitLeakageError("empty parameter grid — nothing to select")

    best = max(scored, key=lambda r: r["metric_value"])
    locked = dict(best["params"])
    return {
        "selected_params": locked,
        "selection_split": used,
        "metric": metric,
        "best_metric_value": best["metric_value"],
        "n_candidates": len(scored),
        "candidates": scored,
        "PARAMETERS_FIXED": True,
        "test_locked_during_selection": split.test_locked,
        "phase": split.phase.value,
    }


def select_sma_params(
    split: ResearchSplit,
    engine: BacktestEngine,
    *,
    symbol: str | None = None,
    fast_min: int = 8,
    fast_max: int = 12,
    fast_step: int = 2,
    slow_min: int = 24,
    slow_max: int = 36,
    slow_step: int = 6,
) -> Dict[str, Any]:
    grid = sma_neighborhood(
        fast_min=fast_min,
        fast_max=fast_max,
        fast_step=fast_step,
        slow_min=slow_min,
        slow_max=slow_max,
        slow_step=slow_step,
    )

    def fac(p: Dict[str, Any]) -> Strategy:
        return SMACrossover(fast=int(p["fast"]), slow=int(p["slow"]), symbol=symbol)

    out = select_on_validation(split, fac, grid, engine)
    out["strategy_id"] = "SMA_CROSS"
    return out


def select_momentum_params(
    split: ResearchSplit,
    engine: BacktestEngine,
    *,
    symbol: str | None = None,
    lookback_min: int = 5,
    lookback_max: int = 15,
    lookback_step: int = 5,
) -> Dict[str, Any]:
    grid = momentum_neighborhood(
        lookback_min=lookback_min,
        lookback_max=lookback_max,
        lookback_step=lookback_step,
    )

    def fac(p: Dict[str, Any]) -> Strategy:
        return SimpleMomentum(
            lookback=int(p["lookback"]),
            threshold=float(p.get("threshold", 0.0)),
            symbol=symbol,
        )

    out = select_on_validation(split, fac, grid, engine)
    out["strategy_id"] = "MOMENTUM"
    return out


def assert_params_fixed_before_oos(
    locked_params: Dict[str, Any] | None,
    *,
    parameters_fixed: bool,
) -> None:
    """Gate: OOS must not run until parameters are locked."""
    if not parameters_fixed or locked_params is None:
        raise SplitLeakageError(
            "PARAMETERS_FIXED required before EVALUATE_OOS; "
            "finish validation selection first"
        )
