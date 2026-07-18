"""Parse FY sales achievement Excel (pivot: distributor / customer / category / months)."""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd

_MONTH_RE = re.compile(
    r"^(apr|may|jun|jul|aug|sep|oct|nov|dec|jan|feb|mar)[-\s]?\d{2,4}$",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(r"\btotal\b", re.IGNORECASE)
_GRAND_TOTAL_LABELS = frozenset({"GRAND TOTAL", "GRANDTOTAL", "GRAND"})


def _is_grand_total_label(text: str) -> bool:
    normalized = (text or "").strip().upper().replace(" ", "")
    return normalized in _GRAND_TOTAL_LABELS or (text or "").strip().upper() == "GRAND TOTAL"


def _is_region_rollup_label(text: str) -> bool:
    upper = (text or "").strip().upper()
    if not upper:
        return False
    if _is_grand_total_label(text):
        return True
    return any(token in upper for token in ("REGION", "GROUP D", "GROUP TOTAL", "(NORTH)", "(SOUTH)"))


def _is_value_subheader_row(row, customer_idx: int, category_idx: int | None) -> bool:
    customer = _norm_header(row.iloc[customer_idx]).upper()
    if customer == "VALUE":
        return True
    if category_idx is not None and _norm_header(row.iloc[category_idx]).upper() == "VALUE":
        return True
    return False


def _sheet_has_budget_hint(raw: pd.DataFrame, header_idx: int, headers: list[str]) -> bool:
    for idx in range(header_idx):
        for val in raw.iloc[idx].tolist():
            text = _norm_header(val).upper()
            if "BUDGET" in text:
                return True
    return any("BUDGET" in (h or "").upper() for h in headers)


def _norm_header(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%b-%y")
    return str(value).strip()


def _to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text in {"-", "—"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_header_row(df: pd.DataFrame) -> int | None:
    for idx in range(min(25, len(df))):
        row = [_norm_header(v).upper() for v in df.iloc[idx].tolist()]
        if "CUSTOMER NAME" in row or "CUSTOMER" in row:
            return idx
    return None


def _column_map(headers: list[str], raw_headers: list[Any]) -> dict[str, int | list[int]]:
    upper = [h.upper() for h in headers]
    mapping: dict[str, int | list[int]] = {"months": []}
    for i, h in enumerate(headers):
        hu = upper[i]
        raw = raw_headers[i]
        if not hu and not isinstance(raw, datetime):
            continue
        if "NICK" in hu and "DISTRIBUTOR" in hu:
            mapping["nick"] = i
        elif hu in {"NICK NAME", "NICK NAME OR DISTRIBUTOR"} or hu.startswith("NICK NAME"):
            mapping["nick"] = i
        elif "CUSTOMER NAME" in hu or hu == "CUSTOMER":
            mapping["customer"] = i
        elif "GROUP" in hu and "CUSTOMER" in hu:
            mapping["group"] = i
        elif hu == "CATEGORY":
            mapping["category"] = i
        elif "GRAND TOTAL" in hu:
            mapping["grand_total"] = i
        elif "BUDGET" in hu:
            mapping["grand_total"] = i
        elif hu == "TOTAL" and "grand_total" not in mapping:
            mapping["grand_total"] = i
        elif _MONTH_RE.match(hu.replace(" ", "")):
            mapping["months"].append(i)
        elif isinstance(raw, datetime):
            mapping["months"].append(i)
    return mapping


def _parse_sheet(raw: pd.DataFrame) -> dict[str, Any] | None:
    header_idx = _find_header_row(raw)
    if header_idx is None:
        return None

    raw_headers = raw.iloc[header_idx].tolist()
    headers = [_norm_header(v) for v in raw_headers]
    colmap = _column_map(headers, raw_headers)
    if "customer" not in colmap:
        return None

    data = raw.iloc[header_idx + 1 :].reset_index(drop=True)
    nick_idx = colmap.get("nick")
    customer_idx = colmap["customer"]
    category_idx = colmap.get("category")
    group_idx = colmap.get("group")
    grand_total_idx = colmap.get("grand_total")
    month_idxs = colmap.get("months") or []
    is_budget_sheet = _sheet_has_budget_hint(raw, header_idx, headers)

    distributors: dict[str, dict[str, Any]] = {}
    categories: list[dict[str, Any]] = []
    current_nick = ""
    current_customer = ""

    for _, row in data.iterrows():
        if _is_value_subheader_row(row, customer_idx, category_idx):
            continue

        nick_val = row.iloc[nick_idx] if nick_idx is not None else None
        cust_val = row.iloc[customer_idx]
        group_text = ""
        if group_idx is not None:
            group_val = row.iloc[group_idx]
            if group_val is not None and not (isinstance(group_val, float) and pd.isna(group_val)):
                group_text = _norm_header(group_val)
        if group_text and _is_region_rollup_label(group_text):
            cust_empty = cust_val is None or (isinstance(cust_val, float) and pd.isna(cust_val)) or not _norm_header(cust_val)
            if cust_empty and not (category_idx is not None and _norm_header(row.iloc[category_idx])):
                continue

        if nick_val is not None and not (isinstance(nick_val, float) and pd.isna(nick_val)):
            nick_text = _norm_header(nick_val)
            if nick_text and not _is_grand_total_label(nick_text):
                current_nick = nick_text
        if cust_val is not None and not (isinstance(cust_val, float) and pd.isna(cust_val)):
            cust_text = _norm_header(cust_val)
            if cust_text and not _TOTAL_RE.search(cust_text) and not _is_grand_total_label(cust_text):
                current_customer = cust_text

        customer = _norm_header(cust_val) if cust_val is not None and not (isinstance(cust_val, float) and pd.isna(cust_val)) else current_customer
        if not customer or _is_grand_total_label(customer):
            continue
        nick = current_nick or (customer if is_budget_sheet else "")
        category = _norm_header(row.iloc[category_idx]) if category_idx is not None else ""
        cat_norm = category.strip().lower()

        if is_budget_sheet and cat_norm == "total":
            dist_name = current_customer or customer
            if not dist_name or _is_grand_total_label(dist_name):
                continue
            amount = None
            if grand_total_idx is not None:
                amount = _to_float(row.iloc[grand_total_idx])
            if amount is None and month_idxs:
                amount = sum(_to_float(row.iloc[i]) or 0 for i in month_idxs)
            if amount is None:
                continue
            distributors[dist_name] = {
                "name": dist_name,
                "nick": dist_name,
                "achievement_lakhs": round(float(amount), 4),
                "target_lakhs": round(float(amount), 4),
            }
            continue

        if cat_norm == "total":
            continue

        if _TOTAL_RE.search(customer) and not category:
            party_name = _TOTAL_RE.sub("", customer).strip(" -")
            if _is_grand_total_label(party_name) or _is_grand_total_label(customer) or _is_region_rollup_label(party_name):
                continue
            amount = None
            if grand_total_idx is not None:
                amount = _to_float(row.iloc[grand_total_idx])
            if amount is None and month_idxs:
                amount = sum(_to_float(row.iloc[i]) or 0 for i in month_idxs)
            if amount is None:
                continue
            key = party_name or customer
            distributors[key] = {
                "name": key,
                "nick": nick or None,
                "achievement_lakhs": round(float(amount), 4),
            }
            continue

        if category and not _TOTAL_RE.search(customer) and cat_norm != "total":
            amount = None
            if grand_total_idx is not None:
                amount = _to_float(row.iloc[grand_total_idx])
            if amount is None and month_idxs:
                amount = sum(_to_float(row.iloc[i]) or 0 for i in month_idxs)
            if not amount:
                continue
            dist_key = current_customer or customer
            categories.append(
                {
                    "distributor": dist_key,
                    "nick": nick or None,
                    "category": category,
                    "achievement_lakhs": round(float(amount), 4),
                }
            )

    if not distributors and categories:
        grouped: dict[str, dict[str, Any]] = {}
        for item in categories:
            key = item["distributor"]
            bucket = grouped.setdefault(
                key,
                {
                    "name": key,
                    "nick": item.get("nick"),
                    "achievement_lakhs": 0.0,
                },
            )
            bucket["achievement_lakhs"] = round(
                bucket["achievement_lakhs"] + float(item["achievement_lakhs"] or 0), 4
            )
        distributors = grouped

    if not distributors:
        grouped = {}
        for _, row in data.iterrows():
            customer = _norm_header(row.iloc[customer_idx])
            if not customer or _TOTAL_RE.search(customer):
                continue
            nick = _norm_header(row.iloc[nick_idx]) if nick_idx is not None else ""
            amount = 0.0
            if grand_total_idx is not None:
                amount = _to_float(row.iloc[grand_total_idx]) or 0.0
            elif month_idxs:
                amount = sum(_to_float(row.iloc[i]) or 0 for i in month_idxs)
            if not amount:
                continue
            bucket = grouped.setdefault(
                customer,
                {"name": customer, "nick": nick or None, "achievement_lakhs": 0.0},
            )
            bucket["achievement_lakhs"] = round(bucket["achievement_lakhs"] + amount, 4)
        distributors = grouped

    if not distributors:
        return None

    return {
        "distributors": distributors,
        "categories": categories,
        "headers": headers,
        "month_idxs": month_idxs,
        "file_kind": "budget" if is_budget_sheet else "achievement",
    }


def _pick_best_sheet(file_bytes: bytes) -> dict[str, Any]:
    book = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")
    sheet_parsed: list[tuple[str, dict[str, Any]]] = []

    for sheet in book.sheet_names:
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        parsed = _parse_sheet(raw)
        if parsed:
            sheet_parsed.append((sheet, parsed))

    if not sheet_parsed:
        raw = pd.read_excel(BytesIO(file_bytes), header=None, engine="openpyxl")
        parsed = _parse_sheet(raw)
        if not parsed:
            raise ValueError("Could not find header row with CUSTOMER NAME.")
        return parsed

    merged_distributors: dict[str, dict[str, Any]] = {}
    merged_categories: list[dict[str, Any]] = []
    headers: list[str] = []
    month_idxs: list[int] = []

    for _sheet_name, parsed in sheet_parsed:
        for dist in parsed.get("distributors", {}).values():
            name = dist.get("name") or ""
            if not name:
                continue
            existing = merged_distributors.get(name)
            if not existing or float(dist.get("achievement_lakhs") or 0) >= float(
                existing.get("achievement_lakhs") or 0
            ):
                merged_distributors[name] = dist
        if parsed.get("categories"):
            merged_categories.extend(parsed.get("categories") or [])
            if not headers:
                headers = parsed.get("headers") or []
                month_idxs = parsed.get("month_idxs") or []

    if merged_categories:
        deduped_categories: dict[tuple[str, str], dict[str, Any]] = {}
        for item in merged_categories:
            dist = (item.get("distributor") or "").strip()
            cat = (item.get("category") or "").strip()
            if not dist or not cat:
                continue
            key = (dist, cat)
            if key not in deduped_categories:
                deduped_categories[key] = {
                    "distributor": dist,
                    "category": cat,
                    "nick": item.get("nick"),
                    "achievement_lakhs": 0.0,
                }
            bucket = deduped_categories[key]
            bucket["achievement_lakhs"] = round(
                bucket["achievement_lakhs"] + float(item.get("achievement_lakhs") or 0), 4
            )
            if not bucket.get("nick") and item.get("nick"):
                bucket["nick"] = item.get("nick")
        merged_categories = list(deduped_categories.values())

    if not merged_distributors and merged_categories:
        grouped: dict[str, dict[str, Any]] = {}
        for item in merged_categories:
            key = item["distributor"]
            bucket = grouped.setdefault(
                key,
                {
                    "name": key,
                    "nick": item.get("nick"),
                    "achievement_lakhs": 0.0,
                },
            )
            bucket["achievement_lakhs"] = round(
                bucket["achievement_lakhs"] + float(item["achievement_lakhs"] or 0), 4
            )
        merged_distributors = grouped

    if not merged_distributors:
        raise ValueError("Could not find distributor totals or category rows in Excel.")

    file_kind = "achievement"
    for _sheet_name, parsed in sheet_parsed:
        if parsed.get("file_kind") == "budget":
            file_kind = "budget"
            break

    return {
        "distributors": merged_distributors,
        "categories": merged_categories,
        "headers": headers,
        "month_idxs": month_idxs,
        "file_kind": file_kind,
    }


def _build_category_matrix(categories: list[dict[str, Any]], distributors: list[dict[str, Any]]) -> dict[str, Any]:
    cat_names = sorted({c["category"] for c in categories if c.get("category")})
    if not cat_names:
        return {"categories": [], "rows": [], "totals_by_category": {}, "grand_total": 0.0}

    by_dist: dict[str, dict[str, float]] = {}
    nick_by_dist: dict[str, str | None] = {}
    for item in categories:
        dist = item["distributor"]
        cat = item["category"]
        nick_by_dist[dist] = item.get("nick") or nick_by_dist.get(dist)
        by_dist.setdefault(dist, {})[cat] = round(
            by_dist.get(dist, {}).get(cat, 0) + float(item["achievement_lakhs"] or 0), 4
        )

    dist_order = {d["name"]: i for i, d in enumerate(distributors)}
    rows = []
    for dist_name, cats in sorted(
        by_dist.items(),
        key=lambda kv: (-sum(kv[1].values()), dist_order.get(kv[0], 999)),
    ):
        nick = nick_by_dist.get(dist_name)
        label = f"{nick} | {dist_name}" if nick else dist_name
        values = {cat: round(cats.get(cat, 0), 2) for cat in cat_names}
        total = round(sum(values.values()), 2)
        rows.append(
            {
                "distributor": dist_name,
                "nick": nick,
                "label": label,
                "values": values,
                "total": total,
            }
        )

    totals_by_category = {
        cat: round(sum(r["values"].get(cat, 0) for r in rows), 2) for cat in cat_names
    }
    grand_total = round(sum(totals_by_category.values()), 2)
    return {
        "categories": cat_names,
        "rows": rows,
        "totals_by_category": totals_by_category,
        "grand_total": grand_total,
    }


def parse_sales_achievement_excel(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """
    Parse pivot-style sales report. Amounts are treated as lakhs.
    Picks the sheet with richest category detail when multiple sheets exist.
    """
    parsed = _pick_best_sheet(file_bytes)
    distributors = parsed["distributors"]
    categories = parsed.get("categories") or []
    dist_list = sorted(
        distributors.values(),
        key=lambda item: item.get("achievement_lakhs") or 0,
        reverse=True,
    )
    total_lakhs = round(sum(d.get("achievement_lakhs") or 0 for d in dist_list), 4)
    category_matrix = _build_category_matrix(categories, dist_list)

    fy_hint = None
    month_idxs = parsed.get("month_idxs") or []
    headers = parsed.get("headers") or []
    if month_idxs:
        sample = _norm_header(headers[month_idxs[0]])
        m = re.search(r"(\d{2,4})$", sample.replace(" ", ""))
        if m:
            end = m.group(1)[-2:]
            start = str(int(end) - 1).zfill(2)
            fy_hint = f"20{start}-20{end}"
        elif isinstance(headers[month_idxs[0]], datetime):
            dt = headers[month_idxs[0]]
            start = dt.year
            fy_hint = f"{start}-{start + 1}"

    return {
        "filename": filename,
        "unit": "lakhs",
        "file_kind": parsed.get("file_kind") or "achievement",
        "financial_year_hint": fy_hint,
        "total_achievement_lakhs": total_lakhs,
        "distributor_count": len(dist_list),
        "distributors": dist_list,
        "categories": categories,
        "category_matrix": category_matrix,
        "has_category_detail": bool(category_matrix.get("rows")),
    }
