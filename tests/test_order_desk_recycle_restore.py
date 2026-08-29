"""Order Desk delete → re-upload restore (match_so + match_run)."""

from __future__ import annotations

import sqlite3

from app.services import fo_so_match_db as matchdb
from app.services import order_desk_archive as oda


def _conn(tmp_path):
    path = tmp_path / "recycle.sqlite3"
    conn = sqlite3.connect(str(path))
    matchdb.ensure_schema(conn)
    oda.ensure_schema(conn)
    return conn


def _payload(so_net: float = 1000.0, so_number: str = "102876303") -> dict:
    return {
        "fo": {
            "id": 11,
            "distributor_id": 3,
            "distributor_name_raw": "Bernina",
            "category": "Bed",
            "season": "AW26",
            "source_filename": "BND.xlsx",
        },
        "match": {
            "totals": {
                "fo_qty": 10,
                "so_qty": 10,
                "delta_qty": 0,
                "fo_exmill_value": so_net,
                "so_net_amount": so_net,
                "delta_value": 0,
            },
            "counts": {"MATCH": 1},
            "rows": [
                {
                    "brand": "525B",
                    "size": "DB BS",
                    "status": "MATCH",
                    "so_numbers": [so_number],
                    "so_breakdown": [
                        {
                            "so_number": so_number,
                            "qty": 10,
                            "net": so_net,
                            "gst": 0,
                            "total": so_net,
                        }
                    ],
                    "so_qty": 10,
                    "fo_qty": 10,
                    "so_net_amount": so_net,
                    "fo_exmill_value": so_net,
                }
            ],
        },
    }


def test_delete_so_archives_and_restores_on_rematch(tmp_path):
    conn = _conn(tmp_path)
    so = "102876303"
    pack = {
        "line_detail": [
            {"so_number": so, "product_name": "525B", "qty": 10, "net_amount": 1000}
        ]
    }
    run = matchdb.save_match_run(
        conn, user_id=1, match_payload=_payload(1000, so), so_pack=pack
    )
    run_id = int(run["id"])
    full = matchdb.get_match_run(conn, run_id, user_id=1)
    oda.archive_match_so(conn, 1, full, so, restore_scope="entity")
    matchdb.delete_match_so_from_run(conn, 1, run_id, so)
    conn.commit()
    assert matchdb.get_match_run(conn, run_id, user_id=1) is None

    run2 = matchdb.save_match_run(
        conn, user_id=1, match_payload=_payload(1000, so), so_pack=pack
    )
    restored = oda.restore_match_archives_after_save(
        conn, 1, int(run2["id"]), 11, [so]
    )
    assert restored == 1
    again = matchdb.get_match_run(conn, int(run2["id"]), user_id=1)
    assert again is not None
    nums = matchdb.extract_so_numbers_from_run_row(again)
    assert so in nums
    assert float(again.get("so_net_amount") or 0) == 1000.0


def test_delete_whole_run_archives(tmp_path):
    conn = _conn(tmp_path)
    pack = {"line_detail": [{"so_number": "SO-1", "qty": 1, "net_amount": 50}]}
    run = matchdb.save_match_run(
        conn, user_id=2, match_payload=_payload(50, "SO-1"), so_pack=pack
    )
    full = matchdb.get_match_run(conn, int(run["id"]), user_id=2)
    oda.archive_match_run(conn, 2, full, restore_scope="run")
    assert matchdb.delete_match_run(conn, 2, int(run["id"]))
    rows = conn.execute(
        "SELECT kind FROM order_desk_archive WHERE user_id = 2 AND restored_at IS NULL"
    ).fetchall()
    assert any(r[0] == "match_run" for r in rows)


def test_purge_expired_drops_old_rows(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        """
        INSERT INTO order_desk_archive (
            user_id, kind, entity_key, restore_scope, payload_json,
            filled_order_id, created_at, expires_at
        ) VALUES (1, 'match_run', 'run:1', 'run', '{}', NULL, '2020-01-01', '2020-02-01')
        """
    )
    conn.commit()
    deleted = oda.purge_expired(conn)
    assert deleted == 1
