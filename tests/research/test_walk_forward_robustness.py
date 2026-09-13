"""Walk-forward, robustness grid, Monte Carlo, train/test."""

from __future__ import annotations

from crypto_lab.backtest.costs import CostModel
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.monte_carlo import run_monte_carlo
from crypto_lab.backtest.robustness import cost_sensitivity, run_sma_grid, sma_neighborhood
from crypto_lab.backtest.splits import ResearchSplit, chronological_split
from crypto_lab.backtest.synthetic import noisy_trend
from crypto_lab.backtest.types import TradeRecord
from crypto_lab.backtest.walk_forward import make_windows, run_walk_forward
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.strategies.sma import SMACrossover
from datetime import datetime, timezone


def test_walk_forward_windows_and_aggregate():
    bars = noisy_trend(120, seed=3, symbol="BTCUSDT")
    wins = make_windows(len(bars), train_size=40, test_size=20, step=20)
    assert wins
    assert all(w.test_end <= len(bars) for w in wins)
    eng = BacktestEngine()
    out = run_walk_forward(
        bars,
        lambda: SMACrossover(fast=5, slow=15, symbol="BTCUSDT"),
        eng,
        train_size=40,
        test_size=20,
        step=20,
        warmup_bars=15,
    )
    assert out["n_windows"] >= 1
    agg = out["aggregate"]
    for k in ("mean_net_return", "mean_max_dd", "mean_n_trades", "mean_net_after_costs"):
        assert k in agg
    row = out["windows"][0]
    for k in ("train_start", "test_start", "params", "net_return", "max_dd", "sharpe", "profit_factor", "n_trades"):
        assert k in row


def test_robustness_sma_grid_labels():
    bars = noisy_trend(100, seed=5, symbol="BTCUSDT")
    grid = sma_neighborhood(fast_min=4, fast_max=8, fast_step=2, slow_min=12, slow_max=16, slow_step=2)
    assert grid
    out = run_sma_grid(
        bars,
        symbol="BTCUSDT",
        fast_min=4,
        fast_max=8,
        fast_step=2,
        slow_min=12,
        slow_max=16,
        slow_step=2,
    )
    assert out["label"] in {"ROBUST_REGION", "SINGLE_PARAMETER_PEAK"}
    assert out["n_cells"] == len(grid)
    assert "cells" in out


def test_cost_sensitivity_runs():
    bars = noisy_trend(80, seed=4, symbol="BTCUSDT")
    base = CostModel(fee_bps=10, spread_bps=4, slippage_bps=2)
    out = cost_sensitivity(
        bars,
        lambda: SMACrossover(fast=5, slow=15, symbol="BTCUSDT"),
        base,
        sizing=SizingPolicy(notional=10_000.0),
        shock=0.5,
    )
    assert out["label"] in {"FRAGILE_TO_COSTS", "ROBUST_TO_COSTS"}
    assert "BASE" in out["nets"]
    assert "PLUS_25" in out["nets"]
    assert "PLUS_50" in out["nets"]
    assert "PLUS_100" in out["nets"]
    assert "STRESS" in out["nets"]


def test_monte_carlo_bootstrap_and_shuffle():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trades = [
        TradeRecord(
            symbol="BTCUSDT",
            side="LONG",
            entry_index=i,
            exit_index=i + 1,
            entry_time=t0,
            exit_time=t0,
            entry_price=100,
            exit_price=100,
            qty=1,
            gross_pnl=p,
            fees=0,
            slippage=0,
            spread_cost=0,
            net_pnl=p,
            bars_held=1,
            mfe=0,
            mae=0,
            exit_reason="x",
        )
        for i, p in enumerate([10.0, -3.0, 5.0, -8.0, 4.0, 1.0])
    ]
    out = run_monte_carlo(trades, initial_capital=1000.0, n_sims=40, seed=1)
    assert out["n_sims"] == 40
    assert "bootstrap" in out and "shuffle" in out
    assert out["label"] in {"FRAGILE_ORDER_DEPENDENT", "ORDER_STABLE"}
    assert "p05" in out["bootstrap"]["net_return"]


def test_train_test_split_indices():
    bars = noisy_trend(50, seed=1)
    spec = chronological_split(50, train_frac=0.6, val_frac=0.2, test_frac=0.2)
    rs = ResearchSplit(bars, spec)
    assert spec.train_end <= spec.val_start
    assert spec.val_end <= spec.test_start
    train = rs.train_bars()
    assert train[-1].event_time < bars[spec.test_start].event_time
