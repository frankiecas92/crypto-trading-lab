"""Research campaign orchestrator (Phase 4B).

Protocol:
  load historical → TRAIN/VAL/TEST (TEST locked) → param select on VAL only →
  PARAMETERS_FIXED → EVALUATE_OOS → WF → costs/robustness/MC → registry → evidence.

Reuses BacktestEngine, DataView, costs, ResearchSplit, walk_forward, robustness,
monte_carlo, registry, evidence, benchmarks, historical catalog/identity.
Never EDGE_CONFIRMED / PROFITABLE on thin samples. MODE=PAPER only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.costs import (
    CostModel,
    CostProfileName,
    get_cost_profile,
    parse_cost_profile_name,
)
from crypto_lab.backtest.engine import BacktestEngine
from crypto_lab.backtest.evidence import SampleAdequacy, is_synthetic_dataset
from crypto_lab.backtest.metrics import value_add_vs
from crypto_lab.backtest.monte_carlo import run_monte_carlo
from crypto_lab.backtest.registry import (
    current_git_commit,
    note_strategy_variant,
    persist_experiment,
    registry_counters,
    reproducibility_block,
)
from crypto_lab.backtest.robustness import cost_sensitivity, run_param_grid, run_sma_grid
from crypto_lab.backtest.splits import ResearchPhase, ResearchSplit, chronological_split
from crypto_lab.backtest.synthetic import dataset_version, noisy_trend
from crypto_lab.backtest.types import Bar
from crypto_lab.backtest.walk_forward import WFMode, run_walk_forward
from crypto_lab.config import get_settings
from crypto_lab.data.historical.catalog import candles_to_bars
from crypto_lab.exceptions import ResearchError, SplitLeakageError
from crypto_lab.reports.artifacts import write_result_artifacts
from crypto_lab.research.evidence_agg import aggregate_campaign_evidence, sample_from_campaign_obs
from crypto_lab.research.param_selection import (
    assert_params_fixed_before_oos,
    momentum_neighborhood,
    select_momentum_params,
    select_sma_params,
)
from crypto_lab.risk.sizing import SizingPolicy
from crypto_lab.risk.stops import StopPolicy
from crypto_lab.strategies.buy_and_hold import BuyAndHoldBTC, BuyAndHoldETH
from crypto_lab.strategies.momentum import SimpleMomentum
from crypto_lab.strategies.sma import SMACrossover
from crypto_lab.strategies.versioning import register_strategy_version


@dataclass
class CampaignConfig:
    """Safe defaults for Phase 4B campaigns (small windows)."""

    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframe: str = "1h"
    seed: int = 7
    cost_profile: str = "BASE"
    train_frac: float = 0.6
    val_frac: float = 0.2
    test_frac: float = 0.2
    initial_capital: float = 100_000.0
    notional: float = 10_000.0
    mc_min_trades: int = 5
    mc_sims: int = 80
    persist: bool = True
    out_dir: str = "data/experiments/campaign"
    # Small SMA / momentum grids (neighborhood, not maximize-only search)
    sma_fast_min: int = 8
    sma_fast_max: int = 12
    sma_fast_step: int = 2
    sma_slow_min: int = 24
    sma_slow_max: int = 36
    sma_slow_step: int = 6
    mom_lookback_min: int = 5
    mom_lookback_max: int = 15
    mom_lookback_step: int = 5


def fixture_bars(
    symbol: str,
    *,
    n: int = 120,
    timeframe: str = "1h",
    seed: int = 7,
) -> List[Bar]:
    """Deterministic fixture bars for unit tests (ENGINE_VALIDATION_ONLY)."""
    drift = 0.0008 if symbol.startswith("BTC") else 0.0005
    return noisy_trend(
        n,
        start=100.0 if symbol.startswith("BTC") else 50.0,
        drift=drift,
        vol=0.012,
        symbol=symbol,
        timeframe=timeframe,
        seed=seed + (0 if symbol.startswith("BTC") else 11),
    )


def load_bars_from_catalog(
    session,
    *,
    dataset_id: str | None = None,
    source: str = "binance",
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    max_bars: int = 200,
) -> tuple[List[Bar], str, Dict[str, Any]]:
    """Load OHLCV via Phase 4A catalog/DB. Returns (bars, dataset_id, meta)."""
    from crypto_lab.data.historical.service import HistoricalDataService
    from crypto_lab.data.repositories import HistoricalDatasetRepository, MarketDataRepository

    meta: Dict[str, Any] = {"origin": "catalog"}
    ds_id = dataset_id
    if ds_id:
        row = HistoricalDatasetRepository(session).find_by_dataset_id(ds_id)
        if row is None:
            raise ResearchError(f"unknown dataset_id={ds_id!r}")
        rows = MarketDataRepository(session).list_candles_range(
            source=row.source,
            symbol=row.symbol,
            timeframe=row.timeframe,
            start=row.start_time,
            end=row.end_time,
        )
        from crypto_lab.data.historical.service import _rows_to_candles

        candles = _rows_to_candles(rows)
        bars = candles_to_bars(candles)
        meta.update(
            {
                "source": row.source,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "checksum": row.checksum,
                "data_version": row.data_version,
                "git_commit": row.git_commit,
            }
        )
        return bars[:max_bars] if max_bars else bars, row.dataset_id, meta

    # Prefer most recent catalog entry for symbol/timeframe
    datasets = HistoricalDatasetRepository(session).list_datasets(limit=50)
    match = [
        d
        for d in datasets
        if d.symbol == symbol and d.timeframe == timeframe and d.source == source
    ]
    if match:
        row = match[0]
        return load_bars_from_catalog(session, dataset_id=row.dataset_id, max_bars=max_bars)

    # Empty catalog — optional small download (caller decides)
    meta["origin"] = "empty_catalog"
    return [], "", meta


def maybe_download_small(
    session,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    max_bars: int = 72,
    source: str = "binance",
) -> tuple[List[Bar], str, Dict[str, Any]]:
    """Download a small public window via Phase 4A (safe limits)."""
    from crypto_lab.data.historical.service import HistoricalDataService

    settings = get_settings()
    hard = int(getattr(settings, "historical_hard_max_bars", 2000))
    if max_bars > hard:
        raise ResearchError(f"max_bars={max_bars} exceeds hard cap {hard}")
    svc = HistoricalDataService(session, settings=settings, source=source)
    try:
        payload = svc.download(symbol, timeframe=timeframe, max_bars=max_bars, resume=True)
    finally:
        svc.close()
    ds = payload.get("dataset") or {}
    ds_id = ds.get("dataset_id")
    if not ds_id:
        raise ResearchError("download did not register a dataset")
    bars, did, meta = load_bars_from_catalog(session, dataset_id=ds_id, max_bars=max_bars)
    meta["origin"] = "download"
    meta["download"] = payload.get("download")
    return bars, did, meta


class ResearchCampaign:
    """Orchestrates one multi-symbol research campaign under the Phase 4B protocol."""

    def __init__(
        self,
        config: CampaignConfig | None = None,
        *,
        cost: CostModel | None = None,
    ) -> None:
        self.config = config or CampaignConfig()
        settings = get_settings()
        profile = parse_cost_profile_name(
            self.config.cost_profile or settings.research_cost_profile
        )
        self.cost = cost or get_cost_profile(profile)
        self.profile_name = profile
        self.thresholds = settings.sample_thresholds()
        self.sizing = SizingPolicy(
            mode="FIXED_NOTIONAL",
            notional=self.config.notional,
            max_exposure=1.0,
        )
        self.stops = StopPolicy()
        self.engine = BacktestEngine(
            cost=self.cost,
            sizing=self.sizing,
            stops=self.stops,
            initial_capital=self.config.initial_capital,
        )
        self.settings = settings

    def run_on_bars(
        self,
        series: Dict[str, List[Bar]],
        *,
        dataset_ids: Dict[str, str] | None = None,
        dataset_meta: Dict[str, Dict[str, Any]] | None = None,
        session=None,
    ) -> Dict[str, Any]:
        """Run campaign on pre-loaded bar series (preferred for unit tests)."""
        out = Path(self.config.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        git = current_git_commit()
        summary: Dict[str, Any] = {
            "phase": "4B",
            "hypothesis": (
                "Phase 4B research campaign: evaluate experimental controls "
                "(BH BTC/ETH, SMA, momentum) under strict TRAIN/VAL/TEST, "
                "cost profiles, WF, robustness. Not an edge search."
            ),
            "cost_profile": self.profile_name.value,
            "cost_assumptions": self.cost.to_dict(),
            "cost_profiles_available": [p.value for p in CostProfileName],
            "seed": self.config.seed,
            "MODE": self.settings.mode,
            "LIVE_TRADING": self.settings.live_trading,
            "EDGE_CONFIRMED": False,
            "PROFITABLE": False,
            "symbols": {},
            "execution_model": "OHLCV_NEXT_BAR_OPEN",
            "git_commit": git,
        }
        if self.settings.mode != "PAPER" or self.settings.live_trading:
            raise ResearchError("Campaign requires MODE=PAPER and LIVE_TRADING=False")

        symbol_blocks: Dict[str, Any] = {}
        locked_by_symbol: Dict[str, Dict[str, Any]] = {}

        for symbol, bars in series.items():
            if len(bars) < 20:
                raise ResearchError(f"too few bars for {symbol}: {len(bars)}")
            ds_id = (dataset_ids or {}).get(symbol) or f"fixture_{symbol.lower()}"
            meta = (dataset_meta or {}).get(symbol) or {"origin": "fixture"}
            block = self._run_symbol(
                symbol,
                bars,
                dataset_id=ds_id,
                meta=meta,
                git_commit=git,
                session=session,
                out=out,
            )
            symbol_blocks[symbol] = block
            locked_by_symbol[symbol] = block.get("locked_params") or {}

        summary["symbols"] = symbol_blocks
        summary["locked_params"] = locked_by_symbol

        # Cross-symbol stability (BTC vs ETH)
        btc = symbol_blocks.get("BTCUSDT", {})
        eth = symbol_blocks.get("ETHUSDT", {})
        btc_eth_stable = None
        if btc and eth:
            btc_net = (
                ((btc.get("oos") or {}).get("SMA_CROSS") or {}).get("metrics") or {}
            ).get("net_return")
            eth_net = (
                ((eth.get("oos") or {}).get("SMA_CROSS") or {}).get("metrics") or {}
            ).get("net_return")
            if btc_net is not None and eth_net is not None:
                # Same sign or both near zero → loosely stable
                btc_eth_stable = (btc_net >= 0) == (eth_net >= 0) or (
                    abs(btc_net) < 0.02 and abs(eth_net) < 0.02
                )

        # Overfitting heuristic
        risks: List[str] = []
        for _sym, block in symbol_blocks.items():
            grid_lab = (block.get("robustness") or {}).get("grid", {}).get("label")
            sens_lab = (block.get("robustness") or {}).get("sensitivity", {}).get("label")
            if grid_lab == "SINGLE_PARAMETER_PEAK":
                risks.append("PEAK")
            if sens_lab == "FRAGILE_TO_COSTS":
                risks.append("COST_FRAGILE")
            for sid, is_b in (block.get("is") or {}).items():
                oos_b = (block.get("oos") or {}).get(sid) or {}
                is_net = (is_b.get("metrics") or {}).get("net_return") or 0.0
                oos_net = (oos_b.get("metrics") or {}).get("net_return") or 0.0
                if is_net > 0 and oos_net < is_net - 0.05:
                    risks.append("IS_OOS_GAP")
        if not risks:
            of_risk = "LOW"
        elif len(set(risks)) >= 2:
            of_risk = "HIGH"
        else:
            of_risk = "MEDIUM"
        summary["overfitting_risk"] = of_risk

        # Aggregate evidence from primary (SMA on first symbol)
        primary_sym = next(iter(symbol_blocks))
        primary = symbol_blocks[primary_sym]
        sample = None
        sa = primary.get("sample_adequacy")
        if sa:
            from crypto_lab.backtest.evidence import SampleStatus

            sample = SampleAdequacy(
                status=SampleStatus(sa["status"]),
                reasons=list(sa.get("reasons") or []),
                thresholds=dict(sa.get("thresholds") or {}),
                observed=dict(sa.get("observed") or {}),
            )
        oos_sma = ((primary.get("oos") or {}).get("SMA_CROSS") or {}).get("metrics") or {}
        evidence = aggregate_campaign_evidence(
            dataset_id=primary.get("dataset_id"),
            sample=sample,
            oos_metrics=oos_sma,
            n_trades=int(oos_sma.get("n_trades") or 0),
            max_dd=oos_sma.get("max_dd"),
            param_stability_label=(primary.get("robustness") or {})
            .get("grid", {})
            .get("label"),
            walk_forward=primary.get("walk_forward") or {},
            cost_sensitivity_label=(primary.get("robustness") or {})
            .get("sensitivity", {})
            .get("label"),
            btc_eth_stable=btc_eth_stable,
            temporal_stable="IS_OOS_GAP" not in risks,
            vs_buy_and_hold=(primary.get("benchmarks") or {})
            .get("oos_vs_buy_and_hold", {})
            .get("SMA_CROSS"),
            overfitting_risk=of_risk,
            force_insufficient=True,  # Phase 4B default on small/demo windows
        )
        summary["evidence"] = evidence
        summary["EVIDENCE_STATUS"] = evidence["EVIDENCE_STATUS"]
        summary["scientific_conclusion"] = evidence["scientific_conclusion"]
        summary["scientific_conclusions"] = evidence["scientific_conclusions"]
        summary["EDGE_CONFIRMED"] = False
        summary["PROFITABLE"] = False
        summary["registry_counters"] = registry_counters()
        summary["reproducibility"] = reproducibility_block(
            dataset_id=primary.get("dataset_id"),
            random_seed=self.config.seed,
            strategy_version="campaign_4B",
            parameters={"symbols": list(series.keys())},
            cost_profile=self.profile_name.value,
            git_commit=git,
        )
        summary["btc_eth_stable"] = btc_eth_stable
        summary["limitations"] = [
            "Small/demo windows → INSUFFICIENT_EVIDENCE by design",
            "OHLCV next-bar-open execution model (no venue microstructure)",
            "Parameter selection is a tiny neighborhood grid on VAL only — not an optimizer",
            "Historical download alone ≠ edge; synthetic/fixture = ENGINE_VALIDATION_ONLY",
            "No live trading, no orders, no autonomy",
        ]

        (out / "campaign_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        return summary

    def _run_symbol(
        self,
        symbol: str,
        bars: List[Bar],
        *,
        dataset_id: str,
        meta: Dict[str, Any],
        git_commit: str | None,
        session,
        out: Path,
    ) -> Dict[str, Any]:
        seed = self.config.seed
        spec = chronological_split(
            len(bars),
            train_frac=self.config.train_frac,
            val_frac=self.config.val_frac,
            test_frac=self.config.test_frac,
        )
        split = ResearchSplit(bars, spec)
        split.set_phase(ResearchPhase.TRAIN)
        train = split.train_bars()
        val = split.validation_bars()

        # --- Param selection on VAL only (TEST locked) ---
        assert split.test_locked
        sma_sel = select_sma_params(
            split,
            self.engine,
            symbol=symbol,
            fast_min=self.config.sma_fast_min,
            fast_max=self.config.sma_fast_max,
            fast_step=self.config.sma_fast_step,
            slow_min=self.config.sma_slow_min,
            slow_max=self.config.sma_slow_max,
            slow_step=self.config.sma_slow_step,
        )
        mom_sel = select_momentum_params(
            split,
            self.engine,
            symbol=symbol,
            lookback_min=self.config.mom_lookback_min,
            lookback_max=self.config.mom_lookback_max,
            lookback_step=self.config.mom_lookback_step,
        )
        sma_params = sma_sel["selected_params"]
        mom_params = mom_sel["selected_params"]
        locked_params = {
            "SMA_CROSS": {**sma_params, "symbol": symbol},
            "MOMENTUM": {**mom_params, "symbol": symbol},
        }
        assert_params_fixed_before_oos(
            locked_params["SMA_CROSS"],
            parameters_fixed=bool(sma_sel.get("PARAMETERS_FIXED")),
        )

        # --- Lock params, unlock TEST for OOS ---
        split.set_phase(ResearchPhase.EVALUATE_OOS)
        # Lock using SMA params as primary locked fingerprint for TEST peek guard
        split.unlock_test(
            confirm="EVALUATE_OOS",
            locked_parameters=locked_params["SMA_CROSS"],
        )
        test = split.test_bars(current_parameters=locked_params["SMA_CROSS"])

        # Strategies with FIXED params
        if symbol == "BTCUSDT":
            bh: Any = BuyAndHoldBTC()
        elif symbol == "ETHUSDT":
            bh = BuyAndHoldETH()
        else:
            from crypto_lab.strategies.buy_and_hold import BuyAndHold

            bh = BuyAndHold(symbol)

        sma = SMACrossover(
            fast=int(sma_params["fast"]),
            slow=int(sma_params["slow"]),
            symbol=symbol,
        )
        mom = SimpleMomentum(
            lookback=int(mom_params["lookback"]),
            threshold=float(mom_params.get("threshold", 0.0)),
            symbol=symbol,
        )
        strategies = [bh, sma, mom]
        for strat in strategies:
            note_strategy_variant(strat.strategy_id, strat.parameters())
            if session is not None and self.config.persist:
                try:
                    register_strategy_version(session, strat)
                except Exception:  # noqa: BLE001
                    pass

        block: Dict[str, Any] = {
            "dataset_id": dataset_id,
            "dataset_meta": meta,
            "dataset_version": dataset_version(bars),
            "n_bars": len(bars),
            "splits": split.period_dict(),
            "param_selection": {"SMA_CROSS": sma_sel, "MOMENTUM": mom_sel},
            "locked_params": locked_params,
            "PARAMETERS_FIXED": True,
            "is": {},
            "validation": {},
            "oos": {},
            "walk_forward": {},
            "robustness": {},
            "monte_carlo": {},
            "benchmarks": {},
            "cost_profile": self.profile_name.value,
        }

        is_results = {}
        oos_results = {}
        for strat in strategies:
            is_res = self.engine.run(
                strat, train, split_name="train_is", seed=seed, dataset_id=dataset_id
            )
            val_res = (
                self.engine.run(
                    strat, val, split_name="validation", seed=seed, dataset_id=dataset_id
                )
                if len(val) >= 3
                else None
            )
            oos_res = self.engine.run(
                strat, test, split_name="test_oos", seed=seed, dataset_id=dataset_id
            )
            is_results[strat.strategy_id] = is_res
            oos_results[strat.strategy_id] = oos_res
            art_is = write_result_artifacts(
                is_res, out / symbol / strat.strategy_id / "is"
            )
            art_oos = write_result_artifacts(
                oos_res, out / symbol / strat.strategy_id / "oos"
            )
            block["is"][strat.strategy_id] = {
                "metrics": is_res.metrics,
                "version": strat.version(),
                "artifacts": art_is,
                "reproducibility": reproducibility_block(
                    dataset_id=dataset_id,
                    random_seed=seed,
                    strategy_version=strat.version(),
                    parameters=strat.parameters(),
                    cost_profile=self.profile_name.value,
                    git_commit=git_commit,
                ),
            }
            block["oos"][strat.strategy_id] = {
                "metrics": oos_res.metrics,
                "version": strat.version(),
                "artifacts": art_oos,
                "reproducibility": reproducibility_block(
                    dataset_id=dataset_id,
                    random_seed=seed,
                    strategy_version=strat.version(),
                    parameters=strat.parameters(),
                    cost_profile=self.profile_name.value,
                    git_commit=git_commit,
                ),
            }
            if val_res is not None:
                block["validation"][strat.strategy_id] = {"metrics": val_res.metrics}

        bh_oos = oos_results[bh.strategy_id]
        comps = {sid: value_add_vs(res, bh_oos) for sid, res in oos_results.items()}
        block["benchmarks"]["oos_vs_buy_and_hold"] = comps

        # Walk-forward with FIXED params
        wf = run_walk_forward(
            bars,
            lambda: SMACrossover(
                fast=int(sma_params["fast"]),
                slow=int(sma_params["slow"]),
                symbol=symbol,
            ),
            self.engine,
            train_size=max(40, len(bars) // 5),
            test_size=max(16, len(bars) // 10),
            warmup_bars=max(int(sma_params["slow"]), 30),
            mode=WFMode.PARAMETERS_FIXED,
        )
        block["walk_forward"] = wf

        # Parameter robustness (neighbors on TRAIN — not maximize-only)
        grid = run_sma_grid(
            train,
            symbol=symbol,
            engine_kwargs={
                "cost": self.cost,
                "sizing": self.sizing,
                "stops": self.stops,
                "initial_capital": self.config.initial_capital,
            },
            fast_min=self.config.sma_fast_min,
            fast_max=self.config.sma_fast_max,
            fast_step=self.config.sma_fast_step,
            slow_min=self.config.sma_slow_min,
            slow_max=self.config.sma_slow_max,
            slow_step=self.config.sma_slow_step,
        )
        # Momentum neighborhood robustness
        mom_grid = momentum_neighborhood(
            lookback_min=self.config.mom_lookback_min,
            lookback_max=self.config.mom_lookback_max,
            lookback_step=self.config.mom_lookback_step,
        )
        mom_robust = run_param_grid(
            train,
            lambda p: SimpleMomentum(
                lookback=int(p["lookback"]),
                threshold=float(p.get("threshold", 0.0)),
                symbol=symbol,
            ),
            mom_grid,
            engine_kwargs={
                "cost": self.cost,
                "sizing": self.sizing,
                "stops": self.stops,
                "initial_capital": self.config.initial_capital,
            },
        )
        sens = cost_sensitivity(
            train,
            lambda: SMACrossover(
                fast=int(sma_params["fast"]),
                slow=int(sma_params["slow"]),
                symbol=symbol,
            ),
            self.cost,
            initial_capital=self.config.initial_capital,
            sizing=self.sizing,
            stops=self.stops,
        )
        # Also report CONSERVATIVE / STRESS profile point estimates
        profile_nets: Dict[str, float] = {}
        for pname in (CostProfileName.BASE, CostProfileName.CONSERVATIVE, CostProfileName.STRESS):
            eng = BacktestEngine(
                cost=get_cost_profile(pname),
                sizing=self.sizing,
                stops=self.stops,
                initial_capital=self.config.initial_capital,
            )
            r = eng.run(sma, train, split_name=f"profile_{pname.value}")
            profile_nets[pname.value] = float(r.metrics.get("net_return") or 0.0)

        block["robustness"] = {
            "grid": grid,
            "momentum_grid": mom_robust,
            "sensitivity": sens,
            "profile_nets": profile_nets,
        }

        # Monte Carlo when enough trades
        sma_oos = oos_results.get("SMA_CROSS")
        n_trades_oos = int((sma_oos.metrics.get("n_trades") if sma_oos else 0) or 0)
        mc_src = None
        mc_tag = None
        for tag, bag in (("SMA_OOS", oos_results), ("SMA_IS", is_results), ("MOM_OOS", oos_results)):
            key = "SMA_CROSS" if "SMA" in tag else "MOMENTUM"
            res = bag.get(key)
            if res is not None and len(res.trades) >= self.config.mc_min_trades:
                mc_src, mc_tag = res, tag
                break
        if mc_src is not None:
            mc = run_monte_carlo(
                mc_src.trades,
                initial_capital=self.config.initial_capital,
                n_sims=self.config.mc_sims,
                seed=seed,
                timeframe=self.config.timeframe,
            )
            mc["source"] = mc_tag
            block["monte_carlo"] = mc
        else:
            block["monte_carlo"] = {
                "label": "INSUFFICIENT_TRADES",
                "n_trades": n_trades_oos,
                "note": "Monte Carlo skipped — not enough trades",
            }

        sample = sample_from_campaign_obs(
            thresholds=self.thresholds,
            n_bars=len(bars),
            n_trades=n_trades_oos,
            oos_bars=len(test),
            wf_windows=int(wf.get("n_windows") or 0),
        )
        block["sample_adequacy"] = sample.to_dict()
        block["evidence"] = aggregate_campaign_evidence(
            dataset_id=dataset_id,
            sample=sample,
            oos_metrics=(sma_oos.metrics if sma_oos else {}),
            n_trades=n_trades_oos,
            max_dd=(sma_oos.metrics.get("max_dd") if sma_oos else None),
            param_stability_label=grid.get("label"),
            walk_forward=wf,
            cost_sensitivity_label=sens.get("label"),
            vs_buy_and_hold=comps.get("SMA_CROSS"),
            overfitting_risk="HIGH",
            force_insufficient=True,
        )

        # Registry — dataset_id + git_commit required
        if self.config.persist and session is not None:
            if not dataset_id:
                raise ResearchError("dataset_id required for campaign registry entry")
            if not git_commit and not is_synthetic_dataset(dataset_id):
                # Prefer real git; allow fixture campaigns with explicit None only if synthetic
                git_commit = current_git_commit()
            if not git_commit:
                git_commit = current_git_commit() or "NO_GIT"
            conclusion = (
                "NO_EVIDENCE_OF_EDGE + INSUFFICIENT_EVIDENCE — "
                "Phase 4B campaign on small/demo window; not trading evidence."
            )
            persist_experiment(
                session,
                hypothesis=(
                    "Phase 4B research campaign protocol evaluation "
                    f"for {symbol}"
                ),
                strategy_id=sma.strategy_id,
                strategy_version=sma.version(),
                parameters=sma.parameters(),
                dataset_id=dataset_id,
                dataset_version=block["dataset_version"],
                symbol=symbol,
                timeframe=self.config.timeframe,
                dates=split.period_dict(),
                split_periods=split.period_dict(),
                cost_assumptions=self.cost.to_dict(),
                results={
                    "is": block["is"].get("SMA_CROSS", {}),
                    "oos": block["oos"].get("SMA_CROSS", {}),
                    "walk_forward": wf.get("aggregate", {}),
                    "robustness": {
                        "grid_label": grid.get("label"),
                        "sens": sens.get("label"),
                    },
                    "monte_carlo": block.get("monte_carlo", {}).get("label"),
                    "sample_adequacy": sample.to_dict(),
                    "evidence": block["evidence"],
                    "param_selection": {
                        "SMA_CROSS": sma_params,
                        "MOMENTUM": mom_params,
                    },
                },
                benchmark=comps.get("SMA_CROSS"),
                conclusion=conclusion,
                random_seed=seed,
                cost_profile=self.profile_name.value,
                extra={
                    "phase": "4B",
                    "campaign": True,
                    "EVIDENCE_STATUS": block["evidence"]["EVIDENCE_STATUS"],
                    "EDGE_CONFIRMED": False,
                    "PROFITABLE": False,
                    "git_commit_required": git_commit,
                    "PARAMETERS_FIXED": True,
                },
            )
            # Ensure git_commit column set (persist_experiment uses reproducibility)
            session.commit()
            # Verify required fields
            from crypto_lab.data.models import Experiment
            from sqlalchemy import select

            row = session.execute(
                select(Experiment).order_by(Experiment.id.desc()).limit(1)
            ).scalar_one()
            if not row.dataset_id:
                raise ResearchError("registry entry missing dataset_id")
            if not row.git_commit:
                raise ResearchError("registry entry missing git_commit")

        return block


def run_research_campaign(
    *,
    out_dir: str | Path = "data/experiments/campaign",
    database_url: str | None = None,
    dataset_id: str | None = None,
    symbols: Sequence[str] | None = None,
    timeframe: str = "1h",
    seed: int = 7,
    max_bars: int = 120,
    persist: bool = True,
    use_fixture: bool = False,
    allow_download: bool = False,
    cost_profile: str | None = None,
    n_fixture: int = 120,
) -> Dict[str, Any]:
    """CLI/entry helper: load data then run ResearchCampaign.

    Prefer catalog dataset_id. If empty and allow_download, fetch a small public
    window. Unit tests should pass use_fixture=True (no network).
    """
    from crypto_lab.data.database import get_session_factory, init_db
    from crypto_lab.execution.safety import guard_live_trading

    guard_live_trading()
    settings = get_settings()
    cfg = CampaignConfig(
        symbols=list(symbols) if symbols else list(settings.symbols),
        timeframe=timeframe,
        seed=seed,
        cost_profile=cost_profile or settings.research_cost_profile,
        persist=persist,
        out_dir=str(out_dir),
    )
    campaign = ResearchCampaign(cfg)

    series: Dict[str, List[Bar]] = {}
    dataset_ids: Dict[str, str] = {}
    dataset_meta: Dict[str, Dict[str, Any]] = {}

    session = None
    url = database_url or settings.database_url
    if persist or not use_fixture:
        init_db(url)
        Session = get_session_factory(url)
        session = Session()

    try:
        if use_fixture:
            for sym in cfg.symbols:
                series[sym] = fixture_bars(
                    sym, n=n_fixture, timeframe=timeframe, seed=seed
                )
                dataset_ids[sym] = f"fixture_{sym.lower()}_{timeframe}"
                dataset_meta[sym] = {"origin": "fixture", "synthetic": True}
        else:
            assert session is not None
            for sym in cfg.symbols:
                bars: List[Bar] = []
                did = ""
                meta: Dict[str, Any] = {}
                if dataset_id and len(cfg.symbols) == 1:
                    bars, did, meta = load_bars_from_catalog(
                        session,
                        dataset_id=dataset_id,
                        symbol=sym,
                        timeframe=timeframe,
                        max_bars=max_bars,
                    )
                else:
                    bars, did, meta = load_bars_from_catalog(
                        session,
                        dataset_id=None,
                        symbol=sym,
                        timeframe=timeframe,
                        max_bars=max_bars,
                    )
                if not bars and allow_download:
                    bars, did, meta = maybe_download_small(
                        session,
                        symbol=sym,
                        timeframe=timeframe,
                        max_bars=min(max_bars, 72),
                    )
                if not bars:
                    # Safe fallback: fixture (ENGINE_VALIDATION_ONLY)
                    bars = fixture_bars(sym, n=n_fixture, timeframe=timeframe, seed=seed)
                    did = f"fixture_{sym.lower()}_{timeframe}"
                    meta = {
                        "origin": "fixture_fallback",
                        "synthetic": True,
                        "note": "catalog empty; fixture used (not trading evidence)",
                    }
                series[sym] = bars
                dataset_ids[sym] = did
                dataset_meta[sym] = meta

        summary = campaign.run_on_bars(
            series,
            dataset_ids=dataset_ids,
            dataset_meta=dataset_meta,
            session=session if persist else None,
        )
        return summary
    finally:
        if session is not None:
            session.close()
