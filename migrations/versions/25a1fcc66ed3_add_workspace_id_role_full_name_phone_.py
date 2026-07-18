"""add workspace_id, role, full_name, phone to users

Revision ID: 25a1fcc66ed3
Revises: 
Create Date: 2026-07-03 18:45:58.683390

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '25a1fcc66ed3'
down_revision = None
branch_labels = None
depends_on = None


def _column_exists(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    return column_name in [col['name'] for col in inspector.get_columns(table_name)]


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'users', 'workspace_id'):
        op.add_column(
            'users',
            sa.Column('workspace_id', sa.String(length=100), nullable=False, server_default=sa.text("'default'")),
        )
    if not _column_exists(conn, 'users', 'role'):
        op.add_column(
            'users',
            sa.Column('role', sa.String(length=50), nullable=False, server_default=sa.text("'admin'")),
        )
    if not _column_exists(conn, 'users', 'full_name'):
        op.add_column(
            'users',
            sa.Column('full_name', sa.String(length=255), nullable=True),
        )
    if not _column_exists(conn, 'users', 'phone'):
        op.add_column(
            'users',
            sa.Column('phone', sa.String(length=20), nullable=True),
        )

    op.execute(
        "UPDATE users SET role = 'admin' WHERE username = 'admin' AND (role IS NULL OR role = '')"
    )
    op.execute(
        "UPDATE users SET workspace_id = 'default' WHERE workspace_id IS NULL OR workspace_id = ''"
    )


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('phone')
        batch_op.drop_column('full_name')
        batch_op.drop_column('role')
        batch_op.drop_column('workspace_id')
