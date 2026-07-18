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
