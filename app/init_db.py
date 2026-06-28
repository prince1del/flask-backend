import os
import sqlite3

DB_PATH = os.getenv("DATABASE_PATH", "centralized_db.sqlite3")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS target_achievement_years (id INTEGER PRIMARY KEY, year TEXT NOT NULL, target REAL NOT NULL, created_at TEXT, updated_at TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS target_achievement_uploads (id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL, distributor_name TEXT NOT NULL, amount REAL NOT NULL, file_name TEXT, uploaded_at TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS master_parties (party_uuid TEXT PRIMARY KEY, party_type TEXT NOT NULL, workspace_id TEXT DEFAULT 'bombay_dyeing', primary_name TEXT NOT NULL, gst_number TEXT, mobile_number TEXT, email TEXT, city TEXT, state TEXT, pin_code TEXT, status TEXT, confidence_score REAL, created_at TEXT, updated_at TEXT, created_by TEXT, updated_by TEXT, preferred_name TEXT, common_nickname TEXT, known_abbreviations TEXT, ai_confidence REAL, learning_count INTEGER DEFAULT 0, manual_corrections INTEGER DEFAULT 0)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_aliases (alias_id INTEGER PRIMARY KEY, party_uuid TEXT NOT NULL, workspace_id TEXT DEFAULT 'bombay_dyeing', alias_name TEXT NOT NULL, gst_number TEXT, mobile_number TEXT, email TEXT, confidence_score REAL, source TEXT, created_at TEXT, created_by TEXT, search_tags TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_merges (id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'bombay_dyeing', source_party_uuid TEXT NOT NULL, source_party_name TEXT, target_party_uuid TEXT NOT NULL, target_party_name TEXT, confidence_score REAL, merge_reason TEXT, merge_status TEXT, merged_by TEXT, merged_at TEXT, can_reverse BOOLEAN DEFAULT TRUE, reversed_at TEXT, reversed_by TEXT, reversal_reason TEXT, notes TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_matching_history (match_id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'bombay_dyeing', party1_uuid TEXT, party1_name TEXT, party2_uuid TEXT, party2_name TEXT, gst_match REAL, mobile_match REAL, email_match REAL, name_similarity REAL, city_match REAL, pin_match REAL, state_match REAL, final_confidence_score REAL, match_category TEXT, suggested_action TEXT, created_at TEXT, source TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS party_review_queue (queue_id INTEGER PRIMARY KEY, workspace_id TEXT DEFAULT 'bombay_dyeing', match_id INTEGER, party1_uuid TEXT, party2_uuid TEXT, confidence_score REAL, review_status TEXT, assigned_to TEXT, reviewed_at TEXT, reviewed_by TEXT, decision TEXT, decision_notes TEXT, created_at TEXT, priority INTEGER)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS storage_accounts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, provider TEXT DEFAULT 'google_drive', oauth_token TEXT, refresh_token TEXT, connected_at TEXT, last_sync TEXT, usage_bytes INTEGER DEFAULT 0, quota_bytes INTEGER DEFAULT 107374182400)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS file_index (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, file_name TEXT NOT NULL, file_id TEXT, file_type TEXT, file_size INTEGER, created_at TEXT, modified_at TEXT, tags TEXT, ocr_text TEXT, ai_status TEXT, processing_status TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS file_versions (id INTEGER PRIMARY KEY, file_id TEXT NOT NULL, version_id TEXT, created_at TEXT, created_by TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS file_operations_log (id INTEGER PRIMARY KEY, file_id TEXT, operation TEXT, performed_by TEXT, performed_at TEXT, details TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS target_achievement_breakup (id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL, distributor_name TEXT NOT NULL, region TEXT, achievement REAL)"""
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized!")
