"""Write equity/drawdown/trade/monthly artifacts (JSON + simple SVG)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from crypto_lab.backtest.types import BacktestResult


def _eq_points(result: BacktestResult) -> List[Dict[str, Any]]:
    return [
        {
            "index": p.index,
            "event_time": p.event_time.isoformat(),
            "equity": p.equity,
            "cash": p.cash,
            "qty": p.qty,
            "close": p.close,
            "drawdown": p.drawdown,
        }
        for p in result.equity_curve
    ]


def _trades(result: BacktestResult) -> List[Dict[str, Any]]:
    out = []
    for t in result.trades:
        out.append(
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_index": t.entry_index,
                "exit_index": t.exit_index,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "gross_pnl": t.gross_pnl,
                "fees": t.fees,
                "slippage": t.slippage,
                "spread_cost": t.spread_cost,
                "net_pnl": t.net_pnl,
                "bars_held": t.bars_held,
                "mfe": t.mfe,
                "mae": t.mae,
                "exit_reason": t.exit_reason,
            }
        )
    return out


def write_svg_polyline(values: List[float], path: Path, *, title: str) -> None:
    if not values:
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>\n", encoding="utf-8")
        return
    w, h, pad = 640, 200, 16
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1.0
    pts = []
    n = len(values)
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * (i / max(n - 1, 1))
        y = pad + (h - 2 * pad) * (1.0 - (v - vmin) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>"
        f"<rect width='100%' height='100%' fill='#0b0f14'/>"
        f"<text x='{pad}' y='14' fill='#9ad' font-size='12'>{title}</text>"
        f"<polyline fill='none' stroke='#6cf' stroke-width='1.5' points='{' '.join(pts)}'/>"
        f"</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def write_result_artifacts(result: BacktestResult, out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}

    def dump(name: str, obj: Any) -> None:
        p = out_dir / name
        p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        written[name] = str(p)

    dump("equity_curve.json", _eq_points(result))
    dump(
        "drawdown.json",
        [{"index": p.index, "event_time": p.event_time.isoformat(), "drawdown": p.drawdown} for p in result.equity_curve],
    )
    dump("underwater.json", [{"index": p.index, "underwater": p.drawdown} for p in result.equity_curve])
    dump("trades.json", _trades(result))
    dump("monthly_returns.json", result.metrics.get("returns_by_month", {}))
    dump("yearly_returns.json", result.metrics.get("returns_by_year", {}))
    dump(
        "trade_distribution.json",
        {
            "nets": [t.net_pnl for t in result.trades],
            "holds": [t.bars_held for t in result.trades],
            "mfe": [t.mfe for t in result.trades],
            "mae": [t.mae for t in result.trades],
        },
    )
    from crypto_lab.backtest.registry import reproducibility_block

    costs = result.cost_assumptions or {}
    repo = reproducibility_block(
        dataset_id=result.dataset_id,
        random_seed=result.seed,
        strategy_version=result.strategy_version,
        parameters=result.parameters,
        cost_profile=costs.get("profile_name"),
        execution_model="OHLCV_NEXT_BAR_OPEN",
    )
    dump(
        "summary.json",
        {
            "strategy_id": result.strategy_id,
            "strategy_version": result.strategy_version,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "parameters": result.parameters,
            "metrics": result.metrics,
            "costs": costs,
            "cost_profile": costs.get("profile_name"),
            "active_stops": result.active_stops,
            "limitations": result.limitations,
            "regime_counts": result.regime_counts,
            "split_name": result.split_name,
            "reproducibility": repo,
            "git_commit": repo["git_commit"],
            "dataset_id": result.dataset_id,
            "random_seed": result.seed,
            "execution_model": repo["execution_model"],
            "timestamp": repo["timestamp"],
        },
    )
    write_svg_polyline(
        [p.equity for p in result.equity_curve],
        out_dir / "equity_curve.svg",
        title=f"equity {result.strategy_id}",
    )
    write_svg_polyline(
        [p.drawdown for p in result.equity_curve],
        out_dir / "underwater.svg",
        title=f"underwater {result.strategy_id}",
    )
    written["equity_curve.svg"] = str(out_dir / "equity_curve.svg")
    written["underwater.svg"] = str(out_dir / "underwater.svg")
    return written
