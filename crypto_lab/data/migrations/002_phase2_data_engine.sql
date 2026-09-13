-- Phase 2 Data Engine — reference SQL (applied programmatically in database.py)
-- Additive only. Do NOT drop Phase 1 tables.

-- 1) Additive columns on market_data
ALTER TABLE market_data ADD COLUMN event_time DATETIME;
ALTER TABLE market_data ADD COLUMN received_at DATETIME;
ALTER TABLE market_data ADD COLUMN source_symbol VARCHAR(32);
ALTER TABLE market_data ADD COLUMN base_asset VARCHAR(16);
ALTER TABLE market_data ADD COLUMN quote_asset VARCHAR(16);
ALTER TABLE market_data ADD COLUMN canonical_asset VARCHAR(16);

UPDATE market_data SET event_time = ts WHERE event_time IS NULL;
UPDATE market_data SET received_at = created_at WHERE received_at IS NULL;

-- 2) Unique key evolution (SQLite requires table rebuild) — see apply_phase2_migrations()
-- New unique: (source, symbol, ts, timeframe)  -- ts mirrors event_time for OHLCV
-- symbol is venue-native (BTCUSDT vs BTC-USD); source keeps venues separate

-- 3) New tables
CREATE TABLE IF NOT EXISTS market_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source VARCHAR(64) NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  event_time DATETIME NOT NULL,
  received_at DATETIME NOT NULL,
  price FLOAT NOT NULL,
  quantity FLOAT NOT NULL,
  trade_id VARCHAR(128),
  side VARCHAR(16),
  source_symbol VARCHAR(32),
  base_asset VARCHAR(16),
  quote_asset VARCHAR(16),
  canonical_asset VARCHAR(16),
  created_at DATETIME,
  CONSTRAINT uq_market_trades UNIQUE (source, symbol, trade_id, event_time)
);

CREATE TABLE IF NOT EXISTS market_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source VARCHAR(64) NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  event_time DATETIME NOT NULL,
  received_at DATETIME NOT NULL,
  bid FLOAT,
  ask FLOAT,
  spread FLOAT,
  last FLOAT,
  volume_24h FLOAT,
  source_symbol VARCHAR(32),
  base_asset VARCHAR(16),
  quote_asset VARCHAR(16),
  canonical_asset VARCHAR(16),
  created_at DATETIME,
  CONSTRAINT uq_market_quotes UNIQUE (source, symbol, event_time)
);

CREATE TABLE IF NOT EXISTS data_quality_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source VARCHAR(64),
  symbol VARCHAR(32),
  record_type VARCHAR(32) NOT NULL,
  rule VARCHAR(64) NOT NULL,
  severity VARCHAR(16) NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT,
  event_time DATETIME,
  received_at DATETIME,
  ts DATETIME
);

-- 4) Additive identity columns on trades/quotes (idempotent ALTERs for existing DBs)
ALTER TABLE market_trades ADD COLUMN base_asset VARCHAR(16);
ALTER TABLE market_trades ADD COLUMN quote_asset VARCHAR(16);
ALTER TABLE market_trades ADD COLUMN canonical_asset VARCHAR(16);
ALTER TABLE market_quotes ADD COLUMN base_asset VARCHAR(16);
ALTER TABLE market_quotes ADD COLUMN quote_asset VARCHAR(16);
ALTER TABLE market_quotes ADD COLUMN canonical_asset VARCHAR(16);
