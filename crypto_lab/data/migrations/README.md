# Schema migrations

## Phase 1
Initial tables via SQLAlchemy `create_all`: `market_data`, `signals`, `trades`,
`positions`, `portfolio`, `system_events`, `strategy_versions`.

## Phase 2 (Data Engine) — additive
Applied automatically by `init_db()` → `apply_phase2_migrations()`:

1. **Add columns** to `market_data` (if missing):
   - `event_time` (exchange event UTC)
   - `received_at` (local receive UTC)
   - `source_symbol`, `base_asset`, `quote_asset`, `canonical_asset` (venue-native identity)
2. **Backfill** `event_time` ← `ts`, `received_at` ← `created_at` where null
3. **Rebuild unique** (SQLite table copy) from `uq_market_data(symbol,ts,timeframe)`
   to `uq_market_data_src_ts(source,symbol,ts,timeframe)` — preserves all rows;
   `symbol` is venue-native so Binance `BTCUSDT` and Coinbase `BTC-USD` do not collide incorrectly
4. **New tables** (via `create_all`):
   - `market_trades`
   - `market_quotes`
   - `data_quality_events`
5. **Additive identity columns** on `market_trades` / `market_quotes` when upgrading older Phase-2 DBs

See also `002_phase2_data_engine.sql` for a human-readable reference script.
Idempotent: safe to re-run.

**No destructive drops of Phase 1 business tables.**


## Phase 3 (Strategy Research Lab) — additive
Applied by `init_db()` → `apply_phase3_migrations()`:

1. **New table** `experiments` (registry: hypothesis, strategy version, params,
   dataset, splits, costs, results, benchmark, conclusion, git commit, seed)
2. Reuses existing `strategy_versions` (no silent overwrite)

See `003_phase3_research.sql`. Idempotent. No destructive drops.
