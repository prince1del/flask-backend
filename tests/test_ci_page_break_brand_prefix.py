from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes.data import (
    extract_order_sheet_item_key,
    parse_bombay_dyeing_so_ci_line_items,
)


def test_flfiest_ksfst_item_key():
    name = "FLFIEST 1+2 KSFST183X19 8+30 8106BLU140T C"
    assert extract_order_sheet_item_key(name) == "FLFIEST|140|KS"


def test_ci_page_break_brand_prefix_held_for_next_sn():
    """Page-end FLFIEST must prepend to next SN that starts with 1+2 KSFST…"""
    path = Path(
        r"G:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\CI\fresh"
        r"\Commercial Invoice (1).PDF"
    )
    if not path.exists():
        return
    items = parse_bombay_dyeing_so_ci_line_items(str(path), "CI")
    assert len(items) == 18
    # SN 14 was the page-break victim
    sn14 = items[13]
    assert sn14["item_name"].upper().startswith("FLFIEST")
    assert "8110LYW" in sn14["item_name"].upper()
    assert sn14["item_key"] == "FLFIEST|140|KS"
    # All lines should share one key — no orphan "Item" group
    keys = {it["item_key"] for it in items}
    assert keys == {"FLFIEST|140|KS"}
    assert sum(it["qty"] or 0 for it in items) == 72
