# Phase 4B — Research Campaign / Strategy Evaluation

**Scope:** orchestrate historical strategy evaluation on Phase 4A datasets using Phase 3 engines.  
**Not in Phase 4B:** Decision Engine, Paper Trader, trading API, orders, money, leverage, private keys, autonomy, auto strategy mutation.

**Safety:** `MODE=PAPER`, `LIVE_TRADING=False`. Never emit `EDGE_CONFIRMED` or `PROFITABLE` without strict multi-factor evidence. Default conclusion on small/demo windows: **`NO_EVIDENCE_OF_EDGE` + `INSUFFICIENT_EVIDENCE`**.

---

## Protocol

```
LOAD historical OHLCV (4A catalog / small download / fixture)
  → TRAIN / VALIDATION / TEST   (TEST LOCKED)
  → Param selection on TRAIN/VAL only (grid neighborhood on VAL)
  → PARAMETERS_FIXED
  → unlock_test(confirm='EVALUATE_OOS')
  → OOS on TEST
  → Walk-forward (PARAMETERS_FIXED)
  → Cost profiles BASE/CONSERVATIVE/STRESS + sensitivity
  → Parameter robustness (neighbors)
  → Sample gates (RESEARCH_MIN_*)
  → Monte Carlo (if enough trades)
  → Benchmark vs Buy&Hold (net after costs)
  → Registry (dataset_id + git_commit required)
  → Evidence aggregator → NO_EVIDENCE_OF_EDGE / INSUFFICIENT_EVIDENCE
```

---

## Reused modules (do not duplicate)

| Module | Role |
|--------|------|
| `BacktestEngine` / `DataView` | Causal simulation |
| `costs` | BASE / CONSERVATIVE / STRESS + sensitivity |
| `ResearchSplit` | TEST LOCKED + `EVALUATE_OOS` |
| `walk_forward` | `PARAMETERS_FIXED` (stubs for future param selection) |
| `robustness` | Neighborhood grid + cost sensitivity |
| `monte_carlo` | Bootstrap / shuffle when enough trades |
| `registry` / `evidence` | Persist + evidence status |
| Benchmarks | BuyAndHold BTC/ETH, SMA, Momentum |
| Historical | catalog / identity / snapshots / service |

---

## New layer

```
crypto_lab/research/
  campaign.py          # ResearchCampaign orchestrator
  param_selection.py   # VAL-only grid; PARAMETERS_FIXED
  evidence_agg.py      # Multi-factor aggregator (never EDGE_CONFIRMED on thin samples)
```

---

## Evidence factors

OOS, trade count, costs, drawdown, param stability, walk-forward, cost sensitivity, BTC vs ETH stability, temporal stability, benchmark comparison, overfitting risk.

On small/demo windows the aggregator **forces** insufficient / no-edge conclusions. Historical download alone ≠ edge. Synthetic / fixture → `ENGINE_VALIDATION_ONLY`.

---

## CLI

```bash
# Fixture path (unit / offline; ENGINE_VALIDATION_ONLY)
crypto-lab research-campaign run --fixture --fixture-bars 120 --seed 7 --no-persist

# Prefer existing Phase 4A catalog dataset
crypto-lab research-campaign run --dataset-id <id> --max-bars 120

# If catalog empty, optional small public download (safe caps)
crypto-lab research-campaign run --allow-download --max-bars 72
```

---

## Registry requirements

Every campaign experiment row must include:

- `dataset_id` (required)
- `git_commit` (required)
- seed, strategy_version, params, cost_profile, execution_model, timestamps
- `experiments_run` / `strategy_variants_tested` counters

---

## What Phase 4B does not do

- No Decision Engine / Paper Trader / live loop
- No orders, money, leverage, secrets, autonomy
- No unconstrained optimizer / auto strategy mutation
- No `EDGE_CONFIRMED` on thin samples
