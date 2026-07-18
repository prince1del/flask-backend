"""create order_sheet_master table

Revision ID: 0d8e2c5f4a7b
Revises: 9e2f5b8c1d3a
Create Date: 2026-07-04 14:00:00.000000

NOTE: this table is ALSO created via a raw
`CREATE TABLE IF NOT EXISTS order_sheet_master` inside
CentralizedDB._initialize() (db.py) — meaning it already exists on
any database where the app has been started even once before this
migration runs. This migration is made idempotent (existence-checked)
to avoid `sqlite3.OperationalError: table order_sheet_master already
exists`, matching the check-first pattern used by every other
migration in this project.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0d8e2c5f4a7b'
down_revision = '9e2f5b8c1d3a'
branch_labels = None
depends_on = None


def _table_exists(connection, table_name):
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'order_sheet_master'):
        return
    op.create_table(
        'order_sheet_master',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('uploaded_at', sa.String(length=50), nullable=False),
        sa.Column('workspace_id', sa.String(length=100), nullable=False, server_default='default'),
        sa.Column('file_reference', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_order_sheet_workspace_name')
    )
    op.create_index(
        'ix_order_sheet_master_workspace_category',
        'order_sheet_master',
        ['workspace_id', 'category'],
        unique=False
    )


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'order_sheet_master'):
        op.drop_index('ix_order_sheet_master_workspace_category', table_name='order_sheet_master')
        op.drop_table('order_sheet_master')
