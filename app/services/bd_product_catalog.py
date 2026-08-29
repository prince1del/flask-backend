"""Bombay Dyeing product teaching maps.

Used only for SO Pack Excel sheets:
  - Brand Wise Size Wise Summary (collection + size/type)
  - Brand Wise Summary (collection only)
Other sheets stay on raw short codes.
"""

from __future__ import annotations

import re
from typing import Any

# Full short-name → collection (from BD teaching sheet). Exact match first.
BD_PRODUCT_COLLECTION_EXACT: dict[str, str] = {
    "ASTER 1+2 DB SET": "Aster",
    "BLUMEN 1+1 SB SET": "Blumen",
    "BLUMEN 1+2 DB SET": "Blumen",
    "CARDINL 1+1 SB SET": "Cardinal",
    "CARDINL 1+2 DB SET": "Cardinal",
    "CARDINL 1+2 KS SET": "Cardinal",
    "EPIGRAM 1+1 SB SET": "Epigram",
    "EPIGRAM 1+2 DB SET": "Epigram",
    "EPIGRAM 1+2 KS SET": "Epigram",
    "FLFIEST 1+1 SB SET": "Floral Fiesta",
    "FLFIEST 1+2 DB SET": "Floral Fiesta",
    "FLFIEST 1+2 KS SET": "Floral Fiesta",
    "FLFIEST 1+2 KS FST": "Floral Fiesta",
    "FLRNTIN 1+2 DB SET": "Florentine",
    "FLRNTIN 1+2 KS SET": "Florentine",
    "ALLURE 1+2 KS FST": "Allure",
    "VINTAGE 1+2 KS SET": "Vintage",
    "VINTAGE 2+2 SB SET": "Vintage",
    "525 B 1+2 DB SET": "525B",
    "525 B 1+2 KS SET": "525B",
    "WNDLDKD 1+1 SB SET": "Wonder Land- Kids",
    "WNDLDKD 1+2 KS SET": "Wonder Land- Kids",
    "SAGE 1+2 KS SET": "Sage",
    "THYME 1+2 KS SET": "Thyme",
    "BEAUCLE 2+2 SB SET": "Beaucale",
    "BEAUCLE 1+2 KS SET": "Beaucale",
    "ESCTASY 1+2 KS SET": "Ecstasy",
    "AKIRA 1+2 KS SET": "Akira",
    "BLATWIL 1+2 KS SET": "Bela Twill",
    "GRDSPAC 1+2 KS SET": "Grid Space",
    "CEL IND 1+4 KS SET": "Celebareting India",
    "ETHNCTY 1+4 KS SET": "Ethnicity",
    "RGL LVNG 1+4 KS SET": "Rigel Living",
    "CTNCOMFRT 1+2 DB SET": "Cotton Comforts",
    "CTNCOMFRT 1+2 KS SET": "Cotton Comforts",
    "CTNCOMFRT 2+2 SB SET": "Cotton Comforts",
    "FLORA 1+2 DB SET": "Flora",
    "FLORA 1+2 KS SET": "Flora",
    "FLORA 2+2 SB SET": "Flora",
    "TOLDJOY 1+2 KS SET": "Toiel",
}

# Collection code prefix → display name (for SKUs not in the exact sheet).
BD_COLLECTION_BY_CODE: dict[str, str] = {
    "ASTER": "Aster",
    "BLUMEN": "Blumen",
    "BLUEMEN": "Blumen",
    "CARDINL": "Cardinal",
    "EPIGRAM": "Epigram",
    "FLFIEST": "Floral Fiesta",
    "FLRNTIN": "Florentine",
    "ALLURE": "Allure",
    "VINTAGE": "Vintage",
    "525 B": "525B",
    "525B": "525B",
    "WNDLDKD": "Wonder Land- Kids",
    "SAGE": "Sage",
    "THYME": "Thyme",
    "BEAUCLE": "Beaucale",
    "ESCTASY": "Ecstasy",
    "AKIRA": "Akira",
    "BLATWIL": "Bela Twill",
    "GRDSPAC": "Grid Space",
    "CEL IND": "Celebareting India",
    "ETHNCTY": "Ethnicity",
    "RGL LVNG": "Rigel Living",
    "CTNCOMFRT": "Cotton Comforts",
    "COTTON COMFORT": "Cotton Comforts",
    "COTTON COMFORTS": "Cotton Comforts",
    "FLORA": "Flora",
    "TOLDJOY": "Toiel",
}

# Set / size composition → product type label (teaching sheet 2).
# Longer patterns first.
BD_SET_TYPE_LABELS: list[tuple[str, str, str]] = [
    # (regex end-anchor pattern, canonical set key, display label)
    (r"1\s*\+\s*2\s+KS\s+FST", "1+2 KS FST", "King Fitted Sheet"),
    (r"1\s*\+\s*2\s*DBSET", "1+2 DB SET", "Double Bedsheet"),
    (r"1\s*\+\s*2\s+DB\s+SET", "1+2 DB SET", "Double Bedsheet"),
    (r"1\s*\+\s*1\s*SBSET", "1+1 SB SET", "Single Bedsheet"),
    (r"1\s*\+\s*1\s+SB\s+SET", "1+1 SB SET", "Single Bedsheet"),
    (r"1\s*\+\s*2\s*KSSET", "1+2 KS SET", "King Bedsheet"),
    (r"1\s*\+\s*2\s+KS\s+SET", "1+2 KS SET", "King Bedsheet"),
    (r"2\s*\+\s*2\s*SBSET", "2+2 SB SET", "Single Bedsheet"),
    (r"2\s*\+\s*2\s+SB\s+SET", "2+2 SB SET", "Single Bedsheet"),
    (r"1\s*\+\s*4\s+KS\s+SET", "1+4 KS SET", "King Bedsheet"),
]

_SET_TYPE_BY_KEY = {key: label for _, key, label in BD_SET_TYPE_LABELS}


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _norm_key(text: str) -> str:
    return _norm_space(text).upper()


def split_bd_collection_and_set_type(short_code: str) -> tuple[str, str | None]:
    """`ASTER 1+2 DB SET` → (`ASTER`, `1+2 DB SET`)."""
    text = _norm_space(short_code)
    if not text:
        return "", None
    for pattern, set_key, _label in BD_SET_TYPE_LABELS:
        m = re.search(rf"(?i)(?:^|\s)({pattern})\s*$", text)
        if m:
            collection = text[: m.start(1)].strip()
            return collection, set_key
    return text, None


def lookup_bd_collection(short_code: str, collection_code: str | None = None) -> str | None:
    key = _norm_key(short_code)
    if key in BD_PRODUCT_COLLECTION_EXACT:
        return BD_PRODUCT_COLLECTION_EXACT[key]
    code = _norm_key(collection_code or "")
    if code and code in BD_COLLECTION_BY_CODE:
        return BD_COLLECTION_BY_CODE[code]
    # Try longest collection code prefix match
    for alias, display in sorted(BD_COLLECTION_BY_CODE.items(), key=lambda x: len(x[0]), reverse=True):
        if key == alias or key.startswith(alias + " "):
            return display
    return None


def lookup_bd_product_type(set_key: str | None) -> str | None:
    if not set_key:
        return None
    return _SET_TYPE_BY_KEY.get(_norm_key(set_key))


def enrich_bd_product(short_code: str) -> dict[str, Any]:
    """Resolve teaching maps for a BD short product code.

    Returns:
      product_code, collection, product_type, product_name (display), matched
    """
    code = _norm_space(short_code)
    collection_code, set_key = split_bd_collection_and_set_type(code)
    collection = lookup_bd_collection(code, collection_code)
    product_type = lookup_bd_product_type(set_key)

    if collection and product_type:
        display = f"{collection} — {product_type}"
    elif collection:
        display = collection
    elif product_type:
        display = product_type
    else:
        display = code

    return {
        "product_code": code,
        "collection": collection,
        "product_type": product_type,
        "set_type_key": set_key,
        "product_name": display,
        "matched": bool(collection or product_type),
    }


def brand_wise_size_wise_label(short_code: str) -> str | None:
    """Brand Wise Size Wise Summary label, e.g. `Aster Double Bedsheet`.

    Returns None when collection or product type is unknown → roll into Others.
    """
    enriched = enrich_bd_product(short_code)
    collection = enriched.get("collection")
    product_type = enriched.get("product_type")
    if collection and product_type:
        return f"{collection} {product_type}"
    return None


def brand_wise_only_label(short_code: str) -> str | None:
    """Brand Wise Summary label — collection only, e.g. `Aster`.

    Returns None when collection is unknown → roll into Others.
    """
    enriched = enrich_bd_product(short_code)
    collection = enriched.get("collection")
    return str(collection) if collection else None


# Back-compat alias
brand_wise_summary_label = brand_wise_size_wise_label
