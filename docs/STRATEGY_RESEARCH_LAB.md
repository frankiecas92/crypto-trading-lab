# Phase 3 — Strategy Research Lab

**Scope:** historical strategy research on top of the Phase 1–2 Data Engine.  
**Not in Phase 3:** paper-trading live loops, real order execution, trading APIs, leverage, money management, autonomous agents, unconstrained auto-optimization.

**Safety:** `MODE=PAPER`, `LIVE_TRADING=False`. `guard_live_trading()` remains fail-closed.

Pipeline:

```
DATA → DATA VALIDATION → FEATURES → STRATEGY → SIGNAL
  → PORTFOLIO/SIZING → EXECUTION SIMULATOR → COST MODEL
  → PERFORMANCE ANALYZER → ROBUSTNESS ANALYZER → REPORT
```

---

## DATA AVAILABLE AT T vs UNKNOWN AT T

Decision time **T** is the `event_time` of the last **closed** bar at `decision_index = i`.

| Available at T | Unknown at T |
|----------------|--------------|
| Bars `j <= i` with `event_time <= T` | Any bar `k > i` |
| OHLCV of the closed candle `i` | Future open / high / low / close / volume |
| Causal features on `bars[0..i]` | Any indicator that includes `k > i` |
| Strategy parameters / version | Next-bar fill price (applied **after** T) |

`DataView` stores only the visible prefix. Indexing a future bar raises `LookAheadError`. Tests inject poisoned future close/high/low/volume and assert strategies / the backtester do not use them.

---

## Strategy interface

```python
class Strategy:
    def generate_signals(self, data_view: DataView) -> StrategySignal: ...
    def parameters(self) -> dict: ...
    def version(self) -> str: ...   # e.g. SMA_CROSS_v001
    def metadata(self) -> dict: ...
```

Versions are persisted in `strategy_versions`. Same `(strategy_id, version)` with a **different** config is refused (no silent overwrite).

### Benchmarks (experimental controls — not “find an edge”)

1. `BuyAndHoldBTC` — `BTCUSDT`
2. `BuyAndHoldETH` — `ETHUSDT`
3. `SMACrossover` — fast/slow SMA cross (BUY / FLAT)
4. `SimpleMomentum` — lookback return (BUY / FLAT)

Symbols: **BTCUSDT / ETHUSDT** (Binance venue-native). Timeframes: **1m, 5m, 15m, 1h, 4h** (framework; demos/tests often use 1h or 1m synthetic).

---

## Splits / leakage

`ResearchSplit` exposes TRAIN (in-sample) / VALIDATION / TEST (OOS).

- TEST is **locked** during development APIs.
- Unlock only with `unlock_test(confirm="EVALUATE_OOS")`.
- `CausalNormalizer` must be fit on TRAIN values only. Fitting on the full dataset raises `SplitLeakageError`.

---

## Cost model (configurable)

| Input | Meaning |
|-------|---------|
| `fee_bps` | Fee as bps of fill notional, per fill |
| `spread_bps` | Full bid–ask in bps of mid (half each side) when quotes absent |
| `slippage_bps` | Adverse bps of mid |
| `latency_bars` / `latency_ms` | Extra delay after the standard next-bar fill |
| `partial_fill_ratio` | `(0,1)` partial; `1` full; `0` no fill |

Reports **gross**, **fees**, **slippage**, **spread / other**, and **net after costs (primary)**.

---

## Execution simulator (historical only)

- **MARKET:** fill at next-bar **open** ± spread/slippage (or **ask/bid** when present on that bar).
- **LIMIT:** fill only if the bar range trades through the limit; otherwise unfilled.
- Missing bar / missing OHLC range → **limitation**, never an invented fill.
- **OHLCV-only limitation (always recorded):** signal at close of T; fill at open of T+1+latency plus the spread model.

This is **not** a live or paper broker.

---

## Sizing and stops

- `FIXED_NOTIONAL` — target quote notional.
- `FIXED_RISK` — `qty = (equity × risk_per_trade) / stop_distance`.
- `max_position_qty`, `max_exposure <= 1`. **No leverage** (cash + fee buffer).
- Optional `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`. Active flags are reported.
- Same-bar SL+TP: conservative assumption (SL first). Gap through: fill at `open`.

---

## Metrics (minimum set)

Return, net return, annualized return (when applicable), win/loss rate, profit factor, expectancy, average win/loss, max drawdown, DD duration, Sharpe, Sortino, Calmar (when appropriate), #trades, average hold, fees, slippage, turnover, best/worst trade, MFE/MAE, results by year/month.

**Regime hooks** (`BULL` / `BEAR` / `SIDEWAYS` / `HIGH_VOL` / `LOW_VOL` / `UNKNOWN`) are infrastructure **labels**, not a Phase-4 decision engine.

Every experiment is compared to buy-and-hold (same symbol) and the other controls — **value-add vs BH**, not just absolute PnL.

---

## OOS, walk-forward, robustness, Monte Carlo

- Separate IS vs OOS metrics. TEST untouched until `EVALUATE_OOS`.
- Walk-forward: fixed params on rolling train/test windows; aggregate net, DD, Sharpe, PF, trades (net after costs).
- Parameter neighborhood (e.g. SMA fast 15–25, slow 45–65) → surface → `ROBUST_REGION` vs `SINGLE_PARAMETER_PEAK`.
- Sensitivity (fees / slippage / latency / spread / size) → `FRAGILE` if net collapses on a small shock.
- Monte Carlo: bootstrap trades and shuffle order → DD/return distributions. `FRAGILE_ORDER_DEPENDENT` if the original path is as bad as the worst tail. **Do not use MC to beautify results.**

---

## Registry / reproducibility

SQLite `experiments` (additive) + JSON under `data/experiments/` (gitignored):

`experiment_id`, hypothesis, strategy version, parameters, dataset + version, dates, train/val/test, cost assumptions, results, benchmark, conclusion, git commit, random seed.

Same seed + same data + same params → identical backtest (engine is deterministic).

---

## Reality tests

Synthetic series with known math: flat price, perfect up, perfect down, known fee/spread/slippage. The backtester must match `compute_fill_quote` cash math.

---

## CLI

```bash
crypto-lab experiment demo --bars 180 --seed 7
crypto-lab experiment demo --cached --no-persist   # use Data Engine cache if present
```

Optional live **fetch** remains the existing `crypto-lab data fetch-rest` (public, no API keys). Phase 3 does not add trading endpoints.

---

## What Phase 3 does not do

- No paper-trading live loop (`crypto_lab/paper/` stays a stub)
- No real orders (`BlockedLiveGateway` + safety guard)
- No leverage, no live risk kill-switch
- No unconstrained optimizer / autonomous agent


---

## Hardening (post Phase 3)

1. **Evidence vs validation:** Synthetic/demo → `EVIDENCE_STATUS=ENGINE_VALIDATION_ONLY`. Never `EDGE_CONFIRMED`. Scientific conclusion: `NO_EVIDENCE_OF_EDGE` + `INSUFFICIENT_EVIDENCE`.
2. **Sample gates:** Configurable thresholds in settings (`RESEARCH_MIN_*`). Experiments may be marked `INSUFFICIENT_SAMPLE` with WHY reasons.
3. **Cost profiles:** `COST_PROFILE_BASE` (fee 10 / spread 4 / slip 2 bps), `CONSERVATIVE`, `STRESS`. `maker_fee_bps` / `taker_fee_bps` fields exist.
4. **Cost sensitivity:** BASE, +25%, +50%, +100%, STRESS → `ROBUST_TO_COSTS` / `FRAGILE_TO_COSTS` (does not change edge conclusion).
5. **OOS lock:** `EVALUATE_OOS` blocked during TRAIN/PARAM_SELECTION; param change after TEST peek → VIOLATION / reject re-TEST.
6. **Walk-forward:** `PARAMETERS_FIXED` only; stubs for future TRAIN_PARAMETER_SELECTION → VALIDATION → LOCK → TEST.
7. **Registry counters:** `experiments_run`, `strategy_variants_tested`.
8. **Reproducibility block** on every report: git_commit, dataset_id, random_seed, strategy_version, parameters, cost_profile, execution_model, timestamp.
