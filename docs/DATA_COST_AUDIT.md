# Phase 4C — Data & Cost Audit

**Scope:** Audit 4B-REAL historical OHLCV + cost model. Pipeline corrections only.  
**Not in scope:** Phase 5 / Decision Engine / new strategies / SMA or Momentum param changes / git commit of DB rewrites.

**Safety:** `MODE=PAPER`, `LIVE_TRADING=False`. No secrets. Research costs are **configuration assumptions**, not official Binance fee schedule lookups.

**Verdict: `AUDIT_PASS_MINOR_CORRECTION`**

Pipeline fixed (incomplete last-candle skip + LIMIT maker fee). 4B-REAL scientific conclusion (`NO_EVIDENCE_OF_EDGE` + `INSUFFICIENT_EVIDENCE`) is unlikely reversed. Full campaign rerun is **not strictly required** before Phase 5 — Phase 5 is not justified anyway.

Do **not** silently rewrite `data/lab.db` and claim the old 4B-REAL checksums still match. Checksums include the forming last bar. Filtering it would change `record_count` (17520 → 17519) and `checksum` / `dataset_id`.

---

## Last candle (CRITICAL — pipeline fixed)

Real DB `data/lab.db` (gitignored; 4B-REAL ingest):

| Symbol | First open (UTC) | Last open (UTC) | Count | Gaps | Dups | Ordered |
|--------|------------------|-----------------|-------|------|------|---------|
| BTCUSDT 1h | 2024-09-13 08:00:00Z | 2026-09-13 07:00:00Z | 17520 | 0 | 0 | yes |
| ETHUSDT 1h | 2024-09-13 08:00:00Z | 2026-09-13 07:00:00Z | 17520 | 0 | 0 | yes |

Range math: `(last − first) / 1h + 1 = 17520` for both series.

Last bar **close** would be `2026-09-13 08:00:00Z`, but `received_at` is ≈ `07:06–07:07Z`:

- BTC last `received_at` ≈ `2026-09-13 07:06:52Z`
- ETH last `received_at` ≈ `2026-09-13 07:07:13Z`

That is a **forming/incomplete candle**. Pre-fix downloader did not exclude open candles. Dataset quality marked `VALID` because `event_time` (bar open) is not in the future vs wall clock.

**Fix:** skip candles where `event_time + timeframe_delta > now_utc`. Helper `is_candle_closed(event_time, timeframe, now)` in `crypto_lab.data.historical`. `DownloadResult` notes the policy and counts `skipped_open`.

Stored-data audit uses the same helper with `now=received_at`: `received_at < close` ⇒ stored while forming. After the close instant has passed, wall-clock `now` alone cannot detect the stale OHLC snapshot.

**Impact on 4B-REAL:** 1 bar / 17520, TEST tail only (split `[14016, 17520)`). Conclusion `NO_EVIDENCE` is unlikely reversed. Recommend **not** rewriting the 4B-REAL DB in place.

---

## Cost model (research assumptions)

`get_cost_profile` exact values:

| Profile | fee_bps | spread_bps | slippage_bps | maker_fee_bps | taker_fee_bps | latency |
|---------|---------|------------|--------------|---------------|---------------|---------|
| BASE | 10 | 4 | 2 | 10 | 10 | 0 |
| CONSERVATIVE | 15 | 6 | 4 | 12 | 15 | 0 |
| STRESS | 25 | 12 | 10 | 20 | 25 | 0 |

- **EXCHANGE FEES:** `fee_bps` / `maker_fee_bps` / `taker_fee_bps`
- **MARKET FRICTION:** `spread_bps` + `slippage_bps`

Do not mix those buckets. MARKET fills use **taker**. OHLCV-only: next-bar open + half spread each side + slippage. Fee is cash per fill (entry **and** exit), not inside `fill_price`. Spread half on buy + half on sell.

### LIMIT maker-fee correction

`_try_limit` used `fee_rate()` default **taker**. Fixed to `liquidity='maker'`. 4B-REAL strategies are MARKET-only, so campaign fee-role results are unchanged.

### Sensitivity

`scale_cost_model` multiplies `fee_bps`, `spread_bps`, `slippage_bps`, `maker_fee_bps`, `taker_fee_bps` by `factor`. Latency and partial-fill ratio are unchanged.

BASE / +25% / +50% / +100% = factors `1.0` / `1.25` / `1.5` / `2.0`. **STRESS is a separate profile**, not a scale of BASE (e.g. BASE×2 spread = 8 bps vs STRESS spread = 12 bps).

### Round-trip $1000 @ mid 100 (qty=10), BASE MARKET OHLCV

| Side | fill | fee | spread_c | slip_c |
|------|------|-----|----------|--------|
| BUY | 100.04 | ≈1.0004 | 0.2 | 0.2 |
| SELL | 99.96 | ≈0.9996 | 0.2 | 0.2 |

RT fees ≈ 2.0, spread ≈ 0.4, slip ≈ 0.4, **total ≈ 2.8 (0.28% of 1000)**. One fee per fill; no double-counting.

---

## 4B-REAL impact

- Incomplete last bar: 1/17520 in TEST. Unlikely to reverse SMA/Momentum vs buy-and-hold OOS or cost-fragility.
- LIMIT maker fix: **no effect** on 4B-REAL (MARKET executions).
- Cost numbers match what the campaign already used.
- Old checksums (`f153f766…` BTC, `e80343e3…` ETH) describe the **including-forming-bar** series. Do not claim they still match a filtered series.

Phase 5 remains **not justified**.
