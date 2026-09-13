"""Fees, slippage, spread, partial fill, latency."""

from __future__ import annotations

import pytest

from crypto_lab.backtest.costs import CostModel, compute_fill_quote
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.synthetic import flat_price
from crypto_lab.execution.simulator import ExecutionSimulator
from crypto_lab.backtest.types import PendingOrder
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC


def test_compute_fill_quote_buy_sell_symmetric():
    bars = flat_price(3, price=100.0)
    cost = CostModel(fee_bps=10, spread_bps=10, slippage_bps=10)
    buy = compute_fill_quote("BUY", bars[0], cost, qty=1.0)
    sell = compute_fill_quote("SELL", bars[0], cost, qty=1.0)
    assert buy.fill_price == pytest.approx(100.15)
    assert sell.fill_price == pytest.approx(99.85)
    assert buy.fee == pytest.approx(0.10015)
    assert buy.spread_cost == pytest.approx(0.05)
    assert buy.slippage_cost == pytest.approx(0.10)


def test_engine_fees_slippage_spread_match_quote():
    bars = flat_price(8, price=100.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=10, spread_bps=10, slippage_bps=10)
    eng = BacktestEngine(
        cost=cost,
        sizing=SizingPolicy(notional=1000.0),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=True)
    assert len(res.fills) >= 2  # entry + eod close
    entry = res.fills[0]
    q = compute_fill_quote("BUY", bars[1], cost, qty=entry.qty, use_open=True)
    assert entry.fill_price == pytest.approx(q.fill_price)
    assert entry.fee == pytest.approx(q.fee)
    assert entry.slippage_cost == pytest.approx(q.slippage_cost)
    assert res.metrics["fees"] > 0
    assert res.metrics["slippage"] > 0
    assert res.metrics["spread_cost"] > 0
    assert res.metrics["net_return"] < res.metrics["gross_return"] or res.metrics["fees"] > 0


def test_zero_costs_flat_is_flat_mtm_if_not_closed():
    bars = flat_price(6, price=100.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0)
    eng = BacktestEngine(
        cost=cost,
        sizing=SizingPolicy(notional=1000.0),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    assert res.final_equity == pytest.approx(10_000.0)


def test_partial_fill_ratio():
    bars = flat_price(6, price=100.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0, partial_fill_ratio=0.5)
    eng = BacktestEngine(
        cost=cost,
        sizing=SizingPolicy(notional=1000.0),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    assert res.fills
    f = res.fills[0]
    assert f.partial is True
    assert f.qty == pytest.approx(f.intended_qty * 0.5)


def test_latency_delays_fill_index():
    bars = flat_price(8, price=100.0, symbol="BTCUSDT")
    cost0 = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0, latency_bars=0)
    cost1 = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0, latency_bars=1)
    r0 = BacktestEngine(cost=cost0, sizing=SizingPolicy(notional=1000.0)).run(
        BuyAndHoldBTC(), bars, close_at_end=False
    )
    r1 = BacktestEngine(cost=cost1, sizing=SizingPolicy(notional=1000.0)).run(
        BuyAndHoldBTC(), bars, close_at_end=False
    )
    assert r0.fills[0].index == 1  # next bar
    assert r1.fills[0].index == 2  # next + 1 latency bar


def test_limit_not_touched_unfilled():
    bars = flat_price(3, price=100.0)
    sim = ExecutionSimulator(CostModel(fee_bps=0, spread_bps=0, slippage_bps=0))
    order = PendingOrder(
        side="BUY", due_index=0, signal_index=0, order_type="LIMIT", limit_price=90.0, reason="limit"
    )
    fill, why = sim.try_fill(order, bars[0], qty=1.0)
    assert fill is None
    assert why == "LIMIT_NOT_TOUCHED"


def test_limit_touched_fills():
    bars = flat_price(3, price=100.0)
    sim = ExecutionSimulator(CostModel(fee_bps=0, spread_bps=0, slippage_bps=0))
    order = PendingOrder(
        side="BUY", due_index=0, signal_index=0, order_type="LIMIT", limit_price=100.0, reason="limit"
    )
    fill, why = sim.try_fill(order, bars[0], qty=1.0)
    assert why is None
    assert fill is not None
    assert fill.fill_price <= 100.0


def test_no_bar_does_not_invent_fill():
    sim = ExecutionSimulator(CostModel())
    order = PendingOrder(side="BUY", due_index=9, signal_index=0, reason="late")
    fill, why = sim.try_fill(order, None, qty=1.0)
    assert fill is None
    assert "NO_BAR" in (why or "")


def test_quotes_used_when_present():
    from dataclasses import replace

    bars = flat_price(2, price=100.0)
    qbar = replace(bars[0], bid=99.0, ask=101.0)
    cost = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0)
    q = compute_fill_quote("BUY", qbar, cost, qty=1.0)
    assert q.used_quotes is True
    assert q.fill_price == pytest.approx(101.0)
