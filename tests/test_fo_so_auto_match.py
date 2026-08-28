"""Tests for automatic matching of incoming Sales Orders to Saved Filled Orders."""

import sqlite3
import tempfile
from pathlib import Path

import filled_orders_db as fodb
from app.services import fo_so_auto_match as auto_match
from app.services import fo_so_match_db as matchdb


def _setup_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    conn = sqlite3.connect(path)
    fodb.ensure_schema(conn)
    matchdb.ensure_schema(conn)
    return conn, path


def test_infer_so_category_and_season():
    # Bath SO
    bath_pack = {
        "line_detail": [
            {
                "product_name": "SANTINO PRE DYED 2PC",
                "product_detail": "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26",
                "material_code": "MT12345",
            }
        ],
        "meta": {"order_date": "18.08.2026"},
    }
    cats, season = auto_match.infer_so_category_and_season(bath_pack)
    assert "Bath" in cats
    assert season == "AW26"

    # Bed SO
    bed_pack = {
        "line_detail": [
            {
                "product_name": "ASTER 1+2 DB SET",
                "product_detail": "ASTER 1+2 DB SET 229X274CM AW26",
                "material_code": "MB99999",
            }
        ],
        "meta": {"order_date": "15.08.2026"},
    }
    cats, season = auto_match.infer_so_category_and_season(bed_pack)
    assert "Bed" in cats
    assert season == "AW26"


def test_find_matching_filled_order():
    conn, db_path = _setup_db()
    try:
        user_id = 1
        dist_id = 101

        # Create a Bath FO
        fo_id = fodb.create_filled_order(
            conn=conn,
            user_id=user_id,
            distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor",
            category="Bath",
            season="AW26",
            source_filename="balaji_bath_fo.xlsx",
        )
        fodb.insert_filled_order_item(
            conn,
            fo_id,
            {
                "item_key": "santino_ladies_towel",
                "brand": "Santino",
                "size": "Ladies Towel",
                "product_type": "Towel",
                "raw_qty_value": 100,
                "detected_unit": "pcs",
                "final_piece_qty": 100,
                "is_clean_bale_multiple": True,
                "matched": True,
                "mrp": 500,
                "ptr": 350,
                "ex_mill_price": 300,
            },
        )

        matched = auto_match.find_matching_filled_order(
            conn,
            user_id=user_id,
            distributor_id=dist_id,
            category_candidates=["Bath", "Towel"],
            season="AW26",
        )
        assert matched is not None
        assert matched["id"] == fo_id
        assert matched["category"] == "Bath"
    finally:
        conn.close()


def test_auto_attach_so_to_filled_order_creates_and_updates_run():
    conn, db_path = _setup_db()
    try:
        user_id = 1
        dist_id = 101

        fo_id = fodb.create_filled_order(
            conn=conn,
            user_id=user_id,
            distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor",
            category="Bath",
            season="AW26",
            source_filename="balaji_bath_fo.xlsx",
        )
        fodb.insert_filled_order_item(
            conn,
            fo_id,
            {
                "item_key": "santino_ladies_towel",
                "brand": "Santino",
                "size": "Ladies Towel",
                "product_type": "Towel",
                "raw_qty_value": 100,
                "detected_unit": "pcs",
                "final_piece_qty": 100,
                "is_clean_bale_multiple": True,
                "matched": True,
                "mrp": 500,
                "ptr": 350,
                "ex_mill_price": 300,
            },
        )

        pack1 = {
            "line_detail": [
                {
                    "so_number": "102876568",
                    "product_name": "SANTINO PRE DYED 2PC",
                    "product_detail": "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26",
                    "material_code": "MT12345",
                    "qty": 50,
                    "net_amount": 17500,
                }
            ],
            "meta": {"order_date": "18.08.2026", "source_filename": "so_1.pdf"},
        }

        # 1. First auto attach creates the match run
        res1 = auto_match.auto_attach_so_to_filled_order(
            conn=conn,
            user_id=user_id,
            distributor_id=dist_id,
            filename="so_1.pdf",
            pre_analyzed_pack=pack1,
        )
        assert res1 is not None
        assert res1["status"] == "created"
        run_id = res1["run_id"]

        run1 = matchdb.get_match_run(conn, run_id, user_id=user_id)
        assert run1 is not None
        assert run1["filled_order_id"] == fo_id
        so_nums1 = matchdb.so_numbers_for_run(conn, run_id)
        assert so_nums1 == ["102876568"]

        # 2. Second auto attach (additional SO from email) merges seamlessly into the same run!
        pack2 = {
            "line_detail": [
                {
                    "so_number": "102876598",
                    "product_name": "SANTINO PRE DYED 2PC",
                    "product_detail": "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26",
                    "material_code": "MT12345",
                    "qty": 50,
                    "net_amount": 17500,
                }
            ],
            "meta": {"order_date": "18.08.2026", "source_filename": "so_2.pdf"},
        }

        res2 = auto_match.auto_attach_so_to_filled_order(
            conn=conn,
            user_id=user_id,
            distributor_id=dist_id,
            filename="so_2.pdf",
            pre_analyzed_pack=pack2,
        )
        assert res2 is not None
        assert res2["status"] == "updated"
        assert res2["run_id"] == run_id

        run2 = matchdb.get_match_run(conn, run_id, user_id=user_id)
        assert run2 is not None
        so_nums2 = matchdb.so_numbers_for_run(conn, run_id)
        assert sorted(so_nums2) == ["102876568", "102876598"]
    finally:
        conn.close()


def _create_order_lifecycle_tracking_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_lifecycle_tracking (
            tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref_no TEXT NOT NULL,
            distributor_id INTEGER NOT NULL,
            sales_order_file_reference TEXT,
            sales_order_parsed TEXT,
            commercial_invoice_file_reference TEXT,
            commercial_invoice_parsed TEXT,
            transit_status TEXT NOT NULL DEFAULT 'ORDERED',
            created_at TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    conn.commit()


def test_list_candidate_sales_orders_for_filled_order():
    """A SO already saved to order_lifecycle_tracking (e.g. mail-synced,
    genuinely a duplicate on re-scan) but never attached to any FO↔SO match
    run must still surface as a retry candidate for its distributor's FO."""
    conn, db_path = _setup_db()
    try:
        user_id, dist_id = 1, 101
        _create_order_lifecycle_tracking_table(conn)

        fo_id = fodb.create_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor", category="Bath", season="AW26",
        )

        cur = conn.execute(
            "INSERT INTO order_lifecycle_tracking "
            "(order_ref_no, distributor_id, sales_order_file_reference, created_at, workspace_id) "
            "VALUES (?, ?, ?, datetime('now'), 'default')",
            ("102876568", dist_id, "/uploads/SO/102876568.pdf"),
        )
        conn.commit()
        tracking_id = cur.lastrowid

        # A row with no SO file at all must not show up as a candidate.
        conn.execute(
            "INSERT INTO order_lifecycle_tracking "
            "(order_ref_no, distributor_id, sales_order_file_reference, created_at, workspace_id) "
            "VALUES (?, ?, NULL, datetime('now'), 'default')",
            ("102876599", dist_id),
        )
        conn.commit()

        candidates = fodb.list_candidate_sales_orders_for_filled_order(conn, fo_id, "default")
        assert [c["tracking_id"] for c in candidates] == [tracking_id]
        assert candidates[0]["sales_order_file_reference"] == "/uploads/SO/102876568.pdf"

        # Once linked to this FO, it must drop out of the candidate list.
        fodb.link_filled_order_to_tracking(conn, fo_id, tracking_id)
        candidates_after = fodb.list_candidate_sales_orders_for_filled_order(conn, fo_id, "default")
        assert candidates_after == []
    finally:
        conn.close()


def test_auto_sync_all_unmatched_sos_does_not_raise_on_missing_helper():
    """Regression test: auto_sync_all_unmatched_sos_for_user() called
    fodb.list_candidate_sales_orders_for_filled_order(), a function that did
    not exist — every call silently AttributeError'd inside the caller's
    bare except Exception: pass (order_match_list in app/routes/data.py),
    so the FO↔SO auto-match "self-heal" on every Order Desk load never ran
    for anyone. Must complete cleanly and find the untracked SO."""
    conn, db_path = _setup_db()
    try:
        user_id, dist_id = 1, 101
        _create_order_lifecycle_tracking_table(conn)

        fo_id = fodb.create_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor", category="Bath", season="AW26",
        )
        fodb.insert_filled_order_item(
            conn, fo_id,
            {
                "item_key": "santino_ladies_towel", "brand": "Santino", "size": "Ladies Towel",
                "product_type": "Towel", "raw_qty_value": 100, "detected_unit": "pcs",
                "final_piece_qty": 100, "is_clean_bale_multiple": True, "matched": True,
                "mrp": 500, "ptr": 350, "ex_mill_price": 300,
            },
        )

        # Points at a real (non-PDF) file so the candidate is at least found —
        # parsing it will fail, which auto_attach_so_to_filled_order already
        # handles internally; what this test guards is the AttributeError.
        so_path = Path(db_path).with_suffix(".so.txt")
        so_path.write_text("not a real SO pdf")
        conn.execute(
            "INSERT INTO order_lifecycle_tracking "
            "(order_ref_no, distributor_id, sales_order_file_reference, created_at, workspace_id) "
            "VALUES (?, ?, ?, datetime('now'), 'default')",
            ("102876568", dist_id, str(so_path)),
        )
        conn.commit()

        matched_count = auto_match.auto_sync_all_unmatched_sos_for_user(
            conn, user_id=user_id, workspace_id="default"
        )
        assert isinstance(matched_count, int)
    finally:
        conn.close()


def test_auto_sync_retries_partially_matched_fo_for_new_candidates(monkeypatch):
    """Regression test for the real "Balaji Homedecor" report: a Filled
    Order that already has SOME SOs matched (the normal way, e.g. 24 of
    them) must still pick up an additional SO that arrived later via
    mail-sync and never got attached. The old code's
    `if existing_lines: continue` skipped the whole FO the moment it had
    ANY matched SO, so new candidates were never even looked at, and a
    `break` after the first successful attach meant only one candidate
    per run got tried even for a brand-new FO."""
    conn, db_path = _setup_db()
    try:
        user_id, dist_id = 1, 101
        _create_order_lifecycle_tracking_table(conn)

        fo_id = fodb.create_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor", category="Bath", season="AW26",
        )
        fodb.insert_filled_order_item(
            conn, fo_id,
            {
                "item_key": "santino_ladies_towel", "brand": "Santino", "size": "Ladies Towel",
                "product_type": "Towel", "raw_qty_value": 100, "detected_unit": "pcs",
                "final_piece_qty": 100, "is_clean_bale_multiple": True, "matched": True,
                "mrp": 500, "ptr": 350, "ex_mill_price": 300,
            },
        )

        # This FO already has one SO matched — mirrors "24 FO<->SO matched"
        # already showing before the extra mail-synced SOs ever arrived.
        pack1 = {
            "line_detail": [{
                "so_number": "102876568",
                "product_name": "SANTINO PRE DYED 2PC",
                "product_detail": "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26",
                "material_code": "MT12345",
                "qty": 50,
                "net_amount": 17500,
            }],
            "meta": {"order_date": "18.08.2026", "source_filename": "so_1.pdf"},
        }
        res1 = auto_match.auto_attach_so_to_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            filename="so_1.pdf", pre_analyzed_pack=pack1,
        )
        assert res1 is not None and res1["status"] == "created"

        # A second, distinct SO already tracked but never attached to the FO.
        so_path = Path(db_path).with_suffix(".so2.txt")
        so_path.write_text("dummy")
        cur = conn.execute(
            "INSERT INTO order_lifecycle_tracking "
            "(order_ref_no, distributor_id, sales_order_file_reference, created_at, workspace_id) "
            "VALUES (?, ?, ?, datetime('now'), 'default')",
            ("102876598", dist_id, str(so_path)),
        )
        conn.commit()
        new_tracking_id = cur.lastrowid

        # Real PDF parsing isn't the point of this test — only that the
        # candidate for the already-matched FO gets looked at at all.
        calls = []

        def fake_attach(**kwargs):
            calls.append(kwargs)
            return {"status": "updated", "run_id": res1["run_id"]}

        monkeypatch.setattr(auto_match, "auto_attach_so_to_filled_order", fake_attach)

        matched_count = auto_match.auto_sync_all_unmatched_sos_for_user(
            conn, user_id=user_id, workspace_id="default"
        )
        assert matched_count == 1
        assert len(calls) == 1
        assert calls[0]["tracking_id"] == new_tracking_id
    finally:
        conn.close()
