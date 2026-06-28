import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl


EXPECTED_COLUMNS = [
    "distributor",
    "state",
    "region",
    "sales_exec",
    "category",
    "amount",
]


def calculate_achievement_percent(
    target: float | int | None, achievement: float | int | None
) -> float:
    """Calculate achievement % safely."""
    try:
        target_val = float(target or 0)
        achievement_val = float(achievement or 0)
    except (TypeError, ValueError):
        return 0.0

    if target_val == 0:
        return 0.0

    return round((achievement_val / target_val) * 100, 2)


def parse_csv_file(file_path: str) -> list[dict[str, Any]]:
    """Parse CSV sales report."""
    rows: list[dict[str, Any]] = []
    with open(file_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        header = [col.strip().lower() for col in reader.fieldnames or []]
        if "distributor" not in header or "amount" not in header:
            raise ValueError("CSV file must contain distributor and amount columns")

        for row in reader:
            normalized = {k.strip().lower(): v for k, v in row.items() if k is not None}
            rows.append(
                {
                    "distributor": normalized.get("distributor", "").strip(),
                    "state": normalized.get("state", "").strip(),
                    "region": normalized.get("region", "").strip(),
                    "sales_exec": normalized.get("sales_exec", "").strip(),
                    "category": normalized.get("category", "").strip(),
                    "amount": float(normalized.get("amount", 0) or 0),
                }
            )
    return rows


def parse_excel_file(file_path: str) -> list[dict[str, Any]]:
    """Parse Excel sales report."""
    workbook = openpyxl.load_workbook(file_path, data_only=True)
    sheet = workbook.active
    header = [
        str(cell.value).strip().lower() if cell.value is not None else ""
        for cell in next(sheet.rows)
    ]

    if "distributor" not in header or "amount" not in header:
        raise ValueError("Excel file must contain distributor and amount columns")

    rows: list[dict[str, Any]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        row_data = {
            header[idx]: row[idx] for idx in range(len(header)) if idx < len(row)
        }
        rows.append(
            {
                "distributor": str(row_data.get("distributor", "") or "").strip(),
                "state": str(row_data.get("state", "") or "").strip(),
                "region": str(row_data.get("region", "") or "").strip(),
                "sales_exec": str(row_data.get("sales_exec", "") or "").strip(),
                "category": str(row_data.get("category", "") or "").strip(),
                "amount": float(row_data.get("amount", 0) or 0),
            }
        )
    return rows


def aggregate_by_distributor(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Group rows by distributor and sum amounts."""
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        distributor = row.get("distributor", "").strip() or "Unknown"
        totals[distributor] += float(row.get("amount", 0) or 0)
    return dict(totals)


def aggregate_by_state(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Group rows by state and sum amounts."""
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        state = row.get("state", "").strip() or "Unknown"
        totals[state] += float(row.get("amount", 0) or 0)
    return dict(totals)


def aggregate_by_region(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Group rows by region and sum amounts."""
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        region = row.get("region", "").strip() or "Unknown"
        totals[region] += float(row.get("amount", 0) or 0)
    return dict(totals)


def validate_financial_year(financial_year: str) -> tuple[bool, str | None]:
    """Validate FY format (YYYY-YYYY)."""
    if not isinstance(financial_year, str):
        return False, "Financial year must be a string"

    pattern = r"^\d{4}-\d{4}$"
    if not re.match(pattern, financial_year.strip()):
        return False, "Financial year must be in YYYY-YYYY format"

    start_year, end_year = financial_year.split("-")
    if int(end_year) != int(start_year) + 1:
        return False, "Financial year must span one year"

    return True, None


def validate_amount(amount: Any) -> bool:
    """Validate amount is positive number."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return False
    return value >= 0


def update_source_labels(has_manual: bool, has_upload: bool) -> str:
    """Update source field based on whether manual + upload exist."""
    if has_manual and has_upload:
        return "Mixed"
    if has_upload:
        return "Upload"
    return "Manual"
