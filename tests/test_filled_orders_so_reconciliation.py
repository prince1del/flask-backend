"""Filled Orders (Article Master module) → SO reconciliation bridge tests."""

import sqlite3

import filled_orders_db as fodb
import filled_orders_reconciliation as forecon
from app.routes.data import extract_order_sheet_item_key, make_order_sheet_item_key
from centralized_db_system.db import CentralizedDB
from order_item_keys import size_code_only_item_key


def _fo_conn(db_path):
    conn = sqlite3.connect(db_path)
    fodb.ensure_schema(conn)
    return conn


def test_size_code_only_normalizes_article_master_and_so_keys():
    assert size_code_only_item_key("ASTER|100|DB BS") == "ASTER|100|DB"
    assert size_code_only_item_key("ASTER|100|DB") == "ASTER|100|DB"
    so_key = extract_order_sheet_item_key("ASTER 1+2 DB SET 224X244 7985BLU 100TC")
    filled_key = make_order_sheet_item_key("Aster", 100, "DB BS")
    assert size_code_only_item_key(so_key) == size_code_only_item_key(filled_key) == "ASTER|100|DB"


def test_filled_order_items_populate_ordered_qty_on_tracking(tmp_path):
    db_path = str(tmp_path / "fo_so_bridge.sqlite3")
    db = CentralizedDB(db_path)
    dist_id = db.add_master_distributor(name="Bernina International", workspace_id="ws-1")

    conn = _fo_conn(db_path)
    fo_id = fodb.create_filled_order(
        conn, 1, dist_id, "Bernina", "Bed", "SS-26",
        source_filename="kag.xlsx", total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(conn, fo_id, {
        "article_id": None,
        "item_key": "ASTER|100|DB BS",
        "brand": "Aster",
        "size": "DB BS",
        "product_type": "Sheet Sets",
        "raw_qty_value": 864,
        "detected_unit": "pieces",
        "final_piece_qty": 864,
        "bale_size_used": 18,
        "is_clean_bale_multiple": True,
        "matched": True,
        "mrp": 1299,
        "ptr": 580,
        "ex_mill_price": 500,
    })
    conn.close()

    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-ASTER-001", distributor_id=dist_id, workspace_id="ws-1",
    )

    conn = sqlite3.connect(db_path)
    fodb.ensure_schema(conn)
    results = forecon.apply_filled_order_ordered_items(
        db, tracking_id=tracking_id, filled_order_id=fo_id, workspace_id="ws-1", conn=conn,
    )
    fodb.link_filled_order_to_tracking(conn, fo_id, tracking_id)
    conn.close()

    assert len(results) == 1
    assert results[0]["ordered_qty"] == 864
    assert results[0]["item_key"] == "ASTER|100|DB"

    so_item = db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name="ASTER 1+2 DB SET 224X244 7985BLU 100TC",
        source="so",
        qty=1188,
        value=689040,
        workspace_id="ws-1",
        item_key=extract_order_sheet_item_key("ASTER 1+2 DB SET 224X244 7985BLU 100TC"),
    )
    assert so_item["ordered_qty"] == 864
    assert so_item["so_qty"] == 1188
    assert so_item["has_discrepancy"] == 1


def test_filled_order_so_link_table_roundtrip(tmp_path):
    db_path = str(tmp_path / "fo_link.sqlite3")
    conn = _fo_conn(db_path)
    fo_id = fodb.create_filled_order(conn, 1, 99, "Test", "Bed", "SS-26")
    fodb.link_filled_order_to_tracking(conn, fo_id, 42)
    assert fodb.get_filled_order_id_for_tracking(conn, 42) == fo_id
    links = fodb.list_tracking_links_for_filled_order(conn, fo_id)
    assert links[0]["tracking_id"] == 42
    conn.close()
