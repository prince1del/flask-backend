import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH")
if not DB_PATH:
    project_root = Path(__file__).resolve().parent.parent
    root_db = project_root / "centralized_db.sqlite3"
    instance_db = project_root / "instance" / "centralized_db.sqlite3"
    DB_PATH = str(root_db) if root_db.exists() else (str(instance_db) if instance_db.exists() else "centralized_db.sqlite3")


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_definition: str) -> None:
    column_name = column_definition.split()[0]
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not table_exists:
        return
    existing_columns = _get_table_columns(conn, table_name)
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS target_achievement_years (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            year TEXT,
            financial_year TEXT,
            target REAL,
            achievement REAL,
            achievement_percent REAL,
            target_source TEXT,
            achievement_source TEXT,
            remarks TEXT,
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS target_achievement_uploads (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            year_id INTEGER,
            financial_year_id INTEGER,
            distributor_name TEXT,
            amount REAL,
            calculated_total REAL,
            file_name TEXT,
            uploaded_at TEXT
        )
        """
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS master_parties (party_uuid TEXT PRIMARY KEY, party_type TEXT NOT NULL, workspace_id TEXT DEFAULT 'default', primary_name TEXT NOT NULL, gst_number TEXT, mobile_number TEXT, email TEXT, city TEXT, state TEXT, pin_code TEXT, status TEXT, confidence_score REAL, created_at TEXT, updated_at TEXT, created_by TEXT, updated_by TEXT, preferred_name TEXT, common_nickname TEXT, known_abbreviations TEXT, ai_confidence REAL, learning_count INTEGER DEFAULT 0, manual_corrections INTEGER DEFAULT 0)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_aliases (alias_id INTEGER PRIMARY KEY, party_uuid TEXT NOT NULL, workspace_id TEXT DEFAULT 'default', alias_name TEXT NOT NULL, gst_number TEXT, mobile_number TEXT, email TEXT, confidence_score REAL, source TEXT, created_at TEXT, created_by TEXT, search_tags TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_merges (id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'default', source_party_uuid TEXT NOT NULL, source_party_name TEXT, target_party_uuid TEXT NOT NULL, target_party_name TEXT, confidence_score REAL, merge_reason TEXT, merge_status TEXT, merged_by TEXT, merged_at TEXT, can_reverse BOOLEAN DEFAULT TRUE, reversed_at TEXT, reversed_by TEXT, reversal_reason TEXT, notes TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_matching_history (match_id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'default', party1_uuid TEXT, party1_name TEXT, party2_uuid TEXT, party2_name TEXT, gst_match REAL, mobile_match REAL, email_match REAL, name_similarity REAL, city_match REAL, pin_match REAL, state_match REAL, final_confidence_score REAL, match_category TEXT, suggested_action TEXT, created_at TEXT, source TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_review_queue (queue_id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'default', match_id INTEGER, party1_uuid TEXT, party2_uuid TEXT, confidence_score REAL, review_status TEXT, assigned_to TEXT, reviewed_at TEXT, reviewed_by TEXT, decision TEXT, decision_notes TEXT, created_at TEXT, priority INTEGER)"""
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_accounts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            workspace_id TEXT DEFAULT 'default',
            provider_type TEXT DEFAULT 'google_drive',
            oauth_token TEXT,
            refresh_token TEXT,
            connected_at TEXT,
            last_sync TEXT,
            sync_status TEXT,
            total_storage_bytes INTEGER DEFAULT 0,
            used_storage_bytes INTEGER DEFAULT 0,
            usage_bytes INTEGER DEFAULT 0,
            quota_bytes INTEGER DEFAULT 107374182400,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, workspace_id, provider_type)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS file_index (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            storage_account_id INTEGER,
            user_id INTEGER,
            owner_id INTEGER,
            company TEXT,
            module TEXT,
            file_id TEXT,
            file_name TEXT,
            file_type TEXT,
            mime_type TEXT,
            folder_path TEXT,
            file_size INTEGER,
            file_size_bytes INTEGER,
            size INTEGER,
            created_at TEXT,
            modified_at TEXT,
            indexed_at TEXT,
            last_synced TEXT,
            sync_status TEXT,
            tags TEXT,
            search_tags TEXT,
            ocr_text TEXT,
            ocr_status TEXT,
            ai_status TEXT,
            processing_status TEXT,
            version_number INTEGER,
            created_by INTEGER,
            updated_by INTEGER,
            FOREIGN KEY(storage_account_id) REFERENCES storage_accounts(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS file_versions (
            id INTEGER PRIMARY KEY,
            file_index_id INTEGER,
            file_id TEXT,
            version_id TEXT,
            version_number INTEGER,
            created_at TEXT,
            modified_at TEXT,
            created_by TEXT,
            FOREIGN KEY(file_index_id) REFERENCES file_index(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS file_operations_log (
            id INTEGER PRIMARY KEY,
            file_index_id INTEGER,
            file_id TEXT,
            operation TEXT,
            operation_type TEXT,
            performed_by INTEGER,
            user_id INTEGER,
            operation_status TEXT,
            error_message TEXT,
            created_at TEXT,
            details TEXT
        )
        """
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS target_achievement_breakup (id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL, distributor_name TEXT NOT NULL, region TEXT, achievement REAL)"""
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            title TEXT,
            status TEXT DEFAULT 'open',
            context TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            role TEXT DEFAULT 'user',
            content TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_context (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            definition TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_executions (
            id INTEGER PRIMARY KEY,
            workflow_id INTEGER NOT NULL,
            workspace_id TEXT DEFAULT 'default',
            status TEXT DEFAULT 'running',
            input_data TEXT,
            output_data TEXT,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS business_rules (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            rule_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            definition TEXT,
            priority INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id INTEGER PRIMARY KEY,
            workflow_id INTEGER NOT NULL,
            step_type TEXT DEFAULT 'manual',
            config TEXT,
            order_index INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_step_executions (
            id INTEGER PRIMARY KEY,
            workflow_execution_id INTEGER NOT NULL,
            workflow_step_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            input_data TEXT,
            output_data TEXT,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY(workflow_execution_id) REFERENCES workflow_executions(id),
            FOREIGN KEY(workflow_step_id) REFERENCES workflow_steps(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_executions (
            id INTEGER PRIMARY KEY,
            rule_id INTEGER NOT NULL,
            workspace_id TEXT DEFAULT 'default',
            result TEXT,
            status TEXT DEFAULT 'completed',
            created_at TEXT,
            FOREIGN KEY(rule_id) REFERENCES business_rules(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_notes (
            id INTEGER PRIMARY KEY,
            workflow_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            author TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_execution_status_history (
            id INTEGER PRIMARY KEY,
            workflow_execution_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY(workflow_execution_id) REFERENCES workflow_executions(id)
        )
        """
    )
    cursor.execute(
        """\n        CREATE TABLE IF NOT EXISTS ai_responses (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            conversation_id INTEGER,
            prompt TEXT,
            response_text TEXT,
            status TEXT DEFAULT 'completed',
            model_name TEXT,
            token_count INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            feedback TEXT,
            extra_metadata TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            event_type TEXT NOT NULL,
            payload TEXT,
            created_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_subscriptions (
            id INTEGER PRIMARY KEY,
            workspace_id TEXT DEFAULT 'default',
            event_type TEXT NOT NULL,
            callback_url TEXT,
            filters TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_graph_entities (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            workspace_id TEXT DEFAULT 'default',
            name TEXT,
            properties TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(entity_type, entity_id, workspace_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_graph_relationships (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            workspace_id TEXT DEFAULT 'default',
            relationship_type TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            properties TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # Legacy schema compatibility: add missing columns to avoid runtime failures
    _add_column_if_missing(conn, 'storage_accounts', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'storage_accounts', 'provider_type TEXT DEFAULT "google_drive"')
    _add_column_if_missing(conn, 'storage_accounts', 'sync_status TEXT')
    _add_column_if_missing(conn, 'storage_accounts', 'total_storage_bytes INTEGER DEFAULT 0')
    _add_column_if_missing(conn, 'storage_accounts', 'used_storage_bytes INTEGER DEFAULT 0')
    _add_column_if_missing(conn, 'business_rules', 'name TEXT DEFAULT "Untitled rule"')
    _add_column_if_missing(conn, 'business_rules', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'business_rules', 'rule_key TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'business_rules', 'definition TEXT')
    _add_column_if_missing(conn, 'business_rules', 'priority INTEGER DEFAULT 0')
    _add_column_if_missing(conn, 'business_rules', 'enabled INTEGER DEFAULT 1')
    _add_column_if_missing(conn, 'business_rules', 'created_at TEXT')
    _add_column_if_missing(conn, 'business_rules', 'updated_at TEXT')
    _add_column_if_missing(conn, 'storage_accounts', 'created_at TEXT')
    _add_column_if_missing(conn, 'storage_accounts', 'updated_at TEXT')

    _add_column_if_missing(conn, 'users', 'email TEXT')
    _add_column_if_missing(conn, 'users', 'full_name TEXT')
    _add_column_if_missing(conn, 'users', 'phone TEXT')
    _add_column_if_missing(conn, 'users', 'updated_at TEXT')
    _add_column_if_missing(conn, 'users', 'gdrive_access_token TEXT')
    _add_column_if_missing(conn, 'users', 'gdrive_refresh_token TEXT')
    _add_column_if_missing(conn, 'users', 'gdrive_connected INTEGER DEFAULT 0')
    _add_column_if_missing(conn, 'users', 'gdrive_email TEXT')

    _add_column_if_missing(conn, 'file_index', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'file_index', 'storage_account_id INTEGER')
    _add_column_if_missing(conn, 'file_index', 'owner_id INTEGER')
    _add_column_if_missing(conn, 'file_index', 'company TEXT')
    _add_column_if_missing(conn, 'file_index', 'module TEXT')
    _add_column_if_missing(conn, 'file_index', 'mime_type TEXT')
    _add_column_if_missing(conn, 'file_index', 'folder_path TEXT')
    _add_column_if_missing(conn, 'file_index', 'file_size_bytes INTEGER')
    _add_column_if_missing(conn, 'file_index', 'indexed_at TEXT')
    _add_column_if_missing(conn, 'file_index', 'last_synced TEXT')
    _add_column_if_missing(conn, 'file_index', 'sync_status TEXT')
    _add_column_if_missing(conn, 'file_index', 'search_tags TEXT')
    _add_column_if_missing(conn, 'file_index', 'version_number INTEGER')
    _add_column_if_missing(conn, 'file_index', 'ocr_status TEXT')
    _add_column_if_missing(conn, 'file_index', 'ai_status TEXT')
    _add_column_if_missing(conn, 'file_index', 'processing_status TEXT')
    _add_column_if_missing(conn, 'file_index', 'created_by INTEGER')
    _add_column_if_missing(conn, 'file_index', 'updated_by INTEGER')

    _add_column_if_missing(conn, 'target_achievement_years', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'target_achievement_years', 'financial_year TEXT')
    _add_column_if_missing(conn, 'target_achievement_years', 'target REAL')
    _add_column_if_missing(conn, 'target_achievement_years', 'achievement REAL')
    _add_column_if_missing(conn, 'target_achievement_years', 'achievement_percent REAL')
    _add_column_if_missing(conn, 'target_achievement_years', 'target_source TEXT')
    _add_column_if_missing(conn, 'target_achievement_years', 'achievement_source TEXT')
    _add_column_if_missing(conn, 'target_achievement_years', 'remarks TEXT')
    _add_column_if_missing(conn, 'target_achievement_years', 'created_by TEXT')
    _add_column_if_missing(conn, 'target_achievement_years', 'updated_at TEXT')

    _add_column_if_missing(conn, 'target_achievement_uploads', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'year_id INTEGER')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'financial_year_id INTEGER')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'distributor_name TEXT')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'amount REAL')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'calculated_total REAL')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'file_name TEXT')
    _add_column_if_missing(conn, 'target_achievement_uploads', 'uploaded_at TEXT')

    _add_column_if_missing(conn, 'sales_orders', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'sales_order_items', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'invoices', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'invoice_payments', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'dispatches', 'workspace_id TEXT DEFAULT "default"')

    _add_column_if_missing(conn, 'distributors', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'retailers', 'workspace_id TEXT DEFAULT "default"')

    _add_column_if_missing(conn, 'target_achievement_breakup', 'workspace_id TEXT DEFAULT "default"')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'year_id INTEGER')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'financial_year_id INTEGER')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'distributor_name TEXT')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'region TEXT')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'achievement REAL')
    _add_column_if_missing(conn, 'target_achievement_breakup', 'created_at TEXT')

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized!")
