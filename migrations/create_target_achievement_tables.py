"""Migration script to create Target vs Achievement tables."""

CREATE TABLE IF NOT EXISTS target_achievement_years (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT DEFAULT 'bombay_dyeing',
  financial_year TEXT NOT NULL,
  target_amount REAL,
  achievement_amount REAL,
  achievement_percent REAL,
  target_source TEXT,
  achievement_source TEXT,
  remarks TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by TEXT,
  UNIQUE(workspace_id, financial_year)
);

CREATE INDEX IF NOT EXISTS idx_fy_year ON target_achievement_years(financial_year);

CREATE TABLE IF NOT EXISTS target_achievement_uploads (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT DEFAULT 'bombay_dyeing',
  financial_year_id INTEGER,
  file_name TEXT,
  file_type TEXT,
  uploaded_by TEXT,
  total_rows INTEGER,
  calculated_total REAL,
  upload_status TEXT,
  parsed_at TIMESTAMP,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(financial_year_id) REFERENCES target_achievement_years(id)
);

CREATE INDEX IF NOT EXISTS idx_fy_id ON target_achievement_uploads(financial_year_id);

CREATE TABLE IF NOT EXISTS target_achievement_breakup (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT DEFAULT 'bombay_dyeing',
  financial_year_id INTEGER,
  attribute_type TEXT,
  attribute_name TEXT,
  target_amount REAL,
  achievement_amount REAL,
  achievement_percent REAL,
  source TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(financial_year_id, attribute_type, attribute_name),
  FOREIGN KEY(financial_year_id) REFERENCES target_achievement_years(id)
);

CREATE INDEX IF NOT EXISTS idx_fy_breakup ON target_achievement_breakup(financial_year_id, attribute_type);
