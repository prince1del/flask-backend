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
