# Phase 4A — Historical Dataset Pipeline

**Scope:** download, validate, store, version, and snapshot **public** OHLCV history.  
**Not in Phase 4A:** Decision Engine, paper trader live loop, trading API, real orders, money, leverage, private keys, auto strategy mutation, EDGE_CONFIRMED / PROFITABLE claims.

**Safety:** `MODE=PAPER`, `LIVE_TRADING=False`. Default fail = no trade.  
Downloading real history is **not** trading evidence.

---

## How to run

```bash
source .venv/bin/activate
crypto-lab init-db
crypto-lab historical-data --help
crypto-lab historical-data download --help
```

### Download (small safe defaults)

Default: Binance `BTCUSDT` **1h**, last **3 days**, **max 200 bars**. Huge ranges are refused.

```bash
# Demo-sized 1h window (public REST, no API key)
crypto-lab historical-data download --source binance --symbol BTCUSDT --timeframe 1h --max-bars 72

# Coinbase venue-native USD (NOT the same market as BTCUSDT)
crypto-lab historical-data download --source coinbase --symbol BTC-USD --timeframe 1h --max-bars 72

# Explicit UTC window (still capped by --max-bars)
crypto-lab historical-data download \
  --source binance --symbol ETHUSDT --timeframe 1h \
  --start 2024-01-01T00:00:00Z --end 2024-01-03T00:00:00Z --max-bars 50
```

Resume is on by default (already-stored timestamps are skipped; no duplicate candles).

### Validate / status / snapshot

```bash
crypto-lab historical-data validate --dataset-id <id>
crypto-lab historical-data status
crypto-lab historical-data status --dataset-id <id>
crypto-lab historical-data snapshot --dataset-id <id>
```

A **snapshot** records the exact dataset used: `dataset_id`, `data_version`, source, symbol, timeframe, range, `record_count`, `checksum`, `git_commit`.

### Reproduce

1. Same source + venue-native symbol + timeframe + `[start, end]` + `data_version`
2. Same candle content → same `checksum` → same `dataset_id`
3. `snapshot` writes that identity; a later snapshot of the same stored rows must match `checksum`

---

## Sources and identity (do not mix markets)

| Source | Symbols | Quote | Notes |
|--------|---------|-------|--------|
| Binance (public) | `BTCUSDT`, `ETHUSDT` | USDT | Primary |
| Coinbase (public) | `BTC-USD`, `ETH-USD` | USD | Separate market |

`canonical_asset` is asset-level only (`BTC` / `ETH`). Coinbase `BTC-USD` is **never** stored as `BTCUSDT`.

Timeframes: `1m`, `5m`, `15m`, `1h`, `4h`. Causal resample `1m → 5m/15m/1h/4h` uses only bars with `event_time <=` output candle close (left-closed, right-open). Incomplete buckets are omitted — **no invented fills**.

---

## Validations

Dataset status: `VALID` / `VALID_WITH_WARNINGS` / `INVALID`.

Critical (never `VALID`): invalid OHLC, future timestamps, negative / impossible prices, invalid volume, duplicates, invalid symbol, naive timestamps.

Gaps are **recorded** (`gap_start`, `gap_end`, expected/actual records, duration, severity). They are **not** interpolated as real market data.

Open / incomplete candles are **excluded** at download: `event_time + timeframe_delta > now_utc` (forming bar is not stored). See Phase 4C [`DATA_COST_AUDIT.md`](DATA_COST_AUDIT.md).

`event_time` (exchange) ≠ `received_at` (local receive). Historical backfill is expected to have `event_time` ≪ `received_at`; that lag is not a critical error.

---

## TEST lock

Catalog rows can attach TRAIN / VALIDATION / TEST roles (same idea as Phase 3 `ResearchSplit`).

- **TEST is LOCKED** until explicit `confirm='EVALUATE_OOS'`
- Parameter selection **cannot** use locked TEST
- Phase 3 `ResearchSplit` API is unchanged

---

## Dataset versioning

`dataset_id` is a content hash of:

`source, source_symbol, base, quote, canonical_asset, timeframe, start, end, record_count, data_version, git_commit (if any), checksum`

Different content → different id. Current `data_version`: `4A.1`.

---

## Evidence / claims

- Synthetic data: `ENGINE_VALIDATION_ONLY`
- Real public history: **not** `EDGE_CONFIRMED`, **not** `PROFITABLE`
- This pipeline stores and versions data. It does not trade.

---

## Limitations

- Public REST only; no private / account endpoints
- Small default windows — not a bulk archive job
- Coinbase has no native 4h; use 1m + causal resample
- Resume skips stored timestamps; it does not invent missing bars
- Forming/incomplete last bars are skipped (`is_candle_closed`); they are not rewritten in already-stored 4B-REAL DB
- Optional live tests: `RUN_LIVE_DATA_TESTS=1 pytest -m live`
- Phase 5 / Decision Engine / paper live loop are out of scope
