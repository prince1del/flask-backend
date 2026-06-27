import csv
import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import openpyxl

import pandas as pd
from rapidfuzz import fuzz
from werkzeug.security import check_password_hash, generate_password_hash

from .firebase_sync import FirebaseSync
from .sync import OfflineSyncStore
from .article_master import ArticleMasterService


class CentralizedDB:

    # ============ DYNAMIC SCHEMA MANAGER ============

    def init_schema_manager(self):
        """Schema manager table banao agar nahi hai"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS custom_schema_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,  -- 'distributor', 'retailer', 'article'
                    field_name TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_type TEXT DEFAULT 'text',  -- text, number, date, select
                    field_order INTEGER DEFAULT 0,
                    is_required INTEGER DEFAULT 0,
                    is_visible INTEGER DEFAULT 1,
                    options TEXT DEFAULT NULL,  -- JSON for select type
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(entity_type, field_name)
                )
            """)
            conn.commit()

    def get_schema_fields(self, entity_type: str) -> list:
        """Entity ke fields lao order ke saath"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM custom_schema_fields
                WHERE entity_type = ? AND is_visible = 1
                ORDER BY field_order ASC
            """, (entity_type,)).fetchall()
            return [dict(r) for r in rows]

    def get_all_schema_fields(self, entity_type: str) -> list:
        """Saare fields (hidden bhi)"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM custom_schema_fields
                WHERE entity_type = ?
                ORDER BY field_order ASC
            """, (entity_type,)).fetchall()
            return [dict(r) for r in rows]

    def add_schema_field(self, entity_type: str, field_name: str, field_label: str,
                        field_type: str = 'text', field_order: int = 0,
                        is_required: int = 0, options: str = None) -> int:
        """Naya field add karo"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO custom_schema_fields
                (entity_type, field_name, field_label, field_type, field_order, is_required, options)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entity_type, field_name, field_label, field_type, field_order, is_required, options))
            conn.commit()
            return cursor.lastrowid

    def delete_schema_field(self, field_id: int) -> bool:
        """Field delete karo"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM custom_schema_fields WHERE id = ?", (field_id,))
            conn.commit()
            return True

    def toggle_schema_field_visibility(self, field_id: int, is_visible: int) -> bool:
        """Field show/hide karo"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE custom_schema_fields SET is_visible = ? WHERE id = ?",
                        (is_visible, field_id))
            conn.commit()
            return True

    def reorder_schema_fields(self, field_orders: list[dict]) -> bool:
        """Fields reorder karo — [{id: 1, order: 0}, {id: 2, order: 1}]"""
        with sqlite3.connect(self.db_path) as conn:
            for item in field_orders:
                conn.execute("UPDATE custom_schema_fields SET field_order = ? WHERE id = ?",
                            (item['order'], item['id']))
            conn.commit()
            return True

    def seed_default_schema(self):
        """Pehli baar default fields seed karo"""
        self.init_schema_manager()
        defaults = {
            'distributor': [
                ('distributor_code', 'Distributor Code', 'text', 0),
                ('firm_name', 'Firm Name', 'text', 1),
                ('firm_nick_name', 'Nick Name', 'text', 2),
                ('name', 'Contact Person', 'text', 3),
                ('phone_number', 'Mobile Number', 'text', 4),
                ('email', 'Email', 'text', 5),
                ('zone', 'State', 'text', 6),
                ('region', 'Area', 'text', 7),
                ('gst_no', 'GST Number', 'text', 8),
                ('payment_terms', 'Payment Terms', 'text', 9),
                ('credit_limit', 'Credit Limit', 'number', 10),
            ],
            'retailer': [
                ('retailer_code', 'Retailer Code', 'text', 0),
                ('name', 'Retailer Name', 'text', 1),
                ('owner_name', 'Owner Name', 'text', 2),
                ('distributor_id', 'Distributor', 'select', 3),
                ('location', 'Location', 'text', 4),
                ('phone_number', 'Phone Number', 'text', 5),
                ('email', 'Email', 'text', 6),
                ('address', 'Address', 'text', 7),
                ('gst_no', 'GST Number', 'text', 8),
            ],
            'article': [
                ('brand', 'Brand', 'text', 0),
                ('tc', 'TC', 'text', 1),
                ('size', 'Size', 'text', 2),
                ('bs_size', 'BS Size', 'text', 3),
                ('product', 'Product', 'text', 4),
                ('print_style', 'Print Style', 'text', 5),
                ('mrp', 'MRP (₹)', 'number', 6),
                ('selling_price', 'Selling Price (₹)', 'number', 7),
                ('ptr', 'PTR (₹)', 'number', 8),
                ('exmill_price', 'Ex-Mill (₹)', 'number', 9),
            ],
        }
        with sqlite3.connect(self.db_path) as conn:
            for entity, fields in defaults.items():
                for field_name, label, ftype, order in fields:
                    conn.execute("""
                        INSERT OR IGNORE INTO custom_schema_fields
                        (entity_type, field_name, field_label, field_type, field_order)
                        VALUES (?, ?, ?, ?, ?)
                    """, (entity, field_name, label, ftype, order))
            conn.commit()
    

    def __init__(self, db_path: str | None = None, sync_store: OfflineSyncStore | None = None):
        self.db_path = self._resolve_db_path(db_path)
        self.sync_store = sync_store or OfflineSyncStore()
        self.firebase_sync = FirebaseSync(sync_store=self.sync_store)
        self.article_service = ArticleMasterService(str(self.db_path))
        self._initialize()

    def _resolve_db_path(self, db_path: str | None = None) -> Path:
        if db_path:
            return Path(db_path).expanduser()

        for env_name in ("CLOUD_DATABASE_URL", "DATABASE_URL"):
            value = os.getenv(env_name)
            if not value:
                continue

            parsed = urlparse(value)
            if value.startswith("sqlite://"):
                path_value = value.removeprefix("sqlite:///")
                if path_value.startswith("/") and len(path_value) >= 3 and path_value[2] == ":":
                    path_value = path_value[1:]
                return Path(path_value).expanduser()

            if parsed.scheme in {"file", ""}:
                return Path(parsed.path or value).expanduser()

            return Path(value).expanduser()

        return Path("centralized_db.sqlite3").expanduser()

    def _log_audit_event(self, conn: sqlite3.Connection, action: str, table_name: str, record_id: int | str | None = None, details: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO database_audit_log (created_at, action, table_name, record_id, details) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                action,
                table_name,
                str(record_id) if record_id is not None else None,
                json.dumps(details or {}, default=str),
            ),
        )

    def backup_database(self, destination: str | Path) -> Path:
        target_path = Path(destination).expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            target_path.unlink()
        with sqlite3.connect(self.db_path) as source_conn:
            with sqlite3.connect(target_path) as backup_conn:
                source_conn.backup(backup_conn)
        with sqlite3.connect(target_path) as conn:
            self._log_audit_event(conn, "backup", "database", details={"source": str(self.db_path), "destination": str(target_path)})
            conn.commit()
        return target_path

    def restore_database(self, source: str | Path, overwrite: bool = False) -> Path:
        source_path = Path(source).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        target_path = self.db_path
        if target_path.exists() and not overwrite:
            raise FileExistsError(f"Target database already exists: {target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() and overwrite:
            try:
                if target_path.exists():
                    target_path.unlink()
            except PermissionError:
                import gc
                gc.collect()
                target_path.unlink(missing_ok=True)
        shutil.copy2(source_path, target_path)
        self._initialize()
        with sqlite3.connect(target_path) as conn:
            self._log_audit_event(conn, "restore", "database", details={"source": str(source_path), "destination": str(target_path)})
            conn.commit()
        return target_path

    def cleanup_temp_uploads(self, directory: str | Path, max_age_hours: int = 24) -> int:
        folder = Path(directory).expanduser()
        if not folder.exists():
            return 0
        removed = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.stat().st_mtime < cutoff.timestamp():
                file_path.unlink(missing_ok=True)
                removed += 1
        with sqlite3.connect(self.db_path) as conn:
            self._log_audit_event(conn, "cleanup", "temp_uploads", details={"directory": str(folder), "removed": removed, "max_age_hours": max_age_hours})
            conn.commit()
        return removed

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, created_at, action, table_name, record_id, details FROM database_audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "action": row[2],
                "table_name": row[3],
                "record_id": row[4],
                "details": json.loads(row[5]) if row[5] else None,
            }
            for row in rows
        ]

    def create_user(self, username: str, password: str) -> dict[str, Any]:
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("username and password are required")

        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                raise ValueError("user already exists")

            password_hash = generate_password_hash(password)
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, created_at),
            )
            return {"id": cursor.lastrowid, "username": username, "created_at": created_at}

    def authenticate_user(self, username: str, password: str) -> bool:
        username = (username or "").strip()
        if not username or not password:
            return False

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return False
            return check_password_hash(row[0], password)

    def ensure_default_admin_user(self) -> None:
        if os.getenv("AUTH_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            return

        with sqlite3.connect(self.db_path) as conn:
            has_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if has_user:
                return

            username = os.getenv("ADMIN_USERNAME", "admin")
            password = os.getenv("ADMIN_PASSWORD", "Admin123!")
            self.create_user(username, password)

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS database_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    record_id TEXT,
                    details TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT,
                    department TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    contact_person TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    gst_number TEXT,
                    credit_limit REAL,
                    balance REAL DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retailers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    contact_person TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    gst_number TEXT,
                    credit_limit REAL,
                    balance REAL DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS master_distributors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id TEXT UNIQUE,
                    firm_name TEXT,
                    firm_nick_name TEXT,
                    name TEXT NOT NULL,
                    phone_number TEXT,
                    location TEXT,
                    address TEXT,
                    pincode TEXT,
                    email TEXT,
                    gst_no TEXT,
                    zone TEXT,
                    region TEXT,
                    payment_terms TEXT,
                    birthday TEXT,
                    anniversary TEXT,
                    credit_limit REAL,
                    latitude REAL,
                    longitude REAL,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS master_retailers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    retailer_id TEXT UNIQUE,
                    name TEXT NOT NULL,
                    distributor_id INTEGER,
                    location TEXT,
                    latitude REAL,
                    longitude REAL,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targets_achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    zone TEXT,
                    target_amount REAL NOT NULL DEFAULT 0,
                    achievement_amount REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS primary_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id INTEGER NOT NULL,
                    invoice_no TEXT,
                    invoice_date TEXT,
                    quantity REAL NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secondary_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id INTEGER NOT NULL,
                    retailer_id INTEGER NOT NULL,
                    invoice_no TEXT,
                    sale_date TEXT,
                    quantity REAL NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_visit_logs (
                    visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id INTEGER NOT NULL,
                    visit_date TEXT NOT NULL,
                    visit_time TEXT,
                    synced_status TEXT NOT NULL DEFAULT 'pending',
                    responses TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_todo_list (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    staff_id INTEGER NOT NULL,
                    party_id INTEGER NOT NULL,
                    party_type TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    is_completed INTEGER NOT NULL DEFAULT 0,
                    created_date TEXT NOT NULL,
                    completed_timestamp TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gps_visit_verification_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_log_id INTEGER NOT NULL,
                    captured_latitude REAL,
                    captured_longitude REAL,
                    geofenced_status TEXT NOT NULL DEFAULT 'OUT_OF_BOUNDS',
                    device_timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_form_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    field_id TEXT UNIQUE,
                    field_label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    options TEXT,
                    is_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retailer_visit_logs (
                    visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    retailer_id INTEGER NOT NULL,
                    linked_distributor_id INTEGER,
                    visit_date TEXT NOT NULL,
                    visit_time TEXT,
                    synced_status TEXT NOT NULL DEFAULT 'pending',
                    responses TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retailer_form_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    field_id TEXT UNIQUE,
                    field_label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    options TEXT,
                    is_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_pjp_plans (
                    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_start_date TEXT NOT NULL,
                    day_of_week TEXT NOT NULL,
                    planned_distributor_ids TEXT,
                    planned_retailer_ids TEXT,
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dsr_reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_date TEXT NOT NULL,
                    summary TEXT,
                    distributor_visit_count INTEGER DEFAULT 0,
                    retailer_visit_count INTEGER DEFAULT 0,
                    orders_booked INTEGER DEFAULT 0,
                    payments_discussed INTEGER DEFAULT 0,
                    feedback_collected INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_type TEXT NOT NULL,
                    reference_id TEXT,
                    content TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_order_uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    verification_session_id TEXT NOT NULL,
                    distributor_name TEXT,
                    stage_key TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS offline_gps_cache (
                    cache_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_log_id INTEGER,
                    captured_latitude REAL,
                    captured_longitude REAL,
                    device_timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS article_master (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id TEXT UNIQUE,
                    category_name TEXT NOT NULL,
                    design_name TEXT NOT NULL,
                    color_way TEXT,
                    base_rate REAL DEFAULT 0,
                    gst_percentage REAL DEFAULT 0,
                    pcs_per_bale REAL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS business_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_key TEXT UNIQUE NOT NULL,
                    rule_value TEXT NOT NULL,
                    is_locked INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS data_entry_alert_logs (
                    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_type TEXT NOT NULL,
                    reference_no TEXT,
                    payload TEXT,
                    warnings TEXT,
                    severity TEXT NOT NULL DEFAULT 'warning',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_control (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id INTEGER UNIQUE NOT NULL,
                    max_credit_limit REAL,
                    credit_days_allowed INTEGER,
                    account_status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_purchase_behavior_logs (
                    behavior_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    distributor_id INTEGER NOT NULL,
                    article_id INTEGER,
                    category_name TEXT,
                    design_name TEXT,
                    color_way TEXT,
                    order_count INTEGER DEFAULT 0,
                    total_volume REAL DEFAULT 0,
                    avg_order_interval_days REAL DEFAULT 0,
                    last_order_date TEXT,
                    trend_window TEXT DEFAULT 'monthly',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_lifecycle_tracking (
                    tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_ref_no TEXT NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    order_received_date TEXT,
                    order_filled_date TEXT,
                    sales_order_generated_date TEXT,
                    payment_status TEXT,
                    commercial_invoice_date TEXT,
                    dispatch_date TEXT,
                    expected_delivery_date TEXT,
                    actual_delivery_date TEXT,
                    pod_number TEXT,
                    transit_status TEXT NOT NULL DEFAULT 'ORDERED',
                    receiving_status TEXT,
                    receiving_condition TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_receipt_logs (
                    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER NOT NULL,
                    article_id INTEGER NOT NULL,
                    invoiced_qty REAL NOT NULL DEFAULT 0,
                    physically_received_qty REAL NOT NULL DEFAULT 0,
                    damaged_qty REAL NOT NULL DEFAULT 0,
                    shortage_qty REAL NOT NULL DEFAULT 0,
                    status_flag TEXT NOT NULL DEFAULT 'MISMATCH_FOUND',
                    verification_result TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_lifecycle_status_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER NOT NULL,
                    transit_status TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    pod_number TEXT,
                    actual_delivery_date TEXT,
                    notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS global_search_index USING fts5(
                    content, category, source_id, source_table, tokenize='porter unicode61'
                )
                """
            )
            self._ensure_column_exists(conn, "master_distributors", "latitude", "REAL")
            self._ensure_column_exists(conn, "master_distributors", "longitude", "REAL")
            self._ensure_column_exists(conn, "master_distributors", "phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "email", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "address", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "zone", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "region", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "gst_no", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "location", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "firm_name", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "firm_nick_name", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "distributor_code", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "pincode", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "payment_terms", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "birthday", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "secondary_distributor_name", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "secondary_distributor_phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "secondary_distributor_birthday", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "secondary_distributor_anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "sales_executive_name", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "sales_executive_phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "sales_executive_email", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "sales_executive_birthday", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "sales_executive_anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "latitude", "REAL")
            self._ensure_column_exists(conn, "master_retailers", "longitude", "REAL")
            self._ensure_column_exists(conn, "master_retailers", "phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "email", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "address", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "gst_no", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "retailer_code", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "secondary_retailer_name", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "secondary_retailer_phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "secondary_retailer_birthday", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "secondary_retailer_anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "sales_executive_name", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "sales_executive_phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "sales_executive_email", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "sales_executive_birthday", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "sales_executive_anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "zone", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "region", "TEXT")
            self._ensure_column_exists(conn, "business_rules", "rule_key", "TEXT")
            self._ensure_column_exists(conn, "business_rules", "rule_value", "TEXT")
            self._ensure_column_exists(conn, "business_rules", "is_locked", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column_exists(conn, "business_rules", "updated_at", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_master_distributors_name ON master_distributors(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_master_distributors_gst_no ON master_distributors(gst_no)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_master_retailers_name ON master_retailers(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_master_retailers_distributor_id ON master_retailers(distributor_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_business_rules_rule_key ON business_rules(rule_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_targets_achievements_distributor ON targets_achievements(distributor_id, year, month)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_primary_sales_distributor ON primary_sales(distributor_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secondary_sales_distributor_retailer ON secondary_sales(distributor_id, retailer_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_distributor ON distributor_order_uploads(distributor_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_stage ON distributor_order_uploads(stage_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_uploaded_at ON distributor_order_uploads(uploaded_at)")
            self._seed_distributor_form_fields(conn)
            self._seed_retailer_form_fields(conn)
            self._seed_business_rules(conn)
            self._refresh_global_search_index(conn)
            conn.commit()

    def _seed_distributor_form_fields(self, conn: sqlite3.Connection) -> None:
        defaults = [
            ("current_stock_audit", "Current Stock Audit (Warehouse stock status)", "text", None, 1),
            ("payment_outstanding_credit_limit", "Payment Outstanding & Credit Limit Discussion", "text", None, 1),
            ("new_primary_order_booking", "New Primary Order Booking (Volume/Items)", "text", None, 1),
            ("distributor_market_feedback_grievances", "Distributor Market Feedback & Grievances", "text", None, 1),
            ("general_meeting_notes_next_actions", "General Meeting Notes & Next Action Steps", "text", None, 1),
        ]
        for field_id, field_label, field_type, options, is_required in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO distributor_form_fields (field_id, field_label, field_type, options, is_required, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (field_id, field_label, field_type, json.dumps(options) if options is not None else None, is_required, datetime.now(timezone.utc).isoformat()),
            )

    def _seed_retailer_form_fields(self, conn: sqlite3.Connection) -> None:
        defaults = [
            ("secondary_sales_volume", "Secondary Sales Volume (Counter sales check)", "text", None, 1),
            ("product_display_stock_availability", "Product Display & Stock Availability Status", "text", None, 1),
            ("distributor_service_rating", "Distributor Service Rating (Scale 1 to 5 stars)", "text", None, 1),
            ("competitor_counter_schemes_discounts", "Competitor Counter Schemes & Discounts Analysis", "text", None, 1),
            ("retailer_order_collection", "Retailer Order Collection (To forward to distributor)", "text", None, 1),
            ("counter_photo_reference", "Shop/Counter Photo Reference (Metadata link for Google Drive storage)", "text", None, 0),
            ("counter_discussion_notes", "Counter Discussion Notes", "text", None, 0),
        ]
        for field_id, field_label, field_type, options, is_required in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO retailer_form_fields (field_id, field_label, field_type, options, is_required, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (field_id, field_label, field_type, json.dumps(options) if options is not None else None, is_required, datetime.now(timezone.utc).isoformat()),
            )

    def _seed_business_rules(self, conn: sqlite3.Connection) -> None:
        defaults = [
            (
                "pricing_exmill_definition",
                "ExMill is distributor purchase price paid to company",
            ),
            (
                "pricing_ptr_definition",
                "PTR is retailer price at which distributor sells to retailer",
            ),
            (
                "size_prompt_precheck_rule",
                "Before asking Single/Double/King, first check if requested brand/item actually has those variants",
            ),
            (
                "size_prompt_single_variant_rule",
                "If only one size exists, directly quote that size and do not ask size selection",
            ),
            (
                "size_prompt_multi_variant_rule",
                "If multiple sizes exist, ask size selection before quoting amount",
            ),
            (
                "ambiguous_query_clarification_rule",
                "For ambiguous asks (e.g., bale size), ask clarification before final answer",
            ),
            (
                "margin_share_analysis_basis",
                "Default margin-share analysis basis is bedsheet quantity share unless another basis is explicitly asked",
            ),
        ]
        now = datetime.now(timezone.utc).isoformat()
        for rule_key, rule_value in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO business_rules (rule_key, rule_value, is_locked, updated_at)
                VALUES (?, ?, 1, ?)
                """,
                (rule_key, rule_value, now),
            )

    def _ensure_column_exists(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(row[1] == column_name for row in rows):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _refresh_global_search_index(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM global_search_index")

        master_rows = conn.execute(
            "SELECT id, name, gst_no, zone, region, NULL AS location FROM master_distributors UNION ALL SELECT id, name, NULL AS gst_no, NULL AS zone, NULL AS region, location FROM master_retailers"
        ).fetchall()
        for row in master_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table) VALUES (?, ?, ?, ?)",
                (content, "masters", row[0], "masters"),
            )

        verification_rows = conn.execute("SELECT id, report_type, reference_id, content FROM verification_outputs").fetchall()
        for row in verification_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table) VALUES (?, ?, ?, ?)",
                (content, "verifications", row[0], "verification_outputs"),
            )

        visit_rows = conn.execute(
            "SELECT visit_id, 'distributor', distributor_id, responses FROM distributor_visit_logs UNION ALL SELECT visit_id, 'retailer', retailer_id, responses FROM retailer_visit_logs"
        ).fetchall()
        for row in visit_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table) VALUES (?, ?, ?, ?)",
                (content, "visit_logs", row[0], "visit_logs"),
            )

        analytics_rows = conn.execute(
            "SELECT id, year, month, zone, target_amount, achievement_amount FROM targets_achievements"
        ).fetchall()
        for row in analytics_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table) VALUES (?, ?, ?, ?)",
                (content, "analytics", row[0], "targets_achievements"),
            )

    def _normalize_text(self, value: Any) -> str:
        return " ".join(str(value or "").strip().split()).lower()

    def _canonicalize_known_master_name(self, value: Any) -> str:
        raw_value = str(value or "").strip()
        if not raw_value:
            return ""

        normalized_value = self._normalize_text(raw_value)
        alias_map = {
            "bnd": "Bernina International P Ltd",
            "choice corner": "Choice Corner Bombay Dyeing",
            "sup": "Sain International",
            "savitri steel": "Savitri Steel Cement Traders",
            "balaji home decor": "Balaji Homedecor",
            "geb": "Goyal Enterprises",
            "kag": "Kalra Agencies",
            "ptj": "Parnami Textiles",
            "shri ram": "Shri Ram & Co",
            "dca": "DCA Marketing",
        }
        return alias_map.get(normalized_value, raw_value)

    def _find_similar_master_entry(
        self,
        conn: sqlite3.Connection | None,
        table: str,
        name_column: str,
        name: str,
        threshold: int = 80,
        extra_filters: dict[str, Any] | None = None,
    ) -> int | None:
        normalized_name = self._normalize_text(name)
        if not normalized_name:
            return None

        connection = conn or sqlite3.connect(self.db_path)
        should_close = conn is None
        try:
            query = f"SELECT id, {name_column} FROM {table}"
            params: list[Any] = []
            clauses: list[str] = []
            if extra_filters:
                for key, value in extra_filters.items():
                    clauses.append(f"{key} = ?")
                    params.append(value)

            if clauses:
                query += " WHERE " + " AND ".join(clauses)

            rows = connection.execute(query, params).fetchall()
            for row in rows:
                candidate_name = self._normalize_text(row[1])
                if candidate_name == normalized_name:
                    return int(row[0])

            return None
        finally:
            if should_close:
                connection.close()

    def validate_data_entry(
        self,
        document_type: str,
        payload: dict[str, Any],
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        normalized_type = str(document_type or "").strip().lower()
        existing_entries = existing_entries or []

        reference_no = payload.get("order_ref_no") or payload.get("reference_no") or payload.get("invoice_no") or payload.get("document_no")
        if reference_no and any(str(entry.get("order_ref_no") or entry.get("reference_no") or entry.get("invoice_no") or entry.get("document_no")) == str(reference_no) for entry in existing_entries):
            warnings.append("Duplicate entry detected for reference number")

        if normalized_type in {"order sheet", "sales order", "sales_order", "so", "commercial invoice", "commercial_invoice", "invoice"}:
            rate = payload.get("rate") or payload.get("unit_rate") or payload.get("unit_price")
            quantity = payload.get("quantity") or payload.get("ordered_qty") or payload.get("filled_qty")
            amount = payload.get("amount") or payload.get("invoice_amount") or payload.get("net_amount") or payload.get("gross_amount")
            if rate is not None and quantity is not None and amount is not None:
                try:
                    expected_amount = float(quantity) * float(rate)
                    if abs(float(amount) - expected_amount) > 0.01:
                        warnings.append("Rate mismatch detected against quantity and amount")
                except (TypeError, ValueError):
                    warnings.append("Unable to validate amount against quantity and rate")

            ordered_qty = payload.get("ordered_qty")
            filled_qty = payload.get("filled_qty")
            if ordered_qty is None:
                ordered_qty = payload.get("quantity")
            if filled_qty is None:
                filled_qty = payload.get("filled_quantity") or payload.get("received_qty")
            if ordered_qty is not None and filled_qty is not None:
                try:
                    if float(filled_qty) != float(ordered_qty):
                        warnings.append("Quantity discrepancy detected between ordered and filled quantities")
                except (TypeError, ValueError):
                    warnings.append("Unable to validate quantity discrepancy")

        return {
            "document_type": document_type,
            "valid": not warnings,
            "warnings": warnings,
        }

    def list_data_entry_alerts(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT alert_id, document_type, reference_no, payload, warnings, severity, created_at FROM data_entry_alert_logs ORDER BY alert_id DESC"
            ).fetchall()
        return [
            {
                "alert_id": row[0],
                "document_type": row[1],
                "reference_no": row[2],
                "payload": json.loads(row[3]) if row[3] else {},
                "warnings": json.loads(row[4]) if row[4] else [],
                "severity": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    def list_credit_control(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "distributor_id": row[1],
                "max_credit_limit": row[2],
                "credit_days_allowed": row[3],
                "account_status": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def create_data_entry_alert(
        self,
        document_type: str,
        reference_no: str | None,
        payload: dict[str, Any],
        warnings: list[str],
        severity: str = "warning",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO data_entry_alert_logs (document_type, reference_no, payload, warnings, severity, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_type,
                    reference_no,
                    json.dumps(payload or {}),
                    json.dumps(warnings),
                    severity,
                    created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def process_data_entry(
        self,
        document_type: str,
        payload: dict[str, Any],
        commit_callback: Any | None = None,
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        validation = self.validate_data_entry(document_type, payload, existing_entries=existing_entries)
        if not validation["valid"]:
            alert_id = self.create_data_entry_alert(document_type, payload.get("order_ref_no") or payload.get("reference_no") or payload.get("invoice_no"), payload, validation["warnings"])
            return {"accepted": False, "alert_id": alert_id, "warnings": validation["warnings"]}

        if commit_callback is not None:
            commit_callback(payload)
        return {"accepted": True, "alert_id": None, "warnings": []}

    def record_sales_order_entry(
        self,
        payload: dict[str, Any],
        commit_callback: Any | None = None,
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.process_data_entry("Sales Order", payload, commit_callback=commit_callback, existing_entries=existing_entries)

    def record_commercial_invoice_entry(
        self,
        payload: dict[str, Any],
        commit_callback: Any | None = None,
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.process_data_entry("Commercial Invoice", payload, commit_callback=commit_callback, existing_entries=existing_entries)

    def upsert_credit_control(
        self,
        distributor_id: int,
        max_credit_limit: float | None = None,
        credit_days_allowed: int | None = None,
        account_status: str = "ACTIVE",
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM credit_control WHERE distributor_id = ?", (distributor_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE credit_control SET max_credit_limit = ?, credit_days_allowed = ?, account_status = ?, created_at = ? WHERE distributor_id = ?",
                    (max_credit_limit, credit_days_allowed, account_status, created_at, distributor_id),
                )
                conn.commit()
                row = conn.execute("SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control WHERE distributor_id = ?", (distributor_id,)).fetchone()
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO credit_control (distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at),
                )
                conn.commit()
                row = conn.execute("SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return {
            "id": row[0],
            "distributor_id": row[1],
            "max_credit_limit": row[2],
            "credit_days_allowed": row[3],
            "account_status": row[4],
            "created_at": row[5],
        }

    def validate_credit_policy(
        self,
        distributor_id: int,
        max_credit_limit: float | None = None,
        credit_days_allowed: int | None = None,
        account_status: str | None = None,
        bypass: bool = True,
    ) -> dict[str, Any]:
        policy = self.upsert_credit_control(distributor_id, max_credit_limit=max_credit_limit, credit_days_allowed=credit_days_allowed, account_status=account_status or "ACTIVE")
        if bypass:
            return {"valid": True, "bypassed": True, "policy": policy}
        return {"valid": True, "bypassed": False, "policy": policy}

    def build_distributor_purchase_behavior_logs(self, distributor_id: int) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM distributor_purchase_behavior_logs WHERE distributor_id = ?", (distributor_id,))
            lifecycle_rows = conn.execute(
                "SELECT tracking_id, order_received_date, order_filled_date, dispatch_date FROM order_lifecycle_tracking WHERE distributor_id = ? ORDER BY order_received_date, order_filled_date, dispatch_date",
                (distributor_id,),
            ).fetchall()

            receipt_rows = conn.execute(
                "SELECT drl.tracking_id, drl.article_id, drl.physically_received_qty, drl.invoiced_qty, am.category_name, am.design_name, am.color_way FROM delivery_receipt_logs drl LEFT JOIN article_master am ON am.id = drl.article_id WHERE drl.tracking_id IN (SELECT tracking_id FROM order_lifecycle_tracking WHERE distributor_id = ?)",
                (distributor_id,),
            ).fetchall()

            order_dates = []
            for row in lifecycle_rows:
                for candidate in [row[1], row[2], row[3]]:
                    if candidate:
                        try:
                            datetime.strptime(candidate, "%Y-%m-%d")
                            order_dates.append(candidate)
                            break
                        except ValueError:
                            continue

            order_dates = sorted(order_dates)
            avg_interval = 0.0
            if len(order_dates) >= 2:
                intervals = []
                for earlier, later in zip(order_dates, order_dates[1:]):
                    try:
                        intervals.append((datetime.strptime(later, "%Y-%m-%d") - datetime.strptime(earlier, "%Y-%m-%d")).days)
                    except ValueError:
                        continue
                avg_interval = sum(intervals) / len(intervals) if intervals else 0.0

            grouped: dict[tuple[int | None, str | None, str | None, str | None], dict[str, Any]] = {}
            for receipt in receipt_rows:
                key = (receipt[1], receipt[4], receipt[5], receipt[6])
                if key not in grouped:
                    grouped[key] = {
                        "article_id": receipt[1],
                        "category_name": receipt[4],
                        "design_name": receipt[5],
                        "color_way": receipt[6],
                        "order_count": 0,
                        "total_volume": 0.0,
                    }
                grouped[key]["order_count"] += 1
                grouped[key]["total_volume"] += float(receipt[2] or 0.0)

            created_at = datetime.now(timezone.utc).isoformat()
            for item in grouped.values():
                conn.execute(
                    """
                    INSERT INTO distributor_purchase_behavior_logs (
                        distributor_id, article_id, category_name, design_name, color_way, order_count, total_volume,
                        avg_order_interval_days, last_order_date, trend_window, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        distributor_id,
                        item["article_id"],
                        item["category_name"],
                        item["design_name"],
                        item["color_way"],
                        item["order_count"],
                        item["total_volume"],
                        avg_interval,
                        order_dates[-1] if order_dates else None,
                        "monthly",
                        created_at,
                    ),
                )
            conn.commit()

            rows = conn.execute(
                "SELECT behavior_id, distributor_id, article_id, category_name, design_name, color_way, order_count, total_volume, avg_order_interval_days, last_order_date, trend_window, created_at FROM distributor_purchase_behavior_logs WHERE distributor_id = ? ORDER BY total_volume DESC",
                (distributor_id,),
            ).fetchall()

        return [
            {
                "behavior_id": row[0],
                "distributor_id": row[1],
                "article_id": row[2],
                "category_name": row[3],
                "design_name": row[4],
                "color_way": row[5],
                "order_count": row[6],
                "total_volume": row[7],
                "avg_order_interval_days": row[8],
                "last_order_date": row[9],
                "trend_window": row[10],
                "created_at": row[11],
            }
            for row in rows
        ]

    def create_order_lifecycle_tracking(
        self,
        order_ref_no: str,
        distributor_id: int,
        order_received_date: str | None = None,
        order_filled_date: str | None = None,
        sales_order_generated_date: str | None = None,
        payment_status: str | None = None,
        commercial_invoice_date: str | None = None,
        dispatch_date: str | None = None,
        expected_delivery_date: str | None = None,
        transit_status: str = "ORDERED",
        receiving_status: str | None = None,
        receiving_condition: str | None = None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO order_lifecycle_tracking (
                    order_ref_no, distributor_id, order_received_date, order_filled_date, sales_order_generated_date,
                    payment_status, commercial_invoice_date, dispatch_date, expected_delivery_date, transit_status,
                    receiving_status, receiving_condition, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_ref_no,
                    distributor_id,
                    order_received_date,
                    order_filled_date,
                    sales_order_generated_date,
                    payment_status,
                    commercial_invoice_date,
                    dispatch_date,
                    expected_delivery_date,
                    transit_status,
                    receiving_status,
                    receiving_condition,
                    created_at,
                ),
            )
            tracking_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, notes) VALUES (?, ?, ?, ?)",
                (tracking_id, transit_status, created_at, "Initial status"),
            )
            conn.commit()
        self.sync_store.enqueue("stock-lifecycle-create", {"tracking_id": tracking_id, "order_ref_no": order_ref_no, "distributor_id": distributor_id, "transit_status": transit_status})
        self.firebase_sync.push_record({"entity": "order_lifecycle", "tracking_id": tracking_id, "order_ref_no": order_ref_no, "transit_status": transit_status})
        return tracking_id

    def get_order_lifecycle_tracking(self, tracking_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT tracking_id, order_ref_no, distributor_id, order_received_date, order_filled_date, sales_order_generated_date, payment_status, commercial_invoice_date, dispatch_date, expected_delivery_date, actual_delivery_date, pod_number, transit_status, receiving_status, receiving_condition, created_at FROM order_lifecycle_tracking WHERE tracking_id = ?",
                (tracking_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "tracking_id": row[0],
            "order_ref_no": row[1],
            "distributor_id": row[2],
            "order_received_date": row[3],
            "order_filled_date": row[4],
            "sales_order_generated_date": row[5],
            "payment_status": row[6],
            "commercial_invoice_date": row[7],
            "dispatch_date": row[8],
            "expected_delivery_date": row[9],
            "actual_delivery_date": row[10],
            "pod_number": row[11],
            "transit_status": row[12],
            "receiving_status": row[13],
            "receiving_condition": row[14],
            "created_at": row[15],
        }

    def list_status_history(self, tracking_id: int) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT history_id, tracking_id, transit_status, changed_at, pod_number, actual_delivery_date, notes FROM order_lifecycle_status_history WHERE tracking_id = ? ORDER BY history_id",
                (tracking_id,),
            ).fetchall()
        return [
            {
                "history_id": row[0],
                "tracking_id": row[1],
                "transit_status": row[2],
                "changed_at": row[3],
                "pod_number": row[4],
                "actual_delivery_date": row[5],
                "notes": row[6],
            }
            for row in rows
        ]

    def update_order_lifecycle_stage(
        self,
        tracking_id: int,
        order_filled_date: str | None = None,
        sales_order_generated_date: str | None = None,
        payment_status: str | None = None,
        commercial_invoice_date: str | None = None,
        dispatch_date: str | None = None,
        expected_delivery_date: str | None = None,
        actual_delivery_date: str | None = None,
        pod_number: str | None = None,
        transit_status: str | None = None,
        receiving_status: str | None = None,
        receiving_condition: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            current = self.get_order_lifecycle_tracking(tracking_id)
            if current is None:
                raise ValueError("Tracking record not found")

            status_value = transit_status or current.get("transit_status") or "ORDERED"
            if status_value == "DELIVERED" and (not pod_number or not actual_delivery_date):
                raise ValueError("POD number and actual delivery date are required for delivered shipments")

            conn.execute(
                """
                UPDATE order_lifecycle_tracking
                SET order_filled_date = ?, sales_order_generated_date = ?, payment_status = ?, commercial_invoice_date = ?,
                    dispatch_date = ?, expected_delivery_date = ?, actual_delivery_date = ?, pod_number = ?, transit_status = ?,
                    receiving_status = ?, receiving_condition = ?
                WHERE tracking_id = ?
                """,
                (
                    order_filled_date if order_filled_date is not None else current.get("order_filled_date"),
                    sales_order_generated_date if sales_order_generated_date is not None else current.get("sales_order_generated_date"),
                    payment_status if payment_status is not None else current.get("payment_status"),
                    commercial_invoice_date if commercial_invoice_date is not None else current.get("commercial_invoice_date"),
                    dispatch_date if dispatch_date is not None else current.get("dispatch_date"),
                    expected_delivery_date if expected_delivery_date is not None else current.get("expected_delivery_date"),
                    actual_delivery_date if actual_delivery_date is not None else current.get("actual_delivery_date"),
                    pod_number if pod_number is not None else current.get("pod_number"),
                    status_value,
                    receiving_status if receiving_status is not None else current.get("receiving_status"),
                    receiving_condition if receiving_condition is not None else current.get("receiving_condition"),
                    tracking_id,
                ),
            )
            conn.execute(
                "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, pod_number, actual_delivery_date, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (tracking_id, status_value, datetime.now(timezone.utc).isoformat(), pod_number if pod_number is not None else current.get("pod_number"), actual_delivery_date if actual_delivery_date is not None else current.get("actual_delivery_date"), notes or f"Stage update for {status_value}"),
            )
            conn.commit()

        self.sync_store.enqueue("stock-lifecycle-update", {"tracking_id": tracking_id, "transit_status": status_value, "pod_number": pod_number, "actual_delivery_date": actual_delivery_date})
        self.firebase_sync.push_record({"entity": "order_lifecycle", "tracking_id": tracking_id, "transit_status": status_value})
        return self.get_order_lifecycle_tracking(tracking_id)

    def update_order_lifecycle_status(
        self,
        tracking_id: int,
        transit_status: str,
        pod_number: str | None = None,
        actual_delivery_date: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"ORDERED", "FILLED", "DISPATCHED", "DELIVERED", "CANCELLED"}
        if transit_status not in allowed_statuses:
            raise ValueError(f"Unsupported status: {transit_status}")

        if transit_status == "DELIVERED":
            if not pod_number or not actual_delivery_date:
                raise ValueError("POD number and actual delivery date are required for delivered shipments")

        with sqlite3.connect(self.db_path) as conn:
            current = self.get_order_lifecycle_tracking(tracking_id)
            if current is None:
                raise ValueError("Tracking record not found")
            conn.execute(
                "UPDATE order_lifecycle_tracking SET actual_delivery_date = ?, pod_number = ?, transit_status = ? WHERE tracking_id = ?",
                (actual_delivery_date, pod_number, transit_status, tracking_id),
            )
            changed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, pod_number, actual_delivery_date, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (tracking_id, transit_status, changed_at, pod_number, actual_delivery_date, notes or f"Status changed to {transit_status}"),
            )
            conn.commit()

        self.sync_store.enqueue("stock-lifecycle-update", {"tracking_id": tracking_id, "transit_status": transit_status, "pod_number": pod_number, "actual_delivery_date": actual_delivery_date})
        self.firebase_sync.push_record({"entity": "order_lifecycle", "tracking_id": tracking_id, "transit_status": transit_status})
        return self.get_order_lifecycle_tracking(tracking_id)

    def record_delivery_receipt(
        self,
        tracking_id: int,
        article_id: int,
        invoiced_qty: float,
        physically_received_qty: float,
        damaged_qty: float = 0.0,
        verification_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shortage_qty = max(0.0, float(invoiced_qty) - float(physically_received_qty))
        if physically_received_qty == invoiced_qty:
            status_flag = "FULLY_RECEIVED"
        elif physically_received_qty == 0:
            status_flag = "NO_STOCK_RECEIVED"
        elif shortage_qty > 0:
            status_flag = "MISMATCH_FOUND"
        else:
            status_flag = "PARTIALLY_RECEIVED"

        if verification_context and verification_context.get("invoiced_qty") is not None:
            expected_invoiced = float(verification_context["invoiced_qty"])
            if float(invoiced_qty) != expected_invoiced:
                status_flag = "MISMATCH_FOUND"

        with sqlite3.connect(self.db_path) as conn:
            existing_tracking = self.get_order_lifecycle_tracking(tracking_id)
            if existing_tracking and existing_tracking.get("transit_status") not in {"DELIVERED", "CANCELLED"}:
                conn.execute(
                    "UPDATE order_lifecycle_tracking SET transit_status = 'DISPATCHED' WHERE tracking_id = ?",
                    (tracking_id,),
                )
                conn.execute(
                    "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, notes) VALUES (?, ?, ?, ?)",
                    (tracking_id, "DISPATCHED", datetime.now(timezone.utc).isoformat(), "Receipt logged at distributor"),
                )

            cursor = conn.execute(
                """
                INSERT INTO delivery_receipt_logs (
                    tracking_id, article_id, invoiced_qty, physically_received_qty, damaged_qty, shortage_qty, status_flag, verification_result, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tracking_id,
                    article_id,
                    invoiced_qty,
                    physically_received_qty,
                    damaged_qty,
                    shortage_qty,
                    status_flag,
                    json.dumps(verification_context or {}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            receipt_id = int(cursor.lastrowid)

        self.sync_store.enqueue("stock-receipt-create", {"receipt_id": receipt_id, "tracking_id": tracking_id, "status_flag": status_flag})
        self.firebase_sync.push_record({"entity": "delivery_receipt", "receipt_id": receipt_id, "tracking_id": tracking_id, "status_flag": status_flag})
        return {
            "receipt_id": receipt_id,
            "tracking_id": tracking_id,
            "article_id": article_id,
            "invoiced_qty": invoiced_qty,
            "physically_received_qty": physically_received_qty,
            "damaged_qty": damaged_qty,
            "shortage_qty": shortage_qty,
            "status_flag": status_flag,
        }

    def list_delivery_receipts(self, tracking_id: int | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if tracking_id is None:
                rows = conn.execute(
                    "SELECT receipt_id, tracking_id, article_id, invoiced_qty, physically_received_qty, damaged_qty, shortage_qty, status_flag, verification_result, created_at FROM delivery_receipt_logs ORDER BY receipt_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT receipt_id, tracking_id, article_id, invoiced_qty, physically_received_qty, damaged_qty, shortage_qty, status_flag, verification_result, created_at FROM delivery_receipt_logs WHERE tracking_id = ? ORDER BY receipt_id",
                    (tracking_id,),
                ).fetchall()
        return [
            {
                "receipt_id": row[0],
                "tracking_id": row[1],
                "article_id": row[2],
                "invoiced_qty": row[3],
                "physically_received_qty": row[4],
                "damaged_qty": row[5],
                "shortage_qty": row[6],
                "status_flag": row[7],
                "verification_result": row[8],
                "created_at": row[9],
            }
            for row in rows
        ]

    def add_record(self, name: str, email: str | None = None, department: str | None = None) -> int:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("name is required")
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO records (name, email, department, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (cleaned_name, email, department, created_at),
            )
            self._log_audit_event(conn, "create", "records", record_id=int(cursor.lastrowid), details={"name": cleaned_name, "email": email, "department": department})
            conn.commit()
            record_id = int(cursor.lastrowid)

        self.sync_store.enqueue(
            "add",
            {
                "name": name,
                "email": email,
                "department": department,
                "created_at": created_at,
            },
        )
        self.firebase_sync.push_record({
            "name": name,
            "email": email,
            "department": department,
            "created_at": created_at,
        })
        return record_id

    def list_records(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, email, department, created_at FROM records ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "department": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, email, department, created_at FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "department": row[3],
            "created_at": row[4],
        }

    def update_record(self, record_id: int, **fields: Any) -> bool:
        if not fields:
            return False
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), record_id]
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"UPDATE records SET {assignments} WHERE id = ?", values)
            self._log_audit_event(conn, "update", "records", record_id=record_id, details=fields)
            conn.commit()

        for key, value in fields.items():
            self.sync_store.enqueue(
                "update",
                {"record_id": record_id, "field": key, "value": value},
            )
            self.firebase_sync.push_record({"record_id": record_id, "field": key, "value": value})
        return cursor.rowcount > 0

    def delete_record(self, record_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            self._log_audit_event(conn, "delete", "records", record_id=record_id)
            conn.commit()

        self.sync_store.enqueue("delete", {"record_id": record_id})
        self.firebase_sync.push_record({"record_id": record_id, "action": "delete"})
        return cursor.rowcount > 0

    def clear_distributor_contacts(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM master_distributors")
            deleted_distributors = cursor.rowcount
            conn.execute("DELETE FROM targets_achievements")
            conn.execute("DELETE FROM primary_sales")
            conn.execute("DELETE FROM secondary_sales")
            conn.commit()

        self.sync_store.enqueue("clear_distributor_contacts", {"tables": ["master_distributors"]})
        self.firebase_sync.push_record({"action": "clear_distributor_contacts"})
        return int(deleted_distributors)

    def clear_retailer_contacts(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM master_retailers")
            deleted_retailers = cursor.rowcount
            conn.commit()

        self.sync_store.enqueue("clear_retailer_contacts", {"tables": ["master_retailers"]})
        self.firebase_sync.push_record({"action": "clear_retailer_contacts"})
        return int(deleted_retailers)

    def clear_master_contacts(self) -> int:
        deleted_distributors = self.clear_distributor_contacts()
        self.clear_retailer_contacts()
        return int(deleted_distributors)

    def add_distributor(
        self,
        name: str,
        contact_person: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        city: str | None = None,
        state: str | None = None,
        gst_number: str | None = None,
        credit_limit: float | None = None,
        balance: float = 0.0,
        status: str = "active",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO distributors (
                    name, contact_person, phone, email, address, city, state,
                    gst_number, credit_limit, balance, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, contact_person, phone, email, address, city, state, gst_number, credit_limit, balance, status, created_at),
            )
            conn.commit()
            record_id = int(cursor.lastrowid)
        return record_id

    def list_distributors(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, contact_person, phone, email, address, city, state, gst_number, credit_limit, balance, status, created_at FROM distributors ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "contact_person": row[2],
                "phone": row[3],
                "email": row[4],
                "address": row[5],
                "city": row[6],
                "state": row[7],
                "gst_number": row[8],
                "credit_limit": row[9],
                "balance": row[10],
                "status": row[11],
                "created_at": row[12],
            }
            for row in rows
        ]

    def add_retailer(
        self,
        name: str,
        contact_person: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        city: str | None = None,
        state: str | None = None,
        gst_number: str | None = None,
        credit_limit: float | None = None,
        balance: float = 0.0,
        status: str = "active",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO retailers (
                    name, contact_person, phone, email, address, city, state,
                    gst_number, credit_limit, balance, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, contact_person, phone, email, address, city, state, gst_number, credit_limit, balance, status, created_at),
            )
            conn.commit()
            record_id = int(cursor.lastrowid)
        return record_id

    def list_retailers(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, contact_person, phone, email, address, city, state, gst_number, credit_limit, balance, status, created_at FROM retailers ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "contact_person": row[2],
                "phone": row[3],
                "email": row[4],
                "address": row[5],
                "city": row[6],
                "state": row[7],
                "gst_number": row[8],
                "credit_limit": row[9],
                "balance": row[10],
                "status": row[11],
                "created_at": row[12],
            }
            for row in rows
        ]

    def count_records(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM records").fetchone()
        return int(row[0]) if row else 0

    def list_distributor_form_fields(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT field_id, field_label, field_type, options, is_required, status FROM distributor_form_fields WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [
            {
                "field_id": row[0],
                "field_label": row[1],
                "field_type": row[2],
                "options": json.loads(row[3]) if row[3] else [],
                "is_required": bool(row[4]),
                "status": row[5],
            }
            for row in rows
        ]

    def list_retailer_form_fields(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT field_id, field_label, field_type, options, is_required, status FROM retailer_form_fields WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [
            {
                "field_id": row[0],
                "field_label": row[1],
                "field_type": row[2],
                "options": json.loads(row[3]) if row[3] else [],
                "is_required": bool(row[4]),
                "status": row[5],
            }
            for row in rows
        ]

    def add_distributor_visit_log(self, distributor_id: int, visit_date: str, visit_time: str | None = None, responses: dict[str, Any] | None = None, synced_status: str = "pending") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(responses or {})
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO distributor_visit_logs (distributor_id, visit_date, visit_time, synced_status, responses, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (distributor_id, visit_date, visit_time, synced_status, payload, created_at),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def add_retailer_visit_log(self, retailer_id: int, linked_distributor_id: int | None = None, visit_date: str = "", visit_time: str | None = None, responses: dict[str, Any] | None = None, synced_status: str = "pending") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(responses or {})
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO retailer_visit_logs (retailer_id, linked_distributor_id, visit_date, visit_time, synced_status, responses, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (retailer_id, linked_distributor_id, visit_date, visit_time, synced_status, payload, created_at),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def create_workflow_todo_task(
        self,
        staff_id: int,
        party_id: int,
        party_type: str,
        task_description: str,
        created_date: str | None = None,
        is_completed: bool = False,
        completed_timestamp: str | None = None,
    ) -> int:
        created_date = created_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO workflow_todo_list (staff_id, party_id, party_type, task_description, is_completed, created_date, completed_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (staff_id, party_id, party_type, task_description, int(is_completed), created_date, completed_timestamp, created_at),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def list_workflow_todos_for_party(self, party_id: int, party_type: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT task_id, staff_id, party_id, party_type, task_description, is_completed, created_date, completed_timestamp, created_at FROM workflow_todo_list WHERE party_id = ? AND party_type = ? ORDER BY created_date, task_id",
                (party_id, party_type),
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "staff_id": row[1],
                "party_id": row[2],
                "party_type": row[3],
                "task_description": row[4],
                "is_completed": bool(row[5]),
                "created_date": row[6],
                "completed_timestamp": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    def get_workflow_todo_task(self, task_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT task_id, staff_id, party_id, party_type, task_description, is_completed, created_date, completed_timestamp, created_at FROM workflow_todo_list WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "task_id": row[0],
            "staff_id": row[1],
            "party_id": row[2],
            "party_type": row[3],
            "task_description": row[4],
            "is_completed": bool(row[5]),
            "created_date": row[6],
            "completed_timestamp": row[7],
            "created_at": row[8],
        }

    def generate_workflow_todos_from_pjp(self, plan_id: int, staff_id: int = 1) -> list[int]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT planned_distributor_ids, planned_retailer_ids FROM weekly_pjp_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not row:
            return []
        planned_distributors = json.loads(row[0] or "[]") if row[0] else []
        planned_retailers = json.loads(row[1] or "[]") if row[1] else []
        task_ids: list[int] = []
        default_tasks = ["Stock Audit", "Payment Discussion", "Order Collection"]
        for party_id in planned_distributors:
            for description in default_tasks:
                task_ids.append(self.create_workflow_todo_task(staff_id, int(party_id), "distributor", description))
        for party_id in planned_retailers:
            for description in default_tasks:
                task_ids.append(self.create_workflow_todo_task(staff_id, int(party_id), "retailer", description))
        return task_ids

    def validate_gps_coordinates(
        self,
        captured_latitude: float | None,
        captured_longitude: float | None,
        expected_latitude: float | None,
        expected_longitude: float | None,
        radius_meters: float = 100.0,
    ) -> dict[str, Any]:
        if captured_latitude is None or captured_longitude is None or expected_latitude is None or expected_longitude is None:
            return {"valid": False, "geofenced_status": "OUT_OF_BOUNDS", "distance_meters": None}
        try:
            from math import asin, cos, radians, sin, sqrt

            earth_radius = 6371000.0
            lat1 = radians(float(captured_latitude))
            lat2 = radians(float(expected_latitude))
            delta_lat = radians(float(expected_latitude) - float(captured_latitude))
            delta_lon = radians(float(expected_longitude) - float(captured_longitude))
            a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
            c = 2 * asin(sqrt(a))
            distance_meters = earth_radius * c
            matched = distance_meters <= float(radius_meters)
            return {"valid": matched, "geofenced_status": "MATCHED" if matched else "OUT_OF_BOUNDS", "distance_meters": round(distance_meters, 2)}
        except Exception:
            return {"valid": False, "geofenced_status": "OUT_OF_BOUNDS", "distance_meters": None}

    def cache_gps_coordinate_offline(self, visit_log_id: int, captured_latitude: float, captured_longitude: float, device_timestamp: str) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO offline_gps_cache (visit_log_id, captured_latitude, captured_longitude, device_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (visit_log_id, captured_latitude, captured_longitude, device_timestamp, created_at),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def record_gps_visit_verification(
        self,
        visit_log_id: int,
        captured_latitude: float | None,
        captured_longitude: float | None,
        device_timestamp: str,
        expected_latitude: float | None = None,
        expected_longitude: float | None = None,
        radius_meters: float = 100.0,
    ) -> int:
        validation = self.validate_gps_coordinates(captured_latitude, captured_longitude, expected_latitude, expected_longitude, radius_meters)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO gps_visit_verification_logs (visit_log_id, captured_latitude, captured_longitude, geofenced_status, device_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (visit_log_id, captured_latitude, captured_longitude, validation["geofenced_status"], device_timestamp, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            self.firebase_sync.push_record({
                "type": "gps_visit_verification",
                "visit_log_id": visit_log_id,
                "captured_latitude": captured_latitude,
                "captured_longitude": captured_longitude,
                "geofenced_status": validation["geofenced_status"],
                "device_timestamp": device_timestamp,
            })
            return int(cursor.lastrowid)

    def get_gps_verification_log(self, log_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT log_id, visit_log_id, captured_latitude, captured_longitude, geofenced_status, device_timestamp, created_at FROM gps_visit_verification_logs WHERE log_id = ?",
                (log_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "log_id": row[0],
            "visit_log_id": row[1],
            "captured_latitude": row[2],
            "captured_longitude": row[3],
            "geofenced_status": row[4],
            "device_timestamp": row[5],
            "created_at": row[6],
        }

    def validate_distributor_visit_payload(self, responses: dict[str, Any]) -> dict[str, Any]:
        templates = self.list_distributor_form_fields()
        errors: list[str] = []
        for template in templates:
            if template.get("is_required") and not str(responses.get(template["field_id"], "")).strip():
                errors.append(f"{template['field_label']} is required")
        return {"valid": not errors, "errors": errors}

    def validate_retailer_visit_payload(self, responses: dict[str, Any]) -> dict[str, Any]:
        templates = self.list_retailer_form_fields()
        errors: list[str] = []
        for template in templates:
            if template.get("is_required") and not str(responses.get(template["field_id"], "")).strip():
                errors.append(f"{template['field_label']} is required")
        return {"valid": not errors, "errors": errors}

    def save_verification_output(self, report_type: str, reference_id: str | None, content: str) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO verification_outputs (report_type, reference_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (report_type, reference_id, content, created_at),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def save_distributor_order_upload(
        self,
        verification_session_id: str,
        stage_key: str,
        file_type: str,
        filename: str,
        file_path: str,
        distributor_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        cleaned_session_id = (verification_session_id or "").strip()
        cleaned_stage_key = (stage_key or "").strip()
        cleaned_file_type = (file_type or "unknown").strip().lower() or "unknown"
        cleaned_filename = (filename or "").strip()
        cleaned_file_path = (file_path or "").strip()
        cleaned_distributor_name = (distributor_name or "").strip() or None
        if not cleaned_session_id:
            raise ValueError("verification_session_id is required")
        if not cleaned_stage_key:
            raise ValueError("stage_key is required")
        if not cleaned_filename:
            raise ValueError("filename is required")
        if not cleaned_file_path:
            raise ValueError("file_path is required")

        uploaded_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO distributor_order_uploads (
                    verification_session_id,
                    distributor_name,
                    stage_key,
                    file_type,
                    filename,
                    file_path,
                    uploaded_at,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_session_id,
                    cleaned_distributor_name,
                    cleaned_stage_key,
                    cleaned_file_type,
                    cleaned_filename,
                    cleaned_file_path,
                    uploaded_at,
                    json.dumps(metadata or {}, default=str),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_distributor_order_uploads(
        self,
        distributor_name: str | None = None,
        verification_session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with sqlite3.connect(self.db_path) as conn:
            query = (
                "SELECT id, verification_session_id, distributor_name, stage_key, file_type, filename, file_path, uploaded_at, metadata "
                "FROM distributor_order_uploads"
            )
            clauses: list[str] = []
            params: list[Any] = []
            if distributor_name:
                clauses.append("LOWER(COALESCE(distributor_name, '')) = LOWER(?)")
                params.append(distributor_name.strip())
            if verification_session_id:
                clauses.append("verification_session_id = ?")
                params.append(verification_session_id.strip())
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(safe_limit)
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "id": row[0],
                "verification_session_id": row[1],
                "distributor_name": row[2],
                "stage_key": row[3],
                "file_type": row[4],
                "filename": row[5],
                "file_path": row[6],
                "uploaded_at": row[7],
                "metadata": json.loads(row[8]) if row[8] else {},
            }
            for row in rows
        ]

    def global_search(self, query: str) -> dict[str, Any]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return {"query": normalized_query, "results": {"masters": [], "verifications": [], "visit_logs": [], "analytics": []}}

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT content, category, source_id, source_table FROM global_search_index WHERE global_search_index MATCH ? ORDER BY rank",
                (normalized_query,),
            ).fetchall()

        results: dict[str, list[dict[str, Any]]] = {"masters": [], "verifications": [], "visit_logs": [], "analytics": []}
        for content, category, source_id, source_table in rows:
            results.setdefault(category, []).append(
                {
                    "category": category,
                    "source_id": source_id,
                    "source_table": source_table,
                    "content": content,
                }
            )

        # Keep the output shape stable and include a fallback partial match for non-FTS cases.
        if not any(results.values()):
            with sqlite3.connect(self.db_path) as conn:
                fallback_rows = conn.execute(
                    "SELECT name, gst_no, zone, region, location, id, 'master' FROM master_distributors WHERE LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(gst_no, '')) LIKE ? OR LOWER(COALESCE(zone, '')) LIKE ? OR LOWER(COALESCE(region, '')) LIKE ? OR LOWER(COALESCE(location, '')) LIKE ? UNION ALL SELECT name, gst_no, zone, region, location, id, 'master' FROM master_retailers WHERE LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(gst_no, '')) LIKE ? OR LOWER(COALESCE(zone, '')) LIKE ? OR LOWER(COALESCE(region, '')) LIKE ? OR LOWER(COALESCE(location, '')) LIKE ?",
                    (f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%", f"%{normalized_query.lower()}%"),
                ).fetchall()
            for row in fallback_rows:
                results["masters"].append({"category": "masters", "source_id": row[5], "source_table": "masters", "content": " ".join(filter(None, [str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])]))})

        return {"query": normalized_query, "results": results}

    def get_last_visit_date(self, entity_type: str, entity_id: int) -> str | None:
        if entity_type == "distributor":
            table = "distributor_visit_logs"
            id_column = "distributor_id"
        elif entity_type == "retailer":
            table = "retailer_visit_logs"
            id_column = "retailer_id"
        else:
            return None
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT visit_date FROM {table} WHERE {id_column} = ? ORDER BY visit_date DESC, visit_time DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        return row[0] if row else None

    def get_morning_suggestion_list(self, current_date: str) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            distributors = conn.execute("SELECT id FROM distributors ORDER BY id").fetchall()
            retailers = conn.execute("SELECT id FROM retailers ORDER BY id").fetchall()

        for (distributor_id,) in distributors:
            last_visit = self.get_last_visit_date("distributor", distributor_id)
            suggestions.append({
                "entity_type": "distributor",
                "entity_id": distributor_id,
                "last_visit_date": last_visit,
                "priority_score": self._days_since(last_visit, current_date),
            })

        for (retailer_id,) in retailers:
            last_visit = self.get_last_visit_date("retailer", retailer_id)
            suggestions.append({
                "entity_type": "retailer",
                "entity_id": retailer_id,
                "last_visit_date": last_visit,
                "priority_score": self._days_since(last_visit, current_date),
            })

        suggestions.sort(key=lambda item: (item["priority_score"], item["entity_type"]), reverse=True)
        return suggestions

    def _days_since(self, last_visit: str | None, current_date: str) -> int:
        if not last_visit:
            return 9999
        try:
            start = datetime.strptime(last_visit, "%Y-%m-%d")
            end = datetime.strptime(current_date, "%Y-%m-%d")
            return max(0, (end - start).days)
        except ValueError:
            return 9999

    def create_weekly_pjp_plan(self, week_start_date: str, day_of_week: str, planned_distributor_ids: list[int], planned_retailer_ids: list[int], status: str = "planned") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO weekly_pjp_plans (week_start_date, day_of_week, planned_distributor_ids, planned_retailer_ids, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (week_start_date, day_of_week, json.dumps(planned_distributor_ids), json.dumps(planned_retailer_ids), status, created_at),
            )
            conn.commit()
            self.generate_workflow_todos_from_pjp(int(cursor.lastrowid), staff_id=1)
            return int(cursor.lastrowid)

    def run_retention_policy(self, retention_days: int = 365) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_text = cutoff.strftime("%Y-%m-%d")
        cutoff_dt = cutoff.isoformat()
        deleted_todos = 0
        deleted_gps = 0
        deleted_verifications = 0
        with sqlite3.connect(self.db_path) as conn:
            deleted_todos = int(conn.execute("DELETE FROM workflow_todo_list WHERE created_date < ?", (cutoff_text,)).rowcount)
            deleted_gps = int(conn.execute("DELETE FROM gps_visit_verification_logs WHERE device_timestamp < ?", (cutoff_dt,)).rowcount)
            deleted_verifications = int(conn.execute("DELETE FROM verification_outputs WHERE created_at < ?", (cutoff_dt,)).rowcount)
            conn.commit()
        return {
            "workflow_todos_deleted": deleted_todos,
            "gps_logs_deleted": deleted_gps,
            "verification_outputs_deleted": deleted_verifications,
            "cutoff_date": cutoff_text,
        }

    def generate_dsr_report(self, report_date: str, summary: str | None = None) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        distributor_visit_count = self._count_visit_logs("distributor", report_date)
        retailer_visit_count = self._count_visit_logs("retailer", report_date)
        orders_booked = 0
        payments_discussed = 0
        feedback_collected = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO dsr_reports (report_date, summary, distributor_visit_count, retailer_visit_count, orders_booked, payments_discussed, feedback_collected, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (report_date, summary or "Auto-generated DSR", distributor_visit_count, retailer_visit_count, orders_booked, payments_discussed, feedback_collected, created_at),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _count_visit_logs(self, entity_type: str, report_date: str) -> int:
        table = "distributor_visit_logs" if entity_type == "distributor" else "retailer_visit_logs"
        id_column = "distributor_id" if entity_type == "distributor" else "retailer_id"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE visit_date = ?",
                (report_date,),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_dsr_reports_by_date_range(self, from_date: str, to_date: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT report_id, report_date, summary, distributor_visit_count, retailer_visit_count, orders_booked, payments_discussed, feedback_collected, created_at FROM dsr_reports WHERE report_date BETWEEN ? AND ? ORDER BY report_date",
                (from_date, to_date),
            ).fetchall()
        return [
            {
                "report_id": row[0],
                "report_date": row[1],
                "summary": row[2],
                "distributor_visit_count": row[3],
                "retailer_visit_count": row[4],
                "orders_booked": row[5],
                "payments_discussed": row[6],
                "feedback_collected": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    def export_dsr_report(self, report_id: int, export_format: str = "excel") -> bytes | str:
        report = self.get_dsr_report(report_id)
        if not report:
            raise ValueError("DSR report not found")
        if export_format.lower() == "excel":
            dataframe = pd.DataFrame([report])
            output = BytesIO()
            dataframe.to_excel(output, index=False)
            output.seek(0)
            return output.getvalue()
        if export_format.lower() == "pdf":
            return f"PDF export placeholder for report {report_id}"
        return json.dumps(report)

    def get_dsr_report(self, report_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT report_id, report_date, summary, distributor_visit_count, retailer_visit_count, orders_booked, payments_discussed, feedback_collected, created_at FROM dsr_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "report_id": row[0],
            "report_date": row[1],
            "summary": row[2],
            "distributor_visit_count": row[3],
            "retailer_visit_count": row[4],
            "orders_booked": row[5],
            "payments_discussed": row[6],
            "feedback_collected": row[7],
            "created_at": row[8],
        }

    def _normalize_text(self, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).strip().split()).lower()

    def _normalize_gst_no(self, value: Any) -> str | None:
        cleaned = self._normalize_text(value).upper()
        if not cleaned:
            return None
        return cleaned

    def _coerce_float(self, value: Any) -> float | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _coerce_int(self, value: Any) -> int | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _resolve_template_header(self, template_config: dict[str, Any] | None, field_name: str, fallback: str) -> str:
        headers = (template_config or {}).get("headers", {})
        if isinstance(headers, dict):
            configured = headers.get(field_name)
            if configured:
                return str(configured)
        return fallback

    def _get_row_value(self, row: dict[str, Any], field_name: str, fallback: str, template_config: dict[str, Any] | None = None) -> Any:
        configured_header = self._resolve_template_header(template_config, field_name, fallback)
        candidates = [configured_header, fallback]
        alias_map = {
            "distributor_name": ["Distributor Name", "Distributor", "Name"],
            "distributor_code": ["Distributor Code", "Distributor ID", "Code"],
            "firm_name": ["Firm Name", "Firm"],
            "firm_nick_name": ["Firm nick name", "Firm Nick Name", "Firm Nickname"],
            "phone_number": ["Phone", "Phone Number", "Mobile Number", "Distributor Mobile Number", "Distributor Phone", "Retailer Mobile Number", "Retailer Phone"],
            "email": ["Email", "Email Address", "Email id", "Distributor Email", "Retailer Email"],
            "address": ["Address"],
            "pincode": ["Pincode", "Pin Code"],
            "gst_no": ["GSTIN", "GST Number", "GST No", "GST"],
            "distribution_state": ["Distribution State", "State", "Zone"],
            "distribution_area": ["Distribution Area", "Area", "Region"],
            "payment_terms": ["Payment Terms", "Payment Term"],
            "birthday": ["Birthday"],
            "anniversary": ["Anniversary"],
            "secondary_distributor_name": ["Secondary Distributor Name", "Secondary Contact Name", "Secondary Distributor", "Secondary Contact"],
            "secondary_distributor_phone_number": ["Secondary Distributor Mobile Number", "Secondary Distributor Phone", "Secondary Contact Mobile Number", "Secondary Contact Phone"],
            "secondary_distributor_birthday": ["Secondary Distributor Birthday", "Secondary Contact Birthday"],
            "secondary_distributor_anniversary": ["Secondary Distributor Anniversary", "Secondary Contact Anniversary"],
            "sales_executive_name": ["Sales Executive Name", "Sales Executive", "Sales Executive Contact Name"],
            "sales_executive_phone_number": ["Sales Executive Mobile Number", "Sales Executive Phone", "Sales Executive Phone Number"],
            "sales_executive_email": ["Sales Executive Email", "Sales Executive Email Address"],
            "sales_executive_birthday": ["Sales Executive Birthday"],
            "sales_executive_anniversary": ["Sales Executive Anniversary"],
            "retailer_name": ["Retailer Name", "Retailer", "Name"],
            "linked_distributor_gst_or_name": ["Distributor", "Linked Distributor GST or Name", "Distributor Name", "Distributor GST or Name"],
            "retailer_code": ["Retailer Code", "Retailer ID", "Code"],
            "location": ["Location", "City"],
            "secondary_retailer_name": ["Secondary Retailer Name", "Secondary Contact Name", "Secondary Retailer", "Secondary Contact"],
            "secondary_retailer_phone_number": ["Secondary Retailer Mobile Number", "Secondary Retailer Phone", "Secondary Contact Mobile Number", "Secondary Contact Phone"],
            "secondary_retailer_birthday": ["Secondary Retailer Birthday", "Secondary Contact Birthday"],
            "secondary_retailer_anniversary": ["Secondary Retailer Anniversary", "Secondary Contact Anniversary"],
        }
        candidates.extend(alias_map.get(field_name, []))

        normalized_lookup = {str(key).strip().lower(): key for key in row.keys()}
        for candidate in candidates:
            if candidate in row:
                return row[candidate]
            if isinstance(candidate, str):
                normalized_candidate = candidate.strip().lower()
                if normalized_candidate in normalized_lookup:
                    return row[normalized_lookup[normalized_candidate]]
        return row.get(fallback, "")

    def _generate_unique_master_id(self, prefix: str) -> str:
        return f"{prefix}{uuid4().hex[:12].upper()}"

    def _load_rows_from_upload(self, path: str | Path) -> list[dict[str, Any]]:
        import pandas as pd

        file_path = Path(path)
        if file_path.suffix.lower() == ".csv":
            dataframe = pd.read_csv(file_path)
        else:
            dataframe = pd.read_excel(file_path, sheet_name=0)
        dataframe = dataframe.fillna("")
        dataframe.columns = [str(col).strip() for col in dataframe.columns]
        return dataframe.to_dict(orient="records")

    def _load_order_sheet_dataframe(self, path: str | Path) -> pd.DataFrame:
        file_path = Path(path)
        if file_path.suffix.lower() == ".csv":
            dataframe = pd.read_csv(file_path)
        else:
            dataframe = pd.read_excel(file_path, sheet_name=0, header=1)
            normalized_columns = [str(col).strip().lower() for col in dataframe.columns]
            signal_columns = {
                "brand",
                "product",
                "size",
                "selling price",
                "exmill price",
                "min bale pack",
                "bale size",
            }
            matched_signals = sum(1 for value in normalized_columns if value in signal_columns)
            if matched_signals < 3:
                dataframe = pd.read_excel(file_path, sheet_name=0, header=0)
        dataframe = dataframe.fillna("")
        dataframe.columns = [str(col).strip() for col in dataframe.columns]
        return dataframe

    def _build_template_dataframe(self, template_config: dict[str, Any] | None, template_type: str) -> pd.DataFrame:
        import pandas as pd

        default_templates = {
            "distributors": {
                "headers": [
                    "distributor_code",
                    "firm_name",
                    "firm_nick_name",
                    "distributor_name",
                    "phone_number",
                    "location",
                    "address",
                    "pincode",
                    "email",
                    "distribution_state",
                    "distribution_area",
                    "gst_no",
                    "payment_terms",
                    "birthday",
                    "anniversary",
                    "secondary_distributor_name",
                    "secondary_distributor_phone_number",
                    "secondary_distributor_birthday",
                    "secondary_distributor_anniversary",
                    "sales_executive_name",
                    "sales_executive_phone_number",
                    "sales_executive_email",
                    "sales_executive_birthday",
                    "sales_executive_anniversary",
                    "zone",
                    "region",
                    "credit_limit",
                    "initial_outstanding_balance",
                ],
                "label_map": {
                    "distributor_code": "Distributor Code",
                    "firm_name": "Firm Name",
                    "firm_nick_name": "Firm nick name",
                    "distributor_name": "Distributor Name",
                    "phone_number": "Mobile Number",
                    "location": "Location",
                    "address": "Address",
                    "pincode": "Pincode",
                    "email": "Email id",
                    "distribution_state": "Distribution State",
                    "distribution_area": "Distribution Area",
                    "gst_no": "GSTIN",
                    "payment_terms": "Payment Terms",
                    "birthday": "Birthday",
                    "anniversary": "Anniversary",
                    "secondary_distributor_name": "Secondary Distributor Name",
                    "secondary_distributor_phone_number": "Secondary Distributor Mobile Number",
                    "secondary_distributor_birthday": "Secondary Distributor Birthday",
                    "secondary_distributor_anniversary": "Secondary Distributor Anniversary",
                    "sales_executive_name": "Sales Executive Name",
                    "sales_executive_phone_number": "Sales Executive Mobile Number",
                    "sales_executive_email": "Sales Executive Email",
                    "sales_executive_birthday": "Sales Executive Birthday",
                    "sales_executive_anniversary": "Sales Executive Anniversary",
                    "zone": "Zone",
                    "region": "Region",
                    "credit_limit": "Credit Limit",
                    "initial_outstanding_balance": "Opening Balance",
                },
            },
            "retailers": {
                "headers": [
                    "retailer_name",
                    "linked_distributor_gst_or_name",
                    "retailer_code",
                    "location",
                    "phone_number",
                    "email",
                    "address",
                    "gst_no",
                    "secondary_retailer_name",
                    "secondary_retailer_phone_number",
                    "secondary_retailer_birthday",
                    "secondary_retailer_anniversary",
                    "sales_executive_name",
                    "sales_executive_phone_number",
                    "sales_executive_email",
                    "sales_executive_birthday",
                    "sales_executive_anniversary",
                ],
                "label_map": {
                    "retailer_name": "Retailer Name",
                    "linked_distributor_gst_or_name": "Distributor",
                    "retailer_code": "Retailer Code",
                    "location": "Location",
                    "phone_number": "Phone",
                    "email": "Email",
                    "address": "Address",
                    "gst_no": "GSTIN",
                    "secondary_retailer_name": "Secondary Retailer Name",
                    "secondary_retailer_phone_number": "Secondary Retailer Mobile Number",
                    "secondary_retailer_birthday": "Secondary Retailer Birthday",
                    "secondary_retailer_anniversary": "Secondary Retailer Anniversary",
                    "sales_executive_name": "Sales Executive Name",
                    "sales_executive_phone_number": "Sales Executive Mobile Number",
                    "sales_executive_email": "Sales Executive Email",
                    "sales_executive_birthday": "Sales Executive Birthday",
                    "sales_executive_anniversary": "Sales Executive Anniversary",
                },
            },
            "articles": {
                "headers": [
                    "category_name",
                    "design_code",
                    "color_way",
                    "base_rate",
                    "gst_percentage",
                    "pcs_per_bale",
                ],
                "label_map": {
                    "category_name": "Category",
                    "design_code": "Design Code",
                    "color_way": "Colour",
                    "base_rate": "Base Rate",
                    "gst_percentage": "GST %",
                    "pcs_per_bale": "Pcs / Bale",
                },
            },
        }
        selected = default_templates[template_type]
        merged_config = dict(selected)
        if template_config:
            header_map = template_config.get("headers", {}) or {}
            merged_config["label_map"] = {key: header_map.get(key, selected["label_map"].get(key, key)) for key in selected["headers"]}
        return pd.DataFrame([{column: "" for column in selected["headers"]}]).rename(columns=merged_config["label_map"])

    def _generate_template_bytes(self, template_type: str, template_config: dict[str, Any] | None = None, file_format: str = "excel") -> bytes:
        dataframe = self._build_template_dataframe(template_config, template_type)
        if file_format.lower() == "csv":
            output = StringIO()
            dataframe.to_csv(output, index=False)
            return output.getvalue().encode("utf-8")
        output = BytesIO()
        dataframe.to_excel(output, index=False)
        return output.getvalue()

    def generate_master_template(self, template_type: str, file_format: str = "excel", template_config: dict[str, Any] | None = None) -> bytes:
        allowed_types = {"distributors", "retailers", "articles"}
        if template_type not in allowed_types:
            raise ValueError(f"Unsupported template type: {template_type}")
        return self._generate_template_bytes(template_type, template_config=template_config, file_format=file_format)

    def bulk_upload_masters(self, master_type: str, path: str | Path, template_config: dict[str, Any] | None = None) -> dict[str, Any]:
        if master_type not in {"distributors", "retailers"}:
            raise ValueError("Unsupported master type")

        rows = self._load_rows_from_upload(path)
        inserted = 0
        updated = 0
        skipped = 0
        errors: list[str] = []

        with sqlite3.connect(self.db_path) as conn:
            if master_type == "distributors":
                for row in rows:
                    try:
                        name = self._canonicalize_known_master_name(self._get_row_value(row, "distributor_name", "Distributor Name", template_config))
                        name_key = self._normalize_text(name)
                        if not name_key:
                            skipped += 1
                            continue
                        distributor_code = self._normalize_text(self._get_row_value(row, "distributor_code", "Distributor Code", template_config))
                        firm_name = self._canonicalize_known_master_name(self._get_row_value(row, "firm_name", "Firm Name", template_config))
                        firm_nick_name = self._normalize_text(self._get_row_value(row, "firm_nick_name", "Firm nick name", template_config))
                        if not firm_nick_name:
                            firm_nick_name = self._normalize_text(self._get_row_value(row, "firm_nick_name", "Firm Nick Name", template_config))
                        gst_no = self._normalize_gst_no(self._get_row_value(row, "gst_no", "GST Number", template_config))
                        if not gst_no:
                            gst_no = self._normalize_gst_no(self._get_row_value(row, "gst_no", "GSTIN", template_config))

                        distribution_state = self._normalize_text(self._get_row_value(row, "distribution_state", "Distribution State", template_config))
                        distribution_area = self._normalize_text(self._get_row_value(row, "distribution_area", "Distribution Area", template_config))
                        zone = distribution_state or self._normalize_text(self._get_row_value(row, "zone", "Zone", template_config))
                        region = distribution_area or self._normalize_text(self._get_row_value(row, "region", "Region", template_config))

                        location = self._normalize_text(self._get_row_value(row, "location", "Location", template_config))
                        pincode = self._normalize_text(self._get_row_value(row, "pincode", "Pincode", template_config))
                        payment_terms = self._normalize_text(self._get_row_value(row, "payment_terms", "Payment Terms", template_config))
                        birthday = self._normalize_text(self._get_row_value(row, "birthday", "Birthday", template_config))
                        anniversary = self._normalize_text(self._get_row_value(row, "anniversary", "Anniversary", template_config))
                        secondary_distributor_name = self._normalize_text(self._get_row_value(row, "secondary_distributor_name", "Secondary Distributor Name", template_config))
                        secondary_distributor_phone_number = self._normalize_text(self._get_row_value(row, "secondary_distributor_phone_number", "Secondary Distributor Mobile Number", template_config))
                        secondary_distributor_birthday = self._normalize_text(self._get_row_value(row, "secondary_distributor_birthday", "Secondary Distributor Birthday", template_config))
                        secondary_distributor_anniversary = self._normalize_text(self._get_row_value(row, "secondary_distributor_anniversary", "Secondary Distributor Anniversary", template_config))
                        sales_executive_name = self._normalize_text(self._get_row_value(row, "sales_executive_name", "Sales Executive Name", template_config))
                        sales_executive_phone_number = self._normalize_text(self._get_row_value(row, "sales_executive_phone_number", "Sales Executive Mobile Number", template_config))
                        sales_executive_email = self._normalize_text(self._get_row_value(row, "sales_executive_email", "Sales Executive Email", template_config))
                        sales_executive_birthday = self._normalize_text(self._get_row_value(row, "sales_executive_birthday", "Sales Executive Birthday", template_config))
                        sales_executive_anniversary = self._normalize_text(self._get_row_value(row, "sales_executive_anniversary", "Sales Executive Anniversary", template_config))

                        credit_limit = self._coerce_float(self._get_row_value(row, "credit_limit", "Credit Limit", template_config))
                        phone_number = self._normalize_text(self._get_row_value(row, "phone_number", "Mobile Number", template_config))
                        if not phone_number:
                            phone_number = self._normalize_text(self._get_row_value(row, "phone_number", "Phone", template_config))

                        email = self._normalize_text(self._get_row_value(row, "email", "Email id", template_config))
                        if not email:
                            email = self._normalize_text(self._get_row_value(row, "email", "Email", template_config))

                        address = self._normalize_text(self._get_row_value(row, "address", "Address", template_config))
                        if gst_no and len(gst_no) < 10:
                            errors.append(f"Invalid GST for distributor {name}")
                            skipped += 1
                            continue

                        existing_by_name_rows = conn.execute(
                            "SELECT id, distributor_id, name, gst_no, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(name) = ?",
                            (name_key,),
                        ).fetchall()
                        existing_by_name = existing_by_name_rows[0] if existing_by_name_rows else None
                        if len(existing_by_name_rows) > 1:
                            errors.append(f"Ambiguous distributor name match for '{name}'. Use unique GST Number.")
                            skipped += 1
                            continue

                        existing_by_gst_rows: list[tuple[Any, ...]] = []
                        if gst_no:
                            existing_by_gst_rows = conn.execute(
                                "SELECT id, distributor_id, name, gst_no, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(COALESCE(gst_no, '')) = ?",
                                (gst_no.lower(),),
                            ).fetchall()
                        existing_by_gst = existing_by_gst_rows[0] if existing_by_gst_rows else None
                        if len(existing_by_gst_rows) > 1:
                            errors.append(f"Ambiguous GST match for distributor '{name}' with GST '{gst_no}'.")
                            skipped += 1
                            continue

                        existing_by_code_rows: list[tuple[Any, ...]] = []
                        if distributor_code:
                            existing_by_code_rows = conn.execute(
                                "SELECT id, distributor_id, name, gst_no, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(COALESCE(distributor_id, '')) = ?",
                                (distributor_code.lower(),),
                            ).fetchall()
                        existing_by_code = existing_by_code_rows[0] if existing_by_code_rows else None
                        if len(existing_by_code_rows) > 1:
                            errors.append(f"Ambiguous distributor code match for '{distributor_code}'.")
                            skipped += 1
                            continue

                        if existing_by_code is not None and existing_by_name is not None and int(existing_by_code[0]) != int(existing_by_name[0]):
                            errors.append(f"Conflict for distributor '{name}': name and distributor code point to different records.")
                            skipped += 1
                            continue

                        if existing_by_code is not None and existing_by_gst is not None and int(existing_by_code[0]) != int(existing_by_gst[0]):
                            errors.append(f"Conflict for distributor code '{distributor_code}': GST points to a different record.")
                            skipped += 1
                            continue

                        if existing_by_name is not None and existing_by_gst is not None and int(existing_by_name[0]) != int(existing_by_gst[0]):
                            errors.append(f"Conflict for distributor '{name}': name and GST point to different records.")
                            skipped += 1
                            continue

                        if existing_by_name is not None and gst_no:
                            existing_name_gst = self._normalize_gst_no(existing_by_name[3])
                            if existing_name_gst and existing_name_gst != gst_no:
                                errors.append(f"Conflict for distributor '{name}': existing GST '{existing_name_gst}' differs from uploaded GST '{gst_no}'.")
                                skipped += 1
                                continue

                        if existing_by_gst is not None and not distributor_code:
                            existing_gst_name = self._normalize_text(existing_by_gst[2])
                            if existing_gst_name and existing_gst_name.lower() != name.lower():
                                errors.append(
                                    f"Conflict for GST '{gst_no}': existing distributor '{existing_by_gst[2]}' differs from uploaded '{name}'."
                                )
                                skipped += 1
                                continue

                        existing_row = existing_by_code or existing_by_gst or existing_by_name
                        if existing_row is not None:
                            distributor_id = int(existing_row[0])
                            updated_code = distributor_code or existing_row[1]
                            updated_name = name or existing_row[2]
                            updated_gst = gst_no or existing_row[3]
                            updated_firm_name = firm_name or existing_row[4]
                            updated_firm_nick_name = firm_nick_name or existing_row[5]
                            updated_zone = zone or existing_row[6]
                            updated_region = region or existing_row[7]
                            updated_location = location or existing_row[8]
                            updated_address = address or existing_row[9]
                            updated_pincode = pincode or existing_row[10]
                            updated_phone = phone_number or existing_row[11]
                            updated_email = email or existing_row[12]
                            updated_payment_terms = payment_terms or existing_row[13]
                            updated_birthday = birthday or existing_row[14]
                            updated_anniversary = anniversary or existing_row[15]
                            updated_secondary_distributor_name = secondary_distributor_name or existing_row[16]
                            updated_secondary_distributor_phone_number = secondary_distributor_phone_number or existing_row[17]
                            updated_secondary_distributor_birthday = secondary_distributor_birthday or existing_row[18]
                            updated_secondary_distributor_anniversary = secondary_distributor_anniversary or existing_row[19]
                            updated_sales_executive_name = sales_executive_name or existing_row[20]
                            updated_sales_executive_phone_number = sales_executive_phone_number or existing_row[21]
                            updated_sales_executive_email = sales_executive_email or existing_row[22]
                            updated_sales_executive_birthday = sales_executive_birthday or existing_row[23]
                            updated_sales_executive_anniversary = sales_executive_anniversary or existing_row[24]
                            updated_credit_limit = credit_limit if credit_limit is not None else existing_row[25]
                            updated_status = existing_row[26] or "active"

                            conn.execute(
                                """
                                UPDATE master_distributors
                                SET
                                    distributor_id = ?,
                                    distributor_code = ?,
                                    name = ?,
                                    gst_no = ?,
                                    firm_name = ?,
                                    firm_nick_name = ?,
                                    zone = ?,
                                    region = ?,
                                    location = ?,
                                    address = ?,
                                    pincode = ?,
                                    phone_number = ?,
                                    email = ?,
                                    payment_terms = ?,
                                    birthday = ?,
                                    anniversary = ?,
                                    secondary_distributor_name = ?,
                                    secondary_distributor_phone_number = ?,
                                    secondary_distributor_birthday = ?,
                                    secondary_distributor_anniversary = ?,
                                    sales_executive_name = ?,
                                    sales_executive_phone_number = ?,
                                    sales_executive_email = ?,
                                    sales_executive_birthday = ?,
                                    sales_executive_anniversary = ?,
                                    credit_limit = ?,
                                    status = ?
                                WHERE id = ?
                                """,
                                (
                                    updated_code,
                                    updated_code,
                                    updated_name,
                                    updated_gst,
                                    updated_firm_name,
                                    updated_firm_nick_name,
                                    updated_zone,
                                    updated_region,
                                    updated_location,
                                    updated_address,
                                    updated_pincode,
                                    updated_phone,
                                    updated_email,
                                    updated_payment_terms,
                                    updated_birthday,
                                    updated_anniversary,
                                    updated_secondary_distributor_name,
                                    updated_secondary_distributor_phone_number,
                                    updated_secondary_distributor_birthday,
                                    updated_secondary_distributor_anniversary,
                                    updated_sales_executive_name,
                                    updated_sales_executive_phone_number,
                                    updated_sales_executive_email,
                                    updated_sales_executive_birthday,
                                    updated_sales_executive_anniversary,
                                    updated_credit_limit,
                                    updated_status,
                                    distributor_id,
                                ),
                            )
                            updated += 1
                            continue

                        self.add_master_distributor(
                            name=name,
                            distributor_code=distributor_code or None,
                            firm_name=firm_name or None,
                            firm_nick_name=firm_nick_name or None,
                            gst_no=gst_no,
                            zone=zone or None,
                            region=region or None,
                            location=location or None,
                            address=address or None,
                            pincode=pincode or None,
                            phone_number=phone_number or None,
                            email=email or None,
                            payment_terms=payment_terms or None,
                            birthday=birthday or None,
                            anniversary=anniversary or None,
                            secondary_distributor_name=secondary_distributor_name or None,
                            secondary_distributor_phone_number=secondary_distributor_phone_number or None,
                            secondary_distributor_birthday=secondary_distributor_birthday or None,
                            secondary_distributor_anniversary=secondary_distributor_anniversary or None,
                            sales_executive_name=sales_executive_name or None,
                            sales_executive_phone_number=sales_executive_phone_number or None,
                            sales_executive_email=sales_executive_email or None,
                            sales_executive_birthday=sales_executive_birthday or None,
                            sales_executive_anniversary=sales_executive_anniversary or None,
                            credit_limit=credit_limit,
                            status="active",
                            conn=conn,
                        )
                        inserted += 1
                    except Exception as exc:  # pragma: no cover - defensive path
                        errors.append(str(exc))
                        skipped += 1
            else:
                for row in rows:
                    try:
                        name = self._canonicalize_known_master_name(self._get_row_value(row, "retailer_name", "Retailer Name", template_config))
                        name_key = self._normalize_text(name)
                        if not name_key:
                            skipped += 1
                            continue
                        distributor_reference = self._canonicalize_known_master_name(self._get_row_value(row, "linked_distributor_gst_or_name", "Distributor", template_config))
                        retailer_code = self._normalize_text(self._get_row_value(row, "retailer_code", "Retailer Code", template_config))
                        location = self._normalize_text(self._get_row_value(row, "location", "Location", template_config))
                        phone_number = self._normalize_text(self._get_row_value(row, "phone_number", "Phone", template_config))
                        email = self._normalize_text(self._get_row_value(row, "email", "Email", template_config))
                        address = self._normalize_text(self._get_row_value(row, "address", "Address", template_config))
                        gst_no = self._normalize_gst_no(self._get_row_value(row, "gst_no", "GSTIN", template_config))
                        secondary_retailer_name = self._normalize_text(self._get_row_value(row, "secondary_retailer_name", "Secondary Retailer Name", template_config))
                        secondary_retailer_phone_number = self._normalize_text(self._get_row_value(row, "secondary_retailer_phone_number", "Secondary Retailer Mobile Number", template_config))
                        secondary_retailer_birthday = self._normalize_text(self._get_row_value(row, "secondary_retailer_birthday", "Secondary Retailer Birthday", template_config))
                        secondary_retailer_anniversary = self._normalize_text(self._get_row_value(row, "secondary_retailer_anniversary", "Secondary Retailer Anniversary", template_config))
                        sales_executive_name = self._normalize_text(self._get_row_value(row, "sales_executive_name", "Sales Executive Name", template_config))
                        sales_executive_phone_number = self._normalize_text(self._get_row_value(row, "sales_executive_phone_number", "Sales Executive Mobile Number", template_config))
                        sales_executive_email = self._normalize_text(self._get_row_value(row, "sales_executive_email", "Sales Executive Email", template_config))
                        sales_executive_birthday = self._normalize_text(self._get_row_value(row, "sales_executive_birthday", "Sales Executive Birthday", template_config))
                        sales_executive_anniversary = self._normalize_text(self._get_row_value(row, "sales_executive_anniversary", "Sales Executive Anniversary", template_config))
                        distributor = None
                        if distributor_reference:
                            distributor = self.get_master_distributor_by_name(distributor_reference)
                            if distributor is None:
                                distributor = self._find_master_distributor_by_gst_or_name(distributor_reference)
                        if distributor is None:
                            distributor = self._find_or_create_distributor_from_reference(distributor_reference)
                        if distributor is None:
                            skipped += 1
                            continue
                        existing_retailer_id = self._find_similar_master_entry(
                            conn,
                            "master_retailers",
                            "name",
                            name,
                            extra_filters={"distributor_id": distributor["id"]},
                        )
                        if existing_retailer_id is not None:
                            conn.execute(
                                """
                                UPDATE master_retailers
                                SET
                                    retailer_code = COALESCE(NULLIF(?, ''), retailer_code),
                                    location = COALESCE(NULLIF(?, ''), location),
                                    phone_number = COALESCE(NULLIF(?, ''), phone_number),
                                    email = COALESCE(NULLIF(?, ''), email),
                                    address = COALESCE(NULLIF(?, ''), address),
                                    gst_no = COALESCE(NULLIF(?, ''), gst_no),
                                    secondary_retailer_name = COALESCE(NULLIF(?, ''), secondary_retailer_name),
                                    secondary_retailer_phone_number = COALESCE(NULLIF(?, ''), secondary_retailer_phone_number),
                                    secondary_retailer_birthday = COALESCE(NULLIF(?, ''), secondary_retailer_birthday),
                                    secondary_retailer_anniversary = COALESCE(NULLIF(?, ''), secondary_retailer_anniversary),
                                    sales_executive_name = COALESCE(NULLIF(?, ''), sales_executive_name),
                                    sales_executive_phone_number = COALESCE(NULLIF(?, ''), sales_executive_phone_number),
                                    sales_executive_email = COALESCE(NULLIF(?, ''), sales_executive_email),
                                    sales_executive_birthday = COALESCE(NULLIF(?, ''), sales_executive_birthday),
                                    sales_executive_anniversary = COALESCE(NULLIF(?, ''), sales_executive_anniversary)
                                WHERE id = ?
                                """,
                                (
                                    retailer_code or "",
                                    location or "",
                                    phone_number or "",
                                    email or "",
                                    address or "",
                                    gst_no or "",
                                    secondary_retailer_name or "",
                                    secondary_retailer_phone_number or "",
                                    secondary_retailer_birthday or "",
                                    secondary_retailer_anniversary or "",
                                    sales_executive_name or "",
                                    sales_executive_phone_number or "",
                                    sales_executive_email or "",
                                    sales_executive_birthday or "",
                                    sales_executive_anniversary or "",
                                    int(existing_retailer_id),
                                ),
                            )
                            updated += 1
                            continue
                        retailer_id = self.add_master_retailer(
                            name=name,
                            distributor_id=distributor["id"],
                            location=location or None,
                            retailer_code=retailer_code or None,
                            phone_number=phone_number or None,
                            email=email or None,
                            address=address or None,
                            gst_no=gst_no or None,
                            secondary_retailer_name=secondary_retailer_name or None,
                            secondary_retailer_phone_number=secondary_retailer_phone_number or None,
                            secondary_retailer_birthday=secondary_retailer_birthday or None,
                            secondary_retailer_anniversary=secondary_retailer_anniversary or None,
                            sales_executive_name=sales_executive_name or None,
                            sales_executive_phone_number=sales_executive_phone_number or None,
                            sales_executive_email=sales_executive_email or None,
                            sales_executive_birthday=sales_executive_birthday or None,
                            sales_executive_anniversary=sales_executive_anniversary or None,
                            conn=conn,
                        )
                        inserted += 1
                    except Exception as exc:  # pragma: no cover - defensive path
                        errors.append(str(exc))
                        skipped += 1

        self.firebase_sync.push_record({"type": "bulk_master_upload", "master_type": master_type, "inserted": inserted, "skipped": skipped})
        return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors, "rows_processed": len(rows)}

    def bulk_upload_articles(self, path: str | Path, template_config: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = self._load_rows_from_upload(path)
        inserted = 0
        skipped = 0
        errors: list[str] = []

        with sqlite3.connect(self.db_path) as conn:
            for row in rows:
                try:
                    payload = {
                        "category_name": self._normalize_text(row.get(self._resolve_template_header(template_config, "category_name", "Category"))),
                        "design_code": self._normalize_text(row.get(self._resolve_template_header(template_config, "design_code", "Design Code"))),
                        "color_way": self._normalize_text(row.get(self._resolve_template_header(template_config, "color_way", "Colour"))),
                        "base_rate": self._coerce_float(row.get(self._resolve_template_header(template_config, "base_rate", "Base Rate"))),
                        "gst_percentage": self._coerce_float(row.get(self._resolve_template_header(template_config, "gst_percentage", "GST %"))),
                        "pcs_per_bale": self._coerce_float(row.get(self._resolve_template_header(template_config, "pcs_per_bale", "Pcs / Bale"))),
                    }
                    if not payload["category_name"] and not payload["design_code"]:
                        skipped += 1
                        continue
                    self.article_service.save_article(payload, conn=conn)
                    inserted += 1
                except Exception as exc:  # pragma: no cover - defensive path
                    errors.append(str(exc))
                    skipped += 1

        return {"inserted": inserted, "skipped": skipped, "errors": errors, "rows_processed": len(rows)}

    def build_article_master_from_order_sheet(self, path: str | Path, template_config: dict[str, Any] | None = None) -> dict[str, Any]:
        dataframe = self._load_order_sheet_dataframe(path)
        rows = dataframe.to_dict(orient="records")
        inserted = 0
        skipped = 0
        duplicates = 0
        errors: list[str] = []

        category_header = self._resolve_template_header(template_config, "category_name", "Product")
        design_header = self._resolve_template_header(template_config, "design_code", "Brand")
        variant_header = self._resolve_template_header(template_config, "variant", "Size")
        color_header = self._resolve_template_header(template_config, "color_way", "Print Style")
        base_rate_header = self._resolve_template_header(template_config, "base_rate", "ExMill Price")
        fallback_base_rate_header = self._resolve_template_header(template_config, "fallback_base_rate", "Selling Price")
        gst_header = self._resolve_template_header(template_config, "gst_percentage", "GST %")
        pcs_header = self._resolve_template_header(template_config, "pcs_per_bale", "Min bale pack")
        fallback_pcs_header = self._resolve_template_header(template_config, "fallback_pcs_per_bale", "Bale Size")

        def _article_key(category_name: str, design_name: str, color_way: str, base_rate: float, gst_percentage: float, pcs_per_bale: float) -> tuple[str, str, str, float, float, float]:
            return (
                category_name.strip().lower(),
                design_name.strip().lower(),
                color_way.strip().lower(),
                round(float(base_rate or 0.0), 4),
                round(float(gst_percentage or 0.0), 4),
                round(float(pcs_per_bale or 0.0), 4),
            )

        with sqlite3.connect(self.db_path) as conn:
            existing_rows = conn.execute(
                "SELECT category_name, design_name, COALESCE(color_way, ''), COALESCE(base_rate, 0), COALESCE(gst_percentage, 0), COALESCE(pcs_per_bale, 0) FROM article_master"
            ).fetchall()
            seen_keys = {
                _article_key(str(item[0] or ""), str(item[1] or ""), str(item[2] or ""), float(item[3] or 0.0), float(item[4] or 0.0), float(item[5] or 0.0))
                for item in existing_rows
            }

            for row in rows:
                try:
                    category_name = self._normalize_text(row.get(category_header))
                    design_code = self._normalize_text(row.get(design_header))
                    variant = self._normalize_text(row.get(variant_header))
                    color_way = self._normalize_text(row.get(color_header))

                    if variant:
                        design_code = f"{design_code} {variant}".strip()

                    base_rate = self._coerce_float(row.get(base_rate_header))
                    if base_rate is None:
                        base_rate = self._coerce_float(row.get(fallback_base_rate_header))

                    gst_percentage = self._coerce_float(row.get(gst_header))
                    pcs_per_bale = self._coerce_float(row.get(pcs_header))
                    if pcs_per_bale is None:
                        pcs_per_bale = self._coerce_float(row.get(fallback_pcs_header))

                    payload = {
                        "category_name": category_name,
                        "design_code": design_code,
                        "color_way": color_way,
                        "base_rate": base_rate or 0.0,
                        "gst_percentage": gst_percentage or 0.0,
                        "pcs_per_bale": pcs_per_bale or 0.0,
                    }

                    if not payload["category_name"] and not payload["design_code"]:
                        skipped += 1
                        continue

                    sanitized_payload = self.article_service.sanitize_article_payload(payload)
                    dedupe_key = _article_key(
                        sanitized_payload["category_name"],
                        sanitized_payload["design_name"],
                        sanitized_payload["color_way"],
                        sanitized_payload["base_rate"],
                        sanitized_payload["gst_percentage"],
                        sanitized_payload["pcs_per_bale"],
                    )
                    if dedupe_key in seen_keys:
                        duplicates += 1
                        continue

                    self.article_service.save_article(payload, conn=conn)
                    seen_keys.add(dedupe_key)
                    inserted += 1
                except Exception as exc:  # pragma: no cover - defensive path
                    errors.append(str(exc))
                    skipped += 1

        return {
            "inserted": inserted,
            "duplicates": duplicates,
            "skipped": skipped,
            "errors": errors,
            "rows_processed": len(rows),
            "source": str(Path(path)),
        }

    def _find_master_distributor_by_gst_or_name(self, reference: str) -> dict[str, Any] | None:
        canonical_reference = self._canonicalize_known_master_name(reference)
        lookup_values = [self._normalize_text(canonical_reference)]
        original_value = self._normalize_text(reference)
        if original_value and original_value not in lookup_values:
            lookup_values.append(original_value)

        if not lookup_values[0]:
            return None
        with sqlite3.connect(self.db_path) as conn:
            query = (
                "SELECT id, distributor_id, firm_name, firm_nick_name, name, gst_no, zone, region, location, address, pincode, payment_terms, birthday, anniversary, credit_limit, latitude, longitude, phone_number, email, status, created_at FROM master_distributors WHERE "
                + " OR ".join("LOWER(name) = ?" for _ in lookup_values)
                + " OR LOWER(gst_no) = ? LIMIT 1"
            )
            exact = conn.execute(query, [*lookup_values, lookup_values[0]]).fetchone()
        if exact is None:
            return None
        return {
            "id": exact[0],
            "distributor_id": exact[1],
            "firm_name": exact[2],
            "firm_nick_name": exact[3],
            "name": exact[4],
            "gst_no": exact[5],
            "zone": exact[6],
            "region": exact[7],
            "location": exact[8],
            "address": exact[9],
            "pincode": exact[10],
            "payment_terms": exact[11],
            "birthday": exact[12],
            "anniversary": exact[13],
            "credit_limit": exact[14],
            "latitude": exact[15],
            "longitude": exact[16],
            "phone_number": exact[17],
            "email": exact[18],
            "status": exact[19],
            "created_at": exact[20],
        }

    def _find_or_create_distributor_from_reference(self, reference: str) -> dict[str, Any] | None:
        if not self._normalize_text(reference):
            return None
        return self.get_master_distributor_by_name(reference) or self.add_master_distributor(name=reference, gst_no=None, zone=None, region=None, credit_limit=None)

    def add_master_distributor(
        self,
        name: str,
        firm_name: str | None = None,
        firm_nick_name: str | None = None,
        gst_no: str | None = None,
        zone: str | None = None,
        region: str | None = None,
        location: str | None = None,
        address: str | None = None,
        pincode: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
        payment_terms: str | None = None,
        birthday: str | None = None,
        anniversary: str | None = None,
        secondary_distributor_name: str | None = None,
        secondary_distributor_phone_number: str | None = None,
        secondary_distributor_birthday: str | None = None,
        secondary_distributor_anniversary: str | None = None,
        sales_executive_name: str | None = None,
        sales_executive_phone_number: str | None = None,
        sales_executive_email: str | None = None,
        sales_executive_birthday: str | None = None,
        sales_executive_anniversary: str | None = None,
        distributor_code: str | None = None,
        credit_limit: float | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        status: str = "active",
        conn: sqlite3.Connection | None = None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        canonical_name = self._canonicalize_known_master_name(name)
        canonical_firm_name = self._canonicalize_known_master_name(firm_name) if firm_name is not None else None
        connection = conn or sqlite3.connect(self.db_path)
        should_close = conn is None
        try:
            existing_id = self._find_similar_master_entry(connection, "master_distributors", "name", canonical_name)
            if existing_id is not None:
                return existing_id

            cursor = connection.execute(
                """
                INSERT INTO master_distributors (
                    distributor_id,
                    distributor_code,
                    firm_name,
                    firm_nick_name,
                    name,
                    phone_number,
                    location,
                    address,
                    pincode,
                    email,
                    gst_no,
                    zone,
                    region,
                    payment_terms,
                    birthday,
                    anniversary,
                    secondary_distributor_name,
                    secondary_distributor_phone_number,
                    secondary_distributor_birthday,
                    secondary_distributor_anniversary,
                    sales_executive_name,
                    sales_executive_phone_number,
                    sales_executive_email,
                    sales_executive_birthday,
                    sales_executive_anniversary,
                    credit_limit,
                    latitude,
                    longitude,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    distributor_code or self._generate_unique_master_id("D"),
                    distributor_code or None,
                    canonical_firm_name,
                    firm_nick_name,
                    canonical_name,
                    phone_number,
                    location,
                    address,
                    pincode,
                    email,
                    gst_no,
                    zone,
                    region,
                    payment_terms,
                    birthday,
                    anniversary,
                    secondary_distributor_name,
                    secondary_distributor_phone_number,
                    secondary_distributor_birthday,
                    secondary_distributor_anniversary,
                    sales_executive_name,
                    sales_executive_phone_number,
                    sales_executive_email,
                    sales_executive_birthday,
                    sales_executive_anniversary,
                    credit_limit,
                    latitude,
                    longitude,
                    status,
                    created_at,
                ),
            )
            connection.commit()
            self._refresh_global_search_index(connection)
            return int(cursor.lastrowid)
        finally:
            if should_close:
                connection.close()

    def get_master_distributor_by_name(self, name: str) -> dict[str, Any] | None:
        canonical_name = self._canonicalize_known_master_name(name)
        lookup_values = [self._normalize_text(canonical_name)]
        original_value = self._normalize_text(name)
        if original_value and original_value not in lookup_values:
            lookup_values.append(original_value)

        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, zone, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at FROM master_distributors WHERE " + " OR ".join("LOWER(name) = ?" for _ in lookup_values) + " LIMIT 1"
            row = conn.execute(query, lookup_values).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "distributor_id": row[1],
            "distributor_code": row[2] or row[1],
            "firm_name": row[3],
            "firm_nick_name": row[4],
            "name": row[5],
            "phone_number": row[6],
            "location": row[7],
            "address": row[8],
            "pincode": row[9],
            "email": row[10],
            "gst_no": row[11],
            "zone": row[12],
            "region": row[13],
            "payment_terms": row[14],
            "birthday": row[15],
            "anniversary": row[16],
            "secondary_distributor_name": row[17],
            "secondary_distributor_phone_number": row[18],
            "secondary_distributor_birthday": row[19],
            "secondary_distributor_anniversary": row[20],
            "sales_executive_name": row[21],
            "sales_executive_phone_number": row[22],
            "sales_executive_email": row[23],
            "sales_executive_birthday": row[24],
            "sales_executive_anniversary": row[25],
            "credit_limit": row[26],
            "latitude": row[27],
            "longitude": row[28],
            "status": row[29],
            "created_at": row[30],
        }

    def get_master_distributor(self, distributor_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, zone, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at FROM master_distributors WHERE id = ?",
                (distributor_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "distributor_id": row[1],
            "distributor_code": row[2] or row[1],
            "firm_name": row[3],
            "firm_nick_name": row[4],
            "name": row[5],
            "phone_number": row[6],
            "location": row[7],
            "address": row[8],
            "pincode": row[9],
            "email": row[10],
            "gst_no": row[11],
            "zone": row[12],
            "region": row[13],
            "payment_terms": row[14],
            "birthday": row[15],
            "anniversary": row[16],
            "secondary_distributor_name": row[17],
            "secondary_distributor_phone_number": row[18],
            "secondary_distributor_birthday": row[19],
            "secondary_distributor_anniversary": row[20],
            "sales_executive_name": row[21],
            "sales_executive_phone_number": row[22],
            "sales_executive_email": row[23],
            "sales_executive_birthday": row[24],
            "sales_executive_anniversary": row[25],
            "credit_limit": row[26],
            "latitude": row[27],
            "longitude": row[28],
            "status": row[29],
            "created_at": row[30],
        }

    def list_master_distributors(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    distributor_id,
                    distributor_code,
                    firm_name,
                    firm_nick_name,
                    name,
                    phone_number,
                    location,
                    address,
                    pincode,
                    email,
                    gst_no,
                    zone,
                    region,
                    payment_terms,
                    birthday,
                    anniversary,
                    secondary_distributor_name,
                    secondary_distributor_phone_number,
                    secondary_distributor_birthday,
                    secondary_distributor_anniversary,
                    sales_executive_name,
                    sales_executive_phone_number,
                    sales_executive_email,
                    sales_executive_birthday,
                    sales_executive_anniversary,
                    credit_limit,
                    status,
                    created_at
                FROM master_distributors
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [
            {
                "id": row[0],
                "distributor_id": row[1],
                "distributor_code": row[2] or row[1],
                "firm_name": row[3],
                "firm_nick_name": row[4],
                "name": row[5],
                "phone_number": row[6],
                "location": row[7],
                "address": row[8],
                "pincode": row[9],
                "email": row[10],
                "gst_no": row[11],
                "zone": row[12],
                "region": row[13],
                "payment_terms": row[14],
                "birthday": row[15],
                "anniversary": row[16],
                "secondary_distributor_name": row[17],
                "secondary_distributor_phone_number": row[18],
                "secondary_distributor_birthday": row[19],
                "secondary_distributor_anniversary": row[20],
                "sales_executive_name": row[21],
                "sales_executive_phone_number": row[22],
                "sales_executive_email": row[23],
                "sales_executive_birthday": row[24],
                "sales_executive_anniversary": row[25],
                "credit_limit": row[26],
                "status": row[27],
                "created_at": row[28],
            }
            for row in rows
        ]

    def get_master_retailer_by_name(self, name: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, retailer_id, retailer_code, name, distributor_id, location, latitude, longitude, status, created_at, phone_number, email, address, gst_no, secondary_retailer_name, secondary_retailer_phone_number, secondary_retailer_birthday, secondary_retailer_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, owner_name FROM master_retailers WHERE LOWER(name) = ? LIMIT 1",
                (str(name or "").strip().lower(),),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "retailer_id": row[1],
            "retailer_code": row[2],
            "name": row[3],
            "distributor_id": row[4],
            "location": row[5],
            "latitude": row[6],
            "longitude": row[7],
            "status": row[8],
            "created_at": row[9],
            "phone_number": row[10],
            "email": row[11],
            "address": row[12],
            "gst_no": row[13],
            "secondary_retailer_name": row[14],
            "secondary_retailer_phone_number": row[15],
            "secondary_retailer_birthday": row[16],
            "secondary_retailer_anniversary": row[17],
            "sales_executive_name": row[18],
            "sales_executive_phone_number": row[19],
            "sales_executive_email": row[20],
            "sales_executive_birthday": row[21],
            "sales_executive_anniversary": row[22],
            "owner_name": row[23],
        }

    def list_master_retailers(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, retailer_id, retailer_code, name, distributor_id, location, latitude, longitude, status, created_at, phone_number, email, address, gst_no, secondary_retailer_name, secondary_retailer_phone_number, secondary_retailer_birthday, secondary_retailer_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, owner_name
                FROM master_retailers
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [
            {
                "id": row[0],
                "retailer_id": row[1],
                "retailer_code": row[2],
                "name": row[3],
                "distributor_id": row[4],
                "location": row[5],
                "latitude": row[6],
                "longitude": row[7],
                "status": row[8],
                "created_at": row[9],
                "phone_number": row[10],
                "email": row[11],
                "address": row[12],
                "gst_no": row[13],
                "secondary_retailer_name": row[14],
                "secondary_retailer_phone_number": row[15],
                "secondary_retailer_birthday": row[16],
                "secondary_retailer_anniversary": row[17],
                "sales_executive_name": row[18],
                "sales_executive_phone_number": row[19],
                "sales_executive_email": row[20],
                "sales_executive_birthday": row[21],
                "sales_executive_anniversary": row[22],
                "owner_name": row[23],
            }
            for row in rows
        ]

    def add_master_retailer(
        self,
        name: str,
        distributor_id: int,
        location: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        status: str = "active",
        retailer_code: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
        address: str | None = None,
        gst_no: str | None = None,
        secondary_retailer_name: str | None = None,
        secondary_retailer_phone_number: str | None = None,
        secondary_retailer_birthday: str | None = None,
        secondary_retailer_anniversary: str | None = None,
        sales_executive_name: str | None = None,
        sales_executive_phone_number: str | None = None,
        sales_executive_email: str | None = None,
        sales_executive_birthday: str | None = None,
        sales_executive_anniversary: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        connection = conn or sqlite3.connect(self.db_path)
        should_close = conn is None
        try:
            existing_id = self._find_similar_master_entry(
                connection,
                "master_retailers",
                "name",
                name,
                extra_filters={"distributor_id": distributor_id},
            )
            if existing_id is not None:
                return existing_id

            cursor = connection.execute(
                """
                INSERT INTO master_retailers (
                    retailer_id,
                    retailer_code,
                    name,
                    distributor_id,
                    location,
                    latitude,
                    longitude,
                    status,
                    created_at,
                    phone_number,
                    email,
                    address,
                    gst_no,
                    secondary_retailer_name,
                    secondary_retailer_phone_number,
                    secondary_retailer_birthday,
                    secondary_retailer_anniversary,
                    sales_executive_name,
                    sales_executive_phone_number,
                    sales_executive_email,
                    sales_executive_birthday,
                    sales_executive_anniversary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._generate_unique_master_id("R"),
                    retailer_code or None,
                    name,
                    distributor_id,
                    location,
                    latitude,
                    longitude,
                    status,
                    created_at,
                    phone_number,
                    email,
                    address,
                    gst_no,
                    secondary_retailer_name,
                    secondary_retailer_phone_number,
                    secondary_retailer_birthday,
                    secondary_retailer_anniversary,
                    sales_executive_name,
                    sales_executive_phone_number,
                    sales_executive_email,
                    sales_executive_birthday,
                    sales_executive_anniversary,
                ),
            )
            connection.commit()
            self._refresh_global_search_index(connection)
            return int(cursor.lastrowid)
        finally:
            if should_close:
                connection.close()

    def get_master_retailer(self, retailer_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, retailer_id, retailer_code, name, distributor_id, location, latitude, longitude, status, created_at, phone_number, email, address, gst_no, secondary_retailer_name, secondary_retailer_phone_number, secondary_retailer_birthday, secondary_retailer_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, owner_name FROM master_retailers WHERE id = ?",
                (retailer_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "retailer_id": row[1],
            "retailer_code": row[2],
            "name": row[3],
            "distributor_id": row[4],
            "location": row[5],
            "latitude": row[6],
            "longitude": row[7],
            "status": row[8],
            "created_at": row[9],
            "phone_number": row[10],
            "email": row[11],
            "address": row[12],
            "gst_no": row[13],
            "secondary_retailer_name": row[14],
            "secondary_retailer_phone_number": row[15],
            "secondary_retailer_birthday": row[16],
            "secondary_retailer_anniversary": row[17],
            "sales_executive_name": row[18],
            "sales_executive_phone_number": row[19],
            "sales_executive_email": row[20],
            "sales_executive_birthday": row[21],
            "sales_executive_anniversary": row[22],
            "owner_name": row[23],
        }

    def bulk_upload_targets_achievements(self, path: str | Path) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        imported = 0
        with sqlite3.connect(self.db_path) as conn:
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    conn.execute(
                        """
                        INSERT INTO targets_achievements (year, month, distributor_id, zone, target_amount, achievement_amount, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(row["year"]),
                            row["month"],
                            int(row["distributor_id"]),
                            row.get("zone"),
                            float(row.get("target_amount", 0) or 0),
                            float(row.get("achievement_amount", 0) or 0),
                            created_at,
                        ),
                    )
                    imported += 1
            conn.commit()
        return imported

    def get_target_variance_summary(self, distributor_id: int | None = None, year: int | None = None, zone: str | None = None) -> dict[str, Any]:
        query = "SELECT year, month, distributor_id, zone, target_amount, achievement_amount FROM targets_achievements WHERE 1=1"
        params: list[Any] = []
        if distributor_id is not None:
            query += " AND distributor_id = ?"
            params.append(distributor_id)
        if year is not None:
            query += " AND year = ?"
            params.append(year)
        if zone is not None:
            query += " AND zone = ?"
            params.append(zone)
        query += " ORDER BY year, CASE month WHEN 'Jan' THEN 1 WHEN 'Feb' THEN 2 WHEN 'Mar' THEN 3 WHEN 'Apr' THEN 4 WHEN 'May' THEN 5 WHEN 'Jun' THEN 6 WHEN 'Jul' THEN 7 WHEN 'Aug' THEN 8 WHEN 'Sep' THEN 9 WHEN 'Oct' THEN 10 WHEN 'Nov' THEN 11 WHEN 'Dec' THEN 12 ELSE 13 END"

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()

        summary_rows: list[dict[str, Any]] = []
        total_target = 0.0
        total_achievement = 0.0
        for row in rows:
            target_amount = float(row[4])
            achievement_amount = float(row[5])
            variance_percentage = 0.0 if target_amount == 0 else ((achievement_amount - target_amount) / target_amount) * 100
            total_target += target_amount
            total_achievement += achievement_amount
            summary_rows.append(
                {
                    "year": row[0],
                    "month": row[1],
                    "distributor_id": row[2],
                    "zone": row[3],
                    "target_amount": target_amount,
                    "achievement_amount": achievement_amount,
                    "variance_percentage": round(variance_percentage, 2),
                }
            )

        overall_variance_percentage = 0.0 if total_target == 0 else round(((total_achievement / total_target) * 100) - 100, 2)
        return {
            "rows": summary_rows,
            "overall_variance_percentage": round(overall_variance_percentage, 2),
        }

    def record_primary_sales(self, payload: dict[str, Any]) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO primary_sales (distributor_id, invoice_no, invoice_date, quantity, amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["distributor_id"]),
                    payload.get("invoice_no"),
                    payload.get("invoice_date"),
                    float(payload.get("quantity", 0) or 0),
                    float(payload.get("amount", 0) or 0),
                    created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def bulk_upload_secondary_sales(self, path: str | Path) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        imported = 0
        with sqlite3.connect(self.db_path) as conn:
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    conn.execute(
                        """
                        INSERT INTO secondary_sales (distributor_id, retailer_id, invoice_no, sale_date, quantity, amount, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(row["distributor_id"]),
                            int(row["retailer_id"]),
                            row.get("invoice_no"),
                            row.get("sale_date"),
                            float(row.get("quantity", 0) or 0),
                            float(row.get("amount", 0) or 0),
                            created_at,
                        ),
                    )
                    imported += 1
            conn.commit()
        return imported

    def get_sales_flow_summary(self, distributor_id: int | None = None) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            primary_row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM primary_sales WHERE (? IS NULL OR distributor_id = ?)",
                (distributor_id, distributor_id),
            ).fetchone()
            secondary_row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM secondary_sales WHERE (? IS NULL OR distributor_id = ?)",
                (distributor_id, distributor_id),
            ).fetchone()
        primary_volume = float(primary_row[0]) if primary_row else 0.0
        secondary_volume = float(secondary_row[0]) if secondary_row else 0.0
        difference = primary_volume - secondary_volume
        variance_percentage = 0.0 if primary_volume == 0 else ((secondary_volume / primary_volume) * 100)
        return {
            "primary_volume": round(primary_volume, 2),
            "secondary_volume": round(secondary_volume, 2),
            "difference": round(difference, 2),
            "variance_percentage": round(variance_percentage, 2),
        }

    def get_dashboard_payload(self) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            distributors_count = conn.execute("SELECT COUNT(*) FROM master_distributors").fetchone()[0]
            retailers_count = conn.execute("SELECT COUNT(*) FROM master_retailers").fetchone()[0]
            targets_rows = conn.execute("SELECT COUNT(*) FROM targets_achievements").fetchone()[0]
            primary_total = conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM primary_sales").fetchone()[0]
            secondary_total = conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM secondary_sales").fetchone()[0]
        return {
            "masters": {
                "distributors": int(distributors_count),
                "retailers": int(retailers_count),
            },
            "targets": {
                "total_rows": int(targets_rows),
            },
            "sales": {
                "primary_total": float(primary_total),
                "secondary_total": float(secondary_total),
            },
        }

    def save_article(self, payload: dict[str, Any]) -> int:
        return self.article_service.save_article(payload)

    def list_articles_by_category(self) -> list[dict[str, Any]]:
        return self.article_service.list_articles_by_category()

    def sanitize_article_payload(self, payload: dict[str, Any], existing_categories: list[str] | None = None) -> dict[str, Any]:
        return self.article_service.sanitize_article_payload(payload, existing_categories)

    def upsert_business_rule(self, rule_key: str, rule_value: str, is_locked: bool = True) -> int:
        cleaned_key = (rule_key or "").strip().lower()
        cleaned_value = (rule_value or "").strip()
        if not cleaned_key:
            raise ValueError("rule_key is required")
        if not cleaned_value:
            raise ValueError("rule_value is required")

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO business_rules (rule_key, rule_value, is_locked, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rule_key) DO UPDATE SET
                    rule_value = excluded.rule_value,
                    is_locked = excluded.is_locked,
                    updated_at = excluded.updated_at
                """,
                (cleaned_key, cleaned_value, 1 if is_locked else 0, now),
            )
            row = conn.execute("SELECT id FROM business_rules WHERE rule_key = ?", (cleaned_key,)).fetchone()
            return int(row[0])

    def list_business_rules(self, locked_only: bool = True) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if locked_only:
                rows = conn.execute(
                    "SELECT id, rule_key, rule_value, is_locked, updated_at FROM business_rules WHERE is_locked = 1 ORDER BY rule_key"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, rule_key, rule_value, is_locked, updated_at FROM business_rules ORDER BY rule_key"
                ).fetchall()

        return [
            {
                "id": row[0],
                "rule_key": row[1],
                "rule_value": row[2],
                "is_locked": bool(row[3]),
                "updated_at": row[4],
            }
            for row in rows
        ]

    def export_table(self, table_name: str, columns: list[str] | None = None) -> str:
        with sqlite3.connect(self.db_path) as conn:
            if columns is None:
                cursor = conn.execute(f"SELECT * FROM {table_name}")
                headers = [description[0] for description in cursor.description]
            else:
                headers = columns
                cursor = conn.execute(f"SELECT {', '.join(columns)} FROM {table_name}")

        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        for row in cursor.fetchall():
            writer.writerow({key: value for key, value in zip(headers, row)})
        return output.getvalue()

    def export_master_distributors(self) -> str:
        return self.export_table(
            "master_distributors",
            [
                "id",
                "distributor_id",
                "firm_name",
                "firm_nick_name",
                "name",
                "phone_number",
                "location",
                "address",
                "pincode",
                "email",
                "gst_no",
                "zone",
                "region",
                "payment_terms",
                "birthday",
                "anniversary",
                "secondary_distributor_name",
                "secondary_distributor_phone_number",
                "secondary_distributor_birthday",
                "secondary_distributor_anniversary",
                "sales_executive_name",
                "sales_executive_phone_number",
                "sales_executive_email",
                "sales_executive_birthday",
                "sales_executive_anniversary",
                "credit_limit",
                "status",
                "created_at",
            ],
        )

    def export_master_distributors_excel(self) -> bytes:
        columns = [
            "id",
            "distributor_id",
            "firm_name",
            "firm_nick_name",
            "name",
            "phone_number",
            "location",
            "address",
            "pincode",
            "email",
            "gst_no",
            "zone",
            "region",
            "payment_terms",
            "birthday",
            "anniversary",
            "secondary_distributor_name",
            "secondary_distributor_phone_number",
            "secondary_distributor_birthday",
            "secondary_distributor_anniversary",
            "sales_executive_name",
            "sales_executive_phone_number",
            "sales_executive_email",
            "sales_executive_birthday",
            "sales_executive_anniversary",
            "credit_limit",
            "status",
            "created_at",
        ]
        rows = self._read_table_rows("master_distributors", columns)
        df = pd.DataFrame(rows, columns=columns)
        buffer = BytesIO()
        df.to_excel(buffer, index=False)
        return buffer.getvalue()

    def export_master_distributors_pdf(self) -> bytes:
        rows = self._read_table_rows(
            "master_distributors",
            [
                "id",
                "distributor_id",
                "firm_name",
                "firm_nick_name",
                "name",
                "phone_number",
                "location",
                "address",
                "pincode",
                "email",
                "gst_no",
                "zone",
                "region",
                "payment_terms",
                "birthday",
                "anniversary",
                "secondary_distributor_name",
                "secondary_distributor_phone_number",
                "secondary_distributor_birthday",
                "secondary_distributor_anniversary",
                "sales_executive_name",
                "sales_executive_phone_number",
                "sales_executive_email",
                "sales_executive_birthday",
                "sales_executive_anniversary",
                "credit_limit",
                "status",
                "created_at",
            ],
        )
        lines = ["Distributors Export", "================"]
        for row in rows:
            lines.append(" | ".join(str(value) for value in row))
        pdf_bytes = bytes(
            "%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R>>endobj\n2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1>>endobj\n3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n4 0 obj<< /Length 0 >>stream\nendstream\nendobj\n5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\nxref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000062 00000 n \n0000000119 00000 n \n0000000207 00000 n \n0000000300 00000 n \ntrailer<< /Size 6 /Root 1 0 R>>\nstartxref\n0\n%%EOF\n",
            "utf-8",
        )
        return pdf_bytes

    def _read_table_rows(self, table_name: str, columns: list[str]) -> list[list[Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table_name}").fetchall()
        return [list(row) for row in rows]

    def export_master_retailers(self) -> str:
        return self.export_table(
            "master_retailers",
            [
                "id",
                "retailer_id",
                "retailer_code",
                "name",
                "distributor_id",
                "location",
                "phone_number",
                "email",
                "address",
                "gst_no",
                "secondary_retailer_name",
                "secondary_retailer_phone_number",
                "secondary_retailer_birthday",
                "secondary_retailer_anniversary",
                "sales_executive_name",
                "sales_executive_phone_number",
                "sales_executive_email",
                "sales_executive_birthday",
                "sales_executive_anniversary",
                "status",
                "created_at",
            ],
        )

    def export_master_retailers_excel(self) -> bytes:
        columns = ["id", "retailer_id", "retailer_code", "name", "distributor_id", "location", "phone_number", "email", "address", "gst_no", "secondary_retailer_name", "secondary_retailer_phone_number", "secondary_retailer_birthday", "secondary_retailer_anniversary", "sales_executive_name", "sales_executive_phone_number", "sales_executive_email", "sales_executive_birthday", "sales_executive_anniversary", "status", "created_at"]
        rows = self._read_table_rows("master_retailers", columns)
        df = pd.DataFrame(rows, columns=columns)
        buffer = BytesIO()
        df.to_excel(buffer, index=False)
        return buffer.getvalue()

    def export_targets_achievements(self) -> str:
        return self.export_table(
            "targets_achievements",
            ["id", "year", "month", "distributor_id", "zone", "target_amount", "achievement_amount", "created_at"],
        )

    def export_primary_sales(self) -> str:
        return self.export_table(
            "primary_sales",
            ["id", "distributor_id", "invoice_no", "invoice_date", "quantity", "amount", "created_at"],
        )

    def export_secondary_sales(self) -> str:
        return self.export_table(
            "secondary_sales",
            ["id", "distributor_id", "retailer_id", "invoice_no", "sale_date", "quantity", "amount", "created_at"],
        )
