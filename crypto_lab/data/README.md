# Data — Phase 2 Data Engine

Pipeline: **RECEIVE → VALIDATE → NORMALIZE → STORE → MONITOR**

- Providers: `BinanceSpotProvider` (primary), `CoinbaseExchangeProvider` (fallback)
- No API keys — public market data only
- No trading decisions, orders, or account endpoints

See `docs/DATA_ENGINE.md` for full documentation.

## Phase 4A — Historical datasets

Package: `crypto_lab/data/historical/` (downloader, identity, quality, causal resample, catalog, snapshots).

See `docs/HISTORICAL_DATA.md`. Public REST only; no trading claims.

