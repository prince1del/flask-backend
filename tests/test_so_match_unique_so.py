"""SO Pack match: one Sales Order number may be saved only once."""

from __future__ import annotations

import sqlite3

import pytest

from app.services import fo_so_match_db as matchdb


def _conn(tmp_path):
    path = tmp_path / "match.sqlite3"
    conn = sqlite3.connect(str(path))
    matchdb.ensure_schema(conn)
    return conn


def _payload(so_net: float = 1000.0) -> dict:
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
                    "so_numbers": ["102876303"],
                    "so_breakdown": [
                        {"so_number": "102876303", "qty": 10, "net": so_net, "gst": 0, "total": so_net}
                    ],
                    "so_qty": 10,
                    "so_net_amount": so_net,
                    "fo_exmill_value": so_net,
                }
            ],
        },
    }


def test_second_upload_same_so_is_rejected(tmp_path):
    conn = _conn(tmp_path)
    pack = {
        "line_detail": [
            {"so_number": "102876303", "product_name": "525B", "qty": 10, "net_amount": 1000}
        ]
    }
    run1 = matchdb.save_match_run(
        conn, user_id=1, match_payload=_payload(1000), so_pack=pack
    )
    assert run1["id"]

    with pytest.raises(matchdb.DuplicateSalesOrderError) as exc:
        matchdb.save_match_run(
            conn, user_id=1, match_payload=_payload(2000), so_pack=pack
        )
    assert "102876303" in str(exc.value)
    assert len(matchdb.list_match_runs(conn, user_id=1)) == 1


def test_delete_frees_so_for_reupload(tmp_path):
    conn = _conn(tmp_path)
    pack = {"line_detail": [{"so_number": "SO-9", "qty": 1}]}
    run = matchdb.save_match_run(
        conn, user_id=1, match_payload=_payload(), so_pack=pack
    )
    assert matchdb.delete_match_run(conn, 1, int(run["id"]))
    run2 = matchdb.save_match_run(
        conn, user_id=1, match_payload=_payload(50), so_pack=pack
    )
    assert run2["id"] != run["id"]


def test_cleanup_keeps_latest_filled_order_run(tmp_path):
    conn = _conn(tmp_path)
    # Bypass uniqueness to simulate legacy duplicates, then cleanup.
    for net in (100.0, 200.0, 300.0):
        conn.execute(
            """
            INSERT INTO fo_so_match_runs (
                user_id, filled_order_id, distributor_id, distributor_name,
                category, season, so_net_amount, rows_json, created_at
            ) VALUES (1, 55, 3, 'Bernina', 'Bed', 'AW26', ?, '[]', datetime('now'))
            """,
            (net,),
        )
    conn.commit()
    deleted = matchdb._cleanup_duplicate_runs_by_filled_order(conn)
    assert deleted == 2
    rows = conn.execute(
        "SELECT so_net_amount FROM fo_so_match_runs WHERE filled_order_id = 55"
    ).fetchall()
    assert len(rows) == 1
    assert float(rows[0][0]) == 300.0
