-- Phase 4A Historical Dataset Pipeline — reference SQL (applied via create_all)
-- Additive only. Do NOT drop Phase 1/2/3 tables.

CREATE TABLE IF NOT EXISTS historical_datasets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id VARCHAR(64) NOT NULL,
  source VARCHAR(64) NOT NULL,
  source_symbol VARCHAR(32) NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  base_asset VARCHAR(16),
  quote_asset VARCHAR(16),
  canonical_asset VARCHAR(16),
  timeframe VARCHAR(16) NOT NULL,
  start_time DATETIME NOT NULL,
  end_time DATETIME NOT NULL,
  record_count INTEGER NOT NULL,
  data_version VARCHAR(32) NOT NULL,
  git_commit VARCHAR(64),
  checksum VARCHAR(64) NOT NULL,
  quality_status VARCHAR(32) NOT NULL,
  quality_json TEXT,
  split_json TEXT,
  test_locked BOOLEAN NOT NULL DEFAULT 1,
  evidence_json TEXT,
  created_at DATETIME,
  updated_at DATETIME,
  CONSTRAINT uq_historical_datasets_id UNIQUE (dataset_id)
);

CREATE INDEX IF NOT EXISTS ix_historical_datasets_src_sym_tf
  ON historical_datasets (source, symbol, timeframe);
CREATE INDEX IF NOT EXISTS ix_historical_datasets_start_end
  ON historical_datasets (start_time, end_time);

CREATE TABLE IF NOT EXISTS dataset_gaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id VARCHAR(64) NOT NULL,
  gap_start DATETIME NOT NULL,
  gap_end DATETIME NOT NULL,
  expected_records INTEGER,
  actual_records INTEGER,
  duration_seconds FLOAT,
  severity VARCHAR(16) NOT NULL,
  message TEXT,
  created_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_dataset_gaps_dataset_id ON dataset_gaps (dataset_id);
CREATE INDEX IF NOT EXISTS ix_dataset_gaps_range ON dataset_gaps (gap_start, gap_end);

CREATE TABLE IF NOT EXISTS dataset_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id VARCHAR(64) NOT NULL,
  dataset_id VARCHAR(64) NOT NULL,
  data_version VARCHAR(32) NOT NULL,
  source VARCHAR(64) NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  timeframe VARCHAR(16) NOT NULL,
  start_time DATETIME NOT NULL,
  end_time DATETIME NOT NULL,
  record_count INTEGER NOT NULL,
  checksum VARCHAR(64) NOT NULL,
  git_commit VARCHAR(64),
  payload_json TEXT,
  created_at DATETIME,
  CONSTRAINT uq_dataset_snapshots_id UNIQUE (snapshot_id)
);

CREATE INDEX IF NOT EXISTS ix_dataset_snapshots_dataset_id ON dataset_snapshots (dataset_id);

CREATE TABLE IF NOT EXISTS dataset_quality_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  critical_errors INTEGER NOT NULL,
  warnings INTEGER NOT NULL,
  gap_count INTEGER NOT NULL,
  summary_json TEXT,
  created_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_dataset_quality_dataset_id
  ON dataset_quality_summaries (dataset_id);

-- Helpful composite index for resume / range reads (event_time ≠ received_at)
CREATE INDEX IF NOT EXISTS ix_market_data_src_sym_tf_et
  ON market_data (source, symbol, timeframe, event_time);
