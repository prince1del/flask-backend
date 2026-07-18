"""fix GST uniqueness: scope to active distributors/retailers per workspace

Revision ID: a2b4c6d8e0f2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-05 12:00:00.000000

Discovery (via real-world testing): "Delete" on a distributor/retailer
is a SOFT delete (status set to 'inactive'), but gst_number had a
blanket UNIQUE constraint with no regard for status — so re-creating a
distributor with the SAME GST after "deleting" the old one always
failed with a confusing "GST number already exists" error, even
though the old record was no longer visible anywhere in the UI.

Fix: replace the blanket unique index on gst_number with a PARTIAL
unique index scoped to (workspace_id, gst_number) WHERE status =
'active' AND gst_number IS NOT NULL. This means:
  - Two ACTIVE distributors in the same workspace can never share a
    GST number (GST numbers are genuinely unique per real firm, by
    law — this must remain a hard constraint).
  - An INACTIVE (soft-deleted) distributor's old GST number becomes
    available for reuse by a new record, matching what "Delete"
    actually looks like from the user's perspective.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a2b4c6d8e0f2'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None

TABLES = ['distributors', 'retailers']


def _find_gst_unique_indexes(connection, table_name):
    """Finds any existing index on this table whose definition
    references gst_number, so we can drop it regardless of its
    auto-generated name."""
    rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table_name,),
    ).fetchall()
    return [
        row[0] for row in rows
        if row[1] and "gst_number" in row[1] and "UNIQUE" in row[1].upper()
    ]


def _partial_index_exists(connection, index_name):
    row = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


def upgrade():
    conn = op.get_bind()
    for table in TABLES:
        for old_index_name in _find_gst_unique_indexes(conn, table):
            conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{old_index_name}"')

        new_index_name = f"uq_{table}_gst_active_per_workspace"
        if not _partial_index_exists(conn, new_index_name):
            conn.exec_driver_sql(
                f'CREATE UNIQUE INDEX "{new_index_name}" '
                f'ON {table} (workspace_id, gst_number) '
                f"WHERE status = 'active' AND gst_number IS NOT NULL"
            )


def downgrade():
    conn = op.get_bind()
    for table in TABLES:
        index_name = f"uq_{table}_gst_active_per_workspace"
        conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')
        conn.exec_driver_sql(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "ix_{table}_gst_number" '
            f"ON {table} (gst_number)"
        )
