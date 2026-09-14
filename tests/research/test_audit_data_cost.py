"""Phase 4C data & cost audit.

Research configuration assumptions — NOT official Binance fee schedule lookups.

Exchange fees vs market friction (must stay separate):
  fee_bps / maker_fee_bps / taker_fee_bps = EXCHANGE FEES
  spread_bps + slippage_bps = MARKET FRICTION

Execution (OHLCV-only): MARKET fills use taker; fill at next-bar open + half
spread each side + slippage. Fee applied once per fill (entry AND exit).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_lab.backtest.costs import (
    CostProfileName,
    compute_fill_quote,
    get_cost_profile,
    scale_cost_model,
)
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.synthetic import flat_price
from crypto_lab.backtest.types import Bar, PendingOrder
from crypto_lab.data.historical.downloader import is_candle_closed
from crypto_lab.execution.simulator import ExecutionSimulator
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC


def test_audit_incomplete_candle_exclusion():
    """Forming last bar (event_time + tf > now) is rejected; closed bars kept.

    Mirrors 4B-REAL: last 1h open 2026-09-13 07:00Z received ~07:06Z
    while close would be 08:00Z.
    """
    now = datetime(2026, 9, 13, 7, 6, tzinfo=timezone.utc)
    forming_open = datetime(2026, 9, 13, 7, 0, tzinfo=timezone.utc)
    closed_open = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    assert is_candle_closed(forming_open, "1h", now) is False
    assert is_candle_closed(closed_open, "1h", now) is True
    # Stored-audit: received_at before close ⇒ incomplete at ingest
    received_at = datetime(2026, 9, 13, 7, 6, 52, tzinfo=timezone.utc)
    assert is_candle_closed(forming_open, "1h", received_at) is False
    close_at = forming_open + timedelta(hours=1)
    assert close_at == datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    assert received_at < close_at


def test_audit_cost_profile_exact_numbers():
    """Exact get_cost_profile values. Research assumptions, not venue lookups.

    EXCHANGE FEES: fee_bps / maker_fee_bps / taker_fee_bps
    MARKET FRICTION: spread_bps + slippage_bps
    """
    base = get_cost_profile(CostProfileName.BASE)
    cons = get_cost_profile(CostProfileName.CONSERVATIVE)
    stress = get_cost_profile(CostProfileName.STRESS)

    assert base.profile_name == CostProfileName.BASE.value
    assert base.fee_bps == 10.0
    assert base.spread_bps == 4.0
    assert base.slippage_bps == 2.0
    assert base.maker_fee_bps == 10.0
    assert base.taker_fee_bps == 10.0
    assert base.latency_bars == 0
    assert base.latency_ms == 0.0
    assert base.partial_fill_ratio == 1.0

    assert cons.profile_name == CostProfileName.CONSERVATIVE.value
    assert cons.fee_bps == 15.0
    assert cons.spread_bps == 6.0
    assert cons.slippage_bps == 4.0
    assert cons.maker_fee_bps == 12.0
    assert cons.taker_fee_bps == 15.0

    assert stress.profile_name == CostProfileName.STRESS.value
    assert stress.fee_bps == 25.0
    assert stress.spread_bps == 12.0
    assert stress.slippage_bps == 10.0
    assert stress.maker_fee_bps == 20.0
    assert stress.taker_fee_bps == 25.0


def test_audit_scale_cost_model_components():
    """scale_cost_model multiplies fee/spread/slip/maker/taker; latency/partial unchanged.

    BASE/+25%/+50%/+100% = factors 1.0 / 1.25 / 1.5 / 2.0.
    STRESS is a separate profile, not a scale of BASE.
    """
    base = get_cost_profile(CostProfileName.BASE, latency_bars=2, partial_fill_ratio=0.8)
    f100 = scale_cost_model(base, 1.0)
    f125 = scale_cost_model(base, 1.25)
    f150 = scale_cost_model(base, 1.50)
    f200 = scale_cost_model(base, 2.0)
    stress = get_cost_profile(CostProfileName.STRESS)

    for scaled, factor in ((f100, 1.0), (f125, 1.25), (f150, 1.5), (f200, 2.0)):
        assert scaled.fee_bps == pytest.approx(10.0 * factor)
        assert scaled.spread_bps == pytest.approx(4.0 * factor)
        assert scaled.slippage_bps == pytest.approx(2.0 * factor)
        assert scaled.maker_fee_bps == pytest.approx(10.0 * factor)
        assert scaled.taker_fee_bps == pytest.approx(10.0 * factor)
        assert scaled.latency_bars == 2
        assert scaled.latency_ms == 0.0
        assert scaled.partial_fill_ratio == 0.8
        assert scaled.profile_name == base.profile_name

    # STRESS is not PLUS_100 of BASE (spread 12 vs 8; slip 10 vs 4; taker 25 vs 20)
    assert stress.spread_bps != pytest.approx(f200.spread_bps)
    assert stress.slippage_bps != pytest.approx(f200.slippage_bps)
    assert stress.taker_fee_bps != pytest.approx(f200.taker_fee_bps)
    assert stress.profile_name != f200.profile_name


def _ohlcv_mid_bar(price: float = 100.0) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        event_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        timeframe="1h",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1.0,
        source="synthetic",
    )


def test_audit_market_round_trip_cost_decomposition():
    """Round-trip $1000 @ mid 100 (qty=10) BASE MARKET OHLCV.

    BUY  fill=100.04 fee≈1.0004 spread_c=0.2 slip_c=0.2
    SELL fill=99.96  fee≈0.9996 spread_c=0.2 slip_c=0.2
    RT fees≈2.0, spread≈0.4, slip≈0.4, total≈2.8 (0.28% of 1000)

    EXCHANGE FEES = fee_bps (taker on MARKET). MARKET FRICTION = half-spread
    each side + slippage. Fee is cash, not baked into fill_price.
    """
    cost = get_cost_profile(CostProfileName.BASE)
    bar = _ohlcv_mid_bar(100.0)
    qty = 10.0
    buy = compute_fill_quote("BUY", bar, cost, qty=qty, liquidity="taker")
    sell = compute_fill_quote("SELL", bar, cost, qty=qty, liquidity="taker")

    assert buy.used_quotes is False
    assert buy.fill_price == pytest.approx(100.04)
    assert buy.fee == pytest.approx(1.0004)
    assert buy.spread_cost == pytest.approx(0.2)
    assert buy.slippage_cost == pytest.approx(0.2)

    assert sell.fill_price == pytest.approx(99.96)
    assert sell.fee == pytest.approx(0.9996)
    assert sell.spread_cost == pytest.approx(0.2)
    assert sell.slippage_cost == pytest.approx(0.2)

    rt_fees = buy.fee + sell.fee
    rt_spread = buy.spread_cost + sell.spread_cost
    rt_slip = buy.slippage_cost + sell.slippage_cost
    rt_total = rt_fees + rt_spread + rt_slip
    assert rt_fees == pytest.approx(2.0)
    assert rt_spread == pytest.approx(0.4)
    assert rt_slip == pytest.approx(0.4)
    assert rt_total == pytest.approx(2.8)
    assert rt_total / 1000.0 == pytest.approx(0.0028)


def test_audit_limit_uses_maker_fee_after_fix():
    """LIMIT fills must charge maker, not default taker.

    CONSERVATIVE: maker 12 bps vs taker 15 bps (distinct by design).
    4B-REAL strategies use MARKET so campaign fee-role results are unchanged.
    """
    bars = flat_price(3, price=100.0)
    cost = get_cost_profile(CostProfileName.CONSERVATIVE)
    assert cost.effective_maker_fee_bps() == 12.0
    assert cost.effective_taker_fee_bps() == 15.0
    sim = ExecutionSimulator(cost)
    order = PendingOrder(
        side="BUY",
        due_index=0,
        signal_index=0,
        order_type="LIMIT",
        limit_price=100.0,
        reason="limit",
    )
    fill, why = sim.try_fill(order, bars[0], qty=1.0)
    assert why is None
    assert fill is not None
    maker_fee = fill.fill_price * fill.qty * cost.fee_rate(liquidity="maker")
    taker_fee = fill.fill_price * fill.qty * cost.fee_rate(liquidity="taker")
    assert fill.fee == pytest.approx(maker_fee)
    assert fill.fee != pytest.approx(taker_fee)
    assert fill.order_type == "LIMIT"


def test_audit_no_double_counting_one_fee_per_fill():
    """Fee applied once per fill (entry AND exit); metrics sum fill.fee only."""
    bars = flat_price(8, price=100.0, symbol="BTCUSDT")
    cost = get_cost_profile(CostProfileName.BASE)
    eng = BacktestEngine(
        cost=cost,
        sizing=SizingPolicy(notional=1000.0),
        initial_capital=10_000.0,
    )
    res = eng.run(BuyAndHoldBTC(), bars, close_at_end=True)
    assert len(res.fills) == 2  # entry + eod exit
    assert all(f.fee > 0 for f in res.fills)
    # one fee field per fill; metrics must not double-count vs trades
    assert res.metrics["fees"] == pytest.approx(sum(f.fee for f in res.fills))
    # spread/slip are friction, not extra fees
    assert res.metrics["spread_cost"] == pytest.approx(sum(f.spread_cost for f in res.fills))
    assert res.metrics["slippage"] == pytest.approx(sum(f.slippage_cost for f in res.fills))
    # fill_price already includes half-spread + slip; fee is cash on top
    for f in res.fills:
        assert f.fee != pytest.approx(0.0)
        # fee is not inside fill_price: fee ≈ fill_price * qty * taker_rate
        expected = f.fill_price * f.qty * cost.fee_rate(liquidity="taker")
        assert f.fee == pytest.approx(expected)
