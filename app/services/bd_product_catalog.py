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


# --- Bath / Towel teaching (FO Brand × Size ↔ SO PDF short names) ---
# Longer aliases first. Display names must soft-match FO brands after
# normalize_brand_and_size (e.g. Huk A Buk, Rimzim Cooltex, BD White).
BD_TOWEL_COLLECTION_ALIASES: tuple[tuple[str, str], ...] = (
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
)

# Bombay Dyeing bed/bedsheet material codes ("BS03DBEPGRM8129LBR",
# "MB..."). The towel material-code rules below read a towel code's
# structure and must never be applied to one of these.
_BED_MATERIAL_CODE_RE = re.compile(r"^(?:BS|MB)\d", re.I)

# Bathrobe short forms as they actually appear (matching _TOWEL_SIZE_RULES
# below), NOT a bare "BR" substring — that also matches colour codes like
# LBR (Light Brown) and BRN, which is how bedsheets became bathrobes.
_BATHROBE_CODE_RE = re.compile(r"BATHROBE|FRBR|FLBR|(?:^|[^A-Z])BR(?:$|[^A-Z])", re.I)

# Physical size / set cues on towel SO lines → FO size labels.
_TOWEL_SIZE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
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
    (
        re.compile(r"40\s*CM\s*[X×]\s*60|40CMX60|40\s*[X×]\s*60|40X\s*60", re.I),
        "Hand Towel",  # upgraded to Set of 2 when 2PC present
    ),
    (
        re.compile(
            r"60\s*CM\s*[X×]\s*1\.?\s*2\s*0?\s*M?|60CMX1\.20|60CMX1\.2|"
            r"60\s*[X×]\s*120|60CM\s*X\s*1\.2|60\s*CM\s*X|60\s*CM\b",
            re.I,
        ),
        "Ladies Towel",
    ),
    (
        re.compile(
            r"75\s*CM\s*[X×]\s*1\.?\s*5\s*0?\s*M?|75CMX150|75CMX1\.5|"
            r"75\s*[X×]\s*150|75CM\s*X\s*1\.5|75CM\s*X1\.5|75\s*[X×]\s*1\.5|75\s*CM\s*X|75\s*CM\b",
            re.I,
        ),
        "Bath Towel",
    ),
    (
        re.compile(
            r"72\s*CM\s*C?\s*[X×]?\s*C?\s*1\.?\s*44\s*M?|72CMX?C?1\.44|72\s*[X×]\s*144|"
            r"72.*?1\.44\s*M?|72\s*CM\b",
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
    (
        re.compile(r"2\s*PC\s*SET|2PCSET|2\s*PC|2PC", re.I),
        "Hand Towel Set of 2",
    ),
)


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _norm_key(text: str) -> str:
    return _norm_space(text).upper()


def lookup_towel_collection(short_code: str) -> str | None:
    key = _norm_key(short_code)
    # Apostrophes / punctuation folded for Nature's Bqt etc.
    key_folded = re.sub(r"[^A-Z0-9]+", " ", key)
    key_folded = _norm_space(key_folded)
    for alias, display in BD_TOWEL_COLLECTION_ALIASES:
        alias_key = _norm_key(alias)
        alias_folded = _norm_space(re.sub(r"[^A-Z0-9]+", " ", alias_key))
        if (
            key == alias_key
            or key.startswith(alias_key + " ")
            or key_folded == alias_folded
            or key_folded.startswith(alias_folded + " ")
        ):
            return display
    return None


def lookup_towel_product_type(
    short_code: str, material_code: str | None = None
) -> str | None:
    text = _norm_space(short_code)
    upper = text.upper() if text else ""
    if upper and re.search(r"\bGYM\b", upper) and "TOWEL" in upper:
        for pat, label in _TOWEL_SIZE_RULES:
            if pat.search(text):
                return label
        return "Gym Towel"
    if text:
        for pat, label in _TOWEL_SIZE_RULES:
            if not pat.search(text):
                continue
            if label == "Hand Towel" and re.search(
                r"2\s*PC|2PC|SET\s*OF\s*2|\b2\s*PC\b", upper
            ):
                return "Hand Towel Set of 2"
            return label
        if re.search(r"\bR4\b", upper):
            return "Towel Set"

    # Material code structural fallback (e.g. MT030030, MT040060, MT0600120)
    c = (material_code or "").upper().strip()

    # These rules read a TOWEL material code's structure, so they must not be
    # applied to a bedsheet/bed SKU. enrich_bd_product() falls back here
    # whenever its bed maps come up short, which sent real bedsheet codes
    # through these towel patterns: BS03DBEPGRM8129LBR ("...8129 Light BRown")
    # matched the bare "BR" substring below and was labelled a Bathrobe. On
    # Shri Ram & Co's SO 102876191 that pulled 14 of 252 SETs out of the DB
    # and KS buckets into a product the order did not contain, turning a
    # clean match into two false shortages.
    if _BED_MATERIAL_CODE_RE.match(c):
        return None

    if "R4" in c:
        return "Towel Set"
    # Anchored, not a bare substring: "BR" inside a colour code (LBR, BRN)
    # is not a bathrobe. FRBR/FLBR are the real short forms, matching the
    # collection rules above. A bathrobe that fails to auto-classify merely
    # shows as unmatched for a human to place; a bedsheet misread AS a
    # bathrobe silently corrupts an order's match.
    if _BATHROBE_CODE_RE.search(c):
        return "Bathrobe"
    if "030030" in c:
        return "Face Towel Set of 3"
    if "040060" in c:
        return "Hand Towel Set of 2" if ("2PC" in c or "S2" in c) else "Hand Towel"
    if "050070" in c:
        return "Bathmat"
    if "0500100" in c:
        return "Gym Towel"
    if "0600120" in c or "060120" in c:
        return "Ladies Towel"
    if "0720144" in c or "0750150" in c or "075150" in c:
        return "Bath Towel"
    if "0900180" in c:
        return "Pool Towel"

    return None


def is_towel_special_discount(material_code: str | None) -> bool:
    """Detect if this towel material code is a special discount SKU."""
    if not material_code:
        return False
    c = material_code.strip().upper()
    # Codes ending in S, S2S, S3S, PVS, LS, LSS are special discount variants
    return (
        c.endswith("S")
        or c.endswith("S2S")
        or c.endswith("S3S")
        or c.endswith("PVS")
        or c.endswith("LS")
        or c.endswith("LSS")
    )


def enrich_towel_product(
    short_code: str, material_code: str | None = None
) -> dict[str, Any] | None:
    """Resolve Bath/towel SO PDF names → FO Brand × Size labels."""
    code = _norm_space(short_code)
    if not code and not material_code:
        return None
    collection = lookup_towel_collection(code)
    if not collection and material_code:
        collection = lookup_towel_collection(material_code)
    product_type = lookup_towel_product_type(code, material_code=material_code)
    if not collection and not product_type:
        return None
    if collection and product_type:
        display = f"{collection} — {product_type}"
    elif collection:
        display = collection
    else:
        display = product_type or code
    return {
        "product_code": code,
        "collection": collection,
        "product_type": product_type,
        "set_type_key": None,
        "product_name": display,
        "matched": bool(collection and product_type),
        "is_special_discount": is_towel_special_discount(material_code),
    }


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


def enrich_bd_product(
    short_code: str, material_code: str | None = None
) -> dict[str, Any]:
    """Resolve teaching maps for a BD short product code.

    Returns:
      product_code, collection, product_type, product_name (display), matched
    """
    code = _norm_space(short_code)
    collection_code, set_key = split_bd_collection_and_set_type(code)
    collection = lookup_bd_collection(code, collection_code)
    product_type = lookup_bd_product_type(set_key)

    # Bed-sheet maps miss towel SKUs — fall back to Bath/towel teaching.
    if not (collection and product_type):
        towel = enrich_towel_product(code, material_code=material_code)
        if towel and towel.get("matched"):
            return towel
        if towel and (towel.get("collection") or towel.get("product_type")):
            # Prefer towel partial over empty bed miss
            if not collection:
                collection = towel.get("collection")
            if not product_type:
                product_type = towel.get("product_type")

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
        "matched": bool(collection and product_type),
        "is_special_discount": is_towel_special_discount(material_code),
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
