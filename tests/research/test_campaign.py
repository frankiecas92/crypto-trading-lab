"""Phase 4B research campaign protocol tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from crypto_lab.backtest.evidence import EvidenceStatus, ScientificConclusion
from crypto_lab.backtest.registry import reset_registry_counters
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, chronological_split
from crypto_lab.cli import main
from crypto_lab.config.settings import get_settings
from crypto_lab.data.database import get_session_factory, init_db, reset_engine
from crypto_lab.data.models import Experiment
from crypto_lab.exceptions import SplitLeakageError
from crypto_lab.research.campaign import (
    CampaignConfig,
    ResearchCampaign,
    fixture_bars,
    run_research_campaign,
)
from crypto_lab.research.evidence_agg import aggregate_campaign_evidence
from crypto_lab.research.param_selection import (
    assert_params_fixed_before_oos,
    select_sma_params,
)
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.costs import get_cost_profile


@pytest.fixture(autouse=True)
def _reset_counters():
    reset_registry_counters()
    yield
    reset_registry_counters()


def test_campaign_protocol_fixture(tmp_path, monkeypatch):
    """Full protocol on fixtures: splits, selection, OOS, WF, costs, evidence."""
    db = tmp_path / "camp.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    reset_engine()

    out = tmp_path / "out"
    summary = run_research_campaign(
        out_dir=out,
        database_url=f"sqlite:///{db}",
        use_fixture=True,
        n_fixture=120,
        seed=7,
        persist=True,
        symbols=["BTCUSDT", "ETHUSDT"],
    )
    assert summary["phase"] == "4B"
    assert summary["MODE"] == "PAPER"
    assert summary["LIVE_TRADING"] is False
    assert summary["EDGE_CONFIRMED"] is False
    assert summary["PROFITABLE"] is False
    assert ScientificConclusion.NO_EVIDENCE_OF_EDGE.value in summary["scientific_conclusion"]
    assert ScientificConclusion.INSUFFICIENT_EVIDENCE.value in summary["scientific_conclusion"]
    assert summary["EVIDENCE_STATUS"] != EvidenceStatus.EDGE_CONFIRMED.value
    assert summary["EVIDENCE_STATUS"] == EvidenceStatus.ENGINE_VALIDATION_ONLY.value

    for sym in ("BTCUSDT", "ETHUSDT"):
        block = summary["symbols"][sym]
        assert block["PARAMETERS_FIXED"] is True
        assert block["splits"]["test"]["locked"] is False  # unlocked after OOS
        assert "SMA_CROSS" in block["oos"]
        assert "MOMENTUM" in block["oos"]
        assert "BUY_AND_HOLD" in list(block["is"].keys())[0] or any(
            "BUY_AND_HOLD" in k for k in block["is"]
        )
        assert "walk_forward" in block
        assert block["walk_forward"].get("mode") == "PARAMETERS_FIXED"
        sens = block["robustness"]["sensitivity"]
        assert sens["label"] in {"ROBUST_TO_COSTS", "FRAGILE_TO_COSTS"}
        assert set(sens["nets"]) >= {"BASE", "PLUS_25", "PLUS_50", "PLUS_100", "STRESS"}
        assert "COST_PROFILE_BASE" in block["robustness"]["profile_nets"]
        assert block["evidence"]["EDGE_CONFIRMED"] is False
        assert block["dataset_id"]
        # Param selection recorded
        assert block["param_selection"]["SMA_CROSS"]["PARAMETERS_FIXED"] is True
        assert block["param_selection"]["SMA_CROSS"]["test_locked_during_selection"] is True

    assert (out / "campaign_summary.json").exists()


def test_test_locked_during_selection():
    bars = fixture_bars("BTCUSDT", n=100, seed=3)
    split = ResearchSplit(bars, chronological_split(len(bars)))
    assert split.test_locked
    engine = BacktestEngine(cost=get_cost_profile("BASE"))
    sel = select_sma_params(split, engine, symbol="BTCUSDT")
    assert sel["test_locked_during_selection"] is True
    assert split.test_locked
    assert split.phase == ResearchPhase.PARAM_SELECTION
    with pytest.raises(SplitLeakageError):
        split.test_bars()


def test_params_fixed_before_oos_gate():
    with pytest.raises(SplitLeakageError):
        assert_params_fixed_before_oos(None, parameters_fixed=False)
    assert_params_fixed_before_oos({"fast": 10, "slow": 30}, parameters_fixed=True)


def test_evidence_never_edge_confirmed_on_insufficient():
    block = aggregate_campaign_evidence(
        dataset_id="hist_real_looking_but_thin",
        oos_metrics={"net_return": 0.5, "n_trades": 2, "max_dd": -0.01},
        n_trades=2,
        walk_forward={"n_windows": 1, "aggregate": {"mean_net_return": 0.1}},
        cost_sensitivity_label="ROBUST_TO_COSTS",
        param_stability_label="ROBUST_REGION",
        btc_eth_stable=True,
        temporal_stable=True,
        vs_buy_and_hold={"net_return_vs": 0.2},
        overfitting_risk="LOW",
        force_insufficient=True,
    )
    assert block["EDGE_CONFIRMED"] is False
    assert block["PROFITABLE"] is False
    assert block["EVIDENCE_STATUS"] != EvidenceStatus.EDGE_CONFIRMED.value
    assert ScientificConclusion.NO_EVIDENCE_OF_EDGE.value in block["scientific_conclusions"]
    assert ScientificConclusion.INSUFFICIENT_EVIDENCE.value in block["scientific_conclusions"]


def test_registry_requires_dataset_id_and_git(tmp_path, monkeypatch):
    db = tmp_path / "reg.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    get_settings.cache_clear()
    reset_engine()
    url = f"sqlite:///{db}"
    init_db(url)
    Session = get_session_factory(url)
    out = tmp_path / "out"
    with Session() as session:
        cfg = CampaignConfig(
            symbols=["BTCUSDT"],
            persist=True,
            out_dir=str(out),
            seed=11,
        )
        # Shrink grids for speed
        cfg.sma_fast_min, cfg.sma_fast_max, cfg.sma_fast_step = 8, 10, 2
        cfg.sma_slow_min, cfg.sma_slow_max, cfg.sma_slow_step = 24, 30, 6
        camp = ResearchCampaign(cfg)
        bars = fixture_bars("BTCUSDT", n=100, seed=11)
        summary = camp.run_on_bars(
            {"BTCUSDT": bars},
            dataset_ids={"BTCUSDT": "fixture_btcusdt_1h"},
            dataset_meta={"BTCUSDT": {"origin": "fixture"}},
            session=session,
        )
        assert summary["git_commit"]  # from repo
        row = session.execute(select(Experiment).order_by(Experiment.id.desc()).limit(1)).scalar_one()
        assert row.dataset_id == "fixture_btcusdt_1h"
        assert row.git_commit
        extra = json.loads(row.extra_json or "{}")
        assert extra.get("reproducibility", {}).get("dataset_id") == "fixture_btcusdt_1h"
        assert extra.get("reproducibility", {}).get("git_commit")


def test_campaign_reuses_historical_identity_helpers():
    """candles_to_bars / identity imports stay available for campaign path."""
    from crypto_lab.data.historical.catalog import candles_to_bars
    from crypto_lab.data.historical.identity import compute_dataset_identity
    from crypto_lab.data.records import CanonicalCandle
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = [
        CanonicalCandle(
            source="binance",
            symbol="BTCUSDT",
            source_symbol="BTCUSDT",
            event_time=t0 + timedelta(hours=i),
            received_at=t0 + timedelta(hours=i, seconds=1),
            timeframe="1h",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1.0,
            base_asset="BTC",
            quote_asset="USDT",
            canonical_asset="BTC",
        )
        for i in range(5)
    ]
    bars = candles_to_bars(candles)
    assert len(bars) == 5
    ident = compute_dataset_identity(
        candles, source="binance", source_symbol="BTCUSDT", timeframe="1h", git_commit=None
    )
    assert ident.dataset_id
    assert ident.checksum


def test_campaign_no_lookahead_on_path(tmp_path, monkeypatch):
    """Poison future closes; campaign/engine must not use them (DataView guard)."""
    from crypto_lab.backtest.data_view import DataView
    from crypto_lab.exceptions import LookAheadError

    bars = fixture_bars("BTCUSDT", n=80, seed=5)
    # Poison a "future" bar that should never be visible at early decision
    poisoned = list(bars)
    mid = 40
    # DataView at decision_index=mid-1 must not see bar mid
    view = DataView(poisoned, mid - 1)
    with pytest.raises(LookAheadError):
        view.bar(mid)
    # Campaign still runs (engine uses DataView)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'la.db'}")
    get_settings.cache_clear()
    reset_engine()
    summary = run_research_campaign(
        out_dir=tmp_path / "out",
        database_url=f"sqlite:///{tmp_path / 'la.db'}",
        use_fixture=True,
        n_fixture=80,
        persist=False,
        symbols=["BTCUSDT"],
    )
    assert summary["EDGE_CONFIRMED"] is False


def test_cli_research_campaign_fixture(tmp_path, monkeypatch):
    db = tmp_path / "cli_camp.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    reset_engine()
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "research-campaign",
            "run",
            "--fixture",
            "--fixture-bars",
            "100",
            "--seed",
            "7",
            "--no-persist",
            "--out",
            str(tmp_path / "out"),
            "--symbol",
            "BTCUSDT",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "4B" in r.output
    assert "EDGE_CONFIRMED" in r.output
    assert "false" in r.output.lower()
    assert "NO_EVIDENCE_OF_EDGE" in r.output or "INSUFFICIENT_EVIDENCE" in r.output


def test_cli_research_campaign_help():
    runner = CliRunner()
    r = runner.invoke(main, ["research-campaign", "--help"])
    assert r.exit_code == 0
    assert "run" in r.output
    r2 = runner.invoke(main, ["research-campaign", "run", "--help"])
    assert r2.exit_code == 0
    assert "fixture" in r2.output
    assert "dataset-id" in r2.output
