# Phase 4B-REAL — Historical Strategy Evaluation (Real Binance Spot)

**Scope:** Run the existing Phase 4B research protocol on **real** Binance Spot public OHLCV for BTCUSDT + ETHUSDT.  
**Not in scope:** Phase 5 / Decision Engine / Paper Trader / orders / money / leverage / private keys / autonomy.

**Safety:** `MODE=PAPER`, `LIVE_TRADING=False`. Evidence class is `REAL_HISTORICAL_EVIDENCE` (not `ENGINE_VALIDATION_ONLY`). Never auto `EDGE_CONFIRMED` / `PROFITABLE`.

---

## Campaign run (this artifact)

| Field | Value |
|-------|-------|
| Phase | 4B-REAL |
| Protocol commit (campaign runtime) | `be9c63d53b626feea6284a77135932a46efd07c5` |
| Seed | 7 |
| Cost profile (primary) | BASE (+ CONSERVATIVE / STRESS sensitivity) |
| Timeframe | 1h |
| Requested window | ~2 years / max 20000 bars |
| Evidence status | **REAL_HISTORICAL_EVIDENCE** |
| Scientific conclusion | **NO_EVIDENCE_OF_EDGE + INSUFFICIENT_EVIDENCE** |
| EDGE_CONFIRMED | false |
| PROFITABLE | false |
| **Consider Phase 5?** | **NO — insufficient / no evidence of edge** |
| MODE / LIVE_TRADING | PAPER / false |
| OOS contamination | false (TEST locked until EVALUATE_OOS; params fixed) |
| Look-ahead (regimes) | Fixed — expanding causal vol median only |

JSON artifacts (gitignored): `data/experiments/campaign_real/campaign_real_summary.json`, per-strategy IS/OOS under the same tree.

---

## Real datasets

| Symbol | dataset_id | Period (UTC) | TF | Bars | Quality | Gaps | Checksum (sha256 prefix) |
|--------|------------|--------------|----|------|---------|------|--------------------------|
| BTCUSDT | `cfd3960bca8cbd332b6d0a4920de7985` | 2024-09-13T08 → 2026-09-13T07 | 1h | 17520 | VALID | 0 (no imputation) | `f153f766…6729e` |
| ETHUSDT | `ce90f26f754173aa5ef8c88b8c7798c6` | 2024-09-13T08 → 2026-09-13T07 | 1h | 17520 | VALID | 0 (no imputation) | `e80343e3…1db8e0` |

Source: Binance Spot public REST only (`data-api.binance.vision`). Resume + pagination. Partial flag: **false** (complete vs request). Casual CLI hard max remains 2000; research path uses `HISTORICAL_RESEARCH_HARD_MAX_BARS=30000`.

---

## TRAIN / VAL / TEST (both symbols, 60/20/20)

| Split | Indices | Period |
|-------|---------|--------|
| TRAIN | [0, 10512) | 2024-09-13 → 2025-11-25 |
| VALIDATION | [10512, 14016) | 2025-11-25 → 2026-04-20 |
| TEST (locked until EVALUATE_OOS) | [14016, 17520) | 2026-04-20 → 2026-09-13 |

Params selected on TRAIN/VAL only → `PARAMETERS_FIXED` → `unlock_test(confirm='EVALUATE_OOS')`.

### Locked params

| Symbol | SMA_CROSS | MOMENTUM |
|--------|-----------|----------|
| BTCUSDT | fast=12, slow=30 | lookback=15, threshold=0 |
| ETHUSDT | fast=12, slow=24 | lookback=15, threshold=0 |

---

## OOS results (TEST, BASE costs)

### BTCUSDT

| Strategy | Net return | Max DD | Trades | Sharpe |
|----------|------------|--------|--------|--------|
| BUY_AND_HOLD_BTC | +0.0030 | -0.032 | 1 | 0.23 |
| SMA_CROSS | **-0.0064** | -0.027 | 65 | -0.56 |
| MOMENTUM | **-0.0620** | -0.076 | 214 | -5.47 |

vs Buy&Hold (OOS net_return_vs): SMA **-0.0094**, Momentum **-0.0649** (both underperform BH).

### ETHUSDT

| Strategy | Net return | Max DD | Trades | Sharpe |
|----------|------------|--------|--------|--------|
| BUY_AND_HOLD_ETH | +0.0093 | -0.039 | 1 | 0.53 |
| SMA_CROSS | +0.0091 | -0.027 | 79 | 0.59 |
| MOMENTUM | **-0.0627** | -0.078 | 225 | -3.90 |

vs Buy&Hold: SMA ≈ **0** (−0.0002), Momentum **-0.072**. SMA roughly matches BH on this OOS slice — **not** an edge claim.

Sample adequacy: **ADEQUATE_SAMPLE** (bars/trades/OOS/WF gates). Adequacy ≠ edge.

---

## Costs / robustness / walk-forward / Monte Carlo

- **Cost profiles (SMA on TRAIN):** BASE / CONSERVATIVE / STRESS — both symbols **FRAGILE_TO_COSTS** (stress deepens losses / erases weak positives).
- **Param neighborhoods:** SMA grid label **ROBUST_REGION** (neighborhood, not maximize-only search).
- **Walk-forward (PARAMETERS_FIXED):** 8 windows each; mean net return BTC ≈ **-1.07%**, ETH ≈ **-0.46%**.
- **Monte Carlo:** ran (enough trades); label **ORDER_STABLE** on available trade sets — does **not** confirm edge.
- **BTC vs ETH stability:** loosely same-sign / near-zero for SMA OOS → `btc_eth_stable=true` (stability ≠ edge).
- **Overfitting risk:** MEDIUM (cost fragility / IS–OOS structure).

Registry: `experiments_run=2`, `strategy_variants_tested=6`, each row has `dataset_id` + `git_commit`.

---

## Evidence separation

| Class | Meaning |
|-------|---------|
| `ENGINE_VALIDATION_ONLY` | Fixtures / synthetic — engine plumbing only |
| `REAL_HISTORICAL_EVIDENCE` | This campaign — real public history evaluation |
| `EDGE_CONFIRMED` / `PROFITABLE` | **Never auto-emitted** |

Fixtures are rejected if labeled as `REAL_HISTORICAL_EVIDENCE`.

---

## Phase 4C audit note (incomplete last bar)

Downloader originally stored the still-forming last 1h bar (`open=2026-09-13 07:00Z`, `received_at≈07:06–07:07Z`; close would be `08:00Z`). Pipeline now skips open candles. Impact: **1 / 17520** bars in TEST tail. Old checksums describe the series **including** that forming bar — do not rewrite `lab.db` and claim they still match. Conclusion `NO_EVIDENCE_OF_EDGE` is unlikely reversed; verdict `AUDIT_PASS_MINOR_CORRECTION`. Full detail: [`DATA_COST_AUDIT.md`](DATA_COST_AUDIT.md).

## Limitations

- OHLCV next-bar-open execution (no microstructure / queue position).
- Tiny VAL neighborhood grid — not an unconstrained optimizer.
- Public Spot history only; no funding, borrow, or cross-venue effects.
- Positive or flat OOS on one slice ≠ tradable edge.
- Regime labels use **causal expanding vol median** (full-sample median look-ahead fixed in 4B-REAL).

---

## Does this justify considering Phase 5?

### **NO.**

SMA does not beat buy-and-hold on BTC OOS, barely matches BH on ETH OOS, is fragile to costs, and walk-forward mean nets are negative. Momentum loses after costs. Default scientific conclusion remains **NO_EVIDENCE_OF_EDGE + INSUFFICIENT_EVIDENCE**. Do **not** proceed to Decision Engine / Paper Trader on this evidence.

---

## CLI

```bash
# Dedicated real campaign (elevated research hard max via settings)
crypto-lab research-campaign run-real --timeframe 1h --years 2 --max-bars 20000

# Equivalent flags on run
crypto-lab research-campaign run --real --max-bars 20000 --timeframe 1h --years 2

# Casual download caps unchanged
# HISTORICAL_MAX_BARS=200  HISTORICAL_HARD_MAX_BARS=2000
# Research-only: HISTORICAL_RESEARCH_HARD_MAX_BARS=30000
```

---

## Reproduce

1. `MODE=PAPER` `LIVE_TRADING=FALSE`
2. `crypto-lab init-db`
3. `crypto-lab research-campaign run-real --years 2 --max-bars 20000 --out data/experiments/campaign_real`
4. Inspect `docs/RESEARCH_CAMPAIGN_REAL.md` + gitignored JSON under `data/experiments/campaign_real/`
