"""
Match Commercial Invoice / SO line item_keys to Article Master.

CI/SO PDFs use Brand|TC|Size keys (e.g. ASTER|100|DB). Article Master
item_key uses category key_fields (often brand|tc|size or brand|size|…)
so we compare with size_code_only normalization and also rebuild a
CI-style key from AM brand + TC + size.
"""

from __future__ import annotations

import re
from typing import Any

from order_item_keys import item_keys_match, size_code_only_item_key

_SIZE_CODE_RE = re.compile(r"\b(DB|SB|KS|KB)\b", re.I)
_TC_RE = re.compile(r"^(\d{2,3})$")


def _extras_dict(article: dict[str, Any]) -> dict[str, Any]:
    extras = article.get("extra_attributes") or {}
    return extras if isinstance(extras, dict) else {}


def _extract_tc_from_article(article: dict[str, Any]) -> str | None:
    extras = _extras_dict(article)
    for key, val in extras.items():
        if str(key).strip().lower() in {"tc", "thread count", "threadcount", "t.c."}:
            try:
                return str(int(float(str(val).strip())))
            except (TypeError, ValueError):
                text = str(val or "").strip()
                m = re.search(r"(\d{2,3})", text)
                return m.group(1) if m else None
    # Fall back: numeric segment inside AM item_key
    for part in str(article.get("item_key") or "").split("|"):
        part = part.strip()
        if _TC_RE.match(part):
            return part
    return None


def rebuild_ci_style_key_from_article(article: dict[str, Any]) -> str | None:
    """Brand|TC|Size-code key comparable to extract_order_sheet_item_key()."""
    brand = (article.get("brand") or "").strip().upper()
    if not brand:
        parts = str(article.get("item_key") or "").split("|")
        brand = (parts[0] or "").strip().upper() if parts else ""
    tc = _extract_tc_from_article(article)
    size_raw = (article.get("size") or "").strip().upper()
    size_match = _SIZE_CODE_RE.search(size_raw) if size_raw else None
    size = size_match.group(1).upper() if size_match else None
    if not size:
        for part in str(article.get("item_key") or "").split("|"):
            m = _SIZE_CODE_RE.search(part.upper())
            if m:
                size = m.group(1).upper()
                break
    if not brand or not tc or not size:
        return None
    return f"{brand}|{tc}|{size}"


def _public_article(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": article.get("id"),
        "brand": article.get("brand"),
        "size": article.get("size"),
        "category": article.get("category"),
        "product_type": article.get("product_type"),
        "item_key": article.get("item_key"),
        "mrp": article.get("mrp"),
        "ptr": article.get("ptr"),
        "ex_mill_price": article.get("ex_mill_price"),
    }


def match_ci_item_to_article(
    conn,
    amdb,
    user_id: int,
    *,
    item_key: str | None = None,
    item_name: str | None = None,
    articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Returns article_match payload for one CI/SO line.

    status: matched | unmatched | no_key | no_catalog
    """
    key = size_code_only_item_key(item_key) if item_key else None
    if not key:
        return {
            "status": "no_key",
            "message": "Could not build Brand|TC|Size key from CI description",
            "ci_item_key": item_key,
            "ci_item_name": item_name,
            "article_id": None,
            "article": None,
            "closest_article": None,
            "match_method": None,
        }

    catalog = articles
    if catalog is None:
        amdb.ensure_schema(conn)
        catalog = amdb.get_all_articles(conn, user_id, active_only=True)

    if not catalog:
        return {
            "status": "no_catalog",
            "message": "Article Master is empty for this user",
            "ci_item_key": key,
            "ci_item_name": item_name,
            "article_id": None,
            "article": None,
            "closest_article": None,
            "match_method": None,
        }

    for article in catalog:
        am_key = article.get("item_key")
        if am_key and item_keys_match(am_key, key):
            return {
                "status": "matched",
                "message": "Matched Article Master on item_key",
                "ci_item_key": key,
                "ci_item_name": item_name,
                "article_id": article.get("id"),
                "article": _public_article(article),
                "closest_article": None,
                "match_method": "item_key",
            }
        rebuilt = rebuild_ci_style_key_from_article(article)
        if rebuilt and item_keys_match(rebuilt, key):
            return {
                "status": "matched",
                "message": "Matched Article Master on brand+TC+size",
                "ci_item_key": key,
                "ci_item_name": item_name,
                "article_id": article.get("id"),
                "article": _public_article(article),
                "closest_article": None,
                "match_method": "brand_tc_size",
            }

    # Closest: same brand segment
    brand = key.split("|")[0].strip().upper()
    closest = None
    alias_map = amdb.get_brand_alias_map(conn, user_id)
    for article in catalog:
        am_brand = amdb.canonicalize_brand_name(article.get("brand") or "", alias_map)
        ci_brand = amdb.canonicalize_brand_name(brand, alias_map)
        if am_brand and ci_brand and am_brand.strip().upper() == ci_brand.strip().upper():
            closest = article
            break
        am_key_brand = str(article.get("item_key") or "").split("|")[0].strip().upper()
        am_key_brand = amdb.canonicalize_brand_name(am_key_brand, alias_map)
        if ci_brand and am_key_brand and ci_brand.strip().upper() == am_key_brand.strip().upper():
            closest = article
            break

    return {
        "status": "unmatched",
        "message": "Not found in Article Master",
        "ci_item_key": key,
        "ci_item_name": item_name,
        "article_id": None,
        "article": None,
        "closest_article": _public_article(closest) if closest else None,
        "match_method": None,
    }


def annotate_ci_line_items_with_article_master(
    conn,
    amdb,
    user_id: int,
    line_items: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annotate each CI line with article_match; return lines + summary."""
    lines = [dict(x) for x in (line_items or []) if isinstance(x, dict)]
    amdb.ensure_schema(conn)
    catalog = amdb.get_all_articles(conn, user_id, active_only=True)

    matched = unmatched = no_key = 0
    unmatched_preview: list[dict[str, Any]] = []

    for line in lines:
        result = match_ci_item_to_article(
            conn,
            amdb,
            user_id,
            item_key=line.get("item_key"),
            item_name=line.get("item_name"),
            articles=catalog,
        )
        line["article_match"] = result
        if result.get("article_id") is not None:
            line["article_id"] = result["article_id"]
        status = result.get("status")
        if status == "matched":
            matched += 1
        elif status == "no_key":
            no_key += 1
            if len(unmatched_preview) < 25:
                unmatched_preview.append({
                    "item_name": line.get("item_name"),
                    "item_key": line.get("item_key"),
                    "status": status,
                    "closest_item_key": (result.get("closest_article") or {}).get("item_key"),
                    "closest_brand": (result.get("closest_article") or {}).get("brand"),
                })
        else:
            unmatched += 1
            if len(unmatched_preview) < 25:
                unmatched_preview.append({
                    "item_name": line.get("item_name"),
                    "item_key": result.get("ci_item_key") or line.get("item_key"),
                    "status": status,
                    "closest_item_key": (result.get("closest_article") or {}).get("item_key"),
                    "closest_brand": (result.get("closest_article") or {}).get("brand"),
                })

    summary = {
        "total": len(lines),
        "matched": matched,
        "unmatched": unmatched,
        "no_key": no_key,
        "catalog_size": len(catalog),
        "unmatched_lines": unmatched_preview,
    }
    return lines, summary
