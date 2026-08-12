"""
NEXORA — Workspace Rename Utility

Safely renames a workspace_id across EVERY table in the database that
has a workspace_id column — both the raw-sqlite tables (managed by
CentralizedDB / centralized_db_system/db.py) and the SQLAlchemy-managed
tables (app/models.py, backing business_brain.py's Conversations,
Workflows, Rules, etc.).

WHY THIS EXISTS:
Renaming a workspace_id "by hand" (editing rows in one table at a time)
is error-prone once real data exists across dozens of tables — you can
easily miss one and end up with orphaned/split data. This script
updates ALL of them together, in a single database transaction: either
every table updates successfully, or NONE of them do (automatic
rollback on any error) — so you can never end up in a half-renamed
state.

USAGE:
    python rename_workspace.py <old_workspace_id> <new_workspace_id>

Example:
    python rename_workspace.py bombay_dyeing_gt_north bombay_dyeing_gt_west

Run this from the project root (same folder as centralized_db.sqlite3),
with your venv activated. It only touches the ONE database file that
your app's DATABASE_PATH/DATABASE_URL already points to — pass a
different path as the DATABASE_PATH env var beforehand if needed.
"""

import os
import sqlite3
import sys
from pathlib import Path

# Every table (across both the raw-sqlite and SQLAlchemy-managed halves
# of the schema) that has a workspace_id column, as of 4 July 2026.
# If a NEW table with workspace_id is added later, add its name here too.
TABLES_WITH_WORKSPACE_ID = [
    # --- raw sqlite (centralized_db_system/db.py) ---
    "users",
    "master_distributors",
    "master_retailers",
    "credit_control",
    "data_entry_alert_logs",
    "workflow_todo_list",
    "gps_visit_verification_logs",
    "primary_sales",
    "secondary_sales",
    "targets_achievements",
    "order_lifecycle_tracking",
    "article_master",
    "article_master_v2",
    "custom_schema_fields",
    "storage_accounts",
    "file_index",
    "target_achievement_years",
    "target_achievement_uploads",
    "target_achievement_breakup",
    # --- SQLAlchemy (app/models.py) ---
    "distributors",
    "retailers",
    "sales_orders",
    "invoices",
    "finance_accounts",
    "gst_returns",
    "vat_returns",
    "inventory",
    "conversations",
    "workflows",
    "workflow_executions",
    "events",
    "event_subscriptions",
    "knowledge_graph_entities",
    "knowledge_graph_relationships",
    "rule_executions",
    "ai_responses",
    "business_rules",
]


def _resolve_db_path() -> str:
    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        return env_path
    project_root = Path(__file__).resolve().parent
    root_db = project_root / "centralized_db.sqlite3"
    instance_db = project_root / "instance" / "centralized_db.sqlite3"
    if root_db.exists():
        return str(root_db)
    if instance_db.exists():
        return str(instance_db)
    return "centralized_db.sqlite3"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _has_workspace_id_column(conn: sqlite3.Connection, table: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(col[1] == "workspace_id" for col in cols)


def rename_workspace(db_path: str, old_id: str, new_id: str, dry_run: bool = False) -> None:
    print(f"Database: {db_path}")
    print(f"Renaming workspace_id: '{old_id}' -> '{new_id}'")
    print(f"Dry run: {dry_run}\n")

    conn = sqlite3.connect(db_path)
    try:
        results = []
        for table in TABLES_WITH_WORKSPACE_ID:
            if not _table_exists(conn, table):
                results.append((table, "SKIPPED (table does not exist)", 0))
                continue
            if not _has_workspace_id_column(conn, table):
                results.append((table, "SKIPPED (no workspace_id column)", 0))
                continue

            count_before = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (old_id,)
            ).fetchone()[0]

            if count_before == 0:
                results.append((table, "no matching rows", 0))
                continue

            if not dry_run:
                conn.execute(
                    f"UPDATE {table} SET workspace_id = ? WHERE workspace_id = ?",
                    (new_id, old_id),
                )

            results.append((table, "updated" if not dry_run else "would update", count_before))

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        print(f"{'Table':<35} {'Status':<30} {'Rows':>6}")
        print("-" * 73)
        total = 0
        for table, status, count in results:
            if count > 0:
                print(f"{table:<35} {status:<30} {count:>6}")
                total += count
        print("-" * 73)
        print(f"Total rows {'that would be' if dry_run else ''} renamed: {total}")

        if dry_run:
            print("\nThis was a DRY RUN — no changes were made to the database.")
            print("Re-run without --dry-run to actually apply the rename.")
        else:
            print("\n✅ Rename complete and committed.")

    except Exception:
        conn.rollback()
        print("\n❌ ERROR — transaction rolled back. NO changes were made to any table.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    if len(args) != 2:
        print(__doc__)
        print("\nERROR: expected exactly 2 arguments (old_workspace_id, new_workspace_id).")
        print("Optionally add --dry-run to preview changes without applying them.")
        sys.exit(1)

    old_workspace_id, new_workspace_id = args
    if old_workspace_id == new_workspace_id:
        print("ERROR: old and new workspace_id are identical — nothing to do.")
        sys.exit(1)

    db_path = _resolve_db_path()
    rename_workspace(db_path, old_workspace_id, new_workspace_id, dry_run=dry_run)
