-- ============================================================
-- Distributor Filled-Order Matching — Schema (per-user isolation)
--
-- Same per-login isolation model as article_master_schema.sql.
-- No changes to article_master / category_master are needed —
-- this feature only reads from them (get_article_by_item_key).
-- ============================================================

CREATE TABLE IF NOT EXISTS filled_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    distributor_id INTEGER,
    distributor_name_raw TEXT,
    category TEXT NOT NULL,
    season TEXT NOT NULL,
    source_filename TEXT,
    quantity_column_used TEXT,
    quantity_unit_used TEXT,
    total_lines INTEGER,
    matched_lines INTEGER,
    unmatched_lines INTEGER,
    flagged_lines INTEGER,
    order_stream TEXT NOT NULL DEFAULT 'regular',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS filled_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filled_order_id INTEGER NOT NULL REFERENCES filled_orders(id) ON DELETE CASCADE,
    article_id INTEGER REFERENCES article_master(id),
    item_key TEXT NOT NULL,
    brand TEXT, size TEXT, product_type TEXT,
    raw_qty_value REAL NOT NULL,
    detected_unit TEXT NOT NULL,
    final_piece_qty REAL NOT NULL,
    bale_size_used REAL,
    is_clean_bale_multiple INTEGER NOT NULL DEFAULT 1,
    matched INTEGER NOT NULL DEFAULT 0,
    mrp REAL, ptr REAL, ex_mill_price REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Remembers, per distributor + category, which column was confirmed as the true
-- order-quantity column, so the confirm-dialog doesn't reappear every upload.
CREATE TABLE IF NOT EXISTS distributor_qty_column_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    distributor_id INTEGER,
    category TEXT NOT NULL,
    confirmed_column_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, distributor_id, category)
);

CREATE INDEX IF NOT EXISTS idx_filled_orders_user ON filled_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_filled_order_items_order ON filled_order_items(filled_order_id);

CREATE TABLE IF NOT EXISTS filled_order_so_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filled_order_id INTEGER NOT NULL REFERENCES filled_orders(id) ON DELETE CASCADE,
    order_lifecycle_tracking_id INTEGER NOT NULL,
    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(filled_order_id, order_lifecycle_tracking_id)
);

CREATE INDEX IF NOT EXISTS idx_filled_order_so_link_order ON filled_order_so_link(filled_order_id);
CREATE INDEX IF NOT EXISTS idx_filled_order_so_link_tracking ON filled_order_so_link(order_lifecycle_tracking_id);
