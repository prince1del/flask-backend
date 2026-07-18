"""add missing status column to users table

Revision ID: 6c1e4f8a2d5b
Revises: 5a8e2d4c7f1a
Create Date: 2026-07-04 18:00:00.000000

Discovery: CentralizedDB.create_user() and app.models.User (SQLAlchemy)
both read/write a `status` column on `users` — but the LIVE production
database's `users` table predates this column being added to the
_initialize() CREATE TABLE statement, and no migration was ever written
for it. Result: create_user() crashed with
`sqlite3.OperationalError: table users has no column named status`
on any existing (non-fresh) database.

This is the same "schema drift between code and existing DB file"
pattern documented in the 4 July 2026 audit log — code was updated,
but the already-existing production database was never migrated to
match.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6c1e4f8a2d5b'
down_revision = '5a8e2d4c7f1a'
branch_labels = None
depends_on = None


def _column_exists(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return True
    return column_name in [col['name'] for col in columns]


def upgrade():
    conn = op.get_bind()
    if not _column_exists(conn, 'users', 'status'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'status',
                    sa.String(length=20),
                    nullable=False,
                    server_default=sa.text("'active'"),
                )
            )
        op.execute(
            "UPDATE users SET status = 'active' WHERE status IS NULL OR status = ''"
        )


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'users', 'status'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_column('status')
