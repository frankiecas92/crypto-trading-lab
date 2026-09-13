"""Phase 4B-REAL: real-campaign loader, hard-max settings, OOS contamination."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from crypto_lab.backtest.evidence import EvidenceStatus, is_synthetic_dataset
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, chronological_split
from crypto_lab.cli import main
from crypto_lab.config.settings import Settings, get_settings
from crypto_lab.exceptions import ResearchError, SplitLeakageError
from crypto_lab.research.campaign import (
    CampaignConfig,
    ResearchCampaign,
    assert_real_historical_eligible,
    detect_oos_contamination,
    fixture_bars,
)
from crypto_lab.research.evidence_agg import aggregate_campaign_evidence


def test_casual_vs_research_hard_max_settings():
    s = Settings(_env_file=None)
    assert s.historical_hard_max_bars == 2000
    assert s.historical_research_hard_max_bars == 30000
    assert s.effective_historical_hard_max(research=False) == 2000
    assert s.effective_historical_hard_max(research=True) == 30000


def test_research_hard_max_from_env(monkeypatch):
    monkeypatch.setenv("HISTORICAL_HARD_MAX_BARS", "2000")
    monkeypatch.setenv("HISTORICAL_RESEARCH_HARD_MAX_BARS", "25000")
    get_settings.cache_clear()
    s = get_settings()
    assert s.historical_hard_max_bars == 2000
    assert s.historical_research_hard_max_bars == 25000
    assert s.effective_historical_hard_max(research=True) == 25000
    get_settings.cache_clear()


def test_real_loader_rejects_fixtures_as_real_historical_evidence():
    with pytest.raises(ResearchError, match="REAL_HISTORICAL_EVIDENCE"):
        assert_real_historical_eligible("fixture_btcusdt_1h", {"origin": "fixture"})
    with pytest.raises(ResearchError, match="fixture"):
        assert_real_historical_eligible(
            "demo_synth", {"origin": "fixture_fallback", "synthetic": True}
        )
    # Real-looking id is allowed by eligibility helper
    assert_real_historical_eligible(
        "binance_btcusdt_1h_abcd", {"origin": "download_real", "synthetic": False}
    )


def test_campaign_real_flag_rejects_fixture_series(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING", "FALSE")
    get_settings.cache_clear()
    cfg = CampaignConfig(
        symbols=["BTCUSDT"],
        persist=False,
        out_dir=str(tmp_path / "out"),
        real_historical=True,
        force_insufficient=False,
    )
    camp = ResearchCampaign(cfg)
    bars = fixture_bars("BTCUSDT", n=80, seed=1)
    with pytest.raises(ResearchError, match="REAL_HISTORICAL_EVIDENCE"):
        camp.run_on_bars(
            {"BTCUSDT": bars},
            dataset_ids={"BTCUSDT": "fixture_btcusdt_1h"},
            dataset_meta={"BTCUSDT": {"origin": "fixture", "synthetic": True}},
        )


def test_aggregate_rejects_fixture_for_real_historical_status():
    with pytest.raises(ValueError, match="REAL_HISTORICAL_EVIDENCE"):
        aggregate_campaign_evidence(
            dataset_id="fixture_btcusdt_1h",
            oos_metrics={"net_return": 0.01, "n_trades": 40, "max_dd": -0.05},
            n_trades=40,
            walk_forward={"n_windows": 5, "aggregate": {"mean_net_return": 0.0}},
            real_historical=True,
            force_insufficient=False,
            overfitting_risk="LOW",
        )


def test_aggregate_real_historical_status_not_engine_validation():
    block = aggregate_campaign_evidence(
        dataset_id="binance_btcusdt_1h_deadbeef",
        oos_metrics={"net_return": -0.02, "n_trades": 40, "max_dd": -0.1},
        n_trades=40,
        walk_forward={"n_windows": 5, "aggregate": {"mean_net_return": -0.01}},
        cost_sensitivity_label="FRAGILE_TO_COSTS",
        param_stability_label="SINGLE_PARAMETER_PEAK",
        overfitting_risk="HIGH",
        real_historical=True,
        force_insufficient=False,
    )
    assert block["EVIDENCE_STATUS"] == EvidenceStatus.REAL_HISTORICAL_EVIDENCE.value
    assert block["EVIDENCE_STATUS"] != EvidenceStatus.ENGINE_VALIDATION_ONLY.value
    assert block["EDGE_CONFIRMED"] is False
    assert block["PROFITABLE"] is False
    assert "NO_EVIDENCE_OF_EDGE" in block["scientific_conclusion"]


def test_oos_contamination_flag_helper():
    locked = {"fast": 10, "slow": 30, "symbol": "BTCUSDT"}
    assert detect_oos_contamination(locked, locked, test_peeked=True) is None
    viol = detect_oos_contamination(
        locked, {"fast": 12, "slow": 30, "symbol": "BTCUSDT"}, test_peeked=True
    )
    assert viol is not None
    assert viol["OOS_CONTAMINATION"] is True
    assert viol["type"] == "OOS_PARAM_CHANGE_VIOLATION"


def test_oos_contamination_via_split_raises():
    bars = fixture_bars("BTCUSDT", n=80, seed=2)
    rs = ResearchSplit(bars, chronological_split(len(bars)))
    locked = {"fast": 10, "slow": 30}
    rs.set_phase(ResearchPhase.EVALUATE_OOS)
    rs.unlock_test(confirm="EVALUATE_OOS", locked_parameters=locked)
    _ = rs.test_bars(current_parameters=locked)
    with pytest.raises(SplitLeakageError, match="VIOLATION"):
        rs.test_bars(current_parameters={"fast": 8, "slow": 30})
    assert any(v["type"] == "OOS_PARAM_CHANGE_VIOLATION" for v in rs.violations)


def test_cli_run_real_help():
    runner = CliRunner()
    r = runner.invoke(main, ["research-campaign", "run-real", "--help"])
    assert r.exit_code == 0, r.output
    assert "max-bars" in r.output
    assert "years" in r.output
    r2 = runner.invoke(main, ["research-campaign", "run", "--help"])
    assert r2.exit_code == 0
    assert "--real" in r2.output


def test_is_synthetic_still_true_for_fixtures():
    assert is_synthetic_dataset("fixture_btcusdt_1h")
    assert not is_synthetic_dataset("binance_BTCUSDT_1h_abc")
