"""Regime *labels* only — infrastructure hooks, not a Phase-4 decision engine.

Labels: BULL / BEAR / SIDEWAYS / HIGH_VOL / LOW_VOL / UNKNOWN
A bar may contribute to a trend label and we also record vol separately
by choosing a single primary label per bar (vol overrides when extreme).
"""

from __future__ import annotations

import bisect
from typing import List, Sequence

from crypto_lab.backtest.features import causal_sma
from crypto_lab.backtest.types import Bar, REGIME_LABELS


def label_regimes(
    bars: Sequence[Bar],
    *,
    trend_period: int = 20,
    vol_period: int = 20,
) -> List[str]:
    """Return one label per bar. Uses only causal information at each i.

    Volatility HIGH/LOW thresholds use an *expanding* median of realized vol
    from bars ≤ i only — never the full-sample median (look-ahead).
    """
    n = len(bars)
    if n == 0:
        return []
    closes = [b.close for b in bars]
    sma = causal_sma(closes, trend_period)
    # realized vol: std of last vol_period simple returns
    rets = [0.0]
    for i in range(1, n):
        rets.append(closes[i] / closes[i - 1] - 1.0 if closes[i - 1] else 0.0)

    vol: List[float | None] = [None] * n
    for i in range(n):
        if i + 1 < vol_period:
            continue
        window = rets[i + 1 - vol_period : i + 1]
        mu = sum(window) / len(window)
        var = sum((x - mu) ** 2 for x in window) / len(window)
        vol[i] = var ** 0.5

    # Expanding (causal) median of known vols at bars ≤ i
    known_sorted: List[float] = []
    labels: List[str] = []
    for i in range(n):
        if vol[i] is not None:
            bisect.insort(known_sorted, float(vol[i]))
        med_vol = known_sorted[len(known_sorted) // 2] if known_sorted else None

        if sma[i] is None or i < 1:
            labels.append("UNKNOWN")
            continue
        # vol overlay — thresholds from causal median only
        if vol[i] is not None and med_vol is not None and med_vol > 0:
            if vol[i] >= 1.5 * med_vol:  # type: ignore[operator]
                labels.append("HIGH_VOL")
                continue
            if vol[i] <= 0.5 * med_vol:  # type: ignore[operator]
                labels.append("LOW_VOL")
                continue
        c = closes[i]
        s = sma[i]
        slope = (sma[i] - sma[i - 1]) / sma[i - 1] if sma[i - 1] else 0.0  # type: ignore[operator]
        band = 0.002
        if s and c > s * (1 + band) and slope > 0:
            labels.append("BULL")
        elif s and c < s * (1 - band) and slope < 0:
            labels.append("BEAR")
        elif s:
            labels.append("SIDEWAYS")
        else:
            labels.append("UNKNOWN")
    return labels


def available_labels() -> tuple[str, ...]:
    return REGIME_LABELS
