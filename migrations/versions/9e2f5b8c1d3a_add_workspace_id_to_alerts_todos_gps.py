"""add workspace_id to data_entry_alert_logs, workflow_todo_list, gps_visit_verification_logs

Revision ID: 9e2f5b8c1d3a
Revises: 7c9d3e1a4f6b
Create Date: 2026-07-04 13:00:00.000000

These three tables are read from live routes (/alerts, /workflow-gps,
and the dashboard summary) but their write paths are currently unused
by any live route — meaning they are effectively empty in practice
today. This migration closes the gap anyway so the read paths are
correct once these features get wired up to real writers.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9e2f5b8c1d3a'
down_revision = '7c9d3e1a4f6b'
branch_labels = None
depends_on = None

TABLES = ["data_entry_alert_logs", "workflow_todo_list", "gps_visit_verification_logs"]


def _column_exists(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return True
    return column_name in [col['name'] for col in columns]


def upgrade():
    conn = op.get_bind()
    for table in TABLES:
        if not _column_exists(conn, table, 'workspace_id'):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        'workspace_id',
                        sa.String(length=100),
                        nullable=False,
                        server_default=sa.text("'default'"),
                    )
                )
            op.execute(
                f"UPDATE {table} SET workspace_id = 'default' "
                f"WHERE workspace_id IS NULL OR workspace_id = ''"
            )


def downgrade():
    conn = op.get_bind()
    for table in TABLES:
        if _column_exists(conn, table, 'workspace_id'):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('workspace_id')
