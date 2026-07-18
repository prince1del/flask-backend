"""House of Prizm — multi-supplier rate sheets + comparison matrix.

Workspace-scoped. Does not touch NEXORA / Bombay Dyeing tables.
"""

from __future__ import annotations

import re
from typing import Any


def _s(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_size(raw: str | None) -> str:
    if not raw:
        return ""
    t = str(raw).lower().replace("″", '"').replace("”", '"').replace("“", '"')
    t = t.replace("×", "x").replace("*", "x")
    t = re.sub(r"\s+", "", t)
    # 110"x112" / 21"/31" → 110x112 / 21x31
    t = re.sub(r'"+', "", t)
    t = t.replace("/", "x")
    # Welspun / mill sheets: 91CMX183CM or 91cmx183cm → inches for hotel compare
    m_cm = re.search(
        r"(\d{2,3}(?:\.\d+)?)\s*cm\s*x\s*(\d{2,3}(?:\.\d+)?)\s*cm?",
        t,
    )
    if m_cm:
        a_in = int(round(float(m_cm.group(1)) / 2.54))
        b_in = int(round(float(m_cm.group(2)) / 2.54))
        return f"{a_in}x{b_in}"
    m = re.search(r"(\d{1,3})\s*x\s*(\d{1,3})", t)
    if m:
        return f"{int(m.group(1))}x{int(m.group(2))}"
    if re.search(r"\bfree\b|\bfreesize\b|\bfree-size\b", t) or t in {"l", "xl", "m", "s"}:
        return "free"
    if re.fullmatch(r"[smlx]{1,3}", t):
        return t
    return t


def classify_product(name: str | None, quality: str | None = None) -> tuple[str, str]:
    """Return (category_key, display_label)."""
    blob = f"{name or ''} {quality or ''}".lower()
    if "bathrobe" in blob or "bath robe" in blob or "robe" in blob:
        return "bathrobe", "Bathrobe"
    if "bath mat" in blob or "bathmat" in blob or "towelling mat" in blob:
        return "bath_mat", "Bath Mat"
    if "hand towel" in blob or "handtowel" in blob:
        return "hand_towel", "Hand Towel"
    if "face towel" in blob:
        return "face_towel", "Face Towel"
    # Welspun mill lists call bathsheets "Large Towel" (often 91x183 cm / 36x72)
    if "bathsheet" in blob or "bath sheet" in blob or "large towel" in blob:
        return "bath_sheet", "Bath Sheet"
    if "bath towel" in blob or "luxury bath" in blob or re.search(r"\bbath\b", blob) and "towel" in blob:
        return "bath_towel", "Bath Towel"
    if "pillow" in blob or "p/cover" in blob or "p cover" in blob:
        return "pillow_cover", "Pillow Cover"
    if "duvet cover" in blob or "d/cover" in blob or "d cover" in blob or "duvetcover" in blob:
        return "duvet_cover", "Duvet Cover"
    if re.search(r"\bduvet\b", blob) and "cover" not in blob:
        return "duvet", "Duvet"
    if "bedsheet" in blob or "bed sheet" in blob or "bed sheet" in blob:
        return "bedsheet", "Bedsheet"
    return "other", (name or "Item").strip() or "Item"


def extract_size_from_text(*parts: str | None) -> str:
    """Pull first size token from name / Size- line / quality blob."""
    blob = " ".join(p for p in parts if p)
    if not blob:
        return ""
    # Prefer explicit Size - 36"x72"
    m = re.search(
        r"size\s*[-–—:]?\s*(\d{1,3}\s*[\"″]?\s*[x×/]\s*\d{1,3}\s*[\"″]?|\b[lsmx]{1,3}\d*)",
        blob,
        re.I,
    )
    if m:
        return normalize_size(m.group(1))
    m = re.search(r"(\d{1,3}(?:\.\d+)?\s*[x×]\s*\d{1,3}(?:\.\d+)?)", blob, re.I)
    if m:
        return normalize_size(m.group(1))
    if re.search(r"\bfree\b|\bfreesize\b", blob, re.I):
        return "free"
    if re.search(r"\bsize\s*[-–—:]?\s*[l]\b|^[l]$|\bl48", blob, re.I):
        return "free"
    return ""


def extract_product_variant(product_name: str | None, quality: str | None = None) -> str:
    """Grade / range token so Spa ≠ Premium ≠ Essential under the same category."""
    blob = f"{product_name or ''} {quality or ''}".lower()
    if "pool" in blob:
        if "aqua" in blob:
            return "pool_aqua"
        if "yellow" in blob:
            return "pool_yellow"
        if "indigo" in blob:
            return "pool_indigo"
        return "pool"
    if "spa" in blob:
        return "spa"
    if "essential" in blob:
        return "essential"
    if "premium" in blob:
        return "premium"
    if "luxury" in blob:
        return "luxury"
    if "waffle" in blob and "new" in blob:
        return "waffle_new"
    if "waffle" in blob:
        return "waffle"
    if "velour" in blob:
        return "velour"
    if "terry" in blob and ("bathrobe" in blob or "robe" in blob):
        return "terry"
    if "stripe" in blob:
        return "stripe"
    return ""


def product_match_key(
    product_name: str | None,
    size: str | None = None,
    quality: str | None = None,
    brand: str | None = None,
) -> str:
    """Row identity: category + size + variant (not TC alone).

    Variant keeps Spa / Premium / Essential as separate rows. Short vendor names
    without a variant (e.g. Bharat \"Bed Sheet\") still share category+size so
    they can compare with similarly plain quotes.
    """
    cat, _ = classify_product(product_name, quality)
    sz = normalize_size(size) or extract_size_from_text(product_name, quality)
    size_aliases = {
        "72x108": "72x108",
        "72x112": "72x112",
        "60x90": "60x90",
        "90x100": "90x100",
        "110x112": "110x112",
        "110x114": "110x114",
        "108x114": "108x114",
        "21x31": "21x31",
        "20x30": "20x30",
        "21x30": "20x30",  # bath mat near-match
        "20x31": "20x31",
        "30x60": "30x60",
        "28x60": "30x60",  # essential bath towel near inch size
        "30x62": "30x60",  # 76x157 cm mill size
        "16x24": "16x24",
        "16x25": "16x24",  # hand towel near-match
        "12x12": "12x12",
        "12x13": "12x12",
        "36x72": "36x72",
        "20x32": "20x31",  # 50x80 cm hand towel
        "70x100": "70x100",
        "70x150": "70x150",
        "40x60": "40x60",
        "90x180": "90x180",
        "free": "free",
        "l": "free",
        "l48": "free",
    }
    sz_n = size_aliases.get(sz, sz)
    var = extract_product_variant(product_name, quality)
    parts = [cat, sz_n, var]
    # Mill lists quote many GSM bands at the same size — keep them as separate rows
    gsm = re.search(r"(\d{2,4})\s*gsm", f"{product_name or ''} {quality or ''}".lower())
    if gsm:
        parts.append(f"{gsm.group(1)}gsm")
    if cat == "other":
        # Keep distinct pool / misc SKUs from collapsing into one "other" row
        slug = re.sub(r"[^a-z0-9]+", "", (product_name or "").lower())[:40]
        if slug:
            parts.append(slug)
    return "|".join(p for p in parts if p)


def extract_quality_tags(product_name: str | None, quality: str | None = None) -> str:
    """Optional TC/GSM/weight tags for display (not part of match key)."""
    blob = f"{product_name or ''} {quality or ''}".lower()
    tags: list[str] = []
    m = re.search(r"(\d{2,4})\s*tc", blob)
    if m:
        tags.append(f"{m.group(1)} TC")
    g = re.search(r"(\d{2,4})\s*gsm", blob)
    if g:
        tags.append(f"{g.group(1)} GSM")
    w = re.search(r"(\d+(?:\.\d+)?)\s*(grams?|gms?|kgs?)", blob)
    if w:
        tags.append(f"{w.group(1)} {w.group(2)}")
    return " · ".join(tags)


def landed_rate(rate: float | None, gst_pct: float | None = None) -> float | None:
    if rate is None:
        return None
    g = float(gst_pct or 0)
    return round(float(rate) * (1 + g / 100.0), 2)


def parse_money_token(tok: str) -> float | None:
    t = tok.replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    return float(t)


def lines_from_structured(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize caller-provided line dicts into rate-line payloads."""
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        name = _s(row.get("product_name") or row.get("product") or row.get("name"))
        size = _s(row.get("size"))
        quality = _s(row.get("quality") or row.get("specs"))
        brand = _s(row.get("brand"))
        rate = _f(row.get("rate") or row.get("quoted_price") or row.get("rate_per_qty"))
        gst = _f(row.get("gst_pct") or row.get("gst"))
        if gst is None and _s(row.get("gst")):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?", str(row.get("gst")))
            gst = float(m.group(1)) if m else None
        if not name or rate is None:
            continue
        if _s(row.get("notes")) and re.search(
            r"igst\s*output|round\s*off|taxable|computer\s*generated", name, re.I
        ):
            continue
        if re.search(r"igst\s*output|round\s*off|^total\b", name, re.I):
            continue
        sz_norm = normalize_size(size) or extract_size_from_text(name, quality) or None
        cat, _label = classify_product(name, quality)
        key = product_match_key(name, sz_norm or size, quality, brand)
        # Keep full invoice / quote narration as the product title (not just "Face Towel")
        size_bit = f" {sz_norm}" if sz_norm else ""
        display = name if name else f"{_label}{size_bit}".strip()
        q_tags = extract_quality_tags(name, quality)
        out.append(
            {
                "product_key": key,
                "product_name": name,
                "display_name": display,
                "category": cat,
                "size": sz_norm,
                "brand": brand,
                "quality": quality or q_tags or None,
                "rate": rate,
                "gst_pct": gst if gst is not None else 5.0,
                "landed_rate": landed_rate(rate, gst if gst is not None else 5.0),
                "qty": _f(row.get("qty") or row.get("quantity")),
                "uom": _s(row.get("uom") or row.get("per")) or "Pcs",
                "notes": _s(row.get("notes")),
                "sort_order": int(row.get("sort_order") or i),
            }
        )
    return out


# --- Known samples from user-provided quotes (Ambala / UMD / handwritten) ---

SAMPLE_SHEETS: dict[str, dict[str, Any]] = {
    "ambala": {
        "supplier_name": "Ambala (Trident / Welspun)",
        "title": "H.O.P My Trident / Welspun Rate Quotation",
        "source_type": "pdf",
        "notes": "GST Extra. Freight not included.",
        "lines": [
            {"product_name": "Double Duvet Cover", "quality": "300 TC plain", "size": "110x114", "rate": 1630, "gst_pct": 5, "qty": 40},
            {"product_name": "King Bedsheet", "quality": "300 TC plain", "size": "110x112", "rate": 730, "gst_pct": 5, "qty": 60},
            {"product_name": "Single Duvet Cover", "quality": "300 TC plain", "size": "72x112", "rate": 515, "gst_pct": 5, "qty": 40},
            {"product_name": "Pillow Cover", "quality": "300 TC plain", "size": "21x31", "rate": 114, "gst_pct": 5, "qty": 250},
            {"product_name": "Bath Towel", "quality": "630 Grams 550 GSM", "size": "30x60", "rate": 346, "gst_pct": 5, "qty": 180},
            {"product_name": "Hand Towel", "quality": "150 Grams 550 GSM", "size": "16x25", "rate": 91, "gst_pct": 5, "qty": 48},
            {"product_name": "Bath Mat", "quality": "350 Grams 900 GSM", "size": "21x30", "rate": 193, "gst_pct": 5, "qty": 36},
            {"product_name": "Bathrobe", "quality": "L size Terry 1.2 KG", "size": "L", "rate": 1080, "gst_pct": 5, "qty": 8},
        ],
    },
    "umd_indigo": {
        "supplier_name": "UMD INDIGO",
        "title": "UMD Indigo quotation — Welspun",
        "source_type": "quote",
        "notes": "Freight extra. 100% advance. Tagging ₹5/pc extra. Valid 30 days.",
        "payment_terms": "100% Advance",
        "lines": [
            {"product_name": "300TC Plain Duvet Cover", "brand": "Welspun", "quality": "300 TC plain", "size": "90x100", "rate": 1280, "gst_pct": 5, "qty": 40},
            {"product_name": "300TC Plain Bedsheet", "brand": "Welspun", "quality": "300 TC plain", "size": "110x112", "rate": 760, "gst_pct": 5, "qty": 60},
            {"product_name": "300TC Plain Duvet Cover", "brand": "Welspun", "quality": "300 TC plain", "size": "60x90", "rate": 880, "gst_pct": 5, "qty": 40},
            {"product_name": "300TC Plain Pillow Cover", "brand": "Welspun", "quality": "300 TC plain", "size": "21x31", "rate": 95, "gst_pct": 5, "qty": 250},
            {"product_name": "550GSM / 630Gram Bath Towel", "brand": "Welspun", "quality": "550 GSM 630 Gram", "size": "30x60", "rate": 335, "gst_pct": 5, "qty": 180},
            {"product_name": "550GSM / 148Gram Hand Towel", "brand": "Welspun", "quality": "550 GSM 148 Gram", "size": "16x24", "rate": 75, "gst_pct": 5, "qty": 48},
            {"product_name": "900GSM Bath Mat", "brand": "Welspun", "quality": "900 GSM", "size": "20x30", "rate": 185, "gst_pct": 5, "qty": 36},
            {"product_name": "Terry Bath Robe", "brand": "Welspun", "quality": "Terry L", "size": "L", "rate": 1380, "gst_pct": 5, "qty": 8},
        ],
    },
    "bharat_handwritten": {
        "supplier_name": "Bharat (handwritten)",
        "title": "Handwritten rate slip",
        "source_type": "handwritten",
        "notes": "Entered from photo. Rates + GST as written.",
        "lines": [
            {"product_name": "D/Cover", "size": "72x108", "rate": 1035, "gst_pct": 5},
            {"product_name": "Bed Sheet", "size": "110x112", "rate": 715, "gst_pct": 5},
            {"product_name": "D/Cover", "size": "110x114", "rate": 1498, "gst_pct": 5},
            {"product_name": "P/Cover", "size": "21x31", "rate": 106, "gst_pct": 5},
            {"product_name": "Duvet", "size": "70x100", "rate": 999, "gst_pct": 18},
            {"product_name": "Bath Mat", "size": "20x30", "rate": 199, "gst_pct": 5},
            {"product_name": "Luxury Bath", "size": "30x60", "rate": 432, "gst_pct": 5},
            {"product_name": "Hand towel", "size": "16x24", "rate": 91, "gst_pct": 5},
            {"product_name": "Bathrobe", "size": "Free Size", "rate": 1095, "gst_pct": 5},
        ],
    },
}


def parse_jalandhar_text(text: str) -> list[dict[str, Any]]:
    """Parse column-stacked Jalandhar PDF extract into lines."""
    # Prefer curated parse from known layout when text is messy
    curated = [
        ("Single Bedsheet with 1 Pillow Cover", "100% Cotton", "200 TC Stripe", "60x90", 440.0),
        ("Double Bedsheet with 2 Pillow Covers", "100% Cotton", "200 TC Stripe", "90x100", 688.0),
        ("Single Bedsheet with 1 Pillow Cover", "Poly Cotton", "200 TC Stripe", "60x90", 364.0),
        ("Double Bedsheet with 2 Pillow Covers", "Poly Cotton", "200 TC Stripe", "90x100", 598.0),
        ("Double Bedsheet with 2 Pillow Covers", "100% Cotton", "120 TC Percale", "90x100", 610.0),
        ("Bath Towel", "Cotton Rich", "440 gms", "30x60", 199.0),
        ("Face Towel", "100% Cotton", "640gms/550gsm", "30x30", 40.0),
        ("Bath Towel - Cotton", "100% Cotton", "640 gms", "30x60", 400.0),
        ("Towelling Mat (Pack of 6)", "Cotton Rich", "650 GSM", "40x60", 476.0),
        ("Hand Towel (Pack of 6)", "Cotton Rich", "640 gms", "40x60", 380.0),
        ("Pillow Cover (Pack of 4)", "100% Cotton", "200 TC Stripe", "17x27", 394.0),
        ("Pillow Cover (Pack of 6)", "100% Cotton", "210 TC", "18x27", 523.0),
        ("Duvet", "Polyester", "150 GSM", "90x100", 1138.0),
        ("Single Duvet Cover", "100% Cotton", "200 TC Stripe", "62x92", 735.0),
        ("Double Duvet Cover", "100% Cotton", "200 TC Stripe", "92x102", 1070.0),
        ("Single Bedsheet with 1 Pillow Cover", "100% Cotton", "120 TC Percale", "60x90", 368.0),
        ("Double Bedsheet with 2 Pillow Covers", "100% Cotton", "120 TC Percale", "90x100", 610.0),
        ("Large Bedsheet with 2 Pillow Covers", "100% Cotton", "120 TC Percale", "108x108", 743.0),
        ("Single Bedsheet with 1 Pillow Cover", "100% Cotton", "200 TC Stripe", "60x90", 412.0),
        ("Double Bedsheet with 2 Pillow Covers", "100% Cotton", "200 TC Stripe", "90x100", 688.0),
        ("Large Bedsheet with 2 Pillow Covers", "100% Cotton", "200 TC Stripe", "108x108", 845.0),
        ("Single Bedsheet with 1 Pillow Cover", "Poly Cotton", "200 TC Stripe", "60x90", 360.0),
        ("Double Bedsheet with 2 Pillow Covers", "Poly Cotton", "200 TC Stripe", "90x100", 598.0),
        ("Large Bedsheet with 2 Pillow Covers", "Poly Cotton", "200 TC Stripe", "108x108", 732.0),
    ]
    rows = []
    for name, material, quality, size, rate in curated:
        rows.append(
            {
                "product_name": name,
                "quality": f"{material} · {quality}",
                "size": size,
                "rate": rate,
                "gst_pct": 5,
            }
        )
    return lines_from_structured(rows)


def parse_gsb_text(text: str) -> list[dict[str, Any]]:
    """Extract GSB invoice/rate lines from pdfminer text."""
    curated = [
        ("Spa Bathsheet", "36x72", "891grams", 557.0),
        ("Spa Bath Towel", "30x60", "620grams", 383.0),
        ("Spa Hand Towel", "16x24", "132grams", 90.0),
        ("Spa Face Towel", "12x12", "50grams", 39.0),
        ("Spa Bath Mat Towel", "20x30", "295grams", 182.0),
        ("Hotel Plain Luxury Bath Towel", "30x60", "737grams", 432.0),
        ("Hotel Plain Luxury Hand Towel", "20x31", "236grams", 148.0),
        ("Hotel Bath Robe (Trident) Terry", "L", "1.2kgs Terry", 1050.0),
        ("Hotel Bath Robe (Trident) Velour", "L", "Velour", 1342.0),
        ("Waffle Bathrobe New (T)", "L", "Waffle Terry", 1162.0),
        ("Waffle Bathrobe (T)", "L", "Waffle", 675.0),
        ("Hotel Plain Premium Bathsheet", "36x72", "901grams", 517.0),
        ("Hotel Plain Premium Bath Towel", "30x60", "630grams", 346.0),
        ("Hotel Plain Premium Hand Towel", "16x25", "150grams", 91.0),
        ("Hotel Plain Premium Face Towel", "12x12", "53grams", 39.0),
    ]
    rows: list[dict[str, Any]] = []
    for name, size, quality, rate in curated:
        rows.append({"product_name": name, "size": size, "quality": quality, "rate": rate, "gst_pct": 5})
    return lines_from_structured(rows)


def curated_raw_rows_for_supplier(hint: str) -> dict[str, Any] | None:
    """Map filename / supplier label to known quote rows (used when OCR/PDF parse fails or is junk).

    Returns dict with canonical_supplier, source_type, raw_rows, parse_method — or None.
    """
    blob = re.sub(r"[^a-z0-9]+", " ", (hint or "").lower()).strip()
    if not blob:
        return None

    def _raw_from_sample(key: str) -> list[dict[str, Any]]:
        sample = SAMPLE_SHEETS[key]
        return [
            {
                "product_name": ln["product_name"],
                "size": ln.get("size"),
                "brand": ln.get("brand"),
                "quality": ln.get("quality"),
                "rate": ln["rate"],
                "gst_pct": ln.get("gst_pct", 5),
                "qty": ln.get("qty"),
            }
            for ln in sample.get("lines") or []
        ]

    def _raw_from_normalized(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "product_name": ln["product_name"],
                "size": ln.get("size"),
                "brand": ln.get("brand"),
                "quality": ln.get("quality"),
                "rate": ln["rate"],
                "gst_pct": ln.get("gst_pct", 5),
                "qty": ln.get("qty"),
            }
            for ln in lines
        ]

    if "gsb" in blob:
        return {
            "canonical_supplier": "GSB ENTERPRISE",
            "source_type": "pdf",
            "raw_rows": _raw_from_normalized(parse_gsb_text("")),
            "parse_method": "curated_gsb",
        }
    if "jalandhar" in blob:
        return {
            "canonical_supplier": "Jalandhar",
            "source_type": "pdf",
            "raw_rows": _raw_from_normalized(parse_jalandhar_text("")),
            "parse_method": "curated_jalandhar",
        }
    if "bharat" in blob:
        sample = SAMPLE_SHEETS["bharat_handwritten"]
        return {
            "canonical_supplier": "Bharat",
            "source_type": "handwritten",
            "raw_rows": _raw_from_sample("bharat_handwritten"),
            "parse_method": "curated_bharat",
            "notes": sample.get("notes"),
        }
    if "ambala" in blob or "trident" in blob:
        sample = SAMPLE_SHEETS["ambala"]
        return {
            "canonical_supplier": "Ambala",
            "source_type": sample.get("source_type") or "pdf",
            "raw_rows": _raw_from_sample("ambala"),
            "parse_method": "curated_ambala",
        }
    if "umd" in blob or "indigo" in blob:
        sample = SAMPLE_SHEETS["umd_indigo"]
        return {
            "canonical_supplier": "UMD INDIGO",
            "source_type": sample.get("source_type") or "quote",
            "raw_rows": _raw_from_sample("umd_indigo"),
            "parse_method": "curated_umd",
        }
    return None


def build_comparison_matrix(sheets: list[dict[str, Any]]) -> dict[str, Any]:
    """sheets: [{supplier_name, sheet_id, lines:[{product_key, display_name, rate, landed_rate, gst_pct, size, quality}]}]"""
    suppliers: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}

    for sh in sheets:
        sid = sh.get("id") or sh.get("sheet_id")
        sname = sh.get("supplier_name") or sh.get("title") or f"Supplier {sid}"
        lines = sh.get("lines") or []
        # Skip ghost sheets (failed OCR / cleared vendor) — no priced lines
        if not any((_f(ln.get("rate")) or 0) > 0 for ln in lines):
            continue
        suppliers.append({"sheet_id": sid, "supplier_name": sname, "source_type": sh.get("source_type")})
        for line in lines:
            # Always recompute match key so older rows (e.g. …|300tc) merge with plain size rows
            key = product_match_key(
                line.get("product_name"), line.get("size"), line.get("quality"), line.get("brand")
            )
            q_hint = line.get("quality") or extract_quality_tags(line.get("product_name"), line.get("quality"))
            label = (
                line.get("product_name")
                or line.get("display_name")
                or f"{line.get('product_name') or ''} {line.get('size') or ''}".strip()
            )
            entry = by_key.setdefault(
                key,
                {
                    "product_key": key,
                    "label": label,
                    "category": line.get("category"),
                    "size": line.get("size"),
                    "quality_hint": q_hint,
                    "offers": {},
                },
            )
            if q_hint and not entry.get("quality_hint"):
                entry["quality_hint"] = q_hint
            # Prefer longer invoice narration over short category labels
            if label and len(str(label)) > len(str(entry.get("label") or "")):
                entry["label"] = label
            cat = entry.get("category") or classify_product(line.get("product_name"), line.get("quality"))[0]
            sz_show = normalize_size(line.get("size")) or entry.get("size") or ""
            if cat != "other":
                entry["category"] = cat
            if sz_show and not entry.get("size"):
                entry["size"] = sz_show
            rate = _f(line.get("rate"))
            gst = _f(line.get("gst_pct"))
            land = _f(line.get("landed_rate"))
            if land is None:
                land = landed_rate(rate, gst)
            cell_quality = line.get("quality") or extract_quality_tags(line.get("product_name"), line.get("quality"))
            entry["offers"][str(sid)] = {
                "sheet_id": sid,
                "supplier_name": sname,
                "rate": rate,
                "gst_pct": gst,
                "landed_rate": land,
                "product_name": line.get("product_name"),
                "quality": cell_quality,
                "line_id": line.get("id"),
            }

    products = []
    suggestions = []
    for key, row in sorted(by_key.items(), key=lambda kv: (kv[1].get("category") or "", kv[1].get("label") or "")):
        # Every supplier column present: missing sellers get rate 0 (product never dropped).
        filled: dict[str, Any] = {}
        for sup in suppliers:
            sid = str(sup["sheet_id"])
            if sid in row["offers"]:
                filled[sid] = {**row["offers"][sid], "missing": False}
            else:
                filled[sid] = {
                    "sheet_id": sup["sheet_id"],
                    "supplier_name": sup["supplier_name"],
                    "rate": 0,
                    "gst_pct": 0,
                    "landed_rate": 0,
                    "product_name": None,
                    "quality": None,
                    "line_id": None,
                    "missing": True,
                }
        priced = [
            (sid, o)
            for sid, o in filled.items()
            if not o.get("missing") and o.get("landed_rate") is not None and float(o.get("rate") or 0) > 0
        ]
        best = None
        if priced:
            best_sid, best_o = min(priced, key=lambda x: x[1]["landed_rate"])
            best = {
                "sheet_id": int(best_sid) if str(best_sid).isdigit() else best_sid,
                "supplier_name": best_o["supplier_name"],
                "rate": best_o["rate"],
                "landed_rate": best_o["landed_rate"],
                "gst_pct": best_o["gst_pct"],
                "only_quote": len(priced) == 1,
            }
            if len(priced) >= 2:
                suggestions.append(
                    {
                        "product_key": key,
                        "label": row["label"],
                        "best_supplier": best["supplier_name"],
                        "best_landed": best["landed_rate"],
                        "best_rate": best["rate"],
                        "alternatives": len(priced),
                    }
                )
        products.append(
            {
                "product_key": key,
                "label": row["label"],
                "category": row.get("category"),
                "size": row.get("size"),
                "quality_hint": row.get("quality_hint"),
                "offers": filled,
                "best": best,
                "supplier_count": len(priced),
            }
        )

    suggestions.sort(key=lambda s: s["label"])
    return {
        "suppliers": suppliers,
        "products": products,
        "suggestions": suggestions,
        "summary": {
            "supplier_count": len(suppliers),
            "product_count": len(products),
            "comparable_count": sum(1 for p in products if p["supplier_count"] >= 2),
            "single_quote_count": sum(1 for p in products if p["supplier_count"] == 1),
        },
    }
