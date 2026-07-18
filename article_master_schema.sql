-- ============================================================
-- Article Master — Schema (per-user isolation)
--
-- Primary scope: user_id — each login has its own catalog.
-- workspace_id is stored for reference and future optional
-- shared-workspace mode; it does NOT drive isolation today.
-- ============================================================

CREATE TABLE IF NOT EXISTS category_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    category_name TEXT NOT NULL,
    key_fields TEXT NOT NULL DEFAULT '["brand","size"]',
    is_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, category_name)
);

CREATE TABLE IF NOT EXISTS article_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    category TEXT NOT NULL,
    product_type TEXT,
    brand TEXT,
    size TEXT,
    mrp REAL,
    ptr REAL,
    ex_mill_price REAL,
    bale_pack_size TEXT,
    season_tag TEXT,
    item_key TEXT NOT NULL,
    extra_attributes TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    source_filename TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, item_key)
);

CREATE TABLE IF NOT EXISTS article_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES article_master(id) ON DELETE CASCADE,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_category_master_user ON category_master(user_id);
CREATE INDEX IF NOT EXISTS idx_article_master_user_category ON article_master(user_id, category);
CREATE INDEX IF NOT EXISTS idx_article_price_history_article ON article_price_history(article_id);

-- Maps distributor spelling variants to one canonical brand per user.
-- e.g. Blumen -> Bluemen so uploads never create duplicate rows.
CREATE TABLE IF NOT EXISTS brand_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    canonical_brand TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_brand_aliases_user ON brand_aliases(user_id);
