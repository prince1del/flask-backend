"""Category-safe FO suggest + strip wrongly locked SO numbers."""

from __future__ import annotations

import json
import sqlite3

from app.services import fo_so_match_db as matchdb


def test_strip_so_numbers_from_run(tmp_path):
    db = tmp_path / "m.sqlite3"
    conn = sqlite3.connect(str(db))
    matchdb.ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO fo_so_match_runs (
            user_id, filled_order_id, distributor_id, distributor_name,
            category, season, fo_source_filename, so_buyer_label, so_source_filename,
            fo_qty, so_qty, delta_qty, fo_exmill_value, so_net_amount, delta_value,
            match_count, fuzzy_count, mismatch_count, missing_count, extra_count,
            rows_json, so_line_detail_json, created_at
        ) VALUES (1, 22, 5, 'Balaji', 'Bed', 'AW26', 'BALAJI.xlsx', 'BALAJI', 'x.pdf',
                  100, 100, 0, 1000, 1000, 0, 1, 0, 0, 0, 0, ?, ?, '2026-01-01')
        """,
        (
            json.dumps([{"so_numbers": ["102876120", "102876586"]}]),
            json.dumps(
                [
                    {"so_number": "102876120", "qty": 90, "net": 900},
                    {"so_number": "102876586", "qty": 10, "net": 100},
                ]
            ),
        ),
    )
    run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    matchdb._insert_so_index_for_run(
        conn,
        run_id=run_id,
        user_id=1,
        filled_order_id=22,
        so_numbers=["102876120", "102876586"],
    )
    conn.commit()

    result = matchdb.strip_so_numbers_from_run(
        conn, run_id=run_id, user_id=1, so_numbers=["102876586"]
    )
    assert result["stripped_so_numbers"] == ["102876586"]
    conflicts = matchdb.find_so_number_conflicts(conn, ["102876586", "102876120"])
    claimed = {c["so_number"] for c in conflicts}
    assert "102876586" not in claimed
    assert "102876120" in claimed
    run = result["run"]
    nums = {
        str(r.get("so_number"))
        for r in (run.get("so_line_detail") or [])
        if isinstance(r, dict)
    }
    assert nums == {"102876120"}
