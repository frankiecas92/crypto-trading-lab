"""Drawdown, Sharpe, Sortino and related metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.backtest.metrics import compute_metrics, max_drawdown, sharpe_ratio, sortino_ratio
from crypto_lab.backtest.types import BacktestResult, EquityPoint, TradeRecord


def _curve(equities: list[float]) -> list[EquityPoint]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    peak = equities[0]
    out = []
    for i, e in enumerate(equities):
        peak = max(peak, e)
        dd = e / peak - 1.0 if peak else 0.0
        out.append(
            EquityPoint(
                index=i,
                event_time=t0 + timedelta(hours=i),
                equity=e,
                cash=e,
                qty=0,
                close=e,
                drawdown=dd,
            )
        )
    return out


def test_max_drawdown_and_duration():
    curve = _curve([100.0, 110.0, 99.0, 99.0, 120.0])
    dd, dur = max_drawdown(curve)
    assert dd == pytest.approx(99 / 110 - 1.0)
    assert dur >= 1


def test_sharpe_sortino_known():
    rets = [0.01, 0.01, 0.01, 0.01]
    s = sharpe_ratio(rets, 252)
    # zero variance + nonzero mean → undefined
    assert s is None
    mixed = [0.02, -0.01, 0.015, -0.005, 0.01]
    sh = sharpe_ratio(mixed, 252)
    so = sortino_ratio(mixed, 252)
    assert sh is not None and so is not None


def test_compute_metrics_minimum_keys():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trades = [
        TradeRecord(
            symbol="BTCUSDT",
            side="LONG",
            entry_index=0,
            exit_index=2,
            entry_time=t0,
            exit_time=t0,
            entry_price=100,
            exit_price=110,
            qty=1,
            gross_pnl=10,
            fees=1,
            slippage=0.2,
            spread_cost=0.1,
            net_pnl=9,
            bars_held=2,
            mfe=12,
            mae=1,
            exit_reason="x",
        ),
        TradeRecord(
            symbol="BTCUSDT",
            side="LONG",
            entry_index=3,
            exit_index=4,
            entry_time=t0,
            exit_time=t0,
            entry_price=110,
            exit_price=100,
            qty=1,
            gross_pnl=-10,
            fees=1,
            slippage=0.2,
            spread_cost=0.1,
            net_pnl=-11,
            bars_held=1,
            mfe=1,
            mae=12,
            exit_reason="x",
        ),
    ]
    res = BacktestResult(
        strategy_id="t",
        strategy_version="t_v001",
        symbol="BTCUSDT",
        timeframe="1h",
        parameters={},
        initial_capital=1000.0,
        equity_curve=_curve([1000, 1010, 999]),
        trades=trades,
    )
    m = compute_metrics(res)
    for key in (
        "return",
        "net_return",
        "ann_return",
        "win_rate",
        "loss_rate",
        "profit_factor",
        "expectancy",
        "avg_win",
        "avg_loss",
        "max_dd",
        "dd_duration_bars",
        "sharpe",
        "sortino",
        "calmar",
        "n_trades",
        "avg_hold_bars",
        "fees",
        "slippage",
        "turnover",
        "best_trade",
        "worst_trade",
        "avg_mfe",
        "avg_mae",
        "returns_by_year",
        "returns_by_month",
        "net_after_costs",
    ):
        assert key in m
    assert m["n_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)
