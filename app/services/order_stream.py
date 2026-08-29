"""Order stream classification — regular vs special (global, not supplier-specific).

Used to keep Filled Order uploads and SO Pack matches in separate streams so
special shade-block bookings never cross-match regular catalog SOs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

import article_master_parser as amparser

STREAM_REGULAR = "regular"
STREAM_SPECIAL = "special"
STREAM_MIXED = "mixed"
STREAM_UNKNOWN = "unknown"

VALID_STREAMS = frozenset({STREAM_REGULAR, STREAM_SPECIAL})

_SPECIAL_PO_TOKENS = frozenset({"SPL", "SPECIAL", "SPCL"})
_SPECIAL_FILENAME_TOKENS = ("special", " spl", "_spl", "-spl", "spl ", "spl_")

# Category words stripped when normalizing PO family (not stream markers).
_PO_CATEGORY_TOKENS = frozenset({
    "TOWEL", "TOWELS", "BATH", "BED", "BEDSHEET", "BEDSHEETS", "LINEN",
    "ORDER", "ORDERS", "BOOKING",
})


def normalize_po_family(po_raw: str | None) -> str | None:
    """RFA 0381 TOWEL SPL → RFA 0381"""
    text = re.sub(r"\s+", " ", (po_raw or "").strip().upper())
    if not text:
        return None
    tokens = [t for t in re.split(r"[\s/\\\-]+", text) if t]
    kept: list[str] = []
    for tok in tokens:
        if tok in _SPECIAL_PO_TOKENS:
            continue
        if tok in _PO_CATEGORY_TOKENS:
            continue
        kept.append(tok)
    return " ".join(kept) if kept else text


def po_stream_tag(po_raw: str | None) -> str | None:
    text = (po_raw or "").upper()
    for tok in _SPECIAL_PO_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", text):
            return tok
    if "SPL" in text.replace(" ", ""):
        return "SPL"
    return None


def classify_stream_from_po(po_raw: str | None) -> str:
    if po_stream_tag(po_raw):
        return STREAM_SPECIAL
    if (po_raw or "").strip():
        return STREAM_REGULAR
    return STREAM_UNKNOWN


def classify_stream_from_filename(filename: str | None) -> str | None:
    stem = Path(filename or "").stem.lower()
    if not stem:
        return None
    if any(tok in stem for tok in _SPECIAL_FILENAME_TOKENS) or "spl" in stem.split():
        return STREAM_SPECIAL
    return None


def _sheet_has_shade_block_layout(raw_df: pd.DataFrame) -> bool:
    if raw_df is None or raw_df.empty:
        return False
    scan_rows = min(6, len(raw_df))
    blob_parts: list[str] = []
    for idx in range(scan_rows):
        for val in raw_df.iloc[idx].tolist():
            norm = amparser._norm(val)
            if norm:
                blob_parts.append(norm)
    blob = " ".join(blob_parts)
    has_shades = "no shades" in blob or "no.shades" in blob.replace(" ", "")
    has_per_color = "per color" in blob or "per colour" in blob
    has_quality = "quality" in blob
    has_total_qty = "total qty" in blob or "total quantity" in blob
    return has_shades and has_per_color and has_quality and has_total_qty


def classify_fo_stream(filename: str | None, workbook_path=None) -> str:
    """Classify a filled-order Excel as regular or special."""
    by_name = classify_stream_from_filename(filename)
    if by_name == STREAM_SPECIAL:
        return STREAM_SPECIAL
    if workbook_path is not None:
        try:
            xl = pd.ExcelFile(workbook_path)
            for sheet in xl.sheet_names[:3]:
                raw = pd.read_excel(workbook_path, sheet_name=sheet, header=None)
                if _sheet_has_shade_block_layout(raw):
                    return STREAM_SPECIAL
        except Exception:
            pass
    return STREAM_REGULAR


def classify_so_header_stream(
    *,
    po_number: str | None = None,
    source_pdf: str | None = None,
) -> str:
    by_po = classify_stream_from_po(po_number)
    if by_po == STREAM_SPECIAL:
        return STREAM_SPECIAL
    by_name = classify_stream_from_filename(source_pdf)
    if by_name == STREAM_SPECIAL:
        return STREAM_SPECIAL
    if (po_number or "").strip() or (source_pdf or "").strip():
        return STREAM_REGULAR
    return STREAM_UNKNOWN


def classify_so_pack_stream(so_pack: dict[str, Any]) -> str:
    """Pack-level stream: regular, special, or mixed."""
    streams: set[str] = set()
    for row in so_pack.get("line_detail") or []:
        if not isinstance(row, dict):
            continue
        streams.add(
            classify_so_header_stream(
                po_number=row.get("po_number"),
                source_pdf=row.get("source_pdf"),
            )
        )
    for row in so_pack.get("so_summary") or []:
        if not isinstance(row, dict):
            continue
        streams.add(
            classify_so_header_stream(
                po_number=row.get("po_number"),
                source_pdf=row.get("source_pdf"),
            )
        )
    streams.discard(STREAM_UNKNOWN)
    if not streams:
        meta = so_pack.get("meta") or {}
        by_name = classify_stream_from_filename(meta.get("source_filename"))
        return by_name or STREAM_REGULAR
    if len(streams) == 1:
        return next(iter(streams))
    return STREAM_MIXED


def _row_stream(row: dict[str, Any]) -> str:
    return classify_so_header_stream(
        po_number=row.get("po_number"),
        source_pdf=row.get("source_pdf"),
    )


def filter_so_pack_by_stream(so_pack: dict[str, Any], stream: str) -> dict[str, Any]:
    """Return a copy of so_pack containing only lines/summaries for one stream."""
    want = (stream or STREAM_REGULAR).strip().lower()
    if want not in VALID_STREAMS:
        want = STREAM_REGULAR

    line_detail = [
        r for r in (so_pack.get("line_detail") or [])
        if isinstance(r, dict) and _row_stream(r) == want
    ]
    kept_sos = {str(r.get("so_number") or "").strip() for r in line_detail if r.get("so_number")}

    so_summary = [
        r for r in (so_pack.get("so_summary") or [])
        if isinstance(r, dict) and (
            str(r.get("so_number") or "").strip() in kept_sos
            or _row_stream(r) == want
        )
    ]

    # Rebuild consolidated from filtered lines
    consol_acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in line_detail:
        key = (str(row.get("so_number") or ""), str(row.get("product_name") or ""))
        acc = consol_acc.get(key)
        if not acc:
            acc = {
                "so_number": row.get("so_number"),
                "order_date": row.get("order_date"),
                "buyer_name": row.get("buyer_name"),
                "po_number": row.get("po_number"),
                "product_name": row.get("product_name"),
                "sku_lines": 0,
                "total_qty": 0.0,
                "net_amount": 0.0,
                "gst_amount": 0.0,
                "total_amount": 0.0,
            }
            consol_acc[key] = acc
        acc["sku_lines"] += 1
        acc["total_qty"] = round(float(acc["total_qty"]) + float(row.get("qty") or 0), 3)
        acc["net_amount"] = round(float(acc["net_amount"]) + float(row.get("net_amount") or 0), 2)
        acc["gst_amount"] = round(float(acc["gst_amount"]) + float(row.get("gst_amount") or 0), 2)
        acc["total_amount"] = round(
            float(acc["total_amount"]) + float(row.get("total_amount") or 0), 2
        )

    meta = dict(so_pack.get("meta") or {})
    meta["order_stream"] = want
    meta["filtered_from_mixed"] = classify_so_pack_stream(so_pack) == STREAM_MIXED
    meta["so_count"] = len(so_summary)
    meta["line_rows"] = len(line_detail)
    meta["consolidated_rows"] = len(consol_acc)
    meta["total_qty"] = round(sum(float(r.get("total_qty") or 0) for r in so_summary), 3)
    meta["net_amount"] = round(sum(float(r.get("net_amount") or 0) for r in so_summary), 2)
    meta["gst_amount"] = round(sum(float(r.get("gst_amount") or 0) for r in so_summary), 2)
    meta["total_amount"] = round(sum(float(r.get("total_amount") or 0) for r in so_summary), 2)

    po_numbers = [r.get("po_number") for r in so_summary if r.get("po_number")]
    meta["po_family"] = normalize_po_family(po_numbers[0]) if po_numbers else meta.get("po_family")

    return {
        "meta": meta,
        "consolidated": list(consol_acc.values()),
        "so_summary": so_summary,
        "line_detail": line_detail,
    }


def annotate_so_pack_meta(so_pack: dict[str, Any]) -> dict[str, Any]:
    """Add order_stream / po_family / mixed flag to meta (in-place copy)."""
    out = dict(so_pack)
    meta = dict(out.get("meta") or {})
    pack_stream = classify_so_pack_stream(out)
    meta["order_stream"] = pack_stream
    meta["mixed_streams"] = pack_stream == STREAM_MIXED
    if pack_stream == STREAM_MIXED:
        counts: dict[str, int] = {STREAM_REGULAR: 0, STREAM_SPECIAL: 0}
        for row in out.get("so_summary") or []:
            if not isinstance(row, dict):
                continue
            st = classify_so_header_stream(
                po_number=row.get("po_number"),
                source_pdf=row.get("source_pdf"),
            )
            if st in counts:
                counts[st] += 1
        meta["stream_so_counts"] = counts
    pos = [
        r.get("po_number")
        for r in (out.get("so_summary") or [])
        if isinstance(r, dict) and r.get("po_number")
    ]
    if pos:
        meta["po_family"] = normalize_po_family(pos[0])
    out["meta"] = meta
    return out


def streams_compatible(fo_stream: str | None, so_stream: str | None) -> bool:
    fo = (fo_stream or STREAM_REGULAR).lower()
    so = (so_stream or STREAM_REGULAR).lower()
    if so == STREAM_MIXED:
        return True  # caller filters before match
    return fo == so


def stream_display_label(stream: str | None) -> str:
    st = (stream or STREAM_REGULAR).lower()
    if st == STREAM_SPECIAL:
        return "Special Order"
    if st == STREAM_MIXED:
        return "Mixed"
    return "Regular"


def build_mixed_zip_retry_hint(
    conn,
    *,
    user_id: int,
    fo: dict[str, Any],
    so_source_filename: str | None,
    pack_was_mixed: bool,
) -> dict[str, Any] | None:
    """When one stream from a mixed zip is already saved, point user to the sibling FO."""
    if not pack_was_mixed:
        return None
    import filled_orders_db as fodb

    fo_stream = (fo.get("order_stream") or STREAM_REGULAR).strip().lower()
    other_stream = STREAM_SPECIAL if fo_stream == STREAM_REGULAR else STREAM_REGULAR
    sibling = fodb.find_filled_order_by_distributor_category_season(
        conn,
        int(user_id),
        fo.get("distributor_id"),
        fo.get("category"),
        fo.get("season"),
        order_stream=other_stream,
    )
    if not sibling or not sibling.get("id"):
        return None
    from app.services import fo_so_match_db as matchdb

    sibling_id = int(sibling["id"])
    if matchdb.fo_has_match_for_so_zip(
        conn, int(user_id), sibling_id, so_source_filename
    ):
        return None
    cur_label = stream_display_label(fo_stream)
    other_label = stream_display_label(other_stream)
    zip_ref = (so_source_filename or "this zip").strip()
    fo_name = sibling.get("source_filename") or sibling.get("distributor_name_raw") or "saved FO"
    return {
        "hint_code": "match_other_stream_fo",
        "other_filled_order_id": sibling_id,
        "other_stream": other_stream,
        "other_stream_label": other_label,
        "message": (
            f"{cur_label} SOs from {zip_ref} are already on this Filled Order. "
            f"Choose {other_label} FO ({fo_name}) to match the other SO lines from the same zip."
        ),
    }
