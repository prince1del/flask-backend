"""
Quantity-column detection + bales/pieces normalization unit tests.

Synthetic fixtures mirror the real-file quirks the feature spec was
validated against (standard column, shifted/unlabeled column, multiple
candidates with a derived-sum relationship) since the actual distributor
files aren't checked into the repo.
"""

import sqlite3
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

import article_master_db as amdb
import article_master_parser as amparser
import filled_orders_parser as foparser


def _write_workbook(tmp_path, header, rows, filename="test.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / filename
    wb.save(path)
    return path


def _load_valid_rows(path):
    raw_df = pd.read_excel(path, sheet_name=0, header=None)
    header_idx = amparser.detect_header_row(raw_df)
    header_row = raw_df.iloc[header_idx].tolist()
    col_mapping = amparser.map_columns_to_core(header_row)
    data_rows = raw_df.iloc[header_idx + 1:]
    valid_rows = [
        row for _, row in data_rows.iterrows()
        if amparser.is_data_row(row.tolist(), col_mapping)
    ]
    return header_row, col_mapping, valid_rows


def test_standard_column_all_pieces(tmp_path):
    """kag.xlsx equivalent: standard 'Qnty' column, values already in pieces."""
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty"]
    rows = [
        ["ASTER", "DB BS", "Bedsheet SS-26", 999, 450, 400, 18, 108],
        ["ASTER", "KG BS", "Bedsheet SS-26", 1200, 500, 420, 12, 60],
    ]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)

    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bed", valid_rows)
    assert detection["status"] == "ok"
    assert detection["column_label"] == "Qnty"

    items = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])
    assert len(items) == 2
    for item in items:
        unit, final_qty = foparser.normalize_quantity(
            item["raw_qty_value"], item["core_fields"]["bale_pack_size"],
        )
        assert unit == "pieces"
        assert final_qty == item["raw_qty_value"]


def test_bath_qty_in_bales_large_bale_count(tmp_path):
    """Towel booking sheet: 30 bales × 24 pack must be 720 pcs, not 30."""
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill Per Pcs", "Bale Pack Sizes", "Qty in Bales"]
    rows = [["Flora", "R4", "Terry Towel", 1425, 954.75, 777.17, 24, 30]]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)

    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bath", valid_rows)
    items = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])
    unit, final = foparser.normalize_quantity(
        items[0]["raw_qty_value"],
        items[0]["core_fields"]["bale_pack_size"],
        qty_column_label=detection["column_label"],
        category="Bath",
    )
    assert unit == "bales"
    assert final == 720


def test_is_bale_count_column():
    assert foparser.is_bale_count_quantity_column("Qty in Bales", "Bath") is True
    assert foparser.is_bale_count_quantity_column("Qnty", "Bed") is False
    assert foparser.is_bale_count_quantity_column("Additional Order Qty", "Bed") is False


def test_standard_column_all_bales(tmp_path):
    """KAG_AGRA.xlsx equivalent: standard 'Qty in Bales' column, all bale counts."""
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill Per Pcs", "Bale Pack Sizes", "Qty in Bales"]
    rows = [
        ["TerryCo", "40x60", "Towel", 300, 150, 120, 96, 1],
        ["TerryCo", "70x140", "Towel", 500, 250, 200, 36, 3],
    ]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)

    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bath", valid_rows)
    assert detection["status"] == "ok"
    assert detection["column_label"].strip().lower() == "qty in bales"

    items = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])
    unit0, final0 = foparser.normalize_quantity(
        items[0]["raw_qty_value"],
        items[0]["core_fields"]["bale_pack_size"],
        qty_column_label=detection["column_label"],
        category="Bath",
    )
    assert (unit0, final0) == ("bales", 96)
    unit1, final1 = foparser.normalize_quantity(
        items[1]["raw_qty_value"],
        items[1]["core_fields"]["bale_pack_size"],
        qty_column_label=detection["column_label"],
        category="Bath",
    )
    assert (unit1, final1) == ("bales", 108)


def test_shifted_unlabeled_column(tmp_path):
    """DCA_Order.xlsx equivalent: standard 'Qnty' column is 100% empty, real
    quantities live in the next (unlabeled) column for some rows."""
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty", None]
    rows = [
        ["A1", "S1", "Bedsheet SS-26", 100, 50, 40, 10, None, 50],
        ["A2", "S2", "Bedsheet SS-26", 200, 90, 70, 12, None, None],
        ["A3", "S3", "Bedsheet SS-26", 150, 70, 55, 15, None, 45],
    ]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)

    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bed", valid_rows)
    assert detection["status"] == "ok"
    assert "unlabeled" in detection["column_label"].lower()

    items = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])
    assert len(items) == 2  # blank-qty rows are skipped entirely
    assert {i["raw_qty_value"] for i in items} == {50, 45}


def test_multiple_candidates_needs_confirmation_with_verified_sum(tmp_path):
    """BND.xlsx equivalent: several populated candidate columns, no single
    standard alias present. Additional Order Qty = Qty + Add for every row —
    verified mathematically and surfaced as a hint, not auto-picked."""
    header = [
        "Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size",
        "No of Bales", "Qty", "Add", "Additional Order Qty",
    ]
    rows = [
        ["Florentine", "King", "Bedsheet SS-26", 1000, 500, 400, 12, 5, 60, 10, 70],
        ["Florentine", "Queen", "Bedsheet SS-26", 900, 450, 360, 12, 8, 96, 0, 96],
        ["Marigold", "King", "Bedsheet SS-26", 1100, 550, 440, 10, 3, 30, 5, 35],
        ["Marigold", "Queen", "Bedsheet SS-26", 950, 480, 380, 10, 2, 20, 2, 22],
    ]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)

    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bed", valid_rows)
    assert detection["status"] == "ok"
    assert detection["column_label"] == "Additional Order Qty"
    assert "auto_selected_reason" in detection

    items = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])
    assert [it["raw_qty_value"] for it in items] == [70, 96, 35, 22]

    flags = []
    for it in items:
        unit, final_qty = foparser.normalize_quantity(it["raw_qty_value"], it["core_fields"]["bale_pack_size"])
        assert unit == "pieces"  # every raw value here is >= its bale size
        flags.append(foparser.is_clean_bale_multiple(final_qty, it["core_fields"]["bale_pack_size"]))
    # 70 % 12 != 0 and 35 % 10 != 0 / 22 % 10 != 0 -> flagged; 96 % 12 == 0 -> clean
    assert flags == [False, True, False, False]


def _make_am_conn(tmp_path):
    db_path = tmp_path / "am_match.sqlite3"
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def test_match_and_normalize_matched_uses_article_master_values(tmp_path):
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty"]
    # File's own price/bale columns are intentionally wrong/stale — matched
    # rows must ignore them and use Article Master's current values instead.
    rows = [["ASTER", "DB BS", "Bedsheet SS-26", 111, 55, 44, 6, 30]]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)
    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bed", valid_rows)
    parsed_rows = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])

    conn = _make_am_conn(tmp_path)
    amdb.create_category(conn, 1, "Bed", ["brand", "size"], is_confirmed=True)
    amdb.upsert_article(conn, 1, {
        "category": "Bed", "product_type": "Bedsheet", "brand": "ASTER", "size": "DB BS",
        "mrp": 999, "ptr": 450, "ex_mill_price": 400, "bale_pack_size": 10,
        "item_key": "ASTER|DB BS", "extra_attributes": {},
    })

    result = foparser.match_and_normalize(conn, amdb, 1, parsed_rows[0], ["brand", "size"])
    conn.close()

    assert result["matched"] is True
    assert result["mrp"] == 999
    assert result["bale_size_used"] == 10
    assert result["detected_unit"] == "pieces"
    assert result["final_piece_qty"] == 30
    assert result["is_clean_bale_multiple"] is True  # 30 % 10 == 0


def test_match_and_normalize_unmatched_falls_back_to_file_values(tmp_path):
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty"]
    rows = [["NEWBRAND", "XL", "Bedsheet SS-26", 111, 55, 44, 6, 30]]
    path = _write_workbook(tmp_path, header, rows)
    header_row, col_mapping, valid_rows = _load_valid_rows(path)
    detection = foparser.detect_quantity_column(header_row, col_mapping, "Bed", valid_rows)
    parsed_rows = foparser.build_filled_order_rows(valid_rows, header_row, col_mapping, detection["column_index"])

    conn = _make_am_conn(tmp_path)
    result = foparser.match_and_normalize(conn, amdb, 1, parsed_rows[0], ["brand", "size"])
    conn.close()

    assert result["matched"] is False
    assert result["article_id"] is None
    assert result["mrp"] == 111
    assert result["bale_size_used"] == 6


def test_annotate_item_issues_unmatched_with_hint(tmp_path):
    conn = _make_am_conn(tmp_path)
    amdb.create_category(conn, 1, "Bed", ["brand", "size"], is_confirmed=True)
    amdb.upsert_article(conn, 1, {
        "category": "Bed", "product_type": "Sheet Sets", "brand": "Cardinal", "size": "SB BS",
        "mrp": 500, "ptr": 300, "ex_mill_price": 250, "bale_pack_size": 10,
        "item_key": "Cardinal|SB BS", "extra_attributes": {},
    })
    item = {
        "matched": False,
        "is_clean_bale_multiple": True,
        "brand": "Blumen",
        "size": "SB BS",
        "product_type": "Sheet Sets",
        "item_key": "Blumen|SB BS",
        "final_piece_qty": 24,
        "bale_size_used": 10,
    }
    foparser.annotate_item_issues(
        conn, amdb, 1, item, ["brand", "size"], category="Bed",
        core_fields={"brand": "Blumen", "size": "SB BS", "product_type": "Sheet Sets"},
        extra_attributes={},
    )
    conn.close()

    assert item["has_issue"] is True
    assert "Not found in Article Master" in item["issue_summary"]
    assert item["field_comparisons"]
    assert item["recommended_action"]["code"] in {"add_to_article_master", "edit_file_or_master"}
    brand_cmp = next(c for c in item["field_comparisons"] if c["field"] == "brand")
    assert brand_cmp["status"] == "mismatch"


def test_annotate_item_issues_flagged_qty(tmp_path):
    conn = _make_am_conn(tmp_path)
    item = {
        "matched": True,
        "is_clean_bale_multiple": False,
        "brand": "Cardinal",
        "size": "SB BS",
        "product_type": "Sheet Sets",
        "final_piece_qty": 96,
        "bale_size_used": 18,
    }
    foparser.annotate_item_issues(conn, amdb, 1, item, ["brand", "size"], category="Bed")
    conn.close()

    assert "not a clean multiple" in (item["issue_summary"] or "")
    assert "96" in (item["issue_summary"] or "")


def test_blumen_typo_matches_bluemen_in_article_master(tmp_path):
    """Distributor file 'Blumen' must match Article Master 'Bluemen'."""
    conn = _make_am_conn(tmp_path)
    amdb.create_category(conn, 1, "Bed", ["brand", "TC", "size"], is_confirmed=True)
    amdb.upsert_article(conn, 1, {
        "category": "Bed", "product_type": "Sheet Sets", "brand": "Bluemen", "size": "DB BS",
        "mrp": 799, "ptr": 450, "ex_mill_price": 400, "bale_pack_size": 5,
        "item_key": "BLUEMEN|104 (ONE IN A DENT)|DB BS",
        "extra_attributes": {"TC": "104 (ONE IN A DENT)"},
    })

    core = {"brand": "Blumen", "size": "DB BS", "product_type": "Sheet Sets"}
    extra = {}
    article = amdb.resolve_article_match(conn, 1, "Bed", core, extra, ["brand", "TC", "size"])
    conn.close()

    assert article is not None
    assert article["brand"] == "Bluemen"
    assert article["size"] == "DB BS"


def test_tc_one_in_a_dent_matches_article_master(tmp_path):
    """File TC='104' must match AM row stored as '104 (ONE IN A DENT)'."""
    conn = _make_am_conn(tmp_path)
    amdb.create_category(conn, 1, "Bed", ["brand", "TC", "size"], is_confirmed=True)
    amdb.upsert_article(conn, 1, {
        "category": "Bed", "product_type": "Bedsheet", "brand": "ASTER", "size": "DB BS",
        "mrp": 999, "ptr": 450, "ex_mill_price": 400, "bale_pack_size": 12,
        "item_key": "ASTER|104 (ONE IN A DENT)|DB BS",
        "extra_attributes": {"TC": "104 (ONE IN A DENT)"},
    })

    core = {"brand": "ASTER", "size": "DB BS", "product_type": "Bedsheet", "bale_pack_size": 12}
    extra = {"TC": "104"}
    key_fields = ["brand", "TC", "size"]
    assert amparser.build_item_key(core, extra, key_fields) == "ASTER|104|DB BS"

    parsed_row = {"core_fields": core, "extra_attributes": extra, "raw_qty_value": 24}
    result = foparser.match_and_normalize(conn, amdb, 1, parsed_row, key_fields, category="Bed")
    conn.close()
    assert result["matched"] is True
    assert result["mrp"] == 999
