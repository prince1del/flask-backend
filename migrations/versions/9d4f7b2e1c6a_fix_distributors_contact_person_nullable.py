"""fix schema drift: contact_person should be nullable (distributors + retailers)

Revision ID: 9d4f7b2e1c6a
Revises: 6c1e4f8a2d5b
Create Date: 2026-07-04 20:30:00.000000

Discovery: app.models.Distributor.contact_person is defined as
nullable=True, but the LIVE database's distributors table has a
NOT NULL constraint on this column (created before the model was
updated, or from an earlier schema version) — causing every
distributor-creation attempt through the real UI to fail with
`sqlite3.IntegrityError: NOT NULL constraint failed:
distributors.contact_person`, since the "Add Distributor" form
(correctly) has no field for it and the model correctly treats it
as optional.

app.models.Retailer.contact_person has the exact same nullable=True
definition — checked proactively here too.

IMPLEMENTATION NOTE: this does NOT use Alembic's batch_alter_table,
because the live distributors/retailers tables have accumulated extra
columns over time via web_app.py's _ensure_compatibility_columns()
raw ALTER TABLE calls, one of which (workspace_id) ended up with a
default-value clause SQLite considers "not constant"
(`DEFAULT ("default")`) — which makes batch_alter_table's own
reflection-based table-rebuild fail with
`OperationalError: default value of column [workspace_id] is not
constant`, even though this migration never touches that column.

Instead, this migration reads each table's CURRENT actual CREATE
TABLE statement directly from sqlite_master, surgically removes only
the "NOT NULL" that immediately follows the contact_person column
definition (leaving every other column, index, and default exactly as
they already are on the live database), and rebuilds the table under
that corrected schema.
"""
import re

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9d4f7b2e1c6a'
down_revision = '6c1e4f8a2d5b'
branch_labels = None
depends_on = None

TABLES_TO_FIX = ['distributors', 'retailers']


def _column_is_nullable(connection, table_name, column_name):
    inspector = sa.inspect(connection)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return True
    for col in columns:
        if col['name'] == column_name:
            return col.get('nullable', True)
    return True


def _get_create_table_sql(connection, table_name):
    row = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] if row else None


def _get_column_names(connection, table_name):
    result = connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
    return [row[1] for row in result.fetchall()]


def _make_contact_person_nullable(create_sql: str) -> str:
    """
    Surgically removes 'NOT NULL' immediately following the
    contact_person column definition, leaving every other column
    definition byte-for-byte identical.
    """
    pattern = re.compile(
        r"(\bcontact_person\b\s+\w+(?:\(\d+\))?)\s+NOT\s+NULL",
        re.IGNORECASE,
    )
    new_sql, count = pattern.subn(r"\1", create_sql, count=1)
    return new_sql


def _rebuild_table_with_nullable_contact_person(connection, table_name):
    original_sql = _get_create_table_sql(connection, table_name)
    if not original_sql:
        return

    if "contact_person" not in original_sql:
        return

    fixed_sql = _make_contact_person_nullable(original_sql)
    if fixed_sql == original_sql:
        return

    temp_table = f"{table_name}_tmp_9d4f7b2e1c6a"
    fixed_sql_for_temp = fixed_sql.replace(
        f"TABLE {table_name}", f"TABLE {temp_table}", 1
    ).replace(
        f'TABLE "{table_name}"', f'TABLE "{temp_table}"', 1
    )

    columns = _get_column_names(connection, table_name)
    column_list = ", ".join(f'"{c}"' for c in columns)

    connection.exec_driver_sql(fixed_sql_for_temp)
    connection.exec_driver_sql(
        f'INSERT INTO "{temp_table}" ({column_list}) SELECT {column_list} FROM "{table_name}"'
    )
    connection.exec_driver_sql(f'DROP TABLE "{table_name}"')
    connection.exec_driver_sql(f'ALTER TABLE "{temp_table}" RENAME TO "{table_name}"')


def upgrade():
    conn = op.get_bind()
    for table in TABLES_TO_FIX:
        if not _column_is_nullable(conn, table, 'contact_person'):
            _rebuild_table_with_nullable_contact_person(conn, table)


def downgrade():
    # Intentionally a no-op: re-introducing a NOT NULL constraint on a
    # column that may now contain NULLs (from records created while
    # nullable) is destructive and not safely reversible here.
    pass
