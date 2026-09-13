"""Walk-forward windows: train/test periods with frozen params (no unconstrained opt).

Current mode: PARAMETERS_FIXED only.
Future (stubs/enums only — do NOT implement optimization):
  TRAIN_PARAMETER_SELECTION → VALIDATION → LOCK → TEST
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Sequence

from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.types import BacktestResult, Bar
from crypto_lab.exceptions import NotImplementedPhaseError
from crypto_lab.strategies.base import Strategy


class WFMode(str, Enum):
    """Walk-forward parameter handling mode."""

    PARAMETERS_FIXED = "PARAMETERS_FIXED"
    # Future — not implemented in Phase 3 hardening:
    TRAIN_PARAMETER_SELECTION = "TRAIN_PARAMETER_SELECTION"


class WFStage(str, Enum):
    """Future pipeline stages (stubs). Current runs stay on PARAMETERS_FIXED TEST."""

    TRAIN_PARAMETER_SELECTION = "TRAIN_PARAMETER_SELECTION"
    VALIDATION = "VALIDATION"
    LOCK = "LOCK"
    TEST = "TEST"


@dataclass(frozen=True)
class WFWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass
class WFWindowResult:
    window: WFWindow
    params: Dict[str, Any]
    net_return: float
    max_dd: float
    sharpe: float | None
    profit_factor: Any
    n_trades: int
    net_after_costs: float
    split_name: str


def make_windows(
    n: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
) -> List[WFWindow]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be > 0")
    step = step or test_size
    out: List[WFWindow] = []
    i = 0
    while i + train_size + test_size <= n:
        tr_s, tr_e = i, i + train_size
        te_s, te_e = tr_e, tr_e + test_size
        out.append(WFWindow(tr_s, tr_e, te_s, te_e))
        i += step
    return out


def run_walk_forward(
    bars: Sequence[Bar],
    strategy_factory: Callable[[], Strategy],
    engine: BacktestEngine,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    close_at_end: bool = True,
    warmup_bars: int = 0,
    mode: WFMode | str = WFMode.PARAMETERS_FIXED,
) -> Dict[str, Any]:
    """Evaluate a FIXED strategy (params from factory) on each test window.

    Optional small train-only inspection is left to the caller; this function
    does not search parameters (no unconstrained auto-optimization).
    """
    wf_mode = mode if isinstance(mode, WFMode) else WFMode(str(mode))
    if wf_mode != WFMode.PARAMETERS_FIXED:
        raise NotImplementedPhaseError(
            f"walk-forward mode {wf_mode.value} is a stub for a later phase; "
            "only PARAMETERS_FIXED is implemented (no parameter optimization)."
        )
    windows = make_windows(len(bars), train_size=train_size, test_size=test_size, step=step)
    rows: List[WFWindowResult] = []
    for w in windows:
        strat = strategy_factory()
        warm = int(warmup_bars or 0)
        start = max(0, w.test_start - warm)
        window_bars = list(bars[start : w.test_end])
        trade_from = w.test_start - start
        if len(window_bars) - trade_from < 2:
            continue
        res = engine.run(
            strat,
            window_bars,
            split_name="walk_forward_test",
            close_at_end=close_at_end,
            trade_from_index=trade_from,
        )
        m = res.metrics
        rows.append(
            WFWindowResult(
                window=w,
                params=strat.parameters(),
                net_return=float(m.get("net_return") or 0.0),
                max_dd=float(m.get("max_dd") or 0.0),
                sharpe=m.get("sharpe"),
                profit_factor=m.get("profit_factor"),
                n_trades=int(m.get("n_trades") or 0),
                net_after_costs=float(m.get("net_after_costs") or 0.0),
                split_name="walk_forward_test",
            )
        )
    out = aggregate_wf(rows)
    out["mode"] = wf_mode.value
    out["future_stages"] = [s.value for s in WFStage]
    out["stage_note"] = (
        "PARAMETERS_FIXED only; future TRAIN_PARAMETER_SELECTION → VALIDATION → "
        "LOCK → TEST is stubbed, not implemented."
    )
    return out


def future_wf_pipeline_stub() -> Dict[str, Any]:
    """Interface stub for future TRAIN_PARAMETER_SELECTION → VALIDATION → LOCK → TEST.

    Does not perform optimization. Raises if invoked as an optimizer.
    """
    return {
        "implemented": False,
        "mode": WFMode.TRAIN_PARAMETER_SELECTION.value,
        "stages": [s.value for s in WFStage],
        "message": (
            "Stub only: future walk-forward parameter selection pipeline is not "
            "implemented. Current lab keeps PARAMETERS_FIXED."
        ),
    }


def run_future_parameter_selection(*_args: Any, **_kwargs: Any) -> None:
    """Explicit non-implementation of unconstrained / WF parameter search."""
    raise NotImplementedPhaseError(
        "TRAIN_PARAMETER_SELECTION → VALIDATION → LOCK → TEST is not implemented; "
        "no parameter optimization in this phase."
    )


def aggregate_wf(rows: Sequence[WFWindowResult]) -> Dict[str, Any]:
    if not rows:
        return {"windows": [], "aggregate": {}, "n_windows": 0}

    def _mean(xs: List[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    nets = [r.net_return for r in rows]
    dds = [r.max_dd for r in rows]
    sharpes = [r.sharpe for r in rows if r.sharpe is not None]
    trades = [float(r.n_trades) for r in rows]
    pfs: List[float] = []
    for r in rows:
        pf = r.profit_factor
        if isinstance(pf, (int, float)):
            pfs.append(float(pf))
    return {
        "n_windows": len(rows),
        "windows": [
            {
                **asdict(r.window),
                "params": r.params,
                "net_return": r.net_return,
                "max_dd": r.max_dd,
                "sharpe": r.sharpe,
                "profit_factor": r.profit_factor,
                "n_trades": r.n_trades,
                "net_after_costs": r.net_after_costs,
            }
            for r in rows
        ],
        "aggregate": {
            "mean_net_return": _mean(nets),
            "mean_max_dd": _mean(dds),
            "mean_sharpe": _mean([float(s) for s in sharpes]) if sharpes else None,
            "mean_profit_factor": _mean(pfs) if pfs else None,
            "mean_n_trades": _mean(trades),
            "mean_net_after_costs": _mean([r.net_after_costs for r in rows]),
        },
    }
