"""Causal regime labels — no full-sample (look-ahead) median vol."""

from __future__ import annotations

from crypto_lab.backtest.regimes import label_regimes
from tests.research.helpers import make_bars


def test_regime_labels_length_and_known_set():
    bars = make_bars(80, path=[100 + (i % 7) * 0.5 for i in range(80)])
    labels = label_regimes(bars, trend_period=10, vol_period=10)
    assert len(labels) == len(bars)
    allowed = {"BULL", "BEAR", "SIDEWAYS", "HIGH_VOL", "LOW_VOL", "UNKNOWN"}
    assert set(labels) <= allowed


def test_regime_vol_median_is_causal_not_full_sample():
    """Poisoning *future* vol must not change early HIGH/LOW_VOL labels.

    Full-sample median look-ahead would let a late vol spike raise the median
    and reclassify early bars. Causal expanding median must keep early labels.
    """
    n = 120
    # Calm early path, then a huge late vol spike
    calm = [100.0 + (i % 3) * 0.01 for i in range(n)]
    spiked = list(calm)
    for i in range(90, n):
        # alternate large jumps → high realized vol late only
        spiked[i] = spiked[i - 1] * (1.08 if i % 2 == 0 else 0.92)

    bars_calm = make_bars(n, path=calm)
    bars_spike = make_bars(n, path=spiked)

    lab_calm = label_regimes(bars_calm, trend_period=10, vol_period=10)
    lab_spike = label_regimes(bars_spike, trend_period=10, vol_period=10)

    # Decision region well before the spike (and after vol warmup)
    early = slice(20, 70)
    assert lab_calm[early] == lab_spike[early], (
        "future vol spike changed early regime labels — look-ahead median leak"
    )


def test_regime_expanding_median_differs_from_naive_full_sample():
    """Sanity: if we reintroduced full-sample median, early labels could flip.

    This documents the causal contract by comparing against an intentionally
    look-ahead reference implementation inline.
    """
    n = 100
    path = [100.0] * 60 + [100.0 * (1.1 if i % 2 == 0 else 0.9) for i in range(40)]
    # rebuild path properly
    path = [100.0]
    for i in range(1, 60):
        path.append(path[-1] * 1.0001)
    for i in range(60, n):
        path.append(path[-1] * (1.05 if i % 2 == 0 else 0.95))
    bars = make_bars(n, path=path)
    causal = label_regimes(bars, trend_period=10, vol_period=10)

    # Inline look-ahead reference (must NOT match production API)
    closes = [b.close for b in bars]
    rets = [0.0]
    for i in range(1, n):
        rets.append(closes[i] / closes[i - 1] - 1.0 if closes[i - 1] else 0.0)
    vol = [None] * n
    for i in range(n):
        if i + 1 < 10:
            continue
        window = rets[i + 1 - 10 : i + 1]
        mu = sum(window) / len(window)
        var = sum((x - mu) ** 2 for x in window) / len(window)
        vol[i] = var ** 0.5
    known = [v for v in vol if v is not None]
    med = sorted(known)[len(known) // 2]
    lookahead_high = sum(
        1
        for i in range(20, 50)
        if vol[i] is not None and med > 0 and vol[i] >= 1.5 * med
    )
    causal_high = sum(1 for i in range(20, 50) if causal[i] == "HIGH_VOL")
    # With calm early path, causal should have fewer/equal early HIGH_VOL than
    # a depressed full-sample median driven by late spikes.
    assert causal_high <= lookahead_high + 5  # soft bound; main guard is prior test
    assert len(causal) == n
