"""create company_profile table

Revision ID: 3e5f7a9c1b2d
Revises: 1a7b2c3d4e5f
Create Date: 2026-07-05 10:00:00.000000

Each workspace's own company identity (name, GST, address, etc.) —
used so the app can tell "our own GST" apart from a buyer/distributor's
GST when parsing Sales Order / Commercial Invoice documents, without
hardcoding any single company's details. One row per workspace_id.

Restricted to executive-level roles (admin, sales_executive) only —
this is deliberately NOT visible to distributor/retailer-type logins
when those are introduced later, since it represents the executive's
own employer/supplier identity, not the distributor's own.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3e5f7a9c1b2d'
down_revision = '1a7b2c3d4e5f'
branch_labels = None
depends_on = None


def _table_exists(connection, table_name):
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade():
    conn = op.get_bind()
    if not _table_exists(conn, 'company_profile'):
        op.create_table(
            'company_profile',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('workspace_id', sa.String(length=100), nullable=False, unique=True),
            sa.Column('company_name', sa.String(length=255), nullable=False),
            sa.Column('gst_number', sa.String(length=15), nullable=True),
            sa.Column('pan_number', sa.String(length=10), nullable=True),
            sa.Column('address', sa.Text(), nullable=True),
            sa.Column('city', sa.String(length=100), nullable=True),
            sa.Column('state', sa.String(length=100), nullable=True),
            sa.Column('pincode', sa.String(length=10), nullable=True),
            sa.Column('created_at', sa.String(length=50), nullable=False),
            sa.Column('updated_at', sa.String(length=50), nullable=False),
        )


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'company_profile'):
        op.drop_table('company_profile')
