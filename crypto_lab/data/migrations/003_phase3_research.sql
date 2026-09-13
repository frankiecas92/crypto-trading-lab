-- Phase 3 Strategy Research Lab — reference SQL (applied via create_all)
-- Additive only. Do NOT drop Phase 1/2 tables.

CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id VARCHAR(64) NOT NULL,
  hypothesis TEXT NOT NULL,
  strategy_id VARCHAR(64) NOT NULL,
  strategy_version VARCHAR(32) NOT NULL,
  parameters_json TEXT,
  dataset_id VARCHAR(64),
  dataset_version VARCHAR(64),
  symbol VARCHAR(32),
  timeframe VARCHAR(16),
  dates_json TEXT,
  split_json TEXT,
  cost_json TEXT,
  results_json TEXT,
  benchmark_json TEXT,
  conclusion TEXT,
  git_commit VARCHAR(64),
  random_seed INTEGER,
  extra_json TEXT,
  created_at DATETIME,
  CONSTRAINT uq_experiments_eid UNIQUE (experiment_id)
);
