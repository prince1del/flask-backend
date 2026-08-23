"""PDF export for HoP document preview (quotation / proforma / invoice)."""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos


def _t(text: Any) -> str:
    s = str(text or "").strip()
    s = s.replace("₹", "Rs.")
    s = s.replace("\u2013", "-").replace("\u2014", "-")
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


def _fit(text: str, max_len: int) -> str:
    s = _t(text)
    return s if len(s) <= max_len else s[: max(0, max_len - 1)] + "…"


def _scale_cols(pdf: FPDF, weights: tuple[float, ...]) -> tuple[float, ...]:
    total = sum(weights) or 1.0
    epw = float(pdf.epw)
    return tuple(epw * w / total for w in weights)


def _full_width_text(pdf: FPDF, h: float, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, _t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _row_cells(
    pdf: FPDF,
    cols: tuple[float, ...],
    values: tuple[str, ...],
    aligns: tuple[str, ...],
    *,
    height: float = 5,
    fill: bool = False,
    header: bool = False,
) -> None:
    pdf.set_x(pdf.l_margin)
    style = "B" if header else ""
    if header:
        pdf.set_fill_color(29, 78, 216)
        pdf.set_text_color(255, 255, 255)
    else:
        pdf.set_text_color(15, 23, 42)
    for w, val, al in zip(cols, values, aligns):
        pdf.cell(w, height, val, border=1, align=al, fill=fill)
    pdf.ln()


class _HopPdf(FPDF):
    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", size=7)
        self.set_text_color(100, 116, 139)
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

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(
        0,
        7,
        _t(firm.get("name") or "House of Prizm"),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(100, 116, 139)
    for bit in (
        firm.get("address"),
        f"Phone: {firm.get('phone')}" if firm.get("phone") else "",
        f"Email: {firm.get('email')}" if firm.get("email") else "",
        f"GSTIN: {firm.get('gstin')}" if firm.get("gstin") else "",
    ):
        if bit:
            _full_width_text(pdf, 4, bit)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(29, 78, 216)
    title = _t(header.get("doc_title") or "Document")
    pdf.cell(0, 8, title, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(15, 23, 42)

    pdf.ln(2)
    epw = float(pdf.epw)
    left_w = epw * 0.58
    right_w = epw - left_w - 4
    right_x = pdf.l_margin + left_w + 4
    y0 = pdf.get_y()

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(100, 116, 139)
    pdf.set_x(pdf.l_margin)
    pdf.cell(left_w, 4, _t(f"{title} For"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
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
            pdf.set_text_color(100, 116, 139)
            pdf.multi_cell(left_w, 4, _t(bit), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y_left = pdf.get_y()

    pdf.set_xy(right_x, y0)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(15, 23, 42)
    label_w = min(28, right_w * 0.35)
    val_w = right_w - label_w
    pdf.cell(label_w, 5, "No.", align="R")
    pdf.cell(val_w, 5, _fit(header.get("doc_number") or "-", 40), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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
    weights = (4, 24, 7, 6, 9, 9, 9, 6, 6, 10)
    cols = _scale_cols(pdf, weights)
    headers = ("Sl", "Item", "Qty", "Unit", "Rate", "Amount", "Per Pc", "Disc", "GST", "Net")
    pdf.set_font("Helvetica", "B", 6)
    _row_cells(pdf, cols, headers, ("C", "L", "R", "C", "R", "R", "R", "C", "C", "R"), height=6, fill=True, header=True)

    pdf.set_font("Helvetica", size=6)
    sl = 0
    table_w = sum(cols)
    for sec in sections:
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(table_w, 5, _fit(sec.get("title") or "Items", 120), border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=6)
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
            item = _t(ln.get("item_name") or "Item")
            desc = _t(ln.get("description"))
            if desc:
                item = f"{item} ({desc})"
            row = (
                str(sl),
                _fit(item, 80),
                f"{qty:g}",
                _fit(ln.get("unit") or "MTR", 6),
                _money(rate, compact=True),
                _money(gross, compact=True),
                _money(per_pc, compact=True),
                f"{disc:g}%",
                f"{gst:g}%",
                _money(net, compact=True),
            )
            _row_cells(pdf, cols, row, ("C", "L", "R", "C", "R", "R", "R", "C", "C", "R"))
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 6)
        pdf.cell(table_w - cols[-1], 5, "Section total", border=1, align="R")
        pdf.cell(cols[-1], 5, _money(sec_total, compact=True), border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=6)
    pdf.ln(2)


def _draw_standard_table(pdf: _HopPdf, lines: list[dict]) -> None:
    weights = (5, 30, 10, 8, 8, 12, 12, 15)
    cols = _scale_cols(pdf, weights)
    headers = ("#", "Item", "HSN", "Qty", "Unit", "Rate", "GST", "Amount")
    pdf.set_font("Helvetica", "B", 7)
    _row_cells(pdf, cols, headers, ("C", "L", "L", "R", "C", "R", "R", "R"), height=6, fill=True, header=True)
    pdf.set_font("Helvetica", size=7)
    for i, ln in enumerate(lines, 1):
        qty = float(ln.get("qty") or 0)
        rate = float(ln.get("rate") or 0)
        gst = float(ln.get("tax_pct") or 0)
        tax_amt = float(ln.get("tax_amount") or 0)
        net = float(ln.get("line_total") or 0)
        gst_cell = f"{_money(tax_amt, compact=True)} ({gst:g}%)" if gst else _money(tax_amt, compact=True)
        row = (
            str(i),
            _fit(ln.get("item_name") or "Item", 55),
            _fit(ln.get("hsn") or "", 12),
            f"{qty:g}",
            _fit(ln.get("unit") or "Pcs", 8),
            _money(rate, compact=True),
            _fit(gst_cell, 18),
            _money(net, compact=True),
        )
        _row_cells(pdf, cols, row, ("C", "L", "L", "R", "C", "R", "R", "R"))
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
        pdf.set_font("Helvetica", "B" if label == "Grand Total" else "", 8 if label != "Grand Total" else 10)
        pdf.cell(label_w, 5, _fit(label, 42), border=0)
        pdf.cell(val_w, 5, val, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def _draw_terms_bank(pdf: _HopPdf, preview: dict) -> None:
    firm = preview.get("firm") or {}
    terms = _t(preview.get("terms"))
    if terms:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Terms and Conditions", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        _full_width_text(pdf, 4, terms)
        pdf.ln(2)
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
        pdf.cell(0, 5, "Bank Details", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=8)
        for b in bank_bits:
            _full_width_text(pdf, 4, b)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=8)
    pdf.cell(0, 4, _t(f"For {firm.get('name') or 'House of Prizm'}"), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 4, "Authorized Signatory", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
