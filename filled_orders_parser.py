"""
Distributor Filled-Order Matching — Parser + Quantity-Column Detection

Reuses Article Master's header/category/item-key logic (article_master_parser.py)
and adds quantity-column detection, qty/bales/value rules, and Article Master match.

Locked teaching rules (Phase 2):
  1. Qty + Bales both present -> focus Qty; check bales == qty/bale_size;
     mismatch -> highlight (no silent fix).
  2. Only Bales -> Qty = Bales * Bale Size.
  3. Value always = Qty * ExMill.
  Qty detect prefers: Revised Order / Order In Pc's / Qnty / Additional Order Qty.
  Never treat as qty: Qnty Per Color, Qnty pre Design, Value, Asst.
  Empty Qnty header may shift right (DCA) but never into a Value column.

Verified against real distributor files across Bed/Bath categories:
  - Standard column, pieces (kag / Choice Corner)
  - Revised Order final qty (kag.xlsx)
  - Order In Bales + Order In Pc's (savitri steel)
  - Standard column empty, data shifted (DCA_Order.xlsx)
  - Multiple candidates, derived sum (BND single-sheet Additional Order Qty)
  - Multi-tab club (BND.xlsx ``base order`` Qty + ``additional order`` Additional quantity)
  - Monthly split + TOTAL (any months, e.g. BALAJI JULY/AUG or SEP/OCT/…) → TOTAL as Qty
"""

import math
import re
from pathlib import Path

import pandas as pd

import article_master_parser as amparser

QTY_COLUMN_ALIASES = {
    # Prefer final/piece columns. Do NOT put bare "qty" here — BND sheets use
    # both Qty and Additional Order Qty; bare "qty" would steal the total.
    "Bed": ["revised order", "order in pcs", "order in pc's", "order in pc s", "order in pieces", "qnty"],
    "Bath": ["qty in bales"],
    "TOB": ["booking qnty"],
    "Pillow": ["awds order in no of bales"],
}

# Headers that look qty-ish but are NEVER the order quantity.
QTY_NOISE_HEADERS = {
    "qnty per color",
    "qty per color",
    "qnty pre design",
    "qnty per design",
    "qty per design",
    "asst",
    "min bale pack",
}

# Separate "No of Bales / Order In Bales" column (checked against piece qty).
BALES_COLUMN_ALIASES = [
    "order in bales",
    "no of bales",
    "no. of bales",
    "number of bales",
]

# Prefer these even when sparsely filled (blank = not ordered).
PREFERRED_FINAL_QTY_HEADERS = [
    "revised order",
    "order in pcs",
    "order in pc's",
    "order in pc s",
    "order in pieces",
    "additional order qty",
    "additional quantity",
    "total qty",
    "total quantity",
    "total pcs",
]

# Calendar-month headers used as order splits (BALAJI / similar booking forms).
# Exact token match only — NOT "Aug - Sep Delivery".
_MONTH_QTY_HEADERS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august",
    "sep", "sept", "september", "oct", "october", "nov", "november",
    "dec", "december",
}

# Order-total column when months are split across columns.
_ORDER_TOTAL_QTY_HEADERS = {
    "total",
    "totals",
    "grand total",
    "order total",
    "total qty",
    "total qnty",
    "total quantity",
    "total order",
    "total pcs",
    "total pieces",
}

# Sheet tabs that are never filled-order line data.
NON_ORDER_SHEET_HINTS = (
    "summary", "readme", "cover", "index", "pivot", "instruction", "note",
)

_UNLABELED_RE = re.compile(r"^Column (\d+) \(unlabeled\)$")


def is_bale_count_quantity_column(
    qty_column_label: str | None, category: str | None = None
) -> bool:
    """True when the confirmed qty column is explicitly a bale count (Towel/Bath booking sheets)."""
    norm = amparser._norm(qty_column_label or "")
    if not norm:
        return False
    if "bales" in norm:
        return True
    if category:
        for alias in QTY_COLUMN_ALIASES.get(category, []):
            if amparser._norm(alias) == norm and "bales" in amparser._norm(alias):
                return True
    return False


def _safe_float(val):
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _column_label(header_row, idx):
    raw = str(header_row[idx]).strip() if idx < len(header_row) else ""
    if raw and raw.lower() != "nan":
        return raw
    return f"Column {idx + 1} (unlabeled)"


def _looks_like_serial_number(values):
    """Detects a 1,2,3,... row-index style column (e.g. 'SL NO') so it never
    gets mistaken for a real quantity candidate."""
    if len(values) < 3:
        return False
    diffs = [b - a for a, b in zip(values, values[1:])]
    return all(abs(d - 1) < 1e-9 for d in diffs)


def normalize_quantity(
    raw_qty,
    bale_size,
    *,
    qty_column_label: str | None = None,
    category: str | None = None,
):
    """
    Normalize distributor-entered quantity to pieces.

    GT Towel booking sheets use an explicit ``Qty in Bales`` column — those
    values are ALWAYS bale counts (30 bales × 24 pack = 720 pcs), even when the
    bale count is larger than the pack size.

    Bed / legacy sheets without a bale column name keep the older heuristic:
    values smaller than bale pack are treated as bale counts, otherwise pieces.
    """
    if raw_qty is None:
        return "pieces", None
    if is_bale_count_quantity_column(qty_column_label, category) and bale_size:
        return "bales", raw_qty * bale_size
    if bale_size and raw_qty < bale_size:
        return "bales", raw_qty * bale_size
    return "pieces", raw_qty


def is_clean_bale_multiple(final_piece_qty, bale_size, tolerance=1e-6):
    if not bale_size:
        return True
    remainder = final_piece_qty % bale_size
    return remainder <= tolerance or (bale_size - remainder) <= tolerance


def is_qty_noise_header(label: str | None) -> bool:
    """True for columns that must never be treated as order qty."""
    norm = amparser._norm(label or "")
    if not norm:
        return False
    if norm in QTY_NOISE_HEADERS:
        return True
    if "per color" in norm or "pre design" in norm or "per design" in norm:
        return True
    if "value" in norm or norm in {"val", "awd value"}:
        return True
    if "delivery" in norm:
        return True  # "Aug - Sep Delivery" = design window, not order qty
    if norm == "asst":
        return True
    return False


def is_month_split_qty_header(label: str | None) -> bool:
    """True for a bare month name used as an order-qty split column."""
    norm = amparser._norm(label or "")
    return norm in _MONTH_QTY_HEADERS


def is_order_total_qty_header(label: str | None) -> bool:
    """True for TOTAL / Grand Total style order-qty columns."""
    norm = amparser._norm(label or "")
    return norm in _ORDER_TOTAL_QTY_HEADERS


def is_bales_column_header(label: str | None) -> bool:
    """True for an explicit bale-count column (not 'Qty in Bales' Bath booking)."""
    norm = amparser._norm(label or "")
    if not norm or is_qty_noise_header(label):
        return False
    if norm in {amparser._norm(a) for a in BALES_COLUMN_ALIASES}:
        return True
    # "No of Bales" style — but not Bath's primary "Qty in Bales"
    if "bale" in norm and "size" not in norm and "pack" not in norm:
        if "qty" in norm or "qnty" in norm:
            return False  # "Qty in Bales" is the qty column for Bath
        return True
    return False


def apply_qty_bales_value_rules(
    *,
    raw_qty,
    sheet_bales,
    bale_size,
    ex_mill,
    qty_column_label: str | None = None,
    category: str | None = None,
):
    """
    Locked filled-order teaching rules:

    1. Qty + Bales both present -> focus Qty (pieces). Check
       sheet_bales == qty / bale_size. Mismatch -> highlight, do NOT silent-fix.
    2. Only Bales -> Qty = Bales * Bale Size.
    3. Value always = Qty * ExMill.
    """
    bale_size_f = _safe_float(bale_size)
    ex_f = _safe_float(ex_mill)
    sheet_bales_f = _safe_float(sheet_bales)
    raw_qty_f = _safe_float(raw_qty)

    qty_is_bale_count = is_bale_count_quantity_column(qty_column_label, category)

    # Case: qty column itself is a bale count (Bath / only-bales sheet)
    if qty_is_bale_count and raw_qty_f is not None:
        final_qty = (raw_qty_f * bale_size_f) if bale_size_f else raw_qty_f
        detected_unit = "bales"
        expected_bales = raw_qty_f
        bale_mismatch = False
        mismatch_detail = None
    elif raw_qty_f is not None and sheet_bales_f is not None and bale_size_f:
        # Rule 1: both present — focus piece qty
        final_qty = raw_qty_f
        detected_unit = "pieces"
        expected_bales = final_qty / bale_size_f
        bale_mismatch = abs(sheet_bales_f - expected_bales) >= 0.01
        mismatch_detail = (
            (
                f"Sheet bales {sheet_bales_f:g} do not match Qty {final_qty:g} "
                f"/ Bale Size {bale_size_f:g} = {expected_bales:g} "
                f"(difference {sheet_bales_f - expected_bales:g})"
            )
            if bale_mismatch
            else None
        )
    elif raw_qty_f is None and sheet_bales_f is not None and bale_size_f:
        # Rule 2: only bales
        final_qty = sheet_bales_f * bale_size_f
        detected_unit = "bales"
        expected_bales = sheet_bales_f
        bale_mismatch = False
        mismatch_detail = None
    elif raw_qty_f is not None:
        # Only qty (or both without usable bale size) — do not invent bales
        if bale_size_f and raw_qty_f < bale_size_f and not qty_is_bale_count:
            # Legacy heuristic for unlabeled small numbers as bale counts
            final_qty = raw_qty_f * bale_size_f
            detected_unit = "bales"
            expected_bales = raw_qty_f
        else:
            final_qty = raw_qty_f
            detected_unit = "pieces"
            expected_bales = (final_qty / bale_size_f) if bale_size_f else None
        bale_mismatch = False
        mismatch_detail = None
    else:
        final_qty = None
        detected_unit = "pieces"
        expected_bales = None
        bale_mismatch = False
        mismatch_detail = None

    line_value = (
        (final_qty * ex_f) if (final_qty is not None and ex_f is not None) else None
    )
    clean = is_clean_bale_multiple(final_qty, bale_size_f) if final_qty is not None else True

    return {
        "detected_unit": detected_unit,
        "final_piece_qty": final_qty,
        "sheet_bales": sheet_bales_f,
        "expected_bales": expected_bales,
        "bale_qty_mismatch": bale_mismatch,
        "bale_mismatch_detail": mismatch_detail,
        "line_value": line_value,
        "is_clean_bale_multiple": clean,
    }


def _find_header_index(header_row, aliases):
    alias_set = {amparser._norm(a) for a in aliases}
    for idx, raw in enumerate(header_row):
        if amparser._norm(raw) in alias_set:
            return idx
    return None


def detect_bales_column(header_row, col_mapping=None, qty_col_idx=None):
    """Find a separate No-of-Bales / Order-In-Bales column (not the qty column)."""
    core_mapped = {idx for idx, f in (col_mapping or {}).items() if f}
    for idx, raw in enumerate(header_row):
        if idx in core_mapped or idx == qty_col_idx:
            continue
        label = _column_label(header_row, idx)
        if is_bales_column_header(label):
            return {"column_index": idx, "column_label": label}
    return None


def _closest_article_hint(conn, amdb, user_id, category, item, key_fields):
    """Best-effort hint when a row did not match Article Master."""
    if not category:
        return None
    articles = amdb.get_articles_by_category(conn, user_id, category)
    if not articles:
        return f"No articles in Article Master for category '{category}' — upload Article Master first."

    brand = (item.get("brand") or "").strip().lower()
    size = (item.get("size") or "").strip().lower()
    product = (item.get("product_type") or "").strip().lower()
    if not brand:
        return "Brand is empty in the file row — check the Excel column mapping."

    same_brand = [
        a for a in articles
        if amparser.brands_match_fuzzy(item.get("brand"), a.get("brand"))
    ]
    if not same_brand:
        known_brands = sorted({a.get("brand") for a in articles if a.get("brand")})[:8]
        close = next(
            (b for b in known_brands if amparser.brands_match_fuzzy(item.get("brand"), b)),
            None,
        )
        if close:
            return (
                f"File brand '{item.get('brand')}' is close to Article Master brand '{close}' "
                f"— check spelling (e.g. Blumen vs Bluemen)."
            )
        return f"Brand '{item.get('brand')}' not in Article Master for category '{category}'."

    if size:
        file_size_norm = amparser.normalize_key_part_value("size", item.get("size"))
        same_size = [
            a for a in same_brand
            if amparser.normalize_key_part_value("size", a.get("size")) == file_size_norm
        ]
        if not same_size:
            known_sizes = sorted({a.get("size") for a in same_brand if a.get("size")})[:8]
            return (
                f"Brand '{item.get('brand')}' exists but size '{item.get('size')}' was not found. "
                f"Known sizes: {', '.join(known_sizes)}"
            )
        if product:
            same_product = [
                a for a in same_size
                if (a.get("product_type") or "").strip().lower() == product
            ]
            if not same_product:
                known_products = sorted({a.get("product_type") for a in same_size if a.get("product_type")})[:5]
                return (
                    f"Brand+size match exists but product '{item.get('product_type')}' was not found. "
                    f"Known products: {', '.join(known_products)}"
                )
    return "Row fields do not match any Article Master item — check spelling or add to Article Master."


def _find_closest_article_for_preview(conn, amdb, user_id, category, core_fields, extra_attributes, key_fields):
    """Best Article Master row to compare against when previewing an unmatched line."""
    if not category:
        return None
    articles = amdb.get_articles_by_category(conn, user_id, category)
    if not articles:
        return None

    file_brand = core_fields.get("brand")
    file_size = core_fields.get("size")
    scored = []
    for article in articles:
        score = 0
        if amparser.brands_match_fuzzy(file_brand, article.get("brand")):
            score += 3
        if file_size and amparser.normalize_key_part_value(
            "size", file_size,
        ) == amparser.normalize_key_part_value("size", article.get("size")):
            score += 3
        file_tc = amparser.extract_key_field_value("TC", core_fields, extra_attributes)
        art_tc = amparser.extract_key_field_value(
            "TC",
            {"tc": article.get("tc")},
            article.get("extra_attributes") or {},
        )
        if file_tc and art_tc and amparser.normalize_key_part_value("tc", file_tc) == amparser.normalize_key_part_value("tc", art_tc):
            score += 2
        if score > 0:
            scored.append((score, article))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _build_field_comparisons(key_fields, core_fields, extra_attributes, master_article):
    comparisons = []
    master_core = {}
    master_extra = {}
    if master_article:
        master_core = {
            "brand": master_article.get("brand"),
            "size": master_article.get("size"),
            "product_type": master_article.get("product_type"),
            "tc": master_article.get("tc"),
        }
        master_extra = master_article.get("extra_attributes") or {}

    for field in key_fields:
        file_val = amparser.extract_key_field_value(field, core_fields, extra_attributes)
        master_val = (
            amparser.extract_key_field_value(field, master_core, master_extra)
            if master_article else None
        )
        field_l = field.lower()
        n_file = amparser.normalize_key_part_value(field, file_val)
        n_master = amparser.normalize_key_part_value(field, master_val)

        if field_l == "brand":
            if amparser.brands_match_fuzzy(file_val, master_val):
                status = "match"
            elif not n_file and not n_master:
                status = "both_empty"
            elif not n_file:
                status = "missing_in_file"
            elif not n_master:
                status = "missing_in_master"
            else:
                status = "mismatch"
        elif n_file and n_master:
            status = "match" if n_file == n_master else "mismatch"
        elif not n_file and not n_master:
            status = "both_empty"
        elif not n_file:
            status = "missing_in_file"
        else:
            status = "missing_in_master"

        comparisons.append({
            "field": field,
            "file_value": file_val if file_val not in (None, "") else None,
            "master_value": master_val if master_val not in (None, "") else None,
            "status": status,
        })
    return comparisons


def _recommended_action(item, comparisons, closest_article):
    actions = []
    if not item.get("matched"):
        mismatches = [c for c in comparisons if c["status"] == "mismatch"]
        missing_in_file = [c for c in comparisons if c["status"] == "missing_in_file"]
        if not closest_article:
            actions.append({
                "code": "add_to_article_master",
                "label": "Add to Article Master",
                "detail": "No close match found — create this article in Article Master.",
            })
        elif mismatches:
            fields = ", ".join(c["field"] for c in mismatches)
            actions.append({
                "code": "edit_file_or_master",
                "label": "Edit file or Article Master",
                "detail": f"Field(s) differ: {fields}. Fix the Excel row or update Article Master.",
            })
        elif missing_in_file:
            fields = ", ".join(c["field"] for c in missing_in_file)
            actions.append({
                "code": "edit_file",
                "label": "Edit Excel file",
                "detail": f"Missing in file: {fields}. Fill these columns and re-upload.",
            })
        else:
            actions.append({
                "code": "add_to_article_master",
                "label": "Add to Article Master",
                "detail": "Row does not exist in Article Master yet.",
            })

    if not item.get("is_clean_bale_multiple") and not item.get("bale_qty_mismatch"):
        actions.append({
            "code": "review_qty",
            "label": "Review quantity",
            "detail": "Quantity is not a clean bale multiple — confirm with distributor or adjust qty.",
        })

    if item.get("bale_qty_mismatch"):
        actions.append({
            "code": "fix_bales_with_distributor",
            "label": "Ask distributor to correct bales",
            "detail": item.get("bale_mismatch_detail")
            or "Sheet bales do not match Qty ÷ Bale Size. Order uses Qty; highlight for correction.",
        })

    if not actions:
        return None
    return actions[0]


def annotate_item_issues(
    conn, amdb, user_id, item, key_fields, category=None,
    core_fields=None, extra_attributes=None,
):
    """Add human-readable preview diagnostics for the save-confirmation UI."""
    core_fields = core_fields or {}
    extra_attributes = extra_attributes or item.get("extra_attributes") or {}
    issues = []
    suggestion = None

    closest_article = None
    if not item.get("matched"):
        closest_article = _find_closest_article_for_preview(
            conn, amdb, user_id, category, core_fields, extra_attributes, key_fields,
        )
        issues.append("Not found in Article Master")
        if category:
            issues.append(f"Category: {category}")
        issues.append(f"Built item_key: {item.get('item_key')}")
        suggestion = _closest_article_hint(conn, amdb, user_id, category, item, key_fields)

    field_comparisons = _build_field_comparisons(
        key_fields, core_fields, extra_attributes, closest_article if not item.get("matched") else None,
    )
    mismatch_fields = [c["field"] for c in field_comparisons if c["status"] == "mismatch"]
    if mismatch_fields:
        issues.append(f"Field mismatch: {', '.join(mismatch_fields)}")

    if item.get("bale_qty_mismatch"):
        detail = item.get("bale_mismatch_detail") or (
            f"Sheet bales {item.get('sheet_bales')} != expected "
            f"{item.get('expected_bales')} (Qty ÷ Bale Size)"
        )
        issues.append(f"Bale mismatch: {detail}")

    if not item.get("is_clean_bale_multiple") and not item.get("bale_qty_mismatch"):
        bale = item.get("bale_size_used")
        qty = item.get("final_piece_qty")
        if bale:
            remainder = qty % bale if qty is not None and bale else 0
            nearest_lower = int(qty // bale) * bale if bale else qty
            nearest_upper = nearest_lower + bale if bale else qty
            issues.append(
                f"Qty {qty} pcs is not a clean multiple of bale size {bale} "
                f"(remainder {remainder:g} pcs; nearest clean: {nearest_lower} or {nearest_upper})"
            )
        elif qty is not None:
            issues.append(f"Qty {qty} pcs — bale size missing, cannot verify packing")

    recommended = _recommended_action(item, field_comparisons, closest_article)

    item["field_comparisons"] = field_comparisons
    item["closest_master_brand"] = closest_article.get("brand") if closest_article else None
    item["closest_master_item_key"] = closest_article.get("item_key") if closest_article else None
    item["recommended_action"] = recommended
    item["issue_summary"] = "; ".join(issues) if issues else None
    item["suggestion"] = suggestion
    item["has_issue"] = bool(issues)
    return item


def _resolve_named_column(header_row, name):
    """Resolve a previously-confirmed column name/label back to its index,
    including the synthetic 'Column N (unlabeled)' label used for headerless
    columns."""
    if not name:
        return None
    m = _UNLABELED_RE.match(name.strip())
    if m:
        return int(m.group(1)) - 1
    for idx, raw in enumerate(header_row):
        raw_label = str(raw).strip()
        if raw_label and raw_label.lower() != "nan" and raw_label == name.strip():
            return idx
    return None


def _detect_sum_relationships(data_rows, candidates, tolerance=0.01):
    """
    Verifies whether one candidate column equals the sum of two others across
    every comparable row in the file (e.g. BND.xlsx: Additional Order Qty =
    Qty + Add for 42/42 rows). Surfaced to the confirm dialog as a hint —
    does NOT auto-pick the answer, the human still confirms (per spec).
    """
    idxs = [c["column_index"] for c in candidates]
    label_by_idx = {c["column_index"]: c["column_label"] for c in candidates}
    columns = {
        idx: [
            _safe_float(row.iloc[idx]) if idx < len(row) else None
            for row in data_rows
        ]
        for idx in idxs
    }
    n = len(data_rows)
    relationships = []
    for c_idx in idxs:
        c_vals = columns[c_idx]
        for a_pos, a_idx in enumerate(idxs):
            if a_idx == c_idx:
                continue
            for b_idx in idxs[a_pos + 1:]:
                if b_idx == c_idx:
                    continue
                comparable = 0
                matches = 0
                for i in range(n):
                    a, b, c = columns[a_idx][i], columns[b_idx][i], c_vals[i]
                    if a is None or b is None or c is None:
                        continue
                    comparable += 1
                    if abs((a + b) - c) <= tolerance:
                        matches += 1
                if comparable >= 3 and matches == comparable:
                    relationships.append({
                        "sum_column_index": c_idx,
                        "sum_column_label": label_by_idx[c_idx],
                        "addend_indices": [a_idx, b_idx],
                        "addend_labels": [label_by_idx[a_idx], label_by_idx[b_idx]],
                        "verified_rows": comparable,
                        "note": (
                            f"{label_by_idx[c_idx]} = {label_by_idx[a_idx]} + "
                            f"{label_by_idx[b_idx]} for all {comparable} comparable rows"
                        ),
                    })
    return relationships


def _values_look_like_order_qty(values):
    """Filter out money/total columns (DCA col 26) and serial-number columns."""
    if not values:
        return False
    if _looks_like_serial_number(values):
        return False
    if max(values) > 5000:
        return False
    return True


def _header_looks_like_qty_column(label):
    """Narrow multi-candidate lists to order-quantity-ish headers."""
    if is_qty_noise_header(label):
        return False
    norm = amparser._norm(label)
    if "unlabeled" in label.lower():
        return True
    if "min bale" in norm:
        return False
    if is_bales_column_header(label):
        # Separate bales col — not the piece-qty column when both exist
        return False
    if is_month_split_qty_header(label) or is_order_total_qty_header(label):
        return True
    if norm in {"add", "qty", "qnty", "quantity", "additional quantity", "additional order qty"}:
        return True
    keywords = (
        "qty", "qnty", "order q", "additional order", "additional quantity",
        "revised order", "order in pc",
    )
    return any(kw in norm for kw in keywords)


def _build_candidate(header_row, idx, numeric_vals):
    label = _column_label(header_row, idx)
    if is_bales_column_header(label) or is_bale_count_quantity_column(label, None):
        kind = "bales"
        hint = "Bale count (how many packs) — not piece quantity"
    elif (
        is_order_total_qty_header(label)
        or is_month_split_qty_header(label)
        or amparser._norm(label) in {
            "qty", "qnty", "quantity", "add", "additional order qty", "additional quantity",
        }
        or any(
            kw in amparser._norm(label)
            for kw in ("order in pc", "revised order", "additional order", "additional quantity")
        )
    ):
        kind = "pieces"
        if is_order_total_qty_header(label):
            hint = "Total order pcs (use this when months are split)"
        elif is_month_split_qty_header(label):
            hint = "Monthly split qty — prefer TOTAL when present"
        else:
            hint = "Piece / order quantity — use this as Qty"
    else:
        kind = "unknown"
        hint = "Possible quantity column"
    return {
        "column_index": idx,
        "column_label": label,
        "sample_values": numeric_vals[:5],
        "populated_count": len(numeric_vals),
        "kind": kind,
        "hint": hint,
    }


def _prefer_piece_qty_candidates(candidates):
    """
    Locked teaching: when both Qty (pieces) and No-of-Bales appear, never ask the
    user — pick the piece column; bales are checked separately against Qty.

    Monthly splits (JULY/AUG/…) + TOTAL → prefer TOTAL without asking.
    """
    if not candidates:
        return candidates

    totals = [c for c in candidates if is_order_total_qty_header(c.get("column_label"))]
    months = [c for c in candidates if is_month_split_qty_header(c.get("column_label"))]
    if len(totals) == 1 and months:
        return totals
    if len(totals) == 1 and not months:
        # Lone TOTAL with other non-month candidates — still prefer it when
        # remaining look like month-ish unknowns or weaker labels.
        others = [c for c in candidates if c is not totals[0]]
        if not others or all(
            is_month_split_qty_header(c.get("column_label"))
            or amparser._norm(c.get("column_label")) in {"add"}
            for c in others
        ):
            return totals

    piece = [c for c in candidates if c.get("kind") == "pieces" or (
        not is_bales_column_header(c.get("column_label"))
        and not is_bale_count_quantity_column(c.get("column_label"), None)
    )]
    # Keep only piece-looking columns when any exist alongside bales
    has_bales = any(
        is_bales_column_header(c.get("column_label"))
        or c.get("kind") == "bales"
        for c in candidates
    )
    if has_bales and piece:
        candidates = piece
    if len(candidates) == 1:
        return candidates
    # Prefer an exact Qty / Qnty header over vague "Add" / unlabeled
    exact = [
        c for c in candidates
        if amparser._norm(c.get("column_label")) in {"qty", "qnty", "quantity"}
    ]
    if len(exact) == 1:
        return exact
    # Drop month splits when a clearer piece/total column remains
    if months and totals:
        return totals
    non_month = [c for c in candidates if not is_month_split_qty_header(c.get("column_label"))]
    if len(non_month) == 1 and months:
        return non_month
    return candidates


def _numeric_population(data_rows, idx):
    vals = []
    for row in data_rows:
        if idx >= len(row):
            continue
        f = _safe_float(row.iloc[idx])
        if f is not None:
            vals.append(f)
    return vals


def _try_select_monthly_booking_total(header_row, data_rows, core_mapped_indices, max_row_len):
    """
    BALAJI-style teaching (generic for every distributor):
    booking forms that split order pcs across month columns (JULY, AUG, …)
    and a TOTAL column → auto-use TOTAL as Qty.
    """
    month_idxs = []
    total_idxs = []
    for idx in range(max_row_len):
        if idx in core_mapped_indices:
            continue
        label = _column_label(header_row, idx)
        if is_qty_noise_header(label):
            continue
        if is_month_split_qty_header(label):
            month_idxs.append(idx)
        elif is_order_total_qty_header(label):
            total_idxs.append(idx)

    if not total_idxs or not month_idxs:
        return None

    # Prefer the TOTAL that sits nearest the month block (usually last).
    total_idxs.sort(key=lambda i: (min(abs(i - m) for m in month_idxs), i))
    for total_idx in total_idxs:
        numeric_vals = _numeric_population(data_rows, total_idx)
        if numeric_vals and _values_look_like_order_qty(numeric_vals):
            label = _column_label(header_row, total_idx)
            month_labels = [_column_label(header_row, i) for i in month_idxs]
            return {
                "status": "ok",
                "column_index": total_idx,
                "column_label": label,
                "auto_selected_reason": (
                    f"Monthly booking split ({', '.join(month_labels)}) — "
                    f"using '{label}' as order Qty"
                ),
            }
    return None


def detect_quantity_column(header_row, col_mapping, category, data_rows, pref_column_name=None):
    """
    Returns either:
      {"status": "ok", "column_index": int, "column_label": str}
    or:
      {"status": "needs_confirmation", "candidates": [...], "relationships": [...]}

    Preference (locked teaching):
      Revised Order / Order In Pc's > Qnty alias > shifted-right-of-empty-Qnty >
      Monthly TOTAL (JULY+AUG+…) > Additional Order Qty (sum) > other qty-ish headers.
      Never: Qnty Per Color / Qnty pre Design / Value / Asst / Delivery windows.
    """
    if pref_column_name:
        idx = _resolve_named_column(header_row, pref_column_name)
        if idx is not None:
            return {"status": "ok", "column_index": idx, "column_label": pref_column_name}

    core_mapped_indices = {idx for idx, f in col_mapping.items() if f}
    max_row_len = max((len(r) for r in data_rows), default=len(header_row))

    # Prefer final qty headers even when sparsely filled (blank = not ordered).
    for prefer in PREFERRED_FINAL_QTY_HEADERS:
        idx = _find_header_index(header_row, [prefer])
        if idx is not None and idx not in core_mapped_indices:
            return {
                "status": "ok",
                "column_index": idx,
                "column_label": _column_label(header_row, idx),
                "auto_selected_reason": f"Preferred final qty column '{_column_label(header_row, idx)}'",
            }

    # Any-distributor monthly booking: month columns + TOTAL → TOTAL is Qty.
    monthly = _try_select_monthly_booking_total(
        header_row, data_rows, core_mapped_indices, max_row_len,
    )
    if monthly:
        return monthly

    aliases = QTY_COLUMN_ALIASES.get(category, [])
    alias_idx = None
    for idx, raw_name in enumerate(header_row):
        if is_qty_noise_header(_column_label(header_row, idx)):
            continue
        if amparser._norm(raw_name) in {amparser._norm(a) for a in aliases}:
            alias_idx = idx
            break

    # Step 1: standard alias column has data
    if alias_idx is not None:
        numeric_vals = _numeric_population(data_rows, alias_idx)
        if numeric_vals:
            return {
                "status": "ok",
                "column_index": alias_idx,
                "column_label": _column_label(header_row, alias_idx),
            }

        # Step 2: alias exists but empty — scan adjacent cols (DCA shift).
        # Never shift into Value / noise / bales-only columns.
        adjacent_candidates = []
        for idx in range(alias_idx + 1, min(alias_idx + 4, max_row_len)):
            label = _column_label(header_row, idx)
            if is_qty_noise_header(label) or is_bales_column_header(label):
                continue
            numeric_vals = _numeric_population(data_rows, idx)
            if numeric_vals and _values_look_like_order_qty(numeric_vals):
                adjacent_candidates.append(_build_candidate(header_row, idx, numeric_vals))
        if len(adjacent_candidates) == 1:
            c = adjacent_candidates[0]
            return {
                "status": "ok",
                "column_index": c["column_index"],
                "column_label": c["column_label"],
                "auto_selected_reason": (
                    f"Qnty header empty — using shifted column '{c['column_label']}'"
                ),
            }

    # Step 3: scan for multiple candidates (filter to qty-ish headers)
    candidates = []
    for idx in range(max_row_len):
        if idx in core_mapped_indices or idx == alias_idx:
            continue
        label = _column_label(header_row, idx)
        if is_qty_noise_header(label):
            continue
        numeric_vals = _numeric_population(data_rows, idx)
        if not numeric_vals or not _values_look_like_order_qty(numeric_vals):
            continue
        if not _header_looks_like_qty_column(label):
            continue
        candidates.append(_build_candidate(header_row, idx, numeric_vals))

    if not candidates:
        # Fallback: only-bales sheet (Order In Bales / No of Bales as the qty source)
        bales_fallback = []
        for idx in range(max_row_len):
            if idx in core_mapped_indices:
                continue
            label = _column_label(header_row, idx)
            if not is_bales_column_header(label) and not is_bale_count_quantity_column(label, category):
                continue
            numeric_vals = _numeric_population(data_rows, idx)
            if numeric_vals:
                bales_fallback.append(_build_candidate(header_row, idx, numeric_vals))
        if len(bales_fallback) == 1:
            c = bales_fallback[0]
            return {
                "status": "ok",
                "column_index": c["column_index"],
                "column_label": c["column_label"],
                "auto_selected_reason": "Only bales column present — qty will be Bales × Bale Size",
            }
        if not bales_fallback:
            raise ValueError(
                "Quantity column not found — no column in the file contains order quantity values."
            )
        return {
            "status": "needs_confirmation",
            "candidates": bales_fallback,
            "relationships": [],
        }

    if len(candidates) == 1:
        c = candidates[0]
        return {"status": "ok", "column_index": c["column_index"], "column_label": c["column_label"]}

    relationships = _detect_sum_relationships(data_rows, candidates)
    # BND pattern: when exactly one column is mathematically proven to be the
    # final total (e.g. Additional Order Qty = Qty + Add), use it without prompting.
    verified_totals = [r for r in relationships if r.get("verified_rows", 0) >= 3]
    if len(verified_totals) == 1:
        r = verified_totals[0]
        return {
            "status": "ok",
            "column_index": r["sum_column_index"],
            "column_label": r["sum_column_label"],
            "auto_selected_reason": r["note"],
        }

    # Explicit BND-style total column name — user confirmed this is always correct.
    additional_order_cols = [
        c for c in candidates
        if amparser._norm(c["column_label"]) in {"additional order qty", "additional quantity"}
    ]
    if len(additional_order_cols) == 1:
        c = additional_order_cols[0]
        return {
            "status": "ok",
            "column_index": c["column_index"],
            "column_label": c["column_label"],
            "auto_selected_reason": f"{c['column_label']} column present (BND-style additional/total)",
        }

    # Qty vs No-of-Bales (or Qty vs Add): prefer piece qty — never dump raw numbers on the user.
    preferred = _prefer_piece_qty_candidates(candidates)
    if len(preferred) == 1:
        c = preferred[0]
        return {
            "status": "ok",
            "column_index": c["column_index"],
            "column_label": c["column_label"],
            "auto_selected_reason": (
                f"Auto-selected '{c['column_label']}' as piece Qty "
                "(bales column is checked separately, not as quantity)"
            ),
        }

    return {
        "status": "needs_confirmation",
        "candidates": preferred,
        "relationships": relationships,
        "guidance": (
            "Excel mein ek se zyada quantity-looking columns hain. "
            "Pieces / Qty column chunein — 'No of Bales' nahi. "
            "Bales alag se Qty ke against check hote hain."
        ),
    }


def build_filled_order_rows(valid_rows, header_row, col_mapping, qty_col_idx, bales_col_idx=None):
    """
    Skips blank/zero quantity rows (not ordered).

    When qty column is blank but a separate bales column has a value (only-bales
    path), the row is kept with raw_qty_value=None and sheet_bales set so
    apply_qty_bales_value_rules can compute Qty = Bales × Bale Size.
    """
    rows = []
    for line_number, row in enumerate(valid_rows, start=1):
        raw_qty = _safe_float(row.iloc[qty_col_idx]) if qty_col_idx < len(row) else None
        sheet_bales = None
        if bales_col_idx is not None and bales_col_idx < len(row):
            sheet_bales = _safe_float(row.iloc[bales_col_idx])

        if raw_qty is not None and raw_qty == 0:
            raw_qty = None
        if sheet_bales is not None and sheet_bales == 0:
            sheet_bales = None

        if raw_qty is None and sheet_bales is None:
            continue

        core_fields = {}
        extra_attributes = {}
        for idx, core_field in col_mapping.items():
            if idx >= len(row):
                continue
            val = row.iloc[idx]
            if pd.isna(val):
                val = None
            else:
                val = amparser._json_safe(val)
            raw_col_name = str(header_row[idx]).strip()
            if core_field:
                core_fields[core_field] = val
            elif raw_col_name and raw_col_name.lower() != "nan":
                extra_attributes[raw_col_name] = val

        fill_ex_mill_from_line_value(core_fields, extra_attributes, raw_qty)

        rows.append({
            "line_number": line_number,
            "core_fields": core_fields,
            "extra_attributes": extra_attributes,
            "raw_qty_value": raw_qty,
            "sheet_bales": sheet_bales,
        })
    return rows

def prepare_filled_order_identity(core_fields, extra_attributes, *, category=None):
    """
    Apply the same Article Master teaching used on AM upload so FO lines
    key the same way (Shade→Color, towel size→Bath Towel + BS Size, etc.).

    File Size spelling is kept for display (e.g. 75x150 / DBL BS); match keys
    still normalize via build_item_key / normalize_key_part_value.
    """
    core = dict(core_fields or {})
    extra_in = dict(extra_attributes or {})
    raw_description = None
    for key in list(extra_in.keys()):
        if amparser.normalize_extra_column_name(key) == "description":
            val = extra_in.pop(key)
            if not amparser.is_blank_attr_value(val):
                raw_description = val
            break

    # Collapse sticky aliases (Shade→Color, Packing spellings, …)
    extra = amparser.merge_extra_attributes({}, extra_in)

    raw_size_cell = core.get("size")
    brand, size_norm = amparser.normalize_brand_and_size(core.get("brand"), core.get("size"))
    if brand is not None:
        core["brand"] = brand
    if size_norm is not None and not amparser.is_blank_attr_value(size_norm):
        raw_size_key = re.sub(r"\s+", "", str(raw_size_cell or "").strip()).upper()
        if raw_size_key == "BATHROBE":
            core["size"] = size_norm

    cat = str(category or "").strip()
    product_type = core.get("product_type")
    if product_type in (None, ""):
        product_type = amparser.DEFAULT_PRODUCT_BY_CATEGORY.get(cat)
    if cat == "Bath" and brand and "bathrobe" in str(brand).lower():
        product_type = "Bathrobe"
    product_type = amparser.normalize_product_spelling(product_type)
    product_type = amparser.resolve_product_type(product_type, size_norm or raw_size_cell)
    core["product_type"] = product_type

    if cat == "Bath":
        physical = amparser.towel_physical_size_code(raw_size_cell)
        if physical and amparser.is_blank_attr_value(extra.get("BS Size")):
            extra["BS Size"] = physical
        raw_color = extra.get("Color")
        color, packing, _is_pkg_only = amparser.normalize_towel_color_and_packing(
            raw_color, raw_description,
        )
        if color:
            extra["Color"] = color
        elif "Color" in extra:
            del extra["Color"]
        if packing and amparser.is_blank_attr_value(extra.get("Packing")):
            extra["Packing"] = packing
        extra = amparser.strip_excluded_extra_attributes(extra, category="Bath")
    elif cat in {"TOB", "Pillow"}:
        extra = amparser.apply_tob_pillow_blend_and_units(extra, cat)
        extra = amparser.strip_excluded_extra_attributes(extra, category=cat)
    elif cat:
        extra = amparser.strip_excluded_extra_attributes(extra, category=cat)

    return core, extra


def match_and_normalize(
    conn,
    amdb,
    user_id,
    parsed_row,
    key_fields,
    category=None,
    qty_column_label: str | None = None,
):
    """
    Matches one parsed row against Article Master and normalizes its
    quantity to pieces using locked qty/bales/value rules.

    Matched: mrp/ptr/ex_mill_price/bale size are the CURRENT Article Master
    values (snapshotted at match time) — never the distributor's own copy of
    those columns, since "distributor never re-types pricing" and their file
    may be a stale copy of an older Article Master send-out.

    Unmatched: falls back to whatever price/bale-size data the row itself
    had (there is no Article Master reference yet) so the row stays usable
    if later added to Article Master via resolve-unmatched.
    """
    # Teach FO identity like AM upload (Shade→Color, towel sizes, …) before match.
    core_fields, extra_attributes = prepare_filled_order_identity(
        parsed_row.get("core_fields"),
        parsed_row.get("extra_attributes"),
        category=category,
    )
    parsed_row["core_fields"] = core_fields
    parsed_row["extra_attributes"] = extra_attributes

    item_key = amparser.build_item_key(core_fields, extra_attributes, key_fields)
    if category:
        article = amdb.resolve_article_match(
            conn, user_id, category, core_fields, extra_attributes, key_fields,
        )
    else:
        article = amdb.get_article_by_item_key(conn, user_id, item_key)

    if article:
        bale_size = article.get("bale_pack_size")
        mrp, ptr, ex_mill = article.get("mrp"), article.get("ptr"), article.get("ex_mill_price")
        matched = True
        article_id = article["id"]
    else:
        fill_ex_mill_from_line_value(
            core_fields, extra_attributes, parsed_row.get("raw_qty_value"),
        )
        bale_size = core_fields.get("bale_pack_size")
        mrp, ptr, ex_mill = (
            core_fields.get("mrp"), core_fields.get("ptr"), core_fields.get("ex_mill_price"),
        )
        matched = False
        article_id = None

    bale_size_f = _safe_float(bale_size)
    ex_f = _safe_float(ex_mill)
    raw_qty = parsed_row["raw_qty_value"]
    sheet_bales = parsed_row.get("sheet_bales")

    resolved = apply_qty_bales_value_rules(
        raw_qty=raw_qty,
        sheet_bales=sheet_bales,
        bale_size=bale_size_f,
        ex_mill=ex_f,
        qty_column_label=qty_column_label,
        category=category,
    )

    return {
        "line_number": parsed_row.get("line_number"),
        "item_key": item_key,
        "article_id": article_id,
        "matched": matched,
        "brand": core_fields.get("brand"),
        "size": core_fields.get("size"),
        "product_type": core_fields.get("product_type"),
        "extra_attributes": extra_attributes,
        "raw_qty_value": raw_qty,
        "detected_unit": resolved["detected_unit"],
        "final_piece_qty": resolved["final_piece_qty"],
        "bale_size_used": bale_size_f,
        "sheet_bales": resolved["sheet_bales"],
        "expected_bales": resolved["expected_bales"],
        "bale_qty_mismatch": resolved["bale_qty_mismatch"],
        "bale_mismatch_detail": resolved["bale_mismatch_detail"],
        "line_value": resolved["line_value"],
        "is_clean_bale_multiple": resolved["is_clean_bale_multiple"],
        "mrp": _safe_float(mrp),
        "ptr": _safe_float(ptr),
        "ex_mill_price": ex_f,
    }


def _looks_like_qty_subheader_row(values) -> bool:
    texts = [amparser._norm(v) for v in values if amparser._norm(v)]
    if not texts:
        return False
    blob = " ".join(texts)
    return (
        "total qty" in blob
        or "total quantity" in blob
        or "per color" in blob
        or "per colour" in blob
    )


def flatten_two_row_order_header(header_row, sub_row):
    """Join a QUALITY/SIZE header with the next 'Per Color / total qty' sub-row."""
    if not _looks_like_qty_subheader_row(sub_row):
        return list(header_row), False
    width = max(len(header_row), len(sub_row))
    merged = []
    for idx in range(width):
        top = header_row[idx] if idx < len(header_row) else None
        sub = sub_row[idx] if idx < len(sub_row) else None
        top_s = str(top).strip() if top is not None and str(top).strip().lower() not in {"", "nan", "none"} else ""
        sub_s = str(sub).strip() if sub is not None and str(sub).strip().lower() not in {"", "nan", "none"} else ""
        if top_s and sub_s:
            merged.append(f"{top_s} {sub_s}")
        else:
            merged.append(top_s or sub_s)
    return merged, True


def fill_ex_mill_from_line_value(core_fields: dict, extra_attributes: dict, raw_qty):
    """If EXMILL rate is missing, derive it from 'value at exmill' / qty."""
    if _safe_float(core_fields.get("ex_mill_price")):
        return
    qty = _safe_float(raw_qty)
    if not qty:
        return
    for key, val in (extra_attributes or {}).items():
        norm = amparser._norm(key)
        compact = norm.replace("-", "").replace(" ", "")
        if "value" in compact and "exmill" in compact:
            amount = _safe_float(val)
            if amount:
                core_fields["ex_mill_price"] = round(amount / qty, 4)
                return


def looks_like_special_order_stream(filename: str | None) -> bool:
    """Special / SPL booking files get their own FO stream — never merge into regular."""
    stem = Path(filename or "").stem.lower()
    if not stem:
        return False
    special_tokens = ("special", " spl", "_spl", "-spl", "spl ", "spl_")
    return any(tok in stem for tok in special_tokens) or "spl" in stem.split()


def looks_like_addon_order_filename(filename: str | None) -> bool:
    """Additional booking files (non-special) add to an existing FO in the same stream."""
    if looks_like_special_order_stream(filename):
        return False
    stem = Path(filename or "").stem.lower()
    return any(
        token in stem
        for token in ("additional", "addnl", "extra order", "addon")
    )


def _sheet_name_looks_non_order(sheet_name: str) -> bool:
    norm = amparser._norm(sheet_name or "")
    return any(hint in norm for hint in NON_ORDER_SHEET_HINTS)


# Tabs produced by Order Desk → SO Pack Excel download (not a distributor FO).
_SO_PACK_SHEET_MARKERS = frozenset({
    "consolidated",
    "so summary",
    "line item detail",
    "brand wise size wise summary",
    "brand wise summary",
})


def looks_like_so_pack_workbook(path) -> bool:
    """True when the file is an SO Pack consolidation export, not a filled order."""
    with open_excel_file(path) as xl:
        names = {amparser._norm(n) for n in xl.sheet_names}
    hits = names & _SO_PACK_SHEET_MARKERS
    if len(hits) >= 2:
        return True
    # Single-tab exports / renamed sheets: title row on Consolidated-style layout.
    if "consolidated" in names or "line item detail" in names:
        return True
    return False


def so_pack_upload_guidance() -> str:
    return (
        "This Excel looks like an SO Pack export (Consolidated / SO Summary / "
        "Line Item Detail), not a distributor filled order. "
        "For Filled Order, upload the distributor booking sheet that has "
        "Brand + Size columns (e.g. Choice Corner.xlsx from Distributor Order) — "
        "not the SO Pack download."
    )


def sheet_has_order_headers(raw_df) -> bool:
    """True when a worksheet looks like a filled-order line table (Brand + Size)."""
    if raw_df is None or raw_df.empty:
        return False
    try:
        header_idx = amparser.detect_header_row(raw_df)
    except Exception:
        return False
    header_row = raw_df.iloc[header_idx].tolist()
    col_mapping = amparser.map_columns_to_core(header_row)
    mapped = {f for f in col_mapping.values() if f}
    return "brand" in mapped and "size" in mapped


def excel_engine_for_path(path) -> str | None:
    """Pick pandas Excel engine. Legacy .xls needs xlrd (any distributor)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        try:
            import xlrd  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Old Excel (.xls) support needs xlrd>=2.0.1. "
                "Install with: pip install 'xlrd>=2.0.1'"
            ) from exc
        return "xlrd"
    if suffix in {".xlsx", ".xlsm"}:
        return "openpyxl"
    return None


def read_excel_sheet(path, sheet_name=0, header=None):
    """Read one sheet from .xls / .xlsx / .xlsm for any distributor upload."""
    engine = excel_engine_for_path(path)
    kwargs = {"sheet_name": sheet_name, "header": header}
    if engine:
        kwargs["engine"] = engine
    return pd.read_excel(path, **kwargs)


def open_excel_file(path):
    """Open workbook listing sheet names (engine-aware for .xls)."""
    engine = excel_engine_for_path(path)
    if engine:
        return pd.ExcelFile(path, engine=engine)
    return pd.ExcelFile(path)


def detect_category_from_order_file(path, filename: str | None = None) -> str | None:
    """
    Detect Bed / Bath / TOB / Pillow from any distributor order file.

    Uses product-column majority vote, then size-code fallback (same rules as
    Article Master). Filename is only a weak hint — works for SAIN.xls,
    BND.xlsx, Choice Corner, etc., not one-off per distributor.
    """
    if looks_like_so_pack_workbook(path):
        raise ValueError(so_pack_upload_guidance())
    sheet_names = list_order_sheet_names(path)
    sheet_name = sheet_names[0] if sheet_names else None
    if not sheet_name:
        with open_excel_file(path) as xl:
            sheet_name = xl.sheet_names[0]
    raw_df = read_excel_sheet(path, sheet_name=sheet_name, header=None)
    header_idx = amparser.detect_header_row(raw_df)
    header_row = raw_df.iloc[header_idx].tolist()
    data_start = header_idx + 1
    if header_idx + 1 < len(raw_df):
        header_row, used_sub = flatten_two_row_order_header(
            header_row, raw_df.iloc[header_idx + 1].tolist(),
        )
        if used_sub:
            data_start = header_idx + 2
    col_mapping = amparser.map_columns_to_core(header_row)
    data_rows_all = raw_df.iloc[data_start:]
    valid_rows = [
        row
        for _, row in data_rows_all.iterrows()
        if amparser.is_data_row(row.tolist(), col_mapping)
    ]
    product_col_idx = next(
        (idx for idx, field in col_mapping.items() if field == "product_type"),
        None,
    )
    size_col_idx = next(
        (idx for idx, field in col_mapping.items() if field == "size"),
        None,
    )
    product_values = (
        [row.iloc[product_col_idx] for row in valid_rows]
        if product_col_idx is not None
        else []
    )
    detect_name = filename or str(path)
    category = amparser.detect_category(product_values, filename=detect_name)
    if not category and size_col_idx is not None:
        size_values = [row.iloc[size_col_idx] for row in valid_rows]
        category = amparser.detect_category_from_sizes(size_values)
    return category


def list_order_sheet_names(path) -> list[str]:
    """
    Return workbook tabs that contain filled-order line data.

    Teaching (BND): when a distributor file has multiple order tabs
    (e.g. ``base order`` + ``additional order``), every data tab is read —
    not only the first sheet.
    """
    with open_excel_file(path) as xl:
        names = list(xl.sheet_names)
    order_sheets = []
    for name in names:
        if _sheet_name_looks_non_order(name):
            continue
        raw_df = read_excel_sheet(path, sheet_name=name, header=None)
        if sheet_has_order_headers(raw_df):
            order_sheets.append(name)
    # Fallback: first sheet if nothing matched (legacy single-tab files).
    if not order_sheets and names:
        order_sheets = [names[0]]
    return order_sheets


def _article_club_key(parsed_row: dict) -> tuple[str, str]:
    cf = parsed_row.get("core_fields") or {}
    brand, size = amparser.normalize_brand_and_size(cf.get("brand"), cf.get("size"))
    return (amparser._norm(brand or ""), amparser._norm(size or ""))


def club_parsed_rows_by_brand_size(parsed_rows: list[dict]) -> list[dict]:
    """
    Sum quantities for the same Brand+Size across tabs (BND teaching).

    Base order Qty + Additional quantity → one clubbed line.
    """
    buckets: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for row in parsed_rows:
        key = _article_club_key(row)
        if not key[0] and not key[1]:
            continue
        if key not in buckets:
            clone = {
                "line_number": row.get("line_number"),
                "core_fields": dict(row.get("core_fields") or {}),
                "extra_attributes": dict(row.get("extra_attributes") or {}),
                "raw_qty_value": row.get("raw_qty_value"),
                "sheet_bales": row.get("sheet_bales"),
                "source_sheets": list(row.get("source_sheets") or []),
            }
            buckets[key] = clone
            order.append(key)
            continue
        dest = buckets[key]
        a = dest.get("raw_qty_value")
        b = row.get("raw_qty_value")
        if a is None and b is None:
            dest["raw_qty_value"] = None
        else:
            dest["raw_qty_value"] = (a or 0) + (b or 0)
        sa = dest.get("sheet_bales")
        sb = row.get("sheet_bales")
        if sa is None and sb is None:
            dest["sheet_bales"] = None
        else:
            dest["sheet_bales"] = (sa or 0) + (sb or 0)
        for s in row.get("source_sheets") or []:
            if s not in dest["source_sheets"]:
                dest["source_sheets"].append(s)
        # Prefer non-empty core/extra from later sheets only when missing on dest
        for k, v in (row.get("core_fields") or {}).items():
            if dest["core_fields"].get(k) in (None, "") and v not in (None, ""):
                dest["core_fields"][k] = v
    for i, key in enumerate(order, start=1):
        buckets[key]["line_number"] = i
    return [buckets[k] for k in order]


def parse_sheet_filled_order_rows(
    path,
    sheet_name: str,
    category: str,
    pref_column_name: str | None = None,
) -> dict:
    """Parse one worksheet into filled-order parsed rows + qty/bales metadata."""
    raw_df = read_excel_sheet(path, sheet_name=sheet_name, header=None)
    header_idx = amparser.detect_header_row(raw_df)
    header_row = raw_df.iloc[header_idx].tolist()
    data_start = header_idx + 1
    if header_idx + 1 < len(raw_df):
        header_row, used_sub = flatten_two_row_order_header(
            header_row, raw_df.iloc[header_idx + 1].tolist(),
        )
        if used_sub:
            data_start = header_idx + 2
    col_mapping = amparser.map_columns_to_core(header_row)
    valid_rows = [
        row for _, row in raw_df.iloc[data_start:].iterrows()
        if amparser.is_data_row(row.tolist(), col_mapping)
    ]
    qty_detection = detect_quantity_column(
        header_row, col_mapping, category, valid_rows, pref_column_name=pref_column_name,
    )
    if qty_detection["status"] != "ok":
        return {
            "sheet_name": sheet_name,
            "qty_detection": qty_detection,
            "parsed_rows": [],
            "header_row": header_row,
            "col_mapping": col_mapping,
            "valid_rows": valid_rows,
            "bales_col_label": None,
        }

    qty_col_idx = qty_detection["column_index"]
    bales_detection = detect_bales_column(header_row, col_mapping, qty_col_idx=qty_col_idx)
    bales_col_idx = bales_detection["column_index"] if bales_detection else None
    parsed_rows = build_filled_order_rows(
        valid_rows, header_row, col_mapping, qty_col_idx, bales_col_idx=bales_col_idx,
    )
    for row in parsed_rows:
        row["source_sheets"] = [sheet_name]
    return {
        "sheet_name": sheet_name,
        "qty_detection": qty_detection,
        "parsed_rows": parsed_rows,
        "header_row": header_row,
        "col_mapping": col_mapping,
        "valid_rows": valid_rows,
        "bales_col_label": bales_detection["column_label"] if bales_detection else None,
        "qty_col_label": qty_detection["column_label"],
    }


def parse_filled_order_workbook(
    path,
    category: str,
    pref_column_name: str | None = None,
) -> dict:
    """
    Read every order tab in a workbook, then club Brand+Size quantities.

    BND teaching: ``base order`` Qty + ``additional order`` Additional quantity
    are summed into one line per Brand+Size.
    """
    if looks_like_so_pack_workbook(path):
        raise ValueError(so_pack_upload_guidance())
    sheet_names = list_order_sheet_names(path)
    sheet_results = []
    all_parsed = []
    qty_labels = []
    bales_labels = []

    for sheet_name in sheet_names:
        # Don't force a saved "Qty" preference onto an Additional quantity tab.
        sheet_pref = pref_column_name if len(sheet_names) == 1 else None
        result = parse_sheet_filled_order_rows(
            path, sheet_name, category, pref_column_name=sheet_pref,
        )
        if result["qty_detection"]["status"] != "ok":
            # Single-sheet files still surface confirmation; multi-sheet skips a
            # broken tab only when at least one other tab already produced rows.
            if not all_parsed and len(sheet_names) == 1:
                return {
                    "status": "qty_column_confirmation_required",
                    "qty_detection": result["qty_detection"],
                    "sheet_name": sheet_name,
                    "sheet_names": sheet_names,
                    "parsed_rows": [],
                    "header_row": result["header_row"],
                    "col_mapping": result["col_mapping"],
                    "valid_rows": result["valid_rows"],
                }
            continue
        sheet_results.append(result)
        all_parsed.extend(result["parsed_rows"])
        qty_labels.append(f"{sheet_name}:{result['qty_col_label']}")
        if result.get("bales_col_label"):
            bales_labels.append(f"{sheet_name}:{result['bales_col_label']}")

    if not all_parsed:
        # Re-run first sheet to return its confirmation payload if any
        first = sheet_names[0] if sheet_names else None
        if first:
            result = parse_sheet_filled_order_rows(path, first, category, pref_column_name)
            if result["qty_detection"]["status"] != "ok":
                return {
                    "status": "qty_column_confirmation_required",
                    "qty_detection": result["qty_detection"],
                    "sheet_name": first,
                    "sheet_names": sheet_names,
                    "parsed_rows": [],
                    "header_row": result["header_row"],
                    "col_mapping": result["col_mapping"],
                    "valid_rows": result["valid_rows"],
                }
        raise ValueError("No order quantity rows found across workbook sheets.")

    clubbed = club_parsed_rows_by_brand_size(all_parsed)
    # Header/col_mapping from the primary (first successful) sheet for category detect
    primary = sheet_results[0]
    # Persist a clean qty label for normalize/recompute; sheet detail stays in sheets_read.
    qty_stored = (
        "Qty (multi-sheet clubbed)"
        if len(sheet_results) > 1
        else (primary.get("qty_col_label") or "Qty")
    )
    return {
        "status": "ok",
        "sheet_names": [r["sheet_name"] for r in sheet_results],
        "sheets_read": len(sheet_results),
        "parsed_rows": clubbed,
        "quantity_column_used": qty_stored,
        "quantity_column_detail": " + ".join(qty_labels),
        "bales_column_used": " + ".join(bales_labels) if bales_labels else None,
        "header_row": primary["header_row"],
        "col_mapping": primary["col_mapping"],
        "valid_rows": primary["valid_rows"],
        "raw_line_count_before_club": len(all_parsed),
    }

