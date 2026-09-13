"""Look-ahead, future candle/volume leakage, data leakage."""

from __future__ import annotations

import pytest

from crypto_lab.backtest.data_view import DataView
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.splits import (
    CausalNormalizer,
    ResearchSplit,
    chronological_split,
    forbid_full_dataset_normalize,
)
from crypto_lab.exceptions import LookAheadError, SplitLeakageError
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover
from tests.research.helpers import make_bars


def test_dataview_blocks_future_index():
    bars = make_bars(10)
    view = DataView(bars, 3)
    assert view.current.close == 100.0
    assert len(view) == 4
    with pytest.raises(LookAheadError):
        view.bar(4)
    with pytest.raises(LookAheadError):
        view[5]


def test_dataview_does_not_hold_future_bars():
    bars = make_bars(5, path=[10, 20, 30, 40, 99])
    view = DataView(bars, 2)
    assert [b.close for b in view.available_bars()] == [10, 20, 30]
    with pytest.raises(LookAheadError):
        view.bar(3)


def test_strategy_ignores_poisoned_future_close():
    clean = make_bars(40, path=[100.0] * 40)
    poison = make_bars(40, path=[100.0] * 25 + [10_000.0] * 15)
    sma = SMACrossover(fast=3, slow=8)
    # At index 20, future poison must not change the signal
    s1 = sma.generate_signals(DataView(clean, 20))
    s2 = sma.generate_signals(DataView(poison, 20))
    assert s1.side == s2.side
    assert DataView(poison, 20).sma(8) == pytest.approx(100.0)


def test_strategy_ignores_poisoned_future_high_low_volume():
    clean = make_bars(30)
    poison = make_bars(30, volumes=[1.0] * 15 + [1e9] * 15)
    # mutate high/low on a copy after T
    poison = list(poison)
    from dataclasses import replace

    poison = [
        replace(b, high=1e9, low=0.01, volume=1e9) if i >= 15 else b
        for i, b in enumerate(poison)
    ]
    mom = SimpleMomentum(lookback=5)
    a = mom.generate_signals(DataView(clean, 14))
    b = mom.generate_signals(DataView(poison, 14))
    assert a.side == b.side
    assert DataView(poison, 14).momentum(5) == pytest.approx(0.0)


def test_backtester_future_candle_leakage():
    # Series identical through index 19; poison starts at index 20 (UNKNOWN at T<=19).
    clean = make_bars(40, path=[100 + i * 0.1 for i in range(40)])
    poison = make_bars(40, path=[100 + i * 0.1 for i in range(20)] + [50.0] * 20)
    eng = BacktestEngine()
    r1 = eng.run(SMACrossover(fast=3, slow=6), clean, close_at_end=False)
    r2 = eng.run(SMACrossover(fast=3, slow=6), poison, close_at_end=False)
    sides1 = [s.side for i, s in r1.signals if i <= 19]
    sides2 = [s.side for i, s in r2.signals if i <= 19]
    assert sides1 == sides2
    fills1 = [f.fill_price for f in r1.fills if f.index <= 19]
    fills2 = [f.fill_price for f in r2.fills if f.index <= 19]
    assert fills1 == fills2


def test_positive_return_offset_is_lookahead():
    bars = make_bars(5)
    view = DataView(bars, 2)
    with pytest.raises(LookAheadError):
        view.return_at(1)


def test_test_split_locked_and_full_normalize_forbidden():
    from crypto_lab.backtest.splits import ResearchPhase

    bars = make_bars(100)
    spec = chronological_split(100)
    rs = ResearchSplit(bars, spec)
    assert len(rs.train_bars()) == spec.train_end
    with pytest.raises(SplitLeakageError):
        rs.test_bars()
    # Accidental EVALUATE_OOS during TRAIN is blocked
    with pytest.raises(SplitLeakageError):
        rs.unlock_test(confirm="EVALUATE_OOS")
    rs.set_phase(ResearchPhase.EVALUATE_OOS)
    rs.unlock_test(confirm="EVALUATE_OOS")
    assert len(rs.test_bars()) == 100 - spec.test_start
    with pytest.raises(SplitLeakageError):
        rs.unlock_test(confirm="nope")  # wrong confirm

    forbid_full_dataset_normalize(n_train=60, n_all=100, fitted_on=60)
    with pytest.raises(SplitLeakageError):
        forbid_full_dataset_normalize(n_train=60, n_all=100, fitted_on=100)

    norm = CausalNormalizer()
    train = [b.close for b in bars[:60]]
    norm.fit(train)
    z = norm.transform([100.0])
    assert z[0] == pytest.approx(0.0)
