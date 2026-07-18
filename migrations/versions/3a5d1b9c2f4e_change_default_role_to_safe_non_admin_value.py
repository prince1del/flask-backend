"""change default role to safe non-admin value

Revision ID: 3a5d1b9c2f4e
Revises: 25a1fcc66ed3
Create Date: 2026-07-03 19:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3a5d1b9c2f4e'
down_revision = '25a1fcc66ed3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=sa.String(length=50),
            existing_nullable=False,
            server_default=sa.text("'unassigned'"),
        )


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=sa.String(length=50),
            existing_nullable=False,
            server_default=sa.text("'admin'"),
        )
