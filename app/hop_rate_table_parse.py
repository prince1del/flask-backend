"""Vendor-agnostic rate-table extraction for HoP uploads.

Prefer this over per-supplier parsers:
  1) PDF/Excel grids with Product / Size / Rate / GST headers
  2) Flattened quote lines ending in RATE + GST%
  3) Tax-invoice layouts stay in hop_rate_upload.parse_gsb_invoice_text
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _header_role(cell: str) -> str | None:
    n = re.sub(r"[^a-z0-9]+", "", (cell or "").lower())
    if not n:
        return None
    if n in {"product", "productname", "item", "itemname", "description", "descriptionofgoods", "goods"} or (
        "product" in n and "code" not in n
    ):
        return "product"
    if n == "item" or n.startswith("item"):
        return "product"
    if "quality" in n or n in {"specs", "specification"}:
        return "quality"
    if "material" in n or "fabric" in n:
        return "material"
    if n in {"size", "sizes", "dimension", "dimensions"} or n.startswith("size"):
        return "size"
    if n in {"qty", "quantity", "quantit", "case", "casepack", "pack"} or "qty" in n:
        return "qty"
    if n in {"mrp", "rsp", "listprice"}:
        return "mrp"
    if any(k in n for k in ("quotedprice", "quoted", "rate", "price", "unitrate", "selling")) and "amount" not in n:
        if n.endswith("gst") or n.startswith("gst"):
            return "gst"
        return "rate"
    if n in {"amount", "value", "total"}:
        return "amount"
    if n in {"gst", "gstpct", "gstextra", "tax"} or n.endswith("gst"):
        return "gst"
    if n in {"sl", "slno", "sno", "no", "srno"}:
        return "sl"
    return None


def parse_size_from_blob(blob: str) -> str | None:
    if not blob:
        return None
    bs = re.search(r"BS\s*-\s*(\d+)\s*[xX×]\s*(\d+)", blob, re.I)
    if bs:
        return f"{int(bs.group(1))}x{int(bs.group(2))}"
    sz = re.search(r"(\d{1,3})\s*[\"″']?\s*[xX×/]\s*(\d{1,3})\s*[\"″']?", blob)
    if sz:
        a, b = int(sz.group(1)), int(sz.group(2))
        # Ignore garbage fragments like 11x11 from broken Ambala OCR when tiny
        if a >= 12 or b >= 12 or (a >= 10 and b >= 10):
            return f"{a}x{b}"
    if re.search(r"\bfree\b|\bsize\s*[l]\b", blob, re.I):
        return "free"
    return None


def _money_from_cell(cell: str) -> float | None:
    t = (cell or "").replace(",", "").replace("₹", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _gst_from_cell(cell: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", (cell or "").strip())
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if 0 < v <= 40 else None


def _looks_like_section_banner(text: str) -> bool:
    """Package / shortlist headers inside a multi-option quote table."""
    t = (text or "").strip()
    if not t or len(t) < 4:
        return False
    return bool(
        re.search(
            r"shortlisted\s*-?\s*\d+|option\s*-?\s*\d+|package\s*-?\s*\d+|"
            r"alternative\s*-?\s*\d+|combination\s*-?\s*\d+",
            t,
            re.I,
        )
    )


def rows_from_grid(table: list[list[Any]]) -> list[dict[str, Any]]:
    """Map any Product/Item + Rate/Price (+ Size/GST) grid into rate rows."""
    if not table or len(table) < 2:
        return []

    header_idx = None
    roles: dict[int, str] = {}
    best_score = 0
    for i, row in enumerate(table[:8]):
        cells = [_cell(c) for c in (row or [])]
        joined = " ".join(cells).lower()
        if len([c for c in cells if c]) <= 1 and not re.search(r"rate|price|gst|product|item", joined):
            continue
        cand: dict[int, str] = {}
        for j, c in enumerate(cells):
            if j == 0 and re.search(r"sl\s*no.*item.*rate", c, re.I):
                cand = {
                    0: "sl",
                    1: "product",
                    2: "quality",
                    3: "size",
                    4: "qty",
                    5: "rate",
                    6: "amount",
                    7: "gst",
                }
                break
            role = _header_role(c)
            if role and role not in cand.values():
                cand[j] = role
        score = len(cand)
        if "product" in cand.values():
            score += 2
        if "rate" in cand.values():
            score += 2
        if score > best_score and ("product" in cand.values() or "rate" in cand.values()):
            best_score = score
            header_idx = i
            roles = cand

    if header_idx is None or best_score < 2:
        return []

    has_rate = "rate" in roles.values()
    out: list[dict[str, Any]] = []
    current_section = ""
    for row in table[header_idx + 1 :]:
        cells = [_cell(c) for c in (row or [])]
        if not any(cells):
            continue

        def col(role: str) -> str:
            for idx, r in roles.items():
                if r == role and idx < len(cells):
                    return cells[idx]
            return ""

        name = col("product")
        rate_probe = _money_from_cell(col("rate"))
        # Adecore-style: Shortlisted-1 / Shortlisted-3 may sit in product or first cell
        section_hit = ""
        if name and _looks_like_section_banner(name) and (rate_probe is None or rate_probe <= 0):
            section_hit = name
        elif not name or (rate_probe is None or rate_probe <= 0):
            for c in cells:
                if c and _looks_like_section_banner(c):
                    section_hit = c
                    break
        if section_hit:
            current_section = re.sub(r"\s+", " ", section_hit).strip()
            continue

        if not name or re.search(r"^total\b|^gst\b|grand\s*total", name, re.I):
            continue
        size_raw = col("size")
        size = parse_size_from_blob(size_raw) or parse_size_from_blob(name)
        quality_bits = [x for x in (col("quality"), col("material")) if x]
        if current_section:
            quality_bits = [current_section] + quality_bits
        rate = rate_probe
        if rate is None and not has_rate:
            rate = _money_from_cell(col("amount"))
        amt = _money_from_cell(col("amount"))
        # Prefer unit rate over line total when both present and rate looks like a total
        if rate is not None and amt is not None and rate > 5000 and 20 <= amt <= 5000:
            rate = amt
        if rate is None or rate <= 0 or rate > 20000:
            continue
        gst = _gst_from_cell(col("gst")) or 5.0
        # Keep package options distinct (same item name across Shortlisted-1/2/3/4)
        display_name = f"{name} — {current_section}" if current_section else name
        out.append(
            {
                "product_name": display_name,
                "size": size,
                "quality": " · ".join(quality_bits) or None,
                "rate": rate,
                "gst_pct": gst,
                "qty": _money_from_cell(col("qty")),
                "notes": size_raw or None,
            }
        )
    return out


def extract_pdf_table_rate_rows(path: Path) -> list[dict[str, Any]]:
    """Read embedded PDF tables — works for any supplier with a real table."""
    try:
        import pdfplumber
    except Exception:
        return []
    collected: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    rows = rows_from_grid(table)
                    if not rows:
                        continue
                    if len(rows) > len(collected):
                        collected = rows
                    else:
                        seen = {(r.get("product_name"), r.get("size"), r.get("rate")) for r in collected}
                        for r in rows:
                            key = (r.get("product_name"), r.get("size"), r.get("rate"))
                            if key not in seen:
                                collected.append(r)
                                seen.add(key)
    except Exception:
        return collected
    return collected


def parse_quote_price_table(text: str) -> list[dict[str, Any]]:
    """Text fallback: lines ending with optional case/mrp + RATE + GST%."""
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", (raw or "").strip())
        if not line or len(line) < 12:
            continue
        if re.search(r"^Product\b|QUOTED\s*PRICE|^Quality\b|^Material\b|^SL\s*NO\b", line, re.I):
            continue
        m = re.search(
            r"^(?P<head>.+?)\s+(?P<case>\d+)\s+(?:(?P<mrp>\d{2,5}|-)\s+)?"
            r"(?P<rate>[\d,]+\.\d{2})\s+(?P<gst>\d+(?:\.\d+)?)\s*%?\s*$",
            line,
        )
        case = None
        if m:
            head = m.group("head").strip()
            try:
                rate = float(m.group("rate").replace(",", ""))
                gst = float(m.group("gst"))
            except ValueError:
                continue
            case = float(m.group("case")) if m.group("case").isdigit() else None
        else:
            m2 = re.search(
                r"^(?P<head>.+?)\s+(?P<rate>[\d,]+\.\d{2}|\d{2,5})\s+(?P<gst>\d+(?:\.\d+)?)\s*%?\s*$",
                line,
            )
            if not m2:
                continue
            head = m2.group("head").strip()
            try:
                rate = float(m2.group("rate").replace(",", ""))
                gst = float(m2.group("gst"))
            except ValueError:
                continue
        if rate <= 0 or rate > 20000 or not re.search(r"[A-Za-z]{3,}", head):
            continue
        size = parse_size_from_blob(head)
        quality_bits: list[str] = []
        for pat in (
            r"\d{2,4}\s*TC\s*(?:Stripe|Percale|Plain)?",
            r"\d+(?:\.\d+)?\s*gms?(?:/\d+\s*gsm)?",
            r"\d{2,4}\s*GSM",
            r"\d+\s*Grams?(?:\s+\d+\s*GSM)?",
            r"100%\s*Cotton",
            r"Poly Cotton(?:\s*\([^)]+\))?",
            r"Cotton Rich",
            r"Polyester",
        ):
            hit = re.search(pat, head, re.I)
            if hit:
                quality_bits.append(re.sub(r"\s+", " ", hit.group(0)).strip())
        name = re.split(r"\s+BS\s*-", head, maxsplit=1, flags=re.I)[0].strip()
        rows.append(
            {
                "product_name": name or head,
                "size": size,
                "quality": " · ".join(dict.fromkeys(quality_bits)) or None,
                "rate": rate,
                "gst_pct": gst if gst > 0 else 5.0,
                "qty": case,
                "notes": head,
            }
        )
    return rows
