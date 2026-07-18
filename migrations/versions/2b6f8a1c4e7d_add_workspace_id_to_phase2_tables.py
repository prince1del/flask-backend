"""add workspace_id to primary_sales, secondary_sales, targets_achievements, order_lifecycle_tracking

Revision ID: 2b6f8a1c4e7d
Revises: 9e2f5b8c1d3a
Create Date: 2026-07-04 14:00:00.000000

These 4 tables are not yet wired to any live route (no API endpoints
use them today) — they exist in the schema ahead of Phase 2 work:
  - primary_sales / secondary_sales: sell-in vs sell-out tracking
  - targets_achievements: the manual-entry target/achievement path
    (distinct from target_achievement_years/uploads, which handles
    the calculated, product-level path — both are intentionally kept)
  - order_lifecycle_tracking: Order -> SO -> CI -> Dispatch -> Payment
    tracking, which Phase 2's reconciliation feature will build on

Adding workspace_id now means the database foundation is ready before
the Phase 2 API routes are built on top of it.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2b6f8a1c4e7d'
down_revision = '9e2f5b8c1d3a'
branch_labels = None
depends_on = None

TABLES = ["primary_sales", "secondary_sales", "targets_achievements", "order_lifecycle_tracking"]


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
