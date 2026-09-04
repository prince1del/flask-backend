"""SO-direct save: match Sales Order lines to Article Master (no Filled Order)."""

from __future__ import annotations

from typing import Any

import article_master_db as amdb

from app.routes.data import extract_order_sheet_item_key
from app.services.fo_so_match_lab import (
    build_so_buckets_from_line_detail,
    display_size_code,
    match_pair_key,
)
from ci_article_match import annotate_ci_line_items_with_article_master


MATCH_MODE_AM_ONLY = "article_master_only"


def _line_item_name(row: dict[str, Any]) -> str:
    """Prefer product_detail alone — appending material_code breaks TC key parsing."""
    detail = str(row.get("product_detail") or "").strip()
    if detail:
        return detail
    parts = [
        row.get("material_code"),
        row.get("product_name"),
    ]
    return " ".join(str(p).strip() for p in parts if p and str(p).strip()).strip()


def prepare_so_lines_for_am_match(line_detail: list[Any] | None) -> list[dict[str, Any]]:
    """Build Brand|TC|Size keys from SO PDF lines (same as CI matching)."""
    out: list[dict[str, Any]] = []
    for raw in line_detail or []:
        if not isinstance(raw, dict):
            continue
        line = dict(raw)
        name = _line_item_name(line)
        if name:
            line.setdefault("item_name", name)
            if not line.get("item_key"):
                key = extract_order_sheet_item_key(name)
                if key:
                    line["item_key"] = key
        out.append(line)
    return out


def _line_brand_size(row: dict[str, Any]) -> tuple[str, str]:
    from app.services.bd_product_catalog import enrich_bd_product, resolve_so_brand_size
    from app.services.so_pack_consolidate import product_short_name

    short = str(row.get("product_name") or "").strip()
    if not short and row.get("product_detail"):
        short = product_short_name(str(row.get("product_detail") or ""))
    brand, size = resolve_so_brand_size(
        short or str(row.get("product_detail") or ""),
        material_code=str(row.get("material_code") or "") or None,
    )
    if not brand or not size:
        enriched = enrich_bd_product(short or _line_item_name(row)) if (short or _line_item_name(row)) else {}
        brand = brand or enriched.get("collection") or ""
        size = size or enriched.get("product_type") or ""
    return str(brand or ""), str(size or "")


def _bucket_am_status(
    bucket_key: tuple[str, str],
    annotated_lines: list[dict[str, Any]],
) -> str:
    """MATCH when every line in this brand×size bucket matched Article Master."""
    hits = 0
    misses = 0
    for line in annotated_lines:
        brand, size = _line_brand_size(line)
        if match_pair_key(brand, size) != bucket_key:
            continue
        am = line.get("article_match") if isinstance(line.get("article_match"), dict) else {}
        if am.get("article_id") is not None:
            hits += 1
        else:
            misses += 1
    if hits and not misses:
        return "MATCH"
    if hits and misses:
        return "AM_PARTIAL"
    return "AM_UNMATCHED"


def build_am_only_match_payload(
    *,
    annotated_lines: list[dict[str, Any]],
    am_summary: dict[str, Any],
    so_pack: dict[str, Any],
) -> dict[str, Any]:
    """Rows compatible with Order Match UI — FO qty/value omitted."""
    bucketed = build_so_buckets_from_line_detail(annotated_lines)
    buckets = bucketed.get("buckets") or {}
    rows: list[dict[str, Any]] = []
    counts = {
        "MATCH": 0,
        "MATCH_FUZZY_BRAND": 0,
        "QTY_MISMATCH": 0,
        "VALUE_MISMATCH": 0,
        "MISSING_ON_SO": 0,
        "EXTRA_ON_SO": 0,
        "AM_UNMATCHED": 0,
        "AM_PARTIAL": 0,
    }

    for key in sorted(buckets.keys(), key=lambda k: (k[0], k[1])):
        bucket = buckets[key]
        brand = str(bucket.get("brand") or key[0])
        size = display_size_code(str(bucket.get("size") or key[1]))
        so_qty = float(bucket.get("qty") or 0)
        so_val = float(bucket.get("value") or 0)
        so_numbers = list(bucket.get("so_numbers") or [])
        by_so = bucket.get("by_so") or {}
        so_breakdown = [
            {
                "so_number": so_n,
                "qty": round(float(cell.get("qty") or 0), 3),
                "net": round(float(cell.get("net") or 0), 2),
                "gst": round(float(cell.get("gst") or 0), 2),
                "total": round(float(cell.get("total") or 0), 2),
            }
            for so_n, cell in sorted(by_so.items(), key=lambda x: str(x[0]))
        ]
        if not so_breakdown and so_numbers:
            split_n = max(1, len(so_numbers))
            for so_n in so_numbers:
                so_breakdown.append(
                    {
                        "so_number": so_n,
                        "qty": round(so_qty / split_n, 3),
                        "net": round(so_val / split_n, 2),
                        "gst": 0.0,
                        "total": round(so_val / split_n, 2),
                    }
                )

        status = _bucket_am_status(key, annotated_lines)
        if status == "AM_PARTIAL":
            counts["AM_PARTIAL"] += 1
            counts["QTY_MISMATCH"] += 1
        elif status == "MATCH":
            counts["MATCH"] += 1
        else:
            counts["AM_UNMATCHED"] += 1
            counts["EXTRA_ON_SO"] += 1

        rows.append(
            {
                "brand": brand,
                "size": size,
                "match_key_brand": key[0],
                "fo_qty": None,
                "so_qty": so_qty,
                "delta_qty": so_qty,
                "fo_exmill_value": None,
                "so_net_amount": so_val,
                "delta_value": so_val,
                "status": status,
                "so_numbers": so_numbers,
                "so_breakdown": so_breakdown,
            }
        )

    # Lines that never resolved Brand×Size still need By-SO cards
    # (otherwise SO qty shows in the header but "By SO (0)" / no line rows).
    covered = set(buckets.keys())
    orphan_by_so: dict[str, dict[str, float]] = {}
    for line in annotated_lines:
        brand, size = _line_brand_size(line)
        if brand and size and match_pair_key(brand, size) in covered:
            continue
        so_n = str(line.get("so_number") or "").strip() or "—"
        cell = orphan_by_so.setdefault(
            so_n, {"qty": 0.0, "net": 0.0, "gst": 0.0, "total": 0.0}
        )
        qty = float(line.get("qty") or 0)
        net = float(line.get("net_amount") or 0)
        gst = float(line.get("gst_amount") or 0)
        total = float(line.get("total_amount") or 0) or round(net + gst, 2)
        cell["qty"] = round(cell["qty"] + qty, 3)
        cell["net"] = round(cell["net"] + net, 2)
        cell["gst"] = round(cell["gst"] + gst, 2)
        cell["total"] = round(cell["total"] + total, 2)

    for so_n, cell in sorted(orphan_by_so.items(), key=lambda x: str(x[0])):
        so_qty = float(cell.get("qty") or 0)
        so_val = float(cell.get("net") or 0)
        if so_qty <= 0 and so_val <= 0:
            continue
        counts["AM_UNMATCHED"] += 1
        counts["EXTRA_ON_SO"] += 1
        nums = [] if so_n == "—" else [so_n]
        rows.append(
            {
                "brand": "Others",
                "size": "—",
                "match_key_brand": "others",
                "fo_qty": None,
                "so_qty": so_qty,
                "delta_qty": so_qty,
                "fo_exmill_value": None,
                "so_net_amount": so_val,
                "delta_value": so_val,
                "status": "AM_UNMATCHED",
                "so_numbers": nums,
                "so_breakdown": [
                    {
                        "so_number": so_n if so_n != "—" else None,
                        "qty": so_qty,
                        "net": so_val,
                        "gst": float(cell.get("gst") or 0),
                        "total": float(cell.get("total") or so_val),
                    }
                ],
            }
        )

    so_qty_t = round(float(bucketed.get("total_qty") or 0), 3)
    so_val_t = round(float(bucketed.get("total_value") or 0), 2)
    meta = so_pack.get("meta") if isinstance(so_pack.get("meta"), dict) else {}

    return {
        "match_mode": MATCH_MODE_AM_ONLY,
        "match": {
            "rows": rows,
            "counts": counts,
            "totals": {
                "fo_qty": None,
                "so_qty": so_qty_t,
                "delta_qty": so_qty_t,
                "fo_exmill_value": None,
                "so_net_amount": so_val_t,
                "delta_value": so_val_t,
            },
            "so_totals": _rollup_so_totals(rows),
        },
        "article_master_match": am_summary,
        "so_meta": meta,
    }


def _rollup_so_totals(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        for cell in r.get("so_breakdown") or []:
            so_n = str(cell.get("so_number") or "").strip()
            if not so_n:
                continue
            acc = out.setdefault(
                so_n, {"qty": 0.0, "net": 0.0, "gst": 0.0, "total": 0.0}
            )
            acc["qty"] = round(acc["qty"] + float(cell.get("qty") or 0), 3)
            acc["net"] = round(acc["net"] + float(cell.get("net") or 0), 2)
            acc["gst"] = round(acc["gst"] + float(cell.get("gst") or 0), 2)
            acc["total"] = round(acc["total"] + float(cell.get("total") or 0), 2)
    return out


def preview_so_article_master_match(
    conn,
    user_id: int,
    so_pack: dict[str, Any],
) -> dict[str, Any]:
    line_detail = prepare_so_lines_for_am_match(so_pack.get("line_detail"))
    if not line_detail:
        raise ValueError("SO pack has no line_detail — analyze the pack first")
    annotated, summary = annotate_ci_line_items_with_article_master(
        conn, amdb, user_id, line_detail
    )
    meta = so_pack.get("meta") if isinstance(so_pack.get("meta"), dict) else {}
    return {
        "article_master_match": summary,
        "line_count": len(annotated),
        "category_hint": meta.get("dominant_category"),
        "buyer_hint": meta.get("primary_buyer_name"),
        "so_meta": meta,
    }


def save_so_article_master_only(
    conn,
    user_id: int,
    *,
    so_pack: dict[str, Any],
    so_buyer_label: str | None = None,
    so_source_filename: str | None = None,
    distributor_id: int | None = None,
    distributor_name: str | None = None,
    category: str | None = None,
    season: str | None = None,
) -> dict[str, Any]:
    from app.services import fo_so_match_db as matchdb

    line_detail = prepare_so_lines_for_am_match(so_pack.get("line_detail"))
    if not line_detail:
        raise ValueError("SO pack has no line_detail — analyze the pack first")

    annotated, summary = annotate_ci_line_items_with_article_master(
        conn, amdb, user_id, line_detail
    )
    payload = build_am_only_match_payload(
        annotated_lines=annotated,
        am_summary=summary,
        so_pack=so_pack,
    )
    meta = so_pack.get("meta") if isinstance(so_pack.get("meta"), dict) else {}
    buyer = (so_buyer_label or meta.get("primary_buyer_name") or "").strip() or None
    cat = (category or meta.get("dominant_category") or "").strip() or None

    return matchdb.save_am_only_match_run(
        conn,
        user_id=user_id,
        match_payload=payload,
        so_buyer_label=buyer,
        so_source_filename=so_source_filename,
        so_line_detail=annotated,
        so_pack=so_pack,
        distributor_id=distributor_id,
        distributor_name=(distributor_name or buyer),
        category=cat,
        season=(season or "").strip() or None,
    )
