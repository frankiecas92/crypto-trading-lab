"""Campaign evidence aggregator — multi-factor, conservative conclusions.

Considers OOS, trade count, costs, DD, param stability, walk-forward,
cost sensitivity, BTC vs ETH stability, temporal stability, benchmark
comparison, overfitting risk → NO_EVIDENCE_OF_EDGE or INSUFFICIENT_EVIDENCE.

Never EDGE_CONFIRMED for thin / demo / insufficient samples.
Historical download alone ≠ edge. Synthetic = ENGINE_VALIDATION_ONLY.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.evidence import (
    EvidenceStatus,
    SampleAdequacy,
    SampleStatus,
    ScientificConclusion,
    assert_not_edge_or_profitable,
    build_evidence_block,
    is_synthetic_dataset,
    validate_sample_adequacy,
)


def _factor(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"factor": name, "supports_edge": False if not ok else False, "ok": ok, "detail": detail}


def aggregate_campaign_evidence(
    *,
    dataset_id: str | None,
    sample: SampleAdequacy | None = None,
    oos_metrics: Dict[str, Any] | None = None,
    is_metrics: Dict[str, Any] | None = None,
    n_trades: int | None = None,
    max_dd: float | None = None,
    param_stability_label: str | None = None,
    walk_forward: Dict[str, Any] | None = None,
    cost_sensitivity_label: str | None = None,
    btc_eth_stable: bool | None = None,
    temporal_stable: bool | None = None,
    vs_buy_and_hold: Dict[str, Any] | None = None,
    overfitting_risk: str | None = None,
    force_insufficient: bool = False,
    real_historical: bool = False,
) -> Dict[str, Any]:
    """Build campaign evidence. Default conclusion is negative / insufficient.

    ``supports_edge`` is always False in Phase 4B for thin samples — we never
    emit EDGE_CONFIRMED or PROFITABLE here.
    """
    factors: List[Dict[str, Any]] = []
    reasons: List[str] = []

    # Sample adequacy
    if sample is not None and sample.status == SampleStatus.INSUFFICIENT_SAMPLE:
        factors.append(_factor("sample_size", False, "; ".join(sample.reasons) or "insufficient"))
        reasons.extend(sample.reasons or ["insufficient sample"])
    elif sample is not None:
        factors.append(_factor("sample_size", True, "meets configured RESEARCH_MIN_* gates"))
    else:
        factors.append(_factor("sample_size", False, "sample adequacy not evaluated"))
        reasons.append("sample adequacy not evaluated")

    # OOS
    oos = oos_metrics or {}
    oos_net = oos.get("net_return")
    if oos_net is None:
        factors.append(_factor("oos", False, "no OOS metrics"))
        reasons.append("no OOS metrics")
    else:
        # Even positive OOS on thin data does not confirm edge.
        factors.append(
            _factor(
                "oos",
                False,
                f"OOS net_return={oos_net!r} (not sufficient alone for edge)",
            )
        )
        reasons.append("OOS alone does not confirm edge")

    # Trade count
    nt = n_trades if n_trades is not None else int(oos.get("n_trades") or 0)
    if nt < 30:
        factors.append(_factor("trade_count", False, f"n_trades={nt} < 30"))
        reasons.append(f"too few trades: {nt}")
    else:
        factors.append(_factor("trade_count", True, f"n_trades={nt}"))

    # Drawdown
    dd = max_dd if max_dd is not None else oos.get("max_dd")
    if dd is not None and float(dd) < -0.25:
        factors.append(_factor("drawdown", False, f"max_dd={dd}"))
        reasons.append(f"large drawdown: {dd}")
    else:
        factors.append(_factor("drawdown", True, f"max_dd={dd}"))

    # Param stability
    if param_stability_label == "ROBUST_REGION":
        factors.append(_factor("param_stability", True, param_stability_label))
    else:
        factors.append(
            _factor(
                "param_stability",
                False,
                param_stability_label or "unknown / SINGLE_PARAMETER_PEAK",
            )
        )
        if param_stability_label == "SINGLE_PARAMETER_PEAK":
            reasons.append("parameter peak (overfit risk)")

    # Walk-forward
    wf = walk_forward or {}
    n_win = int(wf.get("n_windows") or 0)
    agg = wf.get("aggregate") or {}
    if n_win < 3:
        factors.append(_factor("walk_forward", False, f"n_windows={n_win}"))
        reasons.append(f"too few WF windows: {n_win}")
    else:
        mean_net = agg.get("mean_net_return")
        factors.append(
            _factor("walk_forward", False, f"n_windows={n_win} mean_net={mean_net}")
        )
        reasons.append("WF aggregate not sufficient for edge confirmation")

    # Cost sensitivity
    if cost_sensitivity_label == "ROBUST_TO_COSTS":
        factors.append(_factor("cost_sensitivity", True, cost_sensitivity_label))
    else:
        factors.append(
            _factor(
                "cost_sensitivity",
                False,
                cost_sensitivity_label or "unknown / FRAGILE_TO_COSTS",
            )
        )
        if cost_sensitivity_label == "FRAGILE_TO_COSTS":
            reasons.append("fragile to costs")

    # BTC vs ETH
    if btc_eth_stable is True:
        factors.append(_factor("btc_eth_stability", True, "stable across symbols"))
    elif btc_eth_stable is False:
        factors.append(_factor("btc_eth_stability", False, "unstable across BTC/ETH"))
        reasons.append("BTC vs ETH instability")
    else:
        factors.append(_factor("btc_eth_stability", False, "not compared"))

    # Temporal
    if temporal_stable is True:
        factors.append(_factor("temporal_stability", True, "IS/OOS temporally consistent"))
    else:
        factors.append(_factor("temporal_stability", False, "temporal stability unproven"))
        reasons.append("temporal stability unproven")

    # Benchmark
    vs = vs_buy_and_hold or {}
    net_vs = vs.get("net_return_vs")
    if net_vs is not None and float(net_vs) > 0:
        factors.append(
            _factor("benchmark", False, f"net_return_vs_BH={net_vs} (not edge alone)")
        )
        reasons.append("positive vs BH on thin sample ≠ edge")
    else:
        factors.append(_factor("benchmark", False, f"net_return_vs_BH={net_vs}"))
        reasons.append("does not beat buy-and-hold (or unknown)")

    # Overfitting
    of = (overfitting_risk or "HIGH").upper()
    if of == "HIGH":
        factors.append(_factor("overfitting_risk", False, of))
        reasons.append("HIGH overfitting risk")
    elif of == "MEDIUM":
        factors.append(_factor("overfitting_risk", False, of))
        reasons.append("MEDIUM overfitting risk")
    else:
        factors.append(_factor("overfitting_risk", True, of))

    # Fixtures cannot be labeled REAL_HISTORICAL_EVIDENCE
    if real_historical and is_synthetic_dataset(dataset_id):
        raise ValueError(
            "fixtures/synthetic datasets cannot be REAL_HISTORICAL_EVIDENCE "
            f"(dataset_id={dataset_id!r})"
        )

    # Base evidence block (never EDGE_CONFIRMED)
    block = build_evidence_block(
        dataset_id=dataset_id, sample=sample, real_historical=real_historical
    )
    block["EDGE_CONFIRMED"] = False
    block["PROFITABLE"] = False

    # Phase 4B: thin/demo windows → almost always insufficient / no evidence.
    # Real historical campaigns still default to no-edge unless truly strong
    # (and still never auto EDGE_CONFIRMED / PROFITABLE).
    insufficient = (
        force_insufficient
        or (sample is not None and sample.status == SampleStatus.INSUFFICIENT_SAMPLE)
        or is_synthetic_dataset(dataset_id)
        or nt < 30
        or n_win < 3
        or of in {"HIGH", "MEDIUM"}
    )

    conclusions = [
        ScientificConclusion.NO_EVIDENCE_OF_EDGE.value,
        ScientificConclusion.INSUFFICIENT_EVIDENCE.value,
    ]
    # Never promote to EDGE_CONFIRMED
    status = block["EVIDENCE_STATUS"]
    if status == EvidenceStatus.EDGE_CONFIRMED.value:
        status = (
            EvidenceStatus.ENGINE_VALIDATION_ONLY.value
            if is_synthetic_dataset(dataset_id)
            else (
                EvidenceStatus.REAL_HISTORICAL_EVIDENCE.value
                if real_historical
                else EvidenceStatus.TRADING_EVIDENCE_CANDIDATE.value
            )
        )
    if real_historical and not is_synthetic_dataset(dataset_id):
        status = EvidenceStatus.REAL_HISTORICAL_EVIDENCE.value
    elif insufficient and not is_synthetic_dataset(dataset_id):
        # Real history on thin window: candidate at most, still no edge.
        if status == EvidenceStatus.ENGINE_VALIDATION_ONLY.value:
            pass
        else:
            status = EvidenceStatus.TRADING_EVIDENCE_CANDIDATE.value

    block["EVIDENCE_STATUS"] = status
    block["scientific_conclusions"] = conclusions
    block["scientific_conclusion"] = " + ".join(conclusions)
    block["factors"] = factors
    block["why"] = reasons
    block["campaign"] = True
    block["real_historical"] = bool(real_historical)
    block["note"] = (
        "Phase 4B research campaign: multi-factor aggregator. "
        "ENGINE_VALIDATION_ONLY is for fixtures/synthetic; "
        "REAL_HISTORICAL_EVIDENCE is evaluation on public historical OHLCV — "
        "still not EDGE_CONFIRMED / PROFITABLE without strict multi-factor evidence."
    )
    assert_not_edge_or_profitable(block)
    if block.get("EVIDENCE_STATUS") == EvidenceStatus.EDGE_CONFIRMED.value:
        raise ValueError("campaign aggregator must never emit EDGE_CONFIRMED")
    return block


def sample_from_campaign_obs(
    *,
    thresholds,
    n_bars: int,
    n_trades: int,
    oos_bars: int,
    wf_windows: int,
) -> SampleAdequacy:
    return validate_sample_adequacy(
        thresholds=thresholds,
        n_bars=n_bars,
        n_trades=n_trades,
        period_bars=n_bars,
        oos_bars=oos_bars,
        wf_windows=wf_windows,
    )
