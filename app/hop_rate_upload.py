"""HoP rate-sheet file ingest — PDF, Excel, Word/RTF, images, text/CSV.

Workspace-scoped. Does not touch NEXORA / Bombay Dyeing flows.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.hop_rate_compare import lines_from_structured
from app.hop_rate_table_parse import extract_pdf_table_rate_rows, parse_quote_price_table

# Accept broadly; unknown binaries are still stored with manual-review note.
ALLOWED_EXTENSIONS = {
    # documents
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",  # WordPad default
    ".odt",
    ".txt",
    ".csv",
    ".tsv",
    # spreadsheets
    ".xlsx",
    ".xlsm",
    ".xls",
    ".ods",
    # images / media
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".ods"}
WORD_EXTENSIONS = {".doc", ".docx", ".rtf", ".odt"}


def allowed_rate_upload(filename: str) -> bool:
    ext = Path(filename or "").suffix.lower()
    return bool(ext) and ext in ALLOWED_EXTENSIONS


def accept_attr() -> str:
    """HTML accept= value for the file picker."""
    return ",".join(sorted(ALLOWED_EXTENSIONS))


def _s(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        text = "\n".join(chunks).strip()
        if text:
            return text
    except Exception:
        pass
    try:
        from pdfminer.high_level import extract_text

        return (extract_text(str(path)) or "").strip()
    except Exception:
        return ""


def _extract_azure_text(path: Path) -> str:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if not endpoint or not key:
        return ""
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except Exception:
        return ""
    try:
        client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))
        with path.open("rb") as payload:
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                analyze_request=payload,
                content_type="application/octet-stream",
            )
        result = poller.result()
        content_text = getattr(result, "content", "")
        if isinstance(content_text, str) and content_text.strip():
            return content_text.strip()
        lines: list[str] = []
        for page in getattr(result, "pages", []) or []:
            for line in getattr(page, "lines", []) or []:
                content = getattr(line, "content", "")
                if content:
                    lines.append(content)
        return "\n".join(lines).strip()
    except Exception:
        return ""


_RAPID_OCR_ENGINE = None


def _get_rapid_ocr():
    """Lazy-load RapidOCR once (local ONNX models, no cloud key required)."""
    global _RAPID_OCR_ENGINE
    if _RAPID_OCR_ENGINE is False:
        return None
    if _RAPID_OCR_ENGINE is not None:
        return _RAPID_OCR_ENGINE
    try:
        from rapidocr import RapidOCR

        _RAPID_OCR_ENGINE = RapidOCR()
        return _RAPID_OCR_ENGINE
    except Exception:
        _RAPID_OCR_ENGINE = False
        return None


def _pil_open_image(path: Path):
    from PIL import Image, ImageOps

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Upscale small phone photos for better OCR
    w, h = img.size
    if max(w, h) < 1200:
        scale = 1200 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return img


def _extract_rapidocr_text(path: Path) -> str:
    engine = _get_rapid_ocr()
    if engine is None:
        return ""
    try:
        # Preprocess for handwritten / phone photos
        work = path
        tmp_path: Path | None = None
        try:
            img = _pil_open_image(path)
            from PIL import ImageOps, ImageEnhance, ImageFilter

            gray = ImageOps.grayscale(img)
            gray = ImageOps.autocontrast(gray, cutoff=2)
            gray = ImageEnhance.Sharpness(gray).enhance(1.4)
            gray = gray.filter(ImageFilter.MedianFilter(size=3))
            import tempfile

            fd, tmp_name = tempfile.mkstemp(suffix=".png")
            import os as _os

            _os.close(fd)
            tmp_path = Path(tmp_name)
            gray.convert("RGB").save(tmp_path, format="PNG")
            work = tmp_path
        except Exception:
            work = path

        result = engine(str(work))
        texts: list[str] = []
        if result is None:
            return ""
        if hasattr(result, "txts") and result.txts:
            texts = [str(t).strip() for t in result.txts if str(t).strip()]
        elif isinstance(result, (list, tuple)):
            if result and isinstance(result[0], (list, tuple)) and len(result) >= 2 and isinstance(result[1], (list, tuple)):
                texts = [str(t).strip() for t in result[1] if str(t).strip()]
            else:
                for item in result:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        texts.append(str(item[1]).strip())
                    elif isinstance(item, str) and item.strip():
                        texts.append(item.strip())
        return "\n".join(t for t in texts if t)
    except Exception:
        return ""
    finally:
        try:
            if "tmp_path" in locals() and tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _extract_tesseract_text(path: Path) -> str:
    try:
        import pytesseract
    except Exception:
        return ""
    cmd = os.getenv("TESSERACT_CMD", "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        img = _pil_open_image(path)
        # Prefer digits+English for rate sheets; fallback to default
        config = "--psm 6"
        text = pytesseract.image_to_string(img, lang="eng", config=config) or ""
        return text.strip()
    except Exception:
        return ""


def extract_image_ocr_text(path: Path) -> tuple[str, str]:
    """OCR chain for rate-sheet photos — multi-engine handwriting stack.

    Prefer app.hop_handwriting_ocr.run_handwriting_ocr for structured lines.
    Returns (text, method) for legacy callers.
    """
    try:
        from app.hop_handwriting_ocr import run_handwriting_ocr

        result = run_handwriting_ocr(path)
        text = result.get("text") or ""
        method = result.get("method") or "image_ocr_empty"
        # Stash structured lines for parse_rate_upload_file
        extract_image_ocr_text.last_structured = result  # type: ignore[attr-defined]
        return text, method
    except Exception:
        extract_image_ocr_text.last_structured = None  # type: ignore[attr-defined]
    # Legacy fallback chain
    text = _extract_azure_text(path)
    if text:
        return text, "image_azure"
    text = _extract_rapidocr_text(path)
    if text:
        return text, "image_rapidocr"
    text = _extract_tesseract_text(path)
    if text:
        return text, "image_tesseract"
    return "", "image_ocr_empty"


extract_image_ocr_text.last_structured = None  # type: ignore[attr-defined]


def _extract_docx_text(path: Path) -> str:
    """Read .docx without python-docx (zip + word/document.xml)."""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        parts: list[str] = []
        for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
            if node.text:
                parts.append(node.text)
            # paragraph breaks
        # Better: join by paragraphs
        paras: list[str] = []
        for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [t.text or "" for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
            line = "".join(texts).strip()
            if line:
                paras.append(line)
        return "\n".join(paras).strip() or " ".join(parts).strip()
    except Exception:
        return ""


def _strip_rtf(raw: str) -> str:
    # Remove RTF groups / control words enough for WordPad rate lists
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_rtf_text(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_bytes().decode("latin-1", errors="ignore")
    return _strip_rtf(raw)


def _extract_excel_rows(path: Path) -> list[dict[str, Any]]:
    ext = path.suffix.lower()
    rows_out: list[dict[str, Any]] = []

    if ext == ".xls":
        # Prefer pandas if engine available; otherwise empty → manual review
        try:
            import pandas as pd

            frames = pd.read_excel(path, sheet_name=None, dtype=str)
            for _name, df in (frames or {}).items():
                rows_out.extend(_dataframe_to_rate_rows(df))
            return rows_out
        except Exception:
            return []

    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for ws in wb.worksheets:
            grid = [[cell for cell in row] for row in ws.iter_rows(values_only=True)]
            rows_out.extend(_grid_to_rate_rows(grid))
        return rows_out
    except Exception:
        try:
            import pandas as pd

            frames = pd.read_excel(path, sheet_name=None, dtype=str)
            for _name, df in (frames or {}).items():
                rows_out.extend(_dataframe_to_rate_rows(df))
        except Exception:
            return []
    return rows_out


def _dataframe_to_rate_rows(df) -> list[dict[str, Any]]:
    try:
        grid = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
    except Exception:
        return []
    return _grid_to_rate_rows(grid)


def _norm_header(h: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(h or "").lower())


def _grid_to_rate_rows(grid: list[list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return []
    # Find header row with product + rate-like columns
    header_idx = None
    mapping: dict[str, int] = {}
    for i, row in enumerate(grid[:25]):
        norms = [_norm_header(c) for c in row]
        colmap: dict[str, int] = {}
        for j, n in enumerate(norms):
            if not n:
                continue
            if n in {"description", "desc"} or (n.endswith("description") and "product" not in n):
                colmap.setdefault("description", j)
            elif n in {"product", "productname", "item", "descriptionofgoods", "goods"} or "product" in n or n == "item" or n.endswith("item"):
                colmap.setdefault("product", j)
            elif n in {"size", "dimension", "dimensions"} or n.startswith("size"):
                colmap.setdefault("size", j)
            elif n in {"brand"}:
                colmap.setdefault("brand", j)
            elif n in {"quality", "gsm", "tc", "specs", "specification"}:
                colmap.setdefault("quality", j)
            elif n in {"programme", "program", "programe", "range", "collection"}:
                colmap.setdefault("programme", j)
            elif n in {"category"}:
                colmap.setdefault("category", j)
            elif (
                n in {"rate", "rateperqty", "quotedprice", "price", "unitrate", "mrp", "directcustomerprice"}
                or "rate" in n
                or n.endswith("price")
                or "customerprice" in n
            ):
                # Prefer quoted/rate over mrp
                if "mrp" in n and "rate" not in colmap:
                    colmap.setdefault("mrp", j)
                else:
                    colmap["rate"] = j
            elif n in {"gst", "gstrate", "gstpct", "tax"} or n.startswith("gst"):
                colmap.setdefault("gst", j)
            elif n in {"qty", "quantity", "qnty"}:
                colmap.setdefault("qty", j)
        if "product" in colmap and ("rate" in colmap or "mrp" in colmap):
            header_idx = i
            mapping = colmap
            if "rate" not in mapping and "mrp" in mapping:
                mapping["rate"] = mapping["mrp"]
            break

    out: list[dict[str, Any]] = []
    if header_idx is None:
        # Fallback: scan rows for trailing money tokens
        for row in grid:
            cells = [str(c).strip() if c is not None else "" for c in row]
            if not any(cells):
                continue
            joined = " | ".join(c for c in cells if c)
            parsed = _parse_loose_line(joined)
            if parsed:
                out.append(parsed)
        return out

    for row in grid[header_idx + 1 :]:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue

        def cell(key: str) -> str:
            idx = mapping.get(key)
            if idx is None or idx >= len(row):
                return ""
            return "" if row[idx] is None else str(row[idx]).strip()

        product = cell("product")
        rate_raw = cell("rate")
        if not product or not rate_raw:
            continue
        rate_m = re.search(r"(\d+(?:[.,]\d+)?)", rate_raw.replace(",", ""))
        if not rate_m:
            continue
        rate_val = float(rate_m.group(1).replace(",", ""))
        # Excel formula leftovers like 515.789473… → 2dp
        if abs(rate_val - round(rate_val, 2)) > 1e-9:
            rate_val = round(rate_val, 2)
        gst_raw = cell("gst")
        gst = 5.0
        if gst_raw:
            gm = re.search(r"(\d+(?:\.\d+)?)", gst_raw)
            if gm:
                gst = float(gm.group(1))
        qty_raw = cell("qty")
        qty = None
        if qty_raw:
            qm = re.search(r"(\d+(?:\.\d+)?)", qty_raw.replace(",", ""))
            if qm:
                qty = float(qm.group(1))
        desc = cell("description")
        programme = cell("programme")
        category = cell("category")
        # Programme / description carry Pool / Premium / WEL for match keys
        quality_bits = [cell("quality"), programme, category, desc]
        quality = " | ".join(b for b in quality_bits if b) or None
        brand = cell("brand") or None
        if not brand and re.search(r"\bwel\b|welspun", f"{desc} {programme}", re.I):
            brand = "Welspun"
        out.append(
            {
                "product_name": product,
                "size": cell("size") or None,
                "brand": brand,
                "quality": quality,
                "rate": rate_val,
                "gst_pct": gst,
                "qty": qty,
            }
        )
    return out


def _is_junk_rate_line(parsed: dict[str, Any]) -> bool:
    """Drop invoice headers / address / HSN blobs that look like rates."""
    name = str(parsed.get("product_name") or "")
    rate = float(parsed.get("rate") or 0)
    gst = float(parsed.get("gst_pct") or 0)
    if rate <= 0 or rate > 20000:
        return True
    if gst > 40:
        return True
    if re.search(
        r"ack\s*no|state\s*name|gstin|invoice|dist:|uttar\s*pradesh|west\s*bengal|"
        r"ghaziabad|code\s*:|page\s*\d|taxable|grand\s*total|bank\s*detail|"
        r"\bhsn\b|igst\s*output|cgst\s*output|sgst\s*output|round\s*off|"
        r"amount\s*chargeable|computer\s*generated",
        name,
        re.I,
    ):
        return True
    # Pure HSN / numeric code lines (not product titles that merely mention a code)
    if re.fullmatch(r"\d{6,10}", name.strip()):
        return True
    if re.search(r"\b\d{6}\b", name) and not re.search(r"[a-zA-Z]{3,}", name):
        return True
    return False


def _clean_gsb_product_name(name: str) -> str:
    """Strip HSN / qty / rate columns that pdfplumber may glue onto the title."""
    name = re.split(r"\s+\d{6,10}\b", name or "", maxsplit=1)[0]
    name = re.sub(r"\s+\d+(?:\.\d+)?\s*%.*$", "", name)
    name = re.sub(r"\s+\d+\.\d{2}\s*Pcs.*$", "", name, flags=re.I)
    name = re.sub(r"\s+1\.00\s*Pcs.*$", "", name, flags=re.I)
    name = re.sub(r"\s*<\s*\d+\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" -–—")


def parse_gsb_invoice_text(text: str) -> list[dict[str, Any]]:
    """Parse GSB ENTERPRISE tax-invoice layout: numbered Description of Goods + Size/Weight + Rate.

    PDF text is column-jumbled, so we collect item headers + size lines by Sl.No., then
    zip with Amount rates found as ``1.00 Pcs`` / ``RATE`` / ``Pcs`` blocks (first pass only).
    """
    if not text or not re.search(r"GSB\s*ENTERPRISE|Description of Goods", text, re.I):
        return []

    # Stop before tax summary so IGST / totals are not treated as products
    cut = re.search(
        r"(?im)^(?:IGST\s*Output|Taxable\s*Value|Amount Chargeable|Tax Amount\s*\(in words\))",
        text,
    )
    body = text[: cut.start()] if cut else text

    item_re = re.compile(
        r"(?m)^(\d{1,2})\s+((?!Size\b|HSN|GST|Quantity|Amount|Taxable|Total|IGST|Round|"
        r"Description|Disc\.?|per\b)[A-Za-z<][^\n]{2,120})"
    )
    matches = list(item_re.finditer(body))
    if len(matches) < 3:
        return []

    by_sl: dict[int, dict[str, Any]] = {}
    for i, m in enumerate(matches):
        sl = int(m.group(1))
        raw_title = m.group(2)
        name = _clean_gsb_product_name(raw_title)
        if not name or re.search(r"igst|round\s*off|taxable", name, re.I):
            continue
        # Same-line rate (pdfplumber often keeps Amount on the item row)
        inline_rate = None
        rm = re.search(r"1\.00\s*Pcs\s+([\d,]+\.\d{2})", raw_title, flags=re.I)
        if not rm:
            rm = re.search(r"([\d,]+\.\d{2})\s+Pcs\s+\1", raw_title, flags=re.I)
        if rm:
            try:
                inline_rate = float(rm.group(1).replace(",", ""))
            except ValueError:
                inline_rate = None
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[m.end() : end]
        size = None
        quality_bits: list[str] = []
        sm = re.search(r"Size\s*[-–—:]\s*([^\n]+)", chunk, re.I)
        if sm:
            size_blob = sm.group(1).strip()
            size_m = re.search(
                r"(\d{1,3}\s*[\"″]?\s*[x×]\s*\d{1,3}\s*[\"″]?|\b[LSMXlsmx]{1,3}\d*[\"″]?)",
                size_blob,
            )
            if size_m:
                size = size_m.group(1)
            w = re.search(r"Weight\s*[-–—:]\s*([^,\n]+)", size_blob, re.I) or re.search(
                r"Weight\s*[-–—:]\s*([^,\n]+)", chunk, re.I
            )
            if w:
                quality_bits.append(w.group(1).strip())
            tc = re.search(r"(\d{2,4}\s*TC[^,\n]*)", size_blob, re.I)
            if tc:
                quality_bits.append(tc.group(1).strip())
            rest = re.sub(r"^[^,]*,\s*", "", size_blob).strip()
            if rest and not re.search(r"weight", rest, re.I) and rest not in quality_bits:
                if re.search(r"tc|plain|stripe|terry|velour|waffle|grams|gsm|kgs?", rest, re.I):
                    quality_bits.append(rest)
        qm = re.search(r"Quality\s*[-–—:]\s*([^\n,]+)", chunk, re.I)
        if qm:
            quality_bits.append(qm.group(1).strip())
        if not size:
            emb = re.search(
                r"(\d{1,3}(?:\.\d+)?\s*[x×]\s*\d{1,3}(?:\.\d+)?)\s*(?:cms?|cm|mm)?",
                name,
                re.I,
            )
            if emb:
                size = emb.group(1)
        by_sl[sl] = {
            "sl": sl,
            "product_name": name,
            "size": size,
            "quality": " · ".join(dict.fromkeys(quality_bits)) or None,
            "gst_pct": 5.0,
            "inline_rate": inline_rate,
        }

    ordered = [by_sl[k] for k in sorted(by_sl)]
    if len(ordered) < 3:
        return []

    rate_hits = re.findall(
        r"1\.00\s*Pcs\s+([\d,]+\.\d{2})\s+Pcs",
        body,
        flags=re.I,
    )
    rates: list[float] = []
    for tok in rate_hits:
        try:
            rates.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    if len(rates) >= len(ordered):
        rates = rates[: len(ordered)]
    elif len(rates) < len(ordered):
        loose = [
            float(x.replace(",", ""))
            for x in re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)", body)
            if float(x.replace(",", "")) >= 20
        ]
        dedup: list[float] = []
        for v in loose:
            if dedup and abs(dedup[-1] - v) < 0.001:
                continue
            dedup.append(v)
        rates = dedup[: len(ordered)]

    rows: list[dict[str, Any]] = []
    for i, item in enumerate(ordered):
        rate = item.get("inline_rate")
        if rate is None and i < len(rates):
            rate = rates[i]
        if rate is None:
            continue
        rows.append(
            {
                "product_name": item["product_name"],
                "size": item.get("size"),
                "quality": item.get("quality"),
                "rate": rate,
                "gst_pct": item.get("gst_pct") or 5.0,
                "notes": item["product_name"],
            }
        )
    return rows


def _parse_loose_line(line: str) -> dict[str, Any] | None:
    line = re.sub(r"\s+", " ", (line or "").strip())
    if not line or len(line) < 4:
        return None
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            rate_m = re.search(r"(\d+(?:\.\d+)?)", parts[2].replace(",", ""))
            if rate_m and parts[0]:
                gst = 5.0
                if len(parts) >= 4:
                    gm = re.search(r"(\d+(?:\.\d+)?)", parts[3])
                    if gm:
                        gst = float(gm.group(1))
                row = {
                    "product_name": parts[0],
                    "size": parts[1] or None,
                    "rate": float(rate_m.group(1)),
                    "gst_pct": gst,
                }
                return None if _is_junk_rate_line(row) else row
    inv = re.match(
        r"^\d+\s+(.+?)\s+\d{6,8}\s+(\d+(?:\.\d+)?)\s*%?\s+[\d.]+\s*Pcs\s+(\d+(?:\.\d+)?)\s*Pcs",
        line,
        flags=re.I,
    )
    if inv:
        row = {
            "product_name": inv.group(1).strip(),
            "size": None,
            "rate": float(inv.group(3)),
            "gst_pct": float(inv.group(2)),
        }
        return None if _is_junk_rate_line(row) else row
    m = re.match(
        r"^(.+?)\s+(\d{1,3}\s*[x×]\s*\d{1,3}|[Ll]|Free\s*Size)?\s*[-–—:]?\s*"
        r"(\d{2,6}(?:\.\d+)?)\s*(?:\+?\s*(\d+(?:\.\d+)?)\s*%?)?\s*$",
        line,
        flags=re.I,
    )
    if m and m.group(1) and m.group(3):
        name = m.group(1).strip(" -–—:")
        if re.search(r"total|gstin|invoice|page\s*\d|sl\.?\s*no", name, re.I):
            return None
        row = {
            "product_name": name,
            "size": (m.group(2) or "").strip() or None,
            "rate": float(m.group(3)),
            "gst_pct": float(m.group(4)) if m.group(4) else 5.0,
        }
        return None if _is_junk_rate_line(row) else row
    m2 = re.search(
        r"^(.{3,80}?)\s+(\d{1,3}\s*[x×]\s*\d{1,3})?\s+(\d{2,6}(?:\.\d+)?)\s*(?:\+?\s*(\d+(?:\.\d+)?)\s*%?)?\s*$",
        line,
        flags=re.I,
    )
    if m2:
        name = m2.group(1).strip()
        if len(name.split()) >= 1 and not re.search(r"^(sl|sno|total|amount)\b", name, re.I):
            row = {
                "product_name": name,
                "size": (m2.group(2) or "").strip() or None,
                "rate": float(m2.group(3)),
                "gst_pct": float(m2.group(4)) if m2.group(4) else 5.0,
            }
            return None if _is_junk_rate_line(row) else row
    return None


def parse_handwritten_ocr_text(text: str) -> list[dict[str, Any]]:
    """Parse messy OCR from handwritten rate slips.

    Strategy: collect sizes, rate+GST, and product names in reading order, then zip.
    Uses only OCR text — never injects demo rates.
    """
    if not (text or "").strip():
        return []
    raw = (
        text.replace("×", "x")
        .replace("—", "-")
        .replace("–", "-")
        .replace("。", "%")
        .replace("．", ".")
    )
    raw = re.sub(r"(\d)\s*[xX]\s*(\d)", r"\1x\2", raw)
    # Drop obvious serial labels noise but keep the rest of the line
    raw = re.sub(r"S\.?L\.?\s*\.??\s*NO-?\d*-?", " ", raw, flags=re.I)

    size_re = re.compile(r"(\d{1,3}x\d{1,3}|Free\s*Size|FreeSize)", re.I)
    rate_gst_re = re.compile(r"(?<!\d)(\d{2,5})\s*\+\s*(\d{1,2})\s*%?", re.I)

    name_fix = {
        r"d/?cower|d/?cover|duvet\s*cover": "D/Cover",
        r"bed\s*sheer|bed\s*sheet|bedsheet": "Bed Sheet",
        r"p/?coner|p/?cower|p/?cover|y/?cower|pillow": "P/Cover",
        r"\bduur\b|\bdour\b|\bduvet\b": "Duvet",
        r"bath\s*mat|bathmat": "Bath Mat",
        r"luxu?ry?\s*bath|duxuy\s*bath|bath\s*towel": "Luxury Bath",
        r"hand\s*towel|hard\s*towel|hand\s*towl": "Hand towel",
        r"bath\s*robe|balkoose|bathrobe": "Bathrobe",
    }

    def fix_name(s: str) -> str:
        low = s.lower()
        for pat, nice in name_fix.items():
            if re.search(pat, low):
                return nice
        return re.sub(r"\s+", " ", s).strip()

    def is_name_line(s: str) -> bool:
        s = s.strip()
        if len(s) < 3:
            return False
        if re.search(r"sl\.?\s*no|^[0-9]+\)?$|^\d+x\d+$", s, re.I):
            return False
        if size_re.fullmatch(s) or rate_gst_re.fullmatch(s):
            return False
        letters = sum(1 for c in s if c.isalpha())
        return letters >= 3

    sizes: list[str] = []
    rates: list[tuple[float, float]] = []
    names: list[str] = []

    for ln in raw.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        # sizes on this line
        for sm in size_re.finditer(ln):
            sz = sm.group(1).replace(" ", "")
            if sz.lower() == "freesize":
                sz = "Free Size"
            # skip tiny OCR garbage like 10x
            if re.match(r"^\d+x\d+$", sz, re.I):
                a, b = sz.lower().split("x")
                # Drop OCR crumbs like 10x / 10x12 (real hotel sizes are usually >= 16 on one side)
                if max(int(a), int(b)) < 16:
                    continue
            sizes.append(sz)
        # rates on this line — skip absurd pairs like 681+666
        for rm in rate_gst_re.finditer(ln):
            rate = float(rm.group(1))
            gst = float(rm.group(2))
            if rate < 20 or rate > 8000:
                continue
            if gst > 40:
                # OCR often reads 5% as 55/57/59
                gst = 5.0 if gst >= 50 else 18.0
            rates.append((rate, gst))
        if is_name_line(ln) and not size_re.search(ln) and not rate_gst_re.search(ln):
            cleaned = re.sub(r"^\d+\)\s*", "", ln).strip(" -")
            if len(cleaned) >= 3:
                names.append(fix_name(cleaned))

    n = min(len(sizes), len(rates))
    if n == 0:
        return []
    rows: list[dict[str, Any]] = []
    for i in range(n):
        rate, gst = rates[i]
        name = names[i] if i < len(names) else f"Item {i + 1}"
        rows.append(
            {
                "product_name": name,
                "size": sizes[i],
                "rate": rate,
                "gst_pct": gst,
            }
        )
    # Extra names with rates but missing size (rare)
    if len(rates) > len(sizes) and len(names) > len(sizes):
        for i in range(n, min(len(rates), len(names))):
            rate, gst = rates[i]
            rows.append(
                {
                    "product_name": names[i],
                    "size": None,
                    "rate": rate,
                    "gst_pct": gst,
                }
            )
    return rows


def parse_rate_lines_from_text(text: str) -> list[dict[str, Any]]:
    # GSB tax invoices need a dedicated layout parser (22+ Description of Goods rows)
    gsb = parse_gsb_invoice_text(text)
    if len(gsb) >= 5:
        return gsb

    # Generic quotation lines (any supplier)
    quote_rows = parse_quote_price_table(text)
    if len(quote_rows) >= 5:
        return quote_rows

    rows: list[dict[str, Any]] = []
    for raw in (text or "").splitlines():
        parsed = _parse_loose_line(raw.strip())
        if parsed and not re.search(r"sl\.?\s*no", str(parsed.get("product_name") or ""), re.I):
            rows.append(parsed)
    if len(rows) < 2:
        for chunk in re.split(r"[\n\r]+|(?=\d+\))", text or ""):
            parsed = _parse_loose_line(chunk.strip())
            if parsed and not re.search(r"sl\.?\s*no", str(parsed.get("product_name") or ""), re.I):
                rows.append(parsed)
    hw = parse_handwritten_ocr_text(text)
    # Prefer handwritten slip parse when it finds a clearer product list
    if len(hw) >= max(3, len(rows)):
        rows = hw
    elif hw and len(rows) < 3:
        rows = hw
    if len(quote_rows) > len(rows):
        return quote_rows
    seen: set[tuple] = set()
    uniq: list[dict[str, Any]] = []
    for r in rows:
        key = (r.get("product_name"), r.get("size"), r.get("rate"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def extract_text_from_file(path: Path) -> tuple[str, str]:
    """Return (text, method_note)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        text = _extract_pdf_text(path)
        if text:
            return text, "pdf_text"
        # Scanned PDF: try Azure then local OCR on rendered pages is heavy — Azure only here
        text = _extract_azure_text(path)
        if text:
            return text, "pdf_azure"
        return "", "pdf_empty"
    if ext in {".xlsx", ".xlsm", ".xls", ".ods"}:
        return "", "excel_rows"  # handled separately
    if ext == ".docx":
        text = _extract_docx_text(path)
        return text, "docx"
    if ext == ".rtf":
        return _extract_rtf_text(path), "rtf"
    if ext in {".txt", ".csv", ".tsv"}:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        return raw, ext.lstrip(".")
    if ext in {".doc", ".odt"}:
        text = _extract_azure_text(path)
        if text:
            return text, "legacy_word_azure"
        return "", "legacy_word_manual"
    if ext in IMAGE_EXTENSIONS:
        return extract_image_ocr_text(path)
    # any other allowed media
    text = _extract_azure_text(path)
    if text:
        return text, "media_azure"
    return extract_image_ocr_text(path)


def _lines_look_like_invoice_junk(rows: list[dict[str, Any]]) -> bool:
    """True only when rows look like addresses / tax headers — not real product rates.

    Important: do NOT treat missing size alone as junk (Jalandhar embeds size in the
    product text). Do NOT match bare ``ack`` (false-positive on ``Pack of 6``).
    """
    if not rows:
        return True
    productish = 0
    junkish = 0
    for r in rows:
        name = str(r.get("product_name") or "")
        rate = float(r.get("rate") or 0)
        if re.search(
            r"bedsheet|duvet|pillow|bath\s*towel|hand\s*towel|face\s*towel|"
            r"bath\s*mat|towelling|bathrobe|robe|pool\s*towel|\btowel\b",
            name,
            re.I,
        ):
            productish += 1
        if rate > 5000 or _is_junk_rate_line(r):
            junkish += 1
        elif not r.get("size") and re.search(
            r"state\s*name|dist:|ack\s*no|ack\s*date|gstin|code\s*:",
            name,
            re.I,
        ):
            junkish += 1
    # Clear rate lists (Jalandhar / Ambala / etc.) must never be discarded
    if productish >= max(3, len(rows) // 2):
        return False
    return junkish >= max(2, (len(rows) * 2) // 3)


def parse_jalandhar_rate_table(text: str) -> list[dict[str, Any]]:
    """Backward-compatible alias — uses generic quote parser."""
    return parse_quote_price_table(text)


def parse_rate_upload_file(path: Path, supplier_hint: str | None = None) -> dict[str, Any]:
    """Parse an uploaded supplier rate file into structured lines."""
    ext = path.suffix.lower()
    if not allowed_rate_upload(path.name):
        raise ValueError(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    warnings: list[str] = []
    raw_rows: list[dict[str, Any]] = []
    method = ""
    text = ""

    if ext in EXCEL_EXTENSIONS or ext in {".csv", ".tsv"}:
        if ext in {".csv", ".tsv"}:
            try:
                with path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
                    dialect = csv.excel_tab if ext == ".tsv" else csv.excel
                    reader = csv.reader(fh, dialect)
                    grid = [list(r) for r in reader]
                raw_rows = _grid_to_rate_rows(grid)
                method = "csv"
            except Exception as exc:
                warnings.append(f"CSV read failed: {exc}")
        else:
            raw_rows = _extract_excel_rows(path)
            method = "excel"
            if not raw_rows:
                warnings.append("Could not detect product/rate columns in spreadsheet — check header names.")
    else:
        # PDFs: vendor-agnostic table extract first (Product/Size/Rate/GST columns)
        if ext == ".pdf":
            table_rows = extract_pdf_table_rate_rows(path)
            if len(table_rows) >= 3:
                raw_rows = table_rows
                method = "pdf_table"

        if not raw_rows:
            text, method = extract_text_from_file(path)
            structured = getattr(extract_image_ocr_text, "last_structured", None)
            if ext in IMAGE_EXTENSIONS and isinstance(structured, dict) and structured.get("lines"):
                raw_rows = list(structured["lines"])
                method = structured.get("method") or method
                scores = structured.get("engine_scores") or {}
                tried = structured.get("engines_tried") or []
                if scores:
                    warnings.append(
                        "OCR engines: "
                        + ", ".join(f"{k}={v}" for k, v in scores.items())
                        + (f" · tried {len(tried)}" if tried else "")
                    )
            elif text:
                raw_rows = [r for r in parse_rate_lines_from_text(text) if not _is_junk_rate_line(r)]

        if not raw_rows and str(method).startswith("image") and "empty" in str(method):
            warnings.append(
                "OCR could not read rates from this image. "
                "Try a clearer photo, or paste lines manually (Product | Size | Rate | GST)."
            )
        elif not raw_rows and str(method).endswith("manual"):
            warnings.append(
                "Could not auto-read this file. Paste rate lines manually, then Save pasted lines."
            )
        elif not raw_rows:
            warnings.append(
                "No clean product/rate lines matched. "
                "Prefer Excel, or a PDF with a clear Product / Size / Rate / GST table."
            )

    # Never inject demo/sample rates — only what was read from this file.
    if raw_rows and _lines_look_like_invoice_junk(raw_rows):
        warnings.append(
            "Parsed text looked like invoice headers/addresses, not product rates — discarded. "
            "Paste clean lines (Product | Size | Rate | GST) or upload Excel."
        )
        raw_rows = []

    normalized = lines_from_structured(raw_rows) if raw_rows else []
    source_type = "upload"
    if ext == ".pdf":
        source_type = "pdf"
    elif ext in IMAGE_EXTENSIONS:
        source_type = "image"
    elif ext in EXCEL_EXTENSIONS or ext in {".csv", ".tsv"}:
        source_type = "excel"
    elif ext in WORD_EXTENSIONS:
        source_type = "word"

    return {
        "lines": [
            {
                "product_name": ln["product_name"],
                "size": ln.get("size"),
                "brand": ln.get("brand"),
                "quality": ln.get("quality"),
                "rate": ln["rate"],
                "gst_pct": ln.get("gst_pct"),
                "qty": ln.get("qty"),
                "notes": ln.get("notes"),
            }
            for ln in normalized
        ],
        "line_count": len(normalized),
        "source_type": source_type,
        "parse_method": method,
        "warnings": warnings,
        "extracted_preview": (text[:1200] if text else None),
    }
