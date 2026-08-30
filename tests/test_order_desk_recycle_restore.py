"""Order Desk delete → re-upload restore (match, tracking, FO, files)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.services import fo_so_match_db as matchdb
from app.services import order_desk_archive as oda


def _conn(tmp_path):
    path = tmp_path / "recycle.sqlite3"
    conn = sqlite3.connect(str(path))
    matchdb.ensure_schema(conn)
    oda.ensure_schema(conn)
    _ensure_tracking_tables(conn)
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)
    return conn


def _ensure_tracking_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS order_lifecycle_tracking (
            tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref_no TEXT NOT NULL,
            distributor_id INTEGER NOT NULL,
            order_received_date TEXT,
            order_filled_date TEXT,
            sales_order_generated_date TEXT,
            sales_order_file_reference TEXT,
            sales_order_parsed TEXT,
            payment_status TEXT,
            commercial_invoice_date TEXT,
            commercial_invoice_file_reference TEXT,
            commercial_invoice_parsed TEXT,
            dispatch_date TEXT,
            expected_delivery_date TEXT,
            actual_delivery_date TEXT,
            pod_number TEXT,
            transit_status TEXT NOT NULL DEFAULT 'ORDERED',
            receiving_status TEXT,
            receiving_condition TEXT,
            created_at TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT 'default',
            sales_order_drive_file_id TEXT,
            commercial_invoice_drive_file_id TEXT,
            order_sheet_id INTEGER,
            order_sheet_name TEXT
        );
        CREATE TABLE IF NOT EXISTS order_fulfillment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_lifecycle_id INTEGER NOT NULL,
            item_name TEXT,
            item_key TEXT,
            ordered_qty INTEGER DEFAULT 0,
            fulfilled_qty INTEGER DEFAULT 0,
            so_qty REAL,
            so_value REAL,
            ci_qty REAL,
            ci_value REAL,
            created_at TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS distributor_payment_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id INTEGER,
            amount REAL,
            payment_date TEXT,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_lifecycle_tracking_id INTEGER,
            amount REAL,
            currency TEXT,
            source TEXT,
            created_at TEXT,
            workspace_id TEXT DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS processed_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            document_type TEXT NOT NULL,
            document_number TEXT NOT NULL,
            tracking_id INTEGER,
            processed_at TEXT NOT NULL,
            UNIQUE(workspace_id, document_type, document_number)
        );
        """
    )
    conn.commit()


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


def test_delete_whole_run_archives_each_so(tmp_path):
    conn = _conn(tmp_path)
    pack = {"line_detail": [{"so_number": "SO-1", "qty": 1, "net_amount": 50}]}
    run = matchdb.save_match_run(
        conn, user_id=2, match_payload=_payload(50, "SO-1"), so_pack=pack
    )
    full = matchdb.get_match_run(conn, int(run["id"]), user_id=2)
    oda.archive_match_run(conn, 2, full, restore_scope="run")
    rows = conn.execute(
        "SELECT kind FROM order_desk_archive WHERE user_id = 2 AND restored_at IS NULL"
    ).fetchall()
    kinds = {r[0] for r in rows}
    assert "match_run" in kinds
    assert "match_so" in kinds


def test_tracking_restore_merges_ci_and_payments(tmp_path):
    conn = _conn(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO order_lifecycle_tracking (
            order_ref_no, distributor_id, sales_order_file_reference,
            transit_status, created_at, workspace_id
        ) VALUES ('SO-777', 5, '/new/so.pdf', 'ORDERED', ?, 'default')
        """,
        (now,),
    )
    tracking_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()

    archived = {
        "tracking_id": 99,
        "order_ref_no": "SO-777",
        "distributor_id": 5,
        "commercial_invoice_file_reference": "/old/ci.pdf",
        "commercial_invoice_parsed": json.dumps({"header": {"invoice_no": "CI-1"}}),
        "payment_status": "PARTIAL",
        "transit_status": "DISPATCHED",
        "created_at": now,
        "workspace_id": "default",
    }
    oda.archive_tracking_bundle(
        conn,
        3,
        archived,
        fulfillment_items=[
            {
                "item_name": "Towel",
                "item_key": "t1",
                "ordered_qty": 10,
                "fulfilled_qty": 0,
                "so_qty": 10,
                "created_at": now,
                "workspace_id": "default",
            }
        ],
        achievements=[],
        payment_entries=[{"amount": 500.0, "payment_date": "2026-08-01", "note": "RTGS"}],
        processed_documents=[
            {
                "workspace_id": "default",
                "document_type": "SO",
                "document_number": "SO-777",
                "tracking_id": 99,
                "processed_at": now,
            }
        ],
    )
    conn.commit()

    ok = oda.restore_tracking_after_upload(
        conn, 3, "SO-777", tracking_id, "default", upload_kind="so"
    )
    assert ok
    row = conn.execute(
        "SELECT commercial_invoice_file_reference, payment_status FROM order_lifecycle_tracking WHERE tracking_id = ?",
        (tracking_id,),
    ).fetchone()
    assert row[0] == "/old/ci.pdf"
    assert row[1] == "PARTIAL"
    pay = conn.execute(
        "SELECT amount FROM distributor_payment_entries WHERE tracking_id = ?",
        (tracking_id,),
    ).fetchone()
    assert float(pay[0]) == 500.0
    items = conn.execute(
        "SELECT item_name FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
        (tracking_id,),
    ).fetchall()
    assert len(items) == 1


def test_filled_order_items_restore_on_reupload(tmp_path):
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)
    order_id = fodb.create_filled_order(
        conn, 4, 1, "Bernina", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        order_id,
        {
            "item_key": "525B|DB",
            "brand": "525B",
            "size": "DB",
            "raw_qty_value": 10,
            "detected_unit": "pieces",
            "final_piece_qty": 10,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    order = fodb.get_filled_order(conn, 4, order_id)
    items = fodb.get_filled_order_items(conn, order_id)
    oda.archive_filled_order(conn, 4, order, items, restore_scope="run")
    fodb.delete_filled_order(conn, 4, order_id)

    new_id = fodb.create_filled_order(
        conn, 4, 1, "Bernina", "Bed", "AW26",
    )
    key = oda.fo_entity_key("Bernina", "Bed", "AW26")
    n = oda.restore_filled_order_after_upload(conn, 4, new_id, key)
    assert n == 1
    rows = conn.execute(
        "SELECT item_key FROM filled_order_items WHERE filled_order_id = ?",
        (new_id,),
    ).fetchall()
    assert rows[0][0] == "525B|DB"


def test_filled_order_delete_keeps_so_unmatched(tmp_path):
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fo_id = fodb.create_filled_order(
        conn, 7, 1, "Balaji Homedecor", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    pack = {
        "line_detail": [
            {"so_number": "102876310", "product_name": "525B", "qty": 72, "net_amount": 5000}
        ]
    }
    payload = _payload(5000, "102876310")
    payload["fo"]["id"] = fo_id
    payload["fo"]["distributor_name_raw"] = "Balaji Homedecor"
    payload["fo"]["category"] = "Bed"
    run = matchdb.save_match_run(
        conn,
        user_id=7,
        match_payload=payload,
        so_pack=pack,
    )
    run_id = int(run["id"])
    assert matchdb.get_match_run(conn, run_id, user_id=7) is not None

    fodb.delete_filled_order(conn, 7, fo_id)

    assert fodb.get_filled_order(conn, 7, fo_id) is None
    detail = matchdb.get_match_run(conn, run_id, user_id=7)
    assert detail is not None
    assert detail.get("filled_order_id") is None
    assert "102876310" in matchdb.extract_so_numbers_from_run_row(detail)
    assert detail.get("fo_qty") is None
    listed = matchdb.list_match_runs(conn, user_id=7)
    assert any(int(r["id"]) == run_id for r in listed)


def test_fo_reupload_restores_archived_match(tmp_path):
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fo_id = fodb.create_filled_order(
        conn, 8, 1, "Balaji Homedecor", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        fo_id,
        {
            "item_key": "525B|DB",
            "brand": "525B",
            "size": "DB",
            "raw_qty_value": 72,
            "detected_unit": "pieces",
            "final_piece_qty": 72,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    pack = {
        "line_detail": [
            {
                "so_number": "102876310",
                "product_name": "525B",
                "qty": 72,
                "net_amount": 5000,
            }
        ]
    }
    payload = _payload(5000, "102876310")
    payload["fo"]["id"] = fo_id
    payload["fo"]["distributor_name_raw"] = "Balaji Homedecor"
    payload["fo"]["category"] = "Bed"
    run = matchdb.save_match_run(
        conn, user_id=8, match_payload=payload, so_pack=pack,
        so_line_detail=pack["line_detail"],
    )
    assert matchdb.get_match_run(conn, int(run["id"]), user_id=8) is not None

    fodb.delete_filled_order(conn, 8, fo_id)
    detached = matchdb.get_match_run(conn, int(run["id"]), user_id=8)
    assert detached is not None
    assert detached.get("filled_order_id") is None
    assert detached.get("match_count") == 0
    for row in detached.get("rows") or []:
        assert row.get("fo_qty") is None
        assert row.get("status") == "UNMATCHED"

    new_fo_id = fodb.create_filled_order(
        conn, 8, 1, "Balaji Homedecor", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        new_fo_id,
        {
            "item_key": "525B|DB",
            "brand": "525B",
            "size": "DB",
            "raw_qty_value": 72,
            "detected_unit": "pieces",
            "final_piece_qty": 72,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    entity_key = oda.fo_entity_key("Balaji Homedecor", "Bed", "AW26")
    oda.repoint_filled_order_archives(conn, 8, entity_key, new_fo_id)
    restored = oda.restore_match_after_fo_upload(conn, 8, new_fo_id, entity_key)
    assert restored == 1
    runs = conn.execute(
        "SELECT id FROM fo_so_match_runs WHERE user_id = 8 AND filled_order_id = ?",
        (new_fo_id,),
    ).fetchall()
    assert len(runs) == 1
    detail = matchdb.get_match_run(conn, int(runs[0][0]), user_id=8)
    assert detail is not None
    assert "102876310" in matchdb.extract_so_numbers_from_run_row(detail)
    assert detail.get("filled_order_id") == new_fo_id
    assert (detail.get("fo_qty") or 0) > 0
    assert all(row.get("status") != "UNMATCHED" for row in (detail.get("rows") or []))
    assert all(row.get("fo_qty") is not None for row in (detail.get("rows") or []))


def test_fo_reupload_rematches_by_distributor_id_when_name_differs(tmp_path):
    """SO buyer label on run may differ from FO upload distributor_name_raw."""
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fo_id = fodb.create_filled_order(
        conn, 9, 1, "Balaji Homedecor Pvt Ltd", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        fo_id,
        {
            "item_key": "525B|DB",
            "brand": "525B",
            "size": "DB",
            "raw_qty_value": 72,
            "detected_unit": "pieces",
            "final_piece_qty": 72,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    pack = {
        "line_detail": [
            {
                "so_number": "102876310",
                "product_name": "525B DB BS",
                "qty": 72,
                "net_amount": 5000,
            }
        ]
    }
    payload = _payload(5000, "102876310")
    payload["fo"]["id"] = fo_id
    payload["fo"]["distributor_id"] = 1
    payload["fo"]["distributor_name_raw"] = "Balaji Homedecor"
    payload["fo"]["category"] = "Bed"
    run = matchdb.save_match_run(
        conn, user_id=9, match_payload=payload, so_pack=pack,
        so_line_detail=pack["line_detail"],
        so_buyer_label="Balaji Homedecor",
    )
    fodb.delete_filled_order(conn, 9, fo_id)
    new_fo_id = fodb.create_filled_order(
        conn, 9, 1, "Balaji Homedecor Pvt Ltd", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        new_fo_id,
        {
            "item_key": "525B|DB",
            "brand": "525B",
            "size": "DB",
            "raw_qty_value": 72,
            "detected_unit": "pieces",
            "final_piece_qty": 72,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    entity_key = oda.fo_entity_key("Balaji Homedecor Pvt Ltd", "Bed", "AW26")
    restored = oda.restore_match_after_fo_upload(conn, 9, new_fo_id, entity_key)
    assert restored == 1
    detail = matchdb.get_match_run(conn, int(run["id"]), user_id=9)
    assert detail is not None
    assert detail.get("filled_order_id") == new_fo_id
    assert (detail.get("fo_qty") or 0) > 0


def test_auto_relink_when_fo_upload_name_differs(tmp_path):
    """Detached run re-links when distributor_id matches but names differ."""
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fo_id = fodb.create_filled_order(
        conn, 10, 1, "Balaji Homedecor Pvt Ltd", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    pack = {
        "line_detail": [
            {
                "so_number": "102876310",
                "product_name": "525B DB BS",
                "qty": 72,
                "net_amount": 5000,
            }
        ]
    }
    payload = _payload(5000, "102876310")
    payload["fo"]["id"] = fo_id
    payload["fo"]["distributor_id"] = 1
    payload["fo"]["distributor_name_raw"] = "Balaji Homedecor"
    run = matchdb.save_match_run(
        conn, user_id=10, match_payload=payload, so_pack=pack,
        so_line_detail=pack["line_detail"],
        so_buyer_label="Balaji Homedecor",
    )
    fodb.delete_filled_order(conn, 10, fo_id)
    fodb.create_filled_order(
        conn, 10, 1, "Balaji Homedecor Pvt Ltd", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    relinked = matchdb.auto_relink_detached_runs_for_user(conn, 10)
    assert relinked == 1
    detail = matchdb.get_match_run(conn, int(run["id"]), user_id=10)
    assert detail is not None
    assert detail.get("filled_order_id") is not None


def test_full_delete_reupload_then_fo_delete_and_reupload(tmp_path):
    """User flow: delete FO+SO, re-upload both, delete FO, re-upload FO → relink."""
    conn = _conn(tmp_path)
    import filled_orders_db as fodb
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    def save_pair(uid: int, dist_name: str = "Balaji Homedecor") -> tuple[int, dict]:
        fo_id = fodb.create_filled_order(
            conn, uid, 1, dist_name, "Bed", "AW26", total_lines=1, matched_lines=1,
        )
        item = {
            "item_key": "525B|DB BS",
            "brand": "525B",
            "size": "DB BS",
            "raw_qty_value": 360,
            "detected_unit": "pieces",
            "final_piece_qty": 360,
            "matched": True,
            "is_clean_bale_multiple": False,
        }
        fodb.insert_filled_order_item(conn, fo_id, item)
        line_detail = [
            {
                "so_number": "102876310",
                "product_name": "525B DB BS",
                "brand": "525B",
                "size": "DB BS",
                "qty": 360,
                "net_amount": 481409,
            }
        ]
        fo = fodb.get_filled_order(conn, uid, fo_id)
        fo_items = fodb.get_filled_order_items(conn, fo_id)
        pack = {"line_detail": line_detail, "meta": {"source_filename": "balaji.zip"}}
        payload = run_match_saved_fo_vs_so_pack(
            fo_meta={**fo, "id": fo_id},
            fo_items=fo_items,
            so_pack_payload=pack,
        )
        run = matchdb.save_match_run(
            conn,
            user_id=uid,
            match_payload=payload,
            so_pack=pack,
            so_line_detail=line_detail,
            so_buyer_label=dist_name,
        )
        return fo_id, run

    uid = 11
    fo1, run1 = save_pair(uid)
    run_id = int(run1["id"])
    matchdb.delete_match_run(conn, uid, run_id)
    fodb.delete_filled_order(conn, uid, fo1)

    fo2, run2 = save_pair(uid)
    run_id = int(run2["id"])
    fodb.delete_filled_order(conn, uid, fo2)
    detached = matchdb.get_match_run(conn, run_id, user_id=uid)
    assert detached is not None
    assert detached.get("filled_order_id") is None

    fo3 = fodb.create_filled_order(
        conn, uid, 1, "Balaji Homedecor", "Bed", "AW26", total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(
        conn,
        fo3,
        {
            "item_key": "525B|DB BS",
            "brand": "525B",
            "size": "DB BS",
            "raw_qty_value": 360,
            "detected_unit": "pieces",
            "final_piece_qty": 360,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )
    entity_key = oda.fo_entity_key("Balaji Homedecor", "Bed", "AW26")
    restored = oda.restore_match_after_fo_upload(conn, uid, fo3, entity_key)
    assert restored == 1
    linked = matchdb.get_match_run(conn, run_id, user_id=uid)
    assert linked is not None
    assert linked.get("filled_order_id") == fo3
    assert "102876310" in matchdb.extract_so_numbers_from_run_row(linked)


def test_explain_rematch_reports_category_mismatch(tmp_path):
    conn = _conn(tmp_path)
    import filled_orders_db as fodb

    fo_id = fodb.create_filled_order(
        conn, 12, 1, "Balaji Homedecor", "Bed", "AW26", total_lines=1, matched_lines=1,
    )
    pack = {
        "line_detail": [
            {"so_number": "102876310", "product_name": "525B", "qty": 72, "net_amount": 5000}
        ]
    }
    payload = _payload(5000, "102876310")
    payload["fo"]["id"] = fo_id
    payload["fo"]["category"] = "Bed"
    matchdb.save_match_run(
        conn, user_id=12, match_payload=payload, so_pack=pack,
        so_line_detail=pack["line_detail"],
    )
    fodb.delete_filled_order(conn, 12, fo_id)
    bath_fo = fodb.create_filled_order(
        conn, 12, 1, "Balaji Homedecor", "Bath", "AW26", total_lines=1, matched_lines=1,
    )
    ek = oda.fo_entity_key("Balaji Homedecor", "Bath", "AW26")
    diag = matchdb.explain_rematch_for_fo_upload(conn, 12, bath_fo, ek)
    assert diag["detached_run_count"] == 1
    cand = diag["candidate_runs"][0]
    assert cand["would_match"] is False
    assert "category" in (cand["skip_reason"] or "")


def test_archive_schema_migrates_missing_expires_at(tmp_path):
    """Production DBs created before expires_at must migrate on ensure_schema."""
    path = tmp_path / "migrate.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE order_desk_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            restore_scope TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            filled_order_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    oda.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(order_desk_archive)")}
    assert "expires_at" in cols
    assert "restored_at" in cols
    oda.archive_match_run(
        conn,
        1,
        {"id": 9, "filled_order_id": 3, "rows": [], "so_line_detail": []},
        restore_scope="entity",
    )
    row = conn.execute(
        "SELECT expires_at FROM order_desk_archive WHERE user_id = 1"
    ).fetchone()
    assert row is not None
    assert row[0]


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
