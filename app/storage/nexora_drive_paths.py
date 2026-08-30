"""Drive folder layout for Order Desk documents.

NEXORA/{Sales Orders|Filled Orders|Commercial Invoices}/{Season}/{Category}/{Distributor}/file
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SEASON_CODE_RE = re.compile(r"\b(AW|SS)\d{2}\b", re.I)


def sanitize_drive_folder_name(name: str | None, *, fallback: str = "Unassigned") -> str:
    text = re.sub(r'[<>:"/\\|?*]', "_", str(name or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def normalize_category_label(category: str | None) -> str:
    raw = (category or "").strip()
    if not raw:
        return "Others"
    lower = raw.lower()
    if "bed" in lower or "sheet" in lower and "bed" in lower:
        return "Bed"
    if any(t in lower for t in ("bath", "towel", "linen", "robe")):
        return "Bath"
    if "pillow" in lower:
        return "Pillow"
    if "tob" in lower or "dohar" in lower or "comforter" in lower:
        return "TOB"
    if lower in {"bed", "bath", "pillow", "tob", "others"}:
        return raw[:1].upper() + raw[1:].lower() if len(raw) > 1 else raw.upper()
    return raw[:1].upper() + raw[1:] if raw else "Others"


def season_from_order_sheet_name(name: str | None) -> str | None:
    if not name:
        return None
    match = _SEASON_CODE_RE.search(str(name))
    return match.group(0).upper() if match else None


def normalize_season_label(season: str | None) -> str:
    raw = (season or "").strip()
    if not raw:
        return "Unassigned Season"
    match = _SEASON_CODE_RE.search(raw)
    if match:
        return match.group(0).upper()
    return sanitize_drive_folder_name(raw, fallback="Unassigned Season")


def build_order_desk_drive_segments(
    *,
    season: str | None,
    category: str | None,
    distributor_name: str | None,
) -> list[str]:
    return [
        normalize_season_label(season),
        normalize_category_label(category),
        sanitize_drive_folder_name(distributor_name, fallback="Unassigned Distributor"),
    ]


def resolve_drive_season(
    *,
    season: str | None = None,
    order_date: str | None = None,
    order_sheet_name: str | None = None,
    workspace_id: str | None = None,
    db_path: str | None = None,
) -> str:
    if season:
        return normalize_season_label(season)
    if order_date:
        try:
            from app.fiscal_year import season_from_date

            mapped = season_from_date(order_date)
            if mapped:
                return mapped
        except Exception:
            pass
    mapped = season_from_order_sheet_name(order_sheet_name)
    if mapped:
        return mapped
    try:
        from centralized_db_system.db import CentralizedDB

        db = CentralizedDB(db_path) if db_path else CentralizedDB()
        sheet = db.get_latest_order_sheet(workspace_id=workspace_id or "default")
        if sheet:
            mapped = season_from_order_sheet_name(sheet.get("name"))
            if mapped:
                return mapped
    except Exception:
        pass
    return "Unassigned Season"


def _category_for_so_pdf(analyze_data: dict[str, Any], pdf_name: str) -> str:
    from app.services.order_stream import _so_line_category_label

    want = Path(pdf_name).name.casefold()
    counts: dict[str, float] = {}
    for row in analyze_data.get("line_detail") or []:
        if not isinstance(row, dict):
            continue
        source = Path(str(row.get("source_pdf") or "")).name.casefold()
        if source != want:
            continue
        cat = _so_line_category_label(row)
        qty = float(row.get("qty") or 1)
        counts[cat] = counts.get(cat, 0.0) + qty
    if counts:
        best = max(counts.items(), key=lambda item: item[1])[0]
        return normalize_category_label(best)
    meta = analyze_data.get("meta") or {}
    return normalize_category_label(meta.get("dominant_category"))


def so_pdf_drive_contexts(
    analyze_data: dict[str, Any] | None,
    *,
    workspace_id: str | None = None,
    db_path: str | None = None,
) -> dict[str, dict[str, str]]:
    """Map PDF basename -> {season, category, distributor_name} for Drive backup."""
    data = analyze_data or {}
    meta = data.get("meta") or {}
    default_buyer = str(meta.get("primary_buyer_name") or "").strip() or "Unassigned Distributor"
    default_season = resolve_drive_season(
        order_sheet_name=meta.get("order_sheet_name"),
        workspace_id=workspace_id,
        db_path=db_path,
    )
    default_category = normalize_category_label(meta.get("dominant_category"))

    contexts: dict[str, dict[str, str]] = {}
    for row in data.get("so_summary") or []:
        if not isinstance(row, dict):
            continue
        pdf_name = Path(str(row.get("source_pdf") or "")).name
        if not pdf_name:
            continue
        buyer = str(row.get("buyer_name") or default_buyer).strip() or default_buyer
        season = resolve_drive_season(
            order_date=row.get("order_date"),
            order_sheet_name=meta.get("order_sheet_name"),
            workspace_id=workspace_id,
            db_path=db_path,
        )
        contexts[pdf_name] = {
            "season": season or default_season,
            "category": _category_for_so_pdf(data, pdf_name) or default_category,
            "distributor_name": buyer,
        }

    contexts["_default"] = {
        "season": default_season,
        "category": default_category,
        "distributor_name": default_buyer,
    }
    return contexts
