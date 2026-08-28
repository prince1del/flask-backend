"""DUMMY Match Lab — Filled Order vs SO Pack (Brand × Size).

Temporary compare engine for teaching / validation. Not the locked product flow.
FO ExMill Value (qty × file ExMill) is compared to SO Net Amount.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import article_master_parser as amparser
import filled_orders_parser as foparser
import pandas as pd

from app.services.bd_product_catalog import enrich_bd_product
from app.services.so_pack_consolidate import (
    analyze_so_pack,
    analyze_so_pack_pdfs,
    product_short_name,
)

QTY_TOL = 0.01
VALUE_TOL = 10.0  # ₹ floor — FO ExMill vs SO Net
VALUE_TOL_RATE = 0.005  # ±0.5% on large lines (FO bale→pcs exmill float vs SO net)

# Taught FO ↔ SO brand families (distributor wording vs BD collection name).
# Soft keys only — e.g. "Florentine / Allure" and "Allure" share "allure".
BRAND_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"allure", "florentine allure"}),
    frozenset({"bamboo", "nature s bqt", "natures bqt", "nature bqt"}),
    frozenset({"huk a buk", "huck a buck"}),
    frozenset({"rimzim cooltex", "cooltex"}),
)


def _soft_brand_raw(brand: str | None) -> str:
    """Collapse punctuation/hyphens; no alias teaching."""
    text = re.sub(r"[^a-z0-9]+", " ", (brand or "").lower())
    return " ".join(text.split())


def brand_match_keys(brand: str | None) -> set[str]:
    """All soft keys that should collide for this brand label.

    - Soft full string
    - Slash / ampersand parts: ``Florentine / Allure`` → florentine, allure
    - Taught alias groups
    """
    raw = _soft_brand_raw(brand)
    keys: set[str] = set()
    if raw:
        keys.add(raw)
    for part in re.split(r"[/|＆&／⁄∕]+", brand or ""):
        p = _soft_brand_raw(part)
        if p:
            keys.add(p)
    for group in BRAND_ALIAS_GROUPS:
        if keys & group:
            keys |= set(group)
    return keys


def soft_brand_key(brand: str | None) -> str:
    """Canonical brand key for Brand×Size bucketing (alias-aware)."""
    keys = brand_match_keys(brand)
    if not keys:
        return ""
    for group in BRAND_ALIAS_GROUPS:
        if keys & group:
            # Prefer the shortest taught name (Allure over Florentine Allure)
            return min(group, key=len)
    # Dual label without a taught group: prefer last slash part if present
    parts = [_soft_brand_raw(p) for p in re.split(r"[/|＆&／⁄∕]+", brand or "")]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        return parts[-1]
    return _soft_brand_raw(brand)


def brands_equivalent(a: str | None, b: str | None) -> bool:
    return bool(brand_match_keys(a) & brand_match_keys(b))


def soft_size_key(size: str | None) -> str:
    brand, size_n = amparser.normalize_brand_and_size(None, size)
    _ = brand
    return amparser._norm(size_n or size or "")


def display_size_code(size: str | None) -> str:
    """Always show short size codes in Order Match (KB FS, not King Fitted Sheet)."""
    code = amparser.normalize_size_code(size)
    if code:
        return str(code).strip()
    return (size or "").strip()


def match_pair_key(brand: str | None, size: str | None) -> tuple[str, str]:
    return soft_brand_key(brand), soft_size_key(size)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if not v:
                return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_so_number(so_number: Any) -> str | None:
    text = str(so_number or "").strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    return text


def _bucket_add(
    buckets: dict[tuple[str, str], dict[str, Any]],
    *,
    brand: str,
    size: str,
    qty: float | None,
    value: float | None,
    so_number: Any = None,
    gst_amount: float | None = None,
    total_amount: float | None = None,
) -> None:
    key = match_pair_key(brand, size)
    if not key[0] and not key[1]:
        return
    qty_f = float(qty or 0)
    val_f = float(value or 0)
    gst_f = float(gst_amount or 0)
    total_f = float(total_amount) if total_amount is not None else round(val_f + gst_f, 2)
    if abs(qty_f) < 1e-12 and abs(val_f) < 1e-12 and abs(total_f) < 1e-12:
        return
    size_code = display_size_code(size)
    row = buckets.get(key)
    if not row:
        buckets[key] = {
            "brand": (brand or "").strip(),
            "size": size_code,
            "qty": 0.0,
            "value": 0.0,
            "so_numbers": [],
            "by_so": {},
        }
        row = buckets[key]
    row["qty"] = round(row["qty"] + qty_f, 3)
    row["value"] = round(row["value"] + val_f, 2)
    # Prefer a richer display label if we later see one
    if brand and len(brand.strip()) > len(row["brand"]):
        row["brand"] = brand.strip()
    # Always keep short size code (KB FS), never long "King Fitted Sheet"
    if size_code:
        row["size"] = size_code
    so_n = _normalize_so_number(so_number)
    if so_n:
        nums = row.setdefault("so_numbers", [])
        if so_n not in nums:
            nums.append(so_n)
        by_so = row.setdefault("by_so", {})
        cell = by_so.get(so_n)
        if not cell:
            cell = {"qty": 0.0, "net": 0.0, "gst": 0.0, "total": 0.0}
            by_so[so_n] = cell
        cell["qty"] = round(float(cell["qty"]) + qty_f, 3)
        cell["net"] = round(float(cell["net"]) + val_f, 2)
        cell["gst"] = round(float(cell["gst"]) + gst_f, 2)
        cell["total"] = round(float(cell["total"]) + total_f, 2)


def build_fo_buckets_from_workbook(
    path: str | Path,
    *,
    category: str | None = None,
    pref_column_name: str | None = None,
) -> dict[str, Any]:
    """Parse FO Excel using filled-order rules; value = qty × *file* ExMill (not AM)."""
    path = Path(path)
    cat = category or foparser.detect_category_from_order_file(str(path), filename=path.name)
    if not cat:
        cat = amparser.detect_category([], filename=path.name) or "Bedsheet"

    workbook = foparser.parse_filled_order_workbook(
        str(path), cat, pref_column_name=pref_column_name,
    )
    if workbook.get("status") == "qty_column_confirmation_required":
        return {
            "status": "qty_column_confirmation_required",
            "category": cat,
            "qty_detection": workbook.get("qty_detection"),
            "sheet_name": workbook.get("sheet_name"),
            "buckets": {},
        }

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    qty_label = workbook.get("quantity_column_used")
    for parsed in workbook.get("parsed_rows") or []:
        cf = dict(parsed.get("core_fields") or {})
        brand, size = amparser.normalize_brand_and_size(cf.get("brand"), cf.get("size"))
        ex_mill = _safe_float(cf.get("ex_mill_price"))
        bale = _safe_float(cf.get("bale_pack_size"))
        resolved = foparser.apply_qty_bales_value_rules(
            raw_qty=parsed.get("raw_qty_value"),
            sheet_bales=parsed.get("sheet_bales"),
            bale_size=bale,
            ex_mill=ex_mill,
            qty_column_label=qty_label,
            category=cat,
        )
        _bucket_add(
            buckets,
            brand=brand or cf.get("brand") or "",
            size=size or cf.get("size") or "",
            qty=resolved.get("final_piece_qty"),
            value=resolved.get("line_value"),
        )

    return {
        "status": "ok",
        "category": cat,
        "quantity_column_used": qty_label,
        "buckets": buckets,
        "line_count": len(buckets),
        "total_qty": round(sum(b["qty"] for b in buckets.values()), 3),
        "total_value": round(sum(b["value"] for b in buckets.values()), 2),
    }


def so_line_texts(row: dict[str, Any]) -> list[str]:
    """Every product wording of one SO line, best teaching candidate first.

    Bed SO PDFs carry the resolvable wording in `product_name`
    ("BLUMEN 1+2 DB SET") while `product_detail` appends size / design / colour
    / TC ("… 224X254 8136LBL 104TC"), which breaks the set-type lookup. Towel SO
    PDFs are the other way round — the size only exists in the detail
    ("SANTINO PRE DYED 2PC" vs "… 40X60CM ASST12 AW26"). So try both wordings
    (plus their short forms) instead of guessing from string length.
    """
    detail = str(row.get("product_detail") or "").strip()
    name = str(row.get("product_name") or "").strip()
    out: list[str] = []
    for text in (
        detail,
        product_short_name(detail) if detail else "",
        name,
        product_short_name(name) if name else "",
    ):
        candidate = (text or "").strip()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def resolve_so_line_brand_size(
    row: dict[str, Any]
) -> tuple[str | None, str | None, str]:
    """Resolve (brand, size, source_text) for one SO line via the teaching maps.

    A fully resolved wording always wins; the longer detail wording is tried
    first so towel SKUs keep resolving exactly as before.
    """
    best_partial: tuple[str | None, str | None, str] = (None, None, "")
    mat_code = str(row.get("material_code") or "").strip() or None
    for text in so_line_texts(row):
        enriched = enrich_bd_product(text, material_code=mat_code)
        brand = enriched.get("collection")
        size = enriched.get("product_type")
        if brand and size:
            return str(brand), str(size), text
        if (brand or size) and not (best_partial[0] or best_partial[1]):
            best_partial = (brand, size, text)
    return best_partial


def _unmapped_so_key(row: dict[str, Any], brand: str | None) -> tuple[str, str]:
    """Readable Brand × Size label for an SO line the teaching maps do not know.

    Such a line must still reach the compare: silently folding it into "Others"
    is what made a whole SO Pack report SO qty 0 with every FO bucket
    MISSING_ON_SO while the lines were sitting in the run.
    """
    texts = so_line_texts(row)
    shortest = min(texts, key=len) if texts else ""
    label = str(brand or "").strip() or shortest or str(
        row.get("material_code") or ""
    ).strip() or "Unknown"
    size_code = amparser.normalize_size_code(shortest) or ""
    return label, str(size_code or "Unmapped")


def build_so_buckets_from_line_detail(line_detail: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    unmapped_qty = 0.0
    unmapped_net = 0.0
    unmapped_lines = 0
    for row in line_detail or []:
        brand, size, _source = resolve_so_line_brand_size(row)
        qty = _safe_float(row.get("qty")) or 0.0
        net = _safe_float(row.get("net_amount")) or 0.0
        gst = _safe_float(row.get("gst_amount")) or 0.0
        total = _safe_float(row.get("total_amount"))
        if total is None:
            total = round(net + gst, 2)
        if not (brand and size):
            # Keep the line in the compare under its own wording, flagged as
            # unmapped, so its qty and value are never lost.
            brand, size = _unmapped_so_key(row, brand)
            unmapped_qty += qty
            unmapped_net += net
            unmapped_lines += 1
        _bucket_add(
            buckets,
            brand=str(brand),
            size=str(size),
            qty=qty,
            value=net,
            so_number=row.get("so_number"),
            gst_amount=gst,
            total_amount=total,
        )

    return {
        "status": "ok",
        "buckets": buckets,
        "others_qty": round(unmapped_qty, 3),
        "others_net": round(unmapped_net, 2),
        "unmapped_line_count": unmapped_lines,
        "line_count": len(buckets),
        "total_qty": round(sum(b["qty"] for b in buckets.values()), 3),
        "total_value": round(sum(b["value"] for b in buckets.values()), 2),
    }


def build_so_buckets_from_pack_xlsx(path: str | Path) -> dict[str, Any]:
    """Read Brand Wise Size Wise Summary from an already-downloaded SO Pack Excel."""
    path = Path(path)
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet_name = None
    for name in xl.sheet_names:
        if "brand wise size" in str(name).lower():
            sheet_name = name
            break
    if not sheet_name:
        raise ValueError(
            "SO Pack Excel needs a 'Brand Wise Size Wise Summary' sheet "
            "(download from SO Pack Analyze first)."
        )

    raw = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")
    header_idx = None
    for i in range(min(8, len(raw))):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist() if pd.notna(v)]
        if "brand" in vals and any("qty" in v for v in vals):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find Brand/Qty headers on Brand Wise Size Wise sheet")

    headers = [str(v).strip() if pd.notna(v) else "" for v in raw.iloc[header_idx].tolist()]
    col = {amparser._norm(h): idx for idx, h in enumerate(headers) if h}
    brand_i = col.get("brand")
    size_i = col.get("sheet option") if "sheet option" in col else col.get("size")
    qty_i = next((i for k, i in col.items() if "qty" in k and "design" not in k), None)
    net_i = next((i for k, i in col.items() if "net" in k), None)
    if brand_i is None or size_i is None or qty_i is None:
        raise ValueError("Brand / Sheet Option / Qty columns missing on SO Pack sheet")

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in raw.iloc[header_idx + 1 :].iterrows():
        brand = str(row.iloc[brand_i]).strip() if pd.notna(row.iloc[brand_i]) else ""
        if not brand or brand.lower() in ("others", "total", "nan"):
            continue
        size = str(row.iloc[size_i]).strip() if pd.notna(row.iloc[size_i]) else ""
        qty = _safe_float(row.iloc[qty_i])
        net = _safe_float(row.iloc[net_i]) if net_i is not None else None
        _bucket_add(buckets, brand=brand, size=size, qty=qty, value=net)

    return {
        "status": "ok",
        "source": "so_pack_xlsx",
        "sheet_name": sheet_name,
        "buckets": buckets,
        "line_count": len(buckets),
        "total_qty": round(sum(b["qty"] for b in buckets.values()), 3),
        "total_value": round(sum(b["value"] for b in buckets.values()), 2),
    }


def build_so_buckets_from_archive_or_pdfs(
    *,
    mode: str,
    label: str,
    payload: Any,
) -> dict[str, Any]:
    if mode == "pdfs":
        data = analyze_so_pack_pdfs(payload, label)
    else:
        data = analyze_so_pack(payload, label)
    built = build_so_buckets_from_line_detail(data.get("line_detail") or [])
    built["so_meta"] = data.get("meta") or {}
    built["source"] = "so_pack_analyze"
    return built


def compare_fo_so_buckets(
    fo_buckets: dict[tuple[str, str], dict[str, Any]],
    so_buckets: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    from difflib import SequenceMatcher

    fo = dict(fo_buckets)
    so = dict(so_buckets)

    # Second pass: same size + brand alias / near-spell (Blumen≈Bluemen,
    # Florentine/Allure≈Allure when keys did not already canonicalize together)
    fo_only = [k for k in fo if k not in so]
    so_only = [k for k in so if k not in fo]
    used_so: set[tuple[str, str]] = set()
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for fk in fo_only:
        best = None
        best_ratio = 0.0
        fo_brand = (fo.get(fk) or {}).get("brand") or fk[0]
        for sk in so_only:
            if sk in used_so:
                continue
            if fk[1] != sk[1]:  # size must already soft-match
                continue
            so_brand = (so.get(sk) or {}).get("brand") or sk[0]
            if brands_equivalent(fo_brand, so_brand) or brands_equivalent(fk[0], sk[0]):
                ratio = 1.0
            else:
                ratio = SequenceMatcher(None, fk[0], sk[0]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = sk
        if best and best_ratio >= 0.86:
            aliases[fk] = best
            used_so.add(best)

    # Merge fuzzy pairs into FO key space for a single compare row
    merged_so: dict[tuple[str, str], dict[str, Any]] = dict(so)
    fuzzy_flags: set[tuple[str, str]] = set()
    for fk, sk in aliases.items():
        merged_so[fk] = so[sk]
        merged_so.pop(sk, None)
        fuzzy_flags.add(fk)

    keys = sorted(set(fo) | set(merged_so), key=lambda k: (k[0], k[1]))
    rows: list[dict[str, Any]] = []
    counts = {
        "MATCH": 0,
        "MATCH_FUZZY_BRAND": 0,
        "QTY_MISMATCH": 0,
        "VALUE_MISMATCH": 0,
        "MISSING_ON_SO": 0,
        "EXTRA_ON_SO": 0,
    }

    for key in keys:
        fo_row = fo.get(key)
        so_row = merged_so.get(key)
        fo_qty = float((fo_row or {}).get("qty") or 0)
        so_qty = float((so_row or {}).get("qty") or 0)
        fo_val = float((fo_row or {}).get("value") or 0)
        so_val = float((so_row or {}).get("value") or 0)
        brand = (fo_row or so_row or {}).get("brand") or key[0]
        size = display_size_code(
            (fo_row or so_row or {}).get("size") or key[1]
        )
        so_brand_raw = (so_row or {}).get("brand")
        fo_brand_raw = (fo_row or {}).get("brand")
        d_qty = round(so_qty - fo_qty, 3)
        d_val = round(so_val - fo_val, 2)
        so_numbers = list((so_row or {}).get("so_numbers") or [])
        by_so = (so_row or {}).get("by_so") or {}
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
        # Legacy rows without by_so: approximate one bucket per listed SO number.
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

        if fo_row and not so_row:
            status = "MISSING_ON_SO"
        elif so_row and not fo_row:
            status = "EXTRA_ON_SO"
        elif abs(d_qty) > QTY_TOL:
            status = "QTY_MISMATCH"
        elif abs(d_val) > max(
            VALUE_TOL, VALUE_TOL_RATE * max(abs(fo_val), abs(so_val), 1.0)
        ):
            status = "VALUE_MISMATCH"
        elif key in fuzzy_flags or (
            fo_brand_raw
            and so_brand_raw
            and _soft_brand_raw(fo_brand_raw) != _soft_brand_raw(so_brand_raw)
            and brands_equivalent(fo_brand_raw, so_brand_raw)
        ):
            status = "MATCH_FUZZY_BRAND"
            if so_brand_raw and fo_brand_raw and so_brand_raw != fo_brand_raw:
                brand = f"{fo_brand_raw} ≈ {so_brand_raw}"
            elif so_brand_raw and so_brand_raw != brand:
                brand = f"{brand} ≈ {so_brand_raw}"
        else:
            status = "MATCH"

        counts[status] = counts.get(status, 0) + 1
        rows.append(
            {
                "brand": brand,
                "size": size,
                "match_key_brand": key[0],
                "fo_qty": fo_qty,
                "so_qty": so_qty,
                "delta_qty": d_qty,
                "fo_exmill_value": fo_val,
                "so_net_amount": so_val,
                "delta_value": d_val,
                "status": status,
                "so_numbers": so_numbers,
                "so_breakdown": so_breakdown,
            }
        )

    fo_qty_t = round(sum(b["qty"] for b in fo.values()), 3)
    so_qty_t = round(sum(b["qty"] for b in so.values()), 3)
    fo_val_t = round(sum(b["value"] for b in fo.values()), 2)
    so_val_t = round(sum(b["value"] for b in so.values()), 2)

    # Per-SO rollups for mobile headers (qty / net / final incl. tax).
    so_totals: dict[str, dict[str, float]] = {}
    for r in rows:
        for cell in r.get("so_breakdown") or []:
            so_n = str(cell.get("so_number") or "").strip()
            if not so_n:
                continue
            acc = so_totals.setdefault(
                so_n, {"qty": 0.0, "net": 0.0, "gst": 0.0, "total": 0.0, "exmill": 0.0}
            )
            acc["qty"] = round(acc["qty"] + float(cell.get("qty") or 0), 3)
            acc["net"] = round(acc["net"] + float(cell.get("net") or 0), 2)
            acc["gst"] = round(acc["gst"] + float(cell.get("gst") or 0), 2)
            acc["total"] = round(acc["total"] + float(cell.get("total") or 0), 2)
        # Attribute FO ExMill to SOs proportionally by SO qty on that line.
        breakdown = r.get("so_breakdown") or []
        fo_ex = float(r.get("fo_exmill_value") or 0)
        so_line_qty = sum(float(c.get("qty") or 0) for c in breakdown) or 0.0
        if fo_ex and breakdown:
            for cell in breakdown:
                so_n = str(cell.get("so_number") or "").strip()
                if not so_n or so_n not in so_totals:
                    continue
                share = (
                    float(cell.get("qty") or 0) / so_line_qty
                    if so_line_qty > 1e-12
                    else (1.0 / len(breakdown))
                )
                so_totals[so_n]["exmill"] = round(
                    so_totals[so_n]["exmill"] + fo_ex * share, 2
                )

    return {
        "dummy": True,
        "grain": "brand_x_size",
        "compare": {"qty": True, "fo_exmill_vs_so_net": True},
        "rows": rows,
        "counts": counts,
        "so_totals": so_totals,
        "totals": {
            "fo_qty": fo_qty_t,
            "so_qty": so_qty_t,
            "delta_qty": round(so_qty_t - fo_qty_t, 3),
            "fo_exmill_value": fo_val_t,
            "so_net_amount": so_val_t,
            "delta_value": round(so_val_t - fo_val_t, 2),
        },
    }


def run_match_lab_files(
    *,
    fo_path: str | Path,
    so_path: str | Path | None = None,
    so_mode: str | None = None,
    so_label: str | None = None,
    so_payload: Any = None,
    category: str | None = None,
    pref_column_name: str | None = None,
) -> dict[str, Any]:
    """Run dummy match from FO path + SO archive/pdfs OR SO Pack xlsx path."""
    fo = build_fo_buckets_from_workbook(
        fo_path, category=category, pref_column_name=pref_column_name,
    )
    if fo.get("status") == "qty_column_confirmation_required":
        return {"success": False, "status": "qty_column_confirmation_required", "fo": fo}

    if so_path is not None:
        so_path = Path(so_path)
        suffix = so_path.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            so = build_so_buckets_from_pack_xlsx(so_path)
        elif suffix in (".zip", ".rar"):
            so = build_so_buckets_from_archive_or_pdfs(
                mode="single",
                label=so_path.name,
                payload=so_path.read_bytes(),
            )
        else:
            raise ValueError("SO side: use ZIP/RAR, PDF(s), or SO Pack .xlsx")
    elif so_mode and so_payload is not None:
        so = build_so_buckets_from_archive_or_pdfs(
            mode=so_mode, label=so_label or "SO", payload=so_payload,
        )
    else:
        raise ValueError("SO pack file is required")

    compared = compare_fo_so_buckets(fo["buckets"], so["buckets"])
    return {
        "success": True,
        "dummy": True,
        "fo": {
            "category": fo.get("category"),
            "quantity_column_used": fo.get("quantity_column_used"),
            "line_count": fo.get("line_count"),
            "total_qty": fo.get("total_qty"),
            "total_value": fo.get("total_value"),
        },
        "so": {
            "source": so.get("source"),
            "sheet_name": so.get("sheet_name"),
            "line_count": so.get("line_count"),
            "total_qty": so.get("total_qty"),
            "total_value": so.get("total_value"),
            "others_qty": so.get("others_qty"),
            "others_net": so.get("others_net"),
            "meta": so.get("so_meta"),
        },
        "match": compared,
    }


def build_fo_buckets_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Brand×Size buckets from saved filled_order_items (DB).

    Value = final_piece_qty × ex_mill_price (Article Master snapshot on save).
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for it in items or []:
        brand, size = amparser.normalize_brand_and_size(it.get("brand"), it.get("size"))
        qty = _safe_float(it.get("final_piece_qty"))
        ex = _safe_float(it.get("ex_mill_price"))
        value = (qty * ex) if (qty is not None and ex is not None) else None
        _bucket_add(
            buckets,
            brand=brand or it.get("brand") or "",
            size=size or it.get("size") or "",
            qty=qty,
            value=value,
        )
    return {
        "status": "ok",
        "buckets": buckets,
        "line_count": len(buckets),
        "total_qty": round(sum(b["qty"] for b in buckets.values()), 3),
        "total_value": round(sum(b["value"] for b in buckets.values()), 2),
    }


def score_fo_for_buyer(order: dict[str, Any], buyer_label: str | None) -> float:
    """Higher = better FO candidate for this SO Pack buyer name."""
    from difflib import SequenceMatcher

    buyer = soft_brand_key(buyer_label or "")
    if not buyer:
        return 0.0
    names = [
        order.get("distributor_name_raw"),
        order.get("distributor_name"),
        order.get("firm_name"),
        order.get("firm_nick_name"),
    ]
    best = 0.0
    for name in names:
        key = soft_brand_key(str(name or ""))
        if not key:
            continue
        if key in buyer or buyer in key:
            best = max(best, 0.95)
        best = max(best, SequenceMatcher(None, key, buyer).ratio())
    return best


def run_match_saved_fo_vs_so_pack(
    *,
    fo_meta: dict[str, Any],
    fo_items: list[dict[str, Any]],
    so_pack_payload: dict[str, Any],
) -> dict[str, Any]:
    fo = build_fo_buckets_from_items(fo_items)
    so = build_so_buckets_from_line_detail(so_pack_payload.get("line_detail") or [])
    so["source"] = "so_pack_analyze"
    so["so_meta"] = so_pack_payload.get("meta") or {}
    compared = compare_fo_so_buckets(fo["buckets"], so["buckets"])
    return {
        "success": True,
        "dummy": False,
        "fo": {
            "id": fo_meta.get("id"),
            "distributor_id": fo_meta.get("distributor_id"),
            "distributor_name_raw": fo_meta.get("distributor_name_raw"),
            "category": fo_meta.get("category"),
            "season": fo_meta.get("season"),
            "source_filename": fo_meta.get("source_filename"),
            "quantity_column_used": fo_meta.get("quantity_column_used"),
            "line_count": fo.get("line_count"),
            "total_qty": fo.get("total_qty"),
            "total_value": fo.get("total_value"),
        },
        "so": {
            "source": so.get("source"),
            "line_count": so.get("line_count"),
            "total_qty": so.get("total_qty"),
            "total_value": so.get("total_value"),
            "others_qty": so.get("others_qty"),
            "others_net": so.get("others_net"),
            "meta": so.get("so_meta"),
        },
        "match": compared,
    }


def write_upload_to_temp(file_storage, suffix: str | None = None) -> Path:
    name = getattr(file_storage, "filename", None) or "upload"
    suf = suffix or Path(name).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suf) as tmp:
        tmp.write(file_storage.read())
        return Path(tmp.name)
