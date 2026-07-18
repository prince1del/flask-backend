"""
Distributor Filled-Order Matching — Parser + Quantity-Column Detection

Reuses Article Master's header/category/item-key logic (article_master_parser.py)
and adds the one genuinely new problem this feature has: finding the real
order-quantity column and normalizing it into pieces.

Verified against 6 real distributor files across Bed/Bath categories:
  - Standard column, all-pieces (kag.xlsx)
  - Standard column, all-bales (KAG_AGRA.xlsx)
  - Standard column, clean case (savitri_steel.xlsx, Choice_Corner.xlsx)
  - Standard column empty, data shifted to an adjacent/unlabeled column (DCA_Order.xlsx)
  - Multiple candidate columns, true total = derived sum column (BND.xlsx:
    Additional Order Qty = Qty + Add, verified 42/42 rows)
"""

import math
import re

import pandas as pd

import article_master_parser as amparser

QTY_COLUMN_ALIASES = {
    "Bed": ["qnty"],
    "Bath": ["qty in bales"],
    "TOB": ["booking qnty"],
    "TOB Pillow": ["awds order in no of bales"],
}

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
        same_size = [a for a in same_brand if (a.get("size") or "").strip().lower() == size]
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

    if not item.get("is_clean_bale_multiple"):
        actions.append({
            "code": "review_qty",
            "label": "Review quantity",
            "detail": "Quantity is not a clean bale multiple — confirm with distributor or adjust qty.",
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

    if not item.get("is_clean_bale_multiple"):
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
    """Narrow multi-candidate lists to order-quantity-ish headers (BND Bed)."""
    norm = amparser._norm(label)
    if "unlabeled" in label.lower():
        return True
    if "min bale" in norm:
        return False
    if norm in {"add"}:
        return True
    keywords = ("qty", "qnty", "bales", "order q", "additional order")
    return any(kw in norm for kw in keywords)


def _numeric_population(data_rows, idx):
    vals = []
    for row in data_rows:
        if idx >= len(row):
            continue
        f = _safe_float(row.iloc[idx])
        if f is not None:
            vals.append(f)
    return vals


def _build_candidate(header_row, idx, numeric_vals):
    return {
        "column_index": idx,
        "column_label": _column_label(header_row, idx),
        "sample_values": numeric_vals[:5],
        "populated_count": len(numeric_vals),
    }


def detect_quantity_column(header_row, col_mapping, category, data_rows, pref_column_name=None):
    """
    Returns either:
      {"status": "ok", "column_index": int, "column_label": str}
    or:
      {"status": "needs_confirmation", "candidates": [...], "relationships": [...]}
    """
    if pref_column_name:
        idx = _resolve_named_column(header_row, pref_column_name)
        if idx is not None:
            return {"status": "ok", "column_index": idx, "column_label": pref_column_name}

    core_mapped_indices = {idx for idx, f in col_mapping.items() if f}
    max_row_len = max((len(r) for r in data_rows), default=len(header_row))

    aliases = QTY_COLUMN_ALIASES.get(category, [])
    alias_idx = None
    for idx, raw_name in enumerate(header_row):
        if amparser._norm(raw_name) in aliases:
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

        # Step 2: alias exists but 100% empty — scan adjacent unlabeled/shifted cols
        adjacent_candidates = []
        for idx in range(alias_idx + 1, min(alias_idx + 4, max_row_len)):
            numeric_vals = _numeric_population(data_rows, idx)
            if numeric_vals and _values_look_like_order_qty(numeric_vals):
                adjacent_candidates.append(_build_candidate(header_row, idx, numeric_vals))
        if len(adjacent_candidates) == 1:
            c = adjacent_candidates[0]
            return {"status": "ok", "column_index": c["column_index"], "column_label": c["column_label"]}

    # Step 3: scan for multiple candidates (filter to qty-ish headers)
    candidates = []
    for idx in range(max_row_len):
        if idx in core_mapped_indices or idx == alias_idx:
            continue
        numeric_vals = _numeric_population(data_rows, idx)
        if not numeric_vals or not _values_look_like_order_qty(numeric_vals):
            continue
        label = _column_label(header_row, idx)
        if not _header_looks_like_qty_column(label):
            continue
        candidates.append(_build_candidate(header_row, idx, numeric_vals))

    if not candidates:
        raise ValueError(
            "Quantity column not found — no column in the file contains order quantity values."
        )

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
        if amparser._norm(c["column_label"]) == "additional order qty"
    ]
    if len(additional_order_cols) == 1:
        c = additional_order_cols[0]
        return {
            "status": "ok",
            "column_index": c["column_index"],
            "column_label": c["column_label"],
            "auto_selected_reason": "Additional Order Qty column present (BND-style total)",
        }

    return {"status": "needs_confirmation", "candidates": candidates, "relationships": relationships}


def build_filled_order_rows(valid_rows, header_row, col_mapping, qty_col_idx):
    """
    Skips blank/zero quantity rows (not ordered — established rule, no
    '0 ordered' placeholder tracked). Returns list of
    {core_fields, extra_attributes, raw_qty_value}.
    """
    rows = []
    for line_number, row in enumerate(valid_rows, start=1):
        raw_qty = _safe_float(row.iloc[qty_col_idx]) if qty_col_idx < len(row) else None
        if raw_qty is None or raw_qty == 0:
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

        rows.append({
            "line_number": line_number,
            "core_fields": core_fields,
            "extra_attributes": extra_attributes,
            "raw_qty_value": raw_qty,
        })
    return rows


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
    quantity to pieces.

    Matched: mrp/ptr/ex_mill_price/bale size are the CURRENT Article Master
    values (snapshotted at match time) — never the distributor's own copy of
    those columns, since "distributor never re-types pricing" and their file
    may be a stale copy of an older Article Master send-out.

    Unmatched: falls back to whatever price/bale-size data the row itself
    had (there is no Article Master reference yet) so the row stays usable
    if later added to Article Master via resolve-unmatched.
    """
    core_fields = parsed_row["core_fields"]
    extra_attributes = parsed_row["extra_attributes"]
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
        bale_size = core_fields.get("bale_pack_size")
        mrp, ptr, ex_mill = (
            core_fields.get("mrp"), core_fields.get("ptr"), core_fields.get("ex_mill_price"),
        )
        matched = False
        article_id = None

    bale_size_f = _safe_float(bale_size)
    raw_qty = parsed_row["raw_qty_value"]
    detected_unit, final_qty = normalize_quantity(
        raw_qty,
        bale_size_f,
        qty_column_label=qty_column_label,
        category=category,
    )
    clean = is_clean_bale_multiple(final_qty, bale_size_f)

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
        "detected_unit": detected_unit,
        "final_piece_qty": final_qty,
        "bale_size_used": bale_size_f,
        "is_clean_bale_multiple": clean,
        "mrp": _safe_float(mrp),
        "ptr": _safe_float(ptr),
        "ex_mill_price": _safe_float(ex_mill),
    }
