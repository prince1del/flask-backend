"""add buyer_code to master_distributors

Revision ID: 1a7b2c3d4e5f
Revises: 0d8e2c5f4a7b
Create Date: 2026-07-04 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1a7b2c3d4e5f'
down_revision = '0d8e2c5f4a7b'
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
    if not _table_exists(conn, 'master_distributors'):
        return
    if not _column_exists(conn, 'master_distributors', 'buyer_code'):
        with op.batch_alter_table('master_distributors', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column('buyer_code', sa.String(length=100), nullable=True)
            )
    op.execute(
        "UPDATE master_distributors SET buyer_code = NULL WHERE buyer_code IS NULL"
    )


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'master_distributors', 'buyer_code'):
        with op.batch_alter_table('master_distributors', schema=None) as batch_op:
            batch_op.drop_column('buyer_code')
