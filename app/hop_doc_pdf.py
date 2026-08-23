"""PDF export for HoP document preview (quotation / proforma / invoice)."""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from fpdf import FPDF


def _t(text: Any) -> str:
    s = str(text or "").strip()
    s = s.replace("₹", "Rs.")
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    return s.encode("latin-1", "replace").decode("latin-1")


def _money(n: Any) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
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


class _HopPdf(FPDF):
    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", size=7)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, _t(f"Page {self.page_no()}"), align="C")


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
    pdf.cell(0, 7, _t(firm.get("name") or "House of Prizm"), ln=True)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(100, 116, 139)
    for bit in (
        firm.get("address"),
        f"Phone: {firm.get('phone')}" if firm.get("phone") else "",
        f"Email: {firm.get('email')}" if firm.get("email") else "",
        f"GSTIN: {firm.get('gstin')}" if firm.get("gstin") else "",
    ):
        if bit:
            pdf.multi_cell(0, 4, _t(bit))

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(29, 78, 216)
    title = _t(header.get("doc_title") or "Document")
    pdf.cell(0, 8, title, align="C", ln=True)
    pdf.set_text_color(15, 23, 42)

    pdf.ln(2)
    left_w = pdf.w * 0.58
    right_x = pdf.l_margin + left_w + 4
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(left_w, 4, _t(f"{title} For"), ln=True)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(left_w, 5, _t(party.get("billing_name") or party.get("name") or "-"))
    for bit in (party.get("address"), party.get("gstin") and f"GSTIN: {party.get('gstin')}", party.get("phone") and f"Phone: {party.get('phone')}"):
        if bit:
            pdf.set_font("Helvetica", size=8)
            pdf.set_text_color(100, 116, 139)
            pdf.multi_cell(left_w, 4, _t(bit))
    y_left = pdf.get_y()

    pdf.set_xy(right_x, y0)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(30, 5, "No.", align="R")
    pdf.cell(0, 5, _t(header.get("doc_number") or "-"), ln=True)
    pdf.set_x(right_x)
    pdf.cell(30, 5, "Date", align="R")
    doc_date = _t(header.get("doc_date") or "")
    if len(doc_date) >= 10 and doc_date[4] == "-":
        doc_date = f"{doc_date[8:10]}-{doc_date[5:7]}-{doc_date[0:4]}"
    pdf.cell(0, 5, doc_date or "-", ln=True)
    y_right = pdf.get_y()
    pdf.set_y(max(y_left, y_right) + 3)

    notes = _t(header.get("notes"))
    if notes:
        pdf.set_font("Helvetica", size=8)
        for para in notes.split("\n"):
            if para.strip():
                pdf.multi_cell(0, 4, _t(para.strip()))
        pdf.ln(1)

    if commercial:
        _draw_commercial_table(pdf, lines, totals)
    elif lines:
        _draw_standard_table(pdf, lines, totals)
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(0, 5, _t(preview.get("lines_missing_hint") or "No line items."))

    _draw_totals_block(pdf, totals, commercial)
    _draw_terms_bank(pdf, preview)
    return bytes(pdf.output())


def _draw_commercial_table(pdf: _HopPdf, lines: list[dict], totals: dict) -> None:
    sections = _group_sections(lines) if any(_t(l.get("section_title")) for l in lines) else [{"title": "Items", "lines": lines}]
    cols = (8, 52, 14, 12, 18, 18, 18, 12, 12, 22)
    headers = ("Sl", "Item Description", "Qty", "Unit", "Rate", "Amount", "Per Pc", "Disc%", "GST%", "Net")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(29, 78, 216)
    pdf.set_text_color(255, 255, 255)
    for w, h in zip(cols, headers):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(15, 23, 42)
    sl = 0
    for sec in sections:
        pdf.set_fill_color(241, 245, 249)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(sum(cols), 5, _t(sec.get("title") or "Items"), border=1, fill=True, ln=True)
        pdf.set_font("Helvetica", size=7)
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
                item[:70],
                f"{qty:g}",
                _t(ln.get("unit") or "MTR")[:6],
                _money(rate),
                _money(gross),
                _money(per_pc),
                f"{disc:g}%",
                f"{gst:g}%",
                _money(net),
            )
            aligns = ("C", "L", "R", "C", "R", "R", "R", "C", "C", "R")
            for w, val, al in zip(cols, row, aligns):
                pdf.cell(w, 5, val, border=1, align=al)
            pdf.ln()
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(sum(cols) - cols[-1], 5, "Section total", border=1, align="R")
        pdf.cell(cols[-1], 5, _money(sec_total), border=1, align="R", ln=True)
        pdf.set_font("Helvetica", size=7)
    pdf.ln(2)


def _draw_standard_table(pdf: _HopPdf, lines: list[dict], totals: dict) -> None:
    cols = (8, 58, 18, 14, 14, 22, 22, 24)
    headers = ("#", "Item", "HSN", "Qty", "Unit", "Rate", "GST", "Amount")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(29, 78, 216)
    pdf.set_text_color(255, 255, 255)
    for w, h in zip(cols, headers):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(15, 23, 42)
    for i, ln in enumerate(lines, 1):
        qty = float(ln.get("qty") or 0)
        rate = float(ln.get("rate") or 0)
        gst = float(ln.get("tax_pct") or 0)
        tax_amt = float(ln.get("tax_amount") or 0)
        net = float(ln.get("line_total") or 0)
        gst_cell = f"{_money(tax_amt)} ({gst:g}%)" if gst else _money(tax_amt)
        row = (
            str(i),
            _t(ln.get("item_name") or "Item")[:55],
            _t(ln.get("hsn") or "")[:12],
            f"{qty:g}",
            _t(ln.get("unit") or "Pcs")[:8],
            _money(rate),
            gst_cell,
            _money(net),
        )
        aligns = ("C", "L", "L", "R", "C", "R", "R", "R")
        for w, val, al in zip(cols, row, aligns):
            pdf.cell(w, 5, val, border=1, align=al)
        pdf.ln()
    pdf.ln(2)


def _draw_totals_block(pdf: _HopPdf, totals: dict, commercial: bool) -> None:
    if not totals:
        return
    x = pdf.w - pdf.r_margin - 95
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
        pdf.cell(55, 5, label, border=0)
        pdf.cell(40, 5, val, align="R", ln=True)
    pdf.ln(3)


def _draw_terms_bank(pdf: _HopPdf, preview: dict) -> None:
    firm = preview.get("firm") or {}
    terms = _t(preview.get("terms"))
    if terms:
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Terms and Conditions", ln=True)
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(0, 4, terms)
        pdf.ln(2)
    bank_bits = [b for b in (
        firm.get("bank_name") and f"Bank: {firm.get('bank_name')}",
        firm.get("bank_account") and f"A/C: {firm.get('bank_account')}",
        firm.get("bank_ifsc") and f"IFSC: {firm.get('bank_ifsc')}",
        firm.get("bank_holder") and f"Holder: {firm.get('bank_holder')}",
    ) if b]
    if bank_bits:
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Bank Details", ln=True)
        pdf.set_font("Helvetica", size=8)
        for b in bank_bits:
            pdf.cell(0, 4, _t(b), ln=True)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=8)
    pdf.cell(0, 4, _t(f"For {firm.get('name') or 'House of Prizm'}"), align="R", ln=True)
    pdf.cell(0, 4, "Authorized Signatory", align="R", ln=True)
