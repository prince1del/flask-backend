"""Normalize fiscal year labels to YYYY-YYYY."""

from __future__ import annotations

import re


def _expand_year(token: str) -> int:
    n = int(token)
    if n < 100:
        return 2000 + n
    return n


def normalize_fiscal_year(raw: str | None) -> str:
    """
    Accept flexible FY input and return canonical YYYY-YYYY.

    Examples:
      2024-25, 24-25, 2024 2025, 24 2025, 2024-2025 -> 2024-2025
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    tokens = re.findall(r"\d{2,4}", text)
    if len(tokens) < 2:
        return text

    start = _expand_year(tokens[0])
    end = _expand_year(tokens[1])
    if start > end:
        start, end = end, start
    return f"{start}-{end}"


def fiscal_year_sort_key(label: str | None) -> tuple[int, int]:
    normalized = normalize_fiscal_year(label)
    match = re.match(r"^(\d{4})-(\d{4})$", normalized)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def fiscal_year_date_bounds(label: str | None) -> tuple[str | None, str | None]:
    """Indian FY: Apr 1 of start year through Mar 31 of end year."""
    normalized = normalize_fiscal_year(label)
    match = re.match(r"^(\d{4})-(\d{4})$", normalized)
    if not match:
        return None, None
    start_year = int(match.group(1))
    end_year = int(match.group(2))
    return f"{start_year}-04-01", f"{end_year}-03-31"


def season_from_date(date_str: str | None) -> str | None:
    """Map a date to this business's SS/AW season code ("SS26"/"AW26") —
    confirmed rule: SS runs March-July, AW runs August-February. A
    January/February date belongs to the AW that started the previous
    August (e.g. Feb 2026 -> AW25, not AW26), since that AW season is
    still open then; a March date always starts a fresh SS for that same
    calendar year. This is a completely separate calendar from the
    Apr-Mar fiscal year above — do not reuse fiscal_year_date_bounds for
    season grouping, the boundaries don't line up.

    Accepts "YYYY-MM-DD" (or any string with that prefix, e.g. an ISO
    datetime) since that's the normalized form commercial_invoice_date
    and similar fields are stored in. Returns None if unparseable.
    """
    if not date_str:
        return None
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(date_str).strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if 3 <= month <= 7:
        return f"SS{year % 100:02d}"
    if month in (1, 2):
        return f"AW{(year - 1) % 100:02d}"
    return f"AW{year % 100:02d}"
