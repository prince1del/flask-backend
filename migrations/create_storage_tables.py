"""Migration script to create storage account and file index tables."""

CREATE TABLE IF NOT EXISTS storage_accounts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  workspace_id TEXT DEFAULT 'bombay_dyeing',
  provider_type TEXT,
  oauth_token TEXT,
  connected_at TIMESTAMP,
  last_sync TIMESTAMP,
  sync_status TEXT,
  total_storage_bytes BIGINT,
  used_storage_bytes BIGINT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  UNIQUE(user_id, workspace_id, provider_type)
);

CREATE TABLE IF NOT EXISTS file_index (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT DEFAULT 'bombay_dyeing',
  storage_account_id INTEGER,
  file_id TEXT,
  file_name TEXT,
  file_type TEXT,
  mime_type TEXT,
  folder_path TEXT,
  owner_id INTEGER,
  company TEXT,
  module TEXT,
  file_size_bytes BIGINT,
  created_at TIMESTAMP,
  modified_at TIMESTAMP,
  indexed_at TIMESTAMP,
  last_synced TIMESTAMP,
  sync_status TEXT,
  search_tags TEXT,
  version_number INTEGER,
  ocr_status TEXT,
  ai_status TEXT,
  processing_status TEXT,
  created_by INTEGER,
  updated_by INTEGER,
  FOREIGN KEY(storage_account_id) REFERENCES storage_accounts(id),
  UNIQUE(storage_account_id, file_id)
);

CREATE TABLE IF NOT EXISTS file_versions (
  id INTEGER PRIMARY KEY,
  file_index_id INTEGER,
  version_number INTEGER,
  version_file_id TEXT,
  created_at TIMESTAMP,
  modified_at TIMESTAMP,
  created_by INTEGER,
  FOREIGN KEY(file_index_id) REFERENCES file_index(id)
);

CREATE TABLE IF NOT EXISTS file_operations_log (
  id INTEGER PRIMARY KEY,
  file_index_id INTEGER,
  operation_type TEXT,
  user_id INTEGER,
  operation_status TEXT,
  error_message TEXT,
  created_at TIMESTAMP,
  FOREIGN KEY(file_index_id) REFERENCES file_index(id)
);

CREATE INDEX IF NOT EXISTS idx_file_index_workspace ON file_index(workspace_id);
CREATE INDEX IF NOT EXISTS idx_file_index_owner ON file_index(owner_id);
CREATE INDEX IF NOT EXISTS idx_file_index_module ON file_index(module);
CREATE INDEX IF NOT EXISTS idx_file_index_company ON file_index(company);
CREATE INDEX IF NOT EXISTS idx_file_index_created ON file_index(created_at);
CREATE INDEX IF NOT EXISTS idx_storage_accounts_user ON storage_accounts(user_id);
