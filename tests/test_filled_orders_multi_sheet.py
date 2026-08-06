"""Multi-sheet filled order (BND base + additional) teaching tests."""
from pathlib import Path

import pandas as pd
import pytest

import filled_orders_parser as foparser

BND_PATH = Path(r"G:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\BND.xlsx")

pytestmark = pytest.mark.skipif(not BND_PATH.exists(), reason="BND.xlsx not on G: drive")


def _write_two_tab_workbook(tmp_path):
    path = tmp_path / "bnd_two_tabs.xlsx"
    base = pd.DataFrame([
        ["Brand", "Size", "Product", "Bale Size", "MRP", "ExMill Price", "No of Bales", "Qty"],
        ["Aster", "DB BS", "Bedsheet", 18, 1000, 500, 2, 36],
        ["Blumen", "SB BS", "Bedsheet", 24, 800, 400, 1, 24],
    ])
    addl = pd.DataFrame([
        ["Brand", "Size", "Product", "Bale Size", "MRP", "ExMill Price", "Additional quantity"],
        ["Aster", "DB BS", "Bedsheet", 18, 1000, 500, 18],
        ["Epigram", "KS BS", "Bedsheet", 12, 900, 450, 12],
    ])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        base.to_excel(writer, sheet_name="base order", index=False, header=False)
        addl.to_excel(writer, sheet_name="additional order", index=False, header=False)
    return path


def test_list_order_sheet_names_bnd():
    names = foparser.list_order_sheet_names(BND_PATH)
    assert names == ["base order", "additional order"]


def test_additional_quantity_column_detected_on_addl_sheet():
    raw = pd.read_excel(BND_PATH, sheet_name="additional order", header=None)
    import article_master_parser as amparser
    hi = amparser.detect_header_row(raw)
    header = raw.iloc[hi].tolist()
    mapping = amparser.map_columns_to_core(header)
    valid = [row for _, row in raw.iloc[hi + 1:].iterrows() if amparser.is_data_row(row.tolist(), mapping)]
    det = foparser.detect_quantity_column(header, mapping, "Bed", valid)
    assert det["status"] == "ok"
    assert amparser._norm(det["column_label"]) == "additional quantity"


def test_parse_filled_order_workbook_clubs_bnd_tabs():
    result = foparser.parse_filled_order_workbook(BND_PATH, "Bed")
    assert result["status"] == "ok"
    assert result["sheet_names"] == ["base order", "additional order"]
    assert result["quantity_column_used"] == "Qty (multi-sheet clubbed)"
    assert "additional order" in (result.get("quantity_column_detail") or "")
    assert result["raw_line_count_before_club"] > result["parsed_rows"].__len__()
    # All additional lines overlap base → clubbed count == base ordered lines
    # Base has some zero-qty skipped; clubbed should be >= 30
    assert len(result["parsed_rows"]) >= 30
    total_qty = sum(r["raw_qty_value"] or 0 for r in result["parsed_rows"])
    assert total_qty > 20000  # clubbed base+addl teaching total


def test_two_tab_synthetic_clubs_overlapping_brand_size(tmp_path):
    path = _write_two_tab_workbook(tmp_path)
    result = foparser.parse_filled_order_workbook(path, "Bed")
    assert result["status"] == "ok"
    assert set(result["sheet_names"]) == {"base order", "additional order"}
    by_key = {
        (str((r["core_fields"] or {}).get("brand")).lower(), str((r["core_fields"] or {}).get("size")).upper()): r
        for r in result["parsed_rows"]
    }
    # Aster DB = 36 + 18
    aster = next(r for r in result["parsed_rows"] if "aster" in str((r["core_fields"] or {}).get("brand")).lower())
    assert aster["raw_qty_value"] == 54
    # Epigram only on additional
    epi = next(r for r in result["parsed_rows"] if "epigram" in str((r["core_fields"] or {}).get("brand")).lower())
    assert epi["raw_qty_value"] == 12
    assert len(result["parsed_rows"]) == 3
