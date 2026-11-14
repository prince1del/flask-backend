"""SO numbers are unique per company, not across NEXORA."""

import sqlite3

from app.services import fo_so_match_db as matchdb


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, workspace_id TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "co_a"), (2, "co_b"), (3, "co_a")])
    matchdb._schema_ensured = False
    return conn


def test_two_companies_can_save_the_same_so_number():
    conn = _conn()
    matchdb.ensure_schema(conn)
    matchdb._insert_so_index_for_run(conn, run_id=10, user_id=1, filled_order_id=None, so_numbers=["102870001"])
    matchdb._insert_so_index_for_run(conn, run_id=20, user_id=2, filled_order_id=None, so_numbers=["102870001"])

    mine = matchdb.find_so_number_conflicts(conn, ["102870001"], user_id=2)
    assert [c["run_id"] for c in mine] == [20]
    teammate = matchdb.find_so_number_conflicts(conn, ["102870001"], user_id=3)
    assert [c["run_id"] for c in teammate] == [10]


def test_old_global_index_moves_to_each_owners_company():
    conn = _conn()
    conn.execute(
        "CREATE TABLE fo_so_match_so_index (so_number TEXT NOT NULL PRIMARY KEY, run_id INTEGER NOT NULL, "
        "user_id INTEGER, filled_order_id INTEGER, created_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO fo_so_match_so_index VALUES ('111', 10, 1, NULL, 'x')")
    conn.execute("INSERT INTO fo_so_match_so_index VALUES ('222', 20, 2, NULL, 'x')")
    matchdb.ensure_schema(conn)

    rows = conn.execute(
        "SELECT workspace_id, so_number, run_id FROM fo_so_match_so_index ORDER BY so_number"
    ).fetchall()
    assert rows == [("co_a", "111", 10), ("co_b", "222", 20)]
    assert matchdb.find_so_number_conflicts(conn, ["111"], user_id=2) == []
