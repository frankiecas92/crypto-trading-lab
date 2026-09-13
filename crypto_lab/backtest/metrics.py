"""Performance analyzer — net after costs is primary."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.timeframes import bars_per_year
from crypto_lab.backtest.types import BacktestResult, EquityPoint, TradeRecord


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b == 0:
        return default
    return a / b


def max_drawdown(curve: Sequence[EquityPoint]) -> tuple[float, int]:
    """Return (max_dd_fraction negative, duration_in_bars of the worst DD)."""
    if not curve:
        return 0.0, 0
    peak = curve[0].equity
    max_dd = 0.0
    peak_i = 0
    worst_dur = 0
    dur = 0
    for i, p in enumerate(curve):
        if p.equity >= peak:
            peak = p.equity
            peak_i = i
            dur = 0
        else:
            dd = p.equity / peak - 1.0 if peak > 0 else 0.0
            dur = i - peak_i
            if dd < max_dd:
                max_dd = dd
                worst_dur = dur
    return max_dd, worst_dur


def _bar_returns(curve: Sequence[EquityPoint]) -> List[float]:
    rets: List[float] = []
    for i in range(1, len(curve)):
        prev = curve[i - 1].equity
        if prev <= 0:
            rets.append(0.0)
        else:
            rets.append(curve[i].equity / prev - 1.0)
    return rets


def sharpe_ratio(rets: Sequence[float], periods_per_year: float) -> float | None:
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0 if mu == 0 else None
    return (mu / sd) * math.sqrt(periods_per_year)


def sortino_ratio(rets: Sequence[float], periods_per_year: float) -> float | None:
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    downs = [r for r in rets if r < 0]
    if not downs:
        return 0.0 if mu == 0 else None
    dvar = sum(r ** 2 for r in downs) / len(downs)
    dsd = math.sqrt(dvar)
    if dsd == 0:
        return 0.0 if mu == 0 else None
    return (mu / dsd) * math.sqrt(periods_per_year)


def _period_key(dt: datetime, kind: str) -> str:
    if kind == "year":
        return f"{dt.year:04d}"
    return f"{dt.year:04d}-{dt.month:02d}"


def period_returns(curve: Sequence[EquityPoint], kind: str) -> Dict[str, float]:
    if len(curve) < 2:
        return {}
    groups: Dict[str, list[EquityPoint]] = defaultdict(list)
    for p in curve:
        groups[_period_key(p.event_time, kind)].append(p)
    out: Dict[str, float] = {}
    # first equity of period vs last
    items = sorted(groups.items())
    prev_last = None
    for key, pts in items:
        first = pts[0].equity if prev_last is None else prev_last
        last = pts[-1].equity
        out[key] = last / first - 1.0 if first > 0 else 0.0
        prev_last = last
    return out


def compute_metrics(result: BacktestResult) -> Dict[str, Any]:
    curve = result.equity_curve
    trades = result.trades
    init = result.initial_capital
    final = result.final_equity
    net_ret = (final / init - 1.0) if init > 0 else 0.0

    fees = sum(t.fees for t in trades) + sum(
        f.fee for f in result.fills
    )
    # fills include trade fees; prefer fill sum (all costs actually paid)
    fees = sum(f.fee for f in result.fills)
    slippage = sum(f.slippage_cost for f in result.fills)
    spread = sum(f.spread_cost for f in result.fills)
    other = 0.0
    turnover = 0.0
    avg_eq = (sum(p.equity for p in curve) / len(curve)) if curve else init
    notional = sum(f.fill_price * f.qty for f in result.fills)
    turnover = _safe_div(notional, avg_eq)

    # Gross: reconstruct using mid notionals
    gross_pnl = sum(t.gross_pnl for t in trades)
    gross_ret = _safe_div(gross_pnl, init)

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    flats = [t for t in trades if t.net_pnl == 0]
    n_tr = len(trades)
    win_rate = _safe_div(len(wins), n_tr) if n_tr else 0.0
    loss_rate = _safe_div(len(losses), n_tr) if n_tr else 0.0
    avg_win = _safe_div(sum(t.net_pnl for t in wins), len(wins)) if wins else 0.0
    avg_loss = _safe_div(sum(t.net_pnl for t in losses), len(losses)) if losses else 0.0
    gp = sum(t.net_pnl for t in wins)
    gl = abs(sum(t.net_pnl for t in losses))
    profit_factor = (gp / gl) if gl > 0 else (math.inf if gp > 0 else 0.0)
    expectancy = _safe_div(sum(t.net_pnl for t in trades), n_tr) if n_tr else 0.0
    avg_hold = _safe_div(sum(t.bars_held for t in trades), n_tr) if n_tr else 0.0
    best = max((t.net_pnl for t in trades), default=0.0)
    worst = min((t.net_pnl for t in trades), default=0.0)
    mfe = _safe_div(sum(t.mfe for t in trades), n_tr) if n_tr else 0.0
    mae = _safe_div(sum(t.mae for t in trades), n_tr) if n_tr else 0.0

    dd, dd_dur = max_drawdown(curve)
    rets = _bar_returns(curve)
    try:
        bpy = bars_per_year(result.timeframe)
    except ValueError:
        bpy = 365.25 * 24
    n_bars = max(len(curve) - 1, 1)
    if init > 0 and final > 0:
        ann = (final / init) ** (bpy / n_bars) - 1.0
    else:
        ann = None
    sharpe = sharpe_ratio(rets, bpy)
    sortino = sortino_ratio(rets, bpy)
    calmar = None
    if ann is not None and dd < 0:
        calmar = ann / abs(dd)
    elif ann is not None and dd == 0:
        calmar = 0.0 if ann == 0 else None

    return {
        "return": net_ret,
        "net_return": net_ret,
        "gross_return": gross_ret,
        "ann_return": ann,
        "final_equity": final,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "profit_factor": profit_factor if profit_factor != math.inf else "inf",
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd": dd,
        "dd_duration_bars": dd_dur,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "n_trades": n_tr,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_flats": len(flats),
        "avg_hold_bars": avg_hold,
        "fees": fees,
        "slippage": slippage,
        "spread_cost": spread,
        "other_costs": other,
        "net_after_costs": net_ret,
        "turnover": turnover,
        "best_trade": best,
        "worst_trade": worst,
        "avg_mfe": mfe,
        "avg_mae": mae,
        "returns_by_year": period_returns(curve, "year"),
        "returns_by_month": period_returns(curve, "month"),
        "n_fills": len(result.fills),
        "n_unfilled": len(result.unfilled),
    }


def value_add_vs(result: BacktestResult, benchmark: BacktestResult) -> Dict[str, Any]:
    a = result.metrics
    b = benchmark.metrics
    def _sub(key: str) -> float | None:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            return None
        if isinstance(va, str) or isinstance(vb, str):
            return None
        return float(va) - float(vb)

    return {
        "benchmark": benchmark.strategy_id,
        "benchmark_version": benchmark.strategy_version,
        "net_return_vs": _sub("net_return"),
        "ann_return_vs": _sub("ann_return"),
        "max_dd_vs": _sub("max_dd"),
        "sharpe_vs": _sub("sharpe"),
        "profit_factor_vs": _sub("profit_factor"),
        "n_trades_vs": _sub("n_trades"),
    }


def metrics_from_trade_pnls(
    pnls: Sequence[float],
    *,
    initial_capital: float,
    timeframe: str = "1h",
) -> Dict[str, Any]:
    """Rebuild a simple equity path from a sequence of trade net pnls (MC)."""
    eq = float(initial_capital)
    points: List[EquityPoint] = []
    from datetime import timezone
    from datetime import timedelta

    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    points.append(
        EquityPoint(index=0, event_time=t0, equity=eq, cash=eq, qty=0, close=0, drawdown=0)
    )
    peak = eq
    for i, pnl in enumerate(pnls, start=1):
        eq = eq + pnl
        peak = max(peak, eq)
        dd = eq / peak - 1.0 if peak > 0 else 0.0
        points.append(
            EquityPoint(
                index=i,
                event_time=t0 + timedelta(hours=i),
                equity=eq,
                cash=eq,
                qty=0,
                close=0,
                drawdown=dd,
            )
        )
    dummy = BacktestResult(
        strategy_id="mc",
        strategy_version="mc",
        symbol="BTCUSDT",
        timeframe=timeframe,
        parameters={},
        initial_capital=initial_capital,
        equity_curve=points,
        trades=[
            TradeRecord(
                symbol="BTCUSDT",
                side="LONG",
                entry_index=i,
                exit_index=i,
                entry_time=t0,
                exit_time=t0,
                entry_price=1.0,
                exit_price=1.0,
                qty=1.0,
                gross_pnl=p,
                fees=0.0,
                slippage=0.0,
                spread_cost=0.0,
                net_pnl=p,
                bars_held=1,
                mfe=0.0,
                mae=0.0,
                exit_reason="mc",
            )
            for i, p in enumerate(pnls)
        ],
    )
    return compute_metrics(dummy)
