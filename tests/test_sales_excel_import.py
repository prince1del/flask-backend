"""Import path for pivot sales achievement Excel."""

import sqlite3
from pathlib import Path

import pytest

from app.services.sales_achievement_parser import parse_sales_achievement_excel
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def ta_db(tmp_path):
    db_path = tmp_path / "ta_import.sqlite3"
    db = CentralizedDB(str(db_path))
    db.ensure_target_achievement_tables()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO target_achievement_years (
                workspace_id, financial_year, target_amount, achievement_amount
            ) VALUES (?, ?, ?, ?)
            """,
            ("ws-test", "2025-2026", 1000.0, 0.0),
        )
        conn.commit()
        year_id = int(cur.lastrowid)
    return db, year_id


def test_replace_category_breakup_merges_duplicate_rows(ta_db):
    db, year_id = ta_db
    categories = [
        {"distributor": "BERNINA INTERNATIONAL P LTD", "category": "Bed Sheet", "achievement_lakhs": 10},
        {"distributor": "BERNINA INTERNATIONAL P LTD", "category": "Bed Sheet", "achievement_lakhs": 5},
        {"distributor": "BERNINA INTERNATIONAL P LTD", "category": "Towels", "achievement_lakhs": 3},
    ]
    count = db.replace_category_breakup("ws-test", year_id, categories)
    assert count == 2

    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            """
            SELECT category, achievement_lakhs
            FROM target_achievement_category_breakup
            WHERE financial_year_id = ? AND distributor_name = ?
            ORDER BY category
            """,
            (year_id, "BERNINA INTERNATIONAL P LTD"),
        ).fetchall()
    assert rows == [("Bed Sheet", 15.0), ("Towels", 3.0)]


def test_import_primary_workbook_structure(ta_db):
    headers = [
        "NICK NAME or Distributor",
        "CUSTOMER NAME",
        "CATEGORY",
        "Apr-25",
        "Grand Total",
    ]
    rows = [
        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 10],
        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 5, 5],
        ["BND", "BERNINA INTERNATIONAL P LTD Total", "", 15, 287.31],
    ]
    import pandas as pd
    from io import BytesIO

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame([headers] + rows).to_excel(writer, index=False, header=False)
        pd.DataFrame([headers] + [["Z", "Zirise Technologies Private Limited Total", "", 1, 123.41]]).to_excel(
            writer, sheet_name="Sheet1 (2)", index=False, header=False
        )

    db, year_id = ta_db
    parsed = parse_sales_achievement_excel(buf.getvalue(), "Primary 2025-26.xlsx")
    result = db.import_sales_excel_achievement("ws-test", year_id, parsed)

    assert result["distributor_count"] == 2
    assert result["category_row_count"] == 1
    assert round(result["total_achievement_lakhs"], 2) == round(287.31 + 123.41, 2)


def test_clear_fy_achievement_and_targets(ta_db):
    db, year_id = ta_db
    db.upsert_target_distributor_breakup(
        workspace_id="ws-test",
        financial_year_id=year_id,
        distributor_name="BERNINA INTERNATIONAL P LTD",
        achievement_lakhs=100.0,
        target_lakhs=200.0,
        source="excel_upload",
    )
    db.replace_category_breakup(
        "ws-test",
        year_id,
        [{"distributor": "BERNINA INTERNATIONAL P LTD", "category": "Bed Sheet", "achievement_lakhs": 50}],
    )
    ach = db.clear_fy_achievement("ws-test", year_id)
    assert ach["achievement_lakhs"] == 0.0
    breakup = db.list_target_distributor_breakup("ws-test", year_id)
    assert breakup[0]["target_lakhs"] == 200.0
    assert breakup[0]["achievement_excel"] == 0.0

    db.clear_fy_targets("ws-test", year_id)
    breakup_after = db.list_target_distributor_breakup("ws-test", year_id)
    assert not breakup_after or breakup_after[0]["target_lakhs"] == 0.0
