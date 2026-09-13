"""Reality tests: synthetic series with known expected math."""

from __future__ import annotations

import pytest

from crypto_lab.backtest.costs import CostModel, compute_fill_quote
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.synthetic import flat_price, perfect_down, perfect_up
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC


def _engine(cost: CostModel, notional: float = 1000.0, capital: float = 10_000.0) -> BacktestEngine:
    return BacktestEngine(
        cost=cost,
        sizing=SizingPolicy(notional=notional, max_exposure=1.0),
        initial_capital=capital,
    )


def test_reality_flat_zero_costs_close_false():
    bars = flat_price(12, price=100.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0)
    res = _engine(cost).run(BuyAndHoldBTC(), bars, close_at_end=False)
    assert res.final_equity == pytest.approx(10_000.0)
    assert res.metrics["net_return"] == pytest.approx(0.0)


def test_reality_flat_known_costs_round_trip():
    bars = flat_price(12, price=100.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=10, spread_bps=10, slippage_bps=10)
    capital = 10_000.0
    notional = 1000.0
    res = _engine(cost, notional, capital).run(BuyAndHoldBTC(), bars, close_at_end=True)
    # Engine sizes on next-bar OPEN (OHLCV limitation), then applies fill quote.
    qty = notional / bars[1].open
    buy = compute_fill_quote("BUY", bars[1], cost, qty=qty, use_open=True)
    cash = capital - buy.fill_price * qty - buy.fee
    sell = compute_fill_quote("SELL", bars[-1], cost, qty=qty, use_open=False)
    cash = cash + sell.fill_price * qty - sell.fee
    assert res.final_equity == pytest.approx(cash, rel=1e-9, abs=1e-6)
    assert res.metrics["net_after_costs"] == pytest.approx(cash / capital - 1.0, abs=1e-8)


def test_reality_perfect_up_positive_net_after_modest_costs():
    bars = perfect_up(20, start=100.0, step=1.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=5, spread_bps=2, slippage_bps=1)
    res = _engine(cost).run(BuyAndHoldBTC(), bars, close_at_end=True)
    # fill at bar1 open = 101; last close = 120; should be clearly profitable
    assert res.metrics["net_return"] > 0
    entry = res.fills[0]
    expected_buy = compute_fill_quote("BUY", bars[1], cost, qty=entry.qty, use_open=True)
    assert entry.fill_price == pytest.approx(expected_buy.fill_price)


def test_reality_perfect_down_negative_net():
    bars = perfect_down(20, start=100.0, step=1.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=5, spread_bps=2, slippage_bps=1)
    res = _engine(cost).run(BuyAndHoldBTC(), bars, close_at_end=True)
    assert res.metrics["net_return"] < 0


def test_reality_zero_cost_perfect_up_matches_price_ratio():
    bars = perfect_up(10, start=100.0, step=1.0, symbol="BTCUSDT")
    cost = CostModel(fee_bps=0, spread_bps=0, slippage_bps=0)
    capital = 10_000.0
    notional = 1000.0
    res = _engine(cost, notional, capital).run(BuyAndHoldBTC(), bars, close_at_end=True)
    # buy at bar1 open=101, sell at last close=110
    qty = notional / 101.0
    expected = capital - 101.0 * qty + 110.0 * qty
    assert res.final_equity == pytest.approx(expected, abs=1e-6)
