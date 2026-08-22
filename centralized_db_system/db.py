import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from order_item_keys import item_keys_match, line_brands_match, size_code_only_item_key
from urllib.parse import urlparse
from uuid import uuid4

import openpyxl

import pandas as pd
from rapidfuzz import fuzz
from werkzeug.security import check_password_hash, generate_password_hash

from .firebase_sync import FirebaseSync
from .sync import OfflineSyncStore
from .article_master import ArticleMasterService
from .order_reconciliation import normalize_product_code


class CentralizedDB:
    # ============ DYNAMIC SCHEMA MANAGER ============

    def init_schema_manager(self):
        """Schema manager table banao agar nahi hai"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
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
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    UNIQUE(workspace_id, entity_type, field_name)
                )
            """
            )
            conn.commit()

    def ensure_storage_tables(self) -> None:
        """Create storage account and file index tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_accounts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    workspace_id TEXT DEFAULT 'default',
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
                )
            """
            )
            # Older DBs created storage_accounts without the UNIQUE constraint
            # (CREATE TABLE IF NOT EXISTS never upgrades). Add columns + unique
            # index so ON CONFLICT(user_id, workspace_id, provider_type) works.
            existing_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(storage_accounts)").fetchall()
            }
            for col, ddl in (
                ("workspace_id", "TEXT DEFAULT 'default'"),
                ("provider_type", "TEXT DEFAULT 'google_drive'"),
                ("sync_status", "TEXT"),
                ("total_storage_bytes", "INTEGER DEFAULT 0"),
                ("used_storage_bytes", "INTEGER DEFAULT 0"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ):
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE storage_accounts ADD COLUMN {col} {ddl}")
            if "provider" in existing_cols:
                conn.execute(
                    """
                    UPDATE storage_accounts
                    SET provider_type = COALESCE(NULLIF(provider_type, ''), provider, 'google_drive')
                    WHERE provider_type IS NULL OR provider_type = ''
                    """
                )
            else:
                conn.execute(
                    """
                    UPDATE storage_accounts
                    SET provider_type = 'google_drive'
                    WHERE provider_type IS NULL OR provider_type = ''
                    """
                )
            conn.execute(
                """
                UPDATE storage_accounts
                SET workspace_id = 'default'
                WHERE workspace_id IS NULL OR workspace_id = ''
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_storage_accounts_user_ws_provider
                ON storage_accounts(user_id, workspace_id, provider_type)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_index (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT DEFAULT 'default',
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
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_versions (
                    id INTEGER PRIMARY KEY,
                    file_index_id INTEGER,
                    version_number INTEGER,
                    version_file_id TEXT,
                    created_at TIMESTAMP,
                    modified_at TIMESTAMP,
                    created_by INTEGER,
                    FOREIGN KEY(file_index_id) REFERENCES file_index(id)
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_operations_log (
                    id INTEGER PRIMARY KEY,
                    file_index_id INTEGER,
                    operation_type TEXT,
                    user_id INTEGER,
                    operation_status TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP,
                    FOREIGN KEY(file_index_id) REFERENCES file_index(id)
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_index_workspace ON file_index(workspace_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_index_owner ON file_index(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_index_module ON file_index(module)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_index_company ON file_index(company)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_index_created ON file_index(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_storage_accounts_user ON storage_accounts(user_id)"
            )
            # Older file_index tables lack UNIQUE(storage_account_id, file_id) and
            # updated_at; CREATE TABLE IF NOT EXISTS never upgrades them.
            file_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(file_index)").fetchall()
            }
            if file_cols:
                for col, ddl in (
                    ("updated_at", "TEXT"),
                    ("mime_type", "TEXT"),
                    ("folder_path", "TEXT"),
                    ("file_size_bytes", "INTEGER"),
                    ("indexed_at", "TEXT"),
                    ("last_synced", "TEXT"),
                    ("sync_status", "TEXT"),
                    ("storage_account_id", "INTEGER"),
                    ("workspace_id", "TEXT DEFAULT 'default'"),
                ):
                    if col not in file_cols:
                        conn.execute(f"ALTER TABLE file_index ADD COLUMN {col} {ddl}")
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_file_index_account_file
                    ON file_index(storage_account_id, file_id)
                    """
                )
            conn.commit()

    def save_storage_account(
        self,
        user_id: int,
        workspace_id: str,
        provider_type: str,
        oauth_token: dict[str, Any],
        sync_status: str = "connected",
        total_storage_bytes: int | None = None,
        used_storage_bytes: int | None = None,
    ) -> int:
        """Insert or update a storage account record."""
        self.ensure_storage_tables()
        now = datetime.now(timezone.utc).isoformat()
        token_text = json.dumps(oauth_token)
        workspace_id = str(workspace_id or "default")
        provider_type = str(provider_type or "google_drive")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                    INSERT INTO storage_accounts (
                        user_id, workspace_id, provider_type, oauth_token, connected_at,
                        last_sync, sync_status, total_storage_bytes,
                        used_storage_bytes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, workspace_id, provider_type) DO UPDATE SET
                        oauth_token = excluded.oauth_token,
                        last_sync = excluded.last_sync,
                        sync_status = excluded.sync_status,
                        total_storage_bytes = excluded.total_storage_bytes,
                        used_storage_bytes = excluded.used_storage_bytes,
                        updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    workspace_id,
                    provider_type,
                    token_text,
                    now,
                    now,
                    sync_status,
                    total_storage_bytes,
                    used_storage_bytes,
                    now,
                    now,
                ),
            )
            conn.commit()
            if cursor.lastrowid:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT id FROM storage_accounts WHERE user_id = ? AND workspace_id = ? AND provider_type = ?",
                (user_id, workspace_id, provider_type),
            ).fetchone()
            return int(row[0]) if row else 0

    def get_storage_account(
        self,
        user_id: int,
        provider_type: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a storage account for a user."""
        self.ensure_storage_tables()
        query = "SELECT * FROM storage_accounts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if provider_type:
            query += " AND provider_type = ?"
            params.append(provider_type)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(query, tuple(params)).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["oauth_token"] = (
                json.loads(data["oauth_token"]) if data.get("oauth_token") else None
            )
            return data

    def disconnect_storage_account(
        self,
        user_id: int,
        provider_type: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        """Mark a storage account as disconnected."""
        self.ensure_storage_tables()
        query = "UPDATE storage_accounts SET sync_status = ?, oauth_token = NULL, updated_at = ? WHERE user_id = ?"
        params: list[Any] = [
            "disconnected",
            datetime.now(timezone.utc).isoformat(),
            user_id,
        ]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if provider_type:
            query += " AND provider_type = ?"
            params.append(provider_type)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    def upsert_file_index_records(
        self,
        workspace_id: str,
        storage_account_id: int,
        items: list[dict[str, Any]],
        user_id: int | None = None,
    ) -> int:
        """Insert or update file index metadata from synced storage items."""
        self.ensure_storage_tables()
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(file_index)").fetchall()}
            owner_id = user_id
            if owner_id is None:
                row = conn.execute(
                    "SELECT user_id FROM storage_accounts WHERE id = ?",
                    (storage_account_id,),
                ).fetchone()
                owner_id = int(row[0]) if row else 0
            if "sync_status" in cols:
                conn.execute(
                    """
                    UPDATE file_index
                    SET sync_status = 'stale'
                    WHERE storage_account_id = ?
                    """,
                    (storage_account_id,),
                )
            for item in items:
                file_id = item.get("id")
                if not file_id:
                    continue
                name = item.get("name") or "untitled"
                mime = item.get("mimeType")
                folder = (
                    ",".join(item.get("parents", [])) if item.get("parents") else None
                )
                size_val = int(item.get("size")) if item.get("size") else None
                modified = item.get("modifiedTime")
                # Prefer upsert when unique index exists; otherwise delete+insert.
                if "updated_at" in cols:
                    conn.execute(
                        """
                            INSERT INTO file_index (
                                user_id, workspace_id, storage_account_id, file_id, file_name,
                                file_type, mime_type, folder_path, file_size_bytes, file_size,
                                modified_at, indexed_at, last_synced, sync_status,
                                created_at, updated_at, owner_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(storage_account_id, file_id) DO UPDATE SET
                                file_name = excluded.file_name,
                                file_type = excluded.file_type,
                                mime_type = excluded.mime_type,
                                folder_path = excluded.folder_path,
                                file_size_bytes = excluded.file_size_bytes,
                                file_size = excluded.file_size,
                                modified_at = excluded.modified_at,
                                indexed_at = excluded.indexed_at,
                                last_synced = excluded.last_synced,
                                sync_status = excluded.sync_status,
                                updated_at = excluded.updated_at
                        """,
                        (
                            owner_id,
                            workspace_id,
                            storage_account_id,
                            file_id,
                            name,
                            item.get("fileType") or mime,
                            mime,
                            folder,
                            size_val,
                            size_val,
                            modified,
                            now,
                            now,
                            "synced",
                            now,
                            now,
                            owner_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        DELETE FROM file_index
                        WHERE storage_account_id = ? AND file_id = ?
                        """,
                        (storage_account_id, file_id),
                    )
                    conn.execute(
                        """
                            INSERT INTO file_index (
                                user_id, workspace_id, storage_account_id, file_id, file_name,
                                file_type, mime_type, folder_path, file_size_bytes, file_size,
                                modified_at, indexed_at, last_synced, sync_status,
                                created_at, owner_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner_id,
                            workspace_id,
                            storage_account_id,
                            file_id,
                            name,
                            item.get("fileType") or mime,
                            mime,
                            folder,
                            size_val,
                            size_val,
                            modified,
                            now,
                            now,
                            "synced",
                            now,
                            owner_id,
                        ),
                    )
                count += 1
            if "sync_status" in cols:
                conn.execute(
                    """
                    DELETE FROM file_index
                    WHERE storage_account_id = ? AND sync_status = 'stale'
                    """,
                    (storage_account_id,),
                )
            conn.execute(
                """
                UPDATE storage_accounts
                SET last_sync = ?, sync_status = 'connected', updated_at = ?
                WHERE id = ?
                """,
                (now, now, storage_account_id),
            )
            conn.commit()
        return count

    def search_file_index(
        self,
        user_id: int,
        workspace_id: str | None,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search indexed files for a specific user."""
        self.ensure_storage_tables()
        filters = filters or {}
        sql = "SELECT fi.* FROM file_index AS fi JOIN storage_accounts AS sa ON fi.storage_account_id = sa.id WHERE sa.user_id = ?"
        params: list[Any] = [user_id]
        if workspace_id:
            sql += " AND sa.workspace_id = ?"
            params.append(workspace_id)
        if query:
            qparam = f"%{query}%"
            sql += " AND (fi.file_name LIKE ? OR fi.file_type LIKE ? OR fi.search_tags LIKE ? OR fi.folder_path LIKE ?)"
            params.extend([qparam, qparam, qparam, qparam])
        for field in ("file_type", "company", "module", "folder_path"):
            if field in filters and filters[field]:
                sql += f" AND fi.{field} = ?"
                params.append(filters[field])
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]

    def get_storage_account_summary(
        self, user_id: int, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return connected storage account summary for user."""
        account = self.get_storage_account(user_id, workspace_id=workspace_id)
        if not account:
            return None
        return {
            "id": account["id"],
            "provider_type": account["provider_type"],
            "connected_at": account["connected_at"],
            "last_sync": account["last_sync"],
            "sync_status": account["sync_status"],
            "total_storage_bytes": account["total_storage_bytes"],
            "used_storage_bytes": account["used_storage_bytes"],
        }

    def ensure_target_achievement_tables(self) -> None:
        """Create Target vs Achievement tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_years (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT DEFAULT 'default',
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
                    user_id INTEGER,
                    UNIQUE(workspace_id, financial_year, user_id)
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fy_year ON target_achievement_years(financial_year)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_uploads (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT DEFAULT 'default',
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
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fy_id ON target_achievement_uploads(financial_year_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_breakup (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT DEFAULT 'default',
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
                )
            """
            )
            breakup_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(target_achievement_breakup)").fetchall()
            }
            if "attribute_type" in breakup_cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fy_breakup ON target_achievement_breakup(financial_year_id, attribute_type)"
                )
            elif "distributor_name" in breakup_cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fy_breakup_dist ON target_achievement_breakup(financial_year_id, distributor_name)"
                )
            self._migrate_legacy_breakup_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_category_breakup (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT DEFAULT 'default',
                    financial_year_id INTEGER NOT NULL,
                    distributor_name TEXT NOT NULL,
                    nick TEXT,
                    category TEXT NOT NULL,
                    achievement_lakhs REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(financial_year_id, distributor_name, category),
                    FOREIGN KEY(financial_year_id) REFERENCES target_achievement_years(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fy_cat_breakup "
                "ON target_achievement_category_breakup(financial_year_id, distributor_name)"
            )
            self._migrate_category_breakup_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_monthly (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    year_month TEXT NOT NULL,
                    distributor_name TEXT NOT NULL,
                    nick TEXT,
                    amount_lakhs REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, year_month, distributor_name)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ta_monthly_ym "
                "ON target_achievement_monthly(workspace_id, year_month)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_others_lines (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    financial_year_id INTEGER NOT NULL,
                    line_name TEXT NOT NULL,
                    amount_lakhs REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, financial_year_id, line_name),
                    FOREIGN KEY(financial_year_id) REFERENCES target_achievement_years(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ta_others_lines_fy "
                "ON target_others_lines(workspace_id, financial_year_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_achievement_channel_prefs (
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    user_id INTEGER NOT NULL,
                    financial_year_id INTEGER NOT NULL,
                    use_manual INTEGER NOT NULL DEFAULT 0,
                    use_so INTEGER NOT NULL DEFAULT 0,
                    use_ci INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workspace_id, user_id, financial_year_id)
                )
                """
            )
            # Migrate older user-only prefs table (no financial_year_id) if present.
            prefs_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(target_achievement_channel_prefs)"
                ).fetchall()
            }
            if "financial_year_id" not in prefs_cols:
                conn.execute("ALTER TABLE target_achievement_channel_prefs RENAME TO target_achievement_channel_prefs_legacy")
                conn.execute(
                    """
                    CREATE TABLE target_achievement_channel_prefs (
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        user_id INTEGER NOT NULL,
                        financial_year_id INTEGER NOT NULL,
                        use_manual INTEGER NOT NULL DEFAULT 0,
                        use_so INTEGER NOT NULL DEFAULT 0,
                        use_ci INTEGER NOT NULL DEFAULT 1,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (workspace_id, user_id, financial_year_id)
                    )
                    """
                )
                # Legacy rows had no FY — leave them unused; defaults apply per FY.
                conn.execute("DROP TABLE IF EXISTS target_achievement_channel_prefs_legacy")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_manual_category_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    user_id INTEGER NOT NULL,
                    category_name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    builtin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(workspace_id, user_id, category_name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_manual_category_amounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    user_id INTEGER NOT NULL,
                    financial_year_id INTEGER NOT NULL,
                    distributor_name TEXT NOT NULL,
                    category_name TEXT NOT NULL,
                    amount_lakhs REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(workspace_id, user_id, financial_year_id, distributor_name, category_name)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ta_manual_cat_amt_fy "
                "ON target_manual_category_amounts(workspace_id, user_id, financial_year_id)"
            )
            self._ensure_column_exists(
                conn,
                "target_manual_category_catalog",
                "hidden",
                "INTEGER NOT NULL DEFAULT 0",
            )
            conn.commit()

    def get_achievement_channel_prefs(
        self,
        workspace_id: str,
        user_id: int | None,
        financial_year_id: int | None = None,
    ) -> dict[str, bool]:
        """Which achievement channels count toward the active total for one FY.

        Defaults to CI-only (historical behaviour) when unset.
        """
        if user_id is None or financial_year_id is None:
            return {"manual": False, "so": False, "ci": True}
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT use_manual, use_so, use_ci
                FROM target_achievement_channel_prefs
                WHERE workspace_id = ? AND user_id = ? AND financial_year_id = ?
                """,
                (workspace_id, int(user_id), int(financial_year_id)),
            ).fetchone()
        if not row:
            return {"manual": False, "so": False, "ci": True}
        return {
            "manual": bool(int(row[0] or 0)),
            "so": bool(int(row[1] or 0)),
            "ci": bool(int(row[2] or 0)),
        }

    def set_achievement_channel_prefs(
        self,
        workspace_id: str,
        user_id: int,
        financial_year_id: int,
        use_manual: bool,
        use_so: bool,
        use_ci: bool,
    ) -> dict[str, bool]:
        """Persist per-FY channel toggles. SO + CI together is rejected by the route."""
        self.ensure_target_achievement_tables()
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO target_achievement_channel_prefs
                    (workspace_id, user_id, financial_year_id, use_manual, use_so, use_ci, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, user_id, financial_year_id) DO UPDATE SET
                    use_manual = excluded.use_manual,
                    use_so = excluded.use_so,
                    use_ci = excluded.use_ci,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    int(user_id),
                    int(financial_year_id),
                    1 if use_manual else 0,
                    1 if use_so else 0,
                    1 if use_ci else 0,
                    now,
                ),
            )
            conn.commit()
        return {
            "manual": bool(use_manual),
            "so": bool(use_so),
            "ci": bool(use_ci),
            "financial_year_id": int(financial_year_id),
        }

    def _migrate_category_breakup_schema(self, conn: sqlite3.Connection) -> None:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "target_achievement_category_breakup" not in tables:
            return
        for column_name, column_type in (
            ("distributor_id", "INTEGER"),
            ("source_distributor_name", "TEXT"),
        ):
            self._ensure_column_exists(
                conn, "target_achievement_category_breakup", column_name, column_type
            )
        self._invalidate_table_columns_cache("target_achievement_category_breakup")

    TA_MANUAL_CATEGORY_DEFAULTS = ("Bed", "Bath", "TOB", "Pillow")

    def ensure_manual_category_catalog(
        self, workspace_id: str, user_id: int | None
    ) -> list[dict[str, Any]]:
        """Seed Bed/Bath/TOB/Pillow once per user. Catalog is year-independent."""
        self.ensure_target_achievement_tables()
        if user_id is None:
            return []
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for i, name in enumerate(self.TA_MANUAL_CATEGORY_DEFAULTS):
                conn.execute(
                    """
                    INSERT INTO target_manual_category_catalog (
                        workspace_id, user_id, category_name, sort_order, builtin, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(workspace_id, user_id, category_name) DO NOTHING
                    """,
                    (workspace_id, int(user_id), name, i, now),
                )
            conn.commit()
            rows = conn.execute(
                """
                SELECT category_name, sort_order, builtin
                FROM target_manual_category_catalog
                WHERE workspace_id = ? AND user_id = ?
                  AND COALESCE(hidden, 0) = 0
                ORDER BY builtin DESC, sort_order ASC, LOWER(category_name) ASC
                """,
                (workspace_id, int(user_id)),
            ).fetchall()
        return [
            {
                "name": r["category_name"],
                "sort_order": int(r["sort_order"] or 0),
                "builtin": bool(int(r["builtin"] or 0)),
            }
            for r in rows
        ]

    def add_manual_category(
        self, workspace_id: str, user_id: int, category_name: str
    ) -> dict[str, Any]:
        """Add a custom category that shows on every FY. Does not change amounts."""
        self.ensure_target_achievement_tables()
        name = " ".join((category_name or "").split()).strip()
        if not name:
            raise ValueError("category name required")
        if len(name) > 40:
            raise ValueError("category name too long")
        catalog = self.ensure_manual_category_catalog(workspace_id, user_id)
        existing = next(
            (c for c in catalog if c["name"].lower() == name.lower()),
            None,
        )
        if existing:
            return existing
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            hidden_row = conn.execute(
                """
                SELECT category_name, sort_order, builtin
                FROM target_manual_category_catalog
                WHERE workspace_id = ? AND user_id = ?
                  AND LOWER(category_name) = LOWER(?)
                  AND COALESCE(hidden, 0) = 1
                """,
                (workspace_id, int(user_id), name),
            ).fetchone()
            if hidden_row:
                conn.execute(
                    """
                    UPDATE target_manual_category_catalog
                    SET hidden = 0
                    WHERE workspace_id = ? AND user_id = ?
                      AND LOWER(category_name) = LOWER(?)
                    """,
                    (workspace_id, int(user_id), name),
                )
                conn.commit()
                return {
                    "name": hidden_row["category_name"],
                    "sort_order": int(hidden_row["sort_order"] or 0),
                    "builtin": bool(int(hidden_row["builtin"] or 0)),
                }
            custom_count = sum(1 for c in catalog if not c["builtin"])
            conn.execute(
                """
                INSERT INTO target_manual_category_catalog (
                    workspace_id, user_id, category_name, sort_order, builtin, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (workspace_id, int(user_id), name, 100 + custom_count, now),
            )
            conn.commit()
        return {"name": name, "sort_order": 100 + custom_count, "builtin": False}

    def remove_manual_category(
        self, workspace_id: str, user_id: int, category_name: str
    ) -> bool:
        """Hide a category from the user's catalog (builtin or custom). Clears saved amounts."""
        self.ensure_target_achievement_tables()
        name = " ".join((category_name or "").split()).strip()
        if not name:
            raise ValueError("category name required")
        self.ensure_manual_category_catalog(workspace_id, user_id)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE target_manual_category_catalog
                SET hidden = 1
                WHERE workspace_id = ? AND user_id = ?
                  AND LOWER(category_name) = LOWER(?)
                  AND COALESCE(hidden, 0) = 0
                """,
                (workspace_id, int(user_id), name),
            )
            if cur.rowcount <= 0:
                return False
            conn.execute(
                """
                DELETE FROM target_manual_category_amounts
                WHERE workspace_id = ? AND user_id = ?
                  AND LOWER(category_name) = LOWER(?)
                """,
                (workspace_id, int(user_id), name),
            )
            conn.commit()
        return True

    def list_manual_category_amounts(
        self,
        workspace_id: str,
        user_id: int | None,
        financial_year_id: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """FY amounts keyed by lowercase distributor name. Catalog names always included later."""
        self.ensure_target_achievement_tables()
        if user_id is None:
            return {}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT distributor_name, category_name, amount_lakhs
                FROM target_manual_category_amounts
                WHERE workspace_id = ? AND user_id = ? AND financial_year_id = ?
                """,
                (workspace_id, int(user_id), int(financial_year_id)),
            ).fetchall()
        by_dist: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            key = (r["distributor_name"] or "").strip().lower()
            if not key:
                continue
            lakhs = float(r["amount_lakhs"] or 0)
            by_dist.setdefault(key, []).append(
                {
                    "name": r["category_name"],
                    "amount_lakhs": lakhs,
                    "amount_rupees": round(lakhs * 100_000.0, 2),
                }
            )
        return by_dist

    def replace_distributor_manual_categories(
        self,
        *,
        workspace_id: str,
        user_id: int,
        financial_year_id: int,
        distributor_name: str,
        categories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace one distributor's manual category amounts for one FY.

        Empty list clears the split (typed Ach total can still exist).
        New names are added to the year-independent catalog.
        """
        self.ensure_target_achievement_tables()
        dist = (distributor_name or "").strip()
        if not dist:
            raise ValueError("distributor_name required")
        self.ensure_manual_category_catalog(workspace_id, user_id)
        cleaned: list[tuple[str, float]] = []
        seen: set[str] = set()
        for raw in categories or []:
            name = " ".join(
                str(raw.get("name") or raw.get("category") or "").split()
            ).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            if raw.get("amount_rupees") is not None:
                lakhs = float(raw.get("amount_rupees") or 0) / 100_000.0
            else:
                lakhs = float(
                    raw.get("amount_lakhs") or raw.get("amount") or 0
                )
            if lakhs < 0:
                lakhs = 0.0
            if lakhs <= 0.0000005:
                continue
            self.add_manual_category(workspace_id, user_id, name)
            cleaned.append((name, round(lakhs, 6)))
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM target_manual_category_amounts
                WHERE workspace_id = ? AND user_id = ? AND financial_year_id = ?
                  AND LOWER(distributor_name) = LOWER(?)
                """,
                (workspace_id, int(user_id), int(financial_year_id), dist),
            )
            for name, lakhs in cleaned:
                conn.execute(
                    """
                    INSERT INTO target_manual_category_amounts (
                        workspace_id, user_id, financial_year_id, distributor_name,
                        category_name, amount_lakhs, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        int(user_id),
                        int(financial_year_id),
                        dist,
                        name,
                        lakhs,
                        now,
                    ),
                )
            conn.commit()
        return [
            {
                "name": n,
                "amount_lakhs": a,
                "amount_rupees": round(a * 100_000.0, 2),
            }
            for n, a in cleaned
        ]

    def attach_manual_categories_to_breakup(
        self,
        workspace_id: str,
        user_id: int | None,
        financial_year_id: int,
        breakup: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        amounts = self.list_manual_category_amounts(
            workspace_id, user_id, financial_year_id
        )
        for row in breakup:
            key = (row.get("distributor_name") or "").strip().lower()
            row["manual_categories"] = list(amounts.get(key) or [])
        return breakup

    def _breakup_table_columns(self, conn: sqlite3.Connection) -> set[str]:
        return {row[1] for row in conn.execute("PRAGMA table_info(target_achievement_breakup)").fetchall()}

    def _invalidate_table_columns_cache(self, *table_names: str) -> None:
        for name in table_names:
            self._table_columns.pop(name, None)

    def _migrate_legacy_breakup_schema(self, conn: sqlite3.Connection) -> None:
        """Add multi-source achievement columns for legacy init_db breakup tables."""
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "target_achievement_breakup" not in tables:
            return
        for column_name, column_type in (
            ("target_lakhs", "REAL DEFAULT 0"),
            ("achievement_excel", "REAL DEFAULT 0"),
            ("achievement_ci", "REAL DEFAULT 0"),
            ("achievement_manual", "REAL DEFAULT 0"),
            ("nick", "TEXT"),
            ("distributor_id", "INTEGER"),
            ("source_distributor_name", "TEXT"),
        ):
            self._ensure_column_exists(conn, "target_achievement_breakup", column_name, column_type)
        self._ensure_column_exists(
            conn, "target_achievement_years", "achievement_manual_fy", "REAL DEFAULT 0"
        )
        self._migrate_target_years_user_isolation(conn)
        breakup_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(target_achievement_breakup)").fetchall()
        }
        if "achievement" in breakup_cols and "achievement_excel" in breakup_cols:
            conn.execute(
                """
                UPDATE target_achievement_breakup
                SET achievement_excel = achievement
                WHERE COALESCE(achievement_excel, 0) = 0 AND COALESCE(achievement, 0) != 0
                """
            )
        # Copy the legacy single achievement_amount into the matching split
        # column so CI/SO sync cannot later clobber typed manual figures.
        if (
            "achievement_amount" in breakup_cols
            and "source" in breakup_cols
            and "achievement_manual" in breakup_cols
        ):
            conn.execute(
                """
                UPDATE target_achievement_breakup
                SET achievement_manual = achievement_amount
                WHERE COALESCE(achievement_manual, 0) = 0
                  AND LOWER(COALESCE(source, '')) = 'manual'
                  AND COALESCE(achievement_amount, 0) != 0
                """
            )
            conn.execute(
                """
                UPDATE target_achievement_breakup
                SET achievement_ci = achievement_amount
                WHERE COALESCE(achievement_ci, 0) = 0
                  AND LOWER(COALESCE(source, '')) = 'ci'
                  AND COALESCE(achievement_amount, 0) != 0
                """
            )
            conn.execute(
                """
                UPDATE target_achievement_breakup
                SET achievement_excel = achievement_amount
                WHERE COALESCE(achievement_excel, 0) = 0
                  AND LOWER(COALESCE(source, '')) IN ('excel_upload', 'upload', 'excel')
                  AND COALESCE(achievement_amount, 0) != 0
                """
            )
        self._invalidate_table_columns_cache(
            "target_achievement_breakup", "target_achievement_years"
        )

    def _migrate_target_years_user_isolation(self, conn: sqlite3.Connection) -> None:
        """Add user_id and replace workspace-only UNIQUE so peers can share FY labels."""
        self._ensure_column_exists(conn, "target_achievement_years", "user_id", "INTEGER")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_target_years_ws_user_fy'"
        ).fetchone():
            return

        table_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='target_achievement_years'"
        ).fetchone()
        table_sql = (table_sql_row[0] or "") if table_sql_row else ""
        compact = table_sql.replace(" ", "").replace("\n", "")
        has_old_unique = (
            "UNIQUE(workspace_id,financial_year)" in compact
            and "UNIQUE(workspace_id,financial_year,user_id)" not in compact
        )

        if has_old_unique:
            conn.execute(
                "ALTER TABLE target_achievement_years "
                "RENAME TO target_achievement_years__pre_user"
            )
            old_info = conn.execute(
                "PRAGMA table_info(target_achievement_years__pre_user)"
            ).fetchall()
            def_parts: list[str] = []
            select_names: list[str] = []
            for _cid, name, ctype, notnull, dflt, pk in old_info:
                select_names.append(name)
                if name == "user_id":
                    continue
                piece = f'"{name}" {ctype or "TEXT"}'
                if pk:
                    piece += " PRIMARY KEY"
                elif notnull and dflt is None:
                    piece += " NOT NULL"
                if dflt is not None:
                    piece += f" DEFAULT {dflt}"
                def_parts.append(piece)
            if "user_id" not in {r[1] for r in old_info}:
                def_parts.append("user_id INTEGER")
            else:
                def_parts.append("user_id INTEGER")
            conn.execute(
                f"CREATE TABLE target_achievement_years ({', '.join(def_parts)})"
            )
            quoted = ", ".join(f'"{n}"' for n in select_names)
            if "user_id" in select_names:
                conn.execute(
                    f"INSERT INTO target_achievement_years ({quoted}) "
                    f"SELECT {quoted} FROM target_achievement_years__pre_user"
                )
            else:
                conn.execute(
                    f"INSERT INTO target_achievement_years ({quoted}, user_id) "
                    f"SELECT {quoted}, NULL FROM target_achievement_years__pre_user"
                )
            conn.execute("DROP TABLE target_achievement_years__pre_user")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_target_years_ws_user_fy "
            "ON target_achievement_years(workspace_id, IFNULL(user_id, -1), financial_year)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_target_years_user "
            "ON target_achievement_years(workspace_id, user_id)"
        )

    def merge_duplicate_fiscal_years(
        self, workspace_id: str, user_id: int | None = None
    ) -> int:
        """Collapse duplicate FY rows (e.g. 2025-26 vs 2025-2026). Returns rows removed."""
        from app.fiscal_year import normalize_fiscal_year

        self.ensure_target_achievement_tables()
        removed = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            year_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            year_sql = "SELECT * FROM target_achievement_years WHERE workspace_id = ?"
            year_params: list[Any] = [workspace_id]
            if user_id is not None and "user_id" in year_cols:
                year_sql += " AND user_id = ?"
                year_params.append(user_id)
            year_sql += " ORDER BY id"
            rows = conn.execute(year_sql, tuple(year_params)).fetchall()
            groups: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                raw = row["financial_year"] if "financial_year" in row.keys() else None
                if raw is None and "year" in row.keys():
                    raw = row["year"]
                label = normalize_fiscal_year(raw) or (raw or "")
                if not label:
                    continue
                groups.setdefault(label, []).append(row)

            def child_count(fy_id: int) -> int:
                total = 0
                for table in (
                    "target_achievement_breakup",
                    "target_achievement_category_breakup",
                    "target_achievement_uploads",
                ):
                    try:
                        r = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE financial_year_id = ?",
                            (fy_id,),
                        ).fetchone()
                        total += int(r[0] or 0)
                    except sqlite3.OperationalError:
                        pass
                return total

            def year_rank(row: sqlite3.Row) -> tuple:
                fy_id = int(row["id"])
                raw_year = ""
                if "financial_year" in row.keys() and row["financial_year"]:
                    raw_year = str(row["financial_year"])
                elif "year" in row.keys() and row["year"]:
                    raw_year = str(row["year"])
                canonical = normalize_fiscal_year(raw_year) == raw_year
                target_val = 0.0
                if "target" in row.keys() and row["target"] is not None:
                    target_val = float(row["target"])
                elif "target_amount" in row.keys() and row["target_amount"] is not None:
                    target_val = float(row["target_amount"])
                return (child_count(fy_id), target_val, 1 if canonical else 0, -fy_id)

            breakup_cols = self._breakup_table_columns(conn)
            now = datetime.now(timezone.utc).isoformat()

            for label, group in groups.items():
                group.sort(key=year_rank, reverse=True)
                winner = group[0]
                winner_id = int(winner["id"])
                sets = []
                params: list[Any] = []
                if "year" in year_cols:
                    sets.append("year = ?")
                    params.append(label)
                if "financial_year" in year_cols:
                    sets.append("financial_year = ?")
                    params.append(label)
                if sets:
                    params.append(winner_id)
                    conn.execute(
                        f"UPDATE target_achievement_years SET {', '.join(sets)} WHERE id = ?",
                        tuple(params),
                    )

                for loser in group[1:]:
                    loser_id = int(loser["id"])
                    if "target_achievement_breakup" in {
                        r[0]
                        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }:
                        if "attribute_type" in breakup_cols and "attribute_name" in breakup_cols:
                            for brow in conn.execute(
                                "SELECT * FROM target_achievement_breakup WHERE financial_year_id = ?",
                                (loser_id,),
                            ).fetchall():
                                exists = conn.execute(
                                    """
                                    SELECT id FROM target_achievement_breakup
                                    WHERE financial_year_id = ? AND attribute_type = ? AND attribute_name = ?
                                    """,
                                    (winner_id, brow["attribute_type"], brow["attribute_name"]),
                                ).fetchone()
                                if exists:
                                    conn.execute(
                                        "DELETE FROM target_achievement_breakup WHERE id = ?",
                                        (brow["id"],),
                                    )
                                else:
                                    conn.execute(
                                        "UPDATE target_achievement_breakup SET financial_year_id = ? WHERE id = ?",
                                        (winner_id, brow["id"]),
                                    )
                        elif "distributor_name" in breakup_cols:
                            for brow in conn.execute(
                                "SELECT * FROM target_achievement_breakup WHERE financial_year_id = ?",
                                (loser_id,),
                            ).fetchall():
                                dist = brow["distributor_name"]
                                exists = conn.execute(
                                    """
                                    SELECT id FROM target_achievement_breakup
                                    WHERE financial_year_id = ? AND distributor_name = ?
                                    """,
                                    (winner_id, dist),
                                ).fetchone()
                                if exists:
                                    conn.execute(
                                        "DELETE FROM target_achievement_breakup WHERE id = ?",
                                        (brow["id"],),
                                    )
                                else:
                                    conn.execute(
                                        "UPDATE target_achievement_breakup SET financial_year_id = ? WHERE id = ?",
                                        (winner_id, brow["id"]),
                                    )
                                    if "year_id" in breakup_cols:
                                        conn.execute(
                                            "UPDATE target_achievement_breakup SET year_id = ? WHERE id = ?",
                                            (winner_id, brow["id"]),
                                        )
                        else:
                            conn.execute(
                                "UPDATE target_achievement_breakup SET financial_year_id = ? WHERE financial_year_id = ?",
                                (winner_id, loser_id),
                            )

                    for table in ("target_achievement_category_breakup", "target_achievement_uploads"):
                        try:
                            conn.execute(
                                f"UPDATE {table} SET financial_year_id = ? WHERE financial_year_id = ?",
                                (winner_id, loser_id),
                            )
                        except sqlite3.OperationalError:
                            pass

                    conn.execute(
                        "DELETE FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                        (loser_id, workspace_id),
                    )
                    removed += 1

            conn.commit()
        return removed

    def _breakup_source_column(self, source: str) -> str:
        return {
            "excel_upload": "achievement_excel",
            "upload": "achievement_excel",
            "excel": "achievement_excel",
            "manual": "achievement_manual",
            "ci": "achievement_ci",
        }.get((source or "").lower(), "achievement_excel")

    def _derived_breakup_source(self, excel: float, ci: float, manual: float) -> str:
        flags: list[str] = []
        if float(manual or 0) > 0:
            flags.append("manual")
        if float(excel or 0) > 0:
            flags.append("excel_upload")
        if float(ci or 0) > 0:
            flags.append("ci")
        if len(flags) == 1:
            return flags[0]
        return "mixed"

    def _split_achievement_values(
        self,
        *,
        excel: float = 0.0,
        ci: float = 0.0,
        manual: float = 0.0,
        amount: float = 0.0,
        source: str = "",
    ) -> tuple[float, float, float, float]:
        """Return (excel, ci, manual, total). Fall back to legacy single amount+source."""
        excel = float(excel or 0)
        ci = float(ci or 0)
        manual = float(manual or 0)
        amount = float(amount or 0)
        if excel == 0 and ci == 0 and manual == 0 and amount != 0:
            src = (source or "").lower()
            if src == "manual":
                manual = amount
            elif src == "ci":
                ci = amount
            else:
                excel = amount
        total = excel + ci + manual
        if total == 0:
            total = amount
        return excel, ci, manual, total

    def create_financial_year(
        self,
        financial_year: str,
        target_amount: float | None,
        achievement_amount: float | None,
        remarks: str | None,
        created_by: str | None,
    ) -> tuple[bool, int, str | None]:
        """Create new FY record."""
        self.ensure_target_achievement_tables()
        achievement_percent = 0.0
        if target_amount is not None and target_amount != 0:
            achievement_percent = round(
                (achievement_amount or 0) / float(target_amount) * 100, 2
            )

        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                        INSERT INTO target_achievement_years (
                            financial_year, target_amount, achievement_amount,
                            achievement_percent, target_source, achievement_source,
                            remarks, created_at, updated_at, created_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        financial_year,
                        target_amount,
                        achievement_amount,
                        achievement_percent,
                        "Manual",
                        "Manual",
                        remarks,
                        now,
                        now,
                        created_by,
                    ),
                )
                conn.commit()
                return True, int(cursor.lastrowid), None
        except sqlite3.IntegrityError as exc:
            return False, 0, str(exc)

    def get_financial_year(self, fy_id: int) -> dict[str, Any] | None:
        """Get single FY by ID."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE id = ?", (fy_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_financial_years(
        self, workspace_id: str = "default"
    ) -> list[dict[str, Any]]:
        """Get all FYs, sorted by year DESC."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM target_achievement_years WHERE workspace_id = ? ORDER BY financial_year DESC",
                (workspace_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_financial_year(
        self,
        fy_id: int,
        target_amount: float | None = None,
        achievement_amount: float | None = None,
        remarks: str | None = None,
    ) -> bool:
        """Update FY record."""
        self.ensure_target_achievement_tables()
        existing = self.get_financial_year(fy_id)
        if not existing:
            return False

        target_amount = (
            target_amount
            if target_amount is not None
            else existing.get("target_amount")
        )
        achievement_amount = (
            achievement_amount
            if achievement_amount is not None
            else existing.get("achievement_amount")
        )
        achievement_percent = 0.0
        if target_amount is not None and float(target_amount) != 0:
            achievement_percent = round(
                (float(achievement_amount or 0) / float(target_amount)) * 100, 2
            )

        updated_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                    UPDATE target_achievement_years SET
                        target_amount = ?,
                        achievement_amount = ?,
                        achievement_percent = ?,
                        remarks = ?,
                        updated_at = ?
                    WHERE id = ?
                """,
                (
                    target_amount,
                    achievement_amount,
                    achievement_percent,
                    remarks if remarks is not None else existing.get("remarks"),
                    updated_at,
                    fy_id,
                ),
            )
            conn.commit()
            return True

    def delete_financial_year_for_workspace(self, workspace_id: str, fy_id: int) -> bool:
        """Workspace-scoped FY delete (cascade breakup / category / uploads)."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (fy_id, workspace_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM target_achievement_breakup WHERE financial_year_id = ?",
                (fy_id,),
            )
            conn.execute(
                "DELETE FROM target_achievement_category_breakup WHERE financial_year_id = ?",
                (fy_id,),
            )
            conn.execute(
                "DELETE FROM target_achievement_uploads WHERE financial_year_id = ?",
                (fy_id,),
            )
            try:
                conn.execute(
                    "DELETE FROM target_manual_category_amounts WHERE financial_year_id = ?",
                    (fy_id,),
                )
            except sqlite3.OperationalError:
                pass
            cursor = conn.execute(
                "DELETE FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (fy_id, workspace_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_target_distributor_target(
        self,
        *,
        workspace_id: str,
        financial_year_id: int,
        distributor_name: str,
    ) -> dict[str, Any]:
        """Remove one distributor target row (Others → reset to 0). Returns fy total lakhs."""
        self.ensure_target_achievement_tables()
        name = (distributor_name or "").strip()
        if not name:
            raise ValueError("distributor_name required")
        is_others = name.lower() == "others"
        self._invalidate_table_columns_cache("target_achievement_breakup")
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            params: list[Any] = [financial_year_id]
            ws_clause = ""
            if "workspace_id" in cols:
                ws_clause = " AND workspace_id = ?"
                params.append(workspace_id)

            if is_others:
                # Keep Others row but zero the target.
                sets: list[str] = []
                if "target_lakhs" in cols:
                    sets.append("target_lakhs = 0")
                if "target_amount" in cols:
                    sets.append("target_amount = 0")
                if sets:
                    where = f"financial_year_id = ?{ws_clause}"
                    name_col = "distributor_name" if "distributor_name" in cols else "attribute_name"
                    where += f" AND LOWER({name_col}) = 'others'"
                    if "attribute_type" in cols:
                        where += " AND attribute_type = 'distributor'"
                    conn.execute(
                        f"UPDATE target_achievement_breakup SET {', '.join(sets)} WHERE {where}",
                        tuple(params),
                    )
            else:
                name_col = "distributor_name" if "distributor_name" in cols else "attribute_name"
                del_sql = (
                    f"DELETE FROM target_achievement_breakup WHERE financial_year_id = ?{ws_clause} "
                    f"AND LOWER({name_col}) = LOWER(?)"
                )
                del_params = list(params) + [name]
                if "attribute_type" in cols:
                    del_sql += " AND attribute_type = 'distributor'"
                conn.execute(del_sql, tuple(del_params))
            conn.commit()

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    DELETE FROM target_manual_category_amounts
                    WHERE workspace_id = ? AND financial_year_id = ?
                      AND LOWER(distributor_name) = LOWER(?)
                    """,
                    (workspace_id, financial_year_id, name),
                )
                conn.commit()
        except sqlite3.OperationalError:
            pass

        fy_total = self.sync_financial_year_target_from_breakup(workspace_id, financial_year_id)
        return {"distributor_name": name, "fy_target_lakhs": fy_total, "is_others": is_others}

    def list_monthly_distributor_entries(
        self, workspace_id: str, year_month: str
    ) -> list[dict[str, Any]]:
        """List monthly distributor amounts for YYYY-MM."""
        self.ensure_target_achievement_tables()
        ym = (year_month or "").strip()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT distributor_name, nick, amount_lakhs, updated_at
                FROM target_achievement_monthly
                WHERE workspace_id = ? AND year_month = ?
                ORDER BY LOWER(distributor_name)
                """,
                (workspace_id, ym),
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_monthly_distributor_entry(
        self,
        *,
        workspace_id: str,
        year_month: str,
        distributor_name: str,
        amount_lakhs: float,
        nick: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one monthly distributor amount (lakhs). Zero amount deletes the row."""
        self.ensure_target_achievement_tables()
        ym = (year_month or "").strip()
        name = (distributor_name or "").strip()
        if not ym or not name:
            raise ValueError("year_month and distributor_name required")
        amount = float(amount_lakhs or 0)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            if amount <= 0:
                conn.execute(
                    """
                    DELETE FROM target_achievement_monthly
                    WHERE workspace_id = ? AND year_month = ? AND LOWER(distributor_name) = LOWER(?)
                    """,
                    (workspace_id, ym, name),
                )
                conn.commit()
                return {
                    "year_month": ym,
                    "distributor_name": name,
                    "amount_lakhs": 0.0,
                    "deleted": True,
                }
            conn.execute(
                """
                INSERT INTO target_achievement_monthly (
                    workspace_id, year_month, distributor_name, nick, amount_lakhs, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, year_month, distributor_name) DO UPDATE SET
                    nick = COALESCE(excluded.nick, target_achievement_monthly.nick),
                    amount_lakhs = excluded.amount_lakhs,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, ym, name, (nick or "").strip() or None, amount, now),
            )
            conn.commit()
        return {
            "year_month": ym,
            "distributor_name": name,
            "nick": nick,
            "amount_lakhs": amount,
            "deleted": False,
        }

    def save_upload_record(
        self,
        fy_id: int,
        file_name: str,
        file_type: str,
        total_rows: int,
        calculated_total: float,
        uploaded_by: str | None,
    ) -> int:
        """Save file upload metadata."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                    INSERT INTO target_achievement_uploads (
                        financial_year_id, file_name, file_type, uploaded_by,
                        total_rows, calculated_total, upload_status, parsed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fy_id,
                    file_name,
                    file_type,
                    uploaded_by,
                    total_rows,
                    calculated_total,
                    "success",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_upload_records(self, fy_id: int) -> list[dict[str, Any]]:
        """List upload records for a financial year."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM target_achievement_uploads WHERE financial_year_id = ? ORDER BY uploaded_at DESC",
                (fy_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_breakup_record(
        fy_id: int,
        attribute_type: str,
        attribute_name: str,
        target_amount: float | None,
        achievement_amount: float | None,
        source: str,
    ) -> int:
        """Save/update breakup record."""
        self.ensure_target_achievement_tables()
        target_amount = float(target_amount or 0)
        achievement_amount = float(achievement_amount or 0)
        achievement_percent = 0.0
        if target_amount != 0:
            achievement_percent = round((achievement_amount / target_amount) * 100, 2)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                    INSERT INTO target_achievement_breakup (
                        financial_year_id, attribute_type, attribute_name,
                        target_amount, achievement_amount, achievement_percent, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(financial_year_id, attribute_type, attribute_name) DO UPDATE SET
                        target_amount = excluded.target_amount,
                        achievement_amount = excluded.achievement_amount,
                        achievement_percent = excluded.achievement_percent,
                        source = excluded.source,
                        created_at = CURRENT_TIMESTAMP
                """,
                (
                    fy_id,
                    attribute_type,
                    attribute_name,
                    target_amount,
                    achievement_amount,
                    achievement_percent,
                    source,
                ),
            )
            conn.commit()
            return int(
                cursor.lastrowid
                if cursor.lastrowid
                else conn.execute(
                    "SELECT id FROM target_achievement_breakup WHERE financial_year_id = ? AND attribute_type = ? AND attribute_name = ?",
                    (fy_id, attribute_type, attribute_name),
                ).fetchone()[0]
            )

    def get_breakup(self, fy_id: int, attribute_type: str) -> list[dict[str, Any]]:
        """Get breakup records for attribute type."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                    SELECT * FROM target_achievement_breakup
                    WHERE financial_year_id = ? AND attribute_type = ?
                    ORDER BY achievement_percent DESC
                """,
                (fy_id, attribute_type),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_target_achievement_summary(self) -> dict[str, Any]:
        """Get overall metrics across all FYs."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) AS total_count, SUM(target_amount) AS overall_target, SUM(achievement_amount) AS overall_achievement FROM target_achievement_years"
            )
            summary = cursor.fetchone()
            overall_target = summary[1] or 0.0
            overall_achievement = summary[2] or 0.0
            overall_percent = 0.0
            if overall_target != 0:
                overall_percent = round((overall_achievement / overall_target) * 100, 2)
            return {
                "total_count": summary[0] or 0,
                "overall_target": overall_target,
                "overall_achievement": overall_achievement,
                "overall_percent": overall_percent,
            }

    def get_storage_summary(self) -> dict[str, Any]:
        return self.get_target_achievement_summary()

    def get_schema_fields(self, entity_type: str) -> list:
        """Entity ke fields lao order ke saath"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM custom_schema_fields
                WHERE entity_type = ? AND is_visible = 1
                ORDER BY field_order ASC
            """,
                (entity_type,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_schema_fields(self, entity_type: str, workspace_id: str | None = None) -> list:
        """Saare fields (hidden bhi)"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if workspace_id:
                rows = conn.execute(
                    """
                    SELECT * FROM custom_schema_fields
                    WHERE entity_type = ? AND workspace_id = ?
                    ORDER BY field_order ASC
                """,
                    (entity_type, workspace_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM custom_schema_fields
                    WHERE entity_type = ?
                    ORDER BY field_order ASC
                """,
                    (entity_type,),
                ).fetchall()
            return [dict(r) for r in rows]

    def add_schema_field(
        self,
        entity_type: str,
        field_name: str,
        field_label: str,
        field_type: str = "text",
        field_order: int = 0,
        is_required: int = 0,
        options: str = None,
        workspace_id: str = "default",
    ) -> int:
        """Naya field add karo"""
        self.init_schema_manager()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO custom_schema_fields
                (entity_type, field_name, field_label, field_type, field_order, is_required, options, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entity_type,
                    field_name,
                    field_label,
                    field_type,
                    field_order,
                    is_required,
                    options,
                    workspace_id,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_schema_field(self, field_id: int, workspace_id: str | None = None) -> bool:
        """Field delete karo — sirf apni workspace ka field, agar workspace_id diya gaya ho"""
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                conn.execute(
                    "DELETE FROM custom_schema_fields WHERE id = ? AND workspace_id = ?",
                    (field_id, workspace_id),
                )
            else:
                conn.execute("DELETE FROM custom_schema_fields WHERE id = ?", (field_id,))
            conn.commit()
            return True

    def toggle_schema_field_visibility(
        self, field_id: int, is_visible: int, workspace_id: str | None = None
    ) -> bool:
        """Field show/hide karo — sirf apni workspace ka field"""
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                conn.execute(
                    "UPDATE custom_schema_fields SET is_visible = ? WHERE id = ? AND workspace_id = ?",
                    (is_visible, field_id, workspace_id),
                )
            else:
                conn.execute(
                    "UPDATE custom_schema_fields SET is_visible = ? WHERE id = ?",
                    (is_visible, field_id),
                )
            conn.commit()
            return True

    def reorder_schema_fields(
        self, field_orders: list[dict], workspace_id: str | None = None
    ) -> bool:
        """Fields reorder karo — [{id: 1, order: 0}, {id: 2, order: 1}] — sirf apni workspace ke fields"""
        with sqlite3.connect(self.db_path) as conn:
            for item in field_orders:
                if workspace_id:
                    conn.execute(
                        "UPDATE custom_schema_fields SET field_order = ? WHERE id = ? AND workspace_id = ?",
                        (item["order"], item["id"], workspace_id),
                    )
                else:
                    conn.execute(
                        "UPDATE custom_schema_fields SET field_order = ? WHERE id = ?",
                        (item["order"], item["id"]),
                    )
            conn.commit()
            return True

    def seed_default_schema(self, workspace_id: str = "default"):
        """Pehli baar default fields seed karo"""
        self.init_schema_manager()
        defaults = {
            "distributor": [
                ("distributor_code", "Distributor Code", "text", 0),
                ("firm_name", "Firm Name", "text", 1),
                ("firm_nick_name", "Nick Name", "text", 2),
                ("name", "Contact Person", "text", 3),
                ("contact_person_role", "Contact Person Role", "text", 4),
                ("phone_number", "Mobile Number", "text", 5),
                ("email", "Email", "text", 6),
                ("zone", "State", "text", 7),
                ("region", "Area", "text", 8),
                ("gst_no", "GST Number", "text", 9),
                ("payment_terms", "Payment Terms", "text", 10),
                ("credit_limit", "Credit Limit", "number", 11),
            ],
            "retailer": [
                ("retailer_code", "Retailer Code", "text", 0),
                ("name", "Retailer Name", "text", 1),
                ("owner_name", "Owner Name", "text", 2),
                ("distributor_id", "Distributor", "select", 3),
                ("location", "Location", "text", 4),
                ("phone_number", "Phone Number", "text", 5),
                ("email", "Email", "text", 6),
                ("address", "Address", "text", 7),
                ("gst_no", "GST Number", "text", 8),
            ],
            "article": [
                ("brand", "Brand", "text", 0),
                ("tc", "TC", "text", 1),
                ("size", "Size", "text", 2),
                ("bs_size", "BS Size", "text", 3),
                ("product", "Product", "text", 4),
                ("print_style", "Print Style", "text", 5),
                ("mrp", "MRP (₹)", "number", 6),
                ("selling_price", "Selling Price (₹)", "number", 7),
                ("ptr", "PTR (₹)", "number", 8),
                ("exmill_price", "Ex-Mill (₹)", "number", 9),
            ],
        }
        with sqlite3.connect(self.db_path) as conn:
            for entity, fields in defaults.items():
                for field_name, label, ftype, order in fields:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO custom_schema_fields
                        (entity_type, field_name, field_label, field_type, field_order, workspace_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (entity, field_name, label, ftype, order, workspace_id),
                    )
            conn.commit()

    def __init__(
        self, db_path: str | None = None, sync_store: OfflineSyncStore | None = None
    ):
        self.db_path = self._resolve_db_path(db_path)
        self._table_columns: dict[str, set[str]] = {}
        self.sync_store = sync_store or OfflineSyncStore()
        # Lazy — constructing FirebaseSync on every CentralizedDB() flooded Render logs.
        self._firebase_sync: FirebaseSync | None = None
        self.article_service = ArticleMasterService(str(self.db_path))
        self._initialize()

    @property
    def firebase_sync(self) -> FirebaseSync:
        if self._firebase_sync is None:
            self._firebase_sync = FirebaseSync(sync_store=self.sync_store)
        return self._firebase_sync

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
                if (
                    path_value.startswith("/")
                    and len(path_value) >= 3
                    and path_value[2] == ":"
                ):
                    path_value = path_value[1:]
                return Path(path_value).expanduser()

            if parsed.scheme in {"file", ""}:
                return Path(parsed.path or value).expanduser()

            return Path(value).expanduser()

        return Path("centralized_db.sqlite3").expanduser()

    def _table_has_column(self, table_name: str, column_name: str) -> bool:
        if table_name not in self._table_columns:
            with sqlite3.connect(self.db_path) as conn:
                self._table_columns[table_name] = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                }
        return column_name in self._table_columns[table_name]

    def _workspace_clause(self, table_name: str, workspace_id: str | None) -> tuple[str, list[Any]]:
        if workspace_id and self._table_has_column(table_name, "workspace_id"):
            return " WHERE workspace_id = ?", [workspace_id]
        return "", []

    def _log_audit_event(
        self,
        conn: sqlite3.Connection,
        action: str,
        table_name: str,
        record_id: int | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
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
            self._log_audit_event(
                conn,
                "backup",
                "database",
                details={"source": str(self.db_path), "destination": str(target_path)},
            )
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
            self._log_audit_event(
                conn,
                "restore",
                "database",
                details={"source": str(source_path), "destination": str(target_path)},
            )
            conn.commit()
        return target_path

    def cleanup_temp_uploads(
        self, directory: str | Path, max_age_hours: int = 24
    ) -> int:
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
            self._log_audit_event(
                conn,
                "cleanup",
                "temp_uploads",
                details={
                    "directory": str(folder),
                    "removed": removed,
                    "max_age_hours": max_age_hours,
                },
            )
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

    # ---------- Login identity: one email = one account (strict) ----------
    @staticmethod
    def normalize_login_key(value: str | None) -> str | None:
        v = (value or "").strip()
        return v.lower() if v else None

    @staticmethod
    def is_email_shaped(value: str | None) -> bool:
        v = (value or "").strip()
        if "@" not in v:
            return False
        local, _, domain = v.partition("@")
        return bool(local.strip()) and "." in domain

    def ensure_login_identity_indexes(self) -> None:
        """DB guardrail: unique non-empty email (case-insensitive)."""
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "email" not in cols:
                return
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower
                ON users(lower(trim(email)))
                WHERE email IS NOT NULL AND trim(email) != ''
                """
            )
            conn.commit()

    def find_login_identity_owner_ids(
        self,
        conn: sqlite3.Connection,
        *,
        username: str | None = None,
        email: str | None = None,
        exclude_user_id: int | None = None,
    ) -> list[int]:
        """User ids that already own any of the given login keys (username or email)."""
        keys: set[str] = set()
        for raw in (username, email):
            key = self.normalize_login_key(raw)
            if key:
                keys.add(key)
        if not keys:
            return []

        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        has_email = "email" in cols
        owner_ids: set[int] = set()
        for key in keys:
            if has_email:
                rows = conn.execute(
                    """
                    SELECT id FROM users
                    WHERE lower(trim(username)) = ?
                       OR (
                            email IS NOT NULL
                            AND trim(email) != ''
                            AND lower(trim(email)) = ?
                       )
                    """,
                    (key, key),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM users WHERE lower(trim(username)) = ?",
                    (key,),
                ).fetchall()
            for row in rows:
                uid = int(row[0])
                if exclude_user_id is None or uid != int(exclude_user_id):
                    owner_ids.add(uid)
        return sorted(owner_ids)

    def assert_login_identity_available(
        self,
        conn: sqlite3.Connection,
        *,
        username: str,
        email: str | None = None,
        exclude_user_id: int | None = None,
    ) -> tuple[str, str | None]:
        """
        Enforce one email = one login across username and email columns.
        Returns (clean_username, clean_email_or_none).
        """
        clean_user = (username or "").strip()
        if not clean_user:
            raise ValueError("User Id is required")

        clean_email: str | None
        if email is None:
            clean_email = clean_user if self.is_email_shaped(clean_user) else None
        else:
            clean_email = email.strip() or None

        if self.is_email_shaped(clean_user):
            if clean_email and self.normalize_login_key(clean_email) != self.normalize_login_key(
                clean_user
            ):
                raise ValueError(
                    "When User Id is an email, profile email must match that email"
                )
            clean_email = clean_user

        conflicts = self.find_login_identity_owner_ids(
            conn,
            username=clean_user,
            email=clean_email,
            exclude_user_id=exclude_user_id,
        )
        if conflicts:
            raise ValueError(
                "This email or User Id is already registered to another login. "
                "One email = one account."
            )
        return clean_user, clean_email

    def dedupe_email_login_accounts(
        self,
        *,
        prefer_username: str | None = None,
    ) -> dict[str, Any]:
        """
        Remove legacy duplicate login rows that share the same email.
        Keeps workspace owner, then prefer_username, then lowest user id.
        """
        self.ensure_user_profile_columns()
        prefer = (prefer_username or os.getenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")).strip()
        actions: list[dict[str, Any]] = []

        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            has_owner = "is_workspace_owner" in cols
            dup_groups = conn.execute(
                """
                SELECT lower(trim(email)) AS email_key, GROUP_CONCAT(id) AS ids
                FROM users
                WHERE email IS NOT NULL AND trim(email) != ''
                GROUP BY email_key
                HAVING COUNT(*) > 1
                """
            ).fetchall()

            for email_key, ids_csv in dup_groups:
                ids = [int(x) for x in (ids_csv or "").split(",") if x.strip()]
                if len(ids) < 2:
                    continue
                placeholders = ",".join("?" for _ in ids)
                owner_sql = (
                    "SELECT id, username, IFNULL(is_workspace_owner, 0) "
                    if has_owner
                    else "SELECT id, username, 0 "
                )
                rows = conn.execute(
                    f"""
                    {owner_sql}
                    FROM users
                    WHERE id IN ({placeholders})
                    ORDER BY is_workspace_owner DESC,
                             CASE WHEN lower(username) = lower(?) THEN 0 ELSE 1 END,
                             id ASC
                    """,
                    (*ids, prefer),
                ).fetchall()
                keeper_id = int(rows[0][0])
                for dup_id, dup_username, _ in rows[1:]:
                    uid = int(dup_id)
                    conn.execute(
                        "DELETE FROM user_ui_preferences WHERE user_id = ?",
                        (uid,),
                    )
                    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
                    actions.append(
                        {
                            "action": "deleted_duplicate",
                            "email": email_key,
                            "kept_user_id": keeper_id,
                            "removed_user_id": uid,
                            "removed_username": dup_username,
                        }
                    )

            conn.commit()
        purge = self.delete_archived_duplicate_logins()
        if purge.get("deleted"):
            actions.extend(
                {"action": "deleted_archived_duplicate", **item} for item in purge["deleted"]
            )
        return {"action": "deduped", "changes": actions}

    def delete_archived_duplicate_logins(self) -> dict[str, Any]:
        """Hard-delete inactive archived_dup_* login shells left from email dedupe."""
        self.ensure_user_profile_columns()
        deleted: list[dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            owner_clause = (
                "AND IFNULL(is_workspace_owner, 0) = 0"
                if "is_workspace_owner" in cols
                else ""
            )
            rows = conn.execute(
                f"""
                SELECT id, username FROM users
                WHERE lower(username) LIKE 'archived_dup_%'
                  AND IFNULL(status, 'active') = 'inactive'
                  {owner_clause}
                """
            ).fetchall()
            for uid, uname in rows:
                conn.execute(
                    "DELETE FROM user_ui_preferences WHERE user_id = ?",
                    (int(uid),),
                )
                conn.execute("DELETE FROM users WHERE id = ?", (int(uid),))
                deleted.append({"user_id": int(uid), "username": uname})
            conn.commit()
        return {"action": "deleted_archived_duplicates", "deleted": deleted}

    def resolve_user_login_row(
        self,
        conn: sqlite3.Connection,
        login: str,
        select_columns: list[str],
    ) -> sqlite3.Row | None:
        """Resolve login id — prefer username match, then email."""
        needle = (login or "").strip()
        if not needle:
            return None
        key = self.normalize_login_key(needle)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        col_sql = ", ".join(select_columns)
        if "email" in cols:
            rows = conn.execute(
                f"""
                SELECT {col_sql} FROM users
                WHERE username = ?
                   OR lower(trim(username)) = ?
                   OR (
                        email IS NOT NULL
                        AND trim(email) != ''
                        AND lower(trim(email)) = ?
                   )
                """,
                (needle, key, key),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {col_sql} FROM users
                WHERE username = ? OR lower(trim(username)) = ?
                """,
                (needle, key),
            ).fetchall()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        for row in rows:
            if row["username"] == needle:
                return row
        for row in rows:
            if self.normalize_login_key(row["username"]) == key:
                return row
        return rows[0]

    def create_user(
        self,
        username: str,
        password: str,
        role: str = 'unassigned',
        workspace_id: str = 'default',
        email: str | None = None,
    ) -> dict[str, Any]:
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("username and password are required")

        from app.workspace_tenancy import resolve_workspace_id_for_new_user

        # Explicit non-default workspace (seed scripts) is kept.
        # default / empty + executive-style role → private silo per login.
        explicit = (workspace_id or "").strip()
        if explicit and explicit != "default":
            resolved_workspace = explicit
        else:
            resolved_workspace = resolve_workspace_id_for_new_user(
                username,
                role,
                None,
            )

        self.ensure_user_profile_columns()
        self.ensure_login_identity_indexes()

        with sqlite3.connect(self.db_path) as conn:
            clean_user, clean_email = self.assert_login_identity_available(
                conn, username=username, email=email
            )

            password_hash = generate_password_hash(password)
            created_at = datetime.now(timezone.utc).isoformat()
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "email" in cols and clean_email:
                cursor = conn.execute(
                    """
                    INSERT INTO users
                    (username, password_hash, created_at, role, workspace_id, status, email)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_user,
                        password_hash,
                        created_at,
                        role,
                        resolved_workspace,
                        "active",
                        clean_email,
                    ),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, created_at, role, workspace_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (clean_user, password_hash, created_at, role, resolved_workspace, "active"),
                )
            conn.commit()
            return {
                "id": cursor.lastrowid,
                "username": clean_user,
                "email": clean_email,
                "created_at": created_at,
                "workspace_id": resolved_workspace,
                "role": role,
            }

    _DEFAULT_UI_THEME = "emerald"
    _KNOWN_UI_THEMES = frozenset({
        "bright", "emerald", "custom",
        "royal_navy", "burgundy_antique", "black_soft_gold", "deep_teal_brass",
        "chocolate_gold", "plum_rose", "olive_brass", "midnight_copper",
        # Android app's "glass" theme catalog (Theme.kt) — a separate client
        # sharing this same per-user storage. Keep both clients' names here.
        "ruby_glass", "tangerine_glass", "citrine_glass", "lime_glass",
        "emerald_glass", "turquoise_glass", "sapphire_glass", "indigo_glass",
        "amethyst_glass", "rose_glass", "snow_glass",
    })

    @classmethod
    def _normalize_ui_theme(cls, theme_id: str | None) -> str:
        """Normalize unknown UI themes to default."""
        t = (theme_id or cls._DEFAULT_UI_THEME).strip() or cls._DEFAULT_UI_THEME
        if t not in cls._KNOWN_UI_THEMES:
            return cls._DEFAULT_UI_THEME
        return t

    def get_user_ui_theme(self, user_id: int | None) -> dict[str, Any]:
        """Return per-login UI theme. Empty theme means caller should use default."""
        if user_id is None:
            return {"theme": self._DEFAULT_UI_THEME, "custom_colors": None}
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_ui_preferences (
                    user_id INTEGER PRIMARY KEY,
                    theme_id TEXT NOT NULL DEFAULT 'emerald',
                    custom_colors_json TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            row = conn.execute(
                "SELECT theme_id, custom_colors_json FROM user_ui_preferences WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        if not row:
            return {"theme": self._DEFAULT_UI_THEME, "custom_colors": None, "saved": False}
        colors = None
        if row[1]:
            try:
                colors = json.loads(row[1])
            except Exception:
                colors = None
        theme = self._normalize_ui_theme(row[0])
        return {"theme": theme, "custom_colors": colors, "saved": True}

    def set_user_ui_theme(
        self,
        user_id: int,
        theme_id: str,
        custom_colors: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw = (theme_id or self._DEFAULT_UI_THEME).strip() or self._DEFAULT_UI_THEME
        if raw not in self._KNOWN_UI_THEMES:
            raise ValueError("Unknown theme")
        theme = raw
        colors_json = None
        if isinstance(custom_colors, dict):
            colors_json = json.dumps(custom_colors)
        updated_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_ui_preferences (
                    user_id INTEGER PRIMARY KEY,
                    theme_id TEXT NOT NULL DEFAULT 'emerald',
                    custom_colors_json TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO user_ui_preferences (user_id, theme_id, custom_colors_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    theme_id = excluded.theme_id,
                    custom_colors_json = excluded.custom_colors_json,
                    updated_at = excluded.updated_at
                """,
                (int(user_id), theme, colors_json, updated_at),
            )
        return self.get_user_ui_theme(user_id)

    def authenticate_user(self, username: str, password: str) -> bool:
        username = (username or "").strip()
        if not username or not password:
            return False

        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = self.resolve_user_login_row(
                conn, username, ["id", "username", "password_hash"]
            )
            if not row:
                return False
            return check_password_hash(row["password_hash"], password)

    _RECOVERY_PIN_MAX_ATTEMPTS = 5
    _RECOVERY_PIN_LOCKOUT_MINUTES = 15

    def set_recovery_pin(self, user_id: int, pin: str) -> None:
        """Self-service: a logged-in user sets/changes their own recovery PIN."""
        pin = (pin or "").strip()
        if not pin.isdigit() or not (4 <= len(pin) <= 6):
            raise ValueError("Recovery PIN must be 4-6 digits")
        self.ensure_user_profile_columns()
        pin_hash = generate_password_hash(pin)
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE id = ?", (int(user_id),)
            ).fetchone()
            if existing is None:
                raise ValueError("User not found")
            conn.execute(
                "UPDATE users SET recovery_pin_hash = ?, recovery_pin_fail_count = 0, "
                "recovery_pin_locked_until = NULL, updated_at = ? WHERE id = ?",
                (pin_hash, datetime.now(timezone.utc).isoformat(), int(user_id)),
            )

    def has_recovery_pin(self, user_id: int) -> bool:
        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT recovery_pin_hash FROM users WHERE id = ?", (int(user_id),)
            ).fetchone()
        return bool(row and row[0])

    def reset_password_with_pin(
        self, username: str, pin: str, new_password: str
    ) -> tuple[bool, str]:
        """Self-service: forgot password, verified by the recovery PIN instead."""
        username = (username or "").strip()
        pin = (pin or "").strip()
        if not username or not pin or not new_password:
            return False, "User Id, recovery PIN and new password are required"
        if len(new_password) < 6:
            return False, "New password must be at least 6 characters"

        self.ensure_user_profile_columns()
        generic_error = "Invalid User Id or recovery PIN"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            base = self.resolve_user_login_row(
                conn,
                username,
                [
                    "id",
                    "recovery_pin_hash",
                    "recovery_pin_fail_count",
                    "recovery_pin_locked_until",
                ],
            )
            if base is None:
                return False, generic_error
            user_id = base["id"]
            pin_hash = base["recovery_pin_hash"]
            fail_count = base["recovery_pin_fail_count"] or 0
            locked_until = base["recovery_pin_locked_until"]

            if locked_until:
                try:
                    locked_dt = datetime.fromisoformat(locked_until)
                    if datetime.now(timezone.utc) < locked_dt:
                        return False, "Too many attempts. Try again in a few minutes."
                except ValueError:
                    pass

            if not pin_hash:
                return False, "No recovery PIN set for this account. Contact your admin."

            if not check_password_hash(pin_hash, pin):
                fail_count += 1
                if fail_count >= self._RECOVERY_PIN_MAX_ATTEMPTS:
                    locked_dt = datetime.now(timezone.utc) + timedelta(
                        minutes=self._RECOVERY_PIN_LOCKOUT_MINUTES
                    )
                    conn.execute(
                        "UPDATE users SET recovery_pin_fail_count = 0, "
                        "recovery_pin_locked_until = ? WHERE id = ?",
                        (locked_dt.isoformat(), user_id),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET recovery_pin_fail_count = ? WHERE id = ?",
                        (fail_count, user_id),
                    )
                return False, generic_error

            new_hash = generate_password_hash(new_password)
            conn.execute(
                "UPDATE users SET password_hash = ?, recovery_pin_fail_count = 0, "
                "recovery_pin_locked_until = NULL, updated_at = ? WHERE id = ?",
                (new_hash, datetime.now(timezone.utc).isoformat(), user_id),
            )
            return True, "Password reset successful"

    def list_workspace_users(self, workspace_id: str) -> list[dict[str, Any]]:
        """Roster for the workspace-owner's user-management screen — same
        workspace only, never cross-tenant."""
        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, username, full_name, email, role, status
                FROM users
                WHERE workspace_id = ?
                  AND IFNULL(status, 'active') = 'active'
                  AND lower(username) NOT LIKE 'archived_dup_%'
                ORDER BY username COLLATE NOCASE
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_user_role(self, user_id: int, workspace_id: str, role: str) -> dict[str, Any]:
        """Workspace-scoped: can only touch a user inside the caller's own workspace.

        No role-lockout guard needed here: the mobile app has a dedicated
        screen for every role an owner could pick (Admin dashboard for
        "admin"; the BD workspace as the catch-all for a non-hop owner on
        any other role), so an owner can never end up on the unsupported-role
        screen regardless of which role they set on their own account.
        """
        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE id = ? AND workspace_id = ?",
                (int(user_id), workspace_id),
            ).fetchone()
            if existing is None:
                raise ValueError("User not found in this workspace")
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (role, datetime.now(timezone.utc).isoformat(), int(user_id)),
            )
            conn.commit()
        profile = self.get_user_profile(int(user_id))
        if profile is None:
            raise ValueError("User not found")
        return profile

    def ensure_user_profile_columns(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_column_exists(conn, "users", "email", "TEXT")
            self._ensure_column_exists(conn, "users", "full_name", "TEXT")
            self._ensure_column_exists(conn, "users", "phone", "TEXT")
            self._ensure_column_exists(conn, "users", "employee_id", "TEXT")
            self._ensure_column_exists(conn, "users", "updated_at", "TEXT")
            self._ensure_column_exists(conn, "users", "recovery_pin_hash", "TEXT")
            self._ensure_column_exists(
                conn, "users", "recovery_pin_fail_count", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column_exists(conn, "users", "recovery_pin_locked_until", "TEXT")
            self._ensure_column_exists(
                conn, "users", "is_workspace_owner", "INTEGER NOT NULL DEFAULT 0"
            )

    def is_workspace_owner_user(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "is_workspace_owner" not in cols:
                return False
            row = conn.execute(
                "SELECT is_workspace_owner FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        return bool(row and int(row[0] or 0) == 1)

    def promote_workspace_owner(
        self,
        username: str = "kunwar1del",
        *,
        keep_bd_role: bool = True,
        takeover_workspace_data: bool = True,
    ) -> dict[str, Any]:
        """
        Promote a login to supreme workspace owner:
        - is_workspace_owner = 1 (admin powers + claim rights)
        - role stays sales_executive so Android BD shell still works
        - optionally reassign all workspace business rows to this user_id
        """
        self.ensure_user_profile_columns()
        # Target / masters user_id columns exist after normal init; claim helpers need them.
        try:
            self.ensure_target_achievement_tables()
        except Exception:
            pass
        uname = (username or "").strip()
        if not uname:
            return {"action": "noop", "reason": "username required"}

        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            row = conn.execute(
                "SELECT id, role, workspace_id FROM users WHERE lower(username) = lower(?)",
                (uname,),
            ).fetchone()
            if row is None:
                return {"action": "noop", "reason": f"user {uname!r} not found"}

            uid = int(row[0])
            role = (row[1] or "unassigned").strip()
            workspace_id = (row[2] or "default").strip() or "default"

            # Only one supreme owner per workspace.
            if "is_workspace_owner" in cols:
                conn.execute(
                    """
                    UPDATE users SET is_workspace_owner = 0
                    WHERE workspace_id = ? AND id != ?
                    """,
                    (workspace_id, uid),
                )
                conn.execute(
                    "UPDATE users SET is_workspace_owner = 1 WHERE id = ?",
                    (uid,),
                )

            # Keep BD mobile shell; owner powers come from the flag + require_role bypass.
            if keep_bd_role and role not in {"sales_executive", "admin", "hop_admin"}:
                conn.execute(
                    "UPDATE users SET role = ? WHERE id = ?",
                    ("sales_executive", uid),
                )
                role = "sales_executive"
            elif keep_bd_role and role == "admin":
                # Prefer BD shell on Android over bare admin unsupported screen.
                conn.execute(
                    "UPDATE users SET role = ? WHERE id = ?",
                    ("sales_executive", uid),
                )
                role = "sales_executive"

            takeover: dict[str, int] = {}
            if takeover_workspace_data:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                peer_ids = [
                    int(r[0])
                    for r in conn.execute(
                        "SELECT id FROM users WHERE workspace_id = ?",
                        (workspace_id,),
                    ).fetchall()
                ]
                if uid not in peer_ids:
                    peer_ids.append(uid)
                peer_placeholders = ",".join("?" for _ in peer_ids) or "?"

                # Business tables — owner takes the company silo for this workspace.
                candidates = [
                    ("master_distributors", "workspace_id"),
                    ("master_retailers", "workspace_id"),
                    ("target_achievement_years", "workspace_id"),
                    ("order_sheets", "workspace_id"),
                    ("distributor_secondary_sales", "workspace_id"),
                    ("distributor_category_payments", "workspace_id"),
                    ("executive_visits", "workspace_id"),
                    ("dsr_market_visits", "workspace_id"),
                    ("approach_distributors", "workspace_id"),
                    ("article_master", "workspace_id"),
                    ("filled_orders", None),
                    ("fo_so_match_runs", None),
                ]
                for table, ws_col in candidates:
                    if table not in tables:
                        continue
                    tcols = {
                        r[1]
                        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    }
                    if "user_id" not in tcols:
                        continue
                    if ws_col and ws_col in tcols:
                        cur = conn.execute(
                            f"UPDATE {table} SET user_id = ? WHERE {ws_col} = ?",
                            (uid, workspace_id),
                        )
                    else:
                        # No workspace column: only reassign rows owned by users in
                        # this workspace (or still unowned). Never touch other companies.
                        cur = conn.execute(
                            f"""
                            UPDATE {table}
                            SET user_id = ?
                            WHERE user_id IS NULL OR user_id IN ({peer_placeholders})
                            """,
                            (uid, *peer_ids),
                        )
                    takeover[table] = int(cur.rowcount or 0)

            conn.commit()

        # Claim helper also covers any NULL leftovers after schema drift.
        claim = self.claim_unowned_masters(workspace_id=workspace_id, user_id=uid)
        return {
            "action": "promoted",
            "user_id": uid,
            "username": uname,
            "role": role,
            "workspace_id": workspace_id,
            "is_workspace_owner": True,
            "takeover": takeover,
            "claim": claim,
        }

    def get_user_profile(self, user_id: int) -> dict[str, Any] | None:
        self.ensure_user_profile_columns()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            wanted = [
                "id",
                "username",
                "email",
                "full_name",
                "phone",
                "employee_id",
                "role",
                "workspace_id",
                "status",
                "is_workspace_owner",
            ]
            select_cols = [c for c in wanted if c in cols]
            if "id" not in select_cols or "username" not in select_cols:
                return None
            row = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        if "is_workspace_owner" in data:
            data["is_workspace_owner"] = bool(int(data.get("is_workspace_owner") or 0))
        return data

    def update_user_profile(
        self,
        user_id: int,
        *,
        username: str | None = None,
        email: str | None = None,
        full_name: str | None = None,
        phone: str | None = None,
        employee_id: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Owner/self profile update — fields used when creating a user id."""
        self.ensure_user_profile_columns()
        uid = int(user_id)
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, username, email FROM users WHERE id = ?", (uid,)
            ).fetchone()
            if existing is None:
                raise ValueError("User not found")

            next_username = (
                username.strip() if username is not None else (existing[1] or "")
            )
            if email is not None:
                next_email = email.strip() or None
            elif existing[2]:
                next_email = existing[2]
            else:
                next_email = None

            clean_user, clean_email = self.assert_login_identity_available(
                conn,
                username=next_username,
                email=next_email,
                exclude_user_id=uid,
            )

            sets: list[str] = []
            params: list[Any] = []

            if username is not None:
                sets.append("username = ?")
                params.append(clean_user)

            if email is not None or (
                username is not None
                and self.is_email_shaped(clean_user)
                and clean_email
            ):
                sets.append("email = ?")
                params.append(clean_email)

            if full_name is not None:
                sets.append("full_name = ?")
                params.append(full_name.strip() or None)

            if phone is not None:
                sets.append("phone = ?")
                params.append(phone.strip() or None)

            if employee_id is not None:
                clean_emp = employee_id.strip()
                if clean_emp:
                    clash = conn.execute(
                        "SELECT id FROM users WHERE lower(IFNULL(employee_id,'')) = lower(?) AND id != ?",
                        (clean_emp, uid),
                    ).fetchone()
                    if clash:
                        raise ValueError("Employee Id already taken")
                sets.append("employee_id = ?")
                params.append(clean_emp or None)

            if password is not None and str(password).strip():
                sets.append("password_hash = ?")
                params.append(generate_password_hash(str(password).strip()))

            if sets:
                sets.append("updated_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())
                params.append(uid)
                conn.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                    tuple(params),
                )
                conn.commit()

        profile = self.get_user_profile(uid)
        if profile is None:
            raise ValueError("User not found")
        return profile

    def migrate_bd_owner_login(
        self,
        old_username: str = "bd_gt_north_head",
        new_username: str = "kps.julka@gmail.com",
        new_password: str = "@Princeking123",
        full_name: str | None = "K.P.S. Julka",
    ) -> dict[str, Any]:
        """
        One-shot: rename legacy BD login → email User Id (idempotent).
        Keeps same user id + workspace so Party Master / DSR stay linked.
        """
        self.ensure_user_profile_columns()
        old_u = (old_username or "").strip()
        new_u = (new_username or "").strip()
        with sqlite3.connect(self.db_path) as conn:
            old_row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (old_u,)
            ).fetchone()
            new_row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (new_u,)
            ).fetchone()
            if old_row is None and new_row is not None:
                return {
                    "action": "already_renamed",
                    "user_id": int(new_row[0]),
                    "username": new_u,
                }
            if old_row is None:
                return {"action": "noop", "reason": "old user not found"}
            if new_row is not None and int(new_row[0]) != int(old_row[0]):
                return {"action": "blocked", "reason": "target username already exists"}
            uid = int(old_row[0])
            try:
                clean_user, clean_email = self.assert_login_identity_available(
                    conn, username=new_u, email=new_u, exclude_user_id=uid
                )
            except ValueError as exc:
                return {"action": "blocked", "reason": str(exc)}
            conn.execute(
                """
                UPDATE users SET
                    username = ?,
                    password_hash = ?,
                    email = ?,
                    full_name = COALESCE(NULLIF(full_name, ''), ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_user,
                    generate_password_hash(new_password),
                    clean_email,
                    full_name,
                    datetime.now(timezone.utc).isoformat(),
                    uid,
                ),
            )
            conn.commit()
            return {"action": "renamed", "user_id": uid, "username": clean_user}

    def ensure_hop_admin_login(
        self,
        old_username: str = "hop_prizm",
        new_username: str = "prince1del",
        new_password: str = "@Princeking123",
    ) -> dict[str, Any]:
        """Create or rename the House of Prizm admin login (idempotent)."""
        from app.hop_schema import HOP_ROLE, HOP_WORKSPACE_ID, ensure_hop_schema

        ensure_hop_schema(self.db_path)
        self.ensure_user_profile_columns()
        old_u = (old_username or "").strip()
        new_u = (new_username or "").strip()
        password = (new_password or "").strip()
        if not new_u or not password:
            return {"action": "noop", "reason": "username and password required"}

        now = datetime.now(timezone.utc).isoformat()
        pw_hash = generate_password_hash(password)

        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            old_row = (
                conn.execute(
                    "SELECT id FROM users WHERE lower(username) = lower(?)",
                    (old_u,),
                ).fetchone()
                if old_u
                else None
            )
            new_row = conn.execute(
                "SELECT id FROM users WHERE lower(username) = lower(?)",
                (new_u,),
            ).fetchone()

            def _apply(uid: int, username: str) -> None:
                clean_user, clean_email = self.assert_login_identity_available(
                    conn, username=username, email=None, exclude_user_id=uid
                )
                sets = [
                    "username = ?",
                    "password_hash = ?",
                    "role = ?",
                    "workspace_id = ?",
                ]
                params: list[Any] = [clean_user, pw_hash, HOP_ROLE, HOP_WORKSPACE_ID]
                if "email" in cols:
                    sets.append("email = ?")
                    params.append(clean_email)
                if "status" in cols:
                    sets.append("status = ?")
                    params.append("active")
                if "updated_at" in cols:
                    sets.append("updated_at = ?")
                    params.append(now)
                params.append(uid)
                conn.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                    params,
                )

            if old_row is not None and (
                new_row is None or int(new_row[0]) == int(old_row[0])
            ):
                uid = int(old_row[0])
                renamed = old_u.lower() != new_u.lower()
                _apply(uid, new_u)
                conn.commit()
                return {
                    "action": "renamed" if renamed else "updated",
                    "user_id": uid,
                    "username": new_u,
                }

            if new_row is not None:
                uid = int(new_row[0])
                _apply(uid, new_u)
                conn.commit()
                return {"action": "updated", "user_id": uid, "username": new_u}

            try:
                clean_user, clean_email = self.assert_login_identity_available(
                    conn, username=new_u, email=None
                )
            except ValueError as exc:
                return {"action": "blocked", "reason": str(exc)}

            extra_cols: list[str] = []
            extra_vals: list[Any] = []
            if "status" in cols:
                extra_cols.append("status")
                extra_vals.append("active")
            if "email" in cols and clean_email:
                extra_cols.append("email")
                extra_vals.append(clean_email)
            col_sql = "username, password_hash, created_at, role, workspace_id"
            val_sql = "?, ?, ?, ?, ?"
            params = [clean_user, pw_hash, now, HOP_ROLE, HOP_WORKSPACE_ID]
            if extra_cols:
                col_sql += ", " + ", ".join(extra_cols)
                val_sql += ", " + ", ".join("?" for _ in extra_vals)
                params.extend(extra_vals)
            cur = conn.execute(
                f"INSERT INTO users ({col_sql}) VALUES ({val_sql})",
                params,
            )
            conn.commit()
            return {
                "action": "created",
                "user_id": int(cur.lastrowid),
                "username": new_u,
            }

    def ensure_default_admin_user(self) -> None:
        if os.getenv("AUTH_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            return

        with sqlite3.connect(self.db_path) as conn:
            has_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if has_user:
                return

            username = os.getenv("ADMIN_USERNAME", "admin")
            password = os.getenv("ADMIN_PASSWORD", "Admin123!")
            self.create_user(username, password, role='admin')

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'unassigned',
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL DEFAULT 'active'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_ui_preferences (
                    user_id INTEGER PRIMARY KEY,
                    theme_id TEXT NOT NULL DEFAULT 'emerald',
                    custom_colors_json TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
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
                    contact_person_role TEXT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    phone_number TEXT,
                    location TEXT,
                    address TEXT,
                    pincode TEXT,
                    email TEXT,
                    gst_no TEXT,
                    buyer_code TEXT,
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
                    retailer_code TEXT,
                    name TEXT NOT NULL,
                    distributor_id INTEGER,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    location TEXT,
                    phone_number TEXT,
                    email TEXT,
                    address TEXT,
                    gst_no TEXT,
                    secondary_retailer_name TEXT,
                    secondary_retailer_phone_number TEXT,
                    secondary_retailer_birthday TEXT,
                    secondary_retailer_anniversary TEXT,
                    sales_executive_name TEXT,
                    sales_executive_phone_number TEXT,
                    sales_executive_email TEXT,
                    sales_executive_birthday TEXT,
                    sales_executive_anniversary TEXT,
                    owner_name TEXT,
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
                    distributor_id INTEGER,
                    zone TEXT,
                    target_amount REAL NOT NULL DEFAULT 0,
                    achievement_amount REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                CREATE TABLE IF NOT EXISTS processed_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    document_number TEXT NOT NULL,
                    tracking_id INTEGER,
                    processed_at TEXT NOT NULL,
                    UNIQUE(workspace_id, document_type, document_number)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS filled_order_item_baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    item_key TEXT NOT NULL,
                    item_name TEXT,
                    ordered_qty REAL,
                    ordered_value REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, distributor_id, item_key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_filled_order_baselines_lookup "
                "ON filled_order_item_baselines(workspace_id, distributor_id, item_key)"
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
            self._ensure_column_exists(conn, "distributor_order_uploads", "distributor_id", "INTEGER")
            self._ensure_column_exists(conn, "distributor_order_uploads", "workspace_id", "TEXT")
            self._ensure_column_exists(conn, "distributor_order_uploads", "linked_tracking_id", "INTEGER")
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS article_master_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    brand TEXT NOT NULL,
                    tc TEXT,
                    size TEXT,
                    bs_size TEXT,
                    product TEXT,
                    print_style TEXT,
                    bale_size TEXT,
                    colors TEXT,
                    mrp REAL DEFAULT 0,
                    selling_price REAL DEFAULT 0,
                    ptr REAL DEFAULT 0,
                    retailer_margin REAL DEFAULT 0,
                    exmill_price REAL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT UNIQUE NOT NULL,
                    company_name TEXT NOT NULL,
                    gst_number TEXT,
                    pan_number TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    pincode TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                    sales_order_file_reference TEXT,
                    sales_order_parsed TEXT,
                    payment_status TEXT,
                    commercial_invoice_date TEXT,
                    commercial_invoice_file_reference TEXT,
                    commercial_invoice_parsed TEXT,
                    dispatch_date TEXT,
                    expected_delivery_date TEXT,
                    actual_delivery_date TEXT,
                    pod_number TEXT,
                    transit_status TEXT NOT NULL DEFAULT 'ORDERED',
                    receiving_status TEXT,
                    receiving_condition TEXT,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_fulfillment_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_lifecycle_id INTEGER NOT NULL,
                    product_code TEXT,
                    brand TEXT,
                    color TEXT,
                    ordered_qty INTEGER NOT NULL DEFAULT 0,
                    fulfilled_qty INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY(order_lifecycle_id) REFERENCES order_lifecycle_tracking(tracking_id)
                )
                """
            )
            self._ensure_column_exists(conn, "order_fulfillment_items", "item_key", "TEXT")
            self._ensure_column_exists(conn, "order_fulfillment_items", "item_name", "TEXT")
            self._ensure_column_exists(conn, "order_fulfillment_items", "ordered_value", "REAL")
            self._ensure_column_exists(conn, "order_fulfillment_items", "so_qty", "REAL")
            self._ensure_column_exists(conn, "order_fulfillment_items", "so_value", "REAL")
            self._ensure_column_exists(conn, "order_fulfillment_items", "ci_qty", "REAL")
            self._ensure_column_exists(conn, "order_fulfillment_items", "ci_value", "REAL")
            self._ensure_column_exists(conn, "order_fulfillment_items", "has_discrepancy", "INTEGER DEFAULT 0")
            self._ensure_column_exists(conn, "order_fulfillment_items", "discrepancy_notes", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "order_sheet_id", "INTEGER")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "order_sheet_name", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dispatch_pod_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER NOT NULL,
                    pod_number TEXT,
                    driver_name TEXT,
                    vehicle_number TEXT,
                    dispatched_at TEXT,
                    delivered_at TEXT,
                    pod_text TEXT,
                    pod_attachment_reference TEXT,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY(tracking_id) REFERENCES order_lifecycle_tracking(tracking_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS returns_claims (
                    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER NOT NULL,
                    product_code TEXT,
                    returned_qty INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY(tracking_id) REFERENCES order_lifecycle_tracking(tracking_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invoice_reconciliations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER,
                    invoice_number TEXT,
                    invoice_date TEXT,
                    invoice_amount REAL,
                    reconciled BOOLEAN NOT NULL DEFAULT 0,
                    reconciled_at TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id INTEGER,
                    invoice_number TEXT,
                    invoice_date TEXT,
                    invoice_amount REAL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_payment_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    distributor_id INTEGER NOT NULL,
                    tracking_id INTEGER,
                    order_ref_no TEXT,
                    amount REAL NOT NULL,
                    payment_date TEXT NOT NULL,
                    note TEXT,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(tracking_id) REFERENCES order_lifecycle_tracking(tracking_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dpe_workspace_distributor "
                "ON distributor_payment_entries(workspace_id, distributor_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dpe_tracking "
                "ON distributor_payment_entries(tracking_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_code TEXT,
                    adjustment_qty REAL,
                    reason TEXT,
                    related_tracking_id INTEGER,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT,
                    channel TEXT,
                    address TEXT,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS material_code_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_prefix TEXT NOT NULL,
                    mapping_type TEXT NOT NULL,
                    mapping_value TEXT NOT NULL,
                    description TEXT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_lifecycle_tracking_id INTEGER,
                    amount REAL NOT NULL,
                    currency TEXT DEFAULT 'INR',
                    source TEXT,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_alerts (
                    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,
                    reference_id TEXT,
                    message TEXT NOT NULL,
                    resolved BOOLEAN NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default'
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
                CREATE TABLE IF NOT EXISTS order_sheet_master (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    file_reference TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    user_id INTEGER
                )
                """
            )
            self._ensure_column_exists(conn, "order_sheet_master", "user_id", "INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_sheet_master_user "
                "ON order_sheet_master(workspace_id, user_id)"
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS global_search_index USING fts5(
                    content, category, source_id, source_table, workspace_id UNINDEXED, tokenize='porter unicode61'
                )
                """
            )
            # SCHEMA MIGRATION: the table may already exist from before
            # workspace_id was added (FTS5 virtual tables can't be
            # ALTERed) — detect the old 4-column schema and rebuild.
            try:
                existing_cols = [
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(global_search_index)"
                    ).fetchall()
                ]
                if existing_cols and "workspace_id" not in existing_cols:
                    conn.execute("DROP TABLE global_search_index")
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE global_search_index USING fts5(
                            content, category, source_id, source_table, workspace_id UNINDEXED, tokenize='porter unicode61'
                        )
                        """
                    )
            except Exception:
                pass
            self._ensure_column_exists(conn, "master_distributors", "latitude", "REAL")
            self._ensure_column_exists(conn, "master_distributors", "longitude", "REAL")
            self._ensure_column_exists(
                conn, "master_distributors", "phone_number", "TEXT"
            )
            self._ensure_column_exists(conn, "master_distributors", "email", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "address", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "zone", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "territory", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "region", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "gst_no", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "buyer_code", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "location", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "sales_order_file_reference", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "sales_order_parsed", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "sales_order_drive_file_id", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "commercial_invoice_file_reference", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "commercial_invoice_parsed", "TEXT")
            self._ensure_column_exists(conn, "order_lifecycle_tracking", "commercial_invoice_drive_file_id", "TEXT")
            self._ensure_column_exists(conn, "dispatch_pod_records", "pod_text", "TEXT")
            self._ensure_column_exists(conn, "dispatch_pod_records", "pod_attachment_reference", "TEXT")
            self._ensure_column_exists(conn, "invoices", "tracking_id", "INTEGER")
            self._ensure_column_exists(conn, "invoices", "invoice_number", "TEXT")
            self._ensure_column_exists(conn, "invoices", "invoice_date", "TEXT")
            self._ensure_column_exists(conn, "invoices", "invoice_amount", "REAL")
            self._ensure_column_exists(conn, "invoices", "status", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "firm_name", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "updated_at", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "updated_at", "TEXT")
            self._ensure_column_exists(
                conn, "master_distributors", "firm_nick_name", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "distributor_code", "TEXT"
            )
            self._ensure_column_exists(conn, "master_distributors", "pincode", "TEXT")
            self._ensure_column_exists(
                conn, "master_distributors", "payment_terms", "TEXT"
            )
            self._ensure_column_exists(conn, "master_distributors", "birthday", "TEXT")
            self._ensure_column_exists(
                conn, "master_distributors", "anniversary", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "secondary_distributor_name", "TEXT"
            )
            self._ensure_column_exists(
                conn,
                "master_distributors",
                "secondary_distributor_phone_number",
                "TEXT",
            )
            self._ensure_column_exists(
                conn, "master_distributors", "secondary_distributor_birthday", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "secondary_distributor_anniversary", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "sales_executive_name", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "sales_executive_phone_number", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "sales_executive_email", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "sales_executive_birthday", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_distributors", "sales_executive_anniversary", "TEXT"
            )
            self._ensure_column_exists(conn, "master_retailers", "latitude", "REAL")
            self._ensure_column_exists(conn, "master_retailers", "longitude", "REAL")
            self._ensure_column_exists(conn, "master_retailers", "phone_number", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "email", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "address", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "gst_no", "TEXT")
            self._ensure_column_exists(
                conn, "master_retailers", "retailer_code", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "secondary_retailer_name", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "secondary_retailer_phone_number", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "secondary_retailer_birthday", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "secondary_retailer_anniversary", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "sales_executive_name", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "sales_executive_phone_number", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "sales_executive_email", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "sales_executive_birthday", "TEXT"
            )
            self._ensure_column_exists(
                conn, "master_retailers", "sales_executive_anniversary", "TEXT"
            )
            self._ensure_column_exists(conn, "master_retailers", "zone", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "region", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "contact_person", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "state", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "pincode", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "category", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "birthday", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "anniversary", "TEXT")
            self._ensure_column_exists(conn, "master_retailers", "phone_number_2", "TEXT")
            self._ensure_column_exists(conn, "master_distributors", "phone_number_2", "TEXT")
            self._ensure_column_exists(
                conn, "master_distributors", "contact_person_role", "TEXT"
            )
            # Hard per-user Party Master isolation (JWT user_id).
            self._ensure_column_exists(conn, "master_distributors", "user_id", "INTEGER")
            self._ensure_column_exists(conn, "master_retailers", "user_id", "INTEGER")
            self._ensure_column_exists(conn, "business_rules", "rule_key", "TEXT")
            self._ensure_column_exists(conn, "business_rules", "rule_value", "TEXT")
            self._ensure_column_exists(
                conn, "business_rules", "is_locked", "INTEGER NOT NULL DEFAULT 1"
            )
            self._ensure_column_exists(conn, "business_rules", "updated_at", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_distributors_name ON master_distributors(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_distributors_gst_no ON master_distributors(gst_no)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_distributors_user_workspace "
                "ON master_distributors(user_id, workspace_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_retailers_name ON master_retailers(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_retailers_distributor_id ON master_retailers(distributor_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_master_retailers_user_workspace "
                "ON master_retailers(user_id, workspace_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_business_rules_rule_key ON business_rules(rule_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_targets_achievements_distributor ON targets_achievements(distributor_id, year, month)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_primary_sales_distributor ON primary_sales(distributor_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_secondary_sales_distributor_retailer ON secondary_sales(distributor_id, retailer_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_distributor ON distributor_order_uploads(distributor_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_stage ON distributor_order_uploads(stage_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_distributor_order_uploads_uploaded_at ON distributor_order_uploads(uploaded_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_order_sheet_master_workspace_category ON order_sheet_master(workspace_id, category)"
            )
            self._seed_distributor_form_fields(conn)
            self._seed_retailer_form_fields(conn)
            self._seed_business_rules(conn)
            self._refresh_global_search_index(conn)
            conn.commit()

    def _seed_distributor_form_fields(self, conn: sqlite3.Connection) -> None:
        defaults = [
            (
                "current_stock_audit",
                "Current Stock Audit (Warehouse stock status)",
                "text",
                None,
                1,
            ),
            (
                "payment_outstanding_credit_limit",
                "Payment Outstanding & Credit Limit Discussion",
                "text",
                None,
                1,
            ),
            (
                "new_primary_order_booking",
                "New Primary Order Booking (Volume/Items)",
                "text",
                None,
                1,
            ),
            (
                "distributor_market_feedback_grievances",
                "Distributor Market Feedback & Grievances",
                "text",
                None,
                1,
            ),
            (
                "general_meeting_notes_next_actions",
                "General Meeting Notes & Next Action Steps",
                "text",
                None,
                1,
            ),
        ]
        for field_id, field_label, field_type, options, is_required in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO distributor_form_fields (field_id, field_label, field_type, options, is_required, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    field_id,
                    field_label,
                    field_type,
                    json.dumps(options) if options is not None else None,
                    is_required,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _seed_retailer_form_fields(self, conn: sqlite3.Connection) -> None:
        defaults = [
            (
                "secondary_sales_volume",
                "Secondary Sales Volume (Counter sales check)",
                "text",
                None,
                1,
            ),
            (
                "product_display_stock_availability",
                "Product Display & Stock Availability Status",
                "text",
                None,
                1,
            ),
            (
                "distributor_service_rating",
                "Distributor Service Rating (Scale 1 to 5 stars)",
                "text",
                None,
                1,
            ),
            (
                "competitor_counter_schemes_discounts",
                "Competitor Counter Schemes & Discounts Analysis",
                "text",
                None,
                1,
            ),
            (
                "retailer_order_collection",
                "Retailer Order Collection (To forward to distributor)",
                "text",
                None,
                1,
            ),
            (
                "counter_photo_reference",
                "Shop/Counter Photo Reference (Metadata link for Google Drive storage)",
                "text",
                None,
                0,
            ),
            ("counter_discussion_notes", "Counter Discussion Notes", "text", None, 0),
        ]
        for field_id, field_label, field_type, options, is_required in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO retailer_form_fields (field_id, field_label, field_type, options, is_required, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    field_id,
                    field_label,
                    field_type,
                    json.dumps(options) if options is not None else None,
                    is_required,
                    datetime.now(timezone.utc).isoformat(),
                ),
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

    def _ensure_column_exists(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(row[1] == column_name for row in rows):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _refresh_global_search_index(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM global_search_index")

        master_distributor_rows = conn.execute(
            """
            SELECT id, name, firm_name, firm_nick_name, gst_no, zone, region,
                   location, address, pincode, phone_number, workspace_id
            FROM master_distributors
            """
        ).fetchall()
        for row in master_distributor_rows:
            source_id, workspace_id = row[0], row[-1]
            content = " ".join(filter(None, (str(v) for v in row[1:-1] if v)))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "distributors", source_id, "master_distributors", workspace_id),
            )

        master_retailer_rows = conn.execute(
            """
            SELECT
                mr.id,
                mr.name,
                mr.contact_person,
                mr.sales_executive_name,
                mr.gst_no,
                mr.location,
                mr.address,
                mr.pincode,
                mr.phone_number,
                COALESCE(md.firm_name, '') AS distributor_firm_name,
                COALESCE(md.name, '') AS distributor_contact_name,
                COALESCE(md.firm_nick_name, '') AS distributor_nick_name,
                mr.workspace_id
            FROM master_retailers mr
            LEFT JOIN master_distributors md ON mr.distributor_id = md.id
            """
        ).fetchall()
        for row in master_retailer_rows:
            source_id, workspace_id = row[0], row[-1]
            content = " ".join(filter(None, (str(v) for v in row[1:-1] if v)))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "retailers", source_id, "master_retailers", workspace_id),
            )

        # Sales Orders / Commercial Invoices (order_lifecycle_tracking)
        # Include CI/SO document numbers so invoice_no and order_ref search both hit.
        order_rows = conn.execute(
            """
            SELECT
                olt.tracking_id,
                olt.order_ref_no,
                olt.transit_status,
                olt.payment_status,
                olt.sales_order_file_reference,
                olt.commercial_invoice_file_reference,
                olt.commercial_invoice_parsed,
                COALESCE(md.firm_name, md.name, '') AS distributor_name,
                (
                    SELECT GROUP_CONCAT(pd.document_number, ' ')
                    FROM processed_documents pd
                    WHERE pd.tracking_id = olt.tracking_id
                ) AS linked_doc_numbers,
                olt.workspace_id
            FROM order_lifecycle_tracking olt
            LEFT JOIN master_distributors md ON olt.distributor_id = md.id
            """
        ).fetchall()
        for row in order_rows:
            source_id, workspace_id = row[0], row[-1]
            parts = [str(v) for v in row[1:-1] if v]
            invoice_hint = self._extract_ci_invoice_no(row[6])
            if invoice_hint:
                parts.append(invoice_hint)
            content = " ".join(filter(None, parts))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "orders", source_id, "order_lifecycle_tracking", workspace_id),
            )

        # Stock / Inventory (article_master_v2)
        stock_rows = conn.execute(
            "SELECT id, brand, product, colors, size, bs_size, print_style, workspace_id FROM article_master_v2"
        ).fetchall()
        for row in stock_rows:
            source_id, workspace_id = row[0], row[-1]
            content = " ".join(filter(None, (str(v) for v in row[1:-1] if v)))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "stock", source_id, "article_master_v2", workspace_id),
            )

        verification_rows = conn.execute(
            "SELECT id, report_type, reference_id, content FROM verification_outputs"
        ).fetchall()
        for row in verification_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "verifications", row[0], "verification_outputs", "default"),
            )

        visit_rows = conn.execute(
            "SELECT visit_id, 'distributor', distributor_id, responses FROM distributor_visit_logs UNION ALL SELECT visit_id, 'retailer', retailer_id, responses FROM retailer_visit_logs"
        ).fetchall()
        for row in visit_rows:
            content = " ".join(filter(None, [str(row[1]), str(row[2]), str(row[3])]))
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "visit_logs", row[0], "visit_logs", "default"),
            )

        analytics_rows = conn.execute(
            "SELECT id, year, month, zone, target_amount, achievement_amount FROM targets_achievements"
        ).fetchall()
        for row in analytics_rows:
            content = " ".join(
                filter(
                    None,
                    [str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5])],
                )
            )
            conn.execute(
                "INSERT INTO global_search_index (content, category, source_id, source_table, workspace_id) VALUES (?, ?, ?, ?, ?)",
                (content, "analytics", row[0], "targets_achievements", "default"),
            )

    def _normalize_text(self, value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    def _party_name_fold(self, value: Any) -> str:
        """Fold common honorific spellings so Shri/Shree/Sri/Sriram match."""
        text = self._normalize_text(value).lower()
        if not text:
            return ""
        # Glued forms first (shreeram / sriram → shriram)
        text = re.sub(r"\bshree(?=[a-z])", "shri", text)
        text = re.sub(r"\bsree(?=[a-z])", "shri", text)
        text = re.sub(r"\bsri(?=[a-z])", "shri", text)
        # Spaced honorifics
        text = re.sub(r"\bshree\b", "shri", text)
        text = re.sub(r"\bsree\b", "shri", text)
        text = re.sub(r"\bsri\b", "shri", text)
        # Common surname spelling variants (Goyal/Goel/Goil are the same
        # name transliterated differently).
        text = re.sub(r"\bgoel\b", "goyal", text)
        text = re.sub(r"\bgoil\b", "goyal", text)
        return self._normalize_text(text)

    def _party_name_compact(self, value: Any) -> str:
        """Letters/digits only after honorific fold (Shri Ram → shriram)."""
        return re.sub(r"[^a-z0-9]", "", self._party_name_fold(value))

    def _party_name_phonetic(self, value: Any) -> str:
        """
        Soft key for Indic English spelling variants:
        nitin/niten, sunil/suneel, raman/roman → same key.
        Keep first letter, strip vowels from the rest, collapse repeats.
        """
        text = self._party_name_compact(value)
        if not text:
            return ""
        text = (
            text.replace("ee", "i")
            .replace("oo", "u")
            .replace("aa", "a")
            .replace("ie", "i")
            .replace("ei", "i")
        )
        text = re.sub(r"(.)\1+", r"\1", text)
        return text[0] + re.sub(r"[aeiou]", "", text[1:])

    def _global_search_party_name_variants(self, query: str) -> list[str]:
        """Generate spelling variants for party search (shriram / shree ram / sri ram)."""
        raw = (query or "").strip()
        if not raw:
            return []
        folded = self._party_name_fold(raw)
        compact = self._party_name_compact(raw)
        variants: list[str] = [raw]
        if folded and folded.lower() != raw.lower():
            variants.append(folded)
        if compact and len(compact) >= 4:
            variants.append(compact)
            # Re-insert spaces for common 2-token honorific+name (shriram → shri ram)
            if compact.startswith("shri") and len(compact) > 4:
                spaced = f"shri {compact[4:]}"
                variants.append(spaced)
        # Deduplicate preserving order
        out: list[str] = []
        seen: set[str] = set()
        for v in variants:
            key = v.lower()
            if key in seen or not v.strip():
                continue
            seen.add(key)
            out.append(v)
        return out

    def _canonicalize_known_master_name(self, value: Any) -> str:
        raw_value = str(value or "").strip()
        if not raw_value:
            return ""

        normalized_value = self._normalize_text(raw_value).lower()
        folded = self._party_name_fold(raw_value)
        compact = self._party_name_compact(raw_value)
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
            "shriram": "Shri Ram & Co",
            "shree ram": "Shri Ram & Co",
            "sri ram": "Shri Ram & Co",
            "sriram": "Shri Ram & Co",
            "dca": "DCA Marketing",
        }
        return (
            alias_map.get(normalized_value)
            or alias_map.get(folded)
            or alias_map.get(compact)
            or raw_value
        )

    def _fuzzy_match_distributor(
        self,
        reference: str,
        workspace_id: str | None = None,
        threshold: int = 88,
        word_threshold: int = 85,
        ambiguous_margin: int = 10,
    ) -> dict[str, Any]:
        """
        Matches free-text 'Distributor' reference (from a retailer
        bulk-upload row) against EXISTING master_distributors only —
        by name, firm_name, firm_nick_name, AND the individual WORDS
        of firm_name — using fuzzy (typo-tolerant) matching. This
        deliberately never creates a new distributor from a text
        reference, and never silently guesses when the result is
        unclear — matching the "Suggest, Never Silently Assume"
        principle used throughout this project.

        Comparing only against the FULL firm name badly under-scores
        genuine matches: "Benrina" vs "Bernina International P Ltd"
        scores ~35% as a whole-string comparison, but "Benrina" vs
        just the word "Bernina" scores ~86% — so short nicknames must
        also be compared against each individual word of the firm
        name, not only the complete string.

        Returns one of:
          {"status": "matched", "distributor": {...}}
          {"status": "ambiguous", "candidates": [{...}, {...}]}
          {"status": "none"}
        """
        normalized_ref = self._normalize_text(reference).lower()
        if not normalized_ref:
            return {"status": "none"}

        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT id, name, firm_name, firm_nick_name FROM master_distributors"
            params: list[Any] = []
            if workspace_id:
                query += " WHERE workspace_id = ?"
                params.append(workspace_id)
            rows = conn.execute(query, params).fetchall()

        scored: list[tuple[int, int]] = []
        for dist_id, name, firm_name, firm_nick_name in rows:
            best_score = 0

            # Whole-string comparisons (name, firm_name, firm_nick_name).
            for candidate_text in (name, firm_name, firm_nick_name):
                if not candidate_text:
                    continue
                normalized_candidate = self._normalize_text(candidate_text).lower()
                token_score = fuzz.token_set_ratio(normalized_ref, normalized_candidate)
                # See _match_distributor_in_memory() for why
                # partial_ratio is gated behind a minimum length —
                # avoids false positives on very short (e.g. 3-char)
                # reference codes.
                partial_score = (
                    fuzz.partial_ratio(normalized_ref, normalized_candidate)
                    if len(normalized_ref) >= 5
                    else 0
                )
                # Also compare with spaces/special characters stripped
                # from both sides — catches "ShriRam" (no space)
                # against "Shri Ram & Co" (has a space and an
                # ampersand) cleanly, where the space alone was enough
                # to push the plain partial_ratio just below threshold.
                despaced_score = (
                    fuzz.partial_ratio(
                        re.sub(r"[^a-z0-9]", "", normalized_ref),
                        re.sub(r"[^a-z0-9]", "", normalized_candidate),
                    )
                    if len(normalized_ref) >= 5
                    else 0
                )
                best_score = max(best_score, token_score, partial_score, despaced_score)

            ref_phon = self._party_name_phonetic(normalized_ref)

            # Word-level comparisons — catches a short nickname/typo
            # matching just ONE word of a longer official firm name
            # (e.g. "Benrina" vs the "Bernina" in "Bernina
            # International P Ltd").
            for candidate_text in (name, firm_name, firm_nick_name):
                if not candidate_text:
                    continue
                for word in self._normalize_text(candidate_text).lower().split():
                    if len(word) < 3:
                        continue
                    word_score = fuzz.ratio(normalized_ref, word)
                    if word_score >= word_threshold:
                        best_score = max(best_score, word_score)
                    # Indic spelling: nitin/niten, sunil/suneel, raman/roman
                    if ref_phon and len(ref_phon) >= 3 and self._party_name_phonetic(word) == ref_phon:
                        best_score = max(best_score, 94)

            # Accept if either the whole-string or any word-level
            # comparison cleared its threshold — both are already
            # folded into best_score via max() above.
            if best_score >= min(threshold, word_threshold):
                scored.append((dist_id, best_score))

        if not scored:
            return {"status": "none"}

        scored.sort(key=lambda item: -item[1])
        top_id, top_score = scored[0]

        if len(scored) > 1 and (top_score - scored[1][1]) < ambiguous_margin:
            candidates = [
                self.get_master_distributor(dist_id, workspace_id=workspace_id)
                for dist_id, _score in scored[:3]
            ]
            return {"status": "ambiguous", "candidates": [c for c in candidates if c]}

        distributor = self.get_master_distributor(top_id, workspace_id=workspace_id)
        if distributor is None:
            return {"status": "none"}
        return {"status": "matched", "distributor": distributor}

    def _master_distributor_label(self, distributor: dict[str, Any]) -> str:
        firm = (distributor.get("firm_name") or "").strip()
        if firm:
            return firm
        contact = (distributor.get("name") or "").strip()
        if contact:
            return contact
        return f"Distributor #{distributor.get('id')}"

    def resolve_ta_distributor_reference(
        self,
        raw_name: str,
        workspace_id: str,
        nick: str | None = None,
        *,
        threshold: int = 85,
    ) -> dict[str, Any]:
        """
        Map Excel / free-text distributor labels to master_distributors when
        fuzzy match is confident. Unmatched names are kept as-is for display.
        """
        source_name = (raw_name or "").strip() or "Unknown"
        store_nick = (nick or "").strip() or None
        references: list[str] = []
        for ref in (store_nick, source_name):
            if ref and ref not in references:
                references.append(ref)
        for ref in list(references):
            canonical = self._canonicalize_known_master_name(ref)
            if canonical and canonical not in references:
                references.append(canonical)

        matched: dict[str, Any] | None = None
        for ref in references:
            result = self._fuzzy_match_distributor(
                ref, workspace_id=workspace_id, threshold=threshold
            )
            if result.get("status") == "matched":
                matched = result["distributor"]
                break

        if matched:
            canonical_name = self._master_distributor_label(matched)
            master_nick = (matched.get("firm_nick_name") or "").strip() or None
            display_nick = master_nick or store_nick
            display_label = (
                f"{display_nick} | {canonical_name}" if display_nick else canonical_name
            )
            return {
                "matched": True,
                "distributor_id": matched["id"],
                "distributor_name": canonical_name,
                "source_distributor_name": source_name,
                "nick": display_nick,
                "display_label": display_label,
                "master": matched,
            }

        display_label = f"{store_nick} | {source_name}" if store_nick else source_name
        return {
            "matched": False,
            "distributor_id": None,
            "distributor_name": source_name,
            "source_distributor_name": source_name,
            "nick": store_nick,
            "display_label": display_label,
            "master": None,
        }

    def _breakup_numeric_columns(self, cols: set[str]) -> list[str]:
        numeric: list[str] = []
        for name in (
            "target_lakhs",
            "target_amount",
            "achievement_excel",
            "achievement_ci",
            "achievement_manual",
            "achievement",
            "achievement_amount",
        ):
            if name in cols:
                numeric.append(name)
        return numeric

    def _consolidate_breakup_rows_for_resolved(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        financial_year_id: int,
        resolved: dict[str, Any],
        cols: set[str],
    ) -> None:
        """Merge duplicate breakup rows that resolve to the same master distributor."""
        if "distributor_name" not in cols:
            return
        names: list[str] = []
        for name in (resolved["distributor_name"], resolved["source_distributor_name"]):
            if name and name not in names:
                names.append(name)
        if not names and not resolved.get("distributor_id"):
            return

        params: list[Any] = [financial_year_id]
        clauses = ["financial_year_id = ?"]
        if "workspace_id" in cols:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)

        name_placeholders = ", ".join("?" for _ in names)
        id_clause = ""
        id_params: list[Any] = []
        if resolved.get("distributor_id") and "distributor_id" in cols:
            id_clause = " OR distributor_id = ?"
            id_params.append(resolved["distributor_id"])

        where = " AND ".join(clauses) + (
            f" AND (distributor_name IN ({name_placeholders}){id_clause})"
            if names
            else f" AND distributor_id = ?"
        )
        query_params = params + names + id_params
        if not names:
            query_params = params + [resolved["distributor_id"]]

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM target_achievement_breakup WHERE {where}",
            tuple(query_params),
        ).fetchall()
        if len(rows) <= 1:
            if len(rows) == 1:
                self._apply_resolved_breakup_identity(
                    conn, workspace_id, int(rows[0]["id"]), resolved, cols
                )
            return

        keeper = rows[0]
        keeper_id = int(keeper["id"])
        numeric_cols = self._breakup_numeric_columns(cols)
        merged: dict[str, float] = {
            col: float(keeper[col] or 0) for col in numeric_cols
        }
        for row in rows[1:]:
            for col in numeric_cols:
                merged[col] = round(merged[col] + float(row[col] or 0), 4)
            conn.execute(
                "DELETE FROM target_achievement_breakup WHERE id = ?",
                (int(row["id"]),),
            )

        sets = [f"{col} = ?" for col in numeric_cols]
        update_params = [merged[col] for col in numeric_cols]
        self._apply_resolved_breakup_identity(
            conn, workspace_id, keeper_id, resolved, cols, extra_sets=sets, extra_params=update_params
        )

    def _apply_resolved_breakup_identity(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        row_id: int,
        resolved: dict[str, Any],
        cols: set[str],
        *,
        extra_sets: list[str] | None = None,
        extra_params: list[Any] | None = None,
    ) -> None:
        sets = list(extra_sets or [])
        params = list(extra_params or [])
        if "distributor_name" in cols:
            sets.append("distributor_name = ?")
            params.append(resolved["distributor_name"])
        if "nick" in cols and resolved.get("nick"):
            sets.append("nick = ?")
            params.append(resolved["nick"])
        if "distributor_id" in cols:
            sets.append("distributor_id = ?")
            params.append(resolved.get("distributor_id"))
        if "source_distributor_name" in cols:
            sets.append("source_distributor_name = ?")
            params.append(resolved.get("source_distributor_name"))
        if "workspace_id" in cols:
            sets.append("workspace_id = ?")
            params.append(workspace_id)
        if not sets:
            return
        params.append(row_id)
        conn.execute(
            f"UPDATE target_achievement_breakup SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )

    def relink_target_achievement_distributors(self, workspace_id: str) -> dict[str, int]:
        """Re-resolve TA distributor rows against master_distributors for a workspace."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache(
            "target_achievement_breakup", "target_achievement_category_breakup"
        )
        matched = 0
        unmatched = 0
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "distributor_name" not in cols:
                conn.commit()
                return {"matched": 0, "unmatched": 0}

            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, financial_year_id, distributor_name, nick, source_distributor_name "
                "FROM target_achievement_breakup WHERE workspace_id = ?"
                if "workspace_id" in cols
                else "SELECT id, financial_year_id, distributor_name, nick, source_distributor_name FROM target_achievement_breakup",
                (workspace_id,) if "workspace_id" in cols else (),
            ).fetchall() if "distributor_name" in cols else []
            if "distributor_name" not in cols and "attribute_type" in cols:
                rows = conn.execute(
                    """
                    SELECT id, financial_year_id, attribute_name AS distributor_name, nick,
                           source_distributor_name
                    FROM target_achievement_breakup
                    WHERE attribute_type = 'distributor'
                    """
                    + (" AND workspace_id = ?" if "workspace_id" in cols else ""),
                    (workspace_id,) if "workspace_id" in cols else (),
                ).fetchall()

            seen: set[tuple[int, str]] = set()
            for row in rows:
                fy_id = int(row["financial_year_id"])
                raw_name = row["source_distributor_name"] or row["distributor_name"] or ""
                nick = row["nick"]
                dedupe_key = (fy_id, raw_name)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                resolved = self.resolve_ta_distributor_reference(
                    raw_name, workspace_id, nick
                )
                self._consolidate_breakup_rows_for_resolved(
                    conn, workspace_id, fy_id, resolved, cols
                )
                if resolved["matched"]:
                    matched += 1
                else:
                    unmatched += 1

            cat_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(target_achievement_category_breakup)"
                ).fetchall()
            }
            if "distributor_name" in cat_cols:
                self._migrate_category_breakup_schema(conn)
                cat_rows = conn.execute(
                    """
                    SELECT id, financial_year_id, distributor_name, nick, source_distributor_name
                    FROM target_achievement_category_breakup
                    WHERE workspace_id = ?
                    """,
                    (workspace_id,),
                ).fetchall()
                for row in cat_rows:
                    raw_name = (
                        row["source_distributor_name"]
                        if "source_distributor_name" in cat_cols and row["source_distributor_name"]
                        else row["distributor_name"] or ""
                    )
                    resolved = self.resolve_ta_distributor_reference(
                        raw_name, workspace_id, row["nick"]
                    )
                    row_id = int(row["id"])
                    fy_id = int(row["financial_year_id"])
                    canonical = resolved["distributor_name"]
                    raw_name = row["distributor_name"] or ""
                    if canonical != raw_name:
                        dup = conn.execute(
                            """
                            SELECT id, achievement_lakhs FROM target_achievement_category_breakup
                            WHERE financial_year_id = ? AND distributor_name = ? AND category = (
                                SELECT category FROM target_achievement_category_breakup WHERE id = ?
                            )
                            """,
                            (fy_id, canonical, row_id),
                        ).fetchone()
                        if dup and int(dup[0]) != row_id:
                            merged_amt = round(
                                float(dup[1] or 0) + float(
                                    conn.execute(
                                        "SELECT achievement_lakhs FROM target_achievement_category_breakup WHERE id = ?",
                                        (row_id,),
                                    ).fetchone()[0]
                                    or 0
                                ),
                                4,
                            )
                            conn.execute(
                                "UPDATE target_achievement_category_breakup SET achievement_lakhs = ? WHERE id = ?",
                                (merged_amt, int(dup[0])),
                            )
                            conn.execute(
                                "DELETE FROM target_achievement_category_breakup WHERE id = ?",
                                (row_id,),
                            )
                            row_id = int(dup[0])
                    sets: list[str] = []
                    params: list[Any] = []
                    if "distributor_name" in cat_cols:
                        sets.append("distributor_name = ?")
                        params.append(canonical)
                    if "nick" in cat_cols and resolved.get("nick"):
                        sets.append("nick = ?")
                        params.append(resolved["nick"])
                    if "distributor_id" in cat_cols:
                        sets.append("distributor_id = ?")
                        params.append(resolved.get("distributor_id"))
                    if "source_distributor_name" in cat_cols and not (
                        "source_distributor_name" in row.keys() and row["source_distributor_name"]
                    ):
                        sets.append("source_distributor_name = ?")
                        params.append(resolved.get("source_distributor_name"))
                    if sets:
                        params.append(row_id)
                        conn.execute(
                            f"UPDATE target_achievement_category_breakup SET {', '.join(sets)} WHERE id = ?",
                            tuple(params),
                        )
            conn.commit()
        return {"matched": matched, "unmatched": unmatched}

    @staticmethod
    def _user_id_sql(column: str, user_id: int | None) -> tuple[str, list[Any]]:
        """Equality clause that treats NULL correctly (SQLite `= NULL` never matches)."""
        if user_id is None:
            return f"{column} IS NULL", []
        return f"{column} = ?", [user_id]

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
                    if key == "user_id":
                        clause, clause_params = self._user_id_sql("user_id", value)
                        clauses.append(clause)
                        params.extend(clause_params)
                    else:
                        clauses.append(f"{key} = ?")
                        params.append(value)

            if clauses:
                query += " WHERE " + " AND ".join(clauses)

            rows = connection.execute(query, params).fetchall()
            for row in rows:
                candidate_name = self._normalize_text(row[1])
                if not candidate_name:
                    continue
                # Use fuzzy matching to detect similar names (case-insensitive)
                score = fuzz.token_sort_ratio(
                    normalized_name.lower(), candidate_name.lower()
                )
                if score >= threshold:
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

        reference_no = (
            payload.get("order_ref_no")
            or payload.get("reference_no")
            or payload.get("invoice_no")
            or payload.get("document_no")
        )
        if reference_no and any(
            str(
                entry.get("order_ref_no")
                or entry.get("reference_no")
                or entry.get("invoice_no")
                or entry.get("document_no")
            )
            == str(reference_no)
            for entry in existing_entries
        ):
            warnings.append("Duplicate entry detected for reference number")

        if normalized_type in {
            "order sheet",
            "sales order",
            "sales_order",
            "so",
            "commercial invoice",
            "commercial_invoice",
            "invoice",
        }:
            rate = (
                payload.get("rate")
                or payload.get("unit_rate")
                or payload.get("unit_price")
            )
            quantity = (
                payload.get("quantity")
                or payload.get("ordered_qty")
                or payload.get("filled_qty")
            )
            amount = (
                payload.get("amount")
                or payload.get("invoice_amount")
                or payload.get("net_amount")
                or payload.get("gross_amount")
            )
            if rate is not None and quantity is not None and amount is not None:
                try:
                    expected_amount = float(quantity) * float(rate)
                    if abs(float(amount) - expected_amount) > 0.01:
                        warnings.append(
                            "Rate mismatch detected against quantity and amount"
                        )
                except (TypeError, ValueError):
                    warnings.append(
                        "Unable to validate amount against quantity and rate"
                    )

            ordered_qty = payload.get("ordered_qty")
            filled_qty = payload.get("filled_qty")
            if ordered_qty is None:
                ordered_qty = payload.get("quantity")
            if filled_qty is None:
                filled_qty = payload.get("filled_quantity") or payload.get(
                    "received_qty"
                )
            if ordered_qty is not None and filled_qty is not None:
                try:
                    if float(filled_qty) != float(ordered_qty):
                        warnings.append(
                            "Quantity discrepancy detected between ordered and filled quantities"
                        )
                except (TypeError, ValueError):
                    warnings.append("Unable to validate quantity discrepancy")

        return {
            "document_type": document_type,
            "valid": not warnings,
            "warnings": warnings,
        }

    def list_data_entry_alerts(
        self, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT alert_id, document_type, reference_no, payload, warnings, severity, created_at FROM data_entry_alert_logs"
        where_clause, params = self._workspace_clause(
            "data_entry_alert_logs", workspace_id
        )
        query += where_clause + " ORDER BY alert_id DESC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
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

    def list_credit_control(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                rows = conn.execute(
                    "SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control WHERE workspace_id = ? ORDER BY id",
                    (workspace_id,),
                ).fetchall()
            else:
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
        workspace_id: str = "default",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO data_entry_alert_logs (document_type, reference_no, payload, warnings, severity, created_at, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_type,
                    reference_no,
                    json.dumps(payload or {}),
                    json.dumps(warnings),
                    severity,
                    created_at,
                    workspace_id,
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
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        validation = self.validate_data_entry(
            document_type, payload, existing_entries=existing_entries
        )
        if not validation["valid"]:
            alert_id = self.create_data_entry_alert(
                document_type,
                payload.get("order_ref_no")
                or payload.get("reference_no")
                or payload.get("invoice_no"),
                payload,
                validation["warnings"],
                workspace_id=workspace_id,
            )
            return {
                "accepted": False,
                "alert_id": alert_id,
                "warnings": validation["warnings"],
            }

        if commit_callback is not None:
            commit_callback(payload)
        return {"accepted": True, "alert_id": None, "warnings": []}

    def record_sales_order_entry(
        self,
        payload: dict[str, Any],
        commit_callback: Any | None = None,
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.process_data_entry(
            "Sales Order",
            payload,
            commit_callback=commit_callback,
            existing_entries=existing_entries,
        )

    def record_commercial_invoice_entry(
        self,
        payload: dict[str, Any],
        commit_callback: Any | None = None,
        existing_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.process_data_entry(
            "Commercial Invoice",
            payload,
            commit_callback=commit_callback,
            existing_entries=existing_entries,
        )

    def upsert_credit_control(
        self,
        distributor_id: int,
        max_credit_limit: float | None = None,
        credit_days_allowed: int | None = None,
        account_status: str = "ACTIVE",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                # Refuse to touch a distributor that doesn't belong to this
                # workspace. Without this check, any authenticated user could
                # change ANY distributor's credit limit/account status just
                # by guessing/incrementing a distributor_id.
                owner = conn.execute(
                    "SELECT id FROM master_distributors WHERE id = ? AND workspace_id = ?",
                    (distributor_id, workspace_id),
                ).fetchone()
                if not owner:
                    raise ValueError(
                        f"Distributor {distributor_id} not found in workspace '{workspace_id}'"
                    )

            if workspace_id:
                existing = conn.execute(
                    "SELECT id FROM credit_control WHERE distributor_id = ? AND workspace_id = ?",
                    (distributor_id, workspace_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM credit_control WHERE distributor_id = ?",
                    (distributor_id,),
                ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE credit_control SET max_credit_limit = ?, credit_days_allowed = ?, account_status = ?, created_at = ? WHERE id = ?",
                    (
                        max_credit_limit,
                        credit_days_allowed,
                        account_status,
                        created_at,
                        existing[0],
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control WHERE id = ?",
                    (existing[0],),
                ).fetchone()
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO credit_control (distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        distributor_id,
                        max_credit_limit,
                        credit_days_allowed,
                        account_status,
                        created_at,
                        workspace_id or "default",
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT id, distributor_id, max_credit_limit, credit_days_allowed, account_status, created_at FROM credit_control WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
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
        policy = self.upsert_credit_control(
            distributor_id,
            max_credit_limit=max_credit_limit,
            credit_days_allowed=credit_days_allowed,
            account_status=account_status or "ACTIVE",
        )
        if bypass:
            return {"valid": True, "bypassed": True, "policy": policy}
        return {"valid": True, "bypassed": False, "policy": policy}

    def build_distributor_purchase_behavior_logs(
        self, distributor_id: int
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM distributor_purchase_behavior_logs WHERE distributor_id = ?",
                (distributor_id,),
            )
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
                        intervals.append(
                            (
                                datetime.strptime(later, "%Y-%m-%d")
                                - datetime.strptime(earlier, "%Y-%m-%d")
                            ).days
                        )
                    except ValueError:
                        continue
                avg_interval = sum(intervals) / len(intervals) if intervals else 0.0

            grouped: dict[
                tuple[int | None, str | None, str | None, str | None], dict[str, Any]
            ] = {}
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
        sales_order_file_reference: str | None = None,
        sales_order_parsed: str | None = None,
        payment_status: str | None = None,
        commercial_invoice_date: str | None = None,
        dispatch_date: str | None = None,
        expected_delivery_date: str | None = None,
        transit_status: str = "ORDERED",
        receiving_status: str | None = None,
        receiving_condition: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO order_lifecycle_tracking (
                    order_ref_no, distributor_id, order_received_date, order_filled_date, sales_order_generated_date,
                    sales_order_file_reference, sales_order_parsed, payment_status, commercial_invoice_date, dispatch_date,
                    expected_delivery_date, transit_status, receiving_status, receiving_condition, created_at, workspace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_ref_no,
                    distributor_id,
                    order_received_date,
                    order_filled_date,
                    sales_order_generated_date,
                    sales_order_file_reference,
                    sales_order_parsed,
                    payment_status,
                    commercial_invoice_date,
                    dispatch_date,
                    expected_delivery_date,
                    transit_status,
                    receiving_status,
                    receiving_condition,
                    created_at,
                    workspace_id,
                ),
            )
            tracking_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, notes) VALUES (?, ?, ?, ?)",
                (tracking_id, transit_status, created_at, "Initial status"),
            )
            conn.commit()
        self.sync_store.enqueue(
            "stock-lifecycle-create",
            {
                "tracking_id": tracking_id,
                "order_ref_no": order_ref_no,
                "distributor_id": distributor_id,
                "transit_status": transit_status,
            },
        )
        self.firebase_sync.push_record(
            {
                "entity": "order_lifecycle",
                "tracking_id": tracking_id,
                "order_ref_no": order_ref_no,
                "transit_status": transit_status,
            }
        )
        return tracking_id

    def get_order_lifecycle_tracking(
        self,
        tracking_id: int,
        workspace_id: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        # order_sheet_id/order_sheet_name were missing from this
        # SELECT entirely (silently returning None for both, always,
        # to any caller) — this is why generate_distributor_
        # reconciliation_excel() always filed the reconciliation
        # sheet under "Unassigned Order Sheet" instead of the
        # tracking record's real order sheet folder. Built the same
        # way as get_order_lifecycle_by_order_ref_no's fix: a single
        # shared columns list for both the SELECT and the result
        # dict, so they can't drift apart again.
        columns = [
            "tracking_id", "order_ref_no", "distributor_id", "order_received_date",
            "order_filled_date", "sales_order_generated_date", "sales_order_file_reference",
            "sales_order_parsed", "payment_status", "commercial_invoice_date",
            "commercial_invoice_file_reference", "commercial_invoice_parsed",
            "dispatch_date", "expected_delivery_date", "actual_delivery_date",
            "pod_number", "transit_status", "receiving_status", "receiving_condition",
            "created_at", "order_sheet_id", "order_sheet_name",
        ]
        query = f"SELECT {', '.join(columns)} FROM order_lifecycle_tracking WHERE tracking_id = ?"
        params: list[Any] = [tracking_id]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
            if row is None:
                return None
            result = dict(zip(columns, row))
            # Hard-isolate reads by Party Master ownership when JWT user_id given.
            if user_id is not None:
                dist_id = result.get("distributor_id")
                owned = conn.execute(
                    "SELECT 1 FROM master_distributors WHERE id = ? AND user_id = ?",
                    (dist_id, user_id),
                ).fetchone()
                if owned is None:
                    return None
        result["sales_order_parsed"] = json.loads(result["sales_order_parsed"]) if result["sales_order_parsed"] else None
        result["commercial_invoice_parsed"] = json.loads(result["commercial_invoice_parsed"]) if result["commercial_invoice_parsed"] else None
        return result

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

    def create_order_fulfillment_item(
        self,
        order_lifecycle_id: int,
        product_code: str | None = None,
        brand: str | None = None,
        color: str | None = None,
        ordered_qty: int = 0,
        fulfilled_qty: int = 0,
        workspace_id: str = "default",
    ) -> int:
        """Create a fulfillment item record for an order lifecycle."""
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO order_fulfillment_items (
                    order_lifecycle_id, product_code, brand, color, ordered_qty, fulfilled_qty, created_at, workspace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_lifecycle_id,
                    product_code,
                    brand,
                    color,
                    ordered_qty,
                    fulfilled_qty,
                    created_at,
                    workspace_id,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_fulfilled_quantity(
        self,
        fulfillment_id: int,
        fulfilled_increment: int = 0,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Increment fulfilled_qty for a workspace-scoped fulfillment item."""
        workspace_id = str(workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("workspace_id is required")
        try:
            inc = int(fulfilled_increment)
        except (TypeError, ValueError) as exc:
            raise ValueError("fulfilled_increment must be an integer") from exc
        if inc <= 0:
            raise ValueError("fulfilled_increment must be greater than zero")

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, order_lifecycle_id, product_code, brand, color,
                       ordered_qty, fulfilled_qty, created_at, workspace_id
                FROM order_fulfillment_items
                WHERE id = ? AND workspace_id = ?
                """,
                (fulfillment_id, workspace_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Fulfillment item {fulfillment_id} not found")
            ordered_qty = int(row[5] or 0)
            current_fulfilled = int(row[6] or 0)
            new_fulfilled = current_fulfilled + inc
            if new_fulfilled > ordered_qty:
                raise ValueError(
                    f"Fulfillment would exceed ordered quantity ({ordered_qty})"
                )
            conn.execute(
                """
                UPDATE order_fulfillment_items
                SET fulfilled_qty = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (new_fulfilled, fulfillment_id, workspace_id),
            )
            conn.commit()
        return {
            "id": row[0],
            "order_lifecycle_id": row[1],
            "product_code": row[2],
            "brand": row[3],
            "color": row[4],
            "ordered_qty": ordered_qty,
            "fulfilled_qty": new_fulfilled,
            "created_at": row[7],
            "workspace_id": row[8],
        }

    def list_fulfillment_items(
        self, order_lifecycle_id: int, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                rows = conn.execute(
                    "SELECT id, order_lifecycle_id, product_code, brand, color, ordered_qty, fulfilled_qty, created_at, workspace_id FROM order_fulfillment_items WHERE order_lifecycle_id = ? AND workspace_id = ?",
                    (order_lifecycle_id, workspace_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, order_lifecycle_id, product_code, brand, color, ordered_qty, fulfilled_qty, created_at, workspace_id FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
                    (order_lifecycle_id,),
                ).fetchall()
        return [
            {
                "id": row[0],
                "order_lifecycle_id": row[1],
                "product_code": row[2],
                "brand": row[3],
                "color": row[4],
                "ordered_qty": row[5],
                "fulfilled_qty": row[6],
                "created_at": row[7],
                "workspace_id": row[8],
            }
            for row in rows
        ]

    # Phase 2.7 helpers: Dispatch / POD, Returns & Claims, Invoice Reconciliation, Alerts
    def record_dispatch_pod(
        self,
        tracking_id: int,
        pod_number: str | None = None,
        driver_name: str | None = None,
        vehicle_number: str | None = None,
        dispatched_at: str | None = None,
        delivered_at: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO dispatch_pod_records (tracking_id, pod_number, driver_name, vehicle_number, dispatched_at, delivered_at, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tracking_id,
                    pod_number,
                    driver_name,
                    vehicle_number,
                    dispatched_at,
                    delivered_at,
                    created_at,
                    workspace_id,
                ),
            )
            conn.commit()
        # If delivered_at provided, mark lifecycle as DELIVERED
        if delivered_at:
            try:
                self.update_order_lifecycle_stage(tracking_id, actual_delivery_date=delivered_at, pod_number=pod_number, transit_status="DELIVERED", workspace_id=workspace_id)
            except Exception:
                pass
        return int(cursor.lastrowid)

    def record_return_claim(
        self,
        tracking_id: int,
        product_code: str | None = None,
        returned_qty: int = 0,
        reason: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO returns_claims (tracking_id, product_code, returned_qty, reason, status, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tracking_id, product_code, returned_qty, reason, "PENDING", created_at, workspace_id),
            )
            conn.commit()
        # Create an alert for manual review
        self.create_alert("return_claim", str(cursor.lastrowid), f"Return claim created for tracking {tracking_id}", workspace_id=workspace_id)
        return int(cursor.lastrowid)

    def reconcile_invoice(
        self,
        tracking_id: int | None,
        invoice_number: str | None,
        invoice_date: str | None,
        invoice_amount: float | None,
        reconciled: bool = False,
        notes: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        reconciled_at = datetime.now(timezone.utc).isoformat() if reconciled else None
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO invoice_reconciliations (tracking_id, invoice_number, invoice_date, invoice_amount, reconciled, reconciled_at, notes, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tracking_id, invoice_number, invoice_date, invoice_amount, 1 if reconciled else 0, reconciled_at, notes, created_at, workspace_id),
            )
            conn.commit()
        # Optionally update order lifecycle payment_status
        if tracking_id and reconciled:
            try:
                self.update_order_lifecycle_stage(tracking_id, payment_status="PAID", workspace_id=workspace_id)
            except Exception:
                pass
        return int(cursor.lastrowid)

    def create_alert(self, alert_type: str, reference_id: str | None, message: str, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO system_alerts (alert_type, reference_id, message, resolved, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?)",
                (alert_type, reference_id, message, 0, created_at, workspace_id),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_alerts(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                rows = conn.execute("SELECT alert_id, alert_type, reference_id, message, resolved, created_at FROM system_alerts WHERE workspace_id = ? ORDER BY alert_id DESC", (workspace_id,)).fetchall()
            else:
                rows = conn.execute("SELECT alert_id, alert_type, reference_id, message, resolved, created_at FROM system_alerts ORDER BY alert_id DESC").fetchall()
        return [
            {
                "alert_id": row[0],
                "alert_type": row[1],
                "reference_id": row[2],
                "message": row[3],
                "resolved": bool(row[4]),
                "created_at": row[5],
            }
            for row in rows
        ]

    # Phase 2.8 helpers
    def attach_pod_ocr(
        self,
        pod_record_id: int,
        pod_text: str | None = None,
        attachment_reference: str | None = None,
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        workspace_id = str(workspace_id or "").strip() or "default"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, pod_text, pod_attachment_reference
                FROM dispatch_pod_records
                WHERE id = ? AND workspace_id = ?
                """,
                (pod_record_id, workspace_id),
            ).fetchone()
            if row is None:
                raise ValueError("POD record not found")
            conn.execute(
                """
                UPDATE dispatch_pod_records
                SET pod_text = ?, pod_attachment_reference = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    pod_text if pod_text is not None else row[1],
                    attachment_reference if attachment_reference is not None else row[2],
                    pod_record_id,
                    workspace_id,
                ),
            )
            conn.commit()
        return {
            "id": pod_record_id,
            "pod_text": pod_text if pod_text is not None else row[1],
            "pod_attachment_reference": (
                attachment_reference if attachment_reference is not None else row[2]
            ),
        }

    def create_invoice_from_reconciliation(self, reconciliation_id: int, workspace_id: str = "default") -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT id, tracking_id, invoice_number, invoice_date, invoice_amount, reconciled FROM invoice_reconciliations WHERE id = ?", (reconciliation_id,)).fetchone()
            if not row:
                raise ValueError("Reconciliation not found")
            tracking_id = row[1]
            invoice_number = row[2] or f"AUTO-{reconciliation_id}"
            invoice_date = row[3]
            invoice_amount = row[4] or 0.0
            created_at = datetime.now(timezone.utc).isoformat()

            # Build insert dynamically based on existing invoice table columns to avoid schema mismatch
            cols = [r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()]
            # If an invoice with same invoice_number already exists, return it to avoid UNIQUE constraint
            if invoice_number and "invoice_number" in cols:
                existing = conn.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
                if existing:
                    return int(existing[0])
            insert_cols = []
            insert_vals = []
            def add(col, val):
                insert_cols.append(col)
                insert_vals.append(val)

            if "tracking_id" in cols:
                add("tracking_id", tracking_id)
            if "invoice_number" in cols:
                add("invoice_number", invoice_number)
            if "invoice_date" in cols:
                add("invoice_date", invoice_date)
            if "invoice_amount" in cols:
                add("invoice_amount", invoice_amount)
            if "status" in cols:
                add("status", "DRAFT")
            if "created_at" in cols:
                add("created_at", created_at)
            if "workspace_id" in cols:
                add("workspace_id", workspace_id)
            # If legacy required column like so_id exists, provide a default
            if "so_id" in cols and "so_id" not in insert_cols:
                add("so_id", 0)
            if "due_date" in cols and "due_date" not in insert_cols:
                # set due_date to invoice_date or created_at as a sensible default
                add("due_date", invoice_date or created_at)

            placeholders = ",".join(["?" for _ in insert_cols])
            sql = f"INSERT INTO invoices ({','.join(insert_cols)}) VALUES ({placeholders})"
            cursor = conn.execute(sql, tuple(insert_vals))
            conn.commit()
            return int(cursor.lastrowid)

    def apply_inventory_adjustment(self, article_code: str, adjustment_qty: float, reason: str | None = None, related_tracking_id: int | None = None, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("INSERT INTO inventory_adjustments (article_code, adjustment_qty, reason, related_tracking_id, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?)", (article_code, adjustment_qty, reason, related_tracking_id, created_at, workspace_id))
            conn.commit()
            adj_id = int(cursor.lastrowid)
        # enqueue stock adjust event
        self.sync_store.enqueue("inventory-adjustment", {"id": adj_id, "article_code": article_code, "adjustment_qty": adjustment_qty})
        return adj_id

    def create_notification_subscription(self, target: str, channel: str, address: str, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("INSERT INTO notification_subscriptions (target, channel, address, created_at, workspace_id) VALUES (?, ?, ?, ?, ?)", (target, channel, address, created_at, workspace_id))
            conn.commit()
            return int(cursor.lastrowid)

    def send_notification(self, target: str, message: str, workspace_id: str = "default") -> int:
        # Simple enqueue for later delivery — external worker picks this up.
        created_at = datetime.now(timezone.utc).isoformat()
        alert_id = self.create_alert("notification", target, message, workspace_id=workspace_id)
        self.sync_store.enqueue("notification", {"alert_id": alert_id, "target": target, "message": message})
        return alert_id

    # Material code mappings (Phase 2.7)
    def add_material_code_mapping(self, code_prefix: str, mapping_type: str, mapping_value: str, description: str | None = None, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO material_code_mappings (code_prefix, mapping_type, mapping_value, description, workspace_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (code_prefix, mapping_type, mapping_value, description, workspace_id, created_at),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_material_code_mappings(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                rows = conn.execute("SELECT id, code_prefix, mapping_type, mapping_value, description, created_at FROM material_code_mappings WHERE workspace_id = ? ORDER BY id DESC", (workspace_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id, code_prefix, mapping_type, mapping_value, description, created_at FROM material_code_mappings ORDER BY id DESC").fetchall()
        return [
            {
                "id": row[0],
                "code_prefix": row[1],
                "mapping_type": row[2],
                "mapping_value": row[3],
                "description": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def decode_material_code(self, code: str, workspace_id: str) -> dict[str, str]:
        code = (code or "").strip()
        workspace_id = str(workspace_id or "").strip()
        if not code or not workspace_id:
            return {}
        db = sqlite3.connect(self.db_path)
        try:
            rows = db.execute(
                """
                SELECT code_prefix, mapping_type, mapping_value
                FROM material_code_mappings
                WHERE workspace_id = ?
                ORDER BY LENGTH(code_prefix) DESC
                """,
                (workspace_id,),
            ).fetchall()
            result: dict[str, str] = {}
            for prefix, mtype, mvalue in rows:
                if not prefix:
                    continue
                if code.startswith(prefix) or prefix in code:
                    # For a simple decoder, set the mapping_type => mapping_value
                    result[mtype] = mvalue
            return result
        finally:
            db.close()

    # Achievements (Phase 2.8)
    def is_document_already_processed(
        self, workspace_id: str, document_type: str, document_number: str
    ) -> bool:
        """
        Duplicate-detection: has this SPECIFIC Sales Order (by
        order_ref_no/Contract No) or Commercial Invoice (by its own
        Invoice No — NOT the Sales Order Number it references)
        already been processed for this workspace? A genuinely
        different SO/CI (different order_ref_no or invoice_no) is
        always allowed — only re-uploading the EXACT SAME document
        number gets rejected, since re-processing it would silently
        double-count that item's qty/value.

        For CI: the stamp in processed_documents is not enough. One SO
        used to hold a single CI slot, so 9346 could overwrite 9337
        while 9337 stayed marked processed — then 9337 could not
        return. A CI is a duplicate only if a live tracking row still
        carries that invoice number.
        """
        if document_type not in ("SO", "CI"):
            raise ValueError("document_type must be 'SO' or 'CI'")
        normalized_number = (document_number or "").strip()
        if not normalized_number:
            return False
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT tracking_id FROM processed_documents "
                "WHERE workspace_id = ? AND document_type = ? AND document_number = ?",
                (workspace_id, document_type, normalized_number),
            ).fetchone()
        if row is None:
            return False
        if document_type != "CI":
            return True
        tracking_id = row[0]
        if tracking_id is None:
            return True
        tracking = self.get_order_lifecycle_tracking(int(tracking_id), workspace_id=workspace_id)
        if tracking is None:
            tracking = self.get_order_lifecycle_tracking(int(tracking_id))
        if tracking is None:
            # Tracking was deleted or the stamp is orphaned — allow re-upload.
            return False
        live = (self._extract_ci_invoice_no(tracking.get("commercial_invoice_parsed")) or "").strip()
        if live:
            return live == normalized_number
        # Header missing (text-only save): still treat as present if a CI file is linked.
        return self._lifecycle_has_ci(tracking)

    def mark_document_processed(
        self, workspace_id: str, document_type: str, document_number: str, tracking_id: int | None = None
    ) -> None:
        normalized_number = (document_number or "").strip()
        if not normalized_number:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO processed_documents "
                "(workspace_id, document_type, document_number, tracking_id, processed_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, document_type, document_number) DO UPDATE SET "
                "tracking_id = excluded.tracking_id, "
                "processed_at = excluded.processed_at",
                (workspace_id, document_type, normalized_number, tracking_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def get_distributor_order_completeness(
        self, distributor_id: int, workspace_id: str = "default"
    ) -> dict[str, Any]:
        """
        Checkpoint C from the founder's framework: "an order is only
        complete once ALL its items have SOs" — not just when one SO
        arrives. Compares every item from the distributor's original
        Filled Order (filled_order_item_baselines) against however
        many Sales Orders have arrived so far (order_fulfillment_items
        across ALL tracking_ids for this distributor), and reports
        which items are covered vs still pending.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            baseline_rows = conn.execute(
                "SELECT item_key, item_name, ordered_qty, ordered_value FROM filled_order_item_baselines "
                "WHERE workspace_id = ? AND distributor_id = ?",
                (workspace_id, distributor_id),
            ).fetchall()
            fulfillment_rows = conn.execute(
                """
                SELECT ofi.item_key, ofi.so_qty, ofi.so_value, ofi.ci_qty, ofi.ci_value, ofi.has_discrepancy
                FROM order_fulfillment_items ofi
                JOIN order_lifecycle_tracking olt ON ofi.order_lifecycle_id = olt.tracking_id
                WHERE olt.workspace_id = ? AND olt.distributor_id = ?
                """,
                (workspace_id, distributor_id),
            ).fetchall()

        fulfillment_by_key: dict[str, dict] = {}
        for row in fulfillment_rows:
            if row["item_key"]:
                fulfillment_by_key[row["item_key"]] = dict(row)

        covered_items = []
        pending_items = []
        for baseline in baseline_rows:
            key = baseline["item_key"]
            fulfillment = fulfillment_by_key.get(key)
            entry = {
                "item_key": key,
                "item_name": baseline["item_name"],
                "ordered_qty": baseline["ordered_qty"],
                "ordered_value": baseline["ordered_value"],
                "so_qty": (fulfillment or {}).get("so_qty") or 0,
                "has_discrepancy": bool((fulfillment or {}).get("has_discrepancy")),
            }
            if fulfillment and (fulfillment.get("so_qty") or 0) > 0:
                covered_items.append(entry)
            else:
                pending_items.append(entry)

        total = len(baseline_rows)
        return {
            "total_items": total,
            "covered_items_count": len(covered_items),
            "pending_items_count": len(pending_items),
            "is_complete": total > 0 and len(pending_items) == 0,
            "covered_items": covered_items,
            "pending_items": pending_items,
        }

    def get_filled_order_item_baseline(
        self, distributor_id: int, item_key: str, workspace_id: str = "default"
    ) -> dict[str, Any] | None:
        """
        Looks up whether a specific item_key was part of ANY Filled
        Order ever submitted for this distributor — used so that
        whichever SO/CI first mentions a given item (regardless of
        which order_ref_no/tracking_id it arrives under) can still
        correctly populate that item's "Ordered Qty/Value" baseline.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM filled_order_item_baselines WHERE workspace_id = ? AND distributor_id = ? AND item_key = ?",
                (workspace_id, distributor_id, item_key),
            ).fetchone()
        return dict(row) if row else None

    def upsert_order_lifecycle_item(
        self,
        tracking_id: int,
        item_name: str,
        source: str,
        qty: float,
        value: float,
        workspace_id: str = "default",
        item_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Records qty/value for one item against a tracked order, under
        one of three sources: "ordered" (Filled Order placed by the
        distributor), "so" (Sales Order), or "ci" (Commercial Invoice).

        item_key is the normalized Brand+TC+Size key (e.g.
        "ASTER|100|DB", from extract_order_sheet_item_key() /
        make_order_sheet_item_key()) — when provided, matching against
        an existing row uses this EXACT key rather than fuzzy text
        matching, since it's a reliable, verified identifier (an SO's
        18 design+color SKU-lines for "Aster" all normalize to the
        SAME key and correctly accumulate together). Falls back to
        fuzzy name-matching only when item_key isn't available.

        IMPORTANT — accumulates rather than overwrites: multiple
        Sales Orders or Commercial Invoices can genuinely exist against
        the same overall order (e.g. one SO for "Florentine", a
        separate SO for "Paisley"). If a matching item already exists
        for this tracking_id, its so_qty/so_value (or ci_qty/ci_value)
        gets ADDED TO — not replaced — so uploading a second SO/CI
        doesn't erase the first one's contribution to the same item.

        After updating, immediately re-checks for a discrepancy
        between ordered/SO/CI numbers for this item and flags it —
        "raise alarm immediately" per the founder's requirement.
        """
        if source not in {"ordered", "so", "ci"}:
            raise ValueError("source must be one of: ordered, so, ci")

        normalized_new_name = self._normalize_text(item_name).lower()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing_rows = conn.execute(
                "SELECT * FROM order_fulfillment_items WHERE order_lifecycle_id = ? AND workspace_id = ?",
                (tracking_id, workspace_id),
            ).fetchall()

            match_id = None
            if item_key:
                normalized_new_key = size_code_only_item_key(item_key)
                for row in existing_rows:
                    if row["item_key"] and item_keys_match(row["item_key"], normalized_new_key):
                        match_id = row["id"]
                        break
            if match_id is None:
                for row in existing_rows:
                    # Never fuzzy-match a row that already HAS a
                    # reliable item_key against a NEW reference with
                    # no key (or a different key) — that would risk
                    # merging two genuinely different items.
                    if row["item_key"]:
                        continue
                    # Shared pack/size/TC ("DB 1+2 180TC") scores 88+ even
                    # when brands and rupee values differ. Flora ≠ Cotton Comfort.
                    if not line_brands_match(item_name, row["item_name"] or ""):
                        continue
                    existing_name = self._normalize_text(row["item_name"] or "").lower()
                    if not existing_name:
                        continue
                    score = max(
                        fuzz.token_set_ratio(normalized_new_name, existing_name),
                        fuzz.partial_ratio(normalized_new_name, existing_name),
                    )
                    if score >= 88:
                        match_id = row["id"]
                        break

            qty_col = {"ordered": "ordered_qty", "so": "so_qty", "ci": "ci_qty"}[source]
            value_col = {"ordered": "ordered_value", "so": "so_value", "ci": "ci_value"}[source]

            if match_id is not None:
                existing_row = next(r for r in existing_rows if r["id"] == match_id)
                new_qty = (existing_row[qty_col] or 0) + qty
                new_value = (existing_row[value_col] or 0) + value
                update_sql = f"UPDATE order_fulfillment_items SET {qty_col} = ?, {value_col} = ?"
                params: list[Any] = [new_qty, new_value]
                if item_key and not existing_row["item_key"]:
                    update_sql += ", item_key = ?"
                    params.append(size_code_only_item_key(item_key))
                update_sql += " WHERE id = ?"
                params.append(match_id)
                conn.execute(update_sql, params)
                item_id = match_id
            else:
                created_at = datetime.now(timezone.utc).isoformat()
                # If this is a brand-new item arriving via an SO/CI
                # (not the "ordered" source itself), check whether
                # THIS SPECIFIC item was part of ANY Filled Order for
                # this distributor — even one already consumed by a
                # different SO/CI's upload. This is what lets a LATER
                # Sales Order for a different item (different
                # order_ref_no/tracking_id than whichever SO
                # consumed the pending queue first) still correctly
                # show its own "Ordered Qty/Value" baseline.
                baseline_qty, baseline_value = 0, 0
                if source in ("so", "ci") and item_key:
                    tracking_for_distributor = self.get_order_lifecycle_tracking(tracking_id, workspace_id=workspace_id)
                    if tracking_for_distributor and tracking_for_distributor.get("distributor_id"):
                        baseline = self.get_filled_order_item_baseline(
                            tracking_for_distributor["distributor_id"],
                            size_code_only_item_key(item_key),
                            workspace_id=workspace_id,
                        )
                        if baseline:
                            baseline_qty = baseline.get("ordered_qty") or 0
                            baseline_value = baseline.get("ordered_value") or 0

                if source == "ordered":
                    cursor = conn.execute(
                        f"INSERT INTO order_fulfillment_items "
                        f"(order_lifecycle_id, item_name, item_key, {qty_col}, {value_col}, fulfilled_qty, created_at, workspace_id) "
                        f"VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                        (tracking_id, item_name, size_code_only_item_key(item_key), qty, value, created_at, workspace_id),
                    )
                else:
                    cursor = conn.execute(
                        f"INSERT INTO order_fulfillment_items "
                        f"(order_lifecycle_id, item_name, item_key, {qty_col}, {value_col}, ordered_qty, ordered_value, fulfilled_qty, created_at, workspace_id) "
                        f"VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                        (
                            tracking_id, item_name, size_code_only_item_key(item_key), qty, value,
                            baseline_qty, baseline_value, created_at, workspace_id,
                        ),
                    )
                item_id = cursor.lastrowid
            conn.commit()

        self._recheck_item_discrepancy(item_id, workspace_id)
        return self.get_order_lifecycle_item(item_id, workspace_id=workspace_id)

    def _recheck_item_discrepancy(self, item_id: int, workspace_id: str = "default") -> None:
        """
        Flags a discrepancy the moment ordered/SO/CI numbers disagree
        for an item — checked immediately after every upsert, not on
        a delay, per the founder's "raise alarm immediately"
        requirement.
        """
        item = self.get_order_lifecycle_item(item_id, workspace_id=workspace_id)
        if item is None:
            return

        notes = []
        has_discrepancy = False

        def _mismatch(a, b, label):
            nonlocal has_discrepancy
            if a is not None and b is not None and a not in (0,) and b not in (0,) and abs(a - b) > 0.01:
                has_discrepancy = True
                notes.append(f"{label}: {a} vs {b}")

        _mismatch(item.get("ordered_qty"), item.get("so_qty"), "Ordered vs SO qty")
        _mismatch(item.get("ordered_value"), item.get("so_value"), "Ordered vs SO value")
        _mismatch(item.get("so_qty"), item.get("ci_qty"), "SO vs CI qty")
        _mismatch(item.get("so_value"), item.get("ci_value"), "SO vs CI value")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE order_fulfillment_items SET has_discrepancy = ?, discrepancy_notes = ? WHERE id = ?",
                (1 if has_discrepancy else 0, "; ".join(notes) if notes else None, item_id),
            )
            conn.commit()

    def get_order_lifecycle_item(self, item_id: int, workspace_id: str = "default") -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM order_fulfillment_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def list_order_lifecycle_items_for_tracking(
        self, tracking_id: int, workspace_id: str = "default"
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM order_fulfillment_items WHERE order_lifecycle_id = ? AND workspace_id = ? ORDER BY id",
                (tracking_id, workspace_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def generate_distributor_reconciliation_excel(
        self, tracking_id: int, workspace_id: str = "default"
    ) -> str:
        """
        Builds/refreshes the founder-requested reconciliation sheet:
          Order Cycle/{Financial Year}/{Distributor Name}/reconciliation.xlsx
        One row per item, columns for Ordered/SO/CI qty+value, and a
        final "Discrepancy" column that immediately flags any mismatch
        (rather than a tree of nested folders per document type).
        Regenerated in full every time an item changes, so it always
        reflects the LATEST state across however many SOs/CIs have
        been uploaded against this order.
        """
        tracking = self.get_order_lifecycle_tracking(tracking_id, workspace_id=workspace_id)
        if tracking is None:
            raise ValueError(f"No tracked order found for tracking_id={tracking_id}")

        distributor = self.get_master_distributor(tracking["distributor_id"], workspace_id=workspace_id)
        distributor_name = (distributor or {}).get("firm_name") or (distributor or {}).get("name") or "Unassigned"
        # Filesystem-safe folder names
        safe_distributor_name = re.sub(r'[<>:"/\\|?*]', "_", distributor_name).strip()
        safe_order_sheet_name = re.sub(
            r'[<>:"/\\|?*]', "_", tracking.get("order_sheet_name") or "Unassigned Order Sheet"
        ).strip()

        created_at = tracking.get("created_at") or datetime.now(timezone.utc).isoformat()
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_dt = datetime.now(timezone.utc)
        fy_start_year = created_dt.year if created_dt.month >= 4 else created_dt.year - 1
        financial_year = f"FY{fy_start_year}-{str(fy_start_year + 1)[-2:]}"

        upload_root = (
            Path("app/instance/order_fulfillment_files")
            if Path("app/instance").exists()
            else Path("instance/order_fulfillment_files")
        )
        target_dir = upload_root / "Order Cycle" / financial_year / safe_distributor_name / safe_order_sheet_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "reconciliation.xlsx"

        items = self.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id=workspace_id)

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Reconciliation"
        headers = [
            "Item", "Ordered Qty", "Ordered Value",
            "SO Qty", "SO Value", "CI Qty", "CI Value", "Discrepancy",
        ]
        sheet.append(headers)
        for item in items:
            discrepancy_text = item.get("discrepancy_notes") or ("" if not item.get("has_discrepancy") else "Mismatch")
            sheet.append([
                item.get("item_name"),
                item.get("ordered_qty"),
                item.get("ordered_value"),
                item.get("so_qty"),
                item.get("so_value"),
                item.get("ci_qty"),
                item.get("ci_value"),
                discrepancy_text,
            ])

        workbook.save(target_path)
        return str(target_path)

    def create_achievement(self, order_lifecycle_tracking_id: int | None, amount: float, currency: str = "INR", source: str | None = "ci", created_by: str | None = None, workspace_id: str = "default", notes: str | None = None) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO achievements (order_lifecycle_tracking_id, amount, currency, source, created_by, created_at, workspace_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_lifecycle_tracking_id,
                    float(amount),
                    currency,
                    source,
                    created_by,
                    created_at,
                    workspace_id,
                    notes,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_achievements(self, workspace_id: str | None = None, tracking_id: int | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if tracking_id is not None:
                rows = conn.execute("SELECT id, order_lifecycle_tracking_id, amount, currency, source, created_by, created_at, notes FROM achievements WHERE order_lifecycle_tracking_id = ? ORDER BY id DESC", (tracking_id,)).fetchall()
            elif workspace_id:
                rows = conn.execute("SELECT id, order_lifecycle_tracking_id, amount, currency, source, created_by, created_at, notes FROM achievements WHERE workspace_id = ? ORDER BY id DESC", (workspace_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id, order_lifecycle_tracking_id, amount, currency, source, created_by, created_at, notes FROM achievements ORDER BY id DESC").fetchall()
        return [
            {
                "id": r[0],
                "order_lifecycle_tracking_id": r[1],
                "amount": r[2],
                "currency": r[3],
                "source": r[4],
                "created_by": r[5],
                "created_at": r[6],
                "notes": r[7],
            }
            for r in rows
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
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            current = self.get_order_lifecycle_tracking(tracking_id, workspace_id=workspace_id)
            if current is None:
                raise ValueError("Tracking record not found")

            status_value = transit_status or current.get("transit_status") or "ORDERED"
            if status_value == "DELIVERED" and (
                not pod_number or not actual_delivery_date
            ):
                raise ValueError(
                    "POD number and actual delivery date are required for delivered shipments"
                )

            if workspace_id:
                where_clause = "WHERE tracking_id = ? AND workspace_id = ?"
                where_params = (tracking_id, workspace_id)
            else:
                where_clause = "WHERE tracking_id = ?"
                where_params = (tracking_id,)

            conn.execute(
                f"""
                UPDATE order_lifecycle_tracking
                SET order_filled_date = ?, sales_order_generated_date = ?, payment_status = ?, commercial_invoice_date = ?,
                    dispatch_date = ?, expected_delivery_date = ?, actual_delivery_date = ?, pod_number = ?, transit_status = ?,
                    receiving_status = ?, receiving_condition = ?
                {where_clause}
                """,
                (
                    order_filled_date
                    if order_filled_date is not None
                    else current.get("order_filled_date"),
                    sales_order_generated_date
                    if sales_order_generated_date is not None
                    else current.get("sales_order_generated_date"),
                    payment_status
                    if payment_status is not None
                    else current.get("payment_status"),
                    commercial_invoice_date
                    if commercial_invoice_date is not None
                    else current.get("commercial_invoice_date"),
                    dispatch_date
                    if dispatch_date is not None
                    else current.get("dispatch_date"),
                    expected_delivery_date
                    if expected_delivery_date is not None
                    else current.get("expected_delivery_date"),
                    actual_delivery_date
                    if actual_delivery_date is not None
                    else current.get("actual_delivery_date"),
                    pod_number if pod_number is not None else current.get("pod_number"),
                    status_value,
                    receiving_status
                    if receiving_status is not None
                    else current.get("receiving_status"),
                    receiving_condition
                    if receiving_condition is not None
                    else current.get("receiving_condition"),
                    *where_params,
                ),
            )
            conn.execute(
                "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, pod_number, actual_delivery_date, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tracking_id,
                    status_value,
                    datetime.now(timezone.utc).isoformat(),
                    pod_number if pod_number is not None else current.get("pod_number"),
                    actual_delivery_date
                    if actual_delivery_date is not None
                    else current.get("actual_delivery_date"),
                    notes or f"Stage update for {status_value}",
                ),
            )
            conn.commit()

        self.sync_store.enqueue(
            "stock-lifecycle-update",
            {
                "tracking_id": tracking_id,
                "transit_status": status_value,
                "pod_number": pod_number,
                "actual_delivery_date": actual_delivery_date,
            },
        )
        self.firebase_sync.push_record(
            {
                "entity": "order_lifecycle",
                "tracking_id": tracking_id,
                "transit_status": status_value,
            }
        )
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
                raise ValueError(
                    "POD number and actual delivery date are required for delivered shipments"
                )

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
                (
                    tracking_id,
                    transit_status,
                    changed_at,
                    pod_number,
                    actual_delivery_date,
                    notes or f"Status changed to {transit_status}",
                ),
            )
            conn.commit()

        self.sync_store.enqueue(
            "stock-lifecycle-update",
            {
                "tracking_id": tracking_id,
                "transit_status": transit_status,
                "pod_number": pod_number,
                "actual_delivery_date": actual_delivery_date,
            },
        )
        self.firebase_sync.push_record(
            {
                "entity": "order_lifecycle",
                "tracking_id": tracking_id,
                "transit_status": transit_status,
            }
        )
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

        if (
            verification_context
            and verification_context.get("invoiced_qty") is not None
        ):
            expected_invoiced = float(verification_context["invoiced_qty"])
            if float(invoiced_qty) != expected_invoiced:
                status_flag = "MISMATCH_FOUND"

        with sqlite3.connect(self.db_path) as conn:
            existing_tracking = self.get_order_lifecycle_tracking(tracking_id)
            if existing_tracking and existing_tracking.get("transit_status") not in {
                "DELIVERED",
                "CANCELLED",
            }:
                conn.execute(
                    "UPDATE order_lifecycle_tracking SET transit_status = 'DISPATCHED' WHERE tracking_id = ?",
                    (tracking_id,),
                )
                conn.execute(
                    "INSERT INTO order_lifecycle_status_history (tracking_id, transit_status, changed_at, notes) VALUES (?, ?, ?, ?)",
                    (
                        tracking_id,
                        "DISPATCHED",
                        datetime.now(timezone.utc).isoformat(),
                        "Receipt logged at distributor",
                    ),
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

        self.sync_store.enqueue(
            "stock-receipt-create",
            {
                "receipt_id": receipt_id,
                "tracking_id": tracking_id,
                "status_flag": status_flag,
            },
        )
        self.firebase_sync.push_record(
            {
                "entity": "delivery_receipt",
                "receipt_id": receipt_id,
                "tracking_id": tracking_id,
                "status_flag": status_flag,
            }
        )
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

    def list_delivery_receipts(
        self, tracking_id: int | None = None
    ) -> list[dict[str, Any]]:
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

    def add_record(
        self, name: str, email: str | None = None, department: str | None = None
    ) -> int:
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
            self._log_audit_event(
                conn,
                "create",
                "records",
                record_id=int(cursor.lastrowid),
                details={
                    "name": cleaned_name,
                    "email": email,
                    "department": department,
                },
            )
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
        self.firebase_sync.push_record(
            {
                "name": name,
                "email": email,
                "department": department,
                "created_at": created_at,
            }
        )
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
            cursor = conn.execute(
                f"UPDATE records SET {assignments} WHERE id = ?", values
            )
            self._log_audit_event(
                conn, "update", "records", record_id=record_id, details=fields
            )
            conn.commit()

        for key, value in fields.items():
            self.sync_store.enqueue(
                "update",
                {"record_id": record_id, "field": key, "value": value},
            )
            self.firebase_sync.push_record(
                {"record_id": record_id, "field": key, "value": value}
            )
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

        self.sync_store.enqueue(
            "clear_distributor_contacts", {"tables": ["master_distributors"]}
        )
        self.firebase_sync.push_record({"action": "clear_distributor_contacts"})
        return int(deleted_distributors)

    def clear_retailer_contacts(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM master_retailers")
            deleted_retailers = cursor.rowcount
            conn.commit()

        self.sync_store.enqueue(
            "clear_retailer_contacts", {"tables": ["master_retailers"]}
        )
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
                (
                    name,
                    contact_person,
                    phone,
                    email,
                    address,
                    city,
                    state,
                    gst_number,
                    credit_limit,
                    balance,
                    status,
                    created_at,
                ),
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
                (
                    name,
                    contact_person,
                    phone,
                    email,
                    address,
                    city,
                    state,
                    gst_number,
                    credit_limit,
                    balance,
                    status,
                    created_at,
                ),
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

    def add_distributor_visit_log(
        self,
        distributor_id: int,
        visit_date: str,
        visit_time: str | None = None,
        responses: dict[str, Any] | None = None,
        synced_status: str = "pending",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(responses or {})
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO distributor_visit_logs (distributor_id, visit_date, visit_time, synced_status, responses, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    distributor_id,
                    visit_date,
                    visit_time,
                    synced_status,
                    payload,
                    created_at,
                ),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def add_retailer_visit_log(
        self,
        retailer_id: int,
        linked_distributor_id: int | None = None,
        visit_date: str = "",
        visit_time: str | None = None,
        responses: dict[str, Any] | None = None,
        synced_status: str = "pending",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(responses or {})
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO retailer_visit_logs (retailer_id, linked_distributor_id, visit_date, visit_time, synced_status, responses, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retailer_id,
                    linked_distributor_id,
                    visit_date,
                    visit_time,
                    synced_status,
                    payload,
                    created_at,
                ),
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
        workspace_id: str = "default",
    ) -> int:
        created_date = created_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO workflow_todo_list (staff_id, party_id, party_type, task_description, is_completed, created_date, completed_timestamp, created_at, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    staff_id,
                    party_id,
                    party_type,
                    task_description,
                    int(is_completed),
                    created_date,
                    completed_timestamp,
                    created_at,
                    workspace_id,
                ),
            )
            conn.commit()
            self._refresh_global_search_index(conn)
            return int(cursor.lastrowid)

    def list_workflow_todos_for_party(
        self, party_id: int, party_type: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT task_id, staff_id, party_id, party_type, task_description, is_completed, created_date, completed_timestamp, created_at "
            "FROM workflow_todo_list WHERE party_id = ? AND party_type = ?"
        )
        params: list[Any] = [party_id, party_type]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        query += " ORDER BY created_date, task_id"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
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

    def generate_workflow_todos_from_pjp(
        self, plan_id: int, staff_id: int = 1
    ) -> list[int]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT planned_distributor_ids, planned_retailer_ids FROM weekly_pjp_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if not row:
            return []
        planned_distributors = json.loads(row[0] or "[]") if row[0] else []
        planned_retailers = json.loads(row[1] or "[]") if row[1] else []
        task_ids: list[int] = []
        default_tasks = ["Stock Audit", "Payment Discussion", "Order Collection"]
        for party_id in planned_distributors:
            for description in default_tasks:
                task_ids.append(
                    self.create_workflow_todo_task(
                        staff_id, int(party_id), "distributor", description
                    )
                )
        for party_id in planned_retailers:
            for description in default_tasks:
                task_ids.append(
                    self.create_workflow_todo_task(
                        staff_id, int(party_id), "retailer", description
                    )
                )
        return task_ids

    def validate_gps_coordinates(
        self,
        captured_latitude: float | None,
        captured_longitude: float | None,
        expected_latitude: float | None,
        expected_longitude: float | None,
        radius_meters: float = 100.0,
    ) -> dict[str, Any]:
        if (
            captured_latitude is None
            or captured_longitude is None
            or expected_latitude is None
            or expected_longitude is None
        ):
            return {
                "valid": False,
                "geofenced_status": "OUT_OF_BOUNDS",
                "distance_meters": None,
            }
        try:
            from math import asin, cos, radians, sin, sqrt

            earth_radius = 6371000.0
            lat1 = radians(float(captured_latitude))
            lat2 = radians(float(expected_latitude))
            delta_lat = radians(float(expected_latitude) - float(captured_latitude))
            delta_lon = radians(float(expected_longitude) - float(captured_longitude))
            a = (
                sin(delta_lat / 2) ** 2
                + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
            )
            c = 2 * asin(sqrt(a))
            distance_meters = earth_radius * c
            matched = distance_meters <= float(radius_meters)
            return {
                "valid": matched,
                "geofenced_status": "MATCHED" if matched else "OUT_OF_BOUNDS",
                "distance_meters": round(distance_meters, 2),
            }
        except Exception:
            return {
                "valid": False,
                "geofenced_status": "OUT_OF_BOUNDS",
                "distance_meters": None,
            }

    def cache_gps_coordinate_offline(
        self,
        visit_log_id: int,
        captured_latitude: float,
        captured_longitude: float,
        device_timestamp: str,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO offline_gps_cache (visit_log_id, captured_latitude, captured_longitude, device_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    visit_log_id,
                    captured_latitude,
                    captured_longitude,
                    device_timestamp,
                    created_at,
                ),
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
        workspace_id: str = "default",
    ) -> int:
        validation = self.validate_gps_coordinates(
            captured_latitude,
            captured_longitude,
            expected_latitude,
            expected_longitude,
            radius_meters,
        )
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO gps_visit_verification_logs (visit_log_id, captured_latitude, captured_longitude, geofenced_status, device_timestamp, created_at, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    visit_log_id,
                    captured_latitude,
                    captured_longitude,
                    validation["geofenced_status"],
                    device_timestamp,
                    datetime.now(timezone.utc).isoformat(),
                    workspace_id,
                ),
            )
            conn.commit()
            self.firebase_sync.push_record(
                {
                    "type": "gps_visit_verification",
                    "visit_log_id": visit_log_id,
                    "captured_latitude": captured_latitude,
                    "captured_longitude": captured_longitude,
                    "geofenced_status": validation["geofenced_status"],
                    "device_timestamp": device_timestamp,
                }
            )
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

    def validate_distributor_visit_payload(
        self, responses: dict[str, Any]
    ) -> dict[str, Any]:
        templates = self.list_distributor_form_fields()
        errors: list[str] = []
        for template in templates:
            if (
                template.get("is_required")
                and not str(responses.get(template["field_id"], "")).strip()
            ):
                errors.append(f"{template['field_label']} is required")
        return {"valid": not errors, "errors": errors}

    def validate_retailer_visit_payload(
        self, responses: dict[str, Any]
    ) -> dict[str, Any]:
        templates = self.list_retailer_form_fields()
        errors: list[str] = []
        for template in templates:
            if (
                template.get("is_required")
                and not str(responses.get(template["field_id"], "")).strip()
            ):
                errors.append(f"{template['field_label']} is required")
        return {"valid": not errors, "errors": errors}

    def save_verification_output(
        self, report_type: str, reference_id: str | None, content: str
    ) -> int:
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

    _ORDER_LIFECYCLE_LOOKUP_COLUMNS = [
        "tracking_id", "order_ref_no", "distributor_id", "order_received_date",
        "order_filled_date", "sales_order_generated_date", "sales_order_file_reference",
        "sales_order_parsed", "payment_status", "commercial_invoice_date",
        "commercial_invoice_file_reference", "commercial_invoice_parsed",
        "dispatch_date", "expected_delivery_date", "actual_delivery_date",
        "pod_number", "transit_status", "receiving_status", "receiving_condition",
        "created_at", "order_sheet_id", "order_sheet_name",
    ]

    @staticmethod
    def _lifecycle_has_ci(row: dict[str, Any] | None) -> bool:
        if not row:
            return False
        if str(row.get("commercial_invoice_file_reference") or "").strip():
            return True
        parsed = row.get("commercial_invoice_parsed")
        if isinstance(parsed, str) and parsed.strip():
            return True
        return bool(isinstance(parsed, dict) and parsed)

    def _invoice_no_from_lifecycle_row(self, row: dict[str, Any] | None) -> str:
        if not row:
            return ""
        return (self._extract_ci_invoice_no(row.get("commercial_invoice_parsed")) or "").strip()

    def list_order_lifecycle_by_order_ref_no(
        self, order_ref_no: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        if not self._normalize_text(order_ref_no):
            return []
        columns = self._ORDER_LIFECYCLE_LOOKUP_COLUMNS
        query = (
            f"SELECT {', '.join(columns)} FROM order_lifecycle_tracking "
            "WHERE LOWER(order_ref_no) = ?"
        )
        params: list[Any] = [str(order_ref_no).lower()]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        query += " ORDER BY tracking_id ASC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def get_order_lifecycle_by_order_ref_no(
        self, order_ref_no: str, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        rows = self.list_order_lifecycle_by_order_ref_no(
            order_ref_no, workspace_id=workspace_id
        )
        return rows[0] if rows else None

    def list_order_lifecycle_tracking(
        self,
        workspace_id: str | None = None,
        limit: int = 200,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Returns tracked Sales Orders/Commercial Invoices with a
        readable distributor name attached — this is what powers the
        "where do my uploaded files show up" view in the Order
        Fulfillment UI.

        When user_id is set, only rows whose distributor is owned by
        that user (master_distributors.user_id) are returned — tracking
        itself has no user_id column yet.
        """
        from app.fiscal_year import season_from_date as _season_from_date

        query = (
            "SELECT olt.tracking_id, olt.order_ref_no, olt.distributor_id, "
            "COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name, "
            "olt.sales_order_file_reference, olt.commercial_invoice_file_reference, "
            "olt.payment_status, olt.transit_status, olt.created_at, "
            "olt.commercial_invoice_parsed, olt.commercial_invoice_date, "
            "olt.commercial_invoice_drive_file_id, olt.sales_order_parsed "
            "FROM order_lifecycle_tracking olt "
            "LEFT JOIN master_distributors md ON olt.distributor_id = md.id"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if workspace_id:
            clauses.append("olt.workspace_id = ?")
            params.append(workspace_id)
        if user_id is not None:
            clauses.append("md.user_id = ?")
            params.append(user_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY olt.tracking_id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            parsed_raw = row[9]
            parsed: dict[str, Any] | None = None
            if isinstance(parsed_raw, str) and parsed_raw.strip():
                try:
                    loaded = json.loads(parsed_raw)
                    if isinstance(loaded, dict):
                        parsed = loaded
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            elif isinstance(parsed_raw, dict):
                parsed = parsed_raw

            header = (parsed or {}).get("header") if isinstance(parsed, dict) else {}
            if not isinstance(header, dict):
                header = {}
            totals = (parsed or {}).get("totals") if isinstance(parsed, dict) else {}
            if not isinstance(totals, dict):
                totals = {}

            invoice_no = self._extract_ci_invoice_no(parsed) if parsed else None
            buyer_name = (
                (header.get("buyer_name") or header.get("consignee_name") or "")
                .strip()
                or None
            )
            amount = header.get("invoice_total")
            if amount is None:
                amount = totals.get("invoice_total")
            try:
                amount = float(amount) if amount is not None and amount != "" else None
            except (TypeError, ValueError):
                amount = None

            has_ci = bool(row[5]) or bool(row[11]) or bool(parsed)
            ci_date = row[10] or header.get("invoice_date")
            ci_categories = self._ci_categories_summary(parsed) if has_ci else []
            so_parsed_raw = row[12] if len(row) > 12 else None
            has_so_parsed = False
            if isinstance(so_parsed_raw, str) and so_parsed_raw.strip():
                try:
                    so_loaded = json.loads(so_parsed_raw)
                    has_so_parsed = isinstance(so_loaded, dict) and bool(
                        so_loaded.get("header")
                        or so_loaded.get("rows")
                        or so_loaded.get("line_items")
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    has_so_parsed = False
            elif isinstance(so_parsed_raw, dict):
                has_so_parsed = bool(
                    so_parsed_raw.get("header")
                    or so_parsed_raw.get("rows")
                    or so_parsed_raw.get("line_items")
                )
            results.append(
                {
                    "tracking_id": row[0],
                    "order_ref_no": row[1],
                    "distributor_id": row[2],
                    "distributor_name": row[3],
                    # File OR bridged Order Match parsed SO (no PDF required).
                    "has_sales_order": bool(row[4]) or has_so_parsed,
                    "has_commercial_invoice": has_ci,
                    "payment_status": row[6],
                    "transit_status": row[7],
                    "created_at": row[8],
                    "invoice_no": invoice_no,
                    "buyer_name": buyer_name,
                    "buyer_gst": (header.get("buyer_gst") or "").strip() or None,
                    "ci_amount": amount,
                    "commercial_invoice_date": ci_date,
                    # Derived from commercial_invoice_date (SS=Mar-Jul,
                    # AW=Aug-Feb) — order_lifecycle_tracking has no season
                    # column of its own, this was always the CI section's
                    # single hard-coded "Others" bucket before.
                    "ci_season": _season_from_date(ci_date) if has_ci else None,
                    "ci_detail_level": (parsed or {}).get("detail_level") if parsed else None,
                    "ci_line_count": len((parsed or {}).get("line_items") or []) if parsed else 0,
                    "ci_categories": ci_categories,
                }
            )
        return results

    @staticmethod
    def _normalize_ci_category_label(raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return "Others"
        lower = text.lower()
        if lower.startswith("bath") or re.search(r"\bbath\b", lower):
            return "Bath"
        if lower.startswith("bed") or re.search(r"\bbed\b", lower) or "bedsheet" in lower:
            return "Bed"
        if lower.startswith("towel") or re.search(r"\btowel\b", lower):
            return "Bath"
        if lower.startswith("pillow") or re.search(r"\bpillow\b", lower):
            return "Pillow"
        if (
            lower in {"tob", "top of bed", "top-of-bed"}
            or "comforter" in lower
            or "dohar" in lower
            or "blanket" in lower
            or "quilt" in lower
            or "duvet" in lower
        ):
            return "TOB"
        if lower in {"other", "others", "misc", "miscellaneous"}:
            return "Others"
        # Keep short readable labels (Bedsheet → Bed already handled; TOB etc.)
        clean = text.split(":")[0].split("·")[0].split(",")[0].strip()
        return clean[:24] if clean else "Others"

    # Bed size tokens — including glued forms: "1+2KS", "1+1SB", "DBSET".
    # Digit→letter is NOT a regex word boundary, so plain \bKS\b misses 1+2KS.
    _CI_BED_SIZE_RE = re.compile(
        r"(?:(?<=\d\+\d)|(?<![A-Z0-9]))(?:SB|DB|DBL|KS|KB|KDB|QB)"
        r"(?:SET|SETS|BS|FS)?(?:\s*(?:BS|FS|SET|SETS|COMF|COMFORTER))?(?![A-Z])",
        re.IGNORECASE,
    )
    _CI_BED_WORDS = (
        "BEDSHEET",
        "BED SHEET",
        "FITTED SHEET",
        "SHEET SET",
        "BED IN BAG",
        "BINB",
        "DBSET",
        "SBSET",
        "KSSET",
        "TROUSSEAU",
    )
    _CI_TOB_WORDS = (
        "DOHAR",
        "COMFORTER",
        "COMFERTOR",
        "COMFORTOR",
        "BLANKET",
        "QUILT",
        "DUVET",
        "MINK",
        "TOB",
        "TOP OF BED",
        "BED IN A BAG",
        "FIDELIS",
    )
    _CI_BATH_WORDS = (
        "TOWEL",
        "BATH",
        "FACE CLOTH",
        "HAND TOWEL",
        "BATHMAT",
        "BATH MAT",
        "ECOSOFT",
        "ECOSTRIPE",
        "R4 SET",
    )

    def _ci_line_category_label(self, line: dict[str, Any]) -> str:
        if not isinstance(line, dict):
            return "Others"
        am = line.get("article_match") if isinstance(line.get("article_match"), dict) else {}
        art = am.get("article") if isinstance(am.get("article"), dict) else {}
        am_brand = str(art.get("brand") or "").strip().upper()
        pdf_name = str(line.get("item_name") or line.get("item_key") or "").upper()
        # Never use closest_article — that mapped Flora CIs into Aster folders.
        if am_brand and pdf_name.startswith(am_brand.split()[0]):
            cat = self._normalize_ci_category_label(art.get("category"))
            if cat != "Others":
                return cat
        name = " ".join(
            str(x)
            for x in (
                line.get("item_name"),
                line.get("material_code"),
                line.get("item_key"),
            )
            if x
        ).upper()
        # Bath / towel before bed — "BATH TOWEL" and Ecosoft must not fall to Bed.
        if any(tok in name for tok in self._CI_BATH_WORDS):
            return "Bath"
        # Physical towel sizes like "75cm x 1.5m" / "60cmX1.2m" with DYED/ASST.
        if re.search(r"\d+\s*CM", name) and any(
            tok in name for tok in ("DYED", "ASST", "WHITE", "SET", "PKG", "PRE")
        ):
            return "Bath"
        if "PILLOW" in name:
            return "Pillow"
        # TOB before Bed — blankets/dohar/comforter are not bedsheet folders.
        if any(tok in name for tok in self._CI_TOB_WORDS):
            return "TOB"
        if any(tok in name for tok in self._CI_BED_WORDS) or self._CI_BED_SIZE_RE.search(name):
            return "Bed"
        return "Others"

    def _ci_categories_summary(self, parsed: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(parsed, dict):
            return []
        lines = parsed.get("line_items") or []
        if not isinstance(lines, list) or not lines:
            return []
        buckets: dict[str, dict[str, Any]] = {}
        for line in lines:
            if not isinstance(line, dict):
                continue
            cat = self._ci_line_category_label(line)
            amount = None
            for key in ("line_total", "amount", "value", "taxable"):
                amount = self._parse_money(line.get(key))
                if amount is not None:
                    break
            qty = self._parse_money(line.get("qty")) or 0.0
            bucket = buckets.setdefault(
                cat, {"name": cat, "amount": 0.0, "qty": 0.0, "line_count": 0}
            )
            bucket["amount"] = round(float(bucket["amount"]) + float(amount or 0), 2)
            bucket["qty"] = round(float(bucket["qty"]) + float(qty), 2)
            bucket["line_count"] = int(bucket["line_count"]) + 1
        preferred = ("Bed", "Bath", "Towel", "Pillow", "TOB", "Others")
        order_index = {name: i for i, name in enumerate(preferred)}
        return sorted(
            buckets.values(),
            key=lambda b: (order_index.get(str(b["name"]), 50), str(b["name"]).lower()),
        )

    def delete_order_lifecycle_tracking(
        self,
        tracking_id: int,
        workspace_id: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Deletes a tracked Sales Order/Commercial Invoice record — used
        by the "Delete" button on the Order Fulfillment page's
        Sales Orders/CI table, to remove mistaken/duplicate/test
        uploads. Also removes its item-level reconciliation rows.
        Returns the file references (so the caller can also delete
        the physical files from disk) or None if not found.
        """
        tracking = self.get_order_lifecycle_tracking(
            tracking_id, workspace_id=workspace_id, user_id=user_id
        )
        if tracking is None:
            return None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM order_fulfillment_items WHERE order_lifecycle_id = ?", (tracking_id,))
            conn.execute("DELETE FROM achievements WHERE order_lifecycle_tracking_id = ?", (tracking_id,))
            conn.execute(
                "DELETE FROM distributor_payment_entries WHERE tracking_id = ?",
                (tracking_id,),
            )
            # Also clear the duplicate-detection record(s) tied to this
            # tracking_id (one for the SO's order_ref_no, one for the
            # CI's own invoice_no if a CI was linked). Without this, a
            # deleted-then-re-uploaded SO/CI with the SAME order_ref_no
            # or invoice_no gets wrongly rejected as "already
            # processed" even though the record it refers to no longer
            # exists — this was a recurring, confusing bug found during
            # testing (see check_order.py-based diagnosis history).
            conn.execute("DELETE FROM processed_documents WHERE tracking_id = ?", (tracking_id,))
            conn.execute("DELETE FROM order_lifecycle_tracking WHERE tracking_id = ?", (tracking_id,))
            conn.commit()

        return {
            "sales_order_file_reference": tracking.get("sales_order_file_reference"),
            "commercial_invoice_file_reference": tracking.get("commercial_invoice_file_reference"),
        }

    @staticmethod
    def _parse_money(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def _so_bill_amount_for_tracking(
        self,
        tracking: dict[str, Any],
        *,
        so_value_sum: float | None = None,
        ci_value_sum: float | None = None,
    ) -> float:
        """Bill amount for payment tracking: SO lines → SO parse → CI lines → CI header."""
        if so_value_sum is not None and so_value_sum > 0:
            return round(float(so_value_sum), 2)

        so_parsed = tracking.get("sales_order_parsed")
        if isinstance(so_parsed, str) and so_parsed.strip():
            try:
                so_parsed = json.loads(so_parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                so_parsed = None
        if isinstance(so_parsed, dict):
            header = so_parsed.get("header") if isinstance(so_parsed.get("header"), dict) else {}
            totals = so_parsed.get("totals") if isinstance(so_parsed.get("totals"), dict) else {}
            for key in ("invoice_total", "grand_total", "total_amount", "so_total", "line_total"):
                amt = self._parse_money(header.get(key))
                if amt is None:
                    amt = self._parse_money(totals.get(key))
                if amt is not None and amt > 0:
                    return round(amt, 2)
            rows = so_parsed.get("rows") or so_parsed.get("line_items") or []
            if isinstance(rows, list) and rows:
                row_sum = 0.0
                found = False
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for key in ("amount", "value", "total", "exmill", "so_value"):
                        amt = self._parse_money(row.get(key))
                        if amt is not None:
                            row_sum += amt
                            found = True
                            break
                if found and row_sum > 0:
                    return round(row_sum, 2)

        if ci_value_sum is not None and ci_value_sum > 0:
            return round(float(ci_value_sum), 2)

        ci_parsed = tracking.get("commercial_invoice_parsed")
        if isinstance(ci_parsed, str) and ci_parsed.strip():
            try:
                ci_parsed = json.loads(ci_parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                ci_parsed = None
        if isinstance(ci_parsed, dict):
            header = ci_parsed.get("header") if isinstance(ci_parsed.get("header"), dict) else {}
            totals = ci_parsed.get("totals") if isinstance(ci_parsed.get("totals"), dict) else {}
            for key in ("invoice_total", "line_total", "taxable_amount"):
                amt = self._parse_money(header.get(key))
                if amt is None:
                    amt = self._parse_money(totals.get(key))
                if amt is not None and amt > 0:
                    return round(amt, 2)
        return 0.0

    @staticmethod
    def _payment_status_from_amounts(bill: float, paid: float) -> str:
        bill_n = float(bill or 0)
        paid_n = float(paid or 0)
        if bill_n <= 0 and paid_n <= 0:
            return "UNTRACKED"
        if paid_n <= 0:
            return "DUE"
        if paid_n + 0.5 >= bill_n:
            return "PAID"
        return "PARTIAL"

    def list_distributor_payment_collection(
        self,
        workspace_id: str,
        distributor_id: int | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Distributor-wise SO payment board: bill, deposits, outstanding."""
        ws = (workspace_id or "default").strip() or "default"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = (
                "SELECT olt.tracking_id, olt.order_ref_no, olt.distributor_id, "
                "COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name, "
                "olt.sales_order_file_reference, olt.sales_order_parsed, "
                "olt.commercial_invoice_file_reference, olt.commercial_invoice_parsed, "
                "olt.payment_status, olt.created_at "
                "FROM order_lifecycle_tracking olt "
                "LEFT JOIN master_distributors md ON olt.distributor_id = md.id "
                "WHERE olt.workspace_id = ? "
                "AND ("
                "  (olt.sales_order_file_reference IS NOT NULL AND TRIM(olt.sales_order_file_reference) != '') "
                "  OR (olt.sales_order_parsed IS NOT NULL AND TRIM(olt.sales_order_parsed) != '') "
                "  OR EXISTS ("
                "    SELECT 1 FROM order_fulfillment_items ofi "
                "    WHERE ofi.order_lifecycle_id = olt.tracking_id "
                "      AND COALESCE(ofi.so_qty, 0) > 0"
                "  )"
                ") "
            )
            params: list[Any] = [ws]
            if user_id is not None:
                sql += "AND md.user_id = ? "
                params.append(int(user_id))
            if distributor_id is not None:
                sql += "AND olt.distributor_id = ? "
                params.append(int(distributor_id))
            sql += "ORDER BY distributor_name COLLATE NOCASE, olt.tracking_id DESC"
            tracking_rows = conn.execute(sql, tuple(params)).fetchall()

            pay_sql = (
                "SELECT id, distributor_id, tracking_id, order_ref_no, amount, "
                "payment_date, note, created_by, created_at "
                "FROM distributor_payment_entries WHERE workspace_id = ?"
            )
            pay_params: list[Any] = [ws]
            if distributor_id is not None:
                pay_sql += " AND distributor_id = ?"
                pay_params.append(int(distributor_id))
            if user_id is not None:
                # Only deposits on tracking rows the caller can see (owned distributors).
                pay_sql += (
                    " AND tracking_id IN ("
                    "SELECT olt2.tracking_id FROM order_lifecycle_tracking olt2 "
                    "JOIN master_distributors md2 ON olt2.distributor_id = md2.id "
                    "WHERE olt2.workspace_id = ? AND md2.user_id = ?"
                    ")"
                )
                pay_params.extend([ws, int(user_id)])
            pay_sql += " ORDER BY payment_date ASC, id ASC"
            pay_rows = conn.execute(pay_sql, tuple(pay_params)).fetchall()

            value_rows = conn.execute(
                "SELECT order_lifecycle_id, "
                "COALESCE(SUM(so_value), 0) AS so_sum, "
                "COALESCE(SUM(ci_value), 0) AS ci_sum "
                "FROM order_fulfillment_items WHERE workspace_id = ? "
                "GROUP BY order_lifecycle_id",
                (ws,),
            ).fetchall()

        value_by_tid = {
            int(r["order_lifecycle_id"]): (float(r["so_sum"] or 0), float(r["ci_sum"] or 0))
            for r in value_rows
        }
        pays_by_tid: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in pay_rows:
            tid = row["tracking_id"]
            if tid is None:
                continue
            pays_by_tid[int(tid)].append(
                {
                    "id": int(row["id"]),
                    "distributor_id": int(row["distributor_id"]),
                    "tracking_id": int(tid),
                    "order_ref_no": row["order_ref_no"],
                    "amount": round(float(row["amount"] or 0), 2),
                    "payment_date": row["payment_date"],
                    "note": row["note"] or "",
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                }
            )

        by_distributor: dict[int, dict[str, Any]] = {}
        for row in tracking_rows:
            tid = int(row["tracking_id"])
            did = int(row["distributor_id"])
            so_sum, ci_sum = value_by_tid.get(tid, (0.0, 0.0))
            tracking = {
                "sales_order_parsed": row["sales_order_parsed"],
                "commercial_invoice_parsed": row["commercial_invoice_parsed"],
            }
            bill = self._so_bill_amount_for_tracking(
                tracking, so_value_sum=so_sum, ci_value_sum=ci_sum
            )
            payments = pays_by_tid.get(tid, [])
            paid = round(sum(p["amount"] for p in payments), 2)
            outstanding = round(max(bill - paid, 0.0), 2)
            status = self._payment_status_from_amounts(bill, paid)
            order = {
                "tracking_id": tid,
                "order_ref_no": row["order_ref_no"],
                "distributor_id": did,
                "distributor_name": row["distributor_name"],
                "so_bill_amount": bill,
                "paid_amount": paid,
                "outstanding": outstanding,
                "payment_status": status,
                "created_at": row["created_at"],
                "payments": payments,
            }
            bucket = by_distributor.get(did)
            if bucket is None:
                bucket = {
                    "distributor_id": did,
                    "distributor_name": row["distributor_name"],
                    "so_bill_total": 0.0,
                    "paid_total": 0.0,
                    "outstanding_total": 0.0,
                    "orders": [],
                }
                by_distributor[did] = bucket
            bucket["orders"].append(order)
            bucket["so_bill_total"] = round(bucket["so_bill_total"] + bill, 2)
            bucket["paid_total"] = round(bucket["paid_total"] + paid, 2)
            bucket["outstanding_total"] = round(bucket["outstanding_total"] + outstanding, 2)

        distributors = sorted(
            by_distributor.values(),
            key=lambda d: (d["distributor_name"] or "").lower(),
        )
        return distributors

    def add_distributor_payment_entry(
        self,
        *,
        workspace_id: str,
        distributor_id: int,
        tracking_id: int,
        amount: float,
        payment_date: str,
        note: str | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        ws = (workspace_id or "default").strip() or "default"
        tracking = self.get_order_lifecycle_tracking(int(tracking_id), workspace_id=ws)
        if tracking is None:
            raise ValueError("Sales order tracking not found")
        if int(tracking.get("distributor_id") or 0) != int(distributor_id):
            raise ValueError("Distributor does not match this sales order")
        amt = float(amount)
        if amt <= 0:
            raise ValueError("Payment amount must be greater than zero")
        date_s = (payment_date or "").strip()
        if not date_s:
            raise ValueError("payment_date is required (YYYY-MM-DD)")
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO distributor_payment_entries (
                    workspace_id, distributor_id, tracking_id, order_ref_no,
                    amount, payment_date, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ws,
                    int(distributor_id),
                    int(tracking_id),
                    tracking.get("order_ref_no"),
                    round(amt, 2),
                    date_s,
                    (note or "").strip() or None,
                    created_by,
                    created_at,
                ),
            )
            entry_id = int(cur.lastrowid)
            conn.commit()

        self._sync_tracking_payment_status(int(tracking_id), workspace_id=ws)
        return {
            "id": entry_id,
            "distributor_id": int(distributor_id),
            "tracking_id": int(tracking_id),
            "order_ref_no": tracking.get("order_ref_no"),
            "amount": round(amt, 2),
            "payment_date": date_s,
            "note": (note or "").strip() or "",
            "created_by": created_by,
            "created_at": created_at,
        }

    def delete_distributor_payment_entry(
        self, entry_id: int, workspace_id: str
    ) -> dict[str, Any] | None:
        ws = (workspace_id or "default").strip() or "default"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, tracking_id, distributor_id, amount, payment_date "
                "FROM distributor_payment_entries WHERE id = ? AND workspace_id = ?",
                (int(entry_id), ws),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "DELETE FROM distributor_payment_entries WHERE id = ? AND workspace_id = ?",
                (int(entry_id), ws),
            )
            conn.commit()
        tracking_id = int(row["tracking_id"]) if row["tracking_id"] is not None else None
        if tracking_id is not None:
            self._sync_tracking_payment_status(tracking_id, workspace_id=ws)
        return {
            "id": int(row["id"]),
            "tracking_id": tracking_id,
            "distributor_id": int(row["distributor_id"]),
            "amount": float(row["amount"] or 0),
            "payment_date": row["payment_date"],
        }

    def _sync_tracking_payment_status(self, tracking_id: int, workspace_id: str) -> None:
        ws = (workspace_id or "default").strip() or "default"
        tracking = self.get_order_lifecycle_tracking(int(tracking_id), workspace_id=ws)
        if tracking is None:
            return
        with sqlite3.connect(self.db_path) as conn:
            sums = conn.execute(
                "SELECT COALESCE(SUM(so_value), 0), COALESCE(SUM(ci_value), 0) "
                "FROM order_fulfillment_items WHERE order_lifecycle_id = ? AND workspace_id = ?",
                (int(tracking_id), ws),
            ).fetchone()
            paid_row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM distributor_payment_entries "
                "WHERE tracking_id = ? AND workspace_id = ?",
                (int(tracking_id), ws),
            ).fetchone()
        so_sum = float(sums[0] or 0) if sums else 0.0
        ci_sum = float(sums[1] or 0) if sums else 0.0
        bill = self._so_bill_amount_for_tracking(
            tracking, so_value_sum=so_sum, ci_value_sum=ci_sum
        )
        paid = float(paid_row[0] or 0) if paid_row else 0.0
        status = self._payment_status_from_amounts(bill, paid)
        if status == "UNTRACKED":
            status = tracking.get("payment_status") or "DUE"
        self.update_order_lifecycle_stage(
            int(tracking_id), payment_status=status, workspace_id=ws
        )

    def get_latest_order_sheet(self, workspace_id: str = "default") -> dict[str, Any] | None:
        """
        Returns the most recently uploaded ACTIVE order sheet for this
        workspace — used to match a newly-arriving Sales Order/CI
        against "the latest order sheet" per the founder's
        requirement, when no specific order sheet was explicitly
        chosen.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM order_sheet_master WHERE workspace_id = ? AND is_active = 1 "
                "ORDER BY uploaded_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def link_sales_order_to_order_lifecycle(
        self,
        order_ref_no: str,
        distributor_id: int,
        sales_order_file_reference: str | None = None,
        sales_order_parsed: dict[str, Any] | None = None,
        workspace_id: str = "default",
    ) -> int:
        normalized_order_ref_no = (order_ref_no or "").strip()
        if not self._normalize_text(normalized_order_ref_no):
            raise ValueError("order_ref_no is required to link a sales order")

        sales_order_parsed_json = (
            json.dumps(sales_order_parsed, default=str)
            if sales_order_parsed is not None
            else None
        )
        # Prefer exact ref; else adopt a CI-first stub that printed this SO#.
        existing = self.find_mergeable_ci_only_tracking(
            normalized_order_ref_no, workspace_id=workspace_id
        )
        latest_order_sheet = self.get_latest_order_sheet(workspace_id=workspace_id)
        if existing is not None:
            with sqlite3.connect(self.db_path) as conn:
                # If CI was saved as CI-{invoice} but header had the real SO#,
                # rename the stub onto the real order_ref so SO/CI share one row.
                if (existing.get("order_ref_no") or "").strip() != normalized_order_ref_no:
                    conn.execute(
                        "UPDATE order_lifecycle_tracking SET order_ref_no = ? WHERE tracking_id = ?",
                        (normalized_order_ref_no, existing["tracking_id"]),
                    )
                sheet_id = existing.get("order_sheet_id")
                sheet_name = existing.get("order_sheet_name")
                if latest_order_sheet is not None and (
                    not sheet_id
                    or not sheet_name
                    or str(sheet_name).strip().upper().startswith("CI ONLY")
                ):
                    sheet_id = latest_order_sheet["id"]
                    sheet_name = latest_order_sheet["name"]
                conn.execute(
                    """
                    UPDATE order_lifecycle_tracking
                    SET distributor_id = ?,
                        sales_order_file_reference = ?,
                        sales_order_parsed = ?,
                        order_sheet_id = COALESCE(?, order_sheet_id),
                        order_sheet_name = COALESCE(?, order_sheet_name)
                    WHERE tracking_id = ?
                    """,
                    (
                        distributor_id,
                        sales_order_file_reference,
                        sales_order_parsed_json,
                        sheet_id,
                        sheet_name,
                        existing["tracking_id"],
                    ),
                )
                conn.commit()
            tracking_id = existing["tracking_id"]
        else:
            tracking_id = self.create_order_lifecycle_tracking(
                order_ref_no=normalized_order_ref_no,
                distributor_id=distributor_id,
                sales_order_file_reference=sales_order_file_reference,
                sales_order_parsed=sales_order_parsed_json,
                workspace_id=workspace_id,
            )
            if latest_order_sheet is not None:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE order_lifecycle_tracking SET order_sheet_id = ?, order_sheet_name = ? WHERE tracking_id = ?",
                        (latest_order_sheet["id"], latest_order_sheet["name"], tracking_id),
                    )
                    conn.commit()

        # Update fulfillment items based on parsed sales order rows (increment fulfilled_qty)
        try:
            if sales_order_parsed and isinstance(sales_order_parsed, dict):
                rows = sales_order_parsed.get("rows") or []
                if rows:
                    with sqlite3.connect(self.db_path) as conn:
                        for row in rows:
                            product = (row.get("product") or row.get("product_code") or "").strip()
                            qty_raw = row.get("quantity") or row.get("qty") or 0
                            try:
                                qty = int(float(str(qty_raw).replace(",", "")))
                            except Exception:
                                qty = 0
                            if not product or qty <= 0:
                                continue

                            existing_item = conn.execute(
                                "SELECT id, fulfilled_qty FROM order_fulfillment_items WHERE order_lifecycle_id = ? AND product_code = ? LIMIT 1",
                                (tracking_id, product),
                            ).fetchone()
                            if existing_item:
                                new_fulfilled = int(existing_item[1] or 0) + qty
                                conn.execute(
                                    "UPDATE order_fulfillment_items SET fulfilled_qty = ? WHERE id = ?",
                                    (new_fulfilled, int(existing_item[0])),
                                )
                            else:
                                conn.execute(
                                    "INSERT INTO order_fulfillment_items (order_lifecycle_id, product_code, ordered_qty, fulfilled_qty, created_at, workspace_id) VALUES (?, ?, ?, ?, ?, ?)",
                                    (
                                        tracking_id,
                                        product,
                                        0,
                                        qty,
                                        datetime.now(timezone.utc).isoformat(),
                                        workspace_id,
                                    ),
                                )
                        conn.commit()
        except Exception:
            # Avoid breaking the linking process if fulfillment update fails
            pass

        return tracking_id

    def find_mergeable_ci_only_tracking(
        self,
        order_ref_no: str,
        workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        """
        Find a CI-first tracking row that should receive a later Sales Order.

        1) Exact order_ref_no match (CI-only or already open stub).
        2) Orphan CI-only rows saved as CI-{invoice} whose parsed header
           still carries this Sales Order Number.
        """
        normalized = (order_ref_no or "").strip()
        if not normalized:
            return None

        def _has_ci(row: dict[str, Any]) -> bool:
            if str(row.get("commercial_invoice_file_reference") or "").strip():
                return True
            parsed = row.get("commercial_invoice_parsed")
            if isinstance(parsed, str) and parsed.strip():
                return True
            if isinstance(parsed, dict) and parsed:
                return True
            return False

        def _has_real_so(row: dict[str, Any]) -> bool:
            if str(row.get("sales_order_file_reference") or "").strip():
                return True
            parsed = row.get("sales_order_parsed")
            if isinstance(parsed, str) and parsed.strip():
                try:
                    parsed = json.loads(parsed)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            if not isinstance(parsed, dict) or not parsed:
                return False
            return bool(parsed.get("header") or parsed.get("rows") or parsed.get("line_items"))

        def _ci_header_order_ref(row: dict[str, Any]) -> str:
            parsed = row.get("commercial_invoice_parsed")
            if isinstance(parsed, str) and parsed.strip():
                try:
                    parsed = json.loads(parsed)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            if not isinstance(parsed, dict):
                return ""
            header = parsed.get("header") if isinstance(parsed.get("header"), dict) else {}
            for key in ("order_ref_no", "sales_order_number", "so_number", "order_no"):
                val = str(header.get(key) or "").strip()
                if val:
                    return val
            return ""

        exact = self.get_order_lifecycle_by_order_ref_no(normalized, workspace_id=workspace_id)
        if exact is not None:
            # Fresh SO onto empty ref OR merge onto CI-only / incomplete SO stub.
            if not _has_real_so(exact):
                return exact
            return exact  # already has SO — caller will update file (duplicate guard is elsewhere)

        # CI saved before SO under fallback ref CI-{invoice_no}.
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM order_lifecycle_tracking
                WHERE workspace_id = ?
                  AND commercial_invoice_file_reference IS NOT NULL
                  AND TRIM(commercial_invoice_file_reference) != ''
                  AND (
                        sales_order_file_reference IS NULL
                        OR TRIM(sales_order_file_reference) = ''
                      )
                ORDER BY tracking_id DESC
                LIMIT 200
                """,
                (workspace_id,),
            ).fetchall()
        for row in rows:
            item = dict(row)
            if _has_real_so(item):
                continue
            if not _has_ci(item):
                continue
            header_ref = _ci_header_order_ref(item)
            if header_ref and header_ref.strip() == normalized:
                return item
            # Also accept when parsed JSON blob mentions the SO# next to order_ref keys.
            blob = str(item.get("commercial_invoice_parsed") or "")
            if f'"order_ref_no": "{normalized}"' in blob or f'"order_ref_no":"{normalized}"' in blob:
                return item
        return None

    def recheck_all_order_lifecycle_discrepancies(
        self,
        tracking_id: int,
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        """Re-run SO vs CI (and Ordered) discrepancy flags for every item."""
        items = self.list_order_lifecycle_items_for_tracking(
            tracking_id, workspace_id=workspace_id
        )
        flagged = 0
        for item in items:
            item_id = item.get("id")
            if item_id is None:
                continue
            self._recheck_item_discrepancy(int(item_id), workspace_id=workspace_id)
            refreshed = self.get_order_lifecycle_item(int(item_id), workspace_id=workspace_id)
            if refreshed and refreshed.get("has_discrepancy"):
                flagged += 1
        return {
            "tracking_id": tracking_id,
            "item_count": len(items),
            "discrepancy_count": flagged,
            "has_discrepancy": flagged > 0,
        }

    def set_order_lifecycle_drive_file_id(
        self,
        tracking_id: int,
        kind: str,
        drive_file_id: str,
        workspace_id: str = "default",
    ) -> None:
        col = (
            "sales_order_drive_file_id"
            if str(kind).lower() in ("so", "sales_order")
            else "commercial_invoice_drive_file_id"
        )
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_column_exists(conn, "order_lifecycle_tracking", col, "TEXT")
            conn.execute(
                f"UPDATE order_lifecycle_tracking SET {col} = ? WHERE tracking_id = ? AND workspace_id = ?",
                (drive_file_id, tracking_id, workspace_id),
            )
            conn.commit()

    def _write_ci_onto_tracking(
        self,
        tracking_id: int,
        commercial_invoice_file_reference: str | None,
        commercial_invoice_parsed_json: str | None,
        commercial_invoice_date: str | None,
        *,
        fallback_date: str | None = None,
        order_sheet_id: Any = None,
        order_sheet_name: str | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE order_lifecycle_tracking
                SET commercial_invoice_file_reference = ?,
                    commercial_invoice_parsed = ?,
                    commercial_invoice_date = COALESCE(?, commercial_invoice_date, ?)
                WHERE tracking_id = ?
                """,
                (
                    commercial_invoice_file_reference,
                    commercial_invoice_parsed_json,
                    commercial_invoice_date,
                    fallback_date,
                    tracking_id,
                ),
            )
            if order_sheet_id is not None or order_sheet_name:
                conn.execute(
                    """
                    UPDATE order_lifecycle_tracking
                    SET order_sheet_id = COALESCE(?, order_sheet_id),
                        order_sheet_name = COALESCE(?, order_sheet_name)
                    WHERE tracking_id = ?
                    """,
                    (order_sheet_id, order_sheet_name, tracking_id),
                )
            conn.commit()

    def _spawn_ci_sibling_tracking(
        self,
        template: dict[str, Any],
        *,
        commercial_invoice_file_reference: str | None,
        commercial_invoice_parsed_json: str | None,
        commercial_invoice_date: str | None,
        workspace_id: str,
    ) -> int:
        """New tracking for a second CI on the same SO — never overwrite the first invoice."""
        tracking_id = self.create_order_lifecycle_tracking(
            order_ref_no=str(template.get("order_ref_no") or ""),
            distributor_id=int(template["distributor_id"]),
            commercial_invoice_date=commercial_invoice_date,
            transit_status="INVOICED",
            workspace_id=workspace_id,
        )
        self._write_ci_onto_tracking(
            tracking_id,
            commercial_invoice_file_reference,
            commercial_invoice_parsed_json,
            commercial_invoice_date,
            order_sheet_id=template.get("order_sheet_id"),
            order_sheet_name=template.get("order_sheet_name"),
        )
        return tracking_id

    def link_commercial_invoice_to_order_lifecycle(
        self,
        order_ref_no: str,
        commercial_invoice_file_reference: str | None = None,
        commercial_invoice_parsed: dict[str, Any] | None = None,
        commercial_invoice_date: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        normalized_order_ref_no = (order_ref_no or "").strip()
        if not self._normalize_text(normalized_order_ref_no):
            raise ValueError("order_ref_no is required to link a commercial invoice")

        commercial_invoice_parsed_json = (
            json.dumps(commercial_invoice_parsed, default=str)
            if commercial_invoice_parsed is not None
            else None
        )
        incoming_invoice = (self._extract_ci_invoice_no(commercial_invoice_parsed) or "").strip()
        rows = self.list_order_lifecycle_by_order_ref_no(
            normalized_order_ref_no, workspace_id=workspace_id
        )
        if not rows:
            raise ValueError(
                f"No existing order lifecycle record found for order_ref_no '{normalized_order_ref_no}'"
            )

        target: dict[str, Any] | None = None
        if incoming_invoice:
            for row in rows:
                if self._invoice_no_from_lifecycle_row(row) == incoming_invoice:
                    target = row
                    break
        if target is None:
            for row in rows:
                if not self._lifecycle_has_ci(row):
                    target = row
                    break
        if target is None:
            # Same SO, different invoice (e.g. 9337 DBSET vs 9346 SBSET) — keep both.
            return self._spawn_ci_sibling_tracking(
                rows[0],
                commercial_invoice_file_reference=commercial_invoice_file_reference,
                commercial_invoice_parsed_json=commercial_invoice_parsed_json,
                commercial_invoice_date=commercial_invoice_date,
                workspace_id=workspace_id,
            )

        self._write_ci_onto_tracking(
            int(target["tracking_id"]),
            commercial_invoice_file_reference,
            commercial_invoice_parsed_json,
            commercial_invoice_date,
            fallback_date=target.get("commercial_invoice_date"),
        )
        return int(target["tracking_id"])

    def save_ci_only_order_lifecycle(
        self,
        order_ref_no: str,
        distributor_id: int,
        commercial_invoice_file_reference: str | None = None,
        commercial_invoice_parsed: dict[str, Any] | None = None,
        commercial_invoice_date: str | None = None,
        workspace_id: str = "default",
    ) -> int:
        """
        Persist a Commercial Invoice without a prior Sales Order upload.
        Uses the CI's Sales Order Number as order_ref_no when present so a
        later SO upload for the same ref can merge into this tracking row.
        """
        normalized_order_ref_no = (order_ref_no or "").strip()
        if not self._normalize_text(normalized_order_ref_no):
            raise ValueError("order_ref_no is required to save a CI-only tracking record")
        if distributor_id is None:
            raise ValueError("distributor_id is required to save a CI-only tracking record")

        existing = self.get_order_lifecycle_by_order_ref_no(
            normalized_order_ref_no, workspace_id=workspace_id
        )
        if existing is not None:
            # Same SO: attach onto an empty row, or spawn a sibling when
            # this invoice_no is a different CI (never overwrite 9337 with 9346).
            return self.link_commercial_invoice_to_order_lifecycle(
                order_ref_no=normalized_order_ref_no,
                commercial_invoice_file_reference=commercial_invoice_file_reference,
                commercial_invoice_parsed=commercial_invoice_parsed,
                commercial_invoice_date=commercial_invoice_date,
                workspace_id=workspace_id,
            )

        commercial_invoice_parsed_json = (
            json.dumps(commercial_invoice_parsed, default=str)
            if commercial_invoice_parsed is not None
            else None
        )
        tracking_id = self.create_order_lifecycle_tracking(
            order_ref_no=normalized_order_ref_no,
            distributor_id=int(distributor_id),
            commercial_invoice_date=commercial_invoice_date,
            transit_status="INVOICED",
            workspace_id=workspace_id,
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE order_lifecycle_tracking
                SET commercial_invoice_file_reference = ?,
                    commercial_invoice_parsed = ?,
                    commercial_invoice_date = COALESCE(?, commercial_invoice_date)
                WHERE tracking_id = ?
                """,
                (
                    commercial_invoice_file_reference,
                    commercial_invoice_parsed_json,
                    commercial_invoice_date,
                    tracking_id,
                ),
            )
            conn.commit()
        return tracking_id

    def save_pending_filled_order_items(
        self, distributor_id: int, workspace_id: str, items: list[dict[str, Any]]
    ) -> int:
        """
        Stores a Filled Order's parsed items (name/qty/value) as
        "pending" — not yet linked to any order_lifecycle_tracking
        record, since the order_ref_no only becomes known once the
        matching Sales Order PDF is later uploaded. Consumed by
        get_and_consume_pending_filled_order_items() once the FIRST
        SO arrives for the same distributor.

        ALSO writes each item to filled_order_item_baselines, a
        PERMANENT per-item lookup table (keyed by distributor_id +
        item_key) — this is what lets a LATER Sales Order for a
        DIFFERENT item (e.g. "Blumen", arriving under a different
        order_ref_no/tracking_id than the "Aster" SO that consumed
        the pending queue first) still correctly populate its own
        "Ordered Qty/Value" baseline.
        """
        uploaded_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO distributor_order_uploads
                    (verification_session_id, distributor_name, stage_key, file_type,
                     filename, file_path, uploaded_at, metadata, distributor_id, workspace_id, linked_tracking_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(uuid.uuid4()), None, "filled_order_pending_items", "filled_order_items",
                    "pending_items.json", "", uploaded_at, json.dumps(items, default=str),
                    distributor_id, workspace_id,
                ),
            )
            for item in items:
                item_key = item.get("item_key")
                if not item_key:
                    continue
                conn.execute(
                    """
                    INSERT INTO filled_order_item_baselines
                        (workspace_id, distributor_id, item_key, item_name, ordered_qty, ordered_value, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, distributor_id, item_key) DO UPDATE SET
                        item_name = excluded.item_name,
                        ordered_qty = excluded.ordered_qty,
                        ordered_value = excluded.ordered_value
                    """,
                    (
                        workspace_id, distributor_id, item_key, item.get("item_name"),
                        item.get("qty"), item.get("value"), uploaded_at,
                    ),
                )
            conn.commit()
            return int(cursor.lastrowid)

    def get_and_consume_pending_filled_order_items(
        self, distributor_id: int, workspace_id: str
    ) -> list[dict[str, Any]] | None:
        """
        Returns the most recent NOT-YET-LINKED Filled Order items for
        this distributor (if any), and marks that row consumed so it
        won't be re-applied to a later, unrelated Sales Order.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, metadata FROM distributor_order_uploads
                WHERE stage_key = 'filled_order_pending_items'
                  AND distributor_id = ? AND workspace_id = ? AND linked_tracking_id IS NULL
                ORDER BY uploaded_at DESC LIMIT 1
                """,
                (distributor_id, workspace_id),
            ).fetchone()
            if row is None:
                return None
            upload_id, metadata_json = row
            conn.execute(
                "UPDATE distributor_order_uploads SET linked_tracking_id = -1 WHERE id = ?",
                (upload_id,),
            )
            conn.commit()
        try:
            return json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            return None

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

    def _build_fts_prefix_query(self, text: str) -> str:
        """
        Converts free text into an FTS5 query. Single tokens use exact
        match OR prefix (rahul OR rahul*) so typeahead still works but
        unrelated fuzzy tiers are not needed as often.
        """
        tokens = [t for t in re.findall(r"[\w]+", text) if t]
        if not tokens:
            return text
        if len(tokens) == 1 and len(tokens[0]) >= 3:
            token = tokens[0]
            return f'"{token}" OR {token}*'
        return " ".join(f"{token}*" for token in tokens)

    def _extract_ci_invoice_no(self, parsed: Any) -> str | None:
        """Pull CI invoice number from commercial_invoice_parsed JSON (or raw string)."""
        data = parsed
        if isinstance(parsed, str):
            text = parsed.strip()
            if not text:
                return None
            try:
                data = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                # Raw string may still contain the number; leave extraction to LIKE.
                return None
        if not isinstance(data, dict):
            return None
        candidates: list[Any] = [
            data.get("invoice_no"),
            data.get("invoice_number"),
            data.get("ci_number"),
            data.get("ci_no"),
            data.get("document_number"),
        ]
        header = data.get("header") or data.get("meta") or data.get("invoice")
        if isinstance(header, dict):
            candidates.extend(
                [
                    header.get("invoice_no"),
                    header.get("invoice_number"),
                    header.get("ci_number"),
                    header.get("ci_no"),
                    header.get("document_number"),
                ]
            )
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return None

    def _global_search_primary_name(self, category: str, record: dict[str, Any]) -> str:
        if category == "distributors":
            return str(
                record.get("firm_name")
                or record.get("firm_nick_name")
                or record.get("contact_person")
                or ""
            )
        if category == "retailers":
            return str(record.get("name") or record.get("contact_person") or "")
        if category == "orders":
            return str(
                record.get("order_ref_no")
                or record.get("invoice_no")
                or ""
            )
        if category in ("stock", "article_master"):
            return str(record.get("brand") or record.get("product") or "")
        return ""

    def _global_search_record_fields(
        self, category: str, record: dict[str, Any]
    ) -> list[Any]:
        if category == "distributors":
            return [
                record.get("firm_name"),
                record.get("firm_nick_name"),
                record.get("contact_person"),
                record.get("buyer_code"),
                record.get("phone_number"),
                record.get("gst_no"),
                record.get("city"),
                record.get("zone"),
                record.get("region"),
                record.get("address"),
            ]
        if category == "retailers":
            return [
                record.get("name"),
                record.get("contact_person"),
                record.get("distributor_name"),
                record.get("distributor_nick_name"),
                record.get("phone_number"),
                record.get("gst_no"),
                record.get("city"),
                record.get("address"),
            ]
        if category == "orders":
            return [
                record.get("order_ref_no"),
                record.get("invoice_no"),
                record.get("distributor_name"),
                record.get("transit_status"),
                record.get("payment_status"),
            ]
        if category in ("stock", "article_master"):
            return [
                record.get("brand"),
                record.get("product"),
                record.get("colors"),
                record.get("size"),
                record.get("category"),
                record.get("item_key"),
            ]
        return [record.get("content")]

    def _global_search_expand_terms(
        self, query: str, workspace_id: str | None = None
    ) -> list[str]:
        """Expand nicknames/aliases (bnd → Bernina) and Shri/Shree/Sri spelling variants."""
        raw = (query or "").strip()
        if not raw:
            return []

        terms: list[str] = list(self._global_search_party_name_variants(raw))
        canonical = self._canonicalize_known_master_name(raw)
        if canonical and canonical.lower() != raw.lower():
            terms.append(canonical)
            terms.extend(self._global_search_party_name_variants(canonical))

        nick = raw.lower()
        folded_nick = self._party_name_fold(raw)
        with sqlite3.connect(self.db_path) as conn:
            sql = (
                "SELECT firm_name, name, firm_nick_name FROM master_distributors "
                "WHERE LOWER(TRIM(COALESCE(firm_nick_name, ''))) = ? "
                "OR LOWER(TRIM(COALESCE(firm_nick_name, ''))) = ?"
            )
            params: list[Any] = [nick, folded_nick]
            if workspace_id:
                sql += " AND workspace_id = ?"
                params.append(workspace_id)
            for firm_name, name, firm_nick_name in conn.execute(sql, params).fetchall():
                for value in (firm_name, name, firm_nick_name):
                    text = str(value or "").strip()
                    if text:
                        terms.append(text)

        unique: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(term)
        return unique

    def _supplement_parties_by_folded_name(
        self,
        results: dict[str, list[dict[str, Any]]],
        query: str,
        search_terms: list[str],
        workspace_id: str | None = None,
    ) -> None:
        """Merge distributors/retailers matched via Shri/Shree/Sri folding + compact keys."""
        compacts = {
            self._party_name_compact(t)
            for t in ([query] + list(search_terms))
            if len(self._party_name_compact(t)) >= 4
        }
        folds = {
            self._party_name_fold(t)
            for t in ([query] + list(search_terms))
            if len(self._party_name_fold(t)) >= 4
        }
        if not compacts and not folds:
            return

        query_phon = self._party_name_phonetic(query)

        def _party_hit(text: str) -> bool:
            fold = self._party_name_fold(text)
            compact = self._party_name_compact(text)
            phon = self._party_name_phonetic(text)
            if any(f and f in fold for f in folds):
                return True
            if any(c and c in compact for c in compacts):
                return True
            # Indic spelling variants: nitin/niten, sunil/suneel, raman/roman
            if query_phon and len(query_phon) >= 3:
                if phon == query_phon:
                    return True
                for word in re.findall(r"[a-z0-9]+", fold):
                    if len(word) >= 3 and self._party_name_phonetic(word) == query_phon:
                        return True
            return False

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            dist_sql = (
                "SELECT id, distributor_id AS buyer_code, COALESCE(firm_name, name) AS firm_name, "
                "firm_nick_name, name AS contact_person, contact_person_role, phone_number, location AS city, "
                "gst_no, zone, region, address FROM master_distributors"
            )
            dist_params: list[Any] = []
            if workspace_id:
                dist_sql += " WHERE workspace_id = ?"
                dist_params.append(workspace_id)
            dist_rows = conn.execute(dist_sql, dist_params).fetchall()

            seen_dist = {
                int(d["id"])
                for d in results.get("distributors") or []
                if d.get("id") is not None
            }
            for row in dist_rows:
                rid = int(row["id"])
                if rid in seen_dist:
                    continue
                blob = " ".join(
                    str(row[k] or "")
                    for k in ("firm_name", "firm_nick_name", "contact_person")
                )
                if not _party_hit(blob):
                    continue
                results.setdefault("distributors", []).append(dict(row))
                seen_dist.add(rid)

            retail_sql = (
                "SELECT mr.id, mr.name, mr.contact_person, mr.distributor_id, "
                "mr.phone_number, mr.location AS city, mr.gst_no, mr.address, "
                "COALESCE(md.firm_name, md.name, 'Unassigned') AS distributor_name, "
                "COALESCE(md.firm_nick_name, '') AS distributor_nick_name "
                "FROM master_retailers mr "
                "LEFT JOIN master_distributors md ON mr.distributor_id = md.id"
            )
            retail_params: list[Any] = []
            if workspace_id:
                retail_sql += " WHERE mr.workspace_id = ?"
                retail_params.append(workspace_id)
            retail_rows = conn.execute(retail_sql, retail_params).fetchall()

            seen_ret = {
                int(r["id"])
                for r in results.get("retailers") or []
                if r.get("id") is not None
            }
            for row in retail_rows:
                rid = int(row["id"])
                if rid in seen_ret:
                    continue
                blob = " ".join(
                    str(row[k] or "")
                    for k in ("name", "contact_person", "distributor_name", "distributor_nick_name")
                )
                if not _party_hit(blob):
                    continue
                item = dict(row)
                item.pop("distributor_id", None)
                results.setdefault("retailers", []).append(item)
                seen_ret.add(rid)

    def _global_search_record_matches(
        self,
        query: str,
        category: str,
        record: dict[str, Any],
        terms: list[str] | None = None,
    ) -> bool:
        candidates = [t for t in (terms or [query]) if str(t or "").strip()]
        if not candidates:
            return False

        query_compacts = {
            self._party_name_compact(t)
            for t in candidates
            if len(self._party_name_compact(t)) >= 4
        }
        query_folds = {
            self._party_name_fold(t)
            for t in candidates
            if self._party_name_fold(t)
        }

        for term in candidates:
            normalized = str(term).strip().lower()
            if not normalized:
                continue
            for value in self._global_search_record_fields(category, record):
                if value is None:
                    continue
                text = str(value).lower()
                if normalized in text:
                    return True
                fold_text = self._party_name_fold(value)
                if any(f and f in fold_text for f in query_folds):
                    return True
                compact_text = self._party_name_compact(value)
                if any(c and c in compact_text for c in query_compacts):
                    return True

            primary = self._global_search_primary_name(category, record).lower()
            if primary:
                if len(normalized) >= 3 and primary.startswith(normalized):
                    return True
                if fuzz.ratio(normalized, primary) >= 90:
                    return True
                primary_compact = self._party_name_compact(primary)
                if any(
                    c and (c in primary_compact or primary_compact in c)
                    for c in query_compacts
                ):
                    return True
                if fuzz.partial_ratio(
                    self._party_name_fold(normalized), self._party_name_fold(primary)
                ) >= 88:
                    return True
        return False

    @staticmethod
    def _parse_mrp_price_range(query: str) -> tuple[float, float] | None:
        """Detect MRP band queries: '1000-2000', '0 - 3000', '₹1500 to 2500'."""
        q = (query or "").strip()
        if not q:
            return None
        cleaned = re.sub(r"(?i)\b(rs\.?|inr|mrp|price)\b", " ", q)
        cleaned = cleaned.replace("₹", " ").replace("–", "-").replace("—", "-")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        m = re.match(
            r"^([\d,.]+)\s*(?:-|to|/|~)\s*([\d,.]+)$",
            cleaned,
            re.IGNORECASE,
        )
        if not m:
            return None

        def _num(raw: str) -> float | None:
            text = (raw or "").replace(",", "").strip()
            try:
                return float(text)
            except ValueError:
                return None

        lo = _num(m.group(1))
        hi = _num(m.group(2))
        if lo is None or hi is None:
            return None
        if lo < 0 or hi < 0:
            return None
        if lo > hi:
            lo, hi = hi, lo
        # Guard: absurd ceilings are not MRP bands (avoid SO-like digit pairs).
        if hi > 1_000_000:
            return None
        return lo, hi

    @staticmethod
    def _query_looks_like_so_ci_number(query: str) -> bool:
        """SO/CI search only — party/brand names like 'aster' must not open orders."""
        q = (query or "").strip()
        if len(q) < 2:
            return False
        # "1000-2000" is an MRP band, not an SO/CI number.
        if CentralizedDB._parse_mrp_price_range(q) is not None:
            return False
        if not any(ch.isdigit() for ch in q):
            return False
        if re.match(r"^(so|ci|inv|invoice)[\s\-_/]*\d", q, re.I):
            return True
        compact = re.sub(r"[\s\-_/\\.]", "", q)
        if not compact:
            return False
        digits = sum(1 for ch in compact if ch.isdigit())
        if digits >= 4:
            return True
        if digits >= 3 and (digits / len(compact)) >= 0.5:
            return True
        return False

    def _search_articles_by_mrp_range(
        self,
        lo: float,
        hi: float,
        user_id: int | None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active Article Master SKUs whose MRP sits in [lo, hi],
        optionally narrowed to one category ("bedsheet"/"towel"/...) —
        Ask Nexora's "1000 se 2500 ke beech ki bedsheet dikhao"."""
        if user_id is None:
            return []
        sql = (
            "SELECT id, category, brand, size, product_type, mrp, ptr, ex_mill_price, item_key "
            "FROM article_master "
            "WHERE user_id = ? AND is_active = 1 "
            "AND CAST(COALESCE(mrp, 0) AS REAL) >= ? "
            "AND CAST(COALESCE(mrp, 0) AS REAL) <= ?"
        )
        params: list[Any] = [user_id, float(lo), float(hi)]
        if category:
            sql += " AND LOWER(COALESCE(category, '')) = ?"
            params.append(category.lower())
        sql += (
            " ORDER BY CAST(COALESCE(mrp, 0) AS REAL) ASC, "
            "LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, '')) LIMIT 120"
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(r) for r in rows]

    def _search_articles_by_size(
        self,
        size_code: str,
        user_id: int | None,
    ) -> list[dict[str, Any]]:
        """Every active Article Master SKU whose size matches size_code,
        across all brands — Ask Nexora's bare "single bedsheet"/"double
        bedsheet" (no brand named) instead of the old brand-required lookup."""
        if user_id is None or not size_code:
            return []
        sql = (
            "SELECT id, category, brand, size, product_type, mrp, ptr, ex_mill_price, "
            "item_key, extra_attributes "
            "FROM article_master "
            "WHERE user_id = ? AND is_active = 1 "
            "ORDER BY LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, ''))"
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, [user_id]).fetchall()
            except sqlite3.OperationalError:
                return []
        target = normalize_product_code(size_code)
        return [
            dict(r) for r in rows
            if normalize_product_code(r["size"] or "") == target
        ]

    def _filter_global_search_results(
        self,
        query: str,
        results: dict[str, list[dict[str, Any]]],
        terms: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        search_terms = terms or [query]
        filtered: dict[str, list[dict[str, Any]]] = {}
        for category, rows in results.items():
            if category in ("verifications", "visit_logs", "analytics"):
                lowered_terms = [str(t).strip().lower() for t in search_terms if str(t).strip()]
                filtered[category] = [
                    row
                    for row in rows
                    if any(t in str(row.get("content", "")).lower() for t in lowered_terms)
                ]
                continue
            # Article Master SQL already matches brand aliases + extra_attributes.
            # Post-filter fields omit those, so re-filtering would drop valid hits
            # (e.g. search "bluman" → brand "Bluemen", or "digital" → Print Style).
            if category == "article_master":
                filtered[category] = list(rows)
                continue
            filtered[category] = [
                row
                for row in rows
                if self._global_search_record_matches(
                    query, category, row, terms=search_terms
                )
            ]
        return filtered

    def global_search(self, query: str, workspace_id: str | None = None, user_id: int | None = None) -> dict[str, Any]:
        normalized_query = (query or "").strip()
        empty_results = {
            "distributors": [],
            "retailers": [],
            "orders": [],
            "stock": [],
            "article_master": [],
            "verifications": [],
            "visit_logs": [],
            "analytics": [],
        }
        if not normalized_query:
            return {
                "query": normalized_query,
                "results": dict(empty_results),
            }

        # Home / global search: "1000 - 2000" → SKUs in that MRP band (Aster, Cardinal, …).
        price_range = self._parse_mrp_price_range(normalized_query)
        if price_range is not None:
            lo, hi = price_range
            articles = self._search_articles_by_mrp_range(lo, hi, user_id)
            return {
                "query": normalized_query,
                "price_range": {"min": lo, "max": hi, "field": "mrp"},
                "results": {
                    **empty_results,
                    "article_master": articles,
                },
            }

        search_terms = self._global_search_expand_terms(normalized_query, workspace_id)
        fts_parts = [self._build_fts_prefix_query(term) for term in search_terms]
        fts_query = " OR ".join(f"({part})" for part in fts_parts if part)
        so_ci_query = self._query_looks_like_so_ci_number(normalized_query)
        with sqlite3.connect(self.db_path) as conn:
            try:
                if workspace_id:
                    rows = conn.execute(
                        "SELECT content, category, source_id, source_table FROM global_search_index WHERE global_search_index MATCH ? AND workspace_id = ? ORDER BY rank",
                        (fts_query, workspace_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT content, category, source_id, source_table FROM global_search_index WHERE global_search_index MATCH ? ORDER BY rank",
                        (fts_query,),
                    ).fetchall()
            except sqlite3.OperationalError:
                # Malformed FTS query syntax (e.g. a bare special
                # character) — fall through to the fuzzy fallback
                # below instead of raising.
                rows = []

        results: dict[str, list[dict[str, Any]]] = {
            "distributors": [],
            "retailers": [],
            "orders": [],
            "stock": [],
            "article_master": [],
            "verifications": [],
            "visit_logs": [],
            "analytics": [],
        }

        # Group matched ids by category, deduplicating — the raw FTS
        # match list can contain the same underlying row more than
        # once (e.g. it was indexed twice across refreshes).
        ids_by_category: dict[str, set[int]] = {}
        for _content, category, source_id, _source_table in rows:
            ids_by_category.setdefault(category, set()).add(source_id)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if ids_by_category.get("distributors"):
                placeholders = ",".join("?" * len(ids_by_category["distributors"]))
                dist_sql = (
                    f"SELECT id, distributor_id AS buyer_code, COALESCE(firm_name, name) AS firm_name, "
                    f"firm_nick_name, name AS contact_person, contact_person_role, phone_number, "
                    f"location AS city, gst_no, zone, region, address FROM master_distributors "
                    f"WHERE id IN ({placeholders})"
                )
                dist_params: list[Any] = list(ids_by_category["distributors"])
                if user_id is not None:
                    dist_sql += " AND user_id = ?"
                    dist_params.append(user_id)
                dist_rows = conn.execute(dist_sql, tuple(dist_params)).fetchall()
                results["distributors"] = [dict(r) for r in dist_rows]

                # Also surface retailers linked to matched distributors
                # (e.g. search "bernina" / "bnd" should list Bernina's retailers).
                owned_dist_ids = [int(r["id"]) for r in dist_rows if r["id"] is not None]
                if owned_dist_ids:
                    placeholders = ",".join("?" * len(owned_dist_ids))
                    linked_sql = (
                        f"SELECT mr.id, mr.name, mr.contact_person, "
                        f"COALESCE(md.firm_name, md.name, 'Unassigned') AS distributor_name, "
                        f"COALESCE(md.firm_nick_name, '') AS distributor_nick_name, "
                        f"mr.phone_number, mr.location AS city, mr.gst_no, mr.address "
                        f"FROM master_retailers mr "
                        f"LEFT JOIN master_distributors md ON mr.distributor_id = md.id "
                        f"WHERE mr.distributor_id IN ({placeholders})"
                    )
                    linked_params: list[Any] = list(owned_dist_ids)
                    if user_id is not None:
                        linked_sql += " AND mr.user_id = ?"
                        linked_params.append(user_id)
                    linked_retail_rows = conn.execute(
                        linked_sql, tuple(linked_params)
                    ).fetchall()
                    for row in linked_retail_rows:
                        ids_by_category.setdefault("retailers", set()).add(int(row["id"]))

            if ids_by_category.get("retailers"):
                placeholders = ",".join("?" * len(ids_by_category["retailers"]))
                retail_sql = (
                    f"SELECT mr.id, mr.name, mr.contact_person, "
                    f"COALESCE(md.firm_name, md.name, 'Unassigned') AS distributor_name, "
                    f"COALESCE(md.firm_nick_name, '') AS distributor_nick_name, "
                    f"mr.phone_number, mr.location AS city, mr.gst_no, mr.address "
                    f"FROM master_retailers mr LEFT JOIN master_distributors md ON mr.distributor_id = md.id "
                    f"WHERE mr.id IN ({placeholders})"
                )
                retail_params: list[Any] = list(ids_by_category["retailers"])
                if user_id is not None:
                    retail_sql += " AND mr.user_id = ?"
                    retail_params.append(user_id)
                retail_rows = conn.execute(retail_sql, tuple(retail_params)).fetchall()
                results["retailers"] = [dict(r) for r in retail_rows]

            if so_ci_query and ids_by_category.get("orders"):
                placeholders = ",".join("?" * len(ids_by_category["orders"]))
                order_sql = (
                    f"SELECT olt.tracking_id, olt.order_ref_no, "
                    f"COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name, "
                    f"olt.transit_status, olt.payment_status, "
                    f"CASE WHEN olt.sales_order_file_reference IS NOT NULL "
                    f"AND TRIM(olt.sales_order_file_reference) != '' THEN 1 ELSE 0 END AS has_sales_order, "
                    f"CASE WHEN olt.commercial_invoice_file_reference IS NOT NULL "
                    f"AND TRIM(olt.commercial_invoice_file_reference) != '' THEN 1 ELSE 0 END AS has_commercial_invoice, "
                    f"olt.commercial_invoice_parsed, "
                    f"("
                    f"  SELECT pd.document_number FROM processed_documents pd "
                    f"  WHERE pd.tracking_id = olt.tracking_id AND pd.document_type = 'CI' "
                    f"  LIMIT 1"
                    f") AS invoice_no "
                    f"FROM order_lifecycle_tracking olt "
                    f"LEFT JOIN master_distributors md ON olt.distributor_id = md.id "
                    f"WHERE olt.tracking_id IN ({placeholders})"
                )
                order_params: list[Any] = list(ids_by_category["orders"])
                if user_id is not None:
                    order_sql += " AND md.user_id = ?"
                    order_params.append(user_id)
                order_rows = conn.execute(order_sql, tuple(order_params)).fetchall()
                enriched_orders = []
                for r in order_rows:
                    item = dict(r)
                    parsed = item.pop("commercial_invoice_parsed", None)
                    if not item.get("invoice_no"):
                        item["invoice_no"] = self._extract_ci_invoice_no(parsed)
                    item["has_sales_order"] = bool(item.get("has_sales_order"))
                    item["has_commercial_invoice"] = bool(item.get("has_commercial_invoice"))
                    enriched_orders.append(item)
                results["orders"] = enriched_orders

            # Direct SO / CI number lookup only — never match brand text inside PDF JSON
            # or distributor names (e.g. search "aster" must not open SO/CI).
            if so_ci_query:
                like_query = f"%{normalized_query.lower()}%"
                order_like_sql = (
                    "SELECT DISTINCT olt.tracking_id, olt.order_ref_no, "
                    "COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name, "
                    "olt.transit_status, olt.payment_status, "
                    "CASE WHEN olt.sales_order_file_reference IS NOT NULL "
                    "AND TRIM(olt.sales_order_file_reference) != '' THEN 1 ELSE 0 END AS has_sales_order, "
                    "CASE WHEN olt.commercial_invoice_file_reference IS NOT NULL "
                    "AND TRIM(olt.commercial_invoice_file_reference) != '' THEN 1 ELSE 0 END AS has_commercial_invoice, "
                    "olt.commercial_invoice_parsed, "
                    "("
                    "  SELECT pd.document_number FROM processed_documents pd "
                    "  WHERE pd.tracking_id = olt.tracking_id AND pd.document_type = 'CI' "
                    "  LIMIT 1"
                    ") AS invoice_no "
                    "FROM order_lifecycle_tracking olt "
                    "LEFT JOIN master_distributors md ON olt.distributor_id = md.id "
                    "LEFT JOIN processed_documents pd ON pd.tracking_id = olt.tracking_id "
                    "WHERE ("
                    "LOWER(COALESCE(olt.order_ref_no, '')) LIKE ? "
                    "OR LOWER(COALESCE(pd.document_number, '')) LIKE ?"
                    ")"
                )
                order_like_params: list[Any] = [like_query, like_query]
                if workspace_id:
                    order_like_sql += " AND olt.workspace_id = ?"
                    order_like_params.append(workspace_id)
                if user_id is not None:
                    order_like_sql += " AND md.user_id = ?"
                    order_like_params.append(user_id)
                order_like_sql += " LIMIT 50"
                order_like_rows = conn.execute(order_like_sql, tuple(order_like_params)).fetchall()
                if order_like_rows:
                    seen_order_ids = {
                        int(o["tracking_id"])
                        for o in results["orders"]
                        if o.get("tracking_id") is not None
                    }
                    for r in order_like_rows:
                        item = dict(r)
                        tracking_id = item.get("tracking_id")
                        if tracking_id is not None and int(tracking_id) in seen_order_ids:
                            continue
                        parsed = item.pop("commercial_invoice_parsed", None)
                        if not item.get("invoice_no"):
                            item["invoice_no"] = self._extract_ci_invoice_no(parsed)
                        item["has_sales_order"] = bool(item.get("has_sales_order"))
                        item["has_commercial_invoice"] = bool(item.get("has_commercial_invoice"))
                        results["orders"].append(item)
                        if tracking_id is not None:
                            seen_order_ids.add(int(tracking_id))
            else:
                results["orders"] = []

            if ids_by_category.get("stock"):
                placeholders = ",".join("?" * len(ids_by_category["stock"]))
                stock_rows = conn.execute(
                    f"SELECT id, brand, product, colors, size FROM article_master_v2 "
                    f"WHERE id IN ({placeholders})",
                    tuple(ids_by_category["stock"]),
                ).fetchall()
                results["stock"] = [dict(r) for r in stock_rows]

        # Verifications / visit_logs / analytics keep the simpler
        # content-string shape for now (lower priority, less
        # frequently searched than parties/orders/stock).
        for content, category, source_id, source_table in rows:
            if category in ("verifications", "visit_logs", "analytics"):
                results[category].append(
                    {"source_id": source_id, "source_table": source_table, "content": content}
                )

        # Keep the output shape stable and include a fallback partial match for non-FTS cases.
        if not any(results.values()):
            like_query = f"%{normalized_query.lower()}%"

            dist_sql = (
                "SELECT name, gst_no, zone, region, location, address, phone_number, id FROM master_distributors "
                "WHERE (LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(firm_name, '')) LIKE ? "
                "OR LOWER(COALESCE(firm_nick_name, '')) LIKE ? OR LOWER(COALESCE(gst_no, '')) LIKE ? "
                "OR LOWER(COALESCE(zone, '')) LIKE ? OR LOWER(COALESCE(region, '')) LIKE ? "
                "OR LOWER(COALESCE(location, '')) LIKE ? OR LOWER(COALESCE(address, '')) LIKE ? "
                "OR LOWER(COALESCE(phone_number, '')) LIKE ?)"
            )
            dist_params = [like_query] * 9
            if workspace_id:
                dist_sql += " AND workspace_id = ?"
                dist_params.append(workspace_id)
            if user_id is not None:
                dist_sql += " AND user_id = ?"
                dist_params.append(user_id)

            retail_sql = (
                "SELECT mr.id, mr.name, mr.contact_person, mr.distributor_id, mr.phone_number, "
                "mr.location AS city, mr.gst_no, mr.address "
                "FROM master_retailers mr "
                "LEFT JOIN master_distributors md ON mr.distributor_id = md.id "
                "WHERE ("
                "LOWER(COALESCE(mr.name, '')) LIKE ? OR LOWER(COALESCE(mr.contact_person, '')) LIKE ? "
                "OR LOWER(COALESCE(mr.gst_no, '')) LIKE ? OR LOWER(COALESCE(mr.location, '')) LIKE ? "
                "OR LOWER(COALESCE(mr.address, '')) LIKE ? OR LOWER(COALESCE(mr.phone_number, '')) LIKE ? "
                "OR LOWER(COALESCE(md.firm_name, '')) LIKE ? OR LOWER(COALESCE(md.name, '')) LIKE ? "
                "OR LOWER(COALESCE(md.firm_nick_name, '')) LIKE ?"
                ")"
            )
            retail_params = [like_query] * 9
            if workspace_id:
                retail_sql += " AND mr.workspace_id = ?"
                retail_params.append(workspace_id)
            if user_id is not None:
                retail_sql += " AND mr.user_id = ?"
                retail_params.append(user_id)

            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                dist_rows = conn.execute(
                    dist_sql.replace(
                        "SELECT name, gst_no, zone, region, location, address, phone_number, id",
                        "SELECT id, distributor_id AS buyer_code, COALESCE(firm_name, name) AS firm_name, firm_nick_name, name AS contact_person, contact_person_role, phone_number, location AS city, gst_no, zone, region, address",
                    ),
                    dist_params,
                ).fetchall()
                retail_rows_raw = conn.execute(retail_sql, retail_params).fetchall()

                # If distributors matched via LIKE, also include their retailers.
                if dist_rows:
                    dist_ids = [int(r["id"]) for r in dist_rows if r["id"] is not None]
                    if dist_ids:
                        placeholders = ",".join("?" * len(dist_ids))
                        linked = conn.execute(
                            f"SELECT mr.id, mr.name, mr.contact_person, mr.distributor_id, mr.phone_number, "
                            f"mr.location AS city, mr.gst_no, mr.address "
                            f"FROM master_retailers mr WHERE mr.distributor_id IN ({placeholders})"
                            + (" AND mr.user_id = ?" if user_id is not None else ""),
                            tuple(dist_ids) + ((user_id,) if user_id is not None else ()),
                        ).fetchall()
                        seen = {int(r["id"]) for r in retail_rows_raw}
                        for row in linked:
                            if int(row["id"]) not in seen:
                                retail_rows_raw.append(row)
                                seen.add(int(row["id"]))

            results["distributors"] = [dict(r) for r in dist_rows]

            retailers_structured = []
            for r in retail_rows_raw:
                row_dict = dict(r)
                dist_id = row_dict.pop("distributor_id", None)
                distributor_name = "Unassigned"
                if dist_id:
                    with sqlite3.connect(self.db_path) as conn2:
                        conn2.row_factory = sqlite3.Row
                        d = conn2.execute(
                            "SELECT firm_name, name, firm_nick_name FROM master_distributors WHERE id = ?",
                            (dist_id,),
                        ).fetchone()
                        if d:
                            distributor_name = d["firm_name"] or d["name"]
                            row_dict["distributor_nick_name"] = d["firm_nick_name"] or ""
                row_dict["distributor_name"] = distributor_name
                retailers_structured.append(row_dict)
            results["retailers"] = retailers_structured

        # Third tier: typo-tolerant fuzzy matching against distributor/
        # retailer names (e.g. "binina" → "Bernina", "Shree ram" → "Shri Ram").
        # IMPORTANT: run whenever distributors are still empty — do NOT wait for
        # a fully empty result set. Retailer-only FTS/LIKE hits (e.g. "Shree Ram
        # Furnishing") used to short-circuit this engine and hide distributors.
        if not results.get("distributors"):
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                dist_query = "SELECT id, distributor_id, firm_name, firm_nick_name, name, contact_person_role, phone_number, location, gst_no, zone, region, address FROM master_distributors"
                dist_params: list[Any] = []
                dist_clauses: list[str] = []
                if workspace_id:
                    dist_clauses.append("workspace_id = ?")
                    dist_params.append(workspace_id)
                if user_id is not None:
                    dist_clauses.append("user_id = ?")
                    dist_params.append(user_id)
                if dist_clauses:
                    dist_query += " WHERE " + " AND ".join(dist_clauses)
                all_distributors = conn.execute(dist_query, dist_params).fetchall()

                retail_query = (
                    "SELECT mr.id, mr.name, mr.contact_person, mr.distributor_id, mr.phone_number, mr.location, mr.gst_no, mr.address "
                    "FROM master_retailers mr"
                )
                retail_params: list[Any] = []
                retail_clauses: list[str] = []
                if workspace_id:
                    retail_clauses.append("mr.workspace_id = ?")
                    retail_params.append(workspace_id)
                if user_id is not None:
                    retail_clauses.append("mr.user_id = ?")
                    retail_params.append(user_id)
                if retail_clauses:
                    retail_query += " WHERE " + " AND ".join(retail_clauses)
                all_retailers = conn.execute(retail_query, retail_params).fetchall()

            normalized_lower = self._party_name_fold(normalized_query)
            compact_query = self._party_name_compact(normalized_query)
            phon_query = self._party_name_phonetic(normalized_query)
            term_lowers = [
                self._party_name_fold(t)
                for t in search_terms
                if str(t).strip()
            ]
            term_compacts = {
                self._party_name_compact(t)
                for t in search_terms
                if len(self._party_name_compact(t)) >= 4
            }
            term_phons = {
                self._party_name_phonetic(t)
                for t in search_terms
                if len(self._party_name_phonetic(t)) >= 3
            }
            if phon_query and len(phon_query) >= 3:
                term_phons.add(phon_query)
            fuzzy_dist_matches = []
            for d in all_distributors:
                candidates = [
                    (d["firm_name"] or ""),
                    (d["firm_nick_name"] or ""),
                    (d["name"] or ""),
                ]
                score = 0
                for candidate in candidates:
                    if not candidate:
                        continue
                    cand_l = candidate.lower()
                    cand_fold = self._party_name_fold(candidate)
                    cand_compact = self._party_name_compact(candidate)
                    cand_phon = self._party_name_phonetic(candidate)
                    if any(t == cand_l or t in cand_l or t in cand_fold for t in term_lowers):
                        score = max(score, 100)
                    if any(c and c in cand_compact for c in term_compacts):
                        score = max(score, 100)
                    if any(
                        p and (p == cand_phon or any(
                            self._party_name_phonetic(w) == p
                            for w in re.findall(r"[a-z0-9]+", cand_fold)
                            if len(w) >= 3
                        ))
                        for p in term_phons
                    ):
                        score = max(score, 94)
                    # Same scoring family as _fuzzy_match_distributor (upload engine)
                    score = max(
                        score,
                        fuzz.ratio(normalized_lower, cand_fold),
                        fuzz.token_set_ratio(normalized_lower, cand_fold),
                        fuzz.partial_ratio(normalized_lower, cand_fold)
                        if len(normalized_lower) >= 5
                        else 0,
                        fuzz.partial_ratio(compact_query, cand_compact)
                        if len(compact_query) >= 5
                        else 0,
                    )
                if score >= 85:
                    fuzzy_dist_matches.append((score, d))
            fuzzy_dist_matches.sort(key=lambda item: -item[0])
            matched_dist_ids = {int(d["id"]) for _score, d in fuzzy_dist_matches[:25]}
            existing_dist_ids = {
                int(row["id"])
                for row in results.get("distributors") or []
                if row.get("id") is not None
            }
            for _score, d in fuzzy_dist_matches[:25]:
                did = int(d["id"])
                if did in existing_dist_ids:
                    continue
                results.setdefault("distributors", []).append(
                    {
                        "id": d["id"],
                        "buyer_code": d["distributor_id"],
                        "firm_name": d["firm_name"] or d["name"],
                        "firm_nick_name": d["firm_nick_name"],
                        "contact_person": d["name"],
                        "contact_person_role": d["contact_person_role"],
                        "phone_number": d["phone_number"],
                        "city": d["location"],
                        "gst_no": d["gst_no"],
                        "zone": d["zone"],
                        "region": d["region"],
                        "address": d["address"],
                    }
                )
                existing_dist_ids.add(did)

            dist_name_by_id = {
                int(d["id"]): (
                    (d["firm_name"] or d["name"] or ""),
                    (d["firm_nick_name"] or ""),
                )
                for d in all_distributors
                if d["id"] is not None
            }
            # Only rebuild retailers from fuzzy tier when none were found yet;
            # otherwise merge linked retailers for newly matched distributors.
            if not results.get("retailers"):
                fuzzy_retail_matches = []
                for r in all_retailers:
                    candidate = (r["name"] or "")
                    contact = (r["contact_person"] or "")
                    distributor_name, distributor_nick = dist_name_by_id.get(
                        int(r["distributor_id"] or 0), ("", "")
                    )
                    score = max(
                        fuzz.ratio(normalized_lower, self._party_name_fold(candidate)) if candidate else 0,
                        fuzz.ratio(normalized_lower, self._party_name_fold(contact)) if contact else 0,
                        fuzz.ratio(normalized_lower, self._party_name_fold(distributor_name)) if distributor_name else 0,
                        fuzz.ratio(normalized_lower, self._party_name_fold(distributor_nick)) if distributor_nick else 0,
                        fuzz.partial_ratio(
                            compact_query, self._party_name_compact(candidate)
                        )
                        if candidate and len(compact_query) >= 5
                        else 0,
                    )
                    for text in (candidate, contact, distributor_name, distributor_nick):
                        if not text:
                            continue
                        phon = self._party_name_phonetic(text)
                        if phon_query and len(phon_query) >= 3 and phon == phon_query:
                            score = max(score, 94)
                        for word in re.findall(r"[a-z0-9]+", self._party_name_fold(text)):
                            if len(word) >= 3 and phon_query and self._party_name_phonetic(word) == phon_query:
                                score = max(score, 94)
                    linked_to_matched_dist = (
                        r["distributor_id"] is not None
                        and int(r["distributor_id"]) in matched_dist_ids
                    )
                    substring_hit = any(
                        t in candidate.lower()
                        or t in self._party_name_fold(candidate)
                        or (contact and t in self._party_name_fold(contact))
                        or (distributor_name and t in self._party_name_fold(distributor_name))
                        or (distributor_nick and t in self._party_name_fold(distributor_nick))
                        for t in term_lowers
                    )
                    if score >= 85 or linked_to_matched_dist or substring_hit:
                        fuzzy_retail_matches.append(
                            (score if score else 100, r, distributor_name or "Unassigned", distributor_nick)
                        )
                fuzzy_retail_matches.sort(key=lambda item: -item[0])
                fuzzy_retailers_structured = []
                for _score, r, distributor_name, distributor_nick in fuzzy_retail_matches[:100]:
                    fuzzy_retailers_structured.append(
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "contact_person": r["contact_person"],
                            "distributor_name": distributor_name,
                            "distributor_nick_name": distributor_nick,
                            "phone_number": r["phone_number"],
                            "city": r["location"],
                            "gst_no": r["gst_no"],
                            "address": r["address"],
                        }
                    )
                results["retailers"] = fuzzy_retailers_structured
            elif matched_dist_ids:
                seen_ret = {
                    int(r["id"])
                    for r in results.get("retailers") or []
                    if r.get("id") is not None
                }
                for r in all_retailers:
                    if r["distributor_id"] is None:
                        continue
                    if int(r["distributor_id"]) not in matched_dist_ids:
                        continue
                    rid = int(r["id"])
                    if rid in seen_ret:
                        continue
                    distributor_name, distributor_nick = dist_name_by_id.get(
                        int(r["distributor_id"]), ("Unassigned", "")
                    )
                    results.setdefault("retailers", []).append(
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "contact_person": r["contact_person"],
                            "distributor_name": distributor_name or "Unassigned",
                            "distributor_nick_name": distributor_nick,
                            "phone_number": r["phone_number"],
                            "city": r["location"],
                            "gst_no": r["gst_no"],
                            "address": r["address"],
                        }
                    )
                    seen_ret.add(rid)

        if user_id:
            like_query = f"%{normalized_query.lower()}%"
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    am_rows = conn.execute(
                        """
                        SELECT id, category, brand, size, product_type, mrp, ptr, ex_mill_price, item_key, extra_attributes
                        FROM article_master
                        WHERE user_id = ? AND is_active = 1 AND (
                            LOWER(COALESCE(brand, '')) LIKE ?
                            OR LOWER(COALESCE(size, '')) LIKE ?
                            OR LOWER(COALESCE(product_type, '')) LIKE ?
                            OR LOWER(COALESCE(item_key, '')) LIKE ?
                            OR LOWER(COALESCE(category, '')) LIKE ?
                            OR LOWER(COALESCE(extra_attributes, '')) LIKE ?
                            OR LOWER(COALESCE(brand, '')) IN (
                                SELECT LOWER(canonical_brand) FROM brand_aliases
                                WHERE user_id = ? AND LOWER(alias) LIKE ?
                            )
                            OR LOWER(COALESCE(brand, '')) IN (
                                SELECT LOWER(alias) FROM brand_aliases
                                WHERE user_id = ? AND LOWER(canonical_brand) LIKE ?
                            )
                        )
                        ORDER BY LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, ''))
                        LIMIT 50
                        """,
                        (
                            user_id,
                            like_query,
                            like_query,
                            like_query,
                            like_query,
                            like_query,
                            like_query,
                            user_id,
                            like_query,
                            user_id,
                            like_query,
                        ),
                    ).fetchall()
                    results["article_master"] = [dict(r) for r in am_rows]
                except sqlite3.OperationalError:
                    # Fresh / partial DBs may lack article_master or brand_aliases.
                    # Party/order search above must still succeed.
                    results["article_master"] = []

                # A near-miss design/brand name ("Ester" for "Aster", or a
                # missing space — "Wonderland" for "Wonder Land") has zero
                # LIKE-substring overlap with the real name, so the exact
                # match above finds nothing — unlike distributor search,
                # which already has an edit-distance fallback tier, article
                # search had none. Checked against brand AND product_type/
                # category, since which column actually holds a given
                # design's name varies per user's own upload. Only kicks in
                # when the exact search found nothing at all here, and the
                # query looks like one short name (not a whole sentence),
                # so this can't misfire on a query that's legitimately
                # about something else entirely.
                if (
                    not results["article_master"]
                    and re.fullmatch(r"[A-Za-z ]{3,20}", normalized_query.strip())
                ):
                    try:
                        with sqlite3.connect(self.db_path) as conn:
                            name_rows = conn.execute(
                                "SELECT DISTINCT brand, 'brand' FROM article_master "
                                "WHERE user_id = ? AND is_active = 1 AND brand IS NOT NULL "
                                "UNION "
                                "SELECT DISTINCT product_type, 'product_type' FROM article_master "
                                "WHERE user_id = ? AND is_active = 1 AND product_type IS NOT NULL "
                                "UNION "
                                "SELECT DISTINCT category, 'category' FROM article_master "
                                "WHERE user_id = ? AND is_active = 1 AND category IS NOT NULL",
                                (user_id, user_id, user_id),
                            ).fetchall()
                        # A query's normalized (no-space) form also needs to line
                        # up against a candidate's normalized form — "wonderland"
                        # vs "wonder land" would otherwise score lower than a
                        # coincidental shorter match with a space in the same
                        # place as the query.
                        candidates = {
                            (name or "").lower(): (name, field)
                            for name, field in name_rows
                            if name
                        }
                        query_key = normalized_query.lower()
                        query_nospace = query_key.replace(" ", "")
                        # A generic design name ("Wonderland") can have several
                        # active variants sharing that name ("Wonder Land- Glow",
                        # "Wonder Land- Kid") that only differ by a trailing
                        # word — collect every candidate that scores close
                        # enough, not just the single nearest one, or asking
                        # about the design as a whole silently drops the
                        # other variant(s).
                        matched: list[tuple[str, str]] = []
                        for key in candidates:
                            ratio = max(
                                difflib.SequenceMatcher(None, query_key, key).ratio(),
                                difflib.SequenceMatcher(
                                    None, query_nospace, key.replace(" ", "")
                                ).ratio(),
                            )
                            if ratio >= 0.70:
                                matched.append(candidates[key])
                        if matched:
                            by_field: dict[str, list[str]] = {}
                            for name, field in matched:
                                by_field.setdefault(field, []).append(name.lower())
                            am_rows: list[Any] = []
                            with sqlite3.connect(self.db_path) as conn:
                                conn.row_factory = sqlite3.Row
                                for field, names in by_field.items():
                                    placeholders = ",".join("?" * len(names))
                                    am_rows.extend(
                                        conn.execute(
                                            f"SELECT id, category, brand, size, product_type, "
                                            f"mrp, ptr, ex_mill_price, item_key, extra_attributes "
                                            f"FROM article_master WHERE user_id = ? "
                                            f"AND is_active = 1 AND LOWER(COALESCE({field}, '')) "
                                            f"IN ({placeholders}) "
                                            f"ORDER BY LOWER(COALESCE(size, '')) LIMIT 50",
                                            (user_id, *names),
                                        ).fetchall()
                                    )
                            seen_ids: set[Any] = set()
                            deduped = []
                            for row in am_rows:
                                if row["id"] in seen_ids:
                                    continue
                                seen_ids.add(row["id"])
                                deduped.append(dict(row))
                            results["article_master"] = deduped
                    except sqlite3.OperationalError:
                        pass

        # Always merge party hits by folded honorific spellings (Shree/Sri/Shri Ram),
        # even when FTS already found some retailers — otherwise distributor fallback
        # is skipped and "Shree ram" never finds "Shri Ram Distributor".
        self._supplement_parties_by_folded_name(
            results, normalized_query, search_terms, workspace_id
        )

        results = self._filter_global_search_results(
            normalized_query, results, terms=search_terms
        )

        # FO ↔ SO Pack Order Match — only when query looks like an SO/CI number.
        # Name/brand searches (e.g. "aster") must not open this section.
        if so_ci_query:
            match_hits = self._search_order_match_runs_for_query(normalized_query)
            if match_hits:
                seen_match_ids = {
                    int(o["match_run_id"])
                    for o in results["orders"]
                    if o.get("match_run_id") is not None
                }
                seen_refs = {
                    str(o.get("order_ref_no") or "").strip().lower()
                    for o in results["orders"]
                    if o.get("order_ref_no")
                }
                for hit in match_hits:
                    mid = hit.get("match_run_id")
                    ref = str(hit.get("order_ref_no") or "").strip().lower()
                    if mid is not None and int(mid) in seen_match_ids:
                        continue
                    if ref and ref in seen_refs and mid is None:
                        continue
                    results["orders"].append(hit)
                    if mid is not None:
                        seen_match_ids.add(int(mid))
                    if ref:
                        seen_refs.add(ref)
        else:
            results["orders"] = []

        return {"query": normalized_query, "results": results}

    def _search_order_match_runs_for_query(self, query: str) -> list[dict[str, Any]]:
        """Find Order Match runs by SO number only (not party/brand text)."""
        q = (query or "").strip().lower()
        if len(q) < 2 or not self._query_looks_like_so_ci_number(query):
            return []
        like = f"%{q}%"
        try:
            from app.services import fo_so_match_db as matchdb
        except Exception:
            matchdb = None

        with sqlite3.connect(self.db_path) as conn:
            if matchdb is not None:
                try:
                    matchdb.ensure_schema(conn)
                except Exception:
                    pass
            try:
                rows = conn.execute(
                    """
                    SELECT id, distributor_name, so_buyer_label, so_source_filename,
                           filled_order_id, season, category, rows_json
                    FROM fo_so_match_runs
                    WHERE LOWER(COALESCE(rows_json, '')) LIKE ?
                    ORDER BY id DESC
                    LIMIT 40
                    """,
                    (like,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        hits: list[dict[str, Any]] = []
        for row in rows:
            run_id = row[0]
            distributor_name = row[1] or row[2]
            so_source = row[3]
            filled_order_id = row[4]
            season = row[5]
            category = row[6]
            rows_json = row[7] or "[]"
            so_hit: str | None = None
            try:
                match_rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
                if isinstance(match_rows, list):
                    for mr in match_rows:
                        if not isinstance(mr, dict):
                            continue
                        candidates: list[str] = []
                        for sn in mr.get("so_numbers") or []:
                            candidates.append(str(sn or "").strip())
                        for cell in mr.get("so_breakdown") or []:
                            if isinstance(cell, dict):
                                candidates.append(str(cell.get("so_number") or "").strip())
                        # Legacy / grouped payloads may key by SO under by_so
                        by_so = mr.get("by_so")
                        if isinstance(by_so, dict):
                            candidates.extend(str(k).strip() for k in by_so.keys())
                        for sn_text in candidates:
                            if sn_text and q in sn_text.lower():
                                so_hit = sn_text
                                break
                        if so_hit:
                            break
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            # Digits-only SO query: if rows_json matched via LIKE but structured
            # fields missed it, still label the hit with the typed SO number.
            if so_hit is None and q.isdigit() and q in (rows_json or "").lower():
                so_hit = query.strip()
            # No structured SO number hit → skip (do not fall back to party/file name).
            if so_hit is None:
                continue
            status_bits = " · ".join(
                p for p in (season, category, "Order Match") if p
            )
            hits.append(
                {
                    "tracking_id": None,
                    "match_run_id": run_id,
                    "order_ref_no": so_hit,
                    "invoice_no": None,
                    "distributor_name": distributor_name,
                    "transit_status": status_bits or "Order Match",
                    "payment_status": None,
                    "has_sales_order": True,
                    "has_commercial_invoice": False,
                    "filled_order_id": filled_order_id,
                    "so_source_filename": so_source,
                }
            )
        return hits

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

    def get_morning_suggestion_list(
        self, current_date: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Priority list of parties by days since last visit.

        When workspace_id is set, only that tenant's master distributors /
        retailers are included (fail-closed multi-tenant). Legacy calls
        without workspace_id keep the old global distributors/retailers
        tables for unit tests only — HTTP routes must pass workspace_id.
        """
        suggestions: list[dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                distributors = conn.execute(
                    "SELECT id FROM master_distributors WHERE workspace_id = ? ORDER BY id",
                    (workspace_id,),
                ).fetchall()
                retailers = conn.execute(
                    "SELECT id FROM master_retailers WHERE workspace_id = ? ORDER BY id",
                    (workspace_id,),
                ).fetchall()
            else:
                distributors = conn.execute(
                    "SELECT id FROM distributors ORDER BY id"
                ).fetchall()
                retailers = conn.execute(
                    "SELECT id FROM retailers ORDER BY id"
                ).fetchall()

        for (distributor_id,) in distributors:
            last_visit = self.get_last_visit_date("distributor", distributor_id)
            suggestions.append(
                {
                    "entity_type": "distributor",
                    "entity_id": distributor_id,
                    "last_visit_date": last_visit,
                    "priority_score": self._days_since(last_visit, current_date),
                }
            )

        for (retailer_id,) in retailers:
            last_visit = self.get_last_visit_date("retailer", retailer_id)
            suggestions.append(
                {
                    "entity_type": "retailer",
                    "entity_id": retailer_id,
                    "last_visit_date": last_visit,
                    "priority_score": self._days_since(last_visit, current_date),
                }
            )

        suggestions.sort(
            key=lambda item: (item["priority_score"], item["entity_type"]), reverse=True
        )
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

    def create_weekly_pjp_plan(
        self,
        week_start_date: str,
        day_of_week: str,
        planned_distributor_ids: list[int],
        planned_retailer_ids: list[int],
        status: str = "planned",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO weekly_pjp_plans (week_start_date, day_of_week, planned_distributor_ids, planned_retailer_ids, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    week_start_date,
                    day_of_week,
                    json.dumps(planned_distributor_ids),
                    json.dumps(planned_retailer_ids),
                    status,
                    created_at,
                ),
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
            deleted_todos = int(
                conn.execute(
                    "DELETE FROM workflow_todo_list WHERE created_date < ?",
                    (cutoff_text,),
                ).rowcount
            )
            deleted_gps = int(
                conn.execute(
                    "DELETE FROM gps_visit_verification_logs WHERE device_timestamp < ?",
                    (cutoff_dt,),
                ).rowcount
            )
            deleted_verifications = int(
                conn.execute(
                    "DELETE FROM verification_outputs WHERE created_at < ?",
                    (cutoff_dt,),
                ).rowcount
            )
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
                (
                    report_date,
                    summary or "Auto-generated DSR",
                    distributor_visit_count,
                    retailer_visit_count,
                    orders_booked,
                    payments_discussed,
                    feedback_collected,
                    created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _count_visit_logs(self, entity_type: str, report_date: str) -> int:
        table = (
            "distributor_visit_logs"
            if entity_type == "distributor"
            else "retailer_visit_logs"
        )
        id_column = "distributor_id" if entity_type == "distributor" else "retailer_id"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE visit_date = ?",
                (report_date,),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_dsr_reports_by_date_range(
        self, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
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

    def export_dsr_report(
        self, report_id: int, export_format: str = "excel"
    ) -> bytes | str:
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
        return " ".join(str(value).strip().split())

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

    def _resolve_template_header(
        self, template_config: dict[str, Any] | None, field_name: str, fallback: str
    ) -> str:
        headers = (template_config or {}).get("headers", {})
        if isinstance(headers, dict):
            configured = headers.get(field_name)
            if configured:
                return str(configured)
        return fallback

    def _get_row_value(
        self,
        row: dict[str, Any],
        field_name: str,
        fallback: str,
        template_config: dict[str, Any] | None = None,
    ) -> Any:
        configured_header = self._resolve_template_header(
            template_config, field_name, fallback
        )
        candidates = [configured_header, fallback]
        alias_map = {
            "distributor_name": ["Distributor Name", "Distributor", "Name"],
            "distributor_code": ["Distributor Code", "Distributor ID", "Code"],
            "buyer_code": ["Buyer Code", "Buyer ID", "Buyer"] ,
            "firm_name": ["Firm Name", "Firm"],
            "firm_nick_name": ["Firm nick name", "Firm Nick Name", "Firm Nickname"],
            "phone_number": [
                "Phone",
                "Phone Number",
                "Mobile Number",
                "Mobile 1",
                "Mobile",
                "Distributor Mobile Number",
                "Distributor Phone",
                "Retailer Mobile Number",
                "Retailer Phone",
            ],
            "phone_number_2": [
                "Mobile 2",
                "Phone 2",
                "Alternate Mobile Number",
                "Alternate Phone",
                "Secondary Mobile Number",
            ],
            "contact_person": [
                "Contact Person Name",
                "Contact Person",
                "Contact Name",
            ],
            "contact_person_role": [
                "Contact Person Role",
                "Contact Role",
                "Designation",
                "Role",
            ],
            "state": ["State"],
            "pincode": ["Pincode", "Pin Code", "PIN Code", "Zip Code"],
            "category": ["Category", "Store Type", "Shop Type", "Business Type"],
            "birthday": ["Birthday", "Date of Birth", "DOB"],
            "anniversary": ["Anniversary", "Wedding Anniversary"],
            "email": [
                "Email",
                "Email Address",
                "Email id",
                "Distributor Email",
                "Retailer Email",
            ],
            "address": ["Address"],
            "pincode": ["Pincode", "Pin Code"],
            "gst_no": ["GSTIN", "GST Number", "GST No", "GST"],
            "distribution_state": ["Distribution State", "State", "Zone"],
            "distribution_area": ["Distribution Area", "Area", "Region"],
            "payment_terms": ["Payment Terms", "Payment Term"],
            "birthday": ["Birthday"],
            "anniversary": ["Anniversary"],
            "secondary_distributor_name": [
                "Secondary Distributor Name",
                "Secondary Contact Name",
                "Secondary Distributor",
                "Secondary Contact",
            ],
            "secondary_distributor_phone_number": [
                "Secondary Distributor Mobile Number",
                "Secondary Distributor Phone",
                "Secondary Contact Mobile Number",
                "Secondary Contact Phone",
            ],
            "secondary_distributor_birthday": [
                "Secondary Distributor Birthday",
                "Secondary Contact Birthday",
            ],
            "secondary_distributor_anniversary": [
                "Secondary Distributor Anniversary",
                "Secondary Contact Anniversary",
            ],
            "sales_executive_name": [
                "Sales Executive Name",
                "Sales Executive",
                "Sales Executive Contact Name",
            ],
            "sales_executive_phone_number": [
                "Sales Executive Mobile Number",
                "Sales Executive Phone",
                "Sales Executive Phone Number",
            ],
            "sales_executive_email": [
                "Sales Executive Email",
                "Sales Executive Email Address",
            ],
            "sales_executive_birthday": ["Sales Executive Birthday"],
            "sales_executive_anniversary": ["Sales Executive Anniversary"],
            "retailer_name": ["Retailer Name", "Retailer", "Shop Name", "Name"],
            "linked_distributor_gst_or_name": [
                "Distributor",
                "Linked Distributor GST or Name",
                "Distributor Name",
                "Distributor GST or Name",
            ],
            "retailer_code": ["Retailer Code", "Retailer ID", "Code"],
            "location": ["Location", "City"],
            "secondary_retailer_name": [
                "Secondary Retailer Name",
                "Secondary Contact Name",
                "Secondary Retailer",
                "Secondary Contact",
            ],
            "secondary_retailer_phone_number": [
                "Secondary Retailer Mobile Number",
                "Secondary Retailer Phone",
                "Secondary Contact Mobile Number",
                "Secondary Contact Phone",
            ],
            "secondary_retailer_birthday": [
                "Secondary Retailer Birthday",
                "Secondary Contact Birthday",
            ],
            "secondary_retailer_anniversary": [
                "Secondary Retailer Anniversary",
                "Secondary Contact Anniversary",
            ],
        }
        candidates.extend(alias_map.get(field_name, []))
        # Also try the raw internal field_name itself — real-world
        # files are sometimes raw database exports (e.g. "firm_name",
        # "gst_no") rather than the human-friendly template labels
        # (e.g. "Firm Name", "GST No"). Without this, uploading a
        # file with exactly the column names this system itself
        # exports (as happened with the founder's real
        # distributors.xlsx) silently returned empty values for
        # firm_name/gst_no/etc. on every row.
        candidates.append(field_name)

        def _canonical(value: str) -> str:
            # Treat underscores and spaces as equivalent so
            # "firm_name" and "Firm Name" resolve to the same key.
            return str(value).strip().lower().replace("_", " ")

        normalized_lookup = {_canonical(key): key for key in row.keys()}
        for candidate in candidates:
            if candidate in row:
                return row[candidate]
            if isinstance(candidate, str):
                normalized_candidate = _canonical(candidate)
                if normalized_candidate in normalized_lookup:
                    return row[normalized_lookup[normalized_candidate]]
        return row.get(fallback, "")

    def _generate_unique_master_id(self, prefix: str) -> str:
        return f"{prefix}{uuid4().hex[:12].upper()}"

    def _load_rows_from_upload(self, path: str | Path) -> list[dict[str, Any]]:
        import pandas as pd

        file_path = Path(path)
        if file_path.suffix.lower() == ".csv":
            # Real-world CSV files (especially exported from Excel on
            # Windows) are very often NOT valid UTF-8 — they commonly
            # use cp1252/latin-1, which can include bytes like 0xA0
            # (non-breaking space) that crash a strict UTF-8 decode.
            # Try UTF-8 first (most correct/modern), then fall back to
            # the common Windows encodings rather than failing outright.
            last_error: Exception | None = None
            dataframe = None
            for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
                try:
                    dataframe = pd.read_csv(file_path, encoding=encoding)
                    break
                except UnicodeDecodeError as exc:
                    last_error = exc
                    continue
            if dataframe is None:
                raise ValueError(
                    f"Unable to read CSV file with any supported encoding: {last_error}"
                )
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
            matched_signals = sum(
                1 for value in normalized_columns if value in signal_columns
            )
            if matched_signals < 3:
                dataframe = pd.read_excel(file_path, sheet_name=0, header=0)
        dataframe = dataframe.fillna("")
        dataframe.columns = [str(col).strip() for col in dataframe.columns]
        return dataframe

    def _build_template_dataframe(
        self, template_config: dict[str, Any] | None, template_type: str
    ) -> pd.DataFrame:
        import pandas as pd

        default_templates = {
            "distributors": {
                "headers": [
                    "distributor_code",
                    "buyer_code",
                    "firm_name",
                    "firm_nick_name",
                    "distributor_name",
                    "contact_person_role",
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
                    "buyer_code": "Buyer Code",
                    "firm_name": "Firm Name",
                    "firm_nick_name": "Firm nick name",
                    "distributor_name": "Distributor Name",
                    "contact_person_role": "Contact Person Role",
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
            merged_config["label_map"] = {
                key: header_map.get(key, selected["label_map"].get(key, key))
                for key in selected["headers"]
            }
        return pd.DataFrame([{column: "" for column in selected["headers"]}]).rename(
            columns=merged_config["label_map"]
        )

    def _generate_template_bytes(
        self,
        template_type: str,
        template_config: dict[str, Any] | None = None,
        file_format: str = "excel",
    ) -> bytes:
        dataframe = self._build_template_dataframe(template_config, template_type)
        if file_format.lower() == "csv":
            output = StringIO()
            dataframe.to_csv(output, index=False)
            return output.getvalue().encode("utf-8")
        output = BytesIO()
        dataframe.to_excel(output, index=False)
        return output.getvalue()

    def generate_master_template(
        self,
        template_type: str,
        file_format: str = "excel",
        template_config: dict[str, Any] | None = None,
    ) -> bytes:
        allowed_types = {"distributors", "retailers", "articles"}
        if template_type not in allowed_types:
            raise ValueError(f"Unsupported template type: {template_type}")
        return self._generate_template_bytes(
            template_type, template_config=template_config, file_format=file_format
        )

    def bulk_upload_masters(
        self,
        master_type: str,
        path: str | Path,
        template_config: dict[str, Any] | None = None,
        workspace_id: str = "default",
        user_id: int | None = None,
    ) -> dict[str, Any]:
        if master_type not in {"distributors", "retailers"}:
            raise ValueError("Unsupported master type")

        rows = self._load_rows_from_upload(path)
        inserted = 0
        updated = 0
        skipped = 0
        unassigned = 0
        ambiguous_distributor_refs: list[dict[str, Any]] = []
        errors: list[str] = []
        user_clause, user_params = self._user_id_sql("user_id", user_id)

        with sqlite3.connect(self.db_path) as conn:
            if master_type == "distributors":
                for row in rows:
                    try:
                        name = self._canonicalize_known_master_name(
                            self._get_row_value(
                                row,
                                "distributor_name",
                                "Distributor Name",
                                template_config,
                            )
                        )
                        name_key = self._normalize_text(name).lower()
                        if not name_key:
                            skipped += 1
                            continue
                        distributor_code = self._normalize_text(
                            self._get_row_value(
                                row,
                                "distributor_code",
                                "Distributor Code",
                                template_config,
                            )
                        )
                        buyer_code = self._normalize_text(
                            self._get_row_value(
                                row,
                                "buyer_code",
                                "Buyer Code",
                                template_config,
                            )
                        )
                        firm_name = self._canonicalize_known_master_name(
                            self._get_row_value(
                                row, "firm_name", "Firm Name", template_config
                            )
                        )
                        firm_nick_name = self._normalize_text(
                            self._get_row_value(
                                row, "firm_nick_name", "Firm nick name", template_config
                            )
                        )
                        if not firm_nick_name:
                            firm_nick_name = self._normalize_text(
                                self._get_row_value(
                                    row,
                                    "firm_nick_name",
                                    "Firm Nick Name",
                                    template_config,
                                )
                            )
                        gst_no = self._normalize_gst_no(
                            self._get_row_value(
                                row, "gst_no", "GST Number", template_config
                            )
                        )
                        if not gst_no:
                            gst_no = self._normalize_gst_no(
                                self._get_row_value(
                                    row, "gst_no", "GSTIN", template_config
                                )
                            )

                        distribution_state = self._normalize_text(
                            self._get_row_value(
                                row,
                                "distribution_state",
                                "Distribution State",
                                template_config,
                            )
                        )
                        distribution_area = self._normalize_text(
                            self._get_row_value(
                                row,
                                "distribution_area",
                                "Distribution Area",
                                template_config,
                            )
                        )
                        zone = distribution_state or self._normalize_text(
                            self._get_row_value(row, "zone", "Zone", template_config)
                        )
                        region = distribution_area or self._normalize_text(
                            self._get_row_value(
                                row, "region", "Region", template_config
                            )
                        )

                        location = self._normalize_text(
                            self._get_row_value(
                                row, "location", "Location", template_config
                            )
                        )
                        pincode = self._normalize_text(
                            self._get_row_value(
                                row, "pincode", "Pincode", template_config
                            )
                        )
                        payment_terms = self._normalize_text(
                            self._get_row_value(
                                row, "payment_terms", "Payment Terms", template_config
                            )
                        )
                        birthday = self._normalize_text(
                            self._get_row_value(
                                row, "birthday", "Birthday", template_config
                            )
                        )
                        anniversary = self._normalize_text(
                            self._get_row_value(
                                row, "anniversary", "Anniversary", template_config
                            )
                        )
                        secondary_distributor_name = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_distributor_name",
                                "Secondary Distributor Name",
                                template_config,
                            )
                        )
                        secondary_distributor_phone_number = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_distributor_phone_number",
                                "Secondary Distributor Mobile Number",
                                template_config,
                            )
                        )
                        secondary_distributor_birthday = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_distributor_birthday",
                                "Secondary Distributor Birthday",
                                template_config,
                            )
                        )
                        secondary_distributor_anniversary = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_distributor_anniversary",
                                "Secondary Distributor Anniversary",
                                template_config,
                            )
                        )
                        sales_executive_name = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_name",
                                "Sales Executive Name",
                                template_config,
                            )
                        )
                        sales_executive_phone_number = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_phone_number",
                                "Sales Executive Mobile Number",
                                template_config,
                            )
                        )
                        sales_executive_email = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_email",
                                "Sales Executive Email",
                                template_config,
                            )
                        )
                        sales_executive_birthday = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_birthday",
                                "Sales Executive Birthday",
                                template_config,
                            )
                        )
                        sales_executive_anniversary = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_anniversary",
                                "Sales Executive Anniversary",
                                template_config,
                            )
                        )

                        credit_limit = self._coerce_float(
                            self._get_row_value(
                                row, "credit_limit", "Credit Limit", template_config
                            )
                        )
                        phone_number = self._normalize_text(
                            self._get_row_value(
                                row, "phone_number", "Mobile Number", template_config
                            )
                        )
                        if not phone_number:
                            phone_number = self._normalize_text(
                                self._get_row_value(
                                    row, "phone_number", "Phone", template_config
                                )
                            )

                        email = self._normalize_text(
                            self._get_row_value(
                                row, "email", "Email id", template_config
                            )
                        )
                        if not email:
                            email = self._normalize_text(
                                self._get_row_value(
                                    row, "email", "Email", template_config
                                )
                            )

                        address = self._normalize_text(
                            self._get_row_value(
                                row, "address", "Address", template_config
                            )
                        )
                        contact_person_role = self._normalize_text(
                            self._get_row_value(
                                row,
                                "contact_person_role",
                                "Contact Person Role",
                                template_config,
                            )
                        )
                        if gst_no and len(gst_no) < 10:
                            errors.append(f"Invalid GST for distributor {name}")
                            skipped += 1
                            continue

                        existing_by_name_rows = conn.execute(
                            "SELECT id, distributor_id, name, gst_no, buyer_code, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(name) = ? AND workspace_id = ? AND "
                            + user_clause,
                            (name_key, workspace_id, *user_params),
                        ).fetchall()
                        existing_by_name = (
                            existing_by_name_rows[0] if existing_by_name_rows else None
                        )
                        if len(existing_by_name_rows) > 1:
                            errors.append(
                                f"Ambiguous distributor name match for '{name}'. Use unique GST Number."
                            )
                            skipped += 1
                            continue

                        existing_by_gst_rows: list[tuple[Any, ...]] = []
                        if gst_no:
                            existing_by_gst_rows = conn.execute(
                                "SELECT id, distributor_id, name, gst_no, buyer_code, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(COALESCE(gst_no, '')) = ? AND workspace_id = ? AND "
                                + user_clause,
                                (gst_no.lower(), workspace_id, *user_params),
                            ).fetchall()
                        existing_by_gst = (
                            existing_by_gst_rows[0] if existing_by_gst_rows else None
                        )
                        if len(existing_by_gst_rows) > 1:
                            errors.append(
                                f"Ambiguous GST match for distributor '{name}' with GST '{gst_no}'."
                            )
                            skipped += 1
                            continue

                        existing_by_code_rows: list[tuple[Any, ...]] = []
                        if distributor_code:
                            existing_by_code_rows = conn.execute(
                                "SELECT id, distributor_id, name, gst_no, buyer_code, firm_name, firm_nick_name, zone, region, location, address, pincode, phone_number, email, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, status FROM master_distributors WHERE LOWER(COALESCE(distributor_id, '')) = ? AND workspace_id = ? AND "
                                + user_clause,
                                (distributor_code.lower(), workspace_id, *user_params),
                            ).fetchall()
                        existing_by_code = (
                            existing_by_code_rows[0] if existing_by_code_rows else None
                        )
                        if len(existing_by_code_rows) > 1:
                            errors.append(
                                f"Ambiguous distributor code match for '{distributor_code}'."
                            )
                            skipped += 1
                            continue

                        if (
                            existing_by_code is not None
                            and existing_by_name is not None
                            and int(existing_by_code[0]) != int(existing_by_name[0])
                        ):
                            errors.append(
                                f"Conflict for distributor '{name}': name and distributor code point to different records."
                            )
                            skipped += 1
                            continue

                        if (
                            existing_by_code is not None
                            and existing_by_gst is not None
                            and int(existing_by_code[0]) != int(existing_by_gst[0])
                        ):
                            errors.append(
                                f"Conflict for distributor code '{distributor_code}': GST points to a different record."
                            )
                            skipped += 1
                            continue

                        if (
                            existing_by_name is not None
                            and existing_by_gst is not None
                            and int(existing_by_name[0]) != int(existing_by_gst[0])
                        ):
                            errors.append(
                                f"Conflict for distributor '{name}': name and GST point to different records."
                            )
                            skipped += 1
                            continue

                        if existing_by_name is not None and gst_no:
                            existing_name_gst = self._normalize_gst_no(
                                existing_by_name[3]
                            )
                            if existing_name_gst and existing_name_gst != gst_no:
                                errors.append(
                                    f"Conflict for distributor '{name}': existing GST '{existing_name_gst}' differs from uploaded GST '{gst_no}'."
                                )
                                skipped += 1
                                continue

                        if existing_by_gst is not None and not distributor_code:
                            existing_gst_name = self._normalize_text(existing_by_gst[2])
                            if (
                                existing_gst_name
                                and existing_gst_name.lower() != name.lower()
                            ):
                                errors.append(
                                    f"Conflict for GST '{gst_no}': existing distributor '{existing_by_gst[2]}' differs from uploaded '{name}'."
                                )
                                skipped += 1
                                continue

                        existing_row = (
                            existing_by_code or existing_by_gst or existing_by_name
                        )
                        if existing_row is not None:
                            distributor_id = int(existing_row[0])
                            updated_code = distributor_code or existing_row[1]
                            updated_name = name or existing_row[2]
                            updated_gst = gst_no or existing_row[3]
                            updated_buyer_code = buyer_code or existing_row[4]
                            updated_firm_name = firm_name or existing_row[5]
                            updated_firm_nick_name = firm_nick_name or existing_row[6]
                            updated_zone = zone or existing_row[7]
                            updated_region = region or existing_row[8]
                            updated_location = location or existing_row[9]
                            updated_address = address or existing_row[10]
                            updated_pincode = pincode or existing_row[11]
                            updated_phone = phone_number or existing_row[12]
                            updated_email = email or existing_row[13]
                            updated_payment_terms = payment_terms or existing_row[14]
                            updated_birthday = birthday or existing_row[15]
                            updated_anniversary = anniversary or existing_row[16]
                            updated_secondary_distributor_name = (
                                secondary_distributor_name or existing_row[17]
                            )
                            updated_secondary_distributor_phone_number = (
                                secondary_distributor_phone_number or existing_row[18]
                            )
                            updated_secondary_distributor_birthday = (
                                secondary_distributor_birthday or existing_row[19]
                            )
                            updated_secondary_distributor_anniversary = (
                                secondary_distributor_anniversary or existing_row[20]
                            )
                            updated_sales_executive_name = (
                                sales_executive_name or existing_row[21]
                            )
                            updated_sales_executive_phone_number = (
                                sales_executive_phone_number or existing_row[22]
                            )
                            updated_sales_executive_email = (
                                sales_executive_email or existing_row[23]
                            )
                            updated_sales_executive_birthday = (
                                sales_executive_birthday or existing_row[24]
                            )
                            updated_sales_executive_anniversary = (
                                sales_executive_anniversary or existing_row[25]
                            )
                            updated_credit_limit = (
                                credit_limit
                                if credit_limit is not None
                                else existing_row[26]
                            )
                            updated_status = existing_row[27] or "active"
                            conn.execute(
                                """
                                UPDATE master_distributors
                                SET
                                    distributor_id = ?,
                                    distributor_code = ?,
                                    name = ?,
                                    gst_no = ?,
                                    buyer_code = ?,
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
                                    updated_buyer_code,
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
                            if contact_person_role:
                                conn.execute(
                                    "UPDATE master_distributors SET contact_person_role = ? "
                                    "WHERE id = ? AND workspace_id = ?",
                                    (contact_person_role, distributor_id, workspace_id),
                                )
                            continue

                        self.add_master_distributor(
                            name=name,
                            distributor_code=distributor_code or None,
                            buyer_code=buyer_code or None,
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
                            secondary_distributor_name=secondary_distributor_name
                            or None,
                            secondary_distributor_phone_number=secondary_distributor_phone_number
                            or None,
                            secondary_distributor_birthday=secondary_distributor_birthday
                            or None,
                            secondary_distributor_anniversary=secondary_distributor_anniversary
                            or None,
                            sales_executive_name=sales_executive_name or None,
                            sales_executive_phone_number=sales_executive_phone_number
                            or None,
                            sales_executive_email=sales_executive_email or None,
                            sales_executive_birthday=sales_executive_birthday or None,
                            sales_executive_anniversary=sales_executive_anniversary
                            or None,
                            credit_limit=credit_limit,
                            contact_person_role=contact_person_role or None,
                            status="active",
                            workspace_id=workspace_id,
                            user_id=user_id,
                            conn=conn,
                            allow_fuzzy=False,
                            refresh_search_index=False,
                        )
                        inserted += 1
                    except Exception as exc:  # pragma: no cover - defensive path
                        errors.append(str(exc))
                        skipped += 1
            else:
                # PERFORMANCE FIX: fetch every distributor for this
                # workspace ONCE, before the loop — the previous code
                # opened a brand-new sqlite3 connection (via
                # get_master_distributor_by_name /
                # _find_master_distributor_by_gst_or_name /
                # _fuzzy_match_distributor) for EVERY SINGLE retailer
                # row, which made a real-world 2000+ row upload
                # effectively hang. Matching now happens in-memory
                # against this pre-fetched list instead.
                distributor_rows = conn.execute(
                    "SELECT id, distributor_id, name, firm_name, firm_nick_name, gst_no FROM master_distributors WHERE workspace_id = ? AND "
                    + user_clause,
                    (workspace_id, *user_params),
                ).fetchall()
                distributors_by_exact_key: dict[str, dict[str, Any]] = {}
                distributor_cache: dict[int, dict[str, Any]] = {}
                for d_id, d_dist_id, d_name, d_firm_name, d_firm_nick, d_gst in distributor_rows:
                    record = {
                        "id": d_id, "distributor_id": d_dist_id, "name": d_name,
                        "firm_name": d_firm_name, "firm_nick_name": d_firm_nick, "gst_no": d_gst,
                    }
                    distributor_cache[d_id] = record
                    for key_text in (d_name, d_firm_name, d_firm_nick):
                        if key_text:
                            distributors_by_exact_key[self._normalize_text(key_text).lower()] = record
                    if d_gst:
                        distributors_by_exact_key[self._normalize_text(d_gst).lower()] = record

                def _match_distributor_in_memory(reference: str) -> dict[str, Any] | None:
                    ref_key = self._normalize_text(reference).lower()
                    if not ref_key:
                        return None
                    exact = distributors_by_exact_key.get(ref_key)
                    if exact:
                        return exact
                    # In-memory fuzzy match (no DB round-trip per row).
                    # Uses the BEST of two algorithms, since each
                    # catches a different real-world abbreviation
                    # style:
                    #   - token_set_ratio: short code IS a whole word
                    #     from the full name (e.g. "Savitri" inside
                    #     "Savitri Steel Cement Traders", "Balaji"
                    #     inside "Balaji Homedecor").
                    #   - partial_ratio: short code has NO spaces
                    #     while the full name does (e.g. "ShriRam"
                    #     vs "Shri Ram & Co" — token-based matching
                    #     finds zero common whole-tokens here, since
                    #     "shriram" and "shri"/"ram" are different
                    #     tokens, even though character-wise they are
                    #     clearly the same reference).
                    best_score = 0
                    best_match = None
                    for record in distributor_cache.values():
                        for candidate_text in (record["name"], record["firm_name"], record["firm_nick_name"]):
                            if not candidate_text:
                                continue
                            normalized_candidate = self._normalize_text(candidate_text).lower()
                            token_score = fuzz.token_set_ratio(ref_key, normalized_candidate)
                            # partial_ratio is only safe to use for
                            # reasonably long references (5+ chars) —
                            # for very short codes (e.g. "PTM", "SPA",
                            # 3 chars), a coincidental short substring
                            # match against ANY long candidate name can
                            # inflate the score and cause false
                            # positives. token_set_ratio alone already
                            # correctly handles genuine short-code
                            # cases where the code IS a whole word from
                            # the name (e.g. "Savitri", "Balaji").
                            partial_score = (
                                fuzz.partial_ratio(ref_key, normalized_candidate)
                                if len(ref_key) >= 5
                                else 0
                            )
                            # Also compare with spaces/special
                            # characters stripped from both sides —
                            # catches "ShriRam" (no space) against
                            # "Shri Ram & Co" (has a space and an
                            # ampersand) cleanly, where the space
                            # alone was enough to push the plain
                            # partial_ratio just below threshold.
                            despaced_score = (
                                fuzz.partial_ratio(
                                    re.sub(r"[^a-z0-9]", "", ref_key),
                                    re.sub(r"[^a-z0-9]", "", normalized_candidate),
                                )
                                if len(ref_key) >= 5
                                else 0
                            )
                            score = max(token_score, partial_score, despaced_score)
                            if score > best_score:
                                best_score = score
                                best_match = record
                    if best_score >= 88:
                        return best_match
                    return None

                for row in rows:
                    try:
                        name = self._canonicalize_known_master_name(
                            self._get_row_value(
                                row, "retailer_name", "Retailer Name", template_config
                            )
                        )
                        name_key = self._normalize_text(name).lower()
                        if not name_key:
                            skipped += 1
                            continue
                        distributor_reference = self._canonicalize_known_master_name(
                            self._get_row_value(
                                row,
                                "linked_distributor_gst_or_name",
                                "Distributor",
                                template_config,
                            )
                        )
                        retailer_code = self._normalize_text(
                            self._get_row_value(
                                row, "retailer_code", "Retailer Code", template_config
                            )
                        )
                        location = self._normalize_text(
                            self._get_row_value(
                                row, "location", "Location", template_config
                            )
                        )
                        phone_number = self._normalize_text(
                            self._get_row_value(
                                row, "phone_number", "Phone", template_config
                            )
                        )
                        email = self._normalize_text(
                            self._get_row_value(row, "email", "Email", template_config)
                        )
                        address = self._normalize_text(
                            self._get_row_value(
                                row, "address", "Address", template_config
                            )
                        )
                        gst_no = self._normalize_gst_no(
                            self._get_row_value(row, "gst_no", "GSTIN", template_config)
                        )
                        contact_person = self._normalize_text(
                            self._get_row_value(
                                row, "contact_person", "Contact Person Name", template_config
                            )
                        )
                        state = self._normalize_text(
                            self._get_row_value(row, "state", "State", template_config)
                        )
                        pincode = self._normalize_text(
                            self._get_row_value(row, "pincode", "Pincode", template_config)
                        )
                        category = self._normalize_text(
                            self._get_row_value(row, "category", "Category", template_config)
                        )
                        birthday = self._normalize_text(
                            self._get_row_value(row, "birthday", "Birthday", template_config)
                        )
                        anniversary = self._normalize_text(
                            self._get_row_value(
                                row, "anniversary", "Anniversary", template_config
                            )
                        )
                        phone_number_2 = self._normalize_text(
                            self._get_row_value(
                                row, "phone_number_2", "Mobile 2", template_config
                            )
                        )
                        secondary_retailer_name = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_retailer_name",
                                "Secondary Retailer Name",
                                template_config,
                            )
                        )
                        secondary_retailer_phone_number = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_retailer_phone_number",
                                "Secondary Retailer Mobile Number",
                                template_config,
                            )
                        )
                        secondary_retailer_birthday = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_retailer_birthday",
                                "Secondary Retailer Birthday",
                                template_config,
                            )
                        )
                        secondary_retailer_anniversary = self._normalize_text(
                            self._get_row_value(
                                row,
                                "secondary_retailer_anniversary",
                                "Secondary Retailer Anniversary",
                                template_config,
                            )
                        )
                        sales_executive_name = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_name",
                                "Sales Executive Name",
                                template_config,
                            )
                        )
                        sales_executive_phone_number = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_phone_number",
                                "Sales Executive Mobile Number",
                                template_config,
                            )
                        )
                        sales_executive_email = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_email",
                                "Sales Executive Email",
                                template_config,
                            )
                        )
                        sales_executive_birthday = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_birthday",
                                "Sales Executive Birthday",
                                template_config,
                            )
                        )
                        sales_executive_anniversary = self._normalize_text(
                            self._get_row_value(
                                row,
                                "sales_executive_anniversary",
                                "Sales Executive Anniversary",
                                template_config,
                            )
                        )
                        distributor = None
                        if distributor_reference:
                            # In-memory match (exact, then fuzzy) —
                            # no per-row database round-trips.
                            distributor = _match_distributor_in_memory(distributor_reference)
                        # If no confident match was found (or the
                        # reference was blank), the retailer is saved
                        # as UNASSIGNED — it must still appear in the
                        # retailers list, ready for the user to link
                        # manually later. It is never silently
                        # discarded, and no new distributor is ever
                        # auto-created from a raw text guess.
                        if distributor is None:
                            unassigned += 1
                        distributor_id_value = distributor["id"] if distributor else None
                        existing_retailer_id = self._find_similar_master_entry(
                            conn,
                            "master_retailers",
                            "name",
                            name,
                            extra_filters={
                                "distributor_id": distributor_id_value,
                                "workspace_id": workspace_id,
                                "user_id": user_id,
                            },
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
                                    sales_executive_anniversary = COALESCE(NULLIF(?, ''), sales_executive_anniversary),
                                    contact_person = COALESCE(NULLIF(?, ''), contact_person),
                                    state = COALESCE(NULLIF(?, ''), state),
                                    pincode = COALESCE(NULLIF(?, ''), pincode),
                                    category = COALESCE(NULLIF(?, ''), category),
                                    birthday = COALESCE(NULLIF(?, ''), birthday),
                                    anniversary = COALESCE(NULLIF(?, ''), anniversary),
                                    phone_number_2 = COALESCE(NULLIF(?, ''), phone_number_2)
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
                                    contact_person or "",
                                    state or "",
                                    pincode or "",
                                    category or "",
                                    birthday or "",
                                    anniversary or "",
                                    phone_number_2 or "",
                                    int(existing_retailer_id),
                                ),
                            )
                            updated += 1
                            continue
                        retailer_id = self.add_master_retailer(
                            name=name,
                            distributor_id=distributor_id_value,
                            location=location or None,
                            retailer_code=retailer_code or None,
                            phone_number=phone_number or None,
                            email=email or None,
                            address=address or None,
                            gst_no=gst_no or None,
                            secondary_retailer_name=secondary_retailer_name or None,
                            secondary_retailer_phone_number=secondary_retailer_phone_number
                            or None,
                            secondary_retailer_birthday=secondary_retailer_birthday
                            or None,
                            secondary_retailer_anniversary=secondary_retailer_anniversary
                            or None,
                            sales_executive_name=sales_executive_name or None,
                            sales_executive_phone_number=sales_executive_phone_number
                            or None,
                            sales_executive_email=sales_executive_email or None,
                            sales_executive_birthday=sales_executive_birthday or None,
                            sales_executive_anniversary=sales_executive_anniversary
                            or None,
                            contact_person=contact_person or None,
                            state=state or None,
                            pincode=pincode or None,
                            category=category or None,
                            birthday=birthday or None,
                            anniversary=anniversary or None,
                            phone_number_2=phone_number_2 or None,
                            workspace_id=workspace_id,
                            user_id=user_id,
                            conn=conn,
                            refresh_search_index=False,
                        )
                        inserted += 1
                    except Exception as exc:  # pragma: no cover - defensive path
                        errors.append(str(exc))
                        skipped += 1

            # PERFORMANCE: refresh the search index exactly ONCE for
            # the whole batch, not once per row (see the note on
            # add_master_distributor/add_master_retailer's
            # refresh_search_index parameter).
            self._refresh_global_search_index(conn)

        self.firebase_sync.push_record(
            {
                "type": "bulk_master_upload",
                "master_type": master_type,
                "inserted": inserted,
                "skipped": skipped,
            }
        )
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "unassigned": unassigned,
            "ambiguous_distributor_matches": ambiguous_distributor_refs,
            "errors": errors,
            "rows_processed": len(rows),
        }

    def add_article_v2(self, payload: dict[str, Any], workspace_id: str = "default") -> int:
        """
        Insert into article_master_v2 — the detailed textile-product
        catalog used by the /article-master search page (brand, size,
        print style, MRP, PTR, etc.). No bulk-upload path exists for
        this table yet; that is Phase 2 work.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO article_master_v2 (
                    brand, tc, size, bs_size, product, print_style, bale_size,
                    colors, mrp, selling_price, ptr, retailer_margin, exmill_price,
                    created_at, workspace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("brand"),
                    payload.get("tc"),
                    payload.get("size"),
                    payload.get("bs_size"),
                    payload.get("product"),
                    payload.get("print_style"),
                    payload.get("bale_size"),
                    payload.get("colors"),
                    float(payload.get("mrp", 0) or 0),
                    float(payload.get("selling_price", 0) or 0),
                    float(payload.get("ptr", 0) or 0),
                    float(payload.get("retailer_margin", 0) or 0),
                    float(payload.get("exmill_price", 0) or 0),
                    created_at,
                    workspace_id,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def bulk_upload_articles(
        self, path: str | Path, template_config: dict[str, Any] | None = None,
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        rows = self._load_rows_from_upload(path)
        inserted = 0
        skipped = 0
        errors: list[str] = []

        with sqlite3.connect(self.db_path) as conn:
            for row in rows:
                try:
                    payload = {
                        "category_name": self._normalize_text(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "category_name", "Category"
                                )
                            )
                        ),
                        "design_code": self._normalize_text(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "design_code", "Design Code"
                                )
                            )
                        ),
                        "color_way": self._normalize_text(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "color_way", "Colour"
                                )
                            )
                        ),
                        "base_rate": self._coerce_float(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "base_rate", "Base Rate"
                                )
                            )
                        ),
                        "gst_percentage": self._coerce_float(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "gst_percentage", "GST %"
                                )
                            )
                        ),
                        "pcs_per_bale": self._coerce_float(
                            row.get(
                                self._resolve_template_header(
                                    template_config, "pcs_per_bale", "Pcs / Bale"
                                )
                            )
                        ),
                    }
                    if not payload["category_name"] and not payload["design_code"]:
                        skipped += 1
                        continue
                    self.article_service.save_article(payload, conn=conn, workspace_id=workspace_id)
                    inserted += 1
                except Exception as exc:  # pragma: no cover - defensive path
                    errors.append(str(exc))
                    skipped += 1

        return {
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
            "rows_processed": len(rows),
        }

    def build_article_master_from_order_sheet(
        self, path: str | Path, template_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        dataframe = self._load_order_sheet_dataframe(path)
        rows = dataframe.to_dict(orient="records")
        inserted = 0
        skipped = 0
        duplicates = 0
        errors: list[str] = []

        category_header = self._resolve_template_header(
            template_config, "category_name", "Product"
        )
        design_header = self._resolve_template_header(
            template_config, "design_code", "Brand"
        )
        variant_header = self._resolve_template_header(
            template_config, "variant", "Size"
        )
        color_header = self._resolve_template_header(
            template_config, "color_way", "Print Style"
        )
        base_rate_header = self._resolve_template_header(
            template_config, "base_rate", "ExMill Price"
        )
        fallback_base_rate_header = self._resolve_template_header(
            template_config, "fallback_base_rate", "Selling Price"
        )
        gst_header = self._resolve_template_header(
            template_config, "gst_percentage", "GST %"
        )
        pcs_header = self._resolve_template_header(
            template_config, "pcs_per_bale", "Min bale pack"
        )
        fallback_pcs_header = self._resolve_template_header(
            template_config, "fallback_pcs_per_bale", "Bale Size"
        )

        def _article_key(
            category_name: str,
            design_name: str,
            color_way: str,
            base_rate: float,
            gst_percentage: float,
            pcs_per_bale: float,
        ) -> tuple[str, str, str, float, float, float]:
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
                _article_key(
                    str(item[0] or ""),
                    str(item[1] or ""),
                    str(item[2] or ""),
                    float(item[3] or 0.0),
                    float(item[4] or 0.0),
                    float(item[5] or 0.0),
                )
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
                        base_rate = self._coerce_float(
                            row.get(fallback_base_rate_header)
                        )

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

                    sanitized_payload = self.article_service.sanitize_article_payload(
                        payload
                    )
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

                    self.article_service.save_article(payload, conn=conn, workspace_id=workspace_id)
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

    def _find_master_distributor_by_gst_or_name(
        self, reference: str, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        canonical_reference = self._canonicalize_known_master_name(reference)
        lookup_values = [self._normalize_text(canonical_reference)]
        original_value = self._normalize_text(reference)
        if original_value and original_value not in lookup_values:
            lookup_values.append(original_value)

        if not lookup_values[0]:
            return None
        lookup_values_lc = [str(v).lower() for v in lookup_values]
        conditions = " OR ".join("LOWER(name) = ?" for _ in lookup_values)
        conditions += " OR LOWER(gst_no) = ?"
        if workspace_id:
            conditions = f"({conditions}) AND workspace_id = ?"
        params: list[Any] = [*lookup_values_lc, lookup_values_lc[0]]
        if workspace_id:
            params.append(workspace_id)
        query = (
            "SELECT id, distributor_id, firm_name, firm_nick_name, name, gst_no, zone, region, location, address, pincode, payment_terms, birthday, anniversary, credit_limit, latitude, longitude, phone_number, email, status, created_at FROM master_distributors WHERE "
            + conditions
            + " LIMIT 1"
        )
        with sqlite3.connect(self.db_path) as conn:
            exact = conn.execute(query, params).fetchone()
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

    def _find_or_create_distributor_from_reference(
        self, reference: str, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        if not self._normalize_text(reference):
            return None
        return (
            self.get_master_distributor_by_name(reference, workspace_id=workspace_id)
            or self.add_master_distributor(
                name=reference,
                gst_no=None,
                zone=None,
                region=None,
                credit_limit=None,
                workspace_id=workspace_id,
            )
        )

    def add_master_distributor(
        self,
        name: str,
        firm_name: str | None = None,
        firm_nick_name: str | None = None,
        gst_no: str | None = None,
        buyer_code: str | None = None,
        zone: str | None = None,
        territory: str | None = None,
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
        phone_number_2: str | None = None,
        contact_person_role: str | None = None,
        status: str = "active",
        workspace_id: str = "default",
        user_id: int | None = None,
        conn: sqlite3.Connection | None = None,
        allow_fuzzy: bool = True,
        refresh_search_index: bool = True,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        canonical_name = self._canonicalize_known_master_name(name)
        canonical_firm_name = (
            self._canonicalize_known_master_name(firm_name)
            if firm_name is not None
            else None
        )
        connection = conn or sqlite3.connect(self.db_path)
        should_close = conn is None
        user_clause, user_params = self._user_id_sql("user_id", user_id)
        try:
            # Prefer exact GST match when GST number is provided
            if gst_no:
                gst_row = connection.execute(
                    "SELECT id FROM master_distributors WHERE LOWER(COALESCE(gst_no, '')) = ?"
                    f" AND workspace_id = ? AND {user_clause} LIMIT 1",
                    (str(gst_no).lower(), workspace_id, *user_params),
                ).fetchone()
                if gst_row:
                    return int(gst_row[0])
            if buyer_code:
                buyer_row = connection.execute(
                    "SELECT id FROM master_distributors WHERE LOWER(COALESCE(buyer_code, '')) = ?"
                    f" AND workspace_id = ? AND {user_clause} LIMIT 1",
                    (str(buyer_code).lower(), workspace_id, *user_params),
                ).fetchone()
                if buyer_row:
                    return int(buyer_row[0])
            existing_id = None
            if allow_fuzzy:
                existing_id = self._find_similar_master_entry(
                    connection,
                    "master_distributors",
                    "name",
                    canonical_name,
                    extra_filters={"workspace_id": workspace_id, "user_id": user_id},
                )
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
                    workspace_id,
                    user_id,
                    phone_number,
                    location,
                    address,
                    pincode,
                    email,
                    gst_no,
                    buyer_code,
                    zone,
                    territory,
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
                    contact_person_role
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    distributor_code or self._generate_unique_master_id("D"),
                    distributor_code or None,
                    canonical_firm_name,
                    firm_nick_name,
                    canonical_name,
                    workspace_id,
                    user_id,
                    phone_number,
                    location,
                    address,
                    pincode,
                    email,
                    gst_no,
                    buyer_code,
                    zone,
                    territory,
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
                    contact_person_role,
                ),
            )
            new_id = int(cursor.lastrowid)
            if phone_number_2:
                connection.execute(
                    "UPDATE master_distributors SET phone_number_2 = ? WHERE id = ? AND workspace_id = ?",
                    (phone_number_2, new_id, workspace_id),
                )
            # PERFORMANCE: only commit here if THIS call opened the
            # connection itself. When bulk_upload_masters() passes in
            # a shared conn for the whole batch, committing on every
            # single row (1651+ times, observed via profiling to cost
            # ~4.3 of ~7.8 total seconds) is redundant — the caller's
            # own `with sqlite3.connect(...) as conn:` block already
            # commits once when the whole batch finishes.
            if should_close:
                connection.commit()
            if refresh_search_index:
                # PERFORMANCE: this rebuilds the ENTIRE global search
                # index from scratch — safe for a single manual add,
                # but ruinously slow if called once per row during a
                # bulk upload (a real-world 2000+ row upload would
                # effectively hang). bulk_upload_masters() passes
                # refresh_search_index=False per row and refreshes
                # ONCE after the whole batch completes instead.
                self._refresh_global_search_index(connection)
            return int(cursor.lastrowid)
        finally:
            if should_close:
                connection.close()

    def get_master_distributor_by_name(
        self, name: str, workspace_id: str | None = None, user_id: int | None = None
    ) -> dict[str, Any] | None:
        canonical_name = self._canonicalize_known_master_name(name)
        lookup_values = [self._normalize_text(canonical_name)]
        original_value = self._normalize_text(name)
        if original_value and original_value not in lookup_values:
            lookup_values.append(original_value)

        with sqlite3.connect(self.db_path) as conn:
            where_clauses = " OR ".join("LOWER(name) = ?" for _ in lookup_values)
            params = [str(v).lower() for v in lookup_values]
            if workspace_id:
                where_clauses = f"({where_clauses}) AND workspace_id = ?"
                params.append(workspace_id)
            user_clause, user_params = self._user_id_sql("user_id", user_id)
            where_clauses = f"({where_clauses}) AND {user_clause}"
            params.extend(user_params)
            query = (
                "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, buyer_code, zone, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at FROM master_distributors WHERE "
                + where_clauses
                + " LIMIT 1"
            )
            row = conn.execute(query, params).fetchone()
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
            "buyer_code": row[12],
            "zone": row[13],
            "region": row[14],
            "payment_terms": row[15],
            "birthday": row[16],
            "anniversary": row[17],
            "secondary_distributor_name": row[18],
            "secondary_distributor_phone_number": row[19],
            "secondary_distributor_birthday": row[20],
            "secondary_distributor_anniversary": row[21],
            "sales_executive_name": row[22],
            "sales_executive_phone_number": row[23],
            "sales_executive_email": row[24],
            "sales_executive_birthday": row[25],
            "sales_executive_anniversary": row[26],
            "credit_limit": row[27],
            "latitude": row[28],
            "longitude": row[29],
            "status": row[30],
            "created_at": row[31],
        }

    def get_master_distributor_by_gst(
        self, gst_no: str, workspace_id: str | None = None, user_id: int | None = None
    ) -> dict[str, Any] | None:
        if not self._normalize_text(gst_no):
            return None
        query = (
            "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, buyer_code, zone, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at FROM master_distributors WHERE LOWER(COALESCE(gst_no, '')) = ?"
        )
        params: list[Any] = [str(gst_no).strip().lower()]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        user_clause, user_params = self._user_id_sql("user_id", user_id)
        query += f" AND {user_clause}"
        params.extend(user_params)
        query += " LIMIT 1"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
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
            "buyer_code": row[12],
            "zone": row[13],
            "region": row[14],
            "payment_terms": row[15],
            "birthday": row[16],
            "anniversary": row[17],
            "secondary_distributor_name": row[18],
            "secondary_distributor_phone_number": row[19],
            "secondary_distributor_birthday": row[20],
            "secondary_distributor_anniversary": row[21],
            "sales_executive_name": row[22],
            "sales_executive_phone_number": row[23],
            "sales_executive_email": row[24],
            "sales_executive_birthday": row[25],
            "sales_executive_anniversary": row[26],
            "credit_limit": row[27],
            "latitude": row[28],
            "longitude": row[29],
            "status": row[30],
            "created_at": row[31],
        }

    def get_master_distributor_by_buyer_code(
        self, buyer_code: str, workspace_id: str | None = None, user_id: int | None = None
    ) -> dict[str, Any] | None:
        if not self._normalize_text(buyer_code):
            return None
        query = (
            "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, buyer_code, zone, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at FROM master_distributors WHERE LOWER(COALESCE(buyer_code, '')) = ?"
        )
        params: list[Any] = [str(buyer_code).lower()]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        user_clause, user_params = self._user_id_sql("user_id", user_id)
        query += f" AND {user_clause}"
        params.extend(user_params)
        query += " LIMIT 1"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
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
            "buyer_code": row[12],
            "zone": row[13],
            "region": row[14],
            "payment_terms": row[15],
            "birthday": row[16],
            "anniversary": row[17],
            "secondary_distributor_name": row[18],
            "secondary_distributor_phone_number": row[19],
            "secondary_distributor_birthday": row[20],
            "secondary_distributor_anniversary": row[21],
            "sales_executive_name": row[22],
            "sales_executive_phone_number": row[23],
            "sales_executive_email": row[24],
            "sales_executive_birthday": row[25],
            "sales_executive_anniversary": row[26],
            "credit_limit": row[27],
            "latitude": row[28],
            "longitude": row[29],
            "status": row[30],
            "created_at": row[31],
        }

    def get_master_distributor(
        self,
        distributor_id: int,
        workspace_id: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT id, distributor_id, distributor_code, firm_name, firm_nick_name, name, phone_number, location, address, pincode, email, gst_no, buyer_code, zone, territory, region, payment_terms, birthday, anniversary, secondary_distributor_name, secondary_distributor_phone_number, secondary_distributor_birthday, secondary_distributor_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, credit_limit, latitude, longitude, status, created_at, phone_number_2, contact_person_role FROM master_distributors WHERE id = ?"
        params: list[Any] = [distributor_id]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
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
            "buyer_code": row[12],
            "zone": row[13],
            "territory": row[14],
            "region": row[15],
            "payment_terms": row[16],
            "birthday": row[17],
            "anniversary": row[18],
            "secondary_distributor_name": row[19],
            "secondary_distributor_phone_number": row[20],
            "secondary_distributor_birthday": row[21],
            "secondary_distributor_anniversary": row[22],
            "sales_executive_name": row[23],
            "sales_executive_phone_number": row[24],
            "sales_executive_email": row[25],
            "sales_executive_birthday": row[26],
            "sales_executive_anniversary": row[27],
            "credit_limit": row[28],
            "latitude": row[29],
            "longitude": row[30],
            "status": row[31],
            "created_at": row[32],
            "phone_number_2": row[33],
            "contact_person_role": row[34],
        }

    # Every column on master_distributors that's safe to update via the API -
    # deliberately excludes id, workspace_id (tenant boundary, never
    # reassignable), and created_at (immutable). Matches exactly the column
    # set already proven to exist by get_master_distributor()'s SELECT list.
    _DISTRIBUTOR_UPDATABLE_FIELDS = {
        "distributor_id", "distributor_code", "firm_name", "firm_nick_name", "name",
        "phone_number", "phone_number_2", "location", "address", "pincode", "email", "gst_no",
        "buyer_code", "zone", "territory", "region", "payment_terms", "birthday", "anniversary",
        "secondary_distributor_name", "secondary_distributor_phone_number",
        "secondary_distributor_birthday", "secondary_distributor_anniversary",
        "sales_executive_name", "sales_executive_phone_number", "sales_executive_email",
        "sales_executive_birthday", "sales_executive_anniversary",
        "credit_limit", "latitude", "longitude", "status",
        "contact_person_role",
    }

    def update_master_distributor(
        self,
        distributor_id: int,
        workspace_id: str,
        user_id: int | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """
        Partial update - only the fields actually passed are changed, every
        other column is left untouched. Workspace-scoped: a distributor can
        only be updated by the workspace that owns it - if distributor_id
        exists but belongs to a different workspace, this returns None
        (same "not found" signal as a genuinely missing id, so callers can't
        distinguish "wrong workspace" from "doesn't exist" - which is the
        correct behavior for a cross-tenant safety boundary).

        When user_id is provided, ownership is also enforced.

        Returns the updated record (same shape as get_master_distributor()),
        or None if no matching row was found in this workspace.
        """
        unknown = set(fields) - self._DISTRIBUTOR_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update unknown/protected field(s): {sorted(unknown)}")
        if not fields:
            return self.get_master_distributor(
                distributor_id, workspace_id=workspace_id, user_id=user_id
            )

        # Always bump updated_at for delta sync clients.
        fields = dict(fields)
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{col} = ?" for col in fields)
        params = list(fields.values()) + [distributor_id, workspace_id]
        where = "WHERE id = ? AND workspace_id = ?"
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"UPDATE master_distributors SET {set_clause} {where}",
                params,
            )
            if cursor.rowcount == 0:
                return None

        return self.get_master_distributor(
            distributor_id, workspace_id=workspace_id, user_id=user_id
        )

    def delete_master_distributor(
        self, distributor_id: int, workspace_id: str, user_id: int | None = None
    ) -> bool:
        """
        Hard-delete a master distributor from this workspace.
        Linked master retailers keep their rows; distributor_id is cleared.
        Returns True if a row was deleted, False otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            retailer_where = "WHERE distributor_id = ? AND workspace_id = ?"
            retailer_params: list[Any] = [distributor_id, workspace_id]
            if user_id is not None:
                retailer_where += " AND user_id = ?"
                retailer_params.append(user_id)
            conn.execute(
                f"UPDATE master_retailers SET distributor_id = NULL {retailer_where}",
                retailer_params,
            )
            dist_where = "WHERE id = ? AND workspace_id = ?"
            dist_params: list[Any] = [distributor_id, workspace_id]
            if user_id is not None:
                dist_where += " AND user_id = ?"
                dist_params.append(user_id)
            cursor = conn.execute(
                f"DELETE FROM master_distributors {dist_where}",
                dist_params,
            )
            return cursor.rowcount > 0

    def list_master_distributors(
        self,
        limit: int = 50,
        workspace_id: str | None = None,
        offset: int = 0,
        include_inactive: bool = True,
        since: str | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if user_id is None:
            return []
        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        params: list[Any] = []
        query = """
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
                    buyer_code,
                    zone,
                    territory,
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
                    created_at,
                    phone_number_2,
                    updated_at,
                    contact_person_role
                FROM master_distributors
                """
        where_parts: list[str] = ["user_id = ?"]
        params.append(user_id)
        if workspace_id:
            where_parts.append("workspace_id = ?")
            params.append(workspace_id)
        if not include_inactive:
            where_parts.append("IFNULL(status, 'active') != 'inactive'")
        since_norm = (since or "").strip()
        if since_norm:
            # Delta: rows created/updated at or after watermark (ISO or comparable text).
            where_parts.append("COALESCE(updated_at, created_at) >= ?")
            params.append(since_norm)
        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        # Active first, then name — past/inactive still visible with red light on clients.
        query += """
            ORDER BY
                CASE WHEN lower(IFNULL(status, 'active')) = 'inactive' THEN 1 ELSE 0 END,
                lower(IFNULL(firm_name, name)) ASC,
                id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([safe_limit, safe_offset])
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

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
                "buyer_code": row[12],
                "zone": row[13],
                "territory": row[14],
                "region": row[15],
                "payment_terms": row[16],
                "birthday": row[17],
                "anniversary": row[18],
                "secondary_distributor_name": row[19],
                "secondary_distributor_phone_number": row[20],
                "secondary_distributor_birthday": row[21],
                "secondary_distributor_anniversary": row[22],
                "sales_executive_name": row[23],
                "sales_executive_phone_number": row[24],
                "sales_executive_email": row[25],
                "sales_executive_birthday": row[26],
                "sales_executive_anniversary": row[27],
                "credit_limit": row[28],
                "status": row[29] or "active",
                "created_at": row[30],
                "phone_number_2": row[31],
                "updated_at": row[32],
                "contact_person_role": row[33],
            }
            for row in rows
        ]

    def get_party_master_fingerprint(
        self, workspace_id: str | None = None, user_id: int | None = None
    ) -> dict[str, Any]:
        """
        Tiny sync stamp for mobile multi-device Party Master.
        Create / edit / delete changes the stamp so clients pull only when stale.
        Fingerprint is scoped to the JWT user's rows only.
        """
        if user_id is None:
            return {
                "fingerprint": "d:0:0:0|r:0:0:0",
                "distributor_count": 0,
                "retailer_count": 0,
                "distributor_max_id": 0,
                "retailer_max_id": 0,
                "distributor_stamp": 0,
                "retailer_stamp": 0,
            }
        ws = (workspace_id or "").strip() or None

        def _stamp_distributors() -> tuple[int, int, int]:
            q = """
                SELECT
                    COUNT(*),
                    IFNULL(MAX(id), 0),
                    IFNULL(SUM(
                        id
                        + LENGTH(IFNULL(name, ''))
                        + LENGTH(IFNULL(firm_name, ''))
                        + LENGTH(IFNULL(phone_number, ''))
                        + LENGTH(IFNULL(address, ''))
                        + LENGTH(IFNULL(location, ''))
                        + LENGTH(IFNULL(gst_no, ''))
                        + LENGTH(IFNULL(status, ''))
                        + LENGTH(IFNULL(contact_person_role, ''))
                    ), 0)
                FROM master_distributors
                WHERE IFNULL(status, 'active') != 'inactive'
                  AND user_id = ?
            """
            params: list[Any] = [user_id]
            if ws:
                q += " AND workspace_id = ?"
                params.append(ws)
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(q, tuple(params)).fetchone()
            return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

        def _stamp_retailers() -> tuple[int, int, int]:
            q = """
                SELECT
                    COUNT(*),
                    IFNULL(MAX(id), 0),
                    IFNULL(SUM(
                        id
                        + LENGTH(IFNULL(name, ''))
                        + LENGTH(IFNULL(phone_number, ''))
                        + LENGTH(IFNULL(address, ''))
                        + LENGTH(IFNULL(location, ''))
                        + LENGTH(IFNULL(gst_no, ''))
                        + LENGTH(IFNULL(CAST(distributor_id AS TEXT), ''))
                    ), 0)
                FROM master_retailers
                WHERE IFNULL(status, 'active') != 'inactive'
                  AND user_id = ?
            """
            params: list[Any] = [user_id]
            if ws:
                q += " AND workspace_id = ?"
                params.append(ws)
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(q, tuple(params)).fetchone()
            return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

        d_count, d_max, d_stamp = _stamp_distributors()
        r_count, r_max, r_stamp = _stamp_retailers()
        fingerprint = f"d:{d_count}:{d_max}:{d_stamp}|r:{r_count}:{r_max}:{r_stamp}"
        return {
            "fingerprint": fingerprint,
            "distributor_count": d_count,
            "retailer_count": r_count,
            "distributor_max_id": d_max,
            "retailer_max_id": r_max,
            "distributor_stamp": d_stamp,
            "retailer_stamp": r_stamp,
        }

    def get_master_retailer_by_name(
        self, name: str, workspace_id: str | None = None, user_id: int | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT id, retailer_id, retailer_code, name, distributor_id, location, latitude, longitude, status, created_at, phone_number, email, address, gst_no, secondary_retailer_name, secondary_retailer_phone_number, secondary_retailer_birthday, secondary_retailer_anniversary, sales_executive_name, sales_executive_phone_number, sales_executive_email, sales_executive_birthday, sales_executive_anniversary, owner_name FROM master_retailers WHERE LOWER(name) = ?"
        params: list[Any] = [str(name or "").strip().lower()]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        user_clause, user_params = self._user_id_sql("user_id", user_id)
        query += f" AND {user_clause}"
        params.extend(user_params)
        query += " LIMIT 1"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
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

    def list_master_retailers(
        self,
        limit: int = 50,
        workspace_id: str | None = None,
        offset: int = 0,
        since: str | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if user_id is None:
            return []
        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        params: list[Any] = []
        # JOIN distributor name in SQL — avoids loading a second full Party Master into RAM.
        query = """
                SELECT
                    mr.id, mr.retailer_id, mr.retailer_code, mr.name, mr.distributor_id,
                    mr.location, mr.latitude, mr.longitude, mr.status, mr.created_at,
                    mr.phone_number, mr.email, mr.address, mr.gst_no,
                    mr.secondary_retailer_name, mr.secondary_retailer_phone_number,
                    mr.secondary_retailer_birthday, mr.secondary_retailer_anniversary,
                    mr.sales_executive_name, mr.sales_executive_phone_number,
                    mr.sales_executive_email, mr.sales_executive_birthday,
                    mr.sales_executive_anniversary, mr.owner_name, mr.contact_person,
                    mr.state, mr.pincode, mr.category, mr.birthday, mr.anniversary,
                    mr.phone_number_2,
                    COALESCE(md.firm_name, md.name) AS distributor_name,
                    mr.updated_at
                FROM master_retailers mr
                LEFT JOIN master_distributors md
                    ON md.id = mr.distributor_id
                    AND (mr.workspace_id IS NULL OR md.workspace_id = mr.workspace_id)
                """
        where_parts: list[str] = ["mr.user_id = ?"]
        params.append(user_id)
        if workspace_id:
            where_parts.append("mr.workspace_id = ?")
            params.append(workspace_id)
        where_parts.append("IFNULL(mr.status, 'active') != 'inactive'")
        since_norm = (since or "").strip()
        if since_norm:
            where_parts.append("COALESCE(mr.updated_at, mr.created_at) >= ?")
            params.append(since_norm)
        query += " WHERE " + " AND ".join(where_parts)
        query += " ORDER BY mr.id DESC LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

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
                "contact_person": row[24],
                "state": row[25],
                "pincode": row[26],
                "category": row[27],
                "birthday": row[28],
                "anniversary": row[29],
                "phone_number_2": row[30],
                "distributor_name": row[31] or "Unassigned",
                "updated_at": row[32],
            }
            for row in rows
        ]

    def add_master_retailer(
        self,
        name: str,
        distributor_id: int | None,
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
        contact_person: str | None = None,
        state: str | None = None,
        pincode: str | None = None,
        category: str | None = None,
        birthday: str | None = None,
        anniversary: str | None = None,
        phone_number_2: str | None = None,
        workspace_id: str = "default",
        user_id: int | None = None,
        conn: sqlite3.Connection | None = None,
        refresh_search_index: bool = True,
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
                extra_filters={
                    "distributor_id": distributor_id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                },
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
                    workspace_id,
                    user_id,
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
                    owner_name,
                    contact_person,
                    state,
                    pincode,
                    category,
                    birthday,
                    anniversary,
                    phone_number_2
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._generate_unique_master_id("R"),
                    retailer_code or None,
                    name,
                    distributor_id,
                    workspace_id,
                    user_id,
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
                    None,
                    contact_person,
                    state,
                    pincode,
                    category,
                    birthday,
                    anniversary,
                    phone_number_2,
                ),
            )
            # PERFORMANCE: same fix as add_master_distributor — only
            # commit here if this call owns the connection.
            if should_close:
                connection.commit()
            if refresh_search_index:
                self._refresh_global_search_index(connection)
            return int(cursor.lastrowid)
        finally:
            if should_close:
                connection.close()

    _RETAILER_UPDATABLE_FIELDS = {
        "retailer_id", "retailer_code", "name", "distributor_id", "location",
        "phone_number", "email", "address", "gst_no",
        "secondary_retailer_name", "secondary_retailer_phone_number",
        "secondary_retailer_birthday", "secondary_retailer_anniversary",
        "sales_executive_name", "sales_executive_phone_number", "sales_executive_email",
        "sales_executive_birthday", "sales_executive_anniversary",
        "owner_name", "latitude", "longitude", "status",
        # These 7 were being accepted by add_master_retailer() and stored
        # correctly, but get_master_retailer() never selected them back out
        # (see the fixed get_master_retailer() below) - included here too
        # since they're genuinely updatable columns, not a separate concept.
        "contact_person", "state", "pincode", "category", "birthday", "anniversary",
        "phone_number_2",
    }

    def update_master_retailer(
        self,
        retailer_id: int,
        workspace_id: str,
        user_id: int | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """
        Same partial-update, workspace-scoped pattern as
        update_master_distributor(). One extra rule: reassigning
        distributor_id must point at a real distributor in THIS workspace -
        silently accepting a bad/foreign id would orphan the retailer or
        leak a cross-tenant reference, so this raises ValueError instead
        (loud failure, not a silent no-op or cross-tenant leak).
        """
        unknown = set(fields) - self._RETAILER_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update unknown/protected field(s): {sorted(unknown)}")

        if "distributor_id" in fields and fields["distributor_id"] is not None:
            distributor = self.get_master_distributor(
                fields["distributor_id"], workspace_id=workspace_id, user_id=user_id
            )
            if distributor is None:
                raise ValueError(
                    f"distributor_id {fields['distributor_id']} does not exist "
                    f"in workspace {workspace_id!r}"
                )

        if not fields:
            return self.get_master_retailer(
                retailer_id, workspace_id=workspace_id, user_id=user_id
            )

        fields = dict(fields)
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{col} = ?" for col in fields)
        params = list(fields.values()) + [retailer_id, workspace_id]
        where = "WHERE id = ? AND workspace_id = ?"
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"UPDATE master_retailers SET {set_clause} {where}",
                params,
            )
            if cursor.rowcount == 0:
                return None

        return self.get_master_retailer(
            retailer_id, workspace_id=workspace_id, user_id=user_id
        )

    def delete_master_retailer(
        self, retailer_id: int, workspace_id: str, user_id: int | None = None
    ) -> bool:
        """Hard-delete a master retailer from this workspace."""
        with sqlite3.connect(self.db_path) as conn:
            where = "WHERE id = ? AND workspace_id = ?"
            params: list[Any] = [retailer_id, workspace_id]
            if user_id is not None:
                where += " AND user_id = ?"
                params.append(user_id)
            cursor = conn.execute(
                f"DELETE FROM master_retailers {where}",
                params,
            )
            return cursor.rowcount > 0

    # BUG FIX: add_master_retailer() accepts and stores contact_person,
    # state, pincode, category, birthday, anniversary, phone_number_2 - but
    # this SELECT previously never included them, so they silently vanished
    # from every API response even though they were saved correctly in the
    # DB (confirmed by test_get_master_retailer_includes_previously_missing_fields).
    # Uses a shared columns-list (same pattern as elsewhere in this file)
    # so the SELECT and dict-building can never drift out of sync again.
    _RETAILER_COLUMNS = [
        "id", "retailer_id", "retailer_code", "name", "distributor_id", "location",
        "latitude", "longitude", "status", "created_at", "phone_number", "email",
        "address", "gst_no", "secondary_retailer_name", "secondary_retailer_phone_number",
        "secondary_retailer_birthday", "secondary_retailer_anniversary",
        "sales_executive_name", "sales_executive_phone_number", "sales_executive_email",
        "sales_executive_birthday", "sales_executive_anniversary", "owner_name",
        "contact_person", "state", "pincode", "category", "birthday", "anniversary",
        "phone_number_2",
    ]

    def get_master_retailer(
        self,
        retailer_id: int,
        workspace_id: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        cols = ", ".join(self._RETAILER_COLUMNS)
        query = f"SELECT {cols} FROM master_retailers WHERE id = ?"
        params: list[Any] = [retailer_id]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        return dict(zip(self._RETAILER_COLUMNS, row))

    def bulk_upload_targets_achievements(self, path: str | Path, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        imported = 0
        with sqlite3.connect(self.db_path) as conn:
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    conn.execute(
                        """
                        INSERT INTO targets_achievements (year, month, distributor_id, zone, target_amount, achievement_amount, created_at, workspace_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(row["year"]),
                            row["month"],
                            int(row["distributor_id"]),
                            row.get("zone"),
                            float(row.get("target_amount", 0) or 0),
                            float(row.get("achievement_amount", 0) or 0),
                            created_at,
                            workspace_id,
                        ),
                    )
                    imported += 1
            conn.commit()
        return imported

    def get_target_variance_summary(
        self,
        distributor_id: int | None = None,
        year: int | None = None,
        zone: str | None = None,
    ) -> dict[str, Any]:
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
            variance_percentage = (
                0.0
                if target_amount == 0
                else ((achievement_amount - target_amount) / target_amount) * 100
            )
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

        overall_variance_percentage = (
            0.0
            if total_target == 0
            else round(((total_achievement / total_target) * 100) - 100, 2)
        )
        return {
            "rows": summary_rows,
            "overall_variance_percentage": round(overall_variance_percentage, 2),
        }

    def record_primary_sales(self, payload: dict[str, Any], workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO primary_sales (distributor_id, invoice_no, invoice_date, quantity, amount, created_at, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["distributor_id"]),
                    payload.get("invoice_no"),
                    payload.get("invoice_date"),
                    float(payload.get("quantity", 0) or 0),
                    float(payload.get("amount", 0) or 0),
                    created_at,
                    workspace_id,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def bulk_upload_secondary_sales(self, path: str | Path, workspace_id: str = "default") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        imported = 0
        with sqlite3.connect(self.db_path) as conn:
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    conn.execute(
                        """
                        INSERT INTO secondary_sales (distributor_id, retailer_id, invoice_no, sale_date, quantity, amount, created_at, workspace_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(row["distributor_id"]),
                            int(row["retailer_id"]),
                            row.get("invoice_no"),
                            row.get("sale_date"),
                            float(row.get("quantity", 0) or 0),
                            float(row.get("amount", 0) or 0),
                            created_at,
                            workspace_id,
                        ),
                    )
                    imported += 1
            conn.commit()
        return imported

    def get_sales_flow_summary(
        self, distributor_id: int | None = None, workspace_id: str | None = None
    ) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            if workspace_id:
                primary_row = conn.execute(
                    "SELECT COALESCE(SUM(quantity), 0) FROM primary_sales WHERE (? IS NULL OR distributor_id = ?) AND workspace_id = ?",
                    (distributor_id, distributor_id, workspace_id),
                ).fetchone()
                secondary_row = conn.execute(
                    "SELECT COALESCE(SUM(quantity), 0) FROM secondary_sales WHERE (? IS NULL OR distributor_id = ?) AND workspace_id = ?",
                    (distributor_id, distributor_id, workspace_id),
                ).fetchone()
            else:
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
        variance_percentage = (
            0.0 if primary_volume == 0 else ((secondary_volume / primary_volume) * 100)
        )
        return {
            "primary_volume": round(primary_volume, 2),
            "secondary_volume": round(secondary_volume, 2),
            "difference": round(difference, 2),
            "variance_percentage": round(variance_percentage, 2),
        }

    # ============ ORDER SHEET MASTER METHODS ============

    def _hash_file_reference(self, file_reference: str | None) -> str | None:
        if not file_reference:
            return None

        path = Path(file_reference)
        if not path.exists() or not path.is_file():
            return None

        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return None

    def add_order_sheet(
        self,
        name: str,
        category: str,
        file_reference: str | None = None,
        workspace_id: str = "default",
        is_active: int = 1,
        content_fingerprint: str | None = None,
        user_id: int | None = None,
    ) -> int:
        """Add a new order sheet to master. Returns the id of the inserted record."""
        created_at = datetime.now(timezone.utc).isoformat()
        uploaded_at = datetime.now(timezone.utc).isoformat()

        if not content_fingerprint:
            content_fingerprint = self._hash_file_reference(file_reference)

        with sqlite3.connect(self.db_path) as conn:
            self._ensure_column_exists(conn, "order_sheet_master", "user_id", "INTEGER")
            if content_fingerprint:
                existing_sql = """
                    SELECT id, file_reference
                    FROM order_sheet_master
                    WHERE workspace_id = ? AND category = ?
                """
                existing_params: list[Any] = [workspace_id, category]
                if user_id is not None:
                    existing_sql += " AND user_id = ?"
                    existing_params.append(user_id)
                existing_sql += " ORDER BY uploaded_at DESC"
                existing_rows = conn.execute(existing_sql, tuple(existing_params)).fetchall()
                for existing_id, existing_file_reference in existing_rows:
                    if not existing_file_reference:
                        continue
                    existing_hash = self._hash_file_reference(existing_file_reference)
                    if existing_hash and existing_hash == content_fingerprint:
                        return int(existing_id)

            cursor = conn.execute(
                """
                INSERT INTO order_sheet_master (
                    name, category, uploaded_at, workspace_id, file_reference,
                    is_active, created_at, user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    category,
                    uploaded_at,
                    workspace_id,
                    file_reference,
                    is_active,
                    created_at,
                    user_id,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_order_sheet(
        self,
        id: int,
        workspace_id: str = "default",
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a single order sheet by id. Returns None if not found or workspace mismatch."""
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_column_exists(conn, "order_sheet_master", "user_id", "INTEGER")
            query = """
                SELECT id, name, category, uploaded_at, workspace_id, file_reference,
                       is_active, created_at, user_id
                FROM order_sheet_master
                WHERE id = ? AND workspace_id = ?
            """
            params: list[Any] = [id, workspace_id]
            if user_id is not None:
                query += " AND user_id = ?"
                params.append(user_id)
            row = conn.execute(query, tuple(params)).fetchone()

            if not row:
                return None

            return {
                "id": row[0],
                "name": row[1],
                "category": row[2],
                "uploaded_at": row[3],
                "workspace_id": row[4],
                "file_reference": row[5],
                "is_active": row[6],
                "created_at": row[7],
                "user_id": row[8],
            }

    def list_order_sheets(
        self,
        category: str | None = None,
        workspace_id: str = "default",
        limit: int = 100,
        offset: int = 0,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List order sheets filtered by category, workspace, and optional owner."""
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_column_exists(conn, "order_sheet_master", "user_id", "INTEGER")
            query = (
                "SELECT id, name, category, uploaded_at, workspace_id, file_reference, "
                "is_active, created_at, user_id FROM order_sheet_master WHERE workspace_id = ?"
            )
            params: list[Any] = [workspace_id]

            if user_id is not None:
                query += " AND user_id = ?"
                params.append(user_id)

            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY uploaded_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()

            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "category": row[2],
                    "uploaded_at": row[3],
                    "workspace_id": row[4],
                    "file_reference": row[5],
                    "is_active": row[6],
                    "created_at": row[7],
                    "user_id": row[8],
                }
                for row in rows
            ]

    def update_order_sheet_active_status(
        self,
        id: int,
        is_active: int,
        workspace_id: str = "default",
    ) -> bool:
        """Update the is_active status of an order sheet. Returns True if updated, False if not found."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE order_sheet_master
                SET is_active = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (is_active, id, workspace_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_dashboard_payload(self, workspace_id: str | None = None) -> dict[str, Any]:
        distributor_query = "SELECT COUNT(*) FROM master_distributors"
        distributor_params: list[Any] = []
        if workspace_id:
            distributor_query += " WHERE workspace_id = ?"
            distributor_params.append(workspace_id)

        retailer_query = "SELECT COUNT(*) FROM master_retailers"
        retailer_params: list[Any] = []
        if workspace_id:
            retailer_query += " WHERE workspace_id = ?"
            retailer_params.append(workspace_id)

        targets_query = "SELECT COUNT(*) FROM targets_achievements"
        target_params: list[Any] = []
        if workspace_id and self._table_has_column("targets_achievements", "workspace_id"):
            targets_query += " WHERE workspace_id = ?"
            target_params.append(workspace_id)

        primary_query = "SELECT COALESCE(SUM(quantity), 0) FROM primary_sales"
        primary_params: list[Any] = []
        if workspace_id and self._table_has_column("primary_sales", "workspace_id"):
            primary_query += " WHERE workspace_id = ?"
            primary_params.append(workspace_id)

        secondary_query = "SELECT COALESCE(SUM(quantity), 0) FROM secondary_sales"
        secondary_params: list[Any] = []
        if workspace_id and self._table_has_column("secondary_sales", "workspace_id"):
            secondary_query += " WHERE workspace_id = ?"
            secondary_params.append(workspace_id)

        with sqlite3.connect(self.db_path) as conn:
            distributors_count = conn.execute(distributor_query, distributor_params).fetchone()[0]
            retailers_count = conn.execute(retailer_query, retailer_params).fetchone()[0]
            targets_rows = conn.execute(targets_query, target_params).fetchone()[0]
            primary_total = conn.execute(primary_query, primary_params).fetchone()[0]
            secondary_total = conn.execute(secondary_query, secondary_params).fetchone()[0]
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

    def save_article(self, payload: dict[str, Any], workspace_id: str = "default") -> int:
        return self.article_service.save_article(payload, workspace_id=workspace_id)

    def list_articles_by_category(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return self.article_service.list_articles_by_category(workspace_id=workspace_id)

    def sanitize_article_payload(
        self, payload: dict[str, Any], existing_categories: list[str] | None = None
    ) -> dict[str, Any]:
        return self.article_service.sanitize_article_payload(
            payload, existing_categories
        )

    def get_company_profile(self, workspace_id: str) -> dict[str, Any] | None:
        """
        Returns the calling workspace's OWN company identity — used so
        the app can tell "our own GST" apart from a buyer/distributor's
        GST when parsing SO/CI documents. Per-workspace (one row per
        workspace_id) — each subscriber's own company details stay
        entirely separate.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM company_profile WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_company_profile(
        self,
        workspace_id: str,
        company_name: str,
        gst_number: str | None = None,
        pan_number: str | None = None,
        address: str | None = None,
        city: str | None = None,
        state: str | None = None,
        pincode: str | None = None,
    ) -> dict[str, Any]:
        cleaned_name = (company_name or "").strip()
        if not cleaned_name:
            raise ValueError("company_name is required")
        if not workspace_id:
            raise ValueError("workspace_id is required")

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO company_profile
                    (workspace_id, company_name, gst_number, pan_number, address, city, state, pincode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    company_name = excluded.company_name,
                    gst_number = excluded.gst_number,
                    pan_number = excluded.pan_number,
                    address = excluded.address,
                    city = excluded.city,
                    state = excluded.state,
                    pincode = excluded.pincode,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    cleaned_name,
                    (gst_number or "").strip().upper() or None,
                    (pan_number or "").strip().upper() or None,
                    address,
                    city,
                    state,
                    pincode,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_company_profile(workspace_id)

    def upsert_business_rule(
        self, rule_key: str, rule_value: str, is_locked: bool = True
    ) -> int:
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
            row = conn.execute(
                "SELECT id FROM business_rules WHERE rule_key = ?", (cleaned_key,)
            ).fetchone()
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
                "contact_person_role",
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

    def export_master_distributors_excel(
        self, workspace_id: str | None = None, user_id: int | None = None
    ) -> bytes:
        columns = [
            "id",
            "distributor_id",
            "firm_name",
            "firm_nick_name",
            "name",
            "contact_person_role",
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
        rows = self._read_table_rows(
            "master_distributors", columns, workspace_id=workspace_id, user_id=user_id
        )
        df = pd.DataFrame(rows, columns=columns)
        buffer = BytesIO()
        df.to_excel(buffer, index=False)
        return buffer.getvalue()

    def export_master_distributors_csv(
        self, workspace_id: str | None = None, user_id: int | None = None
    ) -> bytes:
        columns = [
            "id", "distributor_id", "firm_name", "firm_nick_name", "name",
            "contact_person_role",
            "phone_number", "location", "address", "pincode", "email", "gst_no",
            "zone", "region", "payment_terms", "birthday", "anniversary",
            "secondary_distributor_name", "secondary_distributor_phone_number",
            "secondary_distributor_birthday", "secondary_distributor_anniversary",
            "sales_executive_name", "sales_executive_phone_number",
            "sales_executive_email", "sales_executive_birthday",
            "sales_executive_anniversary", "credit_limit", "status", "created_at",
        ]
        rows = self._read_table_rows(
            "master_distributors", columns, workspace_id=workspace_id, user_id=user_id
        )
        df = pd.DataFrame(rows, columns=columns)
        return df.to_csv(index=False).encode("utf-8")

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

    def _read_table_rows(
        self,
        table_name: str,
        columns: list[str],
        workspace_id: str | None = None,
        distributor_id: int | None = None,
        user_id: int | None = None,
    ) -> list[list[Any]]:
        if user_id is None and table_name in ("master_distributors", "master_retailers"):
            return []
        query = f"SELECT {', '.join(columns)} FROM {table_name}"
        clauses = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if distributor_id is not None:
            clauses.append("distributor_id = ?")
            params.append(distributor_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
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

    def _read_master_retailer_rows_with_distributor_name(
        self,
        columns: list[str],
        workspace_id: str | None = None,
        distributor_id: int | None = None,
        user_id: int | None = None,
    ) -> list[list[Any]]:
        """
        Same as _read_table_rows("master_retailers", columns, ...) but
        replaces the raw distributor_id column with the distributor's
        actual name — showing a bare internal ID number in an export
        is not usable/readable for a real person.
        """
        if user_id is None:
            return []
        select_parts = []
        for col in columns:
            if col == "distributor_id":
                select_parts.append(
                    "COALESCE(md.firm_name, md.name, 'Unassigned') AS distributor_id"
                )
            else:
                select_parts.append(f"mr.{col}")
        query = (
            f"SELECT {', '.join(select_parts)} FROM master_retailers mr "
            "LEFT JOIN master_distributors md ON mr.distributor_id = md.id"
        )
        clauses = ["mr.user_id = ?"]
        params: list[Any] = [user_id]
        if workspace_id:
            clauses.append("mr.workspace_id = ?")
            params.append(workspace_id)
        if distributor_id is not None:
            clauses.append("mr.distributor_id = ?")
            params.append(distributor_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [list(row) for row in rows]

    def export_master_retailers_excel(
        self,
        workspace_id: str | None = None,
        distributor_id: int | None = None,
        user_id: int | None = None,
    ) -> bytes:
        columns = [
            "id",
            "retailer_id",
            "retailer_code",
            "name",
            "distributor_id",
            "location",
            "phone_number",
            "phone_number_2",
            "email",
            "address",
            "state",
            "pincode",
            "gst_no",
            "contact_person",
            "category",
            "birthday",
            "anniversary",
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
        ]
        display_columns = ["distributor_name" if c == "distributor_id" else c for c in columns]
        rows = self._read_master_retailer_rows_with_distributor_name(
            columns,
            workspace_id=workspace_id,
            distributor_id=distributor_id,
            user_id=user_id,
        )
        df = pd.DataFrame(rows, columns=display_columns)
        buffer = BytesIO()
        df.to_excel(buffer, index=False)
        return buffer.getvalue()

    def export_master_retailers_csv(
        self,
        workspace_id: str | None = None,
        distributor_id: int | None = None,
        user_id: int | None = None,
    ) -> bytes:
        columns = [
            "id", "retailer_id", "retailer_code", "name", "distributor_id",
            "location", "phone_number", "phone_number_2", "email", "address",
            "state", "pincode", "gst_no", "contact_person", "category",
            "birthday", "anniversary",
            "secondary_retailer_name", "secondary_retailer_phone_number",
            "secondary_retailer_birthday", "secondary_retailer_anniversary",
            "sales_executive_name", "sales_executive_phone_number",
            "sales_executive_email", "sales_executive_birthday",
            "sales_executive_anniversary", "status", "created_at",
        ]
        display_columns = ["distributor_name" if c == "distributor_id" else c for c in columns]
        rows = self._read_master_retailer_rows_with_distributor_name(
            columns,
            workspace_id=workspace_id,
            distributor_id=distributor_id,
            user_id=user_id,
        )
        df = pd.DataFrame(rows, columns=display_columns)
        return df.to_csv(index=False).encode("utf-8")

    def claim_unowned_masters(
        self, workspace_id: str, user_id: int
    ) -> dict[str, int]:
        """
        Assign legacy unowned rows (user_id IS NULL) in this workspace to the
        given user: Party Master, Target Achievement years, and order sheets.

        Safe for the historical owner (e.g. kunwar1del) after hard per-user
        isolation. New users who never claim (and never hit lazy-claim paths)
        keep empty lists. Rows already owned by another user_id are never moved.
        """
        if not user_id:
            raise ValueError("user_id is required")
        ws = (workspace_id or "").strip() or "default"
        with sqlite3.connect(self.db_path) as conn:
            dist_cur = conn.execute(
                """
                UPDATE master_distributors
                SET user_id = ?
                WHERE workspace_id = ? AND user_id IS NULL
                """,
                (user_id, ws),
            )
            ret_cur = conn.execute(
                """
                UPDATE master_retailers
                SET user_id = ?
                WHERE workspace_id = ? AND user_id IS NULL
                """,
                (user_id, ws),
            )
            target_claimed = 0
            years_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(target_achievement_years)"
                ).fetchall()
            }
            if "user_id" in years_cols and "workspace_id" in years_cols:
                tgt_cur = conn.execute(
                    """
                    UPDATE target_achievement_years
                    SET user_id = ?
                    WHERE workspace_id = ? AND user_id IS NULL
                    """,
                    (user_id, ws),
                )
                target_claimed = int(tgt_cur.rowcount or 0)

            sheets_claimed = 0
            sheets_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "order_sheets" in sheets_tables:
                sheet_cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(order_sheets)").fetchall()
                }
                if "user_id" in sheet_cols and "workspace_id" in sheet_cols:
                    sheet_cur = conn.execute(
                        """
                        UPDATE order_sheets
                        SET user_id = ?
                        WHERE workspace_id = ? AND user_id IS NULL
                        """,
                        (user_id, ws),
                    )
                    sheets_claimed = int(sheet_cur.rowcount or 0)

            conn.commit()
        return {
            "distributors_claimed": int(dist_cur.rowcount or 0),
            "retailers_claimed": int(ret_cur.rowcount or 0),
            "target_years_claimed": target_claimed,
            "order_sheets_claimed": sheets_claimed,
        }

    def export_targets_achievements(self) -> str:
        return self.export_table(
            "targets_achievements",
            [
                "id",
                "year",
                "month",
                "distributor_id",
                "zone",
                "target_amount",
                "achievement_amount",
                "created_at",
            ],
        )

    def export_primary_sales(self) -> str:
        return self.export_table(
            "primary_sales",
            [
                "id",
                "distributor_id",
                "invoice_no",
                "invoice_date",
                "quantity",
                "amount",
                "created_at",
            ],
        )

    def export_secondary_sales(self) -> str:
        return self.export_table(
            "secondary_sales",
            [
                "id",
                "distributor_id",
                "retailer_id",
                "invoice_no",
                "sale_date",
                "quantity",
                "amount",
                "created_at",
            ],
        )

    def ensure_executive_visits_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executive_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    user_id INTEGER,
                    username TEXT,
                    party_type TEXT NOT NULL,
                    party_id INTEGER,
                    party_name TEXT,
                    visit_date TEXT NOT NULL,
                    notes TEXT,
                    follow_up_date TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_executive_visits_workspace ON executive_visits(workspace_id, visit_date DESC)"
            )
            conn.commit()

    def list_executive_visits(
        self,
        workspace_id: str = "default",
        limit: int = 50,
        party_type: str | None = None,
        party_id: int | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_executive_visits_table()
        query = (
            "SELECT id, workspace_id, user_id, username, party_type, party_id, party_name, "
            "visit_date, notes, follow_up_date, created_at "
            "FROM executive_visits WHERE workspace_id = ?"
        )
        params: list[Any] = [workspace_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if party_type:
            query += " AND party_type = ?"
            params.append(party_type)
        if party_id is not None:
            query += " AND party_id = ?"
            params.append(party_id)
        query += " ORDER BY visit_date DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": r[0],
                "workspace_id": r[1],
                "user_id": r[2],
                "username": r[3],
                "party_type": r[4],
                "party_id": r[5],
                "party_name": r[6],
                "visit_date": r[7],
                "notes": r[8],
                "follow_up_date": r[9],
                "created_at": r[10],
            }
            for r in rows
        ]

    def create_executive_visit(
        self,
        *,
        workspace_id: str,
        user_id: int | None,
        username: str | None,
        party_type: str,
        party_id: int | None,
        party_name: str,
        visit_date: str,
        notes: str | None = None,
        follow_up_date: str | None = None,
    ) -> int:
        self.ensure_executive_visits_table()
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO executive_visits (
                    workspace_id, user_id, username, party_type, party_id, party_name,
                    visit_date, notes, follow_up_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    user_id,
                    username,
                    party_type,
                    party_id,
                    party_name,
                    visit_date,
                    notes,
                    follow_up_date,
                    created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def count_master_parties(self, workspace_id: str = "default") -> dict[str, int]:
        distributors = 0
        retailers = 0
        with sqlite3.connect(self.db_path) as conn:
            if self._table_has_column("master_distributors", "workspace_id"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM master_distributors WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
                distributors = int(row[0] or 0) if row else 0
            else:
                row = conn.execute("SELECT COUNT(*) FROM master_distributors").fetchone()
                distributors = int(row[0] or 0) if row else 0
            if self._table_has_column("master_retailers", "workspace_id"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM master_retailers WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
                retailers = int(row[0] or 0) if row else 0
            else:
                row = conn.execute("SELECT COUNT(*) FROM master_retailers").fetchone()
                retailers = int(row[0] or 0) if row else 0
        return {"distributors": distributors, "retailers": retailers}

    def build_executive_pending_actions(
        self, workspace_id: str = "default", user_id: int | None = None
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        tracking = self.list_order_lifecycle_tracking(workspace_id=workspace_id, limit=500)
        for row in tracking:
            if not row.get("has_sales_order"):
                actions.append(
                    {
                        "type": "missing_so",
                        "severity": "high",
                        "title": f"SO missing — {row.get('distributor_name') or 'Distributor'}",
                        "detail": f"Order Ref {row.get('order_ref_no') or '—'} has no Sales Order uploaded yet.",
                        "tracking_id": row.get("tracking_id"),
                        "distributor_id": row.get("distributor_id"),
                    }
                )
            elif not row.get("has_commercial_invoice"):
                actions.append(
                    {
                        "type": "missing_ci",
                        "severity": "medium",
                        "title": f"CI missing — {row.get('distributor_name') or 'Distributor'}",
                        "detail": f"Order Ref {row.get('order_ref_no') or '—'} has SO but no Commercial Invoice.",
                        "tracking_id": row.get("tracking_id"),
                        "distributor_id": row.get("distributor_id"),
                    }
                )
            payment = (row.get("payment_status") or "").strip().lower()
            if payment and payment not in {"paid", "received", "complete", "completed"}:
                actions.append(
                    {
                        "type": "payment_pending",
                        "severity": "medium",
                        "title": f"Payment pending — {row.get('distributor_name') or 'Distributor'}",
                        "detail": f"Order Ref {row.get('order_ref_no') or '—'}: status {row.get('payment_status')}.",
                        "tracking_id": row.get("tracking_id"),
                        "distributor_id": row.get("distributor_id"),
                    }
                )

        if user_id is not None:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    rows = conn.execute(
                        """
                        SELECT fo.id, fo.distributor_name_raw, fo.category, fo.created_at
                        FROM filled_orders fo
                        LEFT JOIN filled_order_so_link l ON l.filled_order_id = fo.id
                        WHERE fo.user_id = ? AND l.filled_order_id IS NULL
                        ORDER BY fo.id DESC
                        LIMIT 50
                        """,
                        (user_id,),
                    ).fetchall()
                    for r in rows:
                        actions.append(
                            {
                                "type": "filled_order_unlinked",
                                "severity": "high",
                                "title": f"Filled order not linked to SO — {r[1] or 'Distributor'}",
                                "detail": f"{r[2] or 'Order'} uploaded {r[3] or ''} — link to Sales Order.",
                                "filled_order_id": r[0],
                            }
                        )
                except sqlite3.OperationalError:
                    pass

        self.ensure_executive_visits_table()
        today = datetime.now(timezone.utc).date().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            follow_rows = conn.execute(
                """
                SELECT id, party_name, party_type, party_id, follow_up_date, notes
                FROM executive_visits
                WHERE workspace_id = ? AND follow_up_date IS NOT NULL AND follow_up_date <= ?
                ORDER BY follow_up_date ASC
                LIMIT 30
                """,
                (workspace_id, today),
            ).fetchall()
        for r in follow_rows:
            actions.append(
                {
                    "type": "visit_follow_up",
                    "severity": "low",
                    "title": f"Follow up visit — {r[1] or 'Party'}",
                    "detail": r[5] or f"Follow-up due {r[4]}",
                    "visit_id": r[0],
                    "party_type": r[2],
                    "party_id": r[3],
                }
            )

        return actions[:80]

    def upsert_target_distributor_breakup(
        self,
        *,
        workspace_id: str,
        financial_year_id: int,
        distributor_name: str,
        achievement_lakhs: float,
        target_lakhs: float | None = None,
        nick: str | None = None,
        source: str = "upload",
    ) -> None:
        """Store distributor-wise target/achievement (amounts in lakhs).

        Writes only the named source column (manual / ci / excel). Other
        channels on the same distributor row are preserved.
        """
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache("target_achievement_breakup")
        resolved = self.resolve_ta_distributor_reference(
            distributor_name, workspace_id, nick
        )
        display_name = resolved["distributor_name"]
        store_nick = resolved.get("nick")
        distributor_id = resolved.get("distributor_id")
        source_name = resolved.get("source_distributor_name")

        achievement_amount = float(achievement_lakhs or 0)
        target_amount = float(target_lakhs) if target_lakhs is not None else None

        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "distributor_name" in cols:
                self._consolidate_breakup_rows_for_resolved(
                    conn, workspace_id, financial_year_id, resolved, cols
                )
            if "attribute_type" in cols and "attribute_name" in cols:
                if resolved["source_distributor_name"] != display_name:
                    conn.execute(
                        """
                        DELETE FROM target_achievement_breakup
                        WHERE financial_year_id = ? AND attribute_type = 'distributor'
                          AND attribute_name = ?
                        """,
                        (financial_year_id, resolved["source_distributor_name"]),
                    )
                has_split = (
                    "achievement_excel" in cols
                    and "achievement_ci" in cols
                    and "achievement_manual" in cols
                )
                source_col = self._breakup_source_column(source)
                select_cols = ["target_amount"]
                if has_split:
                    select_cols.extend(
                        ["achievement_excel", "achievement_ci", "achievement_manual"]
                    )
                row = conn.execute(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM target_achievement_breakup
                    WHERE financial_year_id = ? AND attribute_type = 'distributor'
                      AND attribute_name = ?
                    """,
                    (financial_year_id, display_name),
                ).fetchone()
                existing_target = float(row[0] or 0) if row else 0.0
                excel = float(row[1] or 0) if row and has_split else 0.0
                ci = float(row[2] or 0) if row and has_split else 0.0
                manual = float(row[3] or 0) if row and has_split else 0.0
                if has_split:
                    if source_col == "achievement_manual":
                        manual = achievement_amount
                    elif source_col == "achievement_ci":
                        ci = achievement_amount
                    else:
                        excel = achievement_amount
                    stored_amount = excel + ci + manual
                    stored_source = self._derived_breakup_source(excel, ci, manual)
                else:
                    stored_amount = achievement_amount
                    stored_source = source
                final_target = target_amount if target_amount is not None else existing_target
                achievement_percent = (
                    round((stored_amount / final_target) * 100, 2) if final_target > 0 else 0.0
                )
                has_ws = "workspace_id" in cols
                if row:
                    sets = [
                        "achievement_amount = ?",
                        "achievement_percent = ?",
                        "target_amount = ?",
                        "source = ?",
                    ]
                    params: list[Any] = [
                        stored_amount,
                        achievement_percent,
                        final_target,
                        stored_source,
                    ]
                    if has_split:
                        sets.extend(
                            [
                                "achievement_excel = ?",
                                "achievement_ci = ?",
                                "achievement_manual = ?",
                            ]
                        )
                        params.extend([excel, ci, manual])
                    params.extend([financial_year_id, display_name])
                    conn.execute(
                        f"""
                        UPDATE target_achievement_breakup
                        SET {", ".join(sets)}
                        WHERE financial_year_id = ? AND attribute_type = 'distributor'
                          AND attribute_name = ?
                        """,
                        tuple(params),
                    )
                else:
                    insert_cols = [
                        "financial_year_id",
                        "attribute_type",
                        "attribute_name",
                        "target_amount",
                        "achievement_amount",
                        "achievement_percent",
                        "source",
                    ]
                    vals: list[Any] = [
                        financial_year_id,
                        "distributor",
                        display_name,
                        final_target,
                        stored_amount,
                        achievement_percent,
                        stored_source,
                    ]
                    if has_ws:
                        insert_cols.insert(0, "workspace_id")
                        vals.insert(0, workspace_id)
                    if has_split:
                        insert_cols.extend(
                            ["achievement_excel", "achievement_ci", "achievement_manual"]
                        )
                        vals.extend([excel, ci, manual])
                    placeholders = ", ".join("?" for _ in insert_cols)
                    conn.execute(
                        f"""
                        INSERT INTO target_achievement_breakup ({", ".join(insert_cols)})
                        VALUES ({placeholders})
                        """,
                        tuple(vals),
                    )
                identity_sets: list[str] = []
                identity_params: list[Any] = []
                if "nick" in cols and store_nick:
                    identity_sets.append("nick = ?")
                    identity_params.append(store_nick)
                if "distributor_id" in cols:
                    identity_sets.append("distributor_id = ?")
                    identity_params.append(distributor_id)
                if "source_distributor_name" in cols:
                    identity_sets.append("source_distributor_name = ?")
                    identity_params.append(source_name)
                if identity_sets:
                    identity_params.extend(
                        [financial_year_id, display_name]
                    )
                    conn.execute(
                        f"""
                        UPDATE target_achievement_breakup
                        SET {', '.join(identity_sets)}
                        WHERE financial_year_id = ? AND attribute_type = 'distributor'
                          AND attribute_name = ?
                        """,
                        tuple(identity_params),
                    )
            elif "distributor_name" in cols:
                source_col = self._breakup_source_column(source)
                ach_value = float(achievement_lakhs or 0)
                has_ws = "workspace_id" in cols
                select_sql = (
                    "SELECT id, target_lakhs FROM target_achievement_breakup "
                    "WHERE financial_year_id = ? AND distributor_name = ?"
                )
                select_params: list[Any] = [financial_year_id, display_name]
                if has_ws:
                    select_sql += " AND workspace_id = ?"
                    select_params.append(workspace_id)
                row = conn.execute(select_sql, tuple(select_params)).fetchone()
                if row:
                    sets = [f"{source_col} = ?"]
                    params: list[Any] = [ach_value]
                    if store_nick and "nick" in cols:
                        sets.append("nick = ?")
                        params.append(store_nick)
                    if "distributor_id" in cols:
                        sets.append("distributor_id = ?")
                        params.append(distributor_id)
                    if "source_distributor_name" in cols:
                        sets.append("source_distributor_name = ?")
                        params.append(source_name)
                    if target_amount is not None and "target_lakhs" in cols:
                        sets.append("target_lakhs = ?")
                        params.append(float(target_amount))
                    if has_ws:
                        sets.append("workspace_id = ?")
                        params.append(workspace_id)
                    update_sql = (
                        f"UPDATE target_achievement_breakup SET {', '.join(sets)} "
                        "WHERE financial_year_id = ? AND distributor_name = ?"
                    )
                    params.extend([financial_year_id, display_name])
                    if has_ws:
                        update_sql += " AND workspace_id = ?"
                        params.append(workspace_id)
                    conn.execute(update_sql, tuple(params))
                else:
                    insert_cols = ["financial_year_id", "distributor_name", source_col]
                    vals: list[Any] = [financial_year_id, display_name, ach_value]
                    if "year_id" in cols:
                        insert_cols.insert(0, "year_id")
                        vals.insert(0, financial_year_id)
                    if "workspace_id" in cols:
                        insert_cols.insert(0, "workspace_id")
                        vals.insert(0, workspace_id)
                    if store_nick and "nick" in cols:
                        insert_cols.append("nick")
                        vals.append(store_nick)
                    if "distributor_id" in cols:
                        insert_cols.append("distributor_id")
                        vals.append(distributor_id)
                    if "source_distributor_name" in cols:
                        insert_cols.append("source_distributor_name")
                        vals.append(source_name)
                    if target_amount is not None and "target_lakhs" in cols:
                        insert_cols.append("target_lakhs")
                        vals.append(float(target_amount))
                    placeholders = ", ".join("?" for _ in insert_cols)
                    conn.execute(
                        f"INSERT INTO target_achievement_breakup ({', '.join(insert_cols)}) VALUES ({placeholders})",
                        tuple(vals),
                    )
            conn.commit()

    def import_sales_excel_achievement(
        self,
        workspace_id: str,
        financial_year_id: int,
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Import distributor excel achievement for a FY from parsed sales file.
        Preserves distributor targets, CI, and manual achievement columns.
        """
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache(
            "target_achievement_breakup", "target_achievement_category_breakup"
        )
        preserved_targets: dict[str, float] = {}
        distributors = parsed.get("distributors") or []
        dist_by_name = {
            (row.get("name") or "").strip(): row
            for row in distributors
            if (row.get("name") or "").strip()
        }
        names_to_keep: set[str] = set()
        for raw_name, row in dist_by_name.items():
            names_to_keep.add(raw_name)
            resolved = self.resolve_ta_distributor_reference(
                raw_name, workspace_id, row.get("nick")
            )
            names_to_keep.add(resolved["distributor_name"])

        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "distributor_name" in cols:
                params: list[Any] = [financial_year_id]
                query = (
                    "SELECT distributor_name, COALESCE(target_lakhs, 0) AS target_lakhs "
                    "FROM target_achievement_breakup WHERE financial_year_id = ?"
                )
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                query += " AND COALESCE(target_lakhs, 0) > 0"
                rows = conn.execute(query, tuple(params)).fetchall()
                for r in rows:
                    raw_key = r[0]
                    target_val = float(r[1])
                    preserved_targets[raw_key] = target_val
                    resolved = self.resolve_ta_distributor_reference(
                        raw_key, workspace_id
                    )
                    canonical = resolved["distributor_name"]
                    if canonical not in preserved_targets:
                        preserved_targets[canonical] = target_val

                if "achievement_excel" in cols:
                    clear_params: list[Any] = [financial_year_id]
                    clear_sql = (
                        "UPDATE target_achievement_breakup SET achievement_excel = 0 "
                        "WHERE financial_year_id = ?"
                    )
                    if "workspace_id" in cols:
                        clear_sql += " AND workspace_id = ?"
                        clear_params.append(workspace_id)
                    if names_to_keep:
                        placeholders = ", ".join("?" for _ in names_to_keep)
                        clear_sql += f" AND distributor_name NOT IN ({placeholders})"
                        clear_params.extend(sorted(names_to_keep))
                    conn.execute(clear_sql, tuple(clear_params))
                conn.commit()

        for row in distributors:
            name = (row.get("name") or "Unknown").strip()
            resolved = self.resolve_ta_distributor_reference(
                name, workspace_id, row.get("nick")
            )
            target = preserved_targets.get(name) or preserved_targets.get(
                resolved["distributor_name"]
            )
            self.upsert_target_distributor_breakup(
                workspace_id=workspace_id,
                financial_year_id=financial_year_id,
                distributor_name=name,
                achievement_lakhs=float(row.get("achievement_lakhs") or 0),
                target_lakhs=target,
                nick=row.get("nick"),
                source="excel_upload",
            )

        category_count = 0
        if parsed.get("categories"):
            category_count = self.replace_category_breakup(
                workspace_id, financial_year_id, parsed.get("categories") or []
            )

        self.relink_target_achievement_distributors(workspace_id)
        total_lakhs = self.sync_financial_year_achievement_from_breakup(
            workspace_id, financial_year_id
        )
        link_stats = {"relinked": True}
        return {
            "distributor_count": len(distributors),
            "category_row_count": category_count,
            "total_achievement_lakhs": total_lakhs,
            "has_category_detail": bool(category_count),
            "category_matrix": parsed.get("category_matrix") or {},
            "file_kind": "achievement",
            "distributor_link_stats": link_stats,
        }

    def import_sales_excel_targets(
        self,
        workspace_id: str,
        financial_year_id: int,
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        """Import distributor FY targets (lakhs) from budget-style Excel."""
        distributors = parsed.get("distributors") or []
        for row in distributors:
            name = (row.get("name") or "Unknown").strip()
            target = float(row.get("target_lakhs") or row.get("achievement_lakhs") or 0)
            self.set_target_distributor_target_lakhs(
                workspace_id=workspace_id,
                financial_year_id=financial_year_id,
                distributor_name=name,
                target_lakhs=target,
                nick=row.get("nick"),
            )
        self.relink_target_achievement_distributors(workspace_id)
        total_target = round(
            sum(float(row.get("target_lakhs") or row.get("achievement_lakhs") or 0) for row in distributors),
            4,
        )
        self.sync_financial_year_target_from_breakup(workspace_id, financial_year_id)
        return {
            "distributor_count": len(distributors),
            "total_target_lakhs": total_target,
            "file_kind": "budget",
        }

    def clear_fy_excel_achievement(
        self, workspace_id: str, financial_year_id: int
    ) -> dict[str, Any]:
        """Remove Excel-upload achievement only; keeps manual + CI + targets."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache(
            "target_achievement_breakup", "target_achievement_category_breakup"
        )
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            params: list[Any] = [financial_year_id]
            ws_clause = ""
            if "workspace_id" in cols:
                ws_clause = " AND workspace_id = ?"
                params.append(workspace_id)
            sets: list[str] = []
            if "achievement_excel" in cols:
                sets.append("achievement_excel = 0")
            # Legacy single column was Excel before excel/ci/manual split.
            if "achievement" in cols and "achievement_excel" in cols:
                sets.append("achievement = 0")
            elif "achievement_amount" in cols:
                # attribute_type schema: only zero rows tagged as excel upload
                pass
            if sets:
                where = f"financial_year_id = ?{ws_clause}"
                if "attribute_type" in cols:
                    where += " AND attribute_type = 'distributor'"
                conn.execute(
                    f"UPDATE target_achievement_breakup SET {', '.join(sets)} WHERE {where}",
                    tuple(params),
                )
            if "achievement_amount" in cols and "source" in cols:
                zero_params: list[Any] = [financial_year_id]
                zero_sql = (
                    "UPDATE target_achievement_breakup SET achievement_amount = 0 "
                    "WHERE financial_year_id = ? AND LOWER(COALESCE(source, '')) IN "
                    "('excel_upload', 'upload', 'excel')"
                )
                if "workspace_id" in cols:
                    zero_sql += " AND workspace_id = ?"
                    zero_params.append(workspace_id)
                if "attribute_type" in cols:
                    zero_sql += " AND attribute_type = 'distributor'"
                conn.execute(zero_sql, tuple(zero_params))
            cat_params: list[Any] = [financial_year_id]
            cat_sql = "DELETE FROM target_achievement_category_breakup WHERE financial_year_id = ?"
            if self._table_has_column("target_achievement_category_breakup", "workspace_id"):
                cat_sql += " AND workspace_id = ?"
                cat_params.append(workspace_id)
            try:
                conn.execute(cat_sql, tuple(cat_params))
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "DELETE FROM target_achievement_uploads WHERE financial_year_id = ?",
                    (financial_year_id,),
                )
            except sqlite3.OperationalError:
                pass
            self._prune_empty_breakup_rows(conn, financial_year_id, workspace_id, cols)
            conn.commit()
        breakup = self.list_target_distributor_breakup(workspace_id, financial_year_id)
        manual_total = sum(float(r.get("achievement_manual") or 0) for r in breakup)
        ci_total = sum(float(r.get("achievement_ci") or 0) for r in breakup)
        total = self.sync_financial_year_achievement_from_breakup(workspace_id, financial_year_id)
        return {
            "achievement_lakhs": total,
            "manual_lakhs": manual_total,
            "ci_lakhs": ci_total,
            "excel_lakhs": 0.0,
        }

    def clear_fy_achievement(self, workspace_id: str, financial_year_id: int) -> dict[str, Any]:
        """Remove all achievement data for a fiscal year; keeps targets."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache(
            "target_achievement_breakup", "target_achievement_category_breakup"
        )
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            params: list[Any] = [financial_year_id]
            ws_clause = ""
            if "workspace_id" in cols:
                ws_clause = " AND workspace_id = ?"
                params.append(workspace_id)
            sets: list[str] = []
            if "achievement_amount" in cols:
                sets.append("achievement_amount = 0")
            if "achievement_excel" in cols:
                sets.extend(["achievement_excel = 0", "achievement_ci = 0", "achievement_manual = 0"])
            if "achievement" in cols:
                sets.append("achievement = 0")
            if sets:
                where = f"financial_year_id = ?{ws_clause}"
                if "attribute_type" in cols:
                    where += " AND attribute_type = 'distributor'"
                conn.execute(
                    f"UPDATE target_achievement_breakup SET {', '.join(sets)} WHERE {where}",
                    tuple(params),
                )
            cat_params: list[Any] = [financial_year_id]
            cat_sql = "DELETE FROM target_achievement_category_breakup WHERE financial_year_id = ?"
            if self._table_has_column("target_achievement_category_breakup", "workspace_id"):
                cat_sql += " AND workspace_id = ?"
                cat_params.append(workspace_id)
            conn.execute(cat_sql, tuple(cat_params))
            conn.execute(
                "DELETE FROM target_achievement_uploads WHERE financial_year_id = ?",
                (financial_year_id,),
            )
            now = datetime.now(timezone.utc).isoformat()
            year_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            if "achievement_manual_fy" in year_cols:
                conn.execute(
                    "UPDATE target_achievement_years SET achievement_manual_fy = 0, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (now, financial_year_id, workspace_id),
                )
            self._prune_empty_breakup_rows(conn, financial_year_id, workspace_id, cols)
            conn.commit()
        total = self.sync_financial_year_achievement_from_breakup(workspace_id, financial_year_id)
        return {"achievement_lakhs": total}

    def clear_fy_targets(self, workspace_id: str, financial_year_id: int) -> dict[str, Any]:
        """Remove all target data for a fiscal year; keeps achievement."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache("target_achievement_breakup")
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            params: list[Any] = [financial_year_id]
            ws_clause = ""
            if "workspace_id" in cols:
                ws_clause = " AND workspace_id = ?"
                params.append(workspace_id)
            sets: list[str] = []
            if "target_lakhs" in cols:
                sets.append("target_lakhs = 0")
            if "target_amount" in cols:
                sets.append("target_amount = 0")
            if sets:
                where = f"financial_year_id = ?{ws_clause}"
                if "attribute_type" in cols:
                    where += " AND attribute_type = 'distributor'"
                conn.execute(
                    f"UPDATE target_achievement_breakup SET {', '.join(sets)} WHERE {where}",
                    tuple(params),
                )
            now = datetime.now(timezone.utc).isoformat()
            year_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            if "target_amount" in year_cols:
                conn.execute(
                    "UPDATE target_achievement_years SET target_amount = 0, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (now, financial_year_id, workspace_id),
                )
            elif "target" in year_cols:
                conn.execute(
                    "UPDATE target_achievement_years SET target = 0, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (now, financial_year_id, workspace_id),
                )
            self._prune_empty_breakup_rows(conn, financial_year_id, workspace_id, cols)
            conn.commit()
        return {"target_lakhs": 0.0}

    def _prune_empty_breakup_rows(
        self,
        conn: sqlite3.Connection,
        financial_year_id: int,
        workspace_id: str,
        cols: set[str],
    ) -> None:
        zero_checks: list[str] = []
        for column in (
            "target_lakhs",
            "target_amount",
            "achievement_excel",
            "achievement_ci",
            "achievement_manual",
            "achievement",
            "achievement_amount",
        ):
            if column in cols:
                zero_checks.append(f"COALESCE({column}, 0) = 0")
        if not zero_checks:
            return
        params: list[Any] = [financial_year_id]
        sql = (
            f"DELETE FROM target_achievement_breakup WHERE financial_year_id = ? "
            f"AND {' AND '.join(zero_checks)}"
        )
        if "attribute_type" in cols:
            sql += " AND attribute_type = 'distributor'"
        if "workspace_id" in cols:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        conn.execute(sql, tuple(params))

    def set_target_distributor_target_lakhs(
        self,
        *,
        workspace_id: str,
        financial_year_id: int,
        distributor_name: str,
        target_lakhs: float,
        nick: str | None = None,
    ) -> None:
        """Set manual distributor target (lakhs), including explicit 0 to clear; keeps achievement."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache("target_achievement_breakup")
        resolved = self.resolve_ta_distributor_reference(
            distributor_name, workspace_id, nick
        )
        display_name = resolved["distributor_name"]
        target_amount = float(target_lakhs or 0)
        if target_amount < 0:
            raise ValueError("target must be >= 0")

        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "distributor_name" in cols:
                self._consolidate_breakup_rows_for_resolved(
                    conn, workspace_id, financial_year_id, resolved, cols
                )

            updated = 0
            if "target_lakhs" in cols and "distributor_name" in cols:
                params: list[Any] = [target_amount, financial_year_id, display_name]
                sql = (
                    "UPDATE target_achievement_breakup SET target_lakhs = ? "
                    "WHERE financial_year_id = ? AND distributor_name = ?"
                )
                if "workspace_id" in cols:
                    sql += " AND workspace_id = ?"
                    params.append(workspace_id)
                cur = conn.execute(sql, tuple(params))
                updated = cur.rowcount
                # Also match by distributor_id when name spelling differs.
                if updated == 0 and resolved.get("distributor_id") and "distributor_id" in cols:
                    params2: list[Any] = [
                        target_amount,
                        display_name,
                        financial_year_id,
                        resolved["distributor_id"],
                    ]
                    sql2 = (
                        "UPDATE target_achievement_breakup "
                        "SET target_lakhs = ?, distributor_name = ? "
                        "WHERE financial_year_id = ? AND distributor_id = ?"
                    )
                    if "workspace_id" in cols:
                        sql2 += " AND workspace_id = ?"
                        params2.append(workspace_id)
                    cur = conn.execute(sql2, tuple(params2))
                    updated = cur.rowcount

            if "target_amount" in cols and "attribute_type" in cols and "attribute_name" in cols:
                params_a: list[Any] = [target_amount, financial_year_id, display_name]
                sql_a = (
                    "UPDATE target_achievement_breakup SET target_amount = ? "
                    "WHERE financial_year_id = ? AND attribute_type = 'distributor' "
                    "AND attribute_name = ?"
                )
                if "workspace_id" in cols:
                    sql_a += " AND workspace_id = ?"
                    params_a.append(workspace_id)
                cur = conn.execute(sql_a, tuple(params_a))
                updated = max(updated, cur.rowcount)

            if updated == 0:
                # No row yet — create Ach=0 shell so mid-year list can show Target-only / cleared.
                conn.commit()
            else:
                # Keep identity fields in sync when we matched an existing row.
                if "distributor_name" in cols:
                    self._consolidate_breakup_rows_for_resolved(
                        conn, workspace_id, financial_year_id, resolved, cols
                    )
                conn.commit()

        if updated == 0:
            self.upsert_target_distributor_breakup(
                workspace_id=workspace_id,
                financial_year_id=financial_year_id,
                distributor_name=distributor_name,
                achievement_lakhs=0.0,
                target_lakhs=target_amount,
                nick=nick,
                source="manual",
            )
        self.sync_financial_year_target_from_breakup(workspace_id, financial_year_id)

    def sync_financial_year_target_from_breakup(
        self, workspace_id: str, financial_year_id: int
    ) -> float:
        """
        Roll up distributor (+ Others) targets into FY year target (lakhs).

        If target_source is 'manual' or 'both', the year-level target is left alone
        (user set it explicitly on the FY card). Still returns the distributor sum
        so callers can show both numbers.
        """
        self.ensure_target_achievement_tables()
        breakup = self.list_target_distributor_breakup(workspace_id, financial_year_id)
        total = round(sum(float(r.get("target_lakhs") or 0) for r in breakup), 6)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            year_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (financial_year_id, workspace_id),
            ).fetchone()
            source = ""
            if row and "target_source" in year_cols:
                source = (dict(row).get("target_source") or "").strip().lower()
            # Manual / both: keep FY card target; only distributor mode (or blank) rolls up.
            if source in ("manual", "both"):
                return total
            sets: list[str] = []
            params: list[Any] = []
            if "target_amount" in year_cols:
                sets.append("target_amount = ?")
                params.append(total)
            if "target" in year_cols:
                sets.append("target = ?")
                params.append(total)
            if "target_source" in year_cols and total > 0:
                sets.append("target_source = ?")
                params.append("distributors")
            if "updated_at" in year_cols:
                sets.append("updated_at = ?")
                params.append(now)
            if sets:
                params.extend([financial_year_id, workspace_id])
                conn.execute(
                    f"UPDATE target_achievement_years SET {', '.join(sets)} "
                    "WHERE id = ? AND workspace_id = ?",
                    tuple(params),
                )
                conn.commit()
        return total

    def set_fy_manual_target(
        self,
        workspace_id: str,
        financial_year_id: int,
        target_lakhs: float,
        *,
        confirm_both: bool = False,
    ) -> dict[str, Any]:
        """
        Set FY-level manual target (lakhs).

        - No distributor targets → target_source = manual
        - Distributor targets exist + confirm_both → target_source = both (keeps both)
        - Distributor targets exist without confirm → needs_confirmation (no write)
        """
        self.ensure_target_achievement_tables()
        amount = float(target_lakhs or 0)
        if amount < 0:
            raise ValueError("target must be >= 0")
        breakup = self.list_target_distributor_breakup(workspace_id, financial_year_id)
        dist_sum = round(sum(float(r.get("target_lakhs") or 0) for r in breakup), 6)
        if dist_sum > 0.5 and amount > 0.5 and not confirm_both:
            return {
                "needs_confirmation": True,
                "distributor_target_lakhs": dist_sum,
                "manual_target_lakhs": amount,
            }
        if amount <= 0.5 and dist_sum > 0.5:
            source = "distributors"
            # Clearing manual → fall back to distributor rollup as FY target.
            write_amount = dist_sum
        elif amount > 0.5 and dist_sum > 0.5:
            source = "both"
            write_amount = amount
        elif amount > 0.5:
            source = "manual"
            write_amount = amount
        else:
            source = ""
            write_amount = 0.0

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            year_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            sets: list[str] = []
            params: list[Any] = []
            if "target_amount" in year_cols:
                sets.append("target_amount = ?")
                params.append(write_amount)
            if "target" in year_cols:
                sets.append("target = ?")
                params.append(write_amount)
            if "target_source" in year_cols:
                sets.append("target_source = ?")
                params.append(source or None)
            if "updated_at" in year_cols:
                sets.append("updated_at = ?")
                params.append(now)
            if not sets:
                raise RuntimeError("No target column on target_achievement_years")
            params.extend([financial_year_id, workspace_id])
            cur = conn.execute(
                f"UPDATE target_achievement_years SET {', '.join(sets)} "
                "WHERE id = ? AND workspace_id = ?",
                tuple(params),
            )
            if cur.rowcount == 0:
                raise ValueError("Year not found")
            conn.commit()
        return {
            "needs_confirmation": False,
            "target_lakhs": write_amount,
            "distributor_target_lakhs": dist_sum,
            "target_source": source or None,
        }

    def fy_target_meta(
        self, workspace_id: str, financial_year_id: int
    ) -> dict[str, Any]:
        """Year target + distributor sum + source for overview / UI."""
        self.ensure_target_achievement_tables()
        breakup = self.list_target_distributor_breakup(workspace_id, financial_year_id)
        dist_sum = round(sum(float(r.get("target_lakhs") or 0) for r in breakup), 6)
        target = 0.0
        source = None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (financial_year_id, workspace_id),
            ).fetchone()
            if row:
                data = dict(row)
                target = float(data.get("target_amount") or data.get("target") or 0)
                source = (data.get("target_source") or "").strip() or None
        return {
            "target_lakhs": target,
            "distributor_target_lakhs": dist_sum,
            "target_source": source,
        }

    def sync_financial_year_achievement_from_breakup(
        self, workspace_id: str, financial_year_id: int
    ) -> float:
        """Roll up distributor breakup into FY achievement (lakhs). Returns total lakhs."""
        self.ensure_target_achievement_tables()
        self._invalidate_table_columns_cache("target_achievement_breakup")
        total = 0.0
        with sqlite3.connect(self.db_path) as conn:
            cols = self._breakup_table_columns(conn)
            has_split = (
                "achievement_excel" in cols
                and "achievement_ci" in cols
                and "achievement_manual" in cols
            )
            if has_split:
                query = (
                    "SELECT SUM("
                    "COALESCE(achievement_excel, 0) + COALESCE(achievement_ci, 0) "
                    "+ COALESCE(achievement_manual, 0)"
                    ") FROM target_achievement_breakup WHERE financial_year_id = ?"
                )
                params: list[Any] = [financial_year_id]
                if "attribute_type" in cols:
                    query += " AND attribute_type = 'distributor'"
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                row = conn.execute(query, tuple(params)).fetchone()
                total = float(row[0] or 0) if row else 0.0
            elif "attribute_type" in cols and "achievement_amount" in cols:
                query = (
                    "SELECT SUM(achievement_amount) FROM target_achievement_breakup "
                    "WHERE financial_year_id = ? AND attribute_type = 'distributor'"
                )
                params = [financial_year_id]
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                row = conn.execute(query, tuple(params)).fetchone()
                total = float(row[0] or 0) if row else 0.0
            elif "achievement_excel" in cols:
                query = (
                    "SELECT SUM("
                    "COALESCE(achievement_excel, 0) + COALESCE(achievement_ci, 0) + COALESCE(achievement_manual, 0)"
                    ") FROM target_achievement_breakup WHERE financial_year_id = ?"
                )
                params = [financial_year_id]
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                row = conn.execute(query, tuple(params)).fetchone()
                total = float(row[0] or 0) if row else 0.0
            elif "achievement" in cols:
                query = "SELECT SUM(achievement) FROM target_achievement_breakup WHERE financial_year_id = ?"
                params = [financial_year_id]
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                row = conn.execute(query, tuple(params)).fetchone()
                total = float(row[0] or 0) if row else 0.0

            year_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(target_achievement_years)").fetchall()
            }
            now = datetime.now(timezone.utc).isoformat()
            if "achievement_amount" in year_cols:
                conn.execute(
                    "UPDATE target_achievement_years SET achievement_amount = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (total, now, financial_year_id, workspace_id),
                )
            elif "achievement" in year_cols:
                conn.execute(
                    "UPDATE target_achievement_years SET achievement = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (total, now, financial_year_id, workspace_id),
                )
            conn.commit()
        return total

    def _ensure_ta_distributors_linked(self, workspace_id: str) -> None:
        """One-time relink when legacy rows lack source_distributor_name."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "source_distributor_name" not in cols:
                return
            params: list[Any] = []
            if "distributor_name" in cols:
                query = (
                    "SELECT 1 FROM target_achievement_breakup "
                    "WHERE source_distributor_name IS NULL"
                )
            elif "attribute_type" in cols:
                query = (
                    "SELECT 1 FROM target_achievement_breakup "
                    "WHERE attribute_type = 'distributor' AND source_distributor_name IS NULL"
                )
            else:
                return
            if "workspace_id" in cols:
                query += " AND workspace_id = ?"
                params.append(workspace_id)
            query += " LIMIT 1"
            pending = conn.execute(query, tuple(params)).fetchone()
        if pending:
            self.relink_target_achievement_distributors(workspace_id)

    def _ta_breakup_display_fields(
        self,
        workspace_id: str,
        distributor_name: str,
        nick: str | None,
        distributor_id: int | None = None,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        if distributor_id:
            master = self.get_master_distributor(distributor_id, workspace_id=workspace_id)
            if master:
                canonical = self._master_distributor_label(master)
                master_nick = (master.get("firm_nick_name") or "").strip() or None
                display_nick = master_nick or nick
                display_label = (
                    f"{display_nick} | {canonical}" if display_nick else canonical
                )
                return {
                    "matched": True,
                    "distributor_id": distributor_id,
                    "distributor_name": canonical,
                    "source_distributor_name": source_name or distributor_name,
                    "nick": display_nick,
                    "display_label": display_label,
                    "master_firm_name": master.get("firm_name"),
                    "master_firm_nick_name": master.get("firm_nick_name"),
                }
        resolved = self.resolve_ta_distributor_reference(
            distributor_name, workspace_id, nick
        )
        return {
            "matched": bool(resolved.get("matched")),
            "distributor_id": resolved.get("distributor_id"),
            "distributor_name": resolved.get("distributor_name") or distributor_name,
            "source_distributor_name": resolved.get("source_distributor_name")
            or distributor_name,
            "nick": resolved.get("nick") or nick,
            "display_label": resolved.get("display_label")
            or (f"{nick} | {distributor_name}" if nick else distributor_name),
            "master_firm_name": (
                resolved.get("master", {}).get("firm_name") if resolved.get("master") else None
            ),
            "master_firm_nick_name": (
                resolved.get("master", {}).get("firm_nick_name")
                if resolved.get("master")
                else None
            ),
        }

    def list_target_distributor_breakup(
        self, workspace_id: str, financial_year_id: int
    ) -> list[dict[str, Any]]:
        self.ensure_target_achievement_tables()
        self._ensure_ta_distributors_linked(workspace_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            if "attribute_type" in cols and "attribute_name" in cols:
                extra_cols = []
                if "nick" in cols:
                    extra_cols.append("nick")
                if "distributor_id" in cols:
                    extra_cols.append("distributor_id")
                if "source_distributor_name" in cols:
                    extra_cols.append("source_distributor_name")
                for split_col in (
                    "achievement_excel",
                    "achievement_ci",
                    "achievement_manual",
                ):
                    if split_col in cols:
                        extra_cols.append(split_col)
                extra_sel = (", " + ", ".join(extra_cols)) if extra_cols else ""
                query = (
                    f"SELECT attribute_name{extra_sel}, target_amount, achievement_amount, "
                    "achievement_percent, source "
                    "FROM target_achievement_breakup "
                    "WHERE financial_year_id = ? AND attribute_type = 'distributor'"
                )
                params: list[Any] = [financial_year_id]
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                query += " ORDER BY attribute_name ASC"
                rows = conn.execute(query, tuple(params)).fetchall()
                result = []
                for r in rows:
                    amount = float(r["achievement_amount"] or 0)
                    src = (r["source"] or "").lower()
                    excel = float(r["achievement_excel"] or 0) if "achievement_excel" in cols else 0.0
                    ci = float(r["achievement_ci"] or 0) if "achievement_ci" in cols else 0.0
                    manual = float(r["achievement_manual"] or 0) if "achievement_manual" in cols else 0.0
                    excel, ci, manual, total = self._split_achievement_values(
                        excel=excel,
                        ci=ci,
                        manual=manual,
                        amount=amount,
                        source=src,
                    )
                    identity = self._ta_breakup_display_fields(
                        workspace_id,
                        r["attribute_name"],
                        r["nick"] if "nick" in cols else None,
                        r["distributor_id"] if "distributor_id" in cols else None,
                        r["source_distributor_name"]
                        if "source_distributor_name" in cols
                        else None,
                    )
                    result.append(
                        {
                            **identity,
                            "target_lakhs": float(r["target_amount"] or 0),
                            "achievement_excel": excel,
                            "achievement_ci": ci,
                            "achievement_manual": manual,
                            "achievement_lakhs": total,
                            "percentage": float(r["achievement_percent"] or 0),
                            "source": src or self._derived_breakup_source(excel, ci, manual),
                        }
                    )
                return result
            if "distributor_name" in cols:
                extra_cols = []
                if "nick" in cols:
                    extra_cols.append("nick")
                if "distributor_id" in cols:
                    extra_cols.append("distributor_id")
                if "source_distributor_name" in cols:
                    extra_cols.append("source_distributor_name")
                extra_sel = (", " + ", ".join(extra_cols)) if extra_cols else ""
                query = f"""
                    SELECT distributor_name{extra_sel},
                           COALESCE(target_lakhs, 0) AS target_lakhs,
                           COALESCE(achievement_excel, achievement, 0) AS achievement_excel,
                           COALESCE(achievement_ci, 0) AS achievement_ci,
                           COALESCE(achievement_manual, 0) AS achievement_manual
                    FROM target_achievement_breakup
                    WHERE financial_year_id = ?
                """
                params = [financial_year_id]
                if "workspace_id" in cols:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)
                query += " ORDER BY distributor_name ASC"
                rows = conn.execute(query, tuple(params)).fetchall()
                result = []
                for r in rows:
                    if "nick" in cols:
                        dist = r["distributor_name"]
                        nick = r["nick"]
                        target = float(r["target_lakhs"] or 0)
                        excel = float(r["achievement_excel"] or 0)
                        ci = float(r["achievement_ci"] or 0)
                        manual = float(r["achievement_manual"] or 0)
                        dist_id = r["distributor_id"] if "distributor_id" in cols else None
                        source_name = (
                            r["source_distributor_name"]
                            if "source_distributor_name" in cols
                            else None
                        )
                    else:
                        dist = r[0]
                        nick = None
                        target = float(r[1] or 0)
                        excel = float(r[2] or 0)
                        ci = float(r[3] or 0)
                        manual = float(r[4] or 0)
                        dist_id = None
                        source_name = None
                    total = excel + ci + manual
                    pct = round((total / target) * 100, 2) if target > 0 else 0.0
                    identity = self._ta_breakup_display_fields(
                        workspace_id, dist, nick, dist_id, source_name
                    )
                    result.append(
                        {
                            **identity,
                            "target_lakhs": target,
                            "achievement_excel": excel,
                            "achievement_ci": ci,
                            "achievement_manual": manual,
                            "achievement_lakhs": total,
                            "percentage": pct,
                            "source": "mixed",
                        }
                    )
                return result
        return []

    def list_others_lines(self, workspace_id: str, financial_year_id: int) -> list[dict]:
        """Named achievement lines that roll into the Others distributor bucket."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, line_name, amount_lakhs, updated_at
                FROM target_others_lines
                WHERE workspace_id = ? AND financial_year_id = ?
                ORDER BY LOWER(line_name)
                """,
                (workspace_id, financial_year_id),
            ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "line_name": r["line_name"],
                "amount_lakhs": float(r["amount_lakhs"] or 0),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def replace_others_lines(
        self,
        *,
        workspace_id: str,
        financial_year_id: int,
        lines: list[dict],
        target_lakhs: float | None = None,
        others_name: str = "Others",
    ) -> dict:
        """
        Replace Others achievement lines and sync Others.achievement_manual = sum(lines).
        Optional target_lakhs updates the Others target row.
        """
        self.ensure_target_achievement_tables()
        cleaned: list[tuple[str, float]] = []
        seen: set[str] = set()
        for raw in lines or []:
            name = str(raw.get("line_name") or raw.get("distributor_name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key == others_name.lower() or key in seen:
                continue
            seen.add(key)
            if raw.get("amount_rupees") is not None:
                amount_lakhs = float(raw.get("amount_rupees") or 0) / 100_000.0
            else:
                amount_lakhs = float(raw.get("amount_lakhs") or raw.get("amount") or 0)
            if amount_lakhs < 0:
                amount_lakhs = 0.0
            cleaned.append((name, round(amount_lakhs, 6)))

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM target_others_lines WHERE workspace_id = ? AND financial_year_id = ?",
                (workspace_id, financial_year_id),
            )
            for name, amount_lakhs in cleaned:
                conn.execute(
                    """
                    INSERT INTO target_others_lines (
                        workspace_id, financial_year_id, line_name, amount_lakhs, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace_id, financial_year_id, name, amount_lakhs, now, now),
                )
            conn.commit()

        total_ach = round(sum(a for _, a in cleaned), 6)
        self.upsert_target_distributor_breakup(
            workspace_id=workspace_id,
            financial_year_id=financial_year_id,
            distributor_name=others_name,
            achievement_lakhs=total_ach,
            target_lakhs=target_lakhs,
            nick=None,
            source="manual",
        )
        fy_target = self.sync_financial_year_target_from_breakup(workspace_id, financial_year_id)
        fy_ach = self.sync_financial_year_achievement_from_breakup(workspace_id, financial_year_id)
        return {
            "lines": [
                {"line_name": n, "amount_lakhs": a, "amount_rupees": round(a * 100_000.0, 2)}
                for n, a in cleaned
            ],
            "total_achievement_lakhs": total_ach,
            "total_achievement_rupees": round(total_ach * 100_000.0, 2),
            "fy_target_lakhs": fy_target,
            "fy_achievement_lakhs": fy_ach,
            "others_name": others_name,
        }

    def set_fy_manual_achievement(
        self, workspace_id: str, financial_year_id: int, achievement_lakhs: float
    ) -> None:
        """Store FY-level manual achievement override (lakhs)."""
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            now = datetime.now(timezone.utc).isoformat()
            if self._table_has_column("target_achievement_years", "achievement_manual_fy"):
                conn.execute(
                    "UPDATE target_achievement_years SET achievement_manual_fy = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (float(achievement_lakhs), now, financial_year_id, workspace_id),
                )
            elif self._table_has_column("target_achievement_years", "achievement_amount"):
                conn.execute(
                    "UPDATE target_achievement_years SET achievement_amount = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (float(achievement_lakhs), now, financial_year_id, workspace_id),
                )
            conn.commit()

    def _zero_ci_achievement_for_fy(
        self, workspace_id: str, financial_year_id: int
    ) -> None:
        """Clear CI channel on breakup so deleted invoices do not leave stale totals."""
        with sqlite3.connect(self.db_path) as conn:
            self._migrate_legacy_breakup_schema(conn)
            cols = self._breakup_table_columns(conn)
            params: list[Any] = [financial_year_id]
            ws_clause = ""
            if "workspace_id" in cols:
                ws_clause = " AND workspace_id = ?"
                params.append(workspace_id)
            type_clause = ""
            if "attribute_type" in cols:
                type_clause = " AND attribute_type = 'distributor'"
            if "achievement_ci" in cols:
                conn.execute(
                    f"UPDATE target_achievement_breakup SET achievement_ci = 0 "
                    f"WHERE financial_year_id = ?{ws_clause}{type_clause}",
                    tuple(params),
                )
            has_split = (
                "achievement_excel" in cols
                and "achievement_manual" in cols
                and "achievement_amount" in cols
            )
            if has_split:
                # Keep typed manual / Excel; only CI was zeroed.
                conn.execute(
                    f"UPDATE target_achievement_breakup SET achievement_amount = "
                    f"COALESCE(achievement_excel, 0) + COALESCE(achievement_ci, 0) "
                    f"+ COALESCE(achievement_manual, 0) "
                    f"WHERE financial_year_id = ?{ws_clause}{type_clause}",
                    tuple(params),
                )
            elif "achievement_amount" in cols and "source" in cols:
                conn.execute(
                    f"UPDATE target_achievement_breakup SET achievement_amount = 0 "
                    f"WHERE financial_year_id = ?{ws_clause}{type_clause} "
                    f"AND LOWER(COALESCE(source, '')) = 'ci'",
                    tuple(params),
                )
            conn.commit()

    def sync_ci_achievement_for_fy(
        self, workspace_id: str, financial_year_id: int, fy_label: str
    ) -> int:
        """Pull CI totals from order fulfillment into distributor breakup (lakhs).

        Always rewrites the CI channel from live invoices: first zeroes
        stored CI achievement for the FY, then re-applies current totals.
        Deleting every CI therefore correctly drives achievement_ci to 0.

        Manual (typed) and Excel channels are left untouched.
        """
        from app.fiscal_year import fiscal_year_date_bounds

        self.ensure_target_achievement_tables()
        start, end = fiscal_year_date_bounds(fy_label)
        if not start or not end:
            return 0

        # Drop stale CI figures left behind after invoice deletes.
        self._zero_ci_achievement_for_fy(workspace_id, financial_year_id)

        updated = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._table_has_column("order_lifecycle_tracking", "commercial_invoice_file_reference"):
                return 0
            has_parsed = self._table_has_column(
                "order_lifecycle_tracking", "commercial_invoice_parsed"
            )
            has_drive = self._table_has_column(
                "order_lifecycle_tracking", "commercial_invoice_drive_file_id"
            )
            ci_present = (
                "( "
                "(olt.commercial_invoice_file_reference IS NOT NULL "
                " AND TRIM(olt.commercial_invoice_file_reference) != '') "
            )
            if has_drive:
                ci_present += (
                    " OR (olt.commercial_invoice_drive_file_id IS NOT NULL "
                    " AND TRIM(olt.commercial_invoice_drive_file_id) != '') "
                )
            if has_parsed:
                ci_present += (
                    " OR (olt.commercial_invoice_parsed IS NOT NULL "
                    " AND TRIM(olt.commercial_invoice_parsed) != '') "
                )
            ci_present += ")"
            parsed_sel = (
                ", olt.commercial_invoice_parsed"
                if has_parsed
                else ", NULL AS commercial_invoice_parsed"
            )
            group_extra = (
                ", olt.commercial_invoice_parsed" if has_parsed else ""
            )
            query = f"""
                SELECT olt.tracking_id,
                       COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name,
                       COALESCE(SUM(ofi.ci_value), 0) AS ci_items_total
                       {parsed_sel}
                FROM order_lifecycle_tracking olt
                JOIN master_distributors md ON md.id = olt.distributor_id
                LEFT JOIN order_fulfillment_items ofi ON ofi.order_lifecycle_id = olt.tracking_id
                WHERE olt.workspace_id = ?
                  AND {ci_present}
                  AND olt.commercial_invoice_date IS NOT NULL
                  AND olt.commercial_invoice_date >= ?
                  AND olt.commercial_invoice_date <= ?
                GROUP BY olt.tracking_id, COALESCE(md.firm_name, md.name, 'Unknown')
                       {group_extra}
            """
            rows = conn.execute(query, (workspace_id, start, end)).fetchall()

        by_distributor: dict[str, float] = {}
        for row in rows:
            name = str(row["distributor_name"] or "Unknown")
            items_total = float(row["ci_items_total"] or 0)
            amount = items_total
            if amount <= 0:
                parsed_raw = row["commercial_invoice_parsed"]
                parsed: dict[str, Any] | None = None
                if isinstance(parsed_raw, str) and parsed_raw.strip():
                    try:
                        loaded = json.loads(parsed_raw)
                        if isinstance(loaded, dict):
                            parsed = loaded
                    except (TypeError, ValueError, json.JSONDecodeError):
                        parsed = None
                elif isinstance(parsed_raw, dict):
                    parsed = parsed_raw
                if isinstance(parsed, dict):
                    header = parsed.get("header") if isinstance(parsed.get("header"), dict) else {}
                    totals = parsed.get("totals") if isinstance(parsed.get("totals"), dict) else {}
                    for key in ("invoice_total", "line_total", "taxable_amount"):
                        amt = self._parse_money(header.get(key))
                        if amt is None:
                            amt = self._parse_money(totals.get(key))
                        if amt is not None and amt > 0:
                            amount = float(amt)
                            break
            if amount <= 0:
                continue
            by_distributor[name] = by_distributor.get(name, 0.0) + amount

        for distributor_name, ci_total in by_distributor.items():
            lakhs = float(ci_total or 0) / 100000.0
            if lakhs <= 0:
                continue
            self.upsert_target_distributor_breakup(
                workspace_id=workspace_id,
                financial_year_id=financial_year_id,
                distributor_name=distributor_name,
                achievement_lakhs=lakhs,
                source="ci",
            )
            updated += 1
        return updated

    def ensure_distributor_category_payments_table(self) -> None:
        """Deposits tracked per (user, distributor, season, category) —
        deliberately NOT tied to a single SO/tracking_id like the older,
        now-backlogged PaymentCollection feature. Scoped by user_id, same
        as filled_orders/article_master, since the SO total each deposit
        is measured against is itself that user's own uploaded order data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_category_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    season TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    payment_date TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dcp_lookup "
                "ON distributor_category_payments(user_id, distributor_id, season, category)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_cd_rates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    season TEXT NOT NULL,
                    cd_percent REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(user_id, distributor_id, season)
                )
                """
            )

    def get_distributor_cd_rates(self, user_id: int) -> dict[tuple[int, str], float]:
        """Return {(distributor_id, season): cd_percent} for a user."""
        self.ensure_distributor_category_payments_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT distributor_id, season, cd_percent FROM distributor_cd_rates WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {(r["distributor_id"], r["season"]): float(r["cd_percent"]) for r in rows}

    def set_distributor_cd_rate(
        self, user_id: int, distributor_id: int, season: str, cd_percent: float
    ) -> dict[str, Any]:
        """Upsert CD% for a distributor+season. Returns the saved record."""
        self.ensure_distributor_category_payments_table()
        cd_percent = max(0.0, min(100.0, cd_percent))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO distributor_cd_rates (user_id, distributor_id, season, cd_percent, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id, distributor_id, season)
                DO UPDATE SET cd_percent = excluded.cd_percent, updated_at = excluded.updated_at
                """,
                (user_id, distributor_id, season, cd_percent),
            )
            conn.commit()
        return {
            "distributor_id": distributor_id,
            "season": season,
            "cd_percent": cd_percent,
        }

    def list_distributor_category_payment_status(self, user_id: int | None) -> list[dict[str, Any]]:
        """Distributor -> season -> category tree: each category carries its
        SO total, the deposits recorded against it, and running paid/
        outstanding totals.

        SO total is the matched Sales Order **bill amount incl. GST**
        (sum of so_breakdown.total on fo_so_match_runs.rows_json) for SOs
        run through Order Desk's FO<->SO matching — deliberately NOT
        so_net_amount alone (ex-mill / pre-tax), and deliberately NOT
        filled_orders / FO ex-mill value, and deliberately NOT SOs
        uploaded but not yet matched (those don't have a reliable
        season/category/amount anywhere else in the app either, so nothing
        is invented for them here). Only (distributor, season, category)
        combinations that actually have a matched SO are included.

        Re-uploading the same SO Pack creates another fo_so_match_runs row;
        Order Desk's Sales Orders tab keeps only the latest run per FO
        (filled_order_id) / party+season+category. Payment totals must use
        the same dedupe — otherwise recovery/SO bill doubles on every upload.

        Note: category/season on a match run are copied from the Filled
        Order it was matched against, not derived from the SO itself — a
        run with match_count = 0 (no lines reconciled) can carry the wrong
        category if it was matched against the wrong FO. That's a data
        problem to fix by re-matching against the right FO in Order Desk,
        not a reason to hide the payment obligation, so it's still counted
        here under whatever category the run currently has."""
        if not user_id:
            return []
        self.ensure_distributor_category_payments_table()
        from app.services.fo_so_match_db import so_net_and_bill_from_match_rows

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            so_run_rows = conn.execute(
                """
                SELECT distributor_id, season, category, so_net_amount, rows_json
                FROM fo_so_match_runs
                WHERE user_id = ?
                  AND distributor_id IS NOT NULL
                  AND season IS NOT NULL AND TRIM(season) != ''
                  AND category IS NOT NULL AND TRIM(category) != ''
                  AND id IN (
                    SELECT MAX(id)
                    FROM fo_so_match_runs
                    WHERE user_id = ?
                      AND distributor_id IS NOT NULL
                      AND season IS NOT NULL AND TRIM(season) != ''
                      AND category IS NOT NULL AND TRIM(category) != ''
                    GROUP BY CASE
                      WHEN filled_order_id IS NOT NULL
                        THEN 'fo:' || CAST(filled_order_id AS TEXT)
                      ELSE 'party:' || CAST(distributor_id AS TEXT)
                           || '|' || COALESCE(season, '')
                           || '|' || COALESCE(category, '')
                    END
                  )
                """,
                (user_id, user_id),
            ).fetchall()
            # Track both net (pre-GST) and bill (incl. GST) per key
            so_net_by_key: dict[tuple[int, str, str], float] = {}
            so_bill_by_key: dict[tuple[int, str, str], float] = {}
            for combo in so_run_rows:
                dist_id = combo["distributor_id"]
                season = combo["season"]
                category = combo["category"]
                key = (dist_id, season, category)
                net, bill = so_net_and_bill_from_match_rows(
                    combo["rows_json"],
                    float(combo["so_net_amount"] or 0),
                )
                so_net_by_key[key] = so_net_by_key.get(key, 0.0) + net
                so_bill_by_key[key] = so_bill_by_key.get(key, 0.0) + bill
            deposit_rows = conn.execute(
                "SELECT id, distributor_id, season, category, amount, payment_date, note, created_at "
                "FROM distributor_category_payments WHERE user_id = ? "
                "ORDER BY payment_date, id",
                (user_id,),
            ).fetchall()
            dist_name_rows = conn.execute(
                "SELECT id, COALESCE(firm_name, name, 'Unknown') AS distributor_name "
                "FROM master_distributors"
            ).fetchall()
            dist_names = {r["id"]: r["distributor_name"] for r in dist_name_rows}

            deposits_by_key: dict[tuple, list[dict]] = {}
            for r in deposit_rows:
                key = (r["distributor_id"], r["season"], r["category"])
                deposits_by_key.setdefault(key, []).append(
                    {
                        "id": r["id"],
                        "amount": float(r["amount"] or 0),
                        "payment_date": r["payment_date"],
                        "note": r["note"],
                        "created_at": r["created_at"],
                    }
                )

            cd_rates = self.get_distributor_cd_rates(user_id)

            by_distributor: dict[int, dict[str, Any]] = {}
            for key in so_bill_by_key:
                dist_id, season, category = key
                so_total = float(so_bill_by_key.get(key, 0))
                so_net = float(so_net_by_key.get(key, 0))
                deposits = deposits_by_key.get(key, [])
                paid_total = sum(d["amount"] for d in deposits)
                cd_pct = cd_rates.get((dist_id, season), 0.0)
                # CD is on net (pre-GST), GST stays on full total
                cd_amount = so_net * (cd_pct / 100.0)
                bill_after_cd = so_total - cd_amount
                dist_entry = by_distributor.setdefault(
                    dist_id,
                    {
                        "distributor_id": dist_id,
                        "distributor_name": dist_names.get(dist_id, "Unknown"),
                        "seasons": {},
                    },
                )
                season_entry = dist_entry["seasons"].setdefault(
                    season, {"season": season, "cd_percent": cd_pct, "categories": []}
                )
                season_entry["categories"].append(
                    {
                        "category": category,
                        "so_total": so_total,
                        "so_net": so_net,
                        "cd_amount": round(cd_amount, 2),
                        "bill_after_cd": round(bill_after_cd, 2),
                        "paid_total": paid_total,
                        "outstanding": round(bill_after_cd - paid_total, 2),
                        "deposits": deposits,
                    }
                )

        result = []
        for dist_entry in by_distributor.values():
            dist_entry["seasons"] = sorted(
                dist_entry["seasons"].values(), key=lambda s: s["season"], reverse=True
            )
            for season_entry in dist_entry["seasons"]:
                season_entry["categories"].sort(key=lambda c: c["category"])
            result.append(dist_entry)
        result.sort(key=lambda d: (d["distributor_name"] or "").lower())
        return result

    def add_distributor_category_payment(
        self,
        user_id: int,
        distributor_id: int,
        season: str,
        category: str,
        amount: float,
        payment_date: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_distributor_category_payments_table()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO distributor_category_payments "
                "(user_id, distributor_id, season, category, amount, payment_date, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, distributor_id, season, category, amount, payment_date, note),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, distributor_id, season, category, amount, payment_date, note, created_at "
                "FROM distributor_category_payments WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return {
            "id": row[0],
            "distributor_id": row[1],
            "season": row[2],
            "category": row[3],
            "amount": float(row[4] or 0),
            "payment_date": row[5],
            "note": row[6],
            "created_at": row[7],
        }

    def delete_distributor_category_payment(self, user_id: int, deposit_id: int) -> bool:
        self.ensure_distributor_category_payments_table()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM distributor_category_payments WHERE id = ? AND user_id = ?",
                (deposit_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def ensure_distributor_secondary_sales_table(self) -> None:
        """BD Distributor Zone: monthly secondary sales per distributor (₹),
        scoped by user_id. Grouped into Indian FY (Apr–Mar) on the client."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributor_secondary_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    distributor_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(user_id, distributor_id, year, month)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dss_lookup "
                "ON distributor_secondary_sales(user_id, distributor_id)"
            )

    @staticmethod
    def _fy_label_for_month(year: int, month: int) -> str:
        start = year if month >= 4 else year - 1
        return f"{start}-{start + 1}"

    def list_distributor_secondary_sales(self, user_id: int | None) -> list[dict[str, Any]]:
        """Active distributors owned by this user + monthly secondary entries,
        grouped by FY under each distributor. Inactive / past parties are omitted.
        Brand-new users with no masters see an empty list.
        """
        if not user_id:
            return []
        self.ensure_distributor_secondary_sales_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            dist_rows = conn.execute(
                """
                SELECT id,
                       COALESCE(firm_name, name, 'Unknown') AS distributor_name,
                       IFNULL(status, 'active') AS status
                FROM master_distributors
                WHERE user_id = ?
                  AND LOWER(IFNULL(status, 'active')) != 'inactive'
                ORDER BY LOWER(COALESCE(firm_name, name, ''))
                """,
                (user_id,),
            ).fetchall()
            entry_rows = conn.execute(
                """
                SELECT id, distributor_id, year, month, amount, note, created_at, updated_at
                FROM distributor_secondary_sales
                WHERE user_id = ?
                ORDER BY year DESC, month DESC, id DESC
                """,
                (user_id,),
            ).fetchall()

        entries_by_dist: dict[int, list[dict[str, Any]]] = {}
        for r in entry_rows:
            dist_id = int(r["distributor_id"])
            year = int(r["year"])
            month = int(r["month"])
            entries_by_dist.setdefault(dist_id, []).append(
                {
                    "id": r["id"],
                    "year": year,
                    "month": month,
                    "amount": float(r["amount"] or 0),
                    "note": r["note"],
                    "fy_label": self._fy_label_for_month(year, month),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )

        result: list[dict[str, Any]] = []
        for d in dist_rows:
            dist_id = int(d["id"])
            months = entries_by_dist.get(dist_id, [])
            fy_map: dict[str, dict[str, Any]] = {}
            for m in months:
                fy = m["fy_label"]
                bucket = fy_map.setdefault(
                    fy, {"fy_label": fy, "total": 0.0, "months": []}
                )
                bucket["months"].append(m)
                bucket["total"] = round(bucket["total"] + float(m["amount"]), 2)
            fiscal_years = sorted(
                fy_map.values(),
                key=lambda x: x["fy_label"],
                reverse=True,
            )
            for fy in fiscal_years:
                fy["months"].sort(key=lambda x: (x["year"], x["month"]), reverse=True)
            total = round(sum(float(m["amount"]) for m in months), 2)
            status = str(d["status"] or "active").strip().lower() or "active"
            result.append(
                {
                    "distributor_id": dist_id,
                    "distributor_name": d["distributor_name"],
                    "status": status,
                    "is_active": status != "inactive",
                    "total_amount": total,
                    "fiscal_years": fiscal_years,
                    "months": months,
                }
            )
        # Prefer distributors that already have entries, then A–Z
        result.sort(
            key=lambda x: (
                0 if (x["total_amount"] or 0) > 0 else 1,
                (x["distributor_name"] or "").lower(),
            )
        )
        return result

    def upsert_distributor_secondary_sale(
        self,
        user_id: int,
        distributor_id: int,
        year: int,
        month: int,
        amount: float,
        note: str | None = None,
    ) -> dict[str, Any]:
        if month < 1 or month > 12:
            raise ValueError("month must be 1–12")
        if year < 2000 or year > 2100:
            raise ValueError("year out of range")
        if amount < 0:
            raise ValueError("amount must be ≥ 0")
        self.ensure_distributor_secondary_sales_table()
        with sqlite3.connect(self.db_path) as conn:
            party = conn.execute(
                """
                SELECT IFNULL(status, 'active') AS status
                FROM master_distributors
                WHERE id = ? AND user_id = ?
                """,
                (distributor_id, user_id),
            ).fetchone()
            if party is None:
                raise ValueError("Distributor not found")
            if str(party[0] or "active").strip().lower() == "inactive":
                raise ValueError(
                    "Cannot record secondary sales for an inactive distributor"
                )
            conn.execute(
                """
                INSERT INTO distributor_secondary_sales
                    (user_id, distributor_id, year, month, amount, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(user_id, distributor_id, year, month) DO UPDATE SET
                    amount = excluded.amount,
                    note = excluded.note,
                    updated_at = datetime('now')
                """,
                (user_id, distributor_id, year, month, amount, note),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT id, distributor_id, year, month, amount, note, created_at, updated_at
                FROM distributor_secondary_sales
                WHERE user_id = ? AND distributor_id = ? AND year = ? AND month = ?
                """,
                (user_id, distributor_id, year, month),
            ).fetchone()
        return {
            "id": row[0],
            "distributor_id": row[1],
            "year": row[2],
            "month": row[3],
            "amount": float(row[4] or 0),
            "note": row[5],
            "fy_label": self._fy_label_for_month(int(row[2]), int(row[3])),
            "created_at": row[6],
            "updated_at": row[7],
        }

    def delete_distributor_secondary_sale(self, user_id: int, entry_id: int) -> bool:
        self.ensure_distributor_secondary_sales_table()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM distributor_secondary_sales WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def sum_so_value_for_fy(self, user_id: int | None, fy_label: str) -> float:
        """Sum ex-mill value (in lakhs) of filled_order_items whose parent
        filled_order was created within the financial year's date range
        (Apr 1 - Mar 31, same bounds sync_ci_achievement_for_fy uses) — the
        same underlying data as the home screen's "Total value of SO"
        card, just date-scoped to one FY instead of by season code. Used
        as a stand-in achievement figure until real CI data exists for
        the year; see build_fy_achievement_summary."""
        from app.fiscal_year import fiscal_year_date_bounds

        if not user_id:
            return 0.0
        start, end = fiscal_year_date_bounds(fy_label)
        if not start or not end:
            return 0.0
        import filled_orders_db as fodb

        with sqlite3.connect(self.db_path) as conn:
            fodb.ensure_schema(conn)
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(foi.final_piece_qty * COALESCE(foi.ex_mill_price, 0)), 0) "
                    "FROM filled_order_items foi "
                    "JOIN filled_orders fo ON fo.id = foi.filled_order_id "
                    "WHERE fo.user_id = ? AND DATE(fo.created_at) BETWEEN ? AND ?",
                    (user_id, start, end),
                ).fetchone()
            except sqlite3.OperationalError:
                return 0.0
        return float(row[0] or 0.0) / 100000.0

    def build_fy_achievement_summary(
        self,
        workspace_id: str,
        financial_year_id: int,
        fy_label: str,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """Summarize target and achievement channels for one FY.

        Real achievement is the live Commercial Invoice (CI) total after
        sync. When every CI is deleted, CI sync zeroes the channel and
        active achievement becomes 0 — we do not fall back to FO/SO
        filled-order totals (that used to leave a stale "67 Lakh" after
        CI count went to 0).

        Excel / manual overrides still apply only when CI is zero and
        those channels have explicit values.
        """
        self.sync_ci_achievement_for_fy(workspace_id, financial_year_id, fy_label)
        breakup = self.list_target_distributor_breakup(workspace_id, financial_year_id)
        target_lakhs = 0.0
        manual_fy = 0.0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (financial_year_id, workspace_id),
            ).fetchone()
            if row:
                data = dict(row)
                target_lakhs = float(data.get("target_amount") or data.get("target") or 0)
                # Only the explicit FY-level manual override — never fall back to
                # achievement_amount / achievement (those are rollups and often stale Excel).
                manual_fy = float(data.get("achievement_manual_fy") or 0)
        excel_total = sum(float(r.get("achievement_excel") or 0) for r in breakup)
        ci_total = sum(float(r.get("achievement_ci") or 0) for r in breakup)
        manual_dist_total = sum(float(r.get("achievement_manual") or 0) for r in breakup)
        so_total = self.sum_so_value_for_fy(user_id, fy_label)
        # "SO value" channel = Order Desk SO total when present, else Excel SO upload.
        so_channel = so_total if so_total > 0 else excel_total
        manual_channel = float(manual_fy or 0) + float(manual_dist_total or 0)

        prefs = self.get_achievement_channel_prefs(workspace_id, user_id, financial_year_id)
        use_manual = bool(prefs.get("manual"))
        use_so = bool(prefs.get("so"))
        use_ci = bool(prefs.get("ci"))
        # Hard rule: never combine SO + CI.
        if use_so and use_ci:
            use_ci = False

        if use_manual or use_so or use_ci:
            active_achievement = 0.0
            sources: list[str] = []
            if use_manual:
                active_achievement += manual_channel
                sources.append("manual")
            if use_so:
                active_achievement += so_channel
                sources.append("so")
            if use_ci:
                active_achievement += ci_total
                sources.append("ci")
            active_source = "+".join(sources) if sources else "none"
        else:
            # Legacy fallback when every toggle is off — keep CI-first behaviour.
            if ci_total > 0:
                active_source, active_achievement = "ci", ci_total
            elif excel_total > 0:
                active_source, active_achievement = "excel", excel_total
            elif manual_fy > 0:
                active_source, active_achievement = "manual_fy", manual_fy
            elif manual_dist_total > 0:
                active_source, active_achievement = "manual_distributor", manual_dist_total
            else:
                active_source, active_achievement = "ci", 0.0
        # Keep year rollup aligned with the active channel (drops stale Excel totals).
        self.sync_financial_year_achievement_from_breakup(workspace_id, financial_year_id)
        pct = round((active_achievement / target_lakhs) * 100, 2) if target_lakhs > 0 else 0.0
        return {
            "target_lakhs": target_lakhs,
            "achievement_manual_fy": manual_fy,
            "achievement_excel_total": excel_total,
            "achievement_ci_total": ci_total,
            "achievement_manual_distributor_total": manual_dist_total,
            "achievement_so_total": so_total,
            "achievement_manual_channel": manual_channel,
            "achievement_so_channel": so_channel,
            "channels": {
                "manual": use_manual,
                "so": use_so,
                "ci": use_ci,
            },
            "active_source": active_source,
            "active_achievement": active_achievement,
            "percentage": pct,
            "unit": "lakhs",
        }

    def replace_category_breakup(
        self,
        workspace_id: str,
        financial_year_id: int,
        categories: list[dict[str, Any]],
    ) -> int:
        """Replace distributor×category rows for a fiscal year (lakhs)."""
        self.ensure_target_achievement_tables()
        now = datetime.now(timezone.utc).isoformat()

        aggregated: dict[tuple[str, str], dict[str, Any]] = {}
        for item in categories:
            raw_dist = (item.get("distributor") or item.get("distributor_name") or "").strip()
            cat = (item.get("category") or "").strip()
            if not raw_dist or not cat:
                continue
            resolved = self.resolve_ta_distributor_reference(
                raw_dist, workspace_id, item.get("nick")
            )
            dist = resolved["distributor_name"]
            amt = float(item.get("achievement_lakhs") or 0)
            if amt == 0:
                continue
            key = (dist, cat)
            if key not in aggregated:
                aggregated[key] = {
                    "distributor": dist,
                    "category": cat,
                    "nick": resolved.get("nick"),
                    "distributor_id": resolved.get("distributor_id"),
                    "source_distributor_name": resolved.get("source_distributor_name"),
                    "achievement_lakhs": 0.0,
                }
            bucket = aggregated[key]
            bucket["achievement_lakhs"] = round(bucket["achievement_lakhs"] + amt, 4)
            if not bucket.get("nick") and resolved.get("nick"):
                bucket["nick"] = resolved.get("nick")

        with sqlite3.connect(self.db_path) as conn:
            delete_sql = (
                "DELETE FROM target_achievement_category_breakup WHERE financial_year_id = ?"
            )
            delete_params: list[Any] = [financial_year_id]
            cat_cols = {row[1] for row in conn.execute("PRAGMA table_info(target_achievement_category_breakup)")}
            self._migrate_category_breakup_schema(conn)
            if "workspace_id" in cat_cols:
                delete_sql += " AND workspace_id = ?"
                delete_params.append(workspace_id)
            conn.execute(delete_sql, tuple(delete_params))
            count = 0
            for item in aggregated.values():
                insert_cols = [
                    "workspace_id",
                    "financial_year_id",
                    "distributor_name",
                    "nick",
                    "category",
                    "achievement_lakhs",
                    "created_at",
                ]
                vals: list[Any] = [
                    workspace_id,
                    financial_year_id,
                    item["distributor"],
                    item.get("nick"),
                    item["category"],
                    float(item["achievement_lakhs"]),
                    now,
                ]
                if "distributor_id" in cat_cols:
                    insert_cols.insert(4, "distributor_id")
                    vals.insert(4, item.get("distributor_id"))
                if "source_distributor_name" in cat_cols:
                    idx = insert_cols.index("category")
                    insert_cols.insert(idx, "source_distributor_name")
                    vals.insert(idx, item.get("source_distributor_name"))
                placeholders = ", ".join("?" for _ in insert_cols)
                conn.execute(
                    f"""
                    INSERT INTO target_achievement_category_breakup ({', '.join(insert_cols)})
                    VALUES ({placeholders})
                    ON CONFLICT(financial_year_id, distributor_name, category) DO UPDATE SET
                        workspace_id = excluded.workspace_id,
                        nick = COALESCE(excluded.nick, target_achievement_category_breakup.nick),
                        achievement_lakhs = excluded.achievement_lakhs,
                        created_at = excluded.created_at
                    """,
                    tuple(vals),
                )
                count += 1
            conn.commit()
        return count

    def has_category_breakup(self, workspace_id: str, financial_year_id: int) -> bool:
        self.ensure_target_achievement_tables()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM target_achievement_category_breakup "
                "WHERE financial_year_id = ? AND workspace_id = ? LIMIT 1",
                (financial_year_id, workspace_id),
            ).fetchone()
            return row is not None

    def get_category_breakup_matrix(
        self, workspace_id: str, financial_year_id: int
    ) -> dict[str, Any]:
        """Return pivot matrix for category detail modal."""
        self.ensure_target_achievement_tables()
        self._ensure_ta_distributors_linked(workspace_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cat_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(target_achievement_category_breakup)"
                ).fetchall()
            }
            extra = []
            if "distributor_id" in cat_cols:
                extra.append("distributor_id")
            if "source_distributor_name" in cat_cols:
                extra.append("source_distributor_name")
            extra_sel = (", " + ", ".join(extra)) if extra else ""
            rows = conn.execute(
                f"""
                SELECT distributor_name, nick{extra_sel}, category, achievement_lakhs
                FROM target_achievement_category_breakup
                WHERE financial_year_id = ? AND workspace_id = ?
                ORDER BY distributor_name ASC, category ASC
                """,
                (financial_year_id, workspace_id),
            ).fetchall()
        if not rows:
            return {
                "has_data": False,
                "categories": [],
                "rows": [],
                "totals_by_category": {},
                "grand_total": 0.0,
                "unit": "lakhs",
            }

        categories = sorted({r["category"] for r in rows})
        by_dist: dict[str, dict[str, Any]] = {}
        for r in rows:
            dist = r["distributor_name"]
            dist_id = r["distributor_id"] if "distributor_id" in cat_cols else None
            source_name = (
                r["source_distributor_name"] if "source_distributor_name" in cat_cols else None
            )
            identity = self._ta_breakup_display_fields(
                workspace_id, dist, r["nick"], dist_id, source_name
            )
            bucket_key = identity["distributor_name"]
            bucket = by_dist.setdefault(
                bucket_key,
                {
                    "distributor": identity["distributor_name"],
                    "nick": identity.get("nick"),
                    "label": identity.get("display_label"),
                    "matched": identity.get("matched"),
                    "distributor_id": identity.get("distributor_id"),
                    "source_distributor_name": identity.get("source_distributor_name"),
                    "values": {},
                },
            )
            if r["nick"] and not bucket.get("nick"):
                bucket["nick"] = r["nick"]
            bucket["values"][r["category"]] = round(
                bucket["values"].get(r["category"], 0) + float(r["achievement_lakhs"] or 0),
                2,
            )

        matrix_rows = []
        for dist, data in sorted(
            by_dist.items(),
            key=lambda kv: -sum(kv[1]["values"].values()),
        ):
            label = data.get("label") or (
                f"{data.get('nick')} | {dist}" if data.get("nick") else dist
            )
            values = {cat: round(data["values"].get(cat, 0), 2) for cat in categories}
            matrix_rows.append(
                {
                    "distributor": dist,
                    "nick": data.get("nick"),
                    "label": label,
                    "matched": data.get("matched"),
                    "distributor_id": data.get("distributor_id"),
                    "source_distributor_name": data.get("source_distributor_name"),
                    "values": values,
                    "total": round(sum(values.values()), 2),
                }
            )

        totals_by_category = {
            cat: round(sum(r["values"].get(cat, 0) for r in matrix_rows), 2)
            for cat in categories
        }
        return {
            "has_data": True,
            "categories": categories,
            "rows": matrix_rows,
            "totals_by_category": totals_by_category,
            "grand_total": round(sum(totals_by_category.values()), 2),
            "unit": "lakhs",
        }

