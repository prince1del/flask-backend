"""add workspace_id to master_distributors and master_retailers

Revision ID: 4f2a6e5c7b8d
Revises: 3a5d1b9c2f4e
Create Date: 2026-07-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4f2a6e5c7b8d'
down_revision = '3a5d1b9c2f4e'
branch_labels = None
depends_on = None


def _table_exists(connection, table_name):
    return table_name in sa.inspect(connection).get_table_names()


def _column_exists(connection, table_name, column_name):
    if not _table_exists(connection, table_name):
        return False
    inspector = sa.inspect(connection)
    return column_name in [col['name'] for col in inspector.get_columns(table_name)]


def upgrade():
    conn = op.get_bind()

    if _table_exists(conn, 'master_distributors') and not _column_exists(conn, 'master_distributors', 'workspace_id'):
        with op.batch_alter_table('master_distributors', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'workspace_id',
                    sa.String(length=100),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
    if _table_exists(conn, 'master_retailers') and not _column_exists(conn, 'master_retailers', 'workspace_id'):
        with op.batch_alter_table('master_retailers', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'workspace_id',
                    sa.String(length=100),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )

    if _table_exists(conn, 'master_distributors'):
        op.execute(
            "UPDATE master_distributors SET workspace_id = 'default' WHERE workspace_id IS NULL OR workspace_id = ''"
        )
    if _table_exists(conn, 'master_retailers'):
        op.execute(
            "UPDATE master_retailers SET workspace_id = 'default' WHERE workspace_id IS NULL OR workspace_id = ''"
        )


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'master_retailers') and _column_exists(conn, 'master_retailers', 'workspace_id'):
        with op.batch_alter_table('master_retailers', schema=None) as batch_op:
            batch_op.drop_column('workspace_id')
    if _table_exists(conn, 'master_distributors') and _column_exists(conn, 'master_distributors', 'workspace_id'):
        with op.batch_alter_table('master_distributors', schema=None) as batch_op:
            batch_op.drop_column('workspace_id')
