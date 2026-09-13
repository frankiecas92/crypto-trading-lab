"""Benchmarks, versioning, reproducibility."""

from __future__ import annotations

import pytest

from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.synthetic import noisy_trend
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.exceptions import ResearchError
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC, BuyAndHoldETH
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover
from crypto_lab.strategies.versioning import register_strategy_version


def test_all_four_benchmarks_run():
    btc = noisy_trend(80, symbol="BTCUSDT", seed=1)
    eth = noisy_trend(80, symbol="ETHUSDT", seed=2, start=50.0)
    eng = BacktestEngine()
    r_btc = eng.run(BuyAndHoldBTC(), btc)
    r_eth = eng.run(BuyAndHoldETH(), eth)
    r_sma = eng.run(SMACrossover(fast=5, slow=15, symbol="BTCUSDT"), btc)
    r_mom = eng.run(SimpleMomentum(lookback=5, symbol="BTCUSDT"), btc)
    assert r_btc.strategy_id == "BUY_AND_HOLD_BTC"
    assert r_eth.strategy_id == "BUY_AND_HOLD_ETH"
    assert r_sma.strategy_id == "SMA_CROSS"
    assert r_mom.strategy_id == "MOMENTUM"
    assert r_btc.metrics["n_trades"] >= 1
    # wrong symbol → no fills
    r_wrong = eng.run(BuyAndHoldETH(), btc)
    assert r_wrong.fills == [] or r_wrong.metrics["n_trades"] == 0


def test_versions_are_v001_style():
    assert BuyAndHoldBTC().version() == "BUY_AND_HOLD_BTC_v001"
    assert SMACrossover(fast=10, slow=20).version() == "SMA_CROSS_v001"
    assert SimpleMomentum().version().startswith("MOMENTUM_v")


def test_versioning_refuses_overwrite(tmp_path):
    url = f"sqlite:///{tmp_path / 'ver.db'}"
    reset_engine()
    init_db(url)
    Session = get_session_factory(url)
    s = SMACrossover(fast=10, slow=20)
    with Session() as session:
        row = register_strategy_version(session, s)
        session.commit()
        again = register_strategy_version(session, SMACrossover(fast=10, slow=20))
        assert again.id == row.id
        s2 = SMACrossover(fast=10, slow=20)
        s2.fast = 11  # same version string, different params
        with pytest.raises(ResearchError):
            register_strategy_version(session, s2)


def test_reproducibility_same_seed_same_metrics():
    bars = noisy_trend(100, seed=99, symbol="BTCUSDT")
    eng = BacktestEngine()
    a = eng.run(SMACrossover(fast=8, slow=21, symbol="BTCUSDT"), bars, seed=99)
    b = eng.run(SMACrossover(fast=8, slow=21, symbol="BTCUSDT"), bars, seed=99)
    assert a.metrics["net_return"] == b.metrics["net_return"]
    assert a.metrics["n_trades"] == b.metrics["n_trades"]
    assert [f.fill_price for f in a.fills] == [f.fill_price for f in b.fills]
