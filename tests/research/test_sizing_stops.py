"""Sizing, stops, max exposure, no leverage."""

from __future__ import annotations

from dataclasses import replace

import pytest

from crypto_lab.backtest.costs import CostModel
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.synthetic import flat_price
from crypto_lab.backtest.types import PortfolioState
from crypto_lab.risk.sizing import SizingPolicy, size_quantity
from crypto_lab.risk.stops import StopPolicy, check_stops
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC
from tests.research.helpers import make_bars, ohlc_path


def test_fixed_notional_qty():
    port = PortfolioState(cash=100_000)
    q = size_quantity(portfolio=port, price=100.0, policy=SizingPolicy(notional=1_000.0), fee_rate=0.0)
    assert q == pytest.approx(10.0)


def test_fixed_risk_qty():
    port = PortfolioState(cash=100_000)
    pol = SizingPolicy(mode="FIXED_RISK", risk_per_trade=0.01, notional=1_000.0)
    q = size_quantity(portfolio=port, price=100.0, policy=pol, stop_distance=2.0)
    # risk cash = 1000, stop dist 2 → qty 500, but max exposure 1.0 * equity / price = 1000
    # equity = 100000, risk = 1000, qty = 1000/2 = 500
    # max exposure 1.0 → 100000/100 = 1000
    # cash afford 100000/100 = 1000
    assert q == pytest.approx(500.0)


def test_fixed_risk_requires_stop():
    port = PortfolioState(cash=1000)
    with pytest.raises(ValueError):
        size_quantity(
            portfolio=port,
            price=100.0,
            policy=SizingPolicy(mode="FIXED_RISK", risk_per_trade=0.01),
            stop_distance=None,
        )


def test_max_exposure_caps_qty():
    port = PortfolioState(cash=100_000)
    pol = SizingPolicy(notional=80_000.0, max_exposure=0.5)
    q = size_quantity(portfolio=port, price=100.0, policy=pol)
    assert q == pytest.approx(500.0)  # 0.5 * 100000 / 100


def test_no_leverage_caps_to_cash():
    port = PortfolioState(cash=500.0)
    pol = SizingPolicy(notional=10_000.0, max_exposure=1.0)
    q = size_quantity(portfolio=port, price=100.0, policy=pol, fee_rate=0.0)
    assert q == pytest.approx(5.0)


def test_engine_respects_max_exposure():
    bars = flat_price(6, price=100.0, symbol="BTCUSDT")
    eng = BacktestEngine(
        cost=CostModel(fee_bps=0, spread_bps=0, slippage_bps=0),
        sizing=SizingPolicy(notional=80_000.0, max_exposure=0.5),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    # position value <= 0.5 * equity
    for p in res.equity_curve:
        if p.qty:
            assert p.qty * p.close <= p.equity * 0.5 + 1e-6


def test_stop_loss_hits():
    # entry at 100, SL 2% → 98. Price path drops to 97.
    opens = [100.0, 100.0, 99.0, 97.0]
    closes = [100.0, 100.0, 98.5, 97.0]
    bars = ohlc_path(opens, closes)
    # make lows include 97
    bars[3] = replace(bars[3], low=97.0, high=99.0)
    eng = BacktestEngine(
        cost=CostModel(fee_bps=0, spread_bps=0, slippage_bps=0),
        sizing=SizingPolicy(notional=1000.0),
        stops=StopPolicy(stop_loss_pct=0.02),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    reasons = [t.exit_reason for t in res.trades]
    assert "STOP_LOSS" in reasons
    assert res.active_stops["stop_loss"] is True


def test_take_profit_hits():
    opens = [100.0, 100.0, 102.0, 106.0]
    closes = [100.0, 101.0, 104.0, 106.0]
    bars = ohlc_path(opens, closes)
    bars[3] = replace(bars[3], high=106.0, low=104.0)
    eng = BacktestEngine(
        cost=CostModel(fee_bps=0, spread_bps=0, slippage_bps=0),
        sizing=SizingPolicy(notional=1000.0),
        stops=StopPolicy(take_profit_pct=0.05),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    assert "TAKE_PROFIT" in [t.exit_reason for t in res.trades]


def test_trailing_stop_hits():
    # rise then give back
    opens = [100.0, 100.0, 110.0, 104.0]
    closes = [100.0, 108.0, 110.0, 104.0]
    bars = ohlc_path(opens, closes)
    bars[2] = replace(bars[2], high=110.0, low=109.0)
    bars[3] = replace(bars[3], high=105.0, low=103.0)
    eng = BacktestEngine(
        cost=CostModel(fee_bps=0, spread_bps=0, slippage_bps=0),
        sizing=SizingPolicy(notional=1000.0),
        stops=StopPolicy(trailing_stop_pct=0.04),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=False)
    assert "TRAILING_STOP" in [t.exit_reason for t in res.trades]
