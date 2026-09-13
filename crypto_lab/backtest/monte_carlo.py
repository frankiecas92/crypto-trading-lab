"""Monte Carlo on realized trades: bootstrap and shuffle order.

Do NOT use MC to beautify results. FRAGILE if DD/return is order-dependent
(original near the worst of the shuffle distribution).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence

from crypto_lab.backtest.metrics import metrics_from_trade_pnls
from crypto_lab.backtest.types import TradeRecord


def _pnls(trades: Sequence[TradeRecord]) -> List[float]:
    return [t.net_pnl for t in trades]


def run_monte_carlo(
    trades: Sequence[TradeRecord],
    *,
    initial_capital: float,
    n_sims: int = 200,
    seed: int = 42,
    timeframe: str = "1h",
    tail: float = 0.10,
) -> Dict[str, Any]:
    pnls = _pnls(trades)
    if len(pnls) < 2:
        return {
            "label": "INSUFFICIENT_TRADES",
            "n_trades": len(pnls),
            "n_sims": 0,
            "bootstrap": {},
            "shuffle": {},
        }
    rng = random.Random(seed)
    boot_rets: List[float] = []
    boot_dds: List[float] = []
    shuf_rets: List[float] = []
    shuf_dds: List[float] = []

    for _ in range(n_sims):
        sample = [pnls[rng.randrange(len(pnls))] for _ in pnls]
        m = metrics_from_trade_pnls(sample, initial_capital=initial_capital, timeframe=timeframe)
        boot_rets.append(float(m["net_return"]))
        boot_dds.append(float(m["max_dd"]))

        order = list(pnls)
        rng.shuffle(order)
        m2 = metrics_from_trade_pnls(order, initial_capital=initial_capital, timeframe=timeframe)
        shuf_rets.append(float(m2["net_return"]))
        shuf_dds.append(float(m2["max_dd"]))

    orig = metrics_from_trade_pnls(pnls, initial_capital=initial_capital, timeframe=timeframe)
    orig_dd = float(orig["max_dd"])
    orig_ret = float(orig["net_return"])

    shuf_dds_sorted = sorted(shuf_dds)  # more negative = worse
    cutoff_idx = max(0, int(len(shuf_dds_sorted) * tail) - 1)
    # worst tail of DD (most negative)
    dd_cut = shuf_dds_sorted[cutoff_idx] if shuf_dds_sorted else orig_dd
    order_dependent = orig_dd <= dd_cut  # original as bad as worst tail

    def _summ(xs: List[float]) -> Dict[str, float]:
        ys = sorted(xs)
        n = len(ys)
        return {
            "mean": sum(ys) / n,
            "p05": ys[max(0, int(0.05 * n) - 1)] if n else 0.0,
            "p50": ys[n // 2] if n else 0.0,
            "p95": ys[min(n - 1, int(0.95 * n))] if n else 0.0,
            "min": ys[0] if n else 0.0,
            "max": ys[-1] if n else 0.0,
        }

    return {
        "label": "FRAGILE_ORDER_DEPENDENT" if order_dependent else "ORDER_STABLE",
        "n_trades": len(pnls),
        "n_sims": n_sims,
        "seed": seed,
        "original_net_return": orig_ret,
        "original_max_dd": orig_dd,
        "bootstrap": {"net_return": _summ(boot_rets), "max_dd": _summ(boot_dds)},
        "shuffle": {"net_return": _summ(shuf_rets), "max_dd": _summ(shuf_dds)},
        "order_dependent": order_dependent,
        "note": "MC diagnoses path luck / order dependence. Do not use to beautify results.",
    }
