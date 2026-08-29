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


# Booking / planning columns that must never enter Article Master (extra_attributes
# or downloads). Highlighted in exported Article_Master_All.xlsx as cols P,Q,S,T,V,AG.
# Match is case/spacing-insensitive on the Excel header.
EXCLUDED_EXTRA_COLUMN_NAMES = {
    "aug - sep delivery",
    "aug-sep delivery",
    "sep - oct delivery",
    "sep-oct delivery",
    "qnty per color",
    "qty per color",
    "quantity per color",
    "qnty pre design",
    "qnty per design",
    "qty per design",
    "quantity per design",
    "no of sets per design",
    "selling price",
    "total moq qnty in sets",
    "total moq qty in sets",
    "total moq quantity in sets",
    "qnty",  # trailing empty booking qty on AW26 template
    "booking qnty",
    "booking qty",
    "bookingqnty",
    "order qty",
    "order value",
    # proposed customer discount = Perceived — kept (see STICKY / aliases), not excluded
    "min bale pack size",
    "no of design",
    "no of designs",
    "aw'25 designs",
    "aw25 designs",
    # Booking colorway *counts* (not SKU Color). SKU Color kept for Bath (from Shade).
    "colors",
    "colours",
    "colorways",
    "no of colorways",
    "no of colourways",
    "no of color",
    "no of colours",
    "total colours",
    "total colors",
    # Towel booking noise
    "description",
    "sl no",
    "sl. no",
    "s.no",
    "s no",
    "delivery date",
    "qty in bales",
    "awd order in no of bales",
    "awd order in no. of bales",
    "awds order in no of bales",
    "awds order in no. of bales",
    # TOB / Pillow booking noise
    "moq per design/color",
    "moq per design / color",
    "moq per design color",
    "dyed / printed option",
    "dyed/printed option",
    "print colorways",
    "print option",
    "option",
    "delivery months (no. of bales)",
    "delivery months",
}

# Bed still drops SKU Color (bedsheet teaching). Bath keeps Color.
BED_EXCLUDED_EXTRA_COLUMN_NAMES = {
    "color",
    "colour",
    "shade",
}


def normalize_extra_column_name(name: str | None) -> str:
    text = " ".join(str(name or "").strip().lower().split())
    text = text.replace("–", "-").replace("—", "-")
    return text


def is_excluded_extra_column(name: str | None, *, category: str | None = None) -> bool:
    key = normalize_extra_column_name(name)
    if key in EXCLUDED_EXTRA_COLUMN_NAMES:
        return True
    if str(category or "").strip() == "Bed" and key in BED_EXCLUDED_EXTRA_COLUMN_NAMES:
        return True
    return False


def strip_excluded_extra_attributes(extra: dict | None, *, category: str | None = None) -> dict:
    """Drop booking/planning keys from extra_attributes (upload + DB cleanup)."""
    if not isinstance(extra, dict):
        return {}
    return {
        k: v for k, v in extra.items()
        if not is_excluded_extra_column(k, category=category)
    }


def is_blank_attr_value(value) -> bool:
    """True when upload cell is empty / NaN — must not wipe an existing AM value."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    try:
        # pandas NA / NaT
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return False


# Schema-gap attrs: any source may fill; later non-blank updates; blank keeps old.
# Map normalized alias → canonical storage key (matches Article Master export headers).
STICKY_SCHEMA_ATTR_ALIASES = {
    "pillow size": "Pillow Size",
    "pillow size (cms)": "Pillow Size",
    "pillow stitching style": "Pillow Stitching Style",
    "print style": "Print Style",
    "print/dyed/weave": "Print Style",
    "print dyed weave": "Print Style",
    "blend": "Blend",
    "packing": "Packing",
    "bs size": "BS Size",
    "bedset size": "BS Size",
    "bedset size (cms)": "BS Size",
    "bed set size": "BS Size",
    # Bath / Towel: Shade → Color (SKU identity)
    "shade": "Color",
    "color": "Color",
    "colour": "Color",
    # Margins from towel booking headers
    "awd mu": "AWD Mark up on Exmill",
    "awd md": "AWD Mark up on Exmill",
    "awd mark up on exmill": "AWD Mark up on Exmill",
    "awd markup on exmill": "AWD Mark up on Exmill",
    "distributor margin": "AWD Mark up on Exmill",
    "retailer md": "Retailer Margin",
    "retailer margin": "Retailer Margin",
    "retail mark down": "Retailer Margin",
    "retailer markdown": "Retailer Margin",
    # Proposed Customer Discount ≡ Perceived (same margin field)
    "perceived": "Proposed Customer Discount",
    "perceive": "Proposed Customer Discount",
    "perceived margin": "Proposed Customer Discount",
    "proposed customer discount": "Proposed Customer Discount",
    "proposed cust discount": "Proposed Customer Discount",
    "proposed cust. discount": "Proposed Customer Discount",
    # Pillow / bedsheet Units
    "unit": "Units",
    "units": "Units",
}


def canonical_sticky_attr_key(name: str | None) -> str | None:
    """Return canonical sticky key if this header is a sticky schema attr, else None."""
    return STICKY_SCHEMA_ATTR_ALIASES.get(normalize_extra_column_name(name))


# --- Spelling: majority vote (2+ files) + correct English -----------------
# Brand family: correct spelling is Blumen (Bluemen/Bluman are typos/aliases).
BRAND_SPELLING_CANONICAL = {
    "blumen": "Blumen",
    "bluemen": "Blumen",
    "bluman": "Blumen",
    "aster": "Aster",
    "cardinal": "Cardinal",
    # Typo fix only — Celebrating India vs Celebrating India (BINB) stay DIFFERENT items.
    "celebareting india": "Celebrating India",
    "celebrating india": "Celebrating India",
    "celebareting india (binb)": "Celebrating India (BINB)",
    "celebrating india (binb)": "Celebrating India (BINB)",
    # Towel / Bath brands
    "bd white": "BD White",
    "gym towel": "Gym Towel",
    "huk a buk": "Huk A Buk",
    "lepord": "Leopard",
    "eco stripe": "Eco Stripe",
    # Pillow brand typo
    "comfot gusset pillow": "Comfort Gusset Pillow",
}

# "Brand KING" rows → base brand + Size KS BS (taught lock).
BRAND_KING_TO_BASE = {
    "CARDINAL KING": "Cardinal",
    "EPIGRAM KING": "Epigram",
}

# Cotton Satin stripe naming variants → one collection.
STRIPE_BRAND_ALIASES = {
    "COTTON SATIN WITH 1 CM STRIPE",
    "COTTON SATIN STRIPE (1 CMS)",
    "COTTON SATIN WITH SMALL STRIPE",
    "COTTON SATIN WITH 1 CM STRIP",
    "COTTON SATIN STRIPE 1 CM",
    "COTTON SATIN WITH 1CM STRIPE",
}
STRIPE_BRAND_CANONICAL = "Cotton Satin With 1 CM Stripe"

# Size short-code dictionary (merge identity). Display names are separate.
SIZE_CODE_ALIASES = {
    "DBL BS": "DB BS",
    "DBL": "DB BS",
    "KDB BS": "KS BS",
    "KDB": "KS BS",
    "DB FITTED SHEET": "DB FS",
    "KDB FITTED SHEET": "KB FS",
    "KB FITTED SHEET": "KB FS",
    "KS FITTED SHEET": "KB FS",
    "KS SETS": "KS BS",
    "KS SET": "KS BS",
    # Full taught names → short codes (round-trip / re-upload safe)
    "SINGLE BEDSHEET": "SB BS",
    "DOUBLE BEDSHEET": "DB BS",
    "KING BEDSHEET": "KS BS",
    "DOUBLE FITTED SHEET": "DB FS",
    "DOUBLE BED FITTED SHEET": "DB FS",
    "KING FITTED SHEET": "KB FS",
    "KING BED FITTED SHEET": "KB FS",
    "DOUBLE COMFORTER": "DB Comf",
    "DOUBLE REVERSIBLE COMFORTER": "DB Reversible Comf",
    "DOUBLE REVERSIBLE COMF": "DB Reversible Comf",
    "DOUBLE DUVET COVER": "DB Duvet Cover",
    # Towel / Bath — store taught display names in Size
    "40X60": "Hand Towel",
    "75X150": "Bath Towel",
    "60X120": "Ladies Towel",
    "90X180": "Pool Towel",
    "R4": "Towel Set",
    "30X30": "Face Towel",
    "72X144": "Bath Towel",
    "50X70": "Bath Mat",
    "50X100": "Gym Towel",
    "91X100": "91x100",
    "L": "Large",
    "XL": "Extra Large",
    "XXL": "Double Extra Large",
    "HAND TOWEL": "Hand Towel",
    "BATH TOWEL": "Bath Towel",
    "LADIES TOWEL": "Ladies Towel",
    "POOL TOWEL": "Pool Towel",
    "TOWEL SET": "Towel Set",
    "FACE TOWEL": "Face Towel",
    "BATH MAT": "Bath Mat",
    "GYM TOWEL": "Gym Towel",
    "HAND TOWEL SET OF 2": "Hand Towel Set of 2",
    "FACE TOWEL SET OF 3": "Face Towel Set of 3",
    "LARGE": "Large",
    "EXTRA LARGE": "Extra Large",
    "DOUBLE EXTRA LARGE": "Double Extra Large",
}

# Column D / UI / download — taught full names.
SIZE_DISPLAY_NAMES = {
    "SB BS": "Single Bedsheet",
    "DB BS": "Double Bedsheet",
    "KS BS": "King Bedsheet",
    "DB FS": "Double Fitted Sheet",
    "KB FS": "King Fitted Sheet",
    "DB COMF": "Double Comforter",
    "DB REVERSIBLE COMF": "Double Reversible Comforter",
    "DB DUVET COVER": "Double Duvet Cover",
    # Towel display names (stored size already full name)
    "HAND TOWEL": "Hand Towel",
    "BATH TOWEL": "Bath Towel",
    "LADIES TOWEL": "Ladies Towel",
    "POOL TOWEL": "Pool Towel",
    "TOWEL SET": "Towel Set",
    "FACE TOWEL": "Face Towel",
    "BATH MAT": "Bath Mat",
    "GYM TOWEL": "Gym Towel",
    "HAND TOWEL SET OF 2": "Hand Towel Set of 2",
    "FACE TOWEL SET OF 3": "Face Towel Set of 3",
    "LARGE": "Large",
    "EXTRA LARGE": "Extra Large",
    "DOUBLE EXTRA LARGE": "Double Extra Large",
    "91X100": "91x100",
}

_CELEBARETING_RE = re.compile(r"(?i)\bcelebareting\b")


def _size_lookup_key(size) -> str:
    return re.sub(r"\s+", " ", str(size or "").strip()).upper()


def _preprocess_towel_size_raw(raw_size):
    """
    Bath special-order sheets use * dimensions (40*60), R4SET, and BATHROBE rows.
    Normalize before towel_physical_size_code / normalize_size_code.
    """
    if is_blank_attr_value(raw_size):
        return raw_size
    s = re.sub(r"\s+", " ", str(raw_size).strip())
    compact = re.sub(r"\s+", "", s).upper()
    if compact in {"R4SET", "R4"}:
        return "R4"
    if compact == "BATHROBE":
        return "BATHROBE"
    s = re.sub(r"(\d+)\s*\*\s*(\d+)", r"\1x\2", s)
    return s


def normalize_size_code(size, *, force_king_bs: bool = False):
    """
    Canonical short size code for merge identity (DB BS, KS BS, DB FS, …).
    Towel/Bath sizes normalize to taught display names (Hand Towel, …).
    force_king_bs: when brand was 'Cardinal KING' / 'Epigram King'.
    """
    if is_blank_attr_value(size):
        if force_king_bs:
            return "KS BS"
        return size
    size = _preprocess_towel_size_raw(size)
    s = re.sub(r"\s+", " ", str(size).strip())
    key = s.upper()
    if key in SIZE_CODE_ALIASES:
        return SIZE_CODE_ALIASES[key]

    # Brand KING lock: Cardinal KING / Epigram King rows are always KS BS.
    if force_king_bs and "FITTED" not in key:
        return "KS BS"

    # Punctuation-insensitive aliases from distributor files:
    # DBL B.S / DB.BS / KDB-BS / etc should resolve to taught codes.
    compact_key = re.sub(r"[^A-Z0-9]", "", key)
    compact_alias = {
        "SBBS": "SB BS",
        "DBBS": "DB BS",
        "DBLBS": "DB BS",
        "KSBS": "KS BS",
        "KDBBS": "KS BS",
        "DBFS": "DB FS",
        "SBFS": "SB FS",
        "KBFS": "KB FS",
        "KSFS": "KB FS",
    }
    if compact_key in compact_alias:
        return compact_alias[compact_key]

    # Towel set forms before generic compact lookup
    compact = re.sub(r"[^0-9A-Z]", "", key)
    if (
        re.fullmatch(r"40X60\(2PC\)|40X60\(SETOF2\)|40X60SETOF2", compact)
        or ("SET OF 2" in key and "40" in compact and "60" in compact)
        or re.search(r"40X60.*2PC|40X60\(2", compact)
    ):
        return "Hand Towel Set of 2"
    if (
        re.fullmatch(r"30X30\(3PC\)|30X30\(SETOF3\)", compact)
        or ("SET OF 3" in key and "30" in compact)
        or re.search(r"30X30.*3PC|30X30\(3", compact)
    ):
        return "Face Towel Set of 3"
    # 40x60 / 75x150 / R4 / L …
    for code, display in (
        ("40X60", "Hand Towel"),
        ("75X150", "Bath Towel"),
        ("60X120", "Ladies Towel"),
        ("90X180", "Pool Towel"),
        ("30X30", "Face Towel"),
        ("72X144", "Bath Towel"),
        ("50X70", "Bath Mat"),
        ("50X100", "Gym Towel"),
        ("91X100", "91x100"),
        ("R4", "Towel Set"),
    ):
        if compact == code:
            return display
    if compact in {"L", "XL", "XXL"}:
        return SIZE_CODE_ALIASES.get(compact, s)

    # Token fixes: DBL→DB, KDB→KS (word boundaries)
    s2 = re.sub(r"(?i)\bDBL\b", "DB", s)
    s2 = re.sub(r"(?i)\bKDB\b", "KS", s2)
    if re.search(r"(?i)fitted\s*sheet", s2):
        if re.match(r"(?i)^(KDB|KB|KS)\b", s2) or re.search(r"(?i)\bking\b", s2):
            return "KB FS"
        if re.match(r"(?i)^SB\b", s2) or re.search(r"(?i)\bsingle\b", s2):
            return "SB FS"
        return "DB FS"
    if force_king_bs:
        return "KS BS"
    return s2


def size_display_name(size) -> str | None:
    """Taught full name for UI/Excel Size column. Unknown codes pass through."""
    if is_blank_attr_value(size):
        return None
    code = normalize_size_code(size)
    if is_blank_attr_value(code):
        return None
    key = _size_lookup_key(code)
    return SIZE_DISPLAY_NAMES.get(key, str(code).strip())


def normalize_brand_spelling(brand):
    if is_blank_attr_value(brand):
        return brand
    text = str(brand).strip()
    text = _CELEBARETING_RE.sub("Celebrating", text)
    text = re.sub(r"(?i)\bComfot\b", "Comfort", text)
    lookup = re.sub(r"\s+", " ", text).strip().lower()
    canon = BRAND_SPELLING_CANONICAL.get(lookup)
    if canon:
        return canon
    # Stripe collection aliases (case-insensitive exact)
    upper = re.sub(r"\s+", " ", text).strip().upper()
    if upper in STRIPE_BRAND_ALIASES or upper.replace("STRIP", "STRIPE") in STRIPE_BRAND_ALIASES:
        return STRIPE_BRAND_CANONICAL
    return text


def normalize_brand_and_size(brand, size):
    """
    Apply brand spelling + KING→base+KS BS + stripe rename + size dictionary.
    Returns (brand, size_code). Wonder Land Glow vs Kids stay separate (no alias).
    """
    force_king = False
    if not is_blank_attr_value(brand):
        text = str(brand).strip()
        text = _CELEBARETING_RE.sub("Celebrating", text)
        upper = re.sub(r"\s+", " ", text).strip().upper()
        if upper in BRAND_KING_TO_BASE:
            brand = BRAND_KING_TO_BASE[upper]
            force_king = True
        else:
            brand = normalize_brand_spelling(brand)
    if not is_blank_attr_value(size):
        size_key = re.sub(r"\s+", "", str(size).strip()).upper()
        if size_key == "BATHROBE":
            if brand and "bathrobe" not in str(brand).lower():
                base = str(brand).strip().title()
                brand = normalize_brand_spelling(f"{base} Bathrobe")
            size = "L"
    size = normalize_size_code(size, force_king_bs=force_king)
    return brand, size


# Season tags for last-3 price columns (SS-25 / SS-26 / AW-26 …).
SEASON_RANK = {
    "SS-24": 20240,
    "AW-24": 20241,
    "SS-25": 20250,
    "AW-25": 20251,
    "SS-26": 20260,
    "AW-26": 20261,
    "SS-27": 20270,
    "AW-27": 20271,
}


def normalize_season_tag(tag):
    if is_blank_attr_value(tag):
        return None
    text = re.sub(r"\s+", "", str(tag).strip().upper().replace("_", "-"))
    m = re.fullmatch(r"(SS|AW)(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(SS|AW)-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return str(tag).strip().upper()


def season_rank(tag) -> int:
    canon = normalize_season_tag(tag)
    if not canon:
        return -1
    if canon in SEASON_RANK:
        return SEASON_RANK[canon]
    m = re.fullmatch(r"(SS|AW)-(\d{2})", canon or "")
    if m:
        base = 20000 + int(m.group(2)) * 10
        return base + (1 if m.group(1) == "AW" else 0)
    return 0


def suggest_season_tag_from_filename(filename) -> str | None:
    """Guess SS-25 / AW-26 etc. from booking / order sheet filenames."""
    if not filename:
        return None
    name = Path(str(filename)).stem.upper()
    name = name.replace("_", " ").replace("-", " ")
    for season in ("27", "26", "25", "24"):
        if re.search(rf"\bAW\s*{season}\b", name) or re.search(rf"\bAW{season}\b", name):
            return f"AW-{season}"
        if re.search(rf"\bSS\s*{season}\b", name) or re.search(rf"\bSS{season}\b", name):
            return f"SS-{season}"
    return None


def is_season_newer_or_equal(incoming_tag, existing_tag) -> bool:
    """True if incoming should win as article 'latest' prices."""
    inc = normalize_season_tag(incoming_tag)
    if not inc:
        return True
    cur = normalize_season_tag(existing_tag)
    if not cur:
        return True
    return season_rank(inc) >= season_rank(cur)


# User preference: Sheet Sets → Bedsheet (Fitted Sheet / Comforter / Bed In Bag stay as-is).
PRODUCT_SPELLING_CANONICAL = {
    "sheet sets": "Bedsheet",
    "sheet set": "Bedsheet",
    "towel": "Terry Towel",
    "terry towel": "Terry Towel",
    "bathrobe": "Bathrobe",
    "bathmat": "Bathmat Antiskid",
    "bath mat": "Bathmat Antiskid",
    "bathmat (anti skid)": "Bathmat Antiskid",
    "bathmat anti skid": "Bathmat Antiskid",
    "bathmat antiskid": "Bathmat Antiskid",
    "bed in a bga": "Bed in a Bag",
    "bed in a bag": "Bed in a Bag",
}

# Generic Product labels that Size can override (e.g. DB Reversible Comf → Comforter).
_GENERIC_BED_PRODUCTS = frozenset({
    "bedsheet",
    "sheet set",
    "sheet sets",
})

_PACKING_WORD_FIXES = (
    (re.compile(r"(?i)\bEnvelop\b(?!e)"), "Envelope"),
    (re.compile(r"(?i)\bFome\b"), "Foam"),
)


def infer_product_from_size(size):
    """Map size code / display name → Product when Size already names the good."""
    key = _size_lookup_key(size)
    if not key:
        return None
    if "DUVET" in key:
        return "Duvet Cover"
    if "COMF" in key or "COMFORTER" in key:
        return "Comforter"
    if key.endswith(" FS") or "FITTED" in key:
        return "Fitted Sheet"
    if key.endswith(" BS") or "BEDSHEET" in key:
        return "Bedsheet"
    return None


def infer_category_from_size(size, product=None):
    """Comforter = TOB. Duvet Cover / Bed In Bag stay Bed."""
    prod = str(product or "").strip().lower()
    if "bed in bag" in prod or prod in {"binb", "b.i.n.b"}:
        return None
    key = _size_lookup_key(size)
    if not key and not prod:
        return None
    # Duvet Cover counts under Bed (not TOB)
    if "DUVET" in key or "duvet" in prod:
        return "Bed"
    if "COMF" in key or "COMFORTER" in key or "comforter" in prod:
        return "TOB"
    return None


def resolve_product_type(product, size):
    """Prefer Size-derived Product over blank / generic Bedsheet."""
    inferred = infer_product_from_size(size)
    normalized = normalize_product_spelling(product)
    if inferred is None:
        return normalized
    if is_blank_attr_value(normalized):
        return inferred
    if str(normalized).strip().lower() in _GENERIC_BED_PRODUCTS and inferred != "Bedsheet":
        return inferred
    return normalized


def resolve_category(category, size, product=None):
    """Keep comforter/duvet under TOB even if uploaded from a Bed sheet."""
    inferred = infer_category_from_size(size, product)
    if inferred:
        return inferred
    return category


def normalize_product_spelling(product):
    if is_blank_attr_value(product):
        return product
    text = str(product).strip()
    text = re.sub(r"\s+", " ", text)
    lookup = text.lower()
    if lookup in PRODUCT_SPELLING_CANONICAL:
        return PRODUCT_SPELLING_CANONICAL[lookup]
    # Bathmat… → Bathmat Antiskid (any suffix / spacing)
    key = re.sub(r"[^a-z0-9]+", " ", lookup).strip()
    if key.startswith("bathmat"):
        return "Bathmat Antiskid"
    return text


_PLY_DISPLAY = {
    "SNL-PLY": "Single Ply",
    "SNL PLY": "Single Ply",
    "1-PLY": "1 Ply",
    "1 PLY": "1 Ply",
    "2-PLY": "2 Ply",
    "2 PLY": "2 Ply",
    "3-PLY": "3 Ply",
    "3 PLY": "3 Ply",
}


def normalize_tob_quality(quality):
    if is_blank_attr_value(quality):
        return None
    text = re.sub(r"\s+", " ", str(quality).strip())
    text = re.sub(r"(?i)\bpolyster\b", "Polyester", text)
    text = re.sub(r"(?i)\bpolyester\b", "Polyester", text)
    text = re.sub(r"(?i)\balovera\b", "Aloe Vera", text)
    return text


def normalize_tob_ply(ply):
    if is_blank_attr_value(ply):
        return None
    raw = re.sub(r"\s+", " ", str(ply).strip())
    mapped = _PLY_DISPLAY.get(raw.upper())
    if mapped:
        return mapped
    m = re.match(r"^(\d+)\s*-?\s*ply$", raw, re.IGNORECASE)
    if m:
        return f"{m.group(1)} Ply"
    if re.match(r"^snl", raw, re.IGNORECASE):
        return "Single Ply"
    return raw


def normalize_tob_weight(weight):
    """Weight as-is into Blend (teaching lock) — only normalize whitespace / int floats."""
    if is_blank_attr_value(weight):
        return None
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        try:
            if float(weight).is_integer():
                return str(int(weight))
        except (TypeError, ValueError):
            pass
        return str(weight)
    text = re.sub(r"\s+", " ", str(weight).strip())
    if text in {"-", "—", "–"}:
        return None
    return text


def build_tob_blend(quality, ply=None, weight=None):
    """TOB: Quality + Ply + Weight → Blend. Pillow: pass ply=None (Quality + Weight only)."""
    q = normalize_tob_quality(quality)
    p = normalize_tob_ply(ply)
    w = normalize_tob_weight(weight)
    head = " ".join(x for x in (q, p) if x)
    if head and w:
        return f"{head}, {w}"
    return head or w


def _pop_extra_by_norm(extra: dict, *norm_names):
    """Remove and return first matching extra value by normalized header name."""
    want = {normalize_extra_column_name(n) for n in norm_names}
    for key in list(extra.keys()):
        if normalize_extra_column_name(key) in want:
            val = extra.pop(key)
            if not is_blank_attr_value(val):
                return val
    return None


def apply_tob_pillow_blend_and_units(extra_attributes: dict, category: str) -> dict:
    """
    Compose Blend from Quality/Ply/Weight for TOB; Quality/Weight for Pillow.
    Map Unit → Units. Drop blend source columns from extras.
    """
    if not isinstance(extra_attributes, dict):
        return {}
    extra = dict(extra_attributes)
    cat = str(category or "").strip()
    if cat not in {"TOB", "Pillow"}:
        return extra

    quality = _pop_extra_by_norm(extra, "quality")
    ply = _pop_extra_by_norm(extra, "ply") if cat == "TOB" else None
    if cat == "Pillow":
        # Pillow has no Ply column — drop if present
        _pop_extra_by_norm(extra, "ply")
    weight = _pop_extra_by_norm(extra, "weight in gram", "weight in grams", "weight")
    units = _pop_extra_by_norm(extra, "unit", "units")

    blend = build_tob_blend(quality, ply if cat == "TOB" else None, weight)
    if blend and is_blank_attr_value(extra.get("Blend")):
        extra["Blend"] = blend
    elif blend:
        extra["Blend"] = blend

    if not is_blank_attr_value(units):
        if isinstance(units, float) and float(units).is_integer():
            units = int(units)
        extra["Units"] = units

    return extra


# Reverse map: taught Bath Size name → default physical code (UI gap-fill / reverse)
TOWEL_DISPLAY_TO_PHYSICAL = {
    "HAND TOWEL": "40x60",
    "HAND TOWEL SET OF 2": "40x60(2pc)",
    "FACE TOWEL": "30x30",
    "FACE TOWEL SET OF 3": "30x30(3pc)",
    "LADIES TOWEL": "60x120",
    "BATH TOWEL": "75x150",
    "BATH MAT": "50x70",
    "POOL TOWEL": "90x180",
    "TOWEL SET": "R4",
    "GYM TOWEL": "50x100",
    "91X100": "91x100",
    "LARGE": "L",
    "EXTRA LARGE": "XL",
    "DOUBLE EXTRA LARGE": "XXL",
}


def towel_physical_size_code(raw_size):
    """
    Booking-sheet physical size for the BS Size / UI 'Size' column (75x150, 40x60(2pc), …).
    Call on the raw cell *before* display-name normalize when possible.
    """
    if is_blank_attr_value(raw_size):
        return None
    raw_size = _preprocess_towel_size_raw(raw_size)
    s = re.sub(r"\s+", " ", str(raw_size).strip())
    key = re.sub(r"\s+", "", s).upper()
    lookup = _size_lookup_key(s)
    if lookup in TOWEL_DISPLAY_TO_PHYSICAL:
        return TOWEL_DISPLAY_TO_PHYSICAL[lookup]
    if (
        re.fullmatch(r"40X60\(2PC\)|40X60\(SETOF2\)|40X60SETOF2", key)
        or ("SET OF 2" in s.upper() and "40" in key and "60" in key)
        or re.search(r"40X60.*2PC|40X60\(2", key)
    ):
        return "40x60(2pc)"
    if (
        re.fullmatch(r"30X30\(3PC\)|30X30\(SETOF3\)", key)
        or ("SET OF 3" in s.upper() and "30" in key)
        or re.search(r"30X30.*3PC|30X30\(3", key)
    ):
        return "30x30(3pc)"
    m = re.fullmatch(r"(\d+)\s*[xX×*]\s*(\d+)(?:\s*[-–]?\s*\d+\s*PCS)?", s, flags=re.IGNORECASE)
    if m:
        return f"{int(m.group(1))}x{int(m.group(2))}"
    if key in {"R4", "L", "XL", "XXL"}:
        return key
    if key == "72X144":
        return "72x144"
    if key == "50X100":
        return "50x100"
    if key == "91X100":
        return "91x100"
    if key == "50X70":
        return "50x70"
    if key == "BATHROBE":
        return "L"
    return s


_PVC_BAG_RE = re.compile(r"\(?\s*PVC\s*bag\s*Pkg\.?\s*\)?", re.IGNORECASE)
_PKG_L_RE = re.compile(r"\(\s*L\s*\)|\(\s*Pkg\.?\s*\)", re.IGNORECASE)


def normalize_towel_color_and_packing(shade_or_color, description=None):
    """
    Towel Shade/Color teaching:
      Assorted/Asst/Asorted → Assorted NN (zero-padded);
      Jacquarad → Jacquard; WHITE → White;
      PVC (shade or description) → Packing 'PVC bag Pkg';
      (L)/(Pkg) notes ignored on Color (drop pkg-only sibling later).
    Returns (color, packing, is_pkg_only).
    """
    text = "" if is_blank_attr_value(shade_or_color) else re.sub(r"\s+", " ", str(shade_or_color).strip())
    desc = "" if is_blank_attr_value(description) else re.sub(r"\s+", " ", str(description).strip())
    blob = f"{text} {desc}".strip()
    has_pvc = bool(_PVC_BAG_RE.search(blob))
    has_pkg_l = bool(
        re.search(r"\(\s*L\s*\)", blob, re.IGNORECASE)
        or (re.search(r"\bPkg\b", blob, re.IGNORECASE) and not has_pvc)
    )
    packing = "PVC bag Pkg" if has_pvc else None
    text = _PVC_BAG_RE.sub("", text)
    text = _PKG_L_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    if not text:
        return None, packing, has_pkg_l and not has_pvc
    if re.fullmatch(r"jacquarad|jacquard", text, flags=re.IGNORECASE):
        return "Jacquard", packing, has_pkg_l and not has_pvc
    m = re.fullmatch(
        r"(?:Assorted|Asorted|Assortede|Asst\.?|Asst)\s*[- ]*0*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return f"Assorted {int(m.group(1)):02d}", packing, has_pkg_l and not has_pvc
    if text.upper() == "WHITE":
        return "White", packing, has_pkg_l and not has_pvc
    return text, packing, has_pkg_l and not has_pvc


def normalize_packing_spelling(packing):
    """Envelop→Envelope, Fome→Foam; leave other packing phrases intact."""
    if is_blank_attr_value(packing):
        return packing
    text = str(packing).strip()
    if text.lower() in {"envelop", "envelope"}:
        return "Envelope"
    fixed = text
    for pattern, repl in _PACKING_WORD_FIXES:
        fixed = pattern.sub(repl, fixed)
    return fixed


def normalize_article_spellings(article: dict) -> dict:
    """Apply vote/English spelling fixes onto a parsed/merged article dict."""
    if not isinstance(article, dict):
        return article
    brand, size = normalize_brand_and_size(article.get("brand"), article.get("size"))
    if "brand" in article or brand is not None:
        article["brand"] = brand
    if "size" in article or size is not None:
        article["size"] = size
    if "product_type" in article or "size" in article:
        article["product_type"] = resolve_product_type(
            article.get("product_type"), article.get("size"),
        )
    if "category" in article or "size" in article:
        article["category"] = resolve_category(
            article.get("category"), article.get("size"), article.get("product_type"),
        )
    extra = article.get("extra_attributes")
    if isinstance(extra, dict) and extra:
        cleaned = {}
        for key, val in extra.items():
            if canonical_sticky_attr_key(key) == "Packing" or normalize_extra_column_name(key) == "packing":
                cleaned[key] = normalize_packing_spelling(val)
            else:
                cleaned[key] = val
        # Collapse packing onto canonical sticky key after spelling fix
        if any(canonical_sticky_attr_key(k) == "Packing" for k in list(cleaned)):
            pack_val = None
            for k in list(cleaned):
                if canonical_sticky_attr_key(k) == "Packing":
                    if not is_blank_attr_value(cleaned[k]):
                        pack_val = cleaned[k]
                    del cleaned[k]
            if pack_val is not None:
                cleaned["Packing"] = normalize_packing_spelling(pack_val)
        article["extra_attributes"] = strip_excluded_extra_attributes(cleaned)
    return article


def format_percent_display(value):
    """0.4 → '40%'; 40 → '40%'. Blank → None."""
    if is_blank_attr_value(value):
        return None
    if isinstance(value, str) and "%" in value:
        return value.strip()
    try:
        num = float(str(value).strip().replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return value
    if abs(num) <= 1:
        pct = int(round(num * 100))
    else:
        pct = int(round(num)) if abs(num - round(num)) < 1e-9 else round(num, 1)
    return f"{pct}%"


def merge_extra_attributes(existing: dict | None, incoming: dict | None, *, overwrite_nonblank: bool = True) -> dict:
    """
    Merge upload extras onto stored extras.

    - Excluded booking columns never enter the result.
    - Blank / missing incoming values do NOT overwrite existing values
      (Pillow Size, Stitching Style, Print Style, Blend, Packing, and any other
      extra key — blank means "not provided", not "clear").
    - Non-blank incoming values update when overwrite_nonblank=True (latest season).
    - When overwrite_nonblank=False (older season upload), only gap-fill missing keys.
    - Sticky attrs collapse onto one canonical key so 'BLEND' and 'Blend' don't both linger.
    """
    base = strip_excluded_extra_attributes(existing)
    incoming = strip_excluded_extra_attributes(incoming)

    # Start from existing, but collapse sticky aliases onto canonical keys first.
    merged: dict = {}
    sticky_present: dict[str, object] = {}
    for key, val in base.items():
        canon = canonical_sticky_attr_key(key)
        if canon:
            if not is_blank_attr_value(val):
                if canon == "Packing":
                    val = normalize_packing_spelling(val)
                sticky_present[canon] = val
            # drop alias forms; re-add canonical below
            continue
        merged[key] = val

    for key, val in incoming.items():
        if is_blank_attr_value(val):
            continue
        canon = canonical_sticky_attr_key(key)
        if canon:
            if canon == "Packing":
                val = normalize_packing_spelling(val)
            if overwrite_nonblank or canon not in sticky_present:
                sticky_present[canon] = val
            continue
        # Non-sticky: prefer incoming key spelling; drop case-duplicate old keys
        norm = normalize_extra_column_name(key)
        already = None
        for old_key in list(merged.keys()):
            if normalize_extra_column_name(old_key) == norm:
                already = old_key
                break
        if already is not None and not overwrite_nonblank:
            continue
        if already is not None and already != key:
            del merged[already]
        merged[key] = val

    merged.update(sticky_present)
    return strip_excluded_extra_attributes(merged)

# Column-name aliases -> canonical core field name.
# Add new aliases here if a future file uses a different header wording —
# no other code needs to change.
# Exact-match aliases (fast path). Case/whitespace normalized before comparison.
CORE_FIELD_ALIASES = {
    # GT SS-25 booking forms use "Collection/Brand Name" instead of "Brand"
    "brand": ["brand", "collection/brand name", "collection / brand name", "brand name"],
    "product_type": ["product"],
    "size": ["size"],
    "mrp": ["mrp"],
    "ptr": ["ptr", "tentative (ptr)", "tentative ptr"],
    "ex_mill_price": [
        "ex-mill", "ex mill", "exmill", "exmill price",
        "ex-mill per pcs", "ex mill per pcs", "exmill rate",
    ],
    "bale_pack_size": [
        "bale size", "bale pack size", "bale pack sizes",
        "pack sizes", "pack size",
    ],
}

# Default Product label when a booking sheet has no Product column (e.g. GT SS-25).
DEFAULT_PRODUCT_BY_CATEGORY = {
    "Bed": "Bedsheet",
    "Bath": "Terry Towel",
    "TOB": "Blanket",
    "Pillow": "Pillow",
}

# Fallback keyword rule, used only when the exact-match pass above finds nothing.
# bale_pack_size specifically has shown 4 different real header phrasings across
# just 4 source files (Bale Size / Bale Pack Size / Bale Pack Sizes / "Bale Pack
# Size  (No. of Bales) " with trailing clarifier text) - the exact-alias list WILL
# miss future variants, so this field gets a robust substring rule: any column
# whose normalized name starts with "bale" and contains "size" anywhere in it.
CORE_FIELD_KEYWORD_RULES = {
    "bale_pack_size": lambda name: name.startswith("bale") and "size" in name,
    "ex_mill_price": lambda name: (
        "exmill" in name.replace("-", "").replace(" ", "")
        and "value" not in name
        and "markup" not in name
    ),
}

# Order matters: check Blanket/Comforter/BIAB before generic Pillow check
CATEGORY_KEYWORDS = [
    (["blanket", "comforter", "slumber", "bed in a bag", "bed in a bga", "biab"], "TOB"),
    (["pillow"], "Pillow"),
    (["towel", "bathmat", "bathrobe", "towelling", "bath mat", "bath linen"], "Bath"),
    (["bedsheet", "sheet set", "sheet sets", "fitted sheet"], "Bed"),
]

# Towel size patterns for category fallback when product column is blank
_BATH_SIZE_PATTERNS = re.compile(
    r"(?:^| )"
    r"(?:30\s*[x×*]\s*30|40\s*[x×*]\s*60|60\s*[x×*]\s*120|75\s*[x×*]\s*150|90\s*[x×*]\s*180"
    r"|bathrobe|r4\s*set|bath\s*robe|gsm)"
    r"(?:$| )",
    re.IGNORECASE,
)

REQUIRED_HEADER_MARKERS = ["brand", "size"]

_ONE_IN_A_DENT_RE = re.compile(r"\s*\(ONE IN A DENT\)\s*", re.IGNORECASE)


def normalize_key_part_value(field_name, value):
    """Normalize one key-field segment. TC values like '104 (ONE IN A DENT)'
    and plain '104' must match — distributors often drop the marketing suffix.

    Size codes use normalize_size_code so DBL BS ≡ DB BS, KDB BS ≡ KS BS, etc.
    """
    if value is None or str(value).strip() == "":
        return ""
    s = str(value).strip()
    field_l = (field_name or "").lower()
    if field_l == "tc":
        s = _ONE_IN_A_DENT_RE.sub("", s).strip()
        return s.upper()
    if field_l == "size":
        code = normalize_size_code(s)
        if code is None or str(code).strip() == "":
            return ""
        return str(code).strip().upper()
    if field_l in {"product", "product_type"}:
        canon = normalize_product_spelling(s)
        if canon is None or str(canon).strip() == "":
            return ""
        return str(canon).strip().upper()
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
    if field_lower in {"product", "product_type"}:
        prod = core_fields.get("product_type")
        if prod not in (None, ""):
            return prod
    if field_lower in core_fields and core_fields[field_lower] not in (None, ""):
        return core_fields[field_lower]
    return next(
        (v for k, v in (extra_attributes or {}).items() if k.lower() == field_lower),
        None,
    )


def _norm(s):
    return re.sub(r"\s+", " ", str(s).strip().lower()) if pd.notna(s) else ""


def bed_size_sort_rank(size) -> int:
    """
    Display order within a brand/collection:
      1 = Single (SB…), 2 = Double (DB/DBL…), 3 = King (KS/KDB/KB…), 4 = other.
    King patterns checked before Double so 'KDB' is not treated as 'DB'.
    """
    if size is None or (isinstance(size, float) and pd.isna(size)):
        return 4
    s = re.sub(r"\s+", " ", str(size).strip().upper())
    if not s:
        return 4
    if s.startswith("SB") or "SINGLE" in s:
        return 1
    if (
        s.startswith("KDB")
        or s.startswith("KS")
        or s.startswith("KB")
        or "KING" in s
    ):
        return 3
    if s.startswith("DBL") or s.startswith("DB") or "DOUBLE" in s:
        return 2
    return 4


def bath_size_sort_rank(size) -> int:
    """Face → Hand → Ladies → Bath → Pool → sets → bathrobe sizes."""
    key = _size_lookup_key(size)
    order = {
        "FACE TOWEL": 10,
        "FACE TOWEL SET OF 3": 11,
        "HAND TOWEL": 20,
        "HAND TOWEL SET OF 2": 21,
        "LADIES TOWEL": 30,
        "BATH TOWEL": 40,
        "BATH MAT": 45,
        "POOL TOWEL": 50,
        "TOWEL SET": 60,
        "GYM TOWEL": 70,
        "91X100": 75,
        "LARGE": 80,
        "EXTRA LARGE": 81,
        "DOUBLE EXTRA LARGE": 82,
    }
    return order.get(key, 999)


def article_display_sort_key(article: dict):
    """Brand A→Z, then size family order, then size / product for stable ties."""
    brand = str(article.get("brand") or "").strip().lower()
    size = article.get("size")
    size_l = str(size or "").strip().lower()
    product = str(article.get("product_type") or "").strip().lower()
    item_key = str(article.get("item_key") or "").strip().lower()
    category = str(article.get("category") or "").strip()
    if category == "Bath":
        return (brand, bath_size_sort_rank(size), size_l, product, item_key)
    return (brand, bed_size_sort_rank(size), size_l, product, item_key)


def sort_articles_for_display(articles: list) -> list:
    return sorted(articles or [], key=article_display_sort_key)


def detect_header_row(raw_df, max_scan_rows=15):
    """Find the header row.

    Normal sheets label Brand + Size. Some distributor booking forms (e.g. Savitri
    Steel) leave the Brand header cell blank while Size/Product/MRP are present —
    treat those as headers too.
    """
    for i in range(min(max_scan_rows, len(raw_df))):
        row_values = [_norm(v) for v in raw_df.iloc[i].tolist()]
        if all(any(marker in val for val in row_values) for marker in REQUIRED_HEADER_MARKERS):
            return i
        # Blank-Brand booking forms: exact Size column + product/rate markers.
        has_exact_size = any(val == "size" for val in row_values)
        has_support = any(
            any(token in val for token in ("product", "mrp", "exmill", "ex mill", "ex-mill", "bale size", "bale pack"))
            for val in row_values
            if val
        )
        if has_exact_size and has_support:
            return i
    raise ValueError("Could not detect header row - no row contains both 'Brand' and 'Size' columns")


def infer_blank_brand_column(header_row, mapping):
    """If Brand header is missing/blank, map the unlabeled column left of Size to brand.

    Savitri Steel AW26 sheets put brands in column 0 with an empty header cell.
    """
    if any(field == "brand" for field in mapping.values()):
        return mapping
    size_idxs = [idx for idx, field in mapping.items() if field == "size"]
    size_idx = min(size_idxs) if size_idxs else None
    blank_idxs = []
    for idx, raw_name in enumerate(header_row):
        if mapping.get(idx) is not None:
            continue
        if _norm(raw_name):
            continue
        if size_idx is None or idx < size_idx:
            blank_idxs.append(idx)
    if blank_idxs:
        mapping = dict(mapping)
        mapping[blank_idxs[0]] = "brand"
        return mapping
    return infer_quality_as_brand_column(header_row, mapping)


def infer_quality_as_brand_column(header_row, mapping):
    """Bernina bath special-order sheets use QUALITY as the brand column."""
    if any(field == "brand" for field in mapping.values()):
        return mapping
    for idx, raw_name in enumerate(header_row):
        if mapping.get(idx) is not None:
            continue
        if _norm(raw_name) == "quality":
            mapping = dict(mapping)
            mapping[idx] = "brand"
            break
    return mapping


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
    mapping = infer_blank_brand_column(header_row, mapping)
    return infer_quality_as_brand_column(header_row, mapping)


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
    if _BATH_SIZE_PATTERNS.search(normalized):
        return "Bath"
    return None


def detect_category_from_sizes(size_values):
    """When Product column is missing (GT booking forms), infer Bed/Bath from size codes."""
    bed_hits = 0
    bath_hits = 0
    for value in size_values:
        s = _norm(value)
        if not s:
            continue
        if _BATH_SIZE_PATTERNS.search(s):
            bath_hits += 1
        elif any(
            tok in s
            for tok in (
                "bedsheet",
                "fitted sheet",
                "duvet",
                "comforter",
                "comf",
                "sb bs",
                "db bs",
                "ks bs",
                "kb fs",
                "db fs",
                "dbl",
                "kdb",
                "single bedsheet",
                "double bedsheet",
                "king bedsheet",
            )
        ) or s in {"sb", "db", "ks", "kb"} or s.startswith(
            ("sb ", "db ", "ks ", "kb ", "dbl ", "kdb ")
        ):
            bed_hits += 1
    if bath_hits > bed_hits and bath_hits > 0:
        return "Bath"
    return "Bed" if bed_hits else None


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

    # Highest vote wins; tie-break by fixed priority: Bed > Bath > TOB > Pillow
    # (Bedsheet sheets historically had occasional comforter/pillow mentions.)
    priority = {"Bed": 0, "Bath": 1, "TOB": 2, "Pillow": 3}
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
    (matched case-insensitively). 'product' resolves to product_type.
    """
    parts = []
    for field in key_fields:
        val = extract_key_field_value(field, core_fields, extra_attributes)
        parts.append(
            normalize_key_part_value(field, val) if val not in (None, "") else ""
        )
    return "|".join(parts)


def parse_article_sheet(
    filepath,
    sheet_name,
    category_key_fields_lookup=None,
    default_key_fields=None,
    forced_category=None,
    source_filename=None,
):
    """
    Parses one Excel sheet into article dicts ready for db.upsert_article().

    Each DATA ROW gets its own category from its Product text (majority-file
    detection is only a fallback / suggested_summary). So a mixed sheet with
    Bedsheet + Towel + TOB rows is fine — each article lands in the right bucket.

    forced_category: if set, overrides EVERY row's category (manual user override).
    source_filename: original upload name (temp paths have no Bed/Bath hint).

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
    size_col_idx = next((idx for idx, f in col_mapping.items() if f == "size"), None)

    data_rows = raw_df.iloc[header_idx + 1:]
    valid_rows = [row for _, row in data_rows.iterrows() if is_data_row(row.tolist(), col_mapping)]
    if product_col_idx is not None:
        product_values = [row.iloc[product_col_idx] for row in valid_rows]
    else:
        # GT-style booking forms omit Product; fall back to filename + size codes.
        product_values = []

    detect_name = source_filename or str(filepath)
    suggested_category = detect_category(product_values, filename=detect_name)
    if not suggested_category and size_col_idx is not None:
        size_values = [row.iloc[size_col_idx] for row in valid_rows]
        suggested_category = detect_category_from_sizes(size_values)
    if forced_category and forced_category.strip():
        suggested_category = forced_category.strip()
    elif not suggested_category:
        # Let the confirm modal force Bed/Bath/TOB — do not hard-fail Product-less sheets.
        suggested_category = "UNCATEGORIZED - REVIEW"

    articles = []
    category_breakdown = {}
    for row in valid_rows:
        core_fields = {}
        extra_attributes = {}
        raw_description = None
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
                # Keep Description only for towel PVC detection — never store it
                if normalize_extra_column_name(raw_col_name) == "description":
                    if not is_blank_attr_value(val):
                        raw_description = val
                    continue
                if is_excluded_extra_column(raw_col_name):
                    continue
                if is_blank_attr_value(val):
                    continue
                extra_attributes[raw_col_name] = val

        # Collapse sticky aliases (e.g. Perceived → Proposed Customer Discount)
        # and drop blanks / excluded keys.
        extra_attributes = merge_extra_attributes({}, extra_attributes)
        # English packing fix as soon as extras are collected
        if "Packing" in extra_attributes:
            extra_attributes["Packing"] = normalize_packing_spelling(extra_attributes["Packing"])

        if forced_category:
            row_category = forced_category.strip()
        else:
            row_category = detect_category_for_text(core_fields.get("product_type"))
            if not row_category:
                # Unknown product text → fall back to file-level majority suggestion
                row_category = suggested_category

        raw_size_cell = core_fields.get("size")
        brand, size = normalize_brand_and_size(core_fields.get("brand"), core_fields.get("size"))
        core_fields["brand"] = brand
        core_fields["size"] = size

        product_type = core_fields.get("product_type")
        if product_type in (None, ""):
            product_type = DEFAULT_PRODUCT_BY_CATEGORY.get(row_category)
        product_type = normalize_product_spelling(product_type)
        # Size wins over blank / generic Bedsheet (e.g. DB Reversible Comf → Comforter)
        product_type = resolve_product_type(product_type, size)
        # Comforter / Duvet size rules; Bed In Bag product stays Bed when size says Comf
        row_category = resolve_category(row_category, size, product_type)
        # Legacy rename
        if row_category == "TOB Pillow":
            row_category = "Pillow"

        # Bath: physical booking size (75x150) → BS Size; Shade/Color + PVC packing
        is_pkg_only = False
        if row_category == "Bath":
            physical = towel_physical_size_code(raw_size_cell)
            if physical and is_blank_attr_value(extra_attributes.get("BS Size")):
                extra_attributes["BS Size"] = physical
            raw_color = extra_attributes.get("Color")
            color, packing, is_pkg_only = normalize_towel_color_and_packing(
                raw_color, raw_description,
            )
            if color:
                extra_attributes["Color"] = color
            elif "Color" in extra_attributes:
                del extra_attributes["Color"]
            if packing:
                existing_pack = extra_attributes.get("Packing")
                if is_blank_attr_value(existing_pack):
                    extra_attributes["Packing"] = packing
            extra_attributes = strip_excluded_extra_attributes(
                extra_attributes, category="Bath",
            )
        else:
            # TOB / Pillow: Quality (+ Ply) + Weight → Blend; Unit → Units
            if row_category in {"TOB", "Pillow"}:
                extra_attributes = apply_tob_pillow_blend_and_units(
                    extra_attributes, row_category,
                )
            extra_attributes = strip_excluded_extra_attributes(
                extra_attributes, category=row_category,
            )

        key_fields = category_key_fields_lookup.get(row_category, default_key_fields)
        # Bath identity needs product_type in core for 'product' key field
        core_fields["product_type"] = product_type
        item_key = build_item_key(core_fields, extra_attributes, key_fields)

        articles.append({
            "category": row_category,
            "product_type": product_type,
            "brand": brand,
            "size": size,
            "mrp": core_fields.get("mrp"),
            "ptr": core_fields.get("ptr"),
            "ex_mill_price": core_fields.get("ex_mill_price"),
            "bale_pack_size": core_fields.get("bale_pack_size"),
            "item_key": item_key,
            "extra_attributes": extra_attributes,
            "_is_pkg_only": is_pkg_only,
            "_has_pvc": (extra_attributes or {}).get("Packing") == "PVC bag Pkg",
        })
        category_breakdown[row_category] = category_breakdown.get(row_category, 0) + 1

    # Towel teaching: ignore Pkg/(L) when PVC/latest sibling shares identity
    articles = _dedupe_towel_pkg_rows(articles)
    category_breakdown = {}
    for a in articles:
        cat = a.get("category") or "UNCATEGORIZED - REVIEW"
        category_breakdown[cat] = category_breakdown.get(cat, 0) + 1

    key_counts = {}
    for a in articles:
        key_counts[a["item_key"]] = key_counts.get(a["item_key"], 0) + 1

    clean_articles = []
    needs_review = []
    for a in articles:
        a.pop("_is_pkg_only", None)
        a.pop("_has_pvc", None)
        if key_counts[a["item_key"]] == 1:
            clean_articles.append(a)
        else:
            needs_review.append(a)

    # is_new_category if ANY used category isn't configured yet
    used_categories = set(category_breakdown.keys())
    is_new_category = any(cat not in category_key_fields_lookup for cat in used_categories)

    return clean_articles, suggested_category, is_new_category, needs_review, category_breakdown


def _dedupe_towel_pkg_rows(articles):
    """Within one upload: drop Pkg/(L) when a PVC or later sibling shares item_key."""
    by_key = {}
    passthrough = []
    for idx, a in enumerate(articles):
        if a.get("category") != "Bath":
            passthrough.append((idx, a))
            continue
        by_key.setdefault(a["item_key"], []).append((idx, a))

    kept = list(passthrough)
    for group in by_key.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        pvc = [g for g in group if g[1].get("_has_pvc")]
        non_pkg = [g for g in group if not g[1].get("_is_pkg_only")]
        pool = pvc or non_pkg or group
        winner = max(pool, key=lambda g: g[0])
        kept.append(winner)

    kept.sort(key=lambda g: g[0])
    # Rebuild category_breakdown counts outside — caller already tallied; OK if slightly off
    return [a for _, a in kept]
