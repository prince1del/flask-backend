"""Budget / target Excel format (CUSTOMER + CATEGORY + Budget column)."""

from io import BytesIO

import pandas as pd
import pytest

from app.services.sales_achievement_parser import parse_sales_achievement_excel


def _budget_workbook():
    rows = [
        ("RETAIL BUDGET 2024-25 Customer-wise", None, None, None, None),
        (None, None, None, None, None),
        ("CUSTOMER", "CATEGORY", "Apr-24", "May-24", "Budget 2024-25"),
        (None, "Value", "Value", "Value", "Value"),
        ("BND", "Bed", 31, 40, 71),
        (None, "Bath", 18, 18, 36),
        (None, "Total", 49, 58, 107),
        ("GEB", "Bed", 3, 3, 6),
        (None, "Total", 3, 3, 6),
        ("Grand Total", None, 52, 61, 113),
    ]
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(list(rows)).to_excel(writer, index=False, header=False)
    return buf.getvalue()


def test_parse_budget_format_distributors():
    parsed = parse_sales_achievement_excel(_budget_workbook(), "Target 2024-25.xlsx")
    assert parsed["file_kind"] == "budget"
    by_name = {d["name"]: d for d in parsed["distributors"]}
    assert by_name["BND"]["target_lakhs"] == 107
    assert by_name["GEB"]["target_lakhs"] == 6
    assert "Grand" not in by_name
    assert parsed["distributor_count"] == 2


def test_parse_primary_format_skips_region_rollup():
    rows = [
        (None, "Customer A/c Group D", "CUSTOMER NAME", "CATEGORY", "Apr-24", "Grand Total"),
        (None, None, "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 10),
        (None, None, "BERNINA INTERNATIONAL P LTD Total", None, 10, 10),
        (None, None, "DCA MARKETING Total", None, 2, 2),
        (None, "RDS - GT (North Region) Total", None, None, 100, 949),
    ]
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(list(rows)).to_excel(writer, index=False, header=False)
    parsed = parse_sales_achievement_excel(buf.getvalue(), "Primary 2024-25 sale.xlsx")
    by_name = {d["name"]: d["achievement_lakhs"] for d in parsed["distributors"]}
    assert by_name["BERNINA INTERNATIONAL P LTD"] == 10
    assert by_name["DCA MARKETING"] == 2
    assert "RDS - GT (North Region)" not in by_name
    assert parsed["total_achievement_lakhs"] == 12


@pytest.mark.skipif(
    not __import__("pathlib").Path(r"G:\My Drive\2024-2025\Year Closing\Target 2024-25.xlsx").exists(),
    reason="User Target workbook not available on this machine",
)
def test_parse_real_target_workbook():
    from pathlib import Path

    path = Path(r"G:\My Drive\2024-2025\Year Closing\Target 2024-25.xlsx")
    parsed = parse_sales_achievement_excel(path.read_bytes(), path.name)
    assert parsed["file_kind"] == "budget"
    assert parsed["distributor_count"] == 11
    assert round(parsed["distributors"][0]["target_lakhs"] or 0, 0) > 0
