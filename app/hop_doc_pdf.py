"""PDF export for HoP document preview (quotation / proforma / invoice)."""
from __future__ import annotations

import base64
import io
import re
from collections import OrderedDict
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Match web preview (nexora-theme.css / hop_commercial_web.js)
C_BLUE = (29, 78, 216)
C_INK = (15, 23, 42)
C_MUTED = (71, 85, 105)
C_BORDER = (148, 163, 184)
C_SECTION_BG = (241, 245, 249)
C_TOTAL_ROW_BG = (248, 250, 252)
C_WHITE = (255, 255, 255)

FONT_BODY = 7.5
FONT_HEADER = 8.0
FONT_ITEM_NAME = 7.5
FONT_ITEM_DESC = 7.0


def _t(text: Any) -> str:
    s = str(text or "").strip()
    s = s.replace("₹", "Rs.")
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2026", "...").replace("…", "...")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    return s.encode("latin-1", "replace").decode("latin-1")


def _money(n: Any, *, compact: bool = False) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    if compact:
        return f"{v:,.2f}"
    return f"Rs. {v:,.2f}"


def _is_commercial(preview: dict[str, Any]) -> bool:
    header = preview.get("header") or {}
    txn_type = int(header.get("txn_type") or 0)
    lines = preview.get("lines") or []
    if txn_type != 27:
        return False
    if any(_t(ln.get("section_title")) for ln in lines):
        return True
    label = _t(header.get("doc_title")).lower()
    return "commercial" in label


def _group_sections(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for ln in lines or []:
        title = _t(ln.get("section_title")) or "Items"
        sections.setdefault(title, []).append(ln)
    return [{"title": k, "lines": v} for k, v in sections.items()]


def _safe_filename(preview: dict[str, Any]) -> str:
    header = preview.get("header") or {}
    raw = _t(header.get("doc_number") or header.get("doc_title") or "document")
    raw = re.sub(r'[/\\:*?"<>|]+', "_", raw)
    raw = re.sub(r"\s+", "_", raw)
    return (raw[:96] or "document").strip("_")


def _scale_cols(pdf: FPDF, weights: tuple[float, ...]) -> tuple[float, ...]:
    total = sum(weights) or 1.0
    epw = float(pdf.epw)
    return tuple(epw * w / total for w in weights)


def _decode_image_bytes(url: Any) -> bytes | None:
    raw = str(url or "").strip()
    if not raw.startswith("data:image"):
        return None
    try:
        b64 = raw.split("base64,", 1)[-1]
        return base64.b64decode(b64)
    except (ValueError, TypeError):
        return None


def _wrap_lines(pdf: FPDF, text: str, width_mm: float, *, font_style: str = "", font_size: float = 6) -> list[str]:
    txt = _t(text)
    if not txt:
        return []
    pdf.set_font("Helvetica", font_style, font_size)
    usable = max(8.0, width_mm - 2.0)
    words = txt.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if pdf.get_string_width(candidate) <= usable:
            current = candidate
            continue
        if current:
            lines.append(current)
        if pdf.get_string_width(word) <= usable:
            current = word
        else:
            chunk = ""
            for ch in word:
                test = chunk + ch
                if pdf.get_string_width(test) <= usable:
                    chunk = test
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = ch
            current = chunk
    if current:
        lines.append(current)
    return lines or [txt[:40]]


def _item_desc_text(description: Any) -> str:
    desc = _t(description)
    if not desc:
        return ""
    if not desc.startswith("("):
        desc = f"({desc})"
    return desc


def _row_height_for_item(
    pdf: FPDF,
    item_col_w: float,
    item_name: str,
    description: str,
    *,
    min_h: float = 7.0,
) -> float:
    name_lines = _wrap_lines(pdf, item_name or "Item", item_col_w, font_style="B", font_size=FONT_ITEM_NAME)
    desc_text = _item_desc_text(description)
    desc_lines = _wrap_lines(pdf, desc_text, item_col_w, font_style="", font_size=FONT_ITEM_DESC) if desc_text else []
    pad = 2.4
    name_h = len(name_lines) * 3.6
    desc_h = len(desc_lines) * 3.2 if desc_lines else 0
    gap = 0.6 if desc_lines else 0
    return max(min_h, pad + name_h + gap + desc_h)


def _set_border_color(pdf: FPDF) -> None:
    pdf.set_draw_color(*C_BORDER)


def _draw_header_row(
    pdf: FPDF,
    cols: tuple[float, ...],
    headers: tuple[str, ...],
    aligns: tuple[str, ...],
    *,
    row_h: float = 8.0,
) -> None:
    y = pdf.get_y()
    table_w = sum(cols)
    pdf.set_fill_color(*C_BLUE)
    pdf.set_draw_color(*C_BLUE)
    pdf.rect(pdf.l_margin, y, table_w, row_h, style="F")
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", FONT_HEADER)
    x = pdf.l_margin
    text_y = y + (row_h - 4.2) / 2
    for w, label, al in zip(cols, headers, aligns):
        pdf.set_xy(x, text_y)
        pdf.cell(w, 4.2, _t(label), align=al, border=0)
        x += w
    _set_border_color(pdf)
    pdf.set_y(y + row_h)


def _full_width_text(pdf: FPDF, h: float, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, _t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _draw_item_cell(pdf: FPDF, x: float, y: float, w: float, h: float, item_name: str, description: str) -> None:
    _set_border_color(pdf)
    pdf.rect(x, y, w, h)
    inner_x = x + 1.4
    inner_w = max(6.0, w - 2.8)
    ty = y + 1.8
    pdf.set_text_color(*C_INK)
    for line in _wrap_lines(pdf, item_name or "Item", inner_w, font_style="B", font_size=FONT_ITEM_NAME):
        pdf.set_xy(inner_x, ty)
        pdf.cell(inner_w, 3.6, line, border=0)
        ty += 3.6
    desc_text = _item_desc_text(description)
    if desc_text:
        ty += 0.5
        pdf.set_text_color(*C_MUTED)
        for line in _wrap_lines(pdf, desc_text, inner_w, font_style="", font_size=FONT_ITEM_DESC):
            pdf.set_xy(inner_x, ty)
            pdf.cell(inner_w, 3.2, line, border=0)
            ty += 3.2


def _draw_table_row(
    pdf: FPDF,
    cols: tuple[float, ...],
    values: tuple[str, ...],
    aligns: tuple[str, ...],
    *,
    row_h: float,
    item_col: int | None = None,
    item_name: str = "",
    item_desc: str = "",
    fill: bool = False,
    fill_color: tuple[int, int, int] = C_WHITE,
    bold_cols: set[int] | None = None,
) -> None:
    y = pdf.get_y()
    x = pdf.l_margin
    bold_cols = bold_cols or set()
    _set_border_color(pdf)
    if fill:
        pdf.set_fill_color(*fill_color)
    pdf.set_text_color(*C_INK)
    for i, (w, val, al) in enumerate(zip(cols, values, aligns)):
        if item_col is not None and i == item_col:
            _draw_item_cell(pdf, x, y, w, row_h, item_name, item_desc)
        else:
            style = "FD" if fill else "D"
            pdf.rect(x, y, w, row_h, style=style)
            pdf.set_font("Helvetica", "B" if i in bold_cols else "", FONT_BODY)
            pdf.set_xy(x, y + (row_h - 4.0) / 2)
            pdf.cell(w, 4.0, _t(val), align=al, border=0)
        x += w
    pdf.set_y(y + row_h)


def _draw_firm_header(pdf: FPDF, firm: dict[str, Any]) -> None:
    logo_bytes = _decode_image_bytes(firm.get("logo_url"))
    logo_w = 24.0 if logo_bytes else 0.0
    text_w = float(pdf.epw) - logo_w - (4.0 if logo_bytes else 0.0)
    y0 = pdf.get_y()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*C_INK)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(text_w, 6, _t(firm.get("name") or "House of Prizm"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*C_MUTED)
    for bit in (
        firm.get("address"),
        f"Phone: {firm.get('phone')}" if firm.get("phone") else "",
        f"Email: {firm.get('email')}" if firm.get("email") else "",
        f"GSTIN: {firm.get('gstin')}" if firm.get("gstin") else "",
        f"State: {firm.get('state')}" if firm.get("state") else "",
    ):
        if bit:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(text_w, 4, _t(bit), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y_text = pdf.get_y()

    if logo_bytes:
        try:
            logo_x = pdf.l_margin + float(pdf.epw) - logo_w
            pdf.image(io.BytesIO(logo_bytes), x=logo_x, y=y0, w=logo_w, h=logo_w)
            pdf.set_y(max(y_text, y0 + logo_w + 2))
        except Exception:
            pdf.set_y(y_text)
    else:
        pdf.set_y(y_text)


class _HopPdf(FPDF):
    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", size=7)
        self.set_text_color(*C_MUTED)
        self.cell(0, 5, _t(f"Page {self.page_no()}"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_preview_pdf(preview: dict[str, Any]) -> bytes:
    commercial = _is_commercial(preview)
    pdf = _HopPdf(orientation="L" if commercial else "P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    firm = preview.get("firm") or {}
    header = preview.get("header") or {}
    party = preview.get("party") or {}
    totals = preview.get("totals") or {}
    lines = preview.get("lines") or []

    _draw_firm_header(pdf, firm)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*C_BLUE)
    title = _t(header.get("doc_title") or "Document")
    pdf.cell(0, 9, title, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*C_INK)

    pdf.ln(2)
    epw = float(pdf.epw)
    left_w = epw * 0.58
    right_w = epw - left_w - 4
    right_x = pdf.l_margin + left_w + 4
    y0 = pdf.get_y()

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_MUTED)
    pdf.set_x(pdf.l_margin)
    pdf.cell(left_w, 4, _t(f"{title} For"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*C_INK)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(left_w, 5, _t(party.get("billing_name") or party.get("name") or "-"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for bit in (
        party.get("address"),
        party.get("gstin") and f"GSTIN: {party.get('gstin')}",
        party.get("phone") and f"Phone: {party.get('phone')}",
    ):
        if bit:
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", size=8)
            pdf.set_text_color(*C_MUTED)
            pdf.multi_cell(left_w, 4, _t(bit), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y_left = pdf.get_y()

    pdf.set_xy(right_x, y0)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(*C_INK)
    label_w = min(28, right_w * 0.35)
    val_w = right_w - label_w
    pdf.cell(label_w, 5, "No.", align="R")
    pdf.cell(val_w, 5, _t(header.get("doc_number") or "-"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(right_x)
    pdf.cell(label_w, 5, "Date", align="R")
    doc_date = _t(header.get("doc_date") or "")
    if len(doc_date) >= 10 and doc_date[4] == "-":
        doc_date = f"{doc_date[8:10]}-{doc_date[5:7]}-{doc_date[0:4]}"
    pdf.cell(val_w, 5, doc_date or "-", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y_right = pdf.get_y()
    pdf.set_xy(pdf.l_margin, max(y_left, y_right) + 3)

    notes = _t(header.get("notes"))
    if notes:
        pdf.set_font("Helvetica", size=8)
        pdf.set_text_color(*C_INK)
        for para in notes.split("\n"):
            if para.strip():
                _full_width_text(pdf, 4, para.strip())
        pdf.ln(1)

    if commercial:
        _draw_commercial_table(pdf, lines)
    elif lines:
        _draw_standard_table(pdf, lines)
    else:
        pdf.set_font("Helvetica", "I", 9)
        _full_width_text(pdf, 5, preview.get("lines_missing_hint") or "No line items.")

    _draw_totals_block(pdf, totals, commercial)
    _draw_terms_bank(pdf, preview)
    return bytes(pdf.output())


def _draw_commercial_table(pdf: _HopPdf, lines: list[dict]) -> None:
    sections = _group_sections(lines) if any(_t(l.get("section_title")) for l in lines) else [{"title": "Items", "lines": lines}]
    weights = (4, 26, 7, 6, 10, 10, 10, 7, 7, 13)
    cols = _scale_cols(pdf, weights)
    item_col_idx = 1
    headers = (
        "Sl.",
        "Item Description",
        "Qty.",
        "Unit",
        "Project Rate",
        "Amount",
        "Per Pc after Disc.",
        "Discount",
        "GST %",
        "Net Amount",
    )
    _draw_header_row(
        pdf,
        cols,
        headers,
        ("C", "L", "R", "C", "R", "R", "R", "C", "C", "R"),
    )

    table_w = sum(cols)
    sl = 0
    net_col = len(cols) - 1
    for sec in sections:
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(*C_SECTION_BG)
        _set_border_color(pdf)
        pdf.set_font("Helvetica", "B", FONT_HEADER)
        pdf.set_text_color(*C_INK)
        pdf.cell(table_w, 6, _t(sec.get("title") or "Items"), border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        sec_total = 0.0
        for ln in sec.get("lines") or []:
            sl += 1
            qty = float(ln.get("qty") or 0)
            rate = float(ln.get("rate") or 0)
            disc = float(ln.get("discount_pct") or 0)
            gst = float(ln.get("tax_pct") or 0)
            gross = round(qty * rate, 2)
            per_pc = round(rate * (1 - disc / 100), 2) if rate else 0
            taxable = max(0.0, gross * (1 - disc / 100))
            net = float(ln.get("line_total") or round(taxable * (1 + gst / 100), 2))
            sec_total += net
            item_name = _t(ln.get("item_name") or "Item")
            item_desc = _t(ln.get("description"))
            row_h = _row_height_for_item(pdf, cols[item_col_idx], item_name, item_desc)
            row = (
                str(sl),
                "",
                f"{qty:g}",
                _t(ln.get("unit") or "MTR")[:6],
                _money(rate, compact=True),
                _money(gross, compact=True),
                _money(per_pc, compact=True),
                f"{disc:g}%",
                f"{gst:g}%",
                _money(net, compact=True),
            )
            _draw_table_row(
                pdf,
                cols,
                row,
                ("C", "L", "R", "C", "R", "R", "R", "C", "C", "R"),
                row_h=row_h,
                item_col=item_col_idx,
                item_name=item_name,
                item_desc=item_desc,
                bold_cols={net_col},
            )
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(*C_TOTAL_ROW_BG)
        _set_border_color(pdf)
        pdf.set_font("Helvetica", "B", FONT_BODY)
        pdf.set_text_color(*C_INK)
        pdf.cell(table_w - cols[-1], 6, "Section total", border=1, align="R", fill=True)
        pdf.cell(cols[-1], 6, _money(sec_total, compact=True), border=1, align="R", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _draw_standard_table(pdf: _HopPdf, lines: list[dict]) -> None:
    weights = (5, 30, 10, 8, 8, 12, 12, 15)
    cols = _scale_cols(pdf, weights)
    item_col_idx = 1
    headers = ("#", "Item Name", "HSN", "Qty", "Unit", "Rate", "GST", "Amount")
    _draw_header_row(
        pdf,
        cols,
        headers,
        ("C", "L", "L", "R", "C", "R", "R", "R"),
    )
    for i, ln in enumerate(lines, 1):
        qty = float(ln.get("qty") or 0)
        rate = float(ln.get("rate") or 0)
        gst = float(ln.get("tax_pct") or 0)
        tax_amt = float(ln.get("tax_amount") or 0)
        net = float(ln.get("line_total") or 0)
        gst_cell = f"{_money(tax_amt, compact=True)} ({gst:g}%)" if gst else _money(tax_amt, compact=True)
        item_name = _t(ln.get("item_name") or "Item")
        item_desc = _t(ln.get("description"))
        row_h = _row_height_for_item(pdf, cols[item_col_idx], item_name, item_desc)
        row = (
            str(i),
            "",
            _t(ln.get("hsn") or "")[:12],
            f"{qty:g}",
            _t(ln.get("unit") or "Pcs")[:8],
            _money(rate, compact=True),
            gst_cell,
            _money(net, compact=True),
        )
        _draw_table_row(
            pdf,
            cols,
            row,
            ("C", "L", "L", "R", "C", "R", "R", "R"),
            row_h=row_h,
            item_col=item_col_idx,
            item_name=item_name,
            item_desc=item_desc,
            bold_cols={len(cols) - 1},
        )
    pdf.ln(2)


def _draw_totals_block(pdf: _HopPdf, totals: dict, commercial: bool) -> None:
    if not totals:
        return
    block_w = min(95, float(pdf.epw) * 0.38)
    label_w = block_w * 0.58
    val_w = block_w - label_w
    x = pdf.w - pdf.r_margin - block_w
    y = pdf.get_y()
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*C_INK)
    rows = []
    if commercial:
        rows.append(("Total value (excl. tax)", _money(totals.get("taxable_total") or totals.get("sub_total"))))
        for b in totals.get("tax_breakdown") or []:
            scope = "Shipping" if b.get("scope") == "shipping" else "Items"
            pct = float(b.get("tax_pct") or 0)
            rows.append((f"GST @ {pct:g}% on {scope}", _money(b.get("tax_amount"))))
        if float(totals.get("shipping_amount") or 0) > 0:
            rows.append(("Shipping charges", _money(totals.get("shipping_amount"))))
    else:
        rows.append(("Sub Total", _money(totals.get("sub_total"))))
        rows.append(("Tax", _money(totals.get("tax_total"))))
    rows.append(("Grand Total", _money(totals.get("grand_total"))))
    for label, val in rows:
        pdf.set_x(x)
        is_grand = label == "Grand Total"
        pdf.set_font("Helvetica", "B", 10 if is_grand else 8)
        if is_grand:
            pdf.set_text_color(*C_BLUE)
        else:
            pdf.set_text_color(*C_INK)
        pdf.cell(label_w, 5, _t(label), border=0)
        pdf.cell(val_w, 5, val, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def _draw_terms_bank(pdf: _HopPdf, preview: dict) -> None:
    firm = preview.get("firm") or {}
    terms = _t(preview.get("terms"))
    delivery = _t(preview.get("delivery_terms"))
    sig_bytes = _decode_image_bytes(firm.get("signature_url"))

    left_w = float(pdf.epw) * 0.62
    sign_w = float(pdf.epw) - left_w - 4
    sign_x = pdf.l_margin + left_w + 4
    y0 = pdf.get_y()

    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*C_INK)
    if delivery:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Delivery Terms", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        _full_width_text(pdf, 4, delivery)
        pdf.ln(1)
    if terms:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(left_w, 5, "Terms and Conditions", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(left_w, 4, terms, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
    bank_bits = [
        b
        for b in (
            firm.get("bank_name") and f"Bank: {firm.get('bank_name')}",
            firm.get("bank_account") and f"A/C: {firm.get('bank_account')}",
            firm.get("bank_ifsc") and f"IFSC: {firm.get('bank_ifsc')}",
            firm.get("bank_holder") and f"Holder: {firm.get('bank_holder')}",
        )
        if b
    ]
    if bank_bits:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(left_w, 5, "Bank Details", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        for b in bank_bits:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(left_w, 4, _t(b), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y_left = pdf.get_y()

    pdf.set_xy(sign_x, y0)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(sign_w, 4, _t(f"For {firm.get('name') or 'House of Prizm'}"), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    sig_y = pdf.get_y() + 1
    if sig_bytes:
        try:
            sig_h = 16.0
            sig_img_w = min(sign_w - 4, 42.0)
            sig_x = sign_x + sign_w - sig_img_w
            pdf.image(io.BytesIO(sig_bytes), x=sig_x, y=sig_y, w=sig_img_w, h=sig_h)
            sig_y += sig_h + 2
        except Exception:
            sig_y += 10
    else:
        sig_y += 12
    _set_border_color(pdf)
    pdf.line(sign_x, sig_y, sign_x + sign_w, sig_y)
    pdf.set_xy(sign_x, sig_y + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_INK)
    pdf.cell(sign_w, 4, "Authorized Signatory", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_y(max(y_left, pdf.get_y()) + 2)
