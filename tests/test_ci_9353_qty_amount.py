import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes.data import (
    build_commercial_invoice_detail,
    extract_order_sheet_item_key,
    parse_bombay_dyeing_so_ci_line_items,
    _ci_lines_disagree_with_header,
)

PDF = Path(r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009353.PDF")


def test_glued_db_item_key():
    assert extract_order_sheet_item_key(
        "FLORENTINE 1+2DB 228X254 7967BLU 144TC"
    ) == "FLORENTINE|144|DB"


def test_ci_9353_matches_pdf_footer():
    if not PDF.exists():
        return
    items = parse_bombay_dyeing_so_ci_line_items(PDF, "CI")
    assert len(items) == 18
    assert sum(float(it["qty"]) for it in items) == 72.0
    assert extract_order_sheet_item_key(items[0]["item_name"]) == "FLORENTINE|144|DB"
    detail = build_commercial_invoice_detail(PDF)
    header = detail["header"]
    assert int(header["total_pieces"]) == 72
    assert abs(float(header["invoice_total"]) - 67813.20) < 0.05
    assert abs(float(detail["totals"]["qty"]) - 72) < 0.05
    assert abs(float(detail["totals"]["invoice_total"]) - 67813.20) < 0.05
    assert _ci_lines_disagree_with_header(
        header,
        [{"qty": 8.0, "line_total": 8820.0}] * 18,
    )
    assert not _ci_lines_disagree_with_header(header, items)
