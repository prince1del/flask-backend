"""
Article Master — Excel Parser

Verified against 4 real Bombay Dyeing GT North booking forms:
Bedsheet_SS-26, AW-26_TOB_Revised, Pillow_05-05-2026, AW-26_Towel_Phase-2.

Handles the 3 real-world quirks found in those files:
  1. Header row position differs per file (row 1 / row 3 / row 7) — detected
     dynamically by scanning for a row containing both "Brand" and "Size".
  2. TOB / Pillow files contain section-header rows (e.g. "Fleece Collection")
     with every other column blank — skipped via is_data_row().
  3. Towel file has blank separator rows between brand-groups — same skip logic.
"""

import pandas as pd
import re
import datetime as dt
from pathlib import Path


def _json_safe(val):
    """Excel date/datetime cells (e.g. Towel file's 'Delivery Date') aren't
    JSON-serializable as-is - convert to ISO string. Everything else passes through."""
    if isinstance(val, (pd.Timestamp, dt.datetime, dt.date)):
        return val.isoformat()
    return val

# Column-name aliases -> canonical core field name.
# Add new aliases here if a future file uses a different header wording —
# no other code needs to change.
# Exact-match aliases (fast path). Case/whitespace normalized before comparison.
CORE_FIELD_ALIASES = {
    "brand": ["brand"],
    "product_type": ["product"],
    "size": ["size"],
    "mrp": ["mrp"],
    "ptr": ["ptr", "tentative (ptr)", "tentative ptr"],
    "ex_mill_price": ["ex-mill", "ex mill", "exmill price", "ex-mill per pcs", "ex mill per pcs"],
    "bale_pack_size": ["bale size", "bale pack size", "bale pack sizes"],
}

# Fallback keyword rule, used only when the exact-match pass above finds nothing.
# bale_pack_size specifically has shown 4 different real header phrasings across
# just 4 source files (Bale Size / Bale Pack Size / Bale Pack Sizes / "Bale Pack
# Size  (No. of Bales) " with trailing clarifier text) - the exact-alias list WILL
# miss future variants, so this field gets a robust substring rule: any column
# whose normalized name starts with "bale" and contains "size" anywhere in it.
CORE_FIELD_KEYWORD_RULES = {
    "bale_pack_size": lambda name: name.startswith("bale") and "size" in name,
}

# Order matters: check Blanket/Comforter before generic Pillow check
CATEGORY_KEYWORDS = [
    (["blanket", "comforter", "slumber"], "TOB"),
    (["pillow"], "TOB Pillow"),
    (["towel"], "Bath"),
    (["bedsheet", "sheet set", "sheet sets", "fitted sheet"], "Bed"),
]

REQUIRED_HEADER_MARKERS = ["brand", "size"]

_ONE_IN_A_DENT_RE = re.compile(r"\s*\(ONE IN A DENT\)\s*", re.IGNORECASE)


def normalize_key_part_value(field_name, value):
    """Normalize one key-field segment. TC values like '104 (ONE IN A DENT)'
    and plain '104' must match — distributors often drop the marketing suffix."""
    if value is None or str(value).strip() == "":
        return ""
    s = str(value).strip()
    if field_name.lower() == "tc":
        s = _ONE_IN_A_DENT_RE.sub("", s).strip()
    return s.upper()


def brands_match_fuzzy(left, right, threshold=0.86):
    """Typo-tolerant brand compare — e.g. Blumen vs Bluemen in distributor files."""
    from difflib import SequenceMatcher

    if left is None or right is None:
        return False
    a = str(left).strip().lower()
    b = str(right).strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True

    # Multi-word brand lines with extra words are different products
    # (e.g. "Urban Living Luxury" vs "Urban Living Luxury / New").
    token_re = re.compile(r"[a-z0-9]+")
    tokens_a = token_re.findall(a)
    tokens_b = token_re.findall(b)
    if tokens_a and tokens_b:
        set_a = set(tokens_a)
        set_b = set(tokens_b)
        if set_a < set_b or set_b < set_a:
            return False

    return SequenceMatcher(None, a, b).ratio() >= threshold


def extract_key_field_value(field, core_fields, extra_attributes):
    field_lower = field.lower()
    if field_lower in core_fields and core_fields[field_lower] not in (None, ""):
        return core_fields[field_lower]
    return next((v for k, v in (extra_attributes or {}).items() if k.lower() == field_lower), None)


def _norm(s):
    return re.sub(r"\s+", " ", str(s).strip().lower()) if pd.notna(s) else ""


def detect_header_row(raw_df, max_scan_rows=15):
    for i in range(min(max_scan_rows, len(raw_df))):
        row_values = [_norm(v) for v in raw_df.iloc[i].tolist()]
        if all(any(marker in val for val in row_values) for marker in REQUIRED_HEADER_MARKERS):
            return i
    raise ValueError("Could not detect header row - no row contains both 'Brand' and 'Size' columns")


def map_columns_to_core(header_row):
    """Returns {excel_col_index: canonical_core_field_name_or_None}."""
    mapping = {}
    for idx, raw_name in enumerate(header_row):
        norm_name = _norm(raw_name)
        matched_core = None
        for core_field, aliases in CORE_FIELD_ALIASES.items():
            if norm_name in aliases:
                matched_core = core_field
                break
        if matched_core is None:
            for core_field, rule in CORE_FIELD_KEYWORD_RULES.items():
                if rule(norm_name):
                    matched_core = core_field
                    break
        mapping[idx] = matched_core
    return mapping


def resolve_core_field_for_name(raw_name):
    """
    Given a single raw column-header string, return the canonical core field
    it maps to (e.g. 'mrp', 'bale_pack_size'), or None if it's a non-core
    field (meaning: look it up in extra_attributes instead).

    Used by the download/export path to rebuild each category's ORIGINAL
    template layout - same alias + keyword-rule logic as map_columns_to_core,
    just for one name at a time instead of a whole header row.
    """
    norm_name = _norm(raw_name)
    for core_field, aliases in CORE_FIELD_ALIASES.items():
        if norm_name in aliases:
            return core_field
    for core_field, rule in CORE_FIELD_KEYWORD_RULES.items():
        if rule(norm_name):
            return core_field
    return None


def detect_category_for_text(text):
    """Match one product/cell/filename text against category keyword rules."""
    normalized = _norm(text)
    if not normalized:
        return None
    for keywords, category in CATEGORY_KEYWORDS:
        if any(kw in normalized for kw in keywords):
            return category
    return None


def detect_category(product_column_values, filename=None):
    """
    Majority-vote category detection across rows.

    Old logic joined ALL product text and returned the first keyword match —
    so one 'comforter' row inside a Bedsheet file forced the whole file to TOB.
    Now each row votes independently; the category with the most votes wins.
    Filename (if provided) contributes one extra vote as a weak hint.
    """
    votes = {}
    for value in product_column_values:
        category = detect_category_for_text(value)
        if category:
            votes[category] = votes.get(category, 0) + 1

    if filename:
        file_hint = detect_category_for_text(Path(filename).stem if filename else "")
        if file_hint:
            # One extra vote — enough to tip ties toward filename intent,
            # not enough to override a clear majority of product rows.
            votes[file_hint] = votes.get(file_hint, 0) + 1

    if not votes:
        return None

    # Highest vote wins; tie-break by fixed priority: Bed > Bath > TOB > TOB Pillow
    # (Bedsheet sheets historically had occasional comforter/pillow mentions.)
    priority = {"Bed": 0, "Bath": 1, "TOB": 2, "TOB Pillow": 3}
    return max(votes.items(), key=lambda item: (item[1], -priority.get(item[0], 99)))[0]


def is_data_row(row_values, col_mapping):
    """
    Real data row only if at least one price-ish core field is populated.
    Filters out section-header rows and blank separator rows.
    """
    price_fields = {"mrp", "ptr", "ex_mill_price"}
    for idx, core_field in col_mapping.items():
        if core_field in price_fields:
            val = row_values[idx] if idx < len(row_values) else None
            if pd.notna(val) and str(val).strip() != "":
                return True
    return False


def build_item_key(core_fields, extra_attributes, key_fields):
    """
    key_fields: ordered list like ["brand", "tc", "size"] from category_master.
    Each entry can be a core field name or an extra_attributes column name
    (matched case-insensitively).
    """
    parts = []
    for field in key_fields:
        field_lower = field.lower()
        if field_lower in core_fields and core_fields[field_lower] not in (None, ""):
            parts.append(normalize_key_part_value(field, core_fields[field_lower]))
        else:
            match = next((v for k, v in extra_attributes.items() if k.lower() == field_lower), None)
            parts.append(normalize_key_part_value(field, match) if match not in (None, "") else "")
    return "|".join(parts)


def parse_article_sheet(
    filepath,
    sheet_name,
    category_key_fields_lookup=None,
    default_key_fields=None,
    forced_category=None,
):
    """
    Parses one Excel sheet into article dicts ready for db.upsert_article().

    Each DATA ROW gets its own category from its Product text (majority-file
    detection is only a fallback / suggested_summary). So a mixed sheet with
    Bedsheet + Towel + TOB rows is fine — each article lands in the right bucket.

    forced_category: if set, overrides EVERY row's category (manual user override).

    Returns: (clean_articles, suggested_category, is_new_category, needs_review,
              category_breakdown)
      suggested_category = majority / filename hint for the confirm dialog
      category_breakdown = {"Bed": 40, "TOB": 2, ...} so UI can show the mix
    """
    category_key_fields_lookup = category_key_fields_lookup or {}
    default_key_fields = default_key_fields or ["brand", "size"]

    raw_df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)

    header_idx = detect_header_row(raw_df)
    header_row = raw_df.iloc[header_idx].tolist()
    col_mapping = map_columns_to_core(header_row)

    product_col_idx = next((idx for idx, f in col_mapping.items() if f == "product_type"), None)
    if product_col_idx is None:
        raise ValueError("Could not find a 'Product' column for category detection")

    data_rows = raw_df.iloc[header_idx + 1:]
    valid_rows = [row for _, row in data_rows.iterrows() if is_data_row(row.tolist(), col_mapping)]
    product_values = [row.iloc[product_col_idx] for row in valid_rows]

    suggested_category = detect_category(product_values, filename=str(filepath))
    if not suggested_category:
        suggested_category = "UNCATEGORIZED - REVIEW"

    articles = []
    category_breakdown = {}
    for row in valid_rows:
        core_fields = {}
        extra_attributes = {}
        for idx, core_field in col_mapping.items():
            if idx >= len(row):
                continue
            val = row.iloc[idx]
            if pd.isna(val):
                val = None
            else:
                val = _json_safe(val)
            raw_col_name = str(header_row[idx]).strip()
            if core_field:
                core_fields[core_field] = val
            elif raw_col_name and raw_col_name.lower() != "nan":
                extra_attributes[raw_col_name] = val

        if forced_category:
            row_category = forced_category.strip()
        else:
            row_category = detect_category_for_text(core_fields.get("product_type"))
            if not row_category:
                # Unknown product text → fall back to file-level majority suggestion
                row_category = suggested_category

        key_fields = category_key_fields_lookup.get(row_category, default_key_fields)
        item_key = build_item_key(core_fields, extra_attributes, key_fields)

        articles.append({
            "category": row_category,
            "product_type": core_fields.get("product_type"),
            "brand": core_fields.get("brand"),
            "size": core_fields.get("size"),
            "mrp": core_fields.get("mrp"),
            "ptr": core_fields.get("ptr"),
            "ex_mill_price": core_fields.get("ex_mill_price"),
            "bale_pack_size": core_fields.get("bale_pack_size"),
            "item_key": item_key,
            "extra_attributes": extra_attributes,
        })
        category_breakdown[row_category] = category_breakdown.get(row_category, 0) + 1

    key_counts = {}
    for a in articles:
        key_counts[a["item_key"]] = key_counts.get(a["item_key"], 0) + 1

    clean_articles = [a for a in articles if key_counts[a["item_key"]] == 1]
    needs_review = [a for a in articles if key_counts[a["item_key"]] > 1]

    # is_new_category if ANY used category isn't configured yet
    used_categories = set(category_breakdown.keys())
    is_new_category = any(cat not in category_key_fields_lookup for cat in used_categories)

    return clean_articles, suggested_category, is_new_category, needs_review, category_breakdown
