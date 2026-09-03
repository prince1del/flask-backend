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


# --- Towel / Bath SO PDF lines (long product_name text, not bedsheet short codes) ---

TOWEL_COLLECTION_ALIASES: list[tuple[str, str]] = [
    ("FLORA BATHROBE", "Flora Bathrobe"),
    ("FLORA", "Flora"),
    ("FLR", "Flora"),
    ("NATURE'S BQT", "Bamboo"),
    ("NATURES BQT", "Bamboo"),
    ("NATURE S BQT", "Bamboo"),
    ("SUPER ULTRX", "Super Ultrx"),
    ("SUPER ULTRA", "Super Ultrx"),
    ("RIMZIM COOLTEX", "Rimzim Cooltex"),
    ("RIMZIM PRINTED", "Rimzim Printed"),
    ("HUCK A BUCK", "Huk A Buk"),
    ("HUK A BUK", "Huk A Buk"),
    ("BD WHITE", "BD White"),
    ("GYM TOWEL", "Gym Towel"),
    ("SANTINO", "Santino"),
    ("COOLTEX", "Rimzim Cooltex"),
    ("TULIP", "Tulip"),
    ("BAMBOO", "Bamboo"),
    ("LUXURY LIVING", "Luxury Living"),
    ("LUXURYLIVING", "Luxury Living"),
]

TOWEL_SIZE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bR4\b.*\bSET\b|\bR4\s*SET\b|\bR4\b", re.I), "Towel Set"),
    (re.compile(r"BATHROBE|FRBR|FLBR", re.I), "Bathrobe"),
    (re.compile(r"BATHMAT|50\s*CM\s*[X×]\s*70|50CMX70|50\s*[X×]\s*70", re.I), "Bathmat"),
    (re.compile(r"50\s*CM\s*[X×]\s*100|50CMX100|50\s*[X×]\s*100|\bGYM\b", re.I), "Gym Towel"),
    (
        re.compile(
            r"90\s*CM\s*[X×]\s*1\.?\s*8\s*M?|90CMX1\.8|90\s*[X×]\s*180|90CM\s*X1\.8|90\s*CM\s*X|90\s*CM\b|90\s*[X×]\s*180",
            re.I,
        ),
        "Pool Towel",
    ),
    (re.compile(r"40\s*CM\s*[X×]\s*60|40CMX60|40\s*[X×]\s*60|40X\s*60", re.I), "Hand Towel"),
    (
        re.compile(
            r"60\s*CM\s*[X×]\s*1\.?\s*2\s*0?\s*M?|60CMX1\.20|60CMX1\.2|60\s*[X×]\s*120|60CM\s*X\s*1\.2|60\s*CM\s*X|60\s*CM\b",
            re.I,
        ),
        "Ladies Towel",
    ),
    (
        re.compile(
            r"75\s*CM\s*[X×]\s*1\.?\s*5\s*0?\s*M?|75CMX150|75CMX1\.5|75\s*[X×]\s*150|75CM\s*X\s*1\.5|75CM\s*X1\.5|75\s*[X×]\s*1\.5|75\s*CM\s*X|75\s*CM\b",
            re.I,
        ),
        "Bath Towel",
    ),
    (
        re.compile(
            r"72\s*CM\s*C?\s*[X×]?\s*C?\s*1\.?\s*44\s*M?|72CMX?C?1\.44|72\s*[X×]\s*144|72.*?1\.44\s*M?|72\s*CM\b",
            re.I,
        ),
        "Bath Towel",
    ),
    (
        re.compile(
            r"30\s*CM\s*[X×]\s*30|30CMX30|30\s*[X×]\s*30|30X\s*30|30\s*CM\b|3\s*PC\s*SET|3PCSET|3\s*PC|3PC",
            re.I,
        ),
        "Face Towel Set of 3",
    ),
    (re.compile(r"2\s*PC\s*SET|2PCSET|2\s*PC|2PC", re.I), "Hand Towel Set of 2"),
]


def _towel_fold_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (text or "").upper())).strip()


def lookup_towel_collection(short_code: str | None) -> str | None:
    key = _norm_space(short_code or "").upper()
    if not key:
        return None
    key_folded = _towel_fold_key(key)
    for alias, display in TOWEL_COLLECTION_ALIASES:
        alias_key = _norm_space(alias).upper()
        alias_folded = _towel_fold_key(alias_key)
        if (
            key == alias_key
            or key.startswith(alias_key + " ")
            or key_folded == alias_folded
            or key_folded.startswith(alias_folded + " ")
        ):
            return display
    return None


def lookup_towel_product_type(short_code: str | None, material_code: str | None = None) -> str | None:
    text = _norm_space(short_code or "")
    upper = text.upper()
    if text:
        if re.search(r"\bGYM\b", upper) and "TOWEL" in upper:
            for pat, label in TOWEL_SIZE_RULES:
                if pat.search(text):
                    return label
            return "Gym Towel"
        for pat, label in TOWEL_SIZE_RULES:
            if not pat.search(text):
                continue
            if label == "Hand Towel" and re.search(r"2\s*PC|2PC|SET\s*OF\s*2|\b2\s*PC\b", upper):
                return "Hand Towel Set of 2"
            return label
        if re.search(r"\bR4\b", upper):
            return "Towel Set"

    code = (material_code or "").strip().upper()
    if code:
        if "R4" in code:
            return "Towel Set"
        if "BR" in code or "BATHROBE" in code:
            return "Bathrobe"
        if "030030" in code:
            return "Face Towel Set of 3"
        if "040060" in code:
            return "Hand Towel Set of 2" if ("2PC" in code or "S2" in code) else "Hand Towel"
        if "050070" in code:
            return "Bathmat"
        if "0500100" in code:
            return "Gym Towel"
        if "0600120" in code or "060120" in code:
            return "Ladies Towel"
        if "0720144" in code or "0750150" in code or "075150" in code:
            return "Bath Towel"
        if "0900180" in code:
            return "Pool Towel"
    return None


def enrich_towel_so_product(
    product_name: str | None,
    *,
    material_code: str | None = None,
) -> dict[str, Any]:
    """Parse towel SO PDF product_name → collection + FO-compatible size label."""
    code = _norm_space(product_name or "")
    collection = lookup_towel_collection(code) or lookup_towel_collection(material_code or "")
    product_type = lookup_towel_product_type(code, material_code=material_code)
    if not collection or not product_type:
        return {
            "collection": collection,
            "product_type": product_type,
            "matched": False,
        }
    size_label = "Large" if product_type == "Bathrobe" else product_type
    return {
        "collection": collection,
        "product_type": size_label,
        "matched": True,
    }


def resolve_so_brand_size(
    product_name: str | None,
    *,
    material_code: str | None = None,
) -> tuple[str | None, str | None]:
    """Bedsheet short codes first, then towel SO PDF teaching."""
    short = _norm_space(product_name or "")
    if not short and material_code:
        short = _norm_space(material_code)
    enriched = enrich_bd_product(short) if short else {}
    brand = enriched.get("collection")
    size = enriched.get("product_type")
    if brand and size:
        return str(brand), str(size)
    towel = enrich_towel_so_product(product_name, material_code=material_code)
    if towel.get("matched"):
        return towel.get("collection"), towel.get("product_type")
    if brand and not size:
        return str(brand), None
    return None, None
