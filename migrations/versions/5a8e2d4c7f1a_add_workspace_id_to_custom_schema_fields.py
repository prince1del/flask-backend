"""add workspace_id to custom_schema_fields (with corrected UNIQUE constraint)

Revision ID: 5a8e2d4c7f1a
Revises: 8f3c1a6d9b2e
Create Date: 2026-07-04 17:00:00.000000

custom_schema_fields previously had UNIQUE(entity_type, field_name) —
a single global schema shared by every workspace. Confirmed with the
founder that different workspaces/executives sell genuinely different
product categories (bedsheets/towels vs parlour items) and need their
own custom fields, so this must become workspace-scoped.

Because the UNIQUE constraint itself needs to change (to
UNIQUE(workspace_id, entity_type, field_name), so two workspaces CAN
both have a field named e.g. "fabric_type"), this requires a full
table rebuild rather than a simple ADD COLUMN — SQLite does not support
altering an existing UNIQUE constraint in place.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5a8e2d4c7f1a'
down_revision = '8f3c1a6d9b2e'
branch_labels = None
depends_on = None


def _table_exists(connection, table_name):
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def _column_exists(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return column_name in [col['name'] for col in columns]


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, 'custom_schema_fields'):
        # Fresh database — CentralizedDB._initialize() will create it
        # with the correct schema directly. Nothing to migrate.
        return

    if _column_exists(conn, 'custom_schema_fields', 'workspace_id'):
        return

    op.execute(
        """
        CREATE TABLE custom_schema_fields_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_label TEXT NOT NULL,
            field_type TEXT DEFAULT 'text',
            field_order INTEGER DEFAULT 0,
            is_required INTEGER DEFAULT 0,
            is_visible INTEGER DEFAULT 1,
            options TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            workspace_id TEXT NOT NULL DEFAULT 'default',
            UNIQUE(workspace_id, entity_type, field_name)
        )
        """
    )
    op.execute(
        """
        INSERT INTO custom_schema_fields_new
            (id, entity_type, field_name, field_label, field_type,
             field_order, is_required, is_visible, options, created_at, workspace_id)
        SELECT
            id, entity_type, field_name, field_label, field_type,
            field_order, is_required, is_visible, options, created_at, 'default'
        FROM custom_schema_fields
        """
    )
    op.execute("DROP TABLE custom_schema_fields")
    op.execute("ALTER TABLE custom_schema_fields_new RENAME TO custom_schema_fields")


def downgrade():
    conn = op.get_bind()
    if not _table_exists(conn, 'custom_schema_fields'):
        return
    if not _column_exists(conn, 'custom_schema_fields', 'workspace_id'):
        return

    op.execute(
        """
        CREATE TABLE custom_schema_fields_old (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_label TEXT NOT NULL,
            field_type TEXT DEFAULT 'text',
            field_order INTEGER DEFAULT 0,
            is_required INTEGER DEFAULT 0,
            is_visible INTEGER DEFAULT 1,
            options TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(entity_type, field_name)
        )
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO custom_schema_fields_old
            (id, entity_type, field_name, field_label, field_type,
             field_order, is_required, is_visible, options, created_at)
        SELECT
            id, entity_type, field_name, field_label, field_type,
            field_order, is_required, is_visible, options, created_at
        FROM custom_schema_fields
        """
    )
    op.execute("DROP TABLE custom_schema_fields")
    op.execute("ALTER TABLE custom_schema_fields_old RENAME TO custom_schema_fields")
