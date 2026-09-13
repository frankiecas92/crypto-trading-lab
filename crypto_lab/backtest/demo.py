"""Runnable demo: 4 benchmarks on synthetic (and optional cached OHLCV).

Hardening: synthetic → ENGINE_VALIDATION_ONLY; never EDGE_CONFIRMED;
scientific conclusion stays NO_EVIDENCE_OF_EDGE + INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from crypto_lab.backtest.costs import get_cost_profile, parse_cost_profile_name
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.evidence import (
    EvidenceStatus,
    ScientificConclusion,
    build_evidence_block,
    validate_sample_adequacy,
)
from crypto_lab.backtest.metrics import value_add_vs
from crypto_lab.backtest.monte_carlo import run_monte_carlo
from crypto_lab.backtest.registry import (
    note_strategy_variant,
    persist_experiment,
    registry_counters,
    reproducibility_block,
)
from crypto_lab.backtest.robustness import cost_sensitivity, run_sma_grid
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, chronological_split
from crypto_lab.backtest.synthetic import dataset_version, noisy_trend
from crypto_lab.backtest.walk_forward import WFMode, run_walk_forward
from crypto_lab.config import get_settings
from crypto_lab.data.database import get_session_factory, init_db
from crypto_lab.exceptions import ResearchError
from crypto_lab.reports.artifacts import write_result_artifacts
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.risk.stops import StopPolicy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC, BuyAndHoldETH
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover
from crypto_lab.strategies.versioning import register_strategy_version


def _maybe_load_cached(symbol: str, timeframe: str, limit: int) -> List[Any] | None:
    try:
        from crypto_lab.data.database import get_session_factory as gsf
        from crypto_lab.data.repositories import MarketDataRepository
        from crypto_lab.backtest.types import Bar

        settings = get_settings()
        Session = gsf(settings.database_url)
        with Session() as session:
            rows = MarketDataRepository(session).list_candles(
                symbol=symbol, timeframe=timeframe, source="binance", limit=limit
            )
            if len(rows) < 30:
                return None
            return [
                Bar(
                    symbol=r.symbol,
                    event_time=r.event_time or r.ts,
                    timeframe=r.timeframe,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    source=r.source,
                    received_at=r.received_at,
                )
                for r in rows
            ]
    except Exception:  # noqa: BLE001
        return None


def build_demo_series(
    symbol: str,
    *,
    n: int = 180,
    timeframe: str = "1h",
    seed: int = 7,
    prefer_cached: bool = False,
) -> tuple[list, str]:
    if prefer_cached:
        cached = _maybe_load_cached(symbol, timeframe, n)
        if cached:
            return cached, "cached_ohlcv"
    drift = 0.0008 if symbol.startswith("BTC") else 0.0005
    bars = noisy_trend(
        n, start=100.0 if symbol.startswith("BTC") else 50.0, drift=drift, vol=0.012,
        symbol=symbol, timeframe=timeframe, seed=seed + (0 if symbol.startswith("BTC") else 11),
    )
    return bars, "synthetic_noisy_trend"


def run_demo(
    *,
    out_dir: str | Path = "data/experiments/demo",
    database_url: str | None = None,
    n: int = 180,
    seed: int = 7,
    prefer_cached: bool = False,
    persist: bool = True,
    cost_profile: str | None = None,
) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    profile_name = parse_cost_profile_name(cost_profile or settings.research_cost_profile)
    cost = get_cost_profile(profile_name)
    thresholds = settings.sample_thresholds()
    sizing = SizingPolicy(mode="FIXED_NOTIONAL", notional=10_000.0, max_exposure=1.0)
    stops = StopPolicy()  # none active — reported
    engine = BacktestEngine(cost=cost, sizing=sizing, stops=stops, initial_capital=100_000.0)

    conclusions = [
        ScientificConclusion.NO_EVIDENCE_OF_EDGE.value,
        ScientificConclusion.INSUFFICIENT_EVIDENCE.value,
    ]
    summary: Dict[str, Any] = {
        "hypothesis": (
            "Experimental controls (BH BTC/ETH, SMA, momentum) on synthetic/cached "
            "OHLCV. These are benchmarks, not an edge search."
        ),
        "cost_profile": profile_name.value,
        "cost_assumptions": cost.to_dict(),
        "sizing": sizing.to_dict(),
        "active_stops": stops.active(),
        "seed": seed,
        "symbols": {},
        "overfitting_risk": "MEDIUM",
        "scientific_conclusions": conclusions,
        "scientific_conclusion": " + ".join(conclusions),
        "EVIDENCE_STATUS": EvidenceStatus.ENGINE_VALIDATION_ONLY.value,
        "EDGE_CONFIRMED": False,
        "sample_thresholds": thresholds.to_dict(),
        "execution_model": "OHLCV_NEXT_BAR_OPEN",
        "MODE": settings.mode,
        "LIVE_TRADING": settings.live_trading,
    }

    session = None
    if persist:
        url = database_url or "sqlite:///./data/lab.db"
        init_db(url)
        Session = get_session_factory(url)
        session = Session()

    try:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            bars, origin = build_demo_series(
                symbol, n=n, timeframe="1h", seed=seed, prefer_cached=prefer_cached
            )
            ds_ver = dataset_version(bars)
            spec = chronological_split(len(bars), train_frac=0.6, val_frac=0.2, test_frac=0.2)
            split = ResearchSplit(bars, spec)
            split.set_phase(ResearchPhase.TRAIN)
            train = split.train_bars()
            val = split.validation_bars()
            # Final OOS only — not during train/param selection.
            split.set_phase(ResearchPhase.EVALUATE_OOS)
            locked_params = {"fast": 10, "slow": 30, "symbol": symbol}
            split.unlock_test(confirm="EVALUATE_OOS", locked_parameters=locked_params)
            test = split.test_bars(current_parameters=locked_params)

            strategies = [
                BuyAndHoldBTC() if symbol == "BTCUSDT" else BuyAndHoldETH(),
                SMACrossover(fast=10, slow=30, symbol=symbol),
                SimpleMomentum(lookback=8, threshold=0.0, symbol=symbol),
            ]
            if symbol == "BTCUSDT":
                bh = BuyAndHoldBTC()
            else:
                bh = BuyAndHoldETH()

            for strat in strategies:
                note_strategy_variant(strat.strategy_id, strat.parameters())

            symbol_block: Dict[str, Any] = {
                "dataset": origin,
                "dataset_version": ds_ver,
                "n_bars": len(bars),
                "splits": split.period_dict(),
                "is": {},
                "oos": {},
                "validation": {},
                "walk_forward": {},
                "robustness": {},
                "monte_carlo": {},
                "benchmarks": {},
                "cost_profile": profile_name.value,
            }

            is_results = {}
            oos_results = {}
            for strat in strategies:
                if persist and session is not None:
                    try:
                        register_strategy_version(session, strat)
                    except ResearchError:
                        pass
                is_res = engine.run(
                    strat, train, split_name="train_is", seed=seed, dataset_id=origin
                )
                val_res = (
                    engine.run(
                        strat, val, split_name="validation", seed=seed, dataset_id=origin
                    )
                    if len(val) >= 3
                    else None
                )
                oos_res = engine.run(
                    strat, test, split_name="test_oos", seed=seed, dataset_id=origin
                )
                is_results[strat.strategy_id] = is_res
                oos_results[strat.strategy_id] = oos_res
                art_is = write_result_artifacts(is_res, out / symbol / strat.strategy_id / "is")
                art_oos = write_result_artifacts(oos_res, out / symbol / strat.strategy_id / "oos")
                symbol_block["is"][strat.strategy_id] = {
                    "metrics": is_res.metrics,
                    "version": strat.version(),
                    "artifacts": art_is,
                    "reproducibility": reproducibility_block(
                        dataset_id=origin,
                        random_seed=seed,
                        strategy_version=strat.version(),
                        parameters=strat.parameters(),
                        cost_profile=profile_name.value,
                    ),
                }
                symbol_block["oos"][strat.strategy_id] = {
                    "metrics": oos_res.metrics,
                    "version": strat.version(),
                    "artifacts": art_oos,
                    "reproducibility": reproducibility_block(
                        dataset_id=origin,
                        random_seed=seed,
                        strategy_version=strat.version(),
                        parameters=strat.parameters(),
                        cost_profile=profile_name.value,
                    ),
                }
                if val_res is not None:
                    symbol_block["validation"][strat.strategy_id] = {"metrics": val_res.metrics}

            bh_oos = engine.run(bh, test, split_name="test_oos_bh", seed=seed, dataset_id=origin)
            comps = {}
            for sid, res in oos_results.items():
                comps[sid] = value_add_vs(res, bh_oos)
            symbol_block["benchmarks"]["oos_vs_buy_and_hold"] = comps

            wf = run_walk_forward(
                bars,
                lambda: SMACrossover(fast=10, slow=30, symbol=symbol),
                engine,
                train_size=max(40, len(bars) // 5),
                test_size=max(16, len(bars) // 10),
                warmup_bars=30,
                mode=WFMode.PARAMETERS_FIXED,
            )
            symbol_block["walk_forward"] = wf

            grid = run_sma_grid(
                train,
                symbol=symbol,
                engine_kwargs={
                    "cost": cost,
                    "sizing": sizing,
                    "stops": stops,
                    "initial_capital": 100_000.0,
                },
                fast_min=8,
                fast_max=12,
                fast_step=2,
                slow_min=24,
                slow_max=32,
                slow_step=4,
            )
            sens = cost_sensitivity(
                train,
                lambda: SMACrossover(fast=10, slow=30, symbol=symbol),
                cost,
                initial_capital=100_000.0,
                sizing=sizing,
                stops=stops,
            )
            symbol_block["robustness"] = {"grid": grid, "sensitivity": sens}

            mc_src = None
            mc_tag = None
            for tag, bag in (
                ("SMA_OOS", oos_results),
                ("SMA_IS", is_results),
                ("MOM_OOS", oos_results),
                ("MOM_IS", is_results),
            ):
                key = "SMA_CROSS" if tag.startswith("SMA") else "MOMENTUM"
                res = bag.get(key)
                if res is not None and len(res.trades) >= 2:
                    mc_src, mc_tag = res, tag
                    break
            if mc_src is not None:
                mc = run_monte_carlo(
                    mc_src.trades,
                    initial_capital=100_000.0,
                    n_sims=80,
                    seed=seed,
                    timeframe="1h",
                )
                mc["source"] = mc_tag
                symbol_block["monte_carlo"] = mc
            else:
                symbol_block["monte_carlo"] = {"label": "INSUFFICIENT_TRADES", "n_trades": 0}

            sma_oos = oos_results.get("SMA_CROSS")
            n_trades_obs = int((sma_oos.metrics.get("n_trades") if sma_oos else 0) or 0)
            sample = validate_sample_adequacy(
                thresholds=thresholds,
                n_bars=len(bars),
                n_trades=n_trades_obs,
                period_bars=len(bars),
                oos_bars=len(test),
                wf_windows=int(wf.get("n_windows") or 0),
            )
            evidence = build_evidence_block(dataset_id=origin, sample=sample)
            symbol_block["sample_adequacy"] = sample.to_dict()
            symbol_block["evidence"] = evidence
            # Never EDGE_CONFIRMED for synthetic/demo
            assert evidence["EDGE_CONFIRMED"] is False
            assert evidence["EVIDENCE_STATUS"] != EvidenceStatus.EDGE_CONFIRMED.value

            conclusion_text = (
                "NO_EVIDENCE_OF_EDGE + INSUFFICIENT_EVIDENCE — "
                "engine validation / controls on synthetic or short sample; "
                "not trading evidence; never profitable-edge claim."
            )
            if persist and session is not None:
                sma = SMACrossover(fast=10, slow=30, symbol=symbol)
                persist_experiment(
                    session,
                    hypothesis=summary["hypothesis"],
                    strategy_id=sma.strategy_id,
                    strategy_version=sma.version(),
                    parameters=sma.parameters(),
                    dataset_id=origin,
                    dataset_version=ds_ver,
                    symbol=symbol,
                    timeframe="1h",
                    dates=split.period_dict(),
                    split_periods=split.period_dict(),
                    cost_assumptions=cost.to_dict(),
                    results={
                        "is": symbol_block["is"].get("SMA_CROSS", {}),
                        "oos": symbol_block["oos"].get("SMA_CROSS", {}),
                        "walk_forward": wf.get("aggregate", {}),
                        "robustness": {
                            "grid_label": grid.get("label"),
                            "sens": sens.get("label"),
                        },
                        "monte_carlo": symbol_block.get("monte_carlo", {}).get("label"),
                        "sample_adequacy": sample.to_dict(),
                        "evidence": evidence,
                    },
                    benchmark=comps.get("SMA_CROSS"),
                    conclusion=conclusion_text,
                    random_seed=seed,
                    cost_profile=profile_name.value,
                    extra={
                        "regime_note": "labels only; not a decision engine",
                        "EVIDENCE_STATUS": evidence["EVIDENCE_STATUS"],
                        "EDGE_CONFIRMED": False,
                    },
                )
                session.commit()

            summary["symbols"][symbol] = symbol_block

        risks = []
        for _sym, block in summary["symbols"].items():
            grid_lab = block.get("robustness", {}).get("grid", {}).get("label")
            sens_lab = block.get("robustness", {}).get("sensitivity", {}).get("label")
            mc_lab = block.get("monte_carlo", {}).get("label")
            for sid, is_b in block.get("is", {}).items():
                oos_b = block.get("oos", {}).get(sid, {})
                is_net = (is_b.get("metrics") or {}).get("net_return") or 0.0
                oos_net = (oos_b.get("metrics") or {}).get("net_return") or 0.0
                if is_net > 0 and oos_net < is_net - 0.05:
                    risks.append("IS_OOS_GAP")
            if grid_lab == "SINGLE_PARAMETER_PEAK":
                risks.append("PEAK")
            if sens_lab in {"FRAGILE_TO_COSTS", "FRAGILE"}:
                risks.append("COST_FRAGILE")
            if mc_lab == "FRAGILE_ORDER_DEPENDENT":
                risks.append("MC_ORDER")
        if not risks:
            summary["overfitting_risk"] = "LOW"
        elif len(set(risks)) >= 2:
            summary["overfitting_risk"] = "HIGH"
        else:
            summary["overfitting_risk"] = "MEDIUM"

        # Aggregate evidence: synthetic demo never confirms edge.
        any_synth = any(
            str(b.get("dataset", "")).startswith("synthetic")
            or "synthetic" in str(b.get("dataset", ""))
            for b in summary["symbols"].values()
        )
        primary_ds = next(iter(summary["symbols"].values()), {}).get("dataset", "synthetic")
        sample_any = None
        for b in summary["symbols"].values():
            sa = b.get("sample_adequacy")
            if sa and sa.get("status") == "INSUFFICIENT_SAMPLE":
                sample_any = sa
                break
        from crypto_lab.backtest.evidence import SampleAdequacy, SampleStatus

        sample_obj = None
        if sample_any:
            sample_obj = SampleAdequacy(
                status=SampleStatus.INSUFFICIENT_SAMPLE,
                reasons=list(sample_any.get("reasons") or []),
                thresholds=dict(sample_any.get("thresholds") or {}),
                observed=dict(sample_any.get("observed") or {}),
            )
        evidence_top = build_evidence_block(dataset_id=primary_ds, sample=sample_obj)
        summary["EVIDENCE_STATUS"] = evidence_top["EVIDENCE_STATUS"]
        summary["EDGE_CONFIRMED"] = False
        summary["scientific_conclusions"] = evidence_top["scientific_conclusions"]
        summary["scientific_conclusion"] = evidence_top["scientific_conclusion"]
        summary["evidence"] = evidence_top
        summary["registry_counters"] = registry_counters()
        summary["reproducibility"] = reproducibility_block(
            dataset_id=primary_ds,
            random_seed=seed,
            strategy_version="demo_controls",
            parameters={"n": n, "seed": seed},
            cost_profile=profile_name.value,
        )
        if any_synth:
            assert summary["EVIDENCE_STATUS"] == EvidenceStatus.ENGINE_VALIDATION_ONLY.value
            assert "NO_EVIDENCE_OF_EDGE" in summary["scientific_conclusion"]
            assert "INSUFFICIENT_EVIDENCE" in summary["scientific_conclusion"]
            assert "profitable" not in summary["scientific_conclusion"].lower()

        (out / "demo_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        return summary
    finally:
        if session is not None:
            session.close()
