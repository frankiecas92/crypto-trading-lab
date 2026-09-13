"""Hardening: evidence status, sample gates, cost profiles, OOS lock, WF stubs, registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crypto_lab.backtest.costs import (
    CostModel,
    CostProfileName,
    get_cost_profile,
    scale_cost_model,
)
from crypto_lab.backtest.demo import run_demo
from crypto_lab.backtest.evidence import (
    EvidenceStatus,
    SampleStatus,
    ScientificConclusion,
    assert_not_edge_confirmed_for_synthetic,
    build_evidence_block,
    evidence_status_for_dataset,
    validate_sample_adequacy,
)
from crypto_lab.backtest.registry import (
    note_strategy_variant,
    registry_counters,
    reproducibility_block,
    reset_registry_counters,
)
from crypto_lab.backtest.robustness import cost_sensitivity
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, chronological_split
from crypto_lab.backtest.synthetic import noisy_trend
from crypto_lab.backtest.walk_forward import (
    WFMode,
    WFStage,
    future_wf_pipeline_stub,
    run_future_parameter_selection,
    run_walk_forward,
)
from crypto_lab.config import get_settings
from crypto_lab.exceptions import NotImplementedPhaseError, SplitLeakageError
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.strategies.sma import SMACrossover


def test_synthetic_evidence_is_engine_validation_only():
    assert evidence_status_for_dataset("synthetic_noisy_trend") == EvidenceStatus.ENGINE_VALIDATION_ONLY
    block = build_evidence_block(dataset_id="synthetic_noisy_trend")
    assert block["EVIDENCE_STATUS"] == EvidenceStatus.ENGINE_VALIDATION_ONLY.value
    assert block["EDGE_CONFIRMED"] is False
    assert ScientificConclusion.NO_EVIDENCE_OF_EDGE.value in block["scientific_conclusions"]
    assert ScientificConclusion.INSUFFICIENT_EVIDENCE.value in block["scientific_conclusions"]
    with pytest.raises(ValueError):
        assert_not_edge_confirmed_for_synthetic(EvidenceStatus.EDGE_CONFIRMED, "synthetic_x")


def test_insufficient_sample_reports_why(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("RESEARCH_MIN_BARS", "500")
    monkeypatch.setenv("RESEARCH_MIN_TRADES", "100")
    monkeypatch.setenv("RESEARCH_MIN_OOS_BARS", "80")
    monkeypatch.setenv("RESEARCH_MIN_WF_WINDOWS", "5")
    get_settings.cache_clear()
    th = get_settings().sample_thresholds()
    result = validate_sample_adequacy(
        thresholds=th,
        n_bars=180,
        n_trades=2,
        period_bars=180,
        oos_bars=36,
        wf_windows=2,
    )
    assert result.status == SampleStatus.INSUFFICIENT_SAMPLE
    assert result.reasons
    assert any("too few bars" in r for r in result.reasons)
    assert any("too few trades" in r for r in result.reasons)
    assert any("OOS length" in r for r in result.reasons)
    assert any("walk-forward" in r for r in result.reasons)
    d = result.to_dict()
    assert d["why_insufficient"]
    get_settings.cache_clear()


def test_cost_profiles_base_conservative_stress():
    base = get_cost_profile(CostProfileName.BASE)
    cons = get_cost_profile(CostProfileName.CONSERVATIVE)
    stress = get_cost_profile(CostProfileName.STRESS)
    assert base.profile_name == CostProfileName.BASE.value
    assert base.fee_bps == 10.0 and base.spread_bps == 4.0 and base.slippage_bps == 2.0
    assert base.effective_maker_fee_bps() == 10.0
    assert base.effective_taker_fee_bps() == 10.0
    assert cons.fee_bps > base.fee_bps
    assert stress.fee_bps > cons.fee_bps
    # maker/taker fields exist and may differ on non-BASE profiles
    assert cons.maker_fee_bps is not None and cons.taker_fee_bps is not None
    assert cons.effective_maker_fee_bps() == 12.0
    assert cons.effective_taker_fee_bps() == 15.0
    d = base.to_dict()
    assert "maker_fee_bps" in d and "taker_fee_bps" in d and "profile_name" in d


def test_cost_sensitivity_scales_and_labels():
    bars = noisy_trend(80, seed=4, symbol="BTCUSDT")
    base = get_cost_profile(CostProfileName.BASE)
    out = cost_sensitivity(
        bars,
        lambda: SMACrossover(fast=5, slow=15, symbol="BTCUSDT"),
        base,
    )
    assert out["label"] in {"ROBUST_TO_COSTS", "FRAGILE_TO_COSTS"}
    for k in ("BASE", "PLUS_25", "PLUS_50", "PLUS_100", "STRESS"):
        assert k in out["nets"]
    assert "edge" in out["note"].lower() or "scientific" in out["note"].lower()
    scaled = scale_cost_model(base, 1.25)
    assert scaled.fee_bps == pytest.approx(12.5)


def test_oos_lock_rejects_param_change_after_peek():
    bars = noisy_trend(60, seed=1)
    rs = ResearchSplit(bars, chronological_split(60))
    rs.set_phase(ResearchPhase.TRAIN)
    _ = rs.train_bars()
    with pytest.raises(SplitLeakageError):
        rs.unlock_test(confirm="EVALUATE_OOS")  # accidental during TRAIN
    rs.set_phase(ResearchPhase.PARAM_SELECTION)
    with pytest.raises(SplitLeakageError):
        rs.unlock_test(confirm="EVALUATE_OOS")
    locked = {"fast": 10, "slow": 30}
    rs.set_phase(ResearchPhase.EVALUATE_OOS)
    rs.unlock_test(confirm="EVALUATE_OOS", locked_parameters=locked)
    # peek TEST
    test1 = rs.test_bars(current_parameters=locked)
    assert len(test1) > 0
    # change params → re-TEST rejected / VIOLATION
    changed = {"fast": 12, "slow": 30}
    with pytest.raises(SplitLeakageError, match="VIOLATION"):
        rs.test_bars(current_parameters=changed)
    assert any(v["type"] == "OOS_PARAM_CHANGE_VIOLATION" for v in rs.violations)
    # record_parameter_change also records
    v = rs.record_parameter_change(changed)
    assert v is not None


def test_walk_forward_parameters_fixed_and_stubs():
    bars = noisy_trend(100, seed=2, symbol="BTCUSDT")
    eng = BacktestEngine(cost=get_cost_profile("BASE"))
    out = run_walk_forward(
        bars,
        lambda: SMACrossover(fast=5, slow=15, symbol="BTCUSDT"),
        eng,
        train_size=40,
        test_size=20,
        mode=WFMode.PARAMETERS_FIXED,
    )
    assert out["mode"] == WFMode.PARAMETERS_FIXED.value
    assert WFStage.LOCK.value in out["future_stages"]
    stub = future_wf_pipeline_stub()
    assert stub["implemented"] is False
    with pytest.raises(NotImplementedPhaseError):
        run_future_parameter_selection()
    with pytest.raises(NotImplementedPhaseError):
        run_walk_forward(
            bars,
            lambda: SMACrossover(fast=5, slow=15, symbol="BTCUSDT"),
            eng,
            train_size=40,
            test_size=20,
            mode=WFMode.TRAIN_PARAMETER_SELECTION,
        )


def test_registry_counters_and_reproducibility():
    reset_registry_counters()
    note_strategy_variant("SMA_CROSS", {"fast": 10, "slow": 30})
    note_strategy_variant("SMA_CROSS", {"fast": 10, "slow": 30})  # duplicate
    note_strategy_variant("SMA_CROSS", {"fast": 12, "slow": 30})
    c = registry_counters()
    assert c["strategy_variants_tested"] == 2
    repo = reproducibility_block(
        dataset_id="synthetic_x",
        random_seed=7,
        strategy_version="SMA_CROSS_v001",
        parameters={"fast": 10},
        cost_profile=CostProfileName.BASE.value,
    )
    for k in (
        "git_commit",
        "dataset_id",
        "random_seed",
        "strategy_version",
        "parameters",
        "cost_profile",
        "execution_model",
        "timestamp",
    ):
        assert k in repo


def test_demo_hardening_conclusions(tmp_path, monkeypatch):
    reset_registry_counters()
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.mode == "PAPER"
    assert settings.live_trading is False
    out = tmp_path / "demo"
    summary = run_demo(
        out_dir=out,
        database_url=f"sqlite:///{tmp_path / 'h.db'}",
        n=80,
        seed=3,
        persist=True,
    )
    assert summary["MODE"] == "PAPER"
    assert summary["LIVE_TRADING"] is False
    assert summary["EVIDENCE_STATUS"] == EvidenceStatus.ENGINE_VALIDATION_ONLY.value
    assert summary["EDGE_CONFIRMED"] is False
    assert "NO_EVIDENCE_OF_EDGE" in summary["scientific_conclusion"]
    assert "INSUFFICIENT_EVIDENCE" in summary["scientific_conclusion"]
    assert "profitable" not in summary["scientific_conclusion"].lower()
    assert summary["cost_profile"] == CostProfileName.BASE.value
    assert "reproducibility" in summary
    for k in ("git_commit", "dataset_id", "random_seed", "cost_profile", "execution_model", "timestamp"):
        assert k in summary["reproducibility"]
    assert summary["registry_counters"]["experiments_run"] >= 1
    assert summary["registry_counters"]["strategy_variants_tested"] >= 1
    # Artifact summary carries reproducibility
    art = next(Path(out).rglob("summary.json"))
    payload = json.loads(art.read_text())
    assert "reproducibility" in payload
    # Sample adequacy WHY present for short demo
    for block in summary["symbols"].values():
        sa = block["sample_adequacy"]
        assert sa["status"] == SampleStatus.INSUFFICIENT_SAMPLE.value
        assert sa["why_insufficient"]
    get_settings.cache_clear()


def test_maker_taker_fee_fields_default_equal_but_distinct():
    c = CostModel(fee_bps=10, maker_fee_bps=None, taker_fee_bps=None)
    assert c.effective_maker_fee_bps() == 10.0
    assert c.effective_taker_fee_bps() == 10.0
    c2 = CostModel(fee_bps=10, maker_fee_bps=8, taker_fee_bps=12)
    assert c2.fee_rate(liquidity="maker") == pytest.approx(0.0008)
    assert c2.fee_rate(liquidity="taker") == pytest.approx(0.0012)
