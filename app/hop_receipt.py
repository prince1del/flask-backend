"""Thermal receipt (Bluetooth printer, 58 mm = 32 columns, 80 mm = 48 columns).

The server lays the receipt out (same bill data and print settings as the PDF); the phone only turns
these lines into ESC/POS bytes and sends them to the paired printer. Plain ASCII, so every cheap
printer prints it: "₹" becomes "Rs.", long item names wrap.
"""
from __future__ import annotations

import textwrap
from typing import Any

WIDTHS = {"thermal58": 32, "thermal80": 48}


def _ascii(s: Any) -> str:
    t = str(s or "").replace("₹", "Rs.").replace("–", "-").replace("—", "-").replace("•", "*")
    return t.encode("ascii", "replace").decode("ascii")


def _money(v: float, decimals: int = 2) -> str:
    return f"{float(v or 0):,.{decimals}f}"


def _num(v: float) -> str:
    v = float(v or 0)
    return f"{v:g}" if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")


def build(data: dict[str, Any], width: int | None = None) -> dict[str, Any]:
    pr = data.get("print") or {}
    w = int(width or WIDTHS.get(pr.get("paper") or "", 32))
    w = 48 if w >= 48 else 32
    dec = int(pr.get("decimals") or 2)
    cur = "Rs." if (pr.get("currency") or "₹") else ""
    firm, party, head, tot = data.get("firm") or {}, data.get("party") or {}, data.get("header") or {}, data.get("totals") or {}
    out: list[dict[str, Any]] = []

    def line(text: str = "", *, bold: bool = False, center: bool = False, big: bool = False) -> None:
        out.append({"text": _ascii(text)[:w], "bold": bold, "center": center, "big": big})

    def wrap(text: str, **kw) -> None:
        for part in textwrap.wrap(_ascii(text), w) or [""]:
            line(part, **kw)

    def lr(left: str, right: str, bold: bool = False) -> None:
        left, right = _ascii(left), _ascii(right)
        room = w - len(right) - 1
        if len(left) > room:
            line(left, bold=bold)
            left = ""
        line(left.ljust(w - len(right)) + right, bold=bold)

    rule = "-" * w
    if pr.get("header"):
        wrap(pr["header"], center=True)
    wrap(firm.get("name") or "", bold=True, center=True, big=True)
    for k in ("address", "phone"):
        if firm.get(k):
            wrap(firm[k], center=True)
    if firm.get("gstin"):
        line(f"GSTIN: {firm['gstin']}", center=True)
    line(rule)
    line(head.get("doc_title") or "Bill", bold=True, center=True)
    lr(f"No: {head.get('doc_number') or ''}", f"Dt: {head.get('doc_date') or ''}")
    if party.get("name"):
        wrap(f"To: {party.get('billing_name') or party['name']}")
    if party.get("phone"):
        line(f"Ph: {party['phone']}")
    if party.get("gstin"):
        line(f"GSTIN: {party['gstin']}")
    line(rule)
    lr("Item", "Amount", bold=True)
    line(rule)
    for ln in data.get("lines") or []:
        wrap(ln.get("item_name") or "Item")
        detail = f"  {_num(ln.get('qty'))} {ln.get('unit') or ''} x {_money(ln.get('rate'), dec)}".rstrip()
        if ln.get("tax_pct"):
            detail += f" +{_num(ln['tax_pct'])}%"
        lr(detail, _money(ln.get("line_total"), dec))
    line(rule)
    lr("Sub total", _money(tot.get("sub_total"), dec))
    if tot.get("discount_amount"):
        lr("Discount", "-" + _money(tot["discount_amount"], dec))
    for g in data.get("gst_split") or []:
        label = g.get("label") or g.get("name") or "Tax"
        lr(str(label), _money(g.get("amount"), dec))
    if not data.get("gst_split") and tot.get("tax_total"):
        lr("Tax", _money(tot["tax_total"], dec))
    if tot.get("shipping_amount"):
        lr("Shipping", _money(tot["shipping_amount"], dec))
    if tot.get("round_off"):
        lr("Round off", _money(tot["round_off"], dec))
    lr(f"TOTAL {cur}".strip(), _money(tot.get("grand_total"), dec), bold=True)
    bal = float(head.get("balance_amount") or 0)
    if 0.009 < bal < float(tot.get("grand_total") or 0) - 0.009:
        lr("Paid", _money(float(tot.get("grand_total") or 0) - bal, dec))
    if bal > 0.009:
        lr("Balance due", _money(bal, dec), bold=True)
    if tot.get("amount_in_words"):
        wrap(tot["amount_in_words"])
    line(rule)
    upi = data.get("upi") or {}
    qr = upi.get("link") or upi.get("upi_link") or None
    if qr:
        line("Scan to pay by UPI", center=True)
    if data.get("footer"):
        wrap(data["footer"], center=True)
    line("Thank you!", center=True)
    return {"width": w, "lines": out, "qr": qr, "copies": int(pr.get("copies") or 1)}
