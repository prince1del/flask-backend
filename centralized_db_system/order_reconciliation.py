"""
Nexora Order Reconciliation Engine
===================================

Purpose
-------
Compares the full business chain:

    Master Booking Form / Order Sheet  -> product reference only
    Distributor Filled Order Sheet     -> actual distributor order using AWDs Qty
    Sales Order / Retail Sale Contract -> company-created SO, SKU/colorway split
    Commercial Invoice                 -> billing against SO

Founder-locked rules implemented here
-------------------------------------
1. AWDs Qty is the actual distributor order quantity.
2. Do NOT derive final order qty from AWDs No. of Bales x Bale Size.
   Use bales only as a cross-check/warning.
3. Order value = AWDs Qty x Exmill Price.
4. Master Booking Form is product/rate/size reference; it is not the
   distributor's actual order quantity sheet.
5. SO/CI can contain 1 or many product groups. Each product group can
   be split into many SKU/material-code/design-color lines.
6. Filled Order may only say Designs=6 and Colorways=3. SO/CI may show
   exact codes like 7985BLU. Nexora compares the structure:
      expected SKU lines = Designs x Colorways.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
import math
import re

import pandas as pd

try:  # pypdf is available in the current project/test environment
    from pypdf import PdfReader
except Exception:  # pragma: no cover - app may use its own PDF extractor
    PdfReader = None  # type: ignore[assignment]


# -----------------------------
# Business aliases / normalizers
# -----------------------------

PRODUCT_ALIASES: dict[str, str] = {
    "DB BS": "DB BS", "DBBS": "DB BS", "DBS": "DB BS", "DB": "DB BS",
    "SB BS": "SB BS", "SBBS": "SB BS", "SBS": "SB BS", "SB": "SB BS",
    "KS BS": "KS BS", "KSBS": "KS BS", "KBS": "KS BS", "KS": "KS BS",
    "KB FS": "KB FS", "KBFS": "KB FS", "KFS": "KB FS", "KB": "KB FS",
    "DB FS": "DB FS", "DBFS": "DB FS", "DFS": "DB FS",
}

PRODUCT_LABELS: dict[str, str] = {
    "DB BS": "Double Bedsheet",
    "SB BS": "Single Bedsheet",
    "KS BS": "King Bedsheet",
    "KB FS": "King Bed Fitted Sheet",
    "DB FS": "Double Bed Fitted Sheet",
}

VALID_SIZE_MAP: dict[str, set[str]] = {
    "DB BS": {"224X244", "224X254", "228X254"},
    "SB BS": {"140X224", "150X224", "152X228"},
    "KS BS": {"274X274", "300X304"},
    "KB FS": {"183X198X30"},
    "DB FS": {"152X198X30"},
}

COLOUR_NAME_MAP: dict[str, str] = {
    "BLU": "Blue",
    "ORG": "Orange",
    "PNK": "Pink",
    "BRW": "Brown",
    "MRN": "Maroon",
    "MST": "Mustard",
    "TEA": "Teal",
    "GRY": "Grey",
    "LLC": "Lilac",
    "GRN": "Green",
    "BGE": "Beige",
    "PCH": "Peach",
}


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if text in {"", ".", "-"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int_if_whole(value: float | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def normalize_size_text(value: Any) -> str:
    """224 X 244, 224x244 and 224 X244 all normalize to 224X244."""
    text = _clean_string(value).upper()
    if not text:
        return ""
    text = text.replace("×", "X")
    text = re.sub(r"\s*[X]\s*", "X", text)
    text = re.sub(r"\s+", "", text)
    return text


def normalize_product_code(value: Any) -> str:
    text = _clean_string(value).upper()
    if not text:
        return ""
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    compact = text.replace(" ", "")

    # Try exact/compact aliases first
    if text in PRODUCT_ALIASES:
        return PRODUCT_ALIASES[text]
    if compact in PRODUCT_ALIASES:
        return PRODUCT_ALIASES[compact]

    # Then try embedded tokens, preferring fitted-sheet aliases before DB generic.
    for alias in ("KB FS", "KBFS", "KFS", "DB FS", "DBFS", "DFS", "KS BS", "KSBS", "KBS", "DB BS", "DBBS", "DBS", "SB BS", "SBBS", "SBS"):
        if alias in text or alias in compact:
            return PRODUCT_ALIASES[alias]
    return text


def short_product_code(product_code: str) -> str:
    """DB BS -> DB, KS BS -> KS, KB FS -> KB, etc. Used for existing Nexora item_key compatibility."""
    normalized = normalize_product_code(product_code)
    return normalized.split()[0] if normalized else ""


def normalize_brand(value: Any) -> str:
    text = _clean_string(value).upper()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_tc(value: Any) -> str:
    text = _clean_string(value).upper().replace("TC", "")
    try:
        return str(int(float(text)))
    except Exception:
        return re.sub(r"\s+", "", text)


def make_item_key(brand: Any, tc: Any, product_code: Any) -> str | None:
    """Backward-compatible Nexora key: ASTER|100|DB."""
    brand_n = normalize_brand(brand)
    tc_n = normalize_tc(tc)
    short = short_product_code(str(product_code))
    if not brand_n or not tc_n or not short:
        return None
    return f"{brand_n}|{tc_n}|{short}"


def make_group_key(brand: Any, tc: Any, product_code: Any, bedset_size: Any, rate: Any | None = None) -> str:
    """More precise group key for reconciliation reports."""
    parts = [normalize_brand(brand), normalize_tc(tc), normalize_product_code(product_code), normalize_size_text(bedset_size)]
    rate_f = _to_float(rate, default=-1)
    if rate_f >= 0:
        parts.append(str(_to_int_if_whole(rate_f)))
    return "|".join(parts)


# -----------------------------
# Data classes
# -----------------------------

@dataclass
class FilledOrderGroup:
    brand: str
    tc: str
    product_code: str
    product_label: str
    bedset_size: str
    units: str = ""
    designs: float = 0
    colorways: float = 0
    expected_sku_lines: float = 0
    bale_size: float = 0
    awd_bales: float = 0
    ordered_qty: float = 0
    exmill_price: float = 0
    ordered_value: float = 0
    expected_order_value: float = 0
    value_difference: float = 0
    bale_qty_crosscheck: float = 0
    bale_qty_difference: float = 0
    item_key: str | None = None
    group_key: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("designs", "colorways", "expected_sku_lines", "bale_size", "awd_bales", "ordered_qty", "exmill_price", "ordered_value", "expected_order_value", "value_difference", "bale_qty_crosscheck", "bale_qty_difference"):
            data[key] = _to_int_if_whole(data[key])
        return data


@dataclass
class DocumentLine:
    material_code: str | None
    material_description: str
    brand: str
    units: str
    product_code: str
    bedset_size: str
    design_no: str
    color_code: str
    color_name: str | None
    tc: str
    qty: float
    rate: float
    net_value: float
    gst_value: float | None = None
    total_value: float | None = None
    item_key: str | None = None
    group_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("qty", "rate", "net_value", "gst_value", "total_value"):
            data[key] = _to_int_if_whole(data[key]) if data[key] is not None else None
        return data


@dataclass
class DocumentGroup:
    brand: str
    tc: str
    product_code: str
    product_label: str
    bedset_size: str
    rate: float
    item_key: str | None
    group_key: str
    qty: float = 0
    net_value: float = 0
    gst_value: float = 0
    total_value: float = 0
    line_count: int = 0
    design_numbers: set[str] = field(default_factory=set)
    color_codes: set[str] = field(default_factory=set)
    color_codes_by_design: dict[str, set[str]] = field(default_factory=dict)
    lines: list[DocumentLine] = field(default_factory=list)

    def add_line(self, line: DocumentLine) -> None:
        self.qty += line.qty
        self.net_value += line.net_value
        self.gst_value += line.gst_value or 0
        self.total_value += line.total_value or 0
        self.line_count += 1
        if line.design_no:
            self.design_numbers.add(line.design_no)
        if line.color_code:
            self.color_codes.add(line.color_code)
        if line.design_no:
            self.color_codes_by_design.setdefault(line.design_no, set()).add(line.color_code)
        self.lines.append(line)

    def to_dict(self, include_lines: bool = True) -> dict[str, Any]:
        colorways_per_design = {d: sorted(c for c in colors if c) for d, colors in sorted(self.color_codes_by_design.items())}
        data = {
            "brand": self.brand,
            "tc": self.tc,
            "product_code": self.product_code,
            "product_label": self.product_label,
            "bedset_size": self.bedset_size,
            "rate": _to_int_if_whole(self.rate),
            "item_key": self.item_key,
            "group_key": self.group_key,
            "qty": _to_int_if_whole(self.qty),
            "net_value": _to_int_if_whole(self.net_value),
            "gst_value": _to_int_if_whole(self.gst_value),
            "total_value": _to_int_if_whole(self.total_value),
            "line_count": self.line_count,
            "design_count": len(self.design_numbers),
            "design_numbers": sorted(self.design_numbers),
            "colorway_count_total_unique": len(self.color_codes),
            "color_codes": sorted(c for c in self.color_codes if c),
            "colorways_per_design": colorways_per_design,
        }
        if colorways_per_design:
            per_design_counts = [len(colors) for colors in colorways_per_design.values()]
            data["colorways_per_design_count"] = per_design_counts[0] if len(set(per_design_counts)) == 1 else per_design_counts
        else:
            data["colorways_per_design_count"] = 0
        if include_lines:
            data["lines"] = [line.to_dict() for line in self.lines]
        return data


# -----------------------------
# Spreadsheet parsers
# -----------------------------

_COLUMN_ALIASES = {
    "brand": ["brand", "design", "collection"],
    "tc": ["tc", "thread count", "thread_count"],
    "size": ["size", "bs size", "bedset type", "product size"],
    "units": ["units", "unit", "set"],
    "bedset_size": ["bedset size (cms)", "bedset size", "bed set size", "size cms", "size (cms)", "dimensions"],
    "designs": ["aw'25 designs", "aw25 designs", "designs", "no of designs", "no. of designs"],
    "colorways": ["colorways", "colourways", "colors", "colours", "no of colors", "no. of colors"],
    "bale_size": ["bale size", "pcs per bale", "pieces per bale"],
    "awd_bales": ["awds no of bales", "awds no. of bales", "awds no of bale", "awd no of bales", "no of bales", "bales"],
    "awd_qty": ["awds qty", "awd qty", "order qty", "ordered qty", "qty"],
    "exmill_price": ["exmill price", "ex mill price", "ex-mill price", "rate", "price"],
    "awd_order_value": ["awd order value", "awds order value", "order value", "value"],
}


def _find_column(columns: Iterable[Any], logical_name: str) -> str | None:
    normalized = {str(c).strip().lower(): c for c in columns}
    for alias in _COLUMN_ALIASES[logical_name]:
        if alias in normalized:
            return normalized[alias]
    # loose fallback
    for alias in _COLUMN_ALIASES[logical_name]:
        alias_compact = re.sub(r"[^a-z0-9]", "", alias)
        for raw in normalized:
            if alias_compact and alias_compact == re.sub(r"[^a-z0-9]", "", raw):
                return normalized[raw]
    return None


def _read_excel_or_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def parse_filled_order_excel(path: str | Path, include_zero_qty: bool = False) -> list[FilledOrderGroup]:
    """Parses a distributor filled order. Actual order qty = AWDs Qty."""
    df = _read_excel_or_csv(path)
    cols = {name: _find_column(df.columns, name) for name in _COLUMN_ALIASES}
    missing_required = [name for name in ("brand", "tc", "size", "awd_qty", "exmill_price") if cols.get(name) is None]
    if missing_required:
        raise ValueError(f"Missing required filled-order columns: {missing_required}")

    groups: list[FilledOrderGroup] = []
    for _, row in df.iterrows():
        brand = normalize_brand(row.get(cols["brand"]))
        if not brand:
            continue
        tc = normalize_tc(row.get(cols["tc"]))
        product_code = normalize_product_code(row.get(cols["size"]))
        bedset_size = normalize_size_text(row.get(cols["bedset_size"])) if cols.get("bedset_size") else ""
        units = _clean_string(row.get(cols["units"])) if cols.get("units") else ""
        designs = _to_float(row.get(cols["designs"])) if cols.get("designs") else 0
        colorways = _to_float(row.get(cols["colorways"])) if cols.get("colorways") else 0
        bale_size = _to_float(row.get(cols["bale_size"])) if cols.get("bale_size") else 0
        awd_bales = _to_float(row.get(cols["awd_bales"])) if cols.get("awd_bales") else 0
        ordered_qty = _to_float(row.get(cols["awd_qty"]))
        exmill_price = _to_float(row.get(cols["exmill_price"]))
        ordered_value = _to_float(row.get(cols["awd_order_value"])) if cols.get("awd_order_value") else ordered_qty * exmill_price

        if ordered_qty <= 0 and not include_zero_qty:
            continue

        expected_order_value = ordered_qty * exmill_price
        value_difference = expected_order_value - ordered_value
        expected_sku_lines = designs * colorways if designs > 0 and colorways > 0 else 0
        bale_qty_crosscheck = awd_bales * bale_size if awd_bales > 0 and bale_size > 0 else 0
        bale_qty_difference = ordered_qty - bale_qty_crosscheck if bale_qty_crosscheck else 0
        item_key = make_item_key(brand, tc, product_code)
        group_key = make_group_key(brand, tc, product_code, bedset_size, exmill_price)
        warnings: list[str] = []

        if abs(value_difference) > 0.01:
            warnings.append(f"Missing/value mismatch: AWDs Qty x Exmill Price = {expected_order_value:.2f}, sheet value = {ordered_value:.2f}")
        if bale_qty_crosscheck and abs(bale_qty_difference) > 0.01:
            warnings.append(f"Bale cross-check mismatch only: AWDs Qty {ordered_qty:g} vs Bales x Bale Size {bale_qty_crosscheck:g}")
        if product_code in VALID_SIZE_MAP and bedset_size and bedset_size not in VALID_SIZE_MAP[product_code]:
            warnings.append(f"Size {bedset_size} is not in locked size map for {product_code}")
        if ordered_qty > 0 and (designs <= 0 or colorways <= 0):
            warnings.append("Design/colorway count missing or zero for ordered row")

        groups.append(FilledOrderGroup(
            brand=brand,
            tc=tc,
            product_code=product_code,
            product_label=PRODUCT_LABELS.get(product_code, product_code),
            bedset_size=bedset_size,
            units=units,
            designs=designs,
            colorways=colorways,
            expected_sku_lines=expected_sku_lines,
            bale_size=bale_size,
            awd_bales=awd_bales,
            ordered_qty=ordered_qty,
            exmill_price=exmill_price,
            ordered_value=ordered_value,
            expected_order_value=expected_order_value,
            value_difference=value_difference,
            bale_qty_crosscheck=bale_qty_crosscheck,
            bale_qty_difference=bale_qty_difference,
            item_key=item_key,
            group_key=group_key,
            warnings=warnings,
        ))
    return groups


def parse_master_booking_form(path: str | Path) -> list[FilledOrderGroup]:
    """Same columns as filled order, but used as reference only. Qty is not treated as actual customer order."""
    return parse_filled_order_excel(path, include_zero_qty=True)


# -----------------------------
# PDF / SO / CI parsers
# -----------------------------

_DESCRIPTION_PATTERN = re.compile(
    r"(?:(?P<material_code>BS[A-Z0-9]+)\s+)?"
    r"(?P<brand>[A-Z][A-Z /&]+?)\s+"
    r"(?P<units>\d\s*\+\s*\d)\s+"
    r"(?P<size_code>DB|SB|KS|KB)\s+SET\s+"
    r"(?P<bedset_size>\d{3}\s*[Xx]\s*\d{3}(?:\s*[Xx]\s*\d{2})?)\s+"
    r"(?P<designcolor>\d{4}[A-Z]{3})\s+"
    r"(?P<tc>\d+)\s*TC",
    re.IGNORECASE | re.DOTALL,
)

_MATERIAL_CODE_PATTERN = re.compile(r"\b(BS[A-Z0-9]{8,})\b", re.I)


def extract_pdf_text(path: str | Path) -> str:
    """Extract text from a PDF using the best available Nexora/project extractor.

    Priority:
    1. pypdf, when installed.
    2. Existing Nexora extractor app.three_step_verification._extract_pdf_text.

    This keeps the reconciliation module working even when pypdf is not
    installed in the local environment, because the main Nexora app already
    has a PDF extraction helper used by the older 4-stage verification flow.
    """
    if PdfReader is not None:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    try:
        from app.three_step_verification import _extract_pdf_text
    except Exception as exc:  # pragma: no cover - environment-specific fallback
        raise RuntimeError(
            "PDF text extraction is not available. Install pypdf with `pip install pypdf` "
            "or ensure app.three_step_verification._extract_pdf_text is available. "
            f"Import error: {exc}"
        ) from exc

    try:
        return _extract_pdf_text(str(path))
    except Exception as exc:  # pragma: no cover - environment-specific fallback
        raise RuntimeError(
            "Could not extract PDF text using Nexora's fallback extractor. "
            f"Details: {exc}"
        ) from exc


def _compact_description_text(text: str) -> str:
    text = text or ""
    # Normalize broken decimal fragments in CI tables: 66.0\n00 -> 66.000, 38,280.\n00 -> 38,280.00
    tokens = text.replace("\r", "\n").split()
    stitched: list[str] = []
    for token in tokens:
        if stitched:
            prev = stitched[-1]
            if prev.endswith(".") and re.fullmatch(r"\d{1,3}", token):
                stitched[-1] = prev + token
                continue
            if re.fullmatch(r"\d[\d,]*\.\d{1,2}", prev) and re.fullmatch(r"\d{1,2}", token):
                frac_len = len(prev.split(".", 1)[1])
                if frac_len + len(token) <= 3:
                    stitched[-1] = prev + token
                    continue
        stitched.append(token)
    return " ".join(stitched)


def _numbers_after(text: str, start_index: int, max_chars: int = 420) -> list[float]:
    window = text[start_index:start_index + max_chars]
    # Remove HSN / isolated serials and capture numbers with decimals.
    values: list[float] = []
    for match in re.finditer(r"\b\d[\d,]*\.\d+\b", window):
        raw = match.group(0).replace(",", "")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def _find_material_code_near(text: str, match_start: int) -> str | None:
    window = text[max(0, match_start - 40):match_start + 20]
    codes = _MATERIAL_CODE_PATTERN.findall(window)
    if codes:
        return codes[-1].upper()
    return None


def parse_material_description(description: str) -> dict[str, Any] | None:
    compact = _compact_description_text(description).upper()
    match = _DESCRIPTION_PATTERN.search(compact)
    if not match:
        return None
    designcolor = match.group("designcolor").upper()
    product_code = normalize_product_code(match.group("size_code"))
    return {
        "material_code": (match.group("material_code") or "").upper() or None,
        "brand": normalize_brand(match.group("brand")),
        "units": re.sub(r"\s+", "", match.group("units")),
        "product_code": product_code,
        "product_label": PRODUCT_LABELS.get(product_code, product_code),
        "bedset_size": normalize_size_text(match.group("bedset_size")),
        "design_no": designcolor[:4],
        "color_code": designcolor[4:],
        "color_name": COLOUR_NAME_MAP.get(designcolor[4:]),
        "tc": normalize_tc(match.group("tc")),
        "designcolor": designcolor,
    }


def parse_so_ci_text(text: str, document_type: str = "SO") -> list[DocumentLine]:
    """
    Parses Bombay Dyeing SO/CI text into SKU/colorway-level lines.
    Works for both SO text (neat 3 physical lines) and CI text where PDF
    extraction splits numbers and descriptions into many lines.
    """
    compact = _compact_description_text(text).upper()
    lines: list[DocumentLine] = []
    for match in _DESCRIPTION_PATTERN.finditer(compact):
        parsed = parse_material_description(match.group(0))
        if not parsed:
            continue
        nums = _numbers_after(compact, match.end())
        # Expected sequence in SO: qty, rate, date-as-07.06, net, gst, total.
        # Expected sequence in CI: qty, rate, amount, discount, taxable, taxes..., igst-rate, igst, total.
        qty = rate = net_value = gst_value = total_value = 0.0
        if len(nums) >= 3:
            qty, rate = nums[0], nums[1]
            # Prefer amount/taxable value equal to qty x rate.
            expected_net = qty * rate
            candidates = nums[2:]
            net_value = candidates[0]
            for val in candidates:
                if abs(val - expected_net) <= max(1.0, expected_net * 0.005):
                    net_value = val
                    break
            # GST and total: last two money-looking values usually work.
            if len(candidates) >= 2:
                money_values = [v for v in candidates if v >= 1]
                if money_values:
                    total_value = money_values[-1]
                if len(money_values) >= 2:
                    gst_value = money_values[-2]
        else:
            # Not enough numeric data; skip this line because qty/value cannot be trusted.
            continue

        material_code = parsed["material_code"] or _find_material_code_near(compact, match.start())
        description = f"{parsed['brand']} {parsed['units']} {parsed['product_code'].split()[0]} SET {parsed['bedset_size']} {parsed['designcolor']} {parsed['tc']}TC"
        item_key = make_item_key(parsed["brand"], parsed["tc"], parsed["product_code"])
        group_key = make_group_key(parsed["brand"], parsed["tc"], parsed["product_code"], parsed["bedset_size"], rate)
        lines.append(DocumentLine(
            material_code=material_code,
            material_description=description,
            brand=parsed["brand"],
            units=parsed["units"],
            product_code=parsed["product_code"],
            bedset_size=parsed["bedset_size"],
            design_no=parsed["design_no"],
            color_code=parsed["color_code"],
            color_name=parsed["color_name"],
            tc=parsed["tc"],
            qty=qty,
            rate=rate,
            net_value=net_value,
            gst_value=gst_value,
            total_value=total_value,
            item_key=item_key,
            group_key=group_key,
        ))
    _append_orphan_pagebreak_lines(compact, lines)
    lines.sort(key=lambda line: (line.brand, line.tc, line.product_code, line.bedset_size, line.design_no, line.color_code))
    return lines


def parse_so_ci_pdf(path: str | Path, document_type: str = "SO") -> list[DocumentLine]:
    return parse_so_ci_text(extract_pdf_text(path), document_type=document_type)



def _append_orphan_pagebreak_lines(compact: str, existing: list[DocumentLine]) -> None:
    """
    Some Commercial Invoice PDFs split the design/color token across a page
    boundary. Example from the real sample: the line containing
    `ASTER 1+2 DB SET 224X244` and its numeric values appears at the end of
    page 2, while `7990BGE 100TC` is extracted at the beginning of page 3.
    The normal description regex cannot see that as one continuous
    description. This repair pass finds designcolor+TC tokens not already
    parsed and attaches them to the nearest preceding product skeleton.
    """
    parsed_designcolors = {f"{line.design_no}{line.color_code}" for line in existing}
    orphan_pattern = re.compile(r"\b(?P<designcolor>\d{4}[A-Z]{3})\s+(?P<tc>\d+)\s*TC\b", re.I)
    skeleton_pattern = re.compile(
        r"(?P<brand>[A-Z][A-Z /&]+?)\s+"
        r"(?P<units>\d\s*\+\s*\d)\s+"
        r"(?P<size_code>DB|SB|KS|KB)\s+SET\s+"
        r"(?P<bedset_size>\d{3}\s*X\s*\d{3}(?:\s*X\s*\d{2})?)\s*",
        re.I | re.DOTALL,
    )
    for orphan in orphan_pattern.finditer(compact):
        designcolor = orphan.group("designcolor").upper()
        if designcolor in parsed_designcolors:
            continue
        lookback_start = max(0, orphan.start() - 520)
        lookback = compact[lookback_start:orphan.start()]
        skeletons = list(skeleton_pattern.finditer(lookback))
        if not skeletons:
            continue
        skeleton = skeletons[-1]
        global_skeleton_start = lookback_start + skeleton.start()
        global_skeleton_end = lookback_start + skeleton.end()

        # Only repair when the text immediately after the size was NOT a normal designcolor.
        after_size = compact[global_skeleton_end:global_skeleton_end + 20]
        if re.match(r"\s*\d{4}[A-Z]{3}\s+\d+\s*TC", after_size, re.I):
            continue

        nums = _numbers_after(compact, global_skeleton_end, max_chars=orphan.start() - global_skeleton_end)
        if len(nums) < 3:
            continue
        qty, rate = nums[0], nums[1]
        expected_net = qty * rate
        candidates = nums[2:]
        net_value = candidates[0]
        for val in candidates:
            if abs(val - expected_net) <= max(1.0, expected_net * 0.005):
                net_value = val
                break
        money_values = [v for v in candidates if v >= 1]
        gst_value = money_values[-2] if len(money_values) >= 2 else 0
        total_value = money_values[-1] if money_values else 0

        brand = normalize_brand(skeleton.group("brand"))
        units = re.sub(r"\s+", "", skeleton.group("units"))
        product_code = normalize_product_code(skeleton.group("size_code"))
        bedset_size = normalize_size_text(skeleton.group("bedset_size"))
        tc = normalize_tc(orphan.group("tc"))
        item_key = make_item_key(brand, tc, product_code)
        group_key = make_group_key(brand, tc, product_code, bedset_size, rate)
        existing.append(DocumentLine(
            material_code=_find_material_code_near(compact, global_skeleton_start),
            material_description=f"{brand} {units} {product_code.split()[0]} SET {bedset_size} {designcolor} {tc}TC",
            brand=brand,
            units=units,
            product_code=product_code,
            bedset_size=bedset_size,
            design_no=designcolor[:4],
            color_code=designcolor[4:],
            color_name=COLOUR_NAME_MAP.get(designcolor[4:]),
            tc=tc,
            qty=qty,
            rate=rate,
            net_value=net_value,
            gst_value=gst_value,
            total_value=total_value,
            item_key=item_key,
            group_key=group_key,
        ))
        parsed_designcolors.add(designcolor)


def group_document_lines(lines: list[DocumentLine]) -> dict[str, DocumentGroup]:
    groups: dict[str, DocumentGroup] = {}
    for line in lines:
        key = line.group_key
        if key not in groups:
            groups[key] = DocumentGroup(
                brand=line.brand,
                tc=line.tc,
                product_code=line.product_code,
                product_label=PRODUCT_LABELS.get(line.product_code, line.product_code),
                bedset_size=line.bedset_size,
                rate=line.rate,
                item_key=line.item_key,
                group_key=key,
            )
        groups[key].add_line(line)
    return groups


# -----------------------------
# Reconciliation
# -----------------------------


def _status_from_bool(ok: bool) -> str:
    return "MATCH" if ok else "MISMATCH"


def _close(a: float, b: float, tolerance: float = 0.01) -> bool:
    return abs((a or 0) - (b or 0)) <= tolerance


def _match_master_reference(filled: FilledOrderGroup, master_by_group: dict[str, FilledOrderGroup]) -> dict[str, Any]:
    master = master_by_group.get(filled.group_key)
    if master:
        return {
            "status": "MATCH",
            "message": "Product, size and rate found in master booking form.",
            "master_reference": master.to_dict(),
        }

    # Fallback: ignore rate in case price changed, but still surface rate mismatch.
    fallback_key_prefix = "|".join(filled.group_key.split("|")[:-1])
    matches = [m for k, m in master_by_group.items() if k.startswith(fallback_key_prefix)]
    if matches:
        m = matches[0]
        return {
            "status": "WARNING",
            "message": "Product and size found in master, but rate/group key differs.",
            "master_reference": m.to_dict(),
        }
    return {"status": "MISMATCH", "message": "Product/size/rate not found in master booking form.", "master_reference": None}


def reconcile_order_chain(
    master_booking_form: str | Path | None,
    distributor_filled_order: str | Path,
    sales_order_pdf: str | Path | None = None,
    commercial_invoice_pdf: str | Path | None = None,
) -> dict[str, Any]:
    master_groups = parse_master_booking_form(master_booking_form) if master_booking_form else []
    filled_groups = parse_filled_order_excel(distributor_filled_order)
    so_lines = parse_so_ci_pdf(sales_order_pdf, "SO") if sales_order_pdf else []
    ci_lines = parse_so_ci_pdf(commercial_invoice_pdf, "CI") if commercial_invoice_pdf else []

    master_by_group = {g.group_key: g for g in master_groups}
    filled_by_group = {g.group_key: g for g in filled_groups}
    so_groups = group_document_lines(so_lines)
    ci_groups = group_document_lines(ci_lines)

    all_keys = sorted(set(filled_by_group) | set(so_groups) | set(ci_groups))
    product_reports: list[dict[str, Any]] = []

    totals = {
        "filled_qty": 0.0, "filled_value": 0.0,
        "so_qty": 0.0, "so_value": 0.0,
        "ci_qty": 0.0, "ci_value": 0.0,
    }

    for key in all_keys:
        filled = filled_by_group.get(key)
        so = so_groups.get(key)
        ci = ci_groups.get(key)
        if filled:
            totals["filled_qty"] += filled.ordered_qty
            totals["filled_value"] += filled.ordered_value
        if so:
            totals["so_qty"] += so.qty
            totals["so_value"] += so.net_value
        if ci:
            totals["ci_qty"] += ci.qty
            totals["ci_value"] += ci.net_value

        design_check: dict[str, Any] = {"status": "NOT_AVAILABLE"}
        if filled:
            expected_designs = int(filled.designs or 0)
            expected_colorways = int(filled.colorways or 0)
            expected_lines = int(filled.expected_sku_lines or 0)
            actual_designs = so.to_dict(False)["design_count"] if so else 0
            actual_colorways_per_design = so.to_dict(False).get("colorways_per_design_count") if so else 0
            actual_lines = so.line_count if so else 0
            if expected_lines > 0 and so:
                design_check = {
                    "status": _status_from_bool(expected_designs == actual_designs and expected_colorways == actual_colorways_per_design and expected_lines == actual_lines),
                    "filled_designs": expected_designs,
                    "filled_colorways": expected_colorways,
                    "filled_expected_sku_lines": expected_lines,
                    "so_designs": actual_designs,
                    "so_colorways_per_design": actual_colorways_per_design,
                    "so_sku_lines": actual_lines,
                    "so_design_numbers": so.to_dict(False).get("design_numbers", []),
                    "so_colorways_per_design_detail": so.to_dict(False).get("colorways_per_design", {}),
                }
            elif expected_lines > 0:
                design_check = {
                    "status": "MISMATCH",
                    "filled_designs": expected_designs,
                    "filled_colorways": expected_colorways,
                    "filled_expected_sku_lines": expected_lines,
                    "so_designs": 0,
                    "so_sku_lines": 0,
                    "message": "Filled order expects design/colorway lines, but SO group is missing.",
                }

        filled_qty = filled.ordered_qty if filled else 0
        filled_value = filled.ordered_value if filled else 0
        so_qty = so.qty if so else 0
        so_value = so.net_value if so else 0
        ci_qty = ci.qty if ci else 0
        ci_value = ci.net_value if ci else 0

        qty_so_check = {
            "status": _status_from_bool(_close(filled_qty, so_qty)) if filled and so else ("MISSING_SO" if filled and not so else "EXTRA_SO"),
            "filled_qty": _to_int_if_whole(filled_qty),
            "so_qty": _to_int_if_whole(so_qty),
            "difference": _to_int_if_whole(so_qty - filled_qty),
        }
        value_so_check = {
            "status": _status_from_bool(_close(filled_value, so_value)) if filled and so else ("MISSING_SO" if filled and not so else "EXTRA_SO"),
            "filled_value": _to_int_if_whole(filled_value),
            "so_value": _to_int_if_whole(so_value),
            "difference": _to_int_if_whole(so_value - filled_value),
        }
        so_ci_check = {
            "status": _status_from_bool(_close(so_qty, ci_qty) and _close(so_value, ci_value)) if so and ci else ("MISSING_CI" if so and not ci else "EXTRA_CI" if ci and not so else "NOT_AVAILABLE"),
            "so_qty": _to_int_if_whole(so_qty),
            "ci_qty": _to_int_if_whole(ci_qty),
            "qty_difference": _to_int_if_whole(ci_qty - so_qty),
            "so_value": _to_int_if_whole(so_value),
            "ci_value": _to_int_if_whole(ci_value),
            "value_difference": _to_int_if_whole(ci_value - so_value),
        }

        product_reports.append({
            "group_key": key,
            "item_key": (filled.item_key if filled else so.item_key if so else ci.item_key if ci else None),
            "product": {
                "brand": filled.brand if filled else so.brand if so else ci.brand if ci else None,
                "tc": filled.tc if filled else so.tc if so else ci.tc if ci else None,
                "product_code": filled.product_code if filled else so.product_code if so else ci.product_code if ci else None,
                "product_label": filled.product_label if filled else so.product_label if so else ci.product_label if ci else None,
                "bedset_size": filled.bedset_size if filled else so.bedset_size if so else ci.bedset_size if ci else None,
                "rate": _to_int_if_whole(filled.exmill_price if filled else so.rate if so else ci.rate if ci else 0),
            },
            "master_validation": _match_master_reference(filled, master_by_group) if filled and master_groups else {"status": "NOT_CHECKED"},
            "filled_order": filled.to_dict() if filled else None,
            "sales_order_group": so.to_dict(include_lines=True) if so else None,
            "commercial_invoice_group": ci.to_dict(include_lines=True) if ci else None,
            "design_color_check": design_check,
            "quantity_check_filled_vs_so": qty_so_check,
            "value_check_filled_vs_so": value_so_check,
            "so_vs_commercial_invoice_check": so_ci_check,
        })

    summary = {
        "filled_order_qty": _to_int_if_whole(totals["filled_qty"]),
        "filled_order_value": _to_int_if_whole(totals["filled_value"]),
        "sales_order_qty": _to_int_if_whole(totals["so_qty"]),
        "sales_order_value": _to_int_if_whole(totals["so_value"]),
        "commercial_invoice_qty": _to_int_if_whole(totals["ci_qty"]),
        "commercial_invoice_value": _to_int_if_whole(totals["ci_value"]),
        "filled_vs_so_qty_difference": _to_int_if_whole(totals["so_qty"] - totals["filled_qty"]),
        "filled_vs_so_value_difference": _to_int_if_whole(totals["so_value"] - totals["filled_value"]),
        "so_vs_ci_qty_difference": _to_int_if_whole(totals["ci_qty"] - totals["so_qty"]),
        "so_vs_ci_value_difference": _to_int_if_whole(totals["ci_value"] - totals["so_value"]),
    }

    final_status = "MATCH"
    if any(p["quantity_check_filled_vs_so"]["status"] != "MATCH" or p["value_check_filled_vs_so"]["status"] != "MATCH" for p in product_reports if p["filled_order"] or p["sales_order_group"]):
        final_status = "MISMATCH"
    if any(p["so_vs_commercial_invoice_check"]["status"] not in {"MATCH", "NOT_AVAILABLE"} for p in product_reports):
        final_status = "MISMATCH"

    return {
        "success": True,
        "final_status": final_status,
        "locked_logic": {
            "master_booking_form": "Reference only: product, size, TC, rate, bale size, design/colorway count.",
            "distributor_filled_order": "Actual order: AWDs Qty is final order qty.",
            "bale_logic": "AWDs No. of Bales x Bale Size is only cross-check/warning.",
            "value_logic": "Order Value = AWDs Qty x Exmill Price.",
            "so_ci_grouping": "SO/CI SKU lines are grouped by brand + TC + product type + bedset size + rate.",
            "design_color_logic": "Filled Designs x Colorways must equal SO design/color SKU line structure.",
        },
        "summary": summary,
        "products": product_reports,
    }
