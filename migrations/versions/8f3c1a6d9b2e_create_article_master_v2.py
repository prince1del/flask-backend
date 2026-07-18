"""create article_master_v2 (fixes broken /article-master route)

Revision ID: 8f3c1a6d9b2e
Revises: 4d7a9c2e5f1b
Create Date: 2026-07-04 16:00:00.000000

Discovery: the /article-master route (data.py) has always queried a
table named `article_master_v2` that was never actually created
anywhere in this codebase (not in _initialize(), not in any service
module) — meaning this route has always thrown
`sqlite3.OperationalError: no such table` whenever it was called.

This is a distinct, textile-specific product catalog (brand, size,
print style, MRP, PTR, etc.) — genuinely different from the plain
`article_master` table, matching Kunwar's confirmed business context
(each workspace sells different products). This migration creates it
correctly, with workspace_id from day one, since it never existed
before there is nothing to backfill.

Note: CentralizedDB._initialize() also creates this table via
CREATE TABLE IF NOT EXISTS, so on a fresh database this migration is
a no-op; it exists mainly to formally track the schema change and to
create the table on any existing database that hasn't restarted the
app since this fix was applied.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '8f3c1a6d9b2e'
down_revision = '4d7a9c2e5f1b'
branch_labels = None
depends_on = None


def _table_exists(connection, table_name):
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade():
    conn = op.get_bind()
    if not _table_exists(conn, 'article_master_v2'):
        op.create_table(
            'article_master_v2',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('brand', sa.String(length=255), nullable=False),
            sa.Column('tc', sa.String(length=100), nullable=True),
            sa.Column('size', sa.String(length=100), nullable=True),
            sa.Column('bs_size', sa.String(length=100), nullable=True),
            sa.Column('product', sa.String(length=255), nullable=True),
            sa.Column('print_style', sa.String(length=255), nullable=True),
            sa.Column('bale_size', sa.String(length=100), nullable=True),
            sa.Column('colors', sa.String(length=255), nullable=True),
            sa.Column('mrp', sa.Float, server_default=sa.text('0')),
            sa.Column('selling_price', sa.Float, server_default=sa.text('0')),
            sa.Column('ptr', sa.Float, server_default=sa.text('0')),
            sa.Column('retailer_margin', sa.Float, server_default=sa.text('0')),
            sa.Column('exmill_price', sa.Float, server_default=sa.text('0')),
            sa.Column('created_at', sa.String(length=64), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column('workspace_id', sa.String(length=100), nullable=False,
                      server_default=sa.text("'default'")),
        )


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'article_master_v2'):
        op.drop_table('article_master_v2')
