"""add workspace_id to credit_control

Revision ID: 7c9d3e1a4f6b
Revises: 4f2a6e5c7b8d
Create Date: 2026-07-04 12:00:00.000000

NOTE: credit_control lives in the raw sqlite3-managed schema
(centralized_db_system/db.py's _initialize()), not the SQLAlchemy
models.py schema. This migration uses raw SQL via op.execute so it
works against that same sqlite database file, consistent with how
migration 4f2a6e5c7b8d handled master_distributors/master_retailers
(which also live in that raw schema).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7c9d3e1a4f6b'
down_revision = '4f2a6e5c7b8d'
branch_labels = None
depends_on = None


def _column_exists(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        # Table doesn't exist yet in this database (e.g. a fresh DB that
        # hasn't run CentralizedDB._initialize() yet) — nothing to migrate.
        return True
    return column_name in [col['name'] for col in columns]


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'credit_control', 'workspace_id'):
        with op.batch_alter_table('credit_control', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'workspace_id',
                    sa.String(length=100),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
        op.execute(
            "UPDATE credit_control SET workspace_id = 'default' "
            "WHERE workspace_id IS NULL OR workspace_id = ''"
        )


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'credit_control', 'workspace_id'):
        with op.batch_alter_table('credit_control', schema=None) as batch_op:
            batch_op.drop_column('workspace_id')
