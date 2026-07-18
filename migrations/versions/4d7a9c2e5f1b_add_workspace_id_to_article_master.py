"""add workspace_id to article_master

Revision ID: 4d7a9c2e5f1b
Revises: 2b6f8a1c4e7d
Create Date: 2026-07-04 15:00:00.000000

Each workspace/executive sells genuinely different products (e.g. one
sells bedsheets/towels, another sells parlour items) — confirmed with
the founder this is NOT shared/global data, unlike business_rules.
This is used live via the /articles route (data.py).

NOTE: the /article-master route queries a separate table named
`article_master_v2` which does not appear anywhere in this codebase's
CREATE TABLE statements — that is a distinct issue being investigated
separately and is NOT addressed by this migration.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4d7a9c2e5f1b'
down_revision = '2b6f8a1c4e7d'
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
    if not _column_exists(conn, 'article_master', 'workspace_id'):
        with op.batch_alter_table('article_master', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'workspace_id',
                    sa.String(length=100),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
        op.execute(
            "UPDATE article_master SET workspace_id = 'default' "
            "WHERE workspace_id IS NULL OR workspace_id = ''"
        )


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'article_master', 'workspace_id'):
        with op.batch_alter_table('article_master', schema=None) as batch_op:
            batch_op.drop_column('workspace_id')
