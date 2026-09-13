"""Robustness: parameter neighborhood, cost sensitivity, surfaces.

Labels a grid as ROBUST REGION vs SINGLE PARAMETER PEAK.
Cost sensitivity: BASE, +25%, +50%, +100%, plus STRESS profile →
ROBUST_TO_COSTS or FRAGILE_TO_COSTS. Does NOT change the scientific
conclusion about edge (that stays in evidence/demo layers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from crypto_lab.backtest.costs import (
    CostModel,
    CostProfileName,
    get_cost_profile,
    scale_cost_model,
)
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.types import Bar
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.risk.stops import StopPolicy
from crypto_lab.strategies.base import Strategy
from crypto_lab.strategies.sma import SMACrossover


@dataclass(frozen=True)
class GridCell:
    params: Dict[str, Any]
    net_return: float
    max_dd: float
    profit_factor: Any
    n_trades: int
    sharpe: float | None


def sma_neighborhood(
    *,
    fast_min: int = 15,
    fast_max: int = 25,
    fast_step: int = 5,
    slow_min: int = 45,
    slow_max: int = 65,
    slow_step: int = 10,
) -> List[Dict[str, int]]:
    cells: List[Dict[str, int]] = []
    for f in range(fast_min, fast_max + 1, fast_step):
        for s in range(slow_min, slow_max + 1, slow_step):
            if f < s:
                cells.append({"fast": f, "slow": s})
    return cells


def run_param_grid(
    bars: Sequence[Bar],
    factory: Callable[[Dict[str, Any]], Strategy],
    grid: Sequence[Dict[str, Any]],
    *,
    engine_kwargs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    kw = dict(engine_kwargs or {})
    cells: List[GridCell] = []
    for params in grid:
        eng = BacktestEngine(**kw)
        res = eng.run(factory(params), bars, split_name="robustness_grid")
        m = res.metrics
        cells.append(
            GridCell(
                params=dict(params),
                net_return=float(m.get("net_return") or 0.0),
                max_dd=float(m.get("max_dd") or 0.0),
                profit_factor=m.get("profit_factor"),
                n_trades=int(m.get("n_trades") or 0),
                sharpe=m.get("sharpe"),
            )
        )
    return classify_grid(cells)


def classify_grid(cells: Sequence[GridCell]) -> Dict[str, Any]:
    if not cells:
        return {"label": "EMPTY", "cells": [], "surface": {}}
    nets = [c.net_return for c in cells]
    best = max(nets)
    # Robust if several neighbors are within 30% of the best (or 2pp if best~0)
    tol = max(0.02, abs(best) * 0.3)
    near = [c for c in cells if abs(c.net_return - best) <= tol]
    label = "ROBUST_REGION" if len(near) >= max(2, len(cells) // 4) else "SINGLE_PARAMETER_PEAK"
    return {
        "label": label,
        "n_cells": len(cells),
        "n_near_best": len(near),
        "best_net_return": best,
        "worst_net_return": min(nets),
        "cells": [
            {
                "params": c.params,
                "net_return": c.net_return,
                "max_dd": c.max_dd,
                "profit_factor": c.profit_factor,
                "n_trades": c.n_trades,
                "sharpe": c.sharpe,
            }
            for c in cells
        ],
    }


def run_sma_grid(
    bars: Sequence[Bar],
    *,
    symbol: str | None = None,
    engine_kwargs: Dict[str, Any] | None = None,
    **grid_kw: Any,
) -> Dict[str, Any]:
    grid = sma_neighborhood(**grid_kw)

    def fac(p: Dict[str, Any]) -> Strategy:
        return SMACrossover(fast=int(p["fast"]), slow=int(p["slow"]), symbol=symbol)

    return run_param_grid(bars, fac, grid, engine_kwargs=engine_kwargs)


def cost_sensitivity(
    bars: Sequence[Bar],
    strategy_factory: Callable[[], Strategy],
    base_cost: CostModel,
    *,
    initial_capital: float = 100_000.0,
    sizing: SizingPolicy | None = None,
    stops: StopPolicy | None = None,
    shock: float | None = None,  # unused; retained for call-site compat
) -> Dict[str, Any]:
    """Re-run under BASE, +25%, +50%, +100% cost scales, plus STRESS profile.

    Labels ROBUST_TO_COSTS or FRAGILE_TO_COSTS. Does not alter edge conclusions.
    ``shock`` is accepted but ignored (legacy call sites).
    """
    _ = shock
    variants: Dict[str, CostModel] = {
        "BASE": base_cost if base_cost.profile_name else CostModel(
            fee_bps=base_cost.fee_bps,
            spread_bps=base_cost.spread_bps,
            slippage_bps=base_cost.slippage_bps,
            latency_bars=base_cost.latency_bars,
            latency_ms=base_cost.latency_ms,
            partial_fill_ratio=base_cost.partial_fill_ratio,
            maker_fee_bps=base_cost.maker_fee_bps,
            taker_fee_bps=base_cost.taker_fee_bps,
            profile_name=CostProfileName.BASE.value,
        ),
        "PLUS_25": scale_cost_model(base_cost, 1.25),
        "PLUS_50": scale_cost_model(base_cost, 1.50),
        "PLUS_100": scale_cost_model(base_cost, 2.0),
        "STRESS": get_cost_profile(CostProfileName.STRESS),
    }
    results: Dict[str, float] = {}
    profiles_used: Dict[str, Any] = {}
    for name, cost in variants.items():
        eng = BacktestEngine(
            cost=cost, sizing=sizing, stops=stops, initial_capital=initial_capital
        )
        res = eng.run(strategy_factory(), bars, split_name=f"sens_{name}")
        results[name] = float(res.metrics.get("net_return") or 0.0)
        profiles_used[name] = {
            "profile_name": cost.profile_name,
            "fee_bps": cost.fee_bps,
            "spread_bps": cost.spread_bps,
            "slippage_bps": cost.slippage_bps,
            "maker_fee_bps": cost.effective_maker_fee_bps(),
            "taker_fee_bps": cost.effective_taker_fee_bps(),
        }

    base = results["BASE"]
    fragile = False
    reasons: List[str] = []
    for name, val in results.items():
        if name == "BASE":
            continue
        drop = base - val
        if base > 0 and val < 0:
            fragile = True
            reasons.append(f"{name}: sign flip {base:.4f} → {val:.4f}")
        elif abs(base) > 1e-9 and drop > 0.5 * abs(base):
            fragile = True
            reasons.append(f"{name}: collapsed {base:.4f} → {val:.4f}")
        elif name == "STRESS" and abs(base) > 1e-9 and drop > 0.35 * abs(base):
            fragile = True
            reasons.append(f"{name}: stress drawdown {base:.4f} → {val:.4f}")

    label = "FRAGILE_TO_COSTS" if fragile else "ROBUST_TO_COSTS"
    return {
        "label": label,
        # Legacy aliases for report heuristics (do not drive edge claims).
        "legacy_label": "FRAGILE" if fragile else "STABLE",
        "nets": results,
        "profiles": profiles_used,
        "reasons": reasons,
        "note": "Cost robustness label does not change scientific edge conclusion.",
    }
