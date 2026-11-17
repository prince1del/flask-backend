"""HoP document preview payloads (Vyapar-style Estimate / Invoice / Bill)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


ONES = [
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def amount_in_words_inr(amount: float) -> str:
    """Indian numbering: Crore / Lakh / Thousand."""
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        n = 0.0
    sign = "Minus " if n < 0 else ""
    n = abs(n)
    rupees = int(n)
    paise = int(round((n - rupees) * 100))

    def _two(num: int) -> str:
        if num < 20:
            return ONES[num]
        return f"{TENS[num // 10]}{(' ' + ONES[num % 10]) if num % 10 else ''}".strip()

    def _three(num: int) -> str:
        h = num // 100
        r = num % 100
        parts = []
        if h:
            parts.append(f"{ONES[h]} Hundred")
        if r:
            parts.append(_two(r))
        return " ".join(parts)

    if rupees == 0:
        words = "Zero"
    else:
        crore = rupees // 10000000
        rem = rupees % 10000000
        lakh = rem // 100000
        rem %= 100000
        thousand = rem // 1000
        rem %= 1000
        chunks = []
        if crore:
            chunks.append(f"{_three(crore)} Crore")
        if lakh:
            chunks.append(f"{_three(lakh)} Lakh")
        if thousand:
            chunks.append(f"{_three(thousand)} Thousand")
        if rem:
            chunks.append(_three(rem))
        words = " ".join(chunks)

    out = f"{sign}{words} Rupees"
    if paise:
        out += f" and {_two(paise)} Paise"
    return out + " Only"


def upsert_firm_profile(conn: sqlite3.Connection, workspace_id: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO hop_firm_profile (
            workspace_id, firm_name, address, phone, email, gstin, state, pan,
            bank_name, bank_account, bank_ifsc, bank_holder, logo_url, terms_default,
            delivery_terms, business_type, business_category, pincode, signature_url,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(workspace_id) DO UPDATE SET
            firm_name=excluded.firm_name,
            address=excluded.address,
            phone=excluded.phone,
            email=excluded.email,
            gstin=excluded.gstin,
            state=excluded.state,
            pan=excluded.pan,
            bank_name=COALESCE(NULLIF(excluded.bank_name,''), hop_firm_profile.bank_name),
            bank_account=COALESCE(NULLIF(excluded.bank_account,''), hop_firm_profile.bank_account),
            bank_ifsc=COALESCE(NULLIF(excluded.bank_ifsc,''), hop_firm_profile.bank_ifsc),
            bank_holder=COALESCE(NULLIF(excluded.bank_holder,''), hop_firm_profile.bank_holder),
            logo_url=COALESCE(NULLIF(excluded.logo_url,''), hop_firm_profile.logo_url),
            terms_default=excluded.terms_default,
            delivery_terms=excluded.delivery_terms,
            business_type=COALESCE(NULLIF(excluded.business_type,''), hop_firm_profile.business_type),
            business_category=COALESCE(NULLIF(excluded.business_category,''), hop_firm_profile.business_category),
            pincode=COALESCE(NULLIF(excluded.pincode,''), hop_firm_profile.pincode),
            signature_url=COALESCE(NULLIF(excluded.signature_url,''), hop_firm_profile.signature_url),
            updated_at=datetime('now')
        """,
        (
            workspace_id,
            _clean(payload.get("firm_name")),
            _clean(payload.get("address")),
            _clean(payload.get("phone")),
            _clean(payload.get("email")),
            _clean(payload.get("gstin")),
            _clean(payload.get("state")),
            _clean(payload.get("pan")),
            _clean(payload.get("bank_name")),
            _clean(payload.get("bank_account")),
            _clean(payload.get("bank_ifsc")),
            _clean(payload.get("bank_holder")),
            _clean(payload.get("logo_url")),
            _clean(payload.get("terms_default")),
            _clean(payload.get("delivery_terms")),
            _clean(payload.get("business_type")),
            _clean(payload.get("business_category")),
            _clean(payload.get("pincode")),
            _clean(payload.get("signature_url")),
        ),
    )


def patch_firm_profile(conn: sqlite3.Connection, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Settings screen save — overwrites provided fields (incl. logo / banking)."""
    existing = get_firm_profile(conn, workspace_id)
    merged = dict(existing)
    field_map = (
        "firm_name", "address", "phone", "email", "gstin", "state", "pan",
        "bank_name", "bank_account", "bank_ifsc", "bank_holder", "logo_url",
        "terms_default", "delivery_terms", "business_type", "business_category",
        "pincode", "signature_url",
    )
    for key in field_map:
        if key in payload:
            merged[key] = _clean(payload.get(key))
    if not _clean(merged.get("firm_name")):
        merged["firm_name"] = "Your Business Name"
    upsert_firm_profile(conn, workspace_id, merged)
    conn.commit()
    return get_firm_profile(conn, workspace_id)


def _line_taxable_amount(ln: dict[str, Any]) -> float:
    qty = float(ln.get("qty") or 0)
    rate = float(ln.get("rate") or 0)
    disc = float(ln.get("discount_amount") or 0)
    disc_pct = float(ln.get("discount_pct") or 0)
    gross = round(qty * rate, 2)
    if disc_pct > 0.009:
        disc = round(gross * disc_pct / 100.0, 2)
    return max(0.0, round(gross - disc, 2))


def tax_breakdown_from_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group line GST by tax_pct — for preview / PDF totals."""
    buckets: dict[float, dict[str, Any]] = {}
    for ln in lines or []:
        pct = round(float(ln.get("tax_pct") or 0), 2)
        tax = float(ln.get("tax_amount") or 0)
        if pct <= 0.009 and tax <= 0.009:
            continue
        bucket = buckets.setdefault(
            pct,
            {"tax_pct": pct, "taxable_amount": 0.0, "tax_amount": 0.0, "scope": "items"},
        )
        bucket["taxable_amount"] += _line_taxable_amount(ln)
        bucket["tax_amount"] += tax
    out: list[dict[str, Any]] = []
    for bucket in buckets.values():
        out.append(
            {
                "tax_pct": bucket["tax_pct"],
                "taxable_amount": round(bucket["taxable_amount"], 2),
                "tax_amount": round(bucket["tax_amount"], 2),
                "scope": "items",
            }
        )
    return sorted(out, key=lambda x: float(x.get("tax_pct") or 0))


def _shipping_tax_amounts(
    shipping: float, shipping_tax_pct: float, shipping_tax_amount: float = 0.0
) -> tuple[float, float]:
    ship = max(0.0, float(shipping or 0))
    pct = max(0.0, float(shipping_tax_pct or 0))
    tax = float(shipping_tax_amount or 0)
    if tax <= 0.009 and ship > 0.009 and pct > 0.009:
        tax = round(ship * pct / 100.0, 2)
    return ship, round(tax, 2)


def compute_line_amounts(
    qty: float,
    rate: float,
    discount_pct: float = 0.0,
    discount_amount: float = 0.0,
    tax_pct: float = 0.0,
) -> dict[str, float]:
    """FAIRFIELD-style: gross = qty×rate; net = (gross−disc)×(1+GST%)."""
    gross = round(qty * rate, 2)
    disc = float(discount_amount or 0)
    if float(discount_pct or 0) > 0.009:
        disc = round(gross * float(discount_pct) / 100.0, 2)
    taxable = max(0.0, gross - disc)
    tax_amt = round(taxable * float(tax_pct or 0) / 100.0, 2)
    line_total = round(taxable + tax_amt, 2)
    return {
        "gross": gross,
        "discount_amount": disc,
        "taxable": round(taxable, 2),
        "tax_amount": tax_amt,
        "line_total": line_total,
    }


def replace_txn_lines(
    conn: sqlite3.Connection,
    workspace_id: str,
    source_txn_id: int,
    lines: list[dict[str, Any]],
) -> int:
    conn.execute(
        "DELETE FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=?",
        (workspace_id, int(source_txn_id)),
    )
    n = 0
    for i, line in enumerate(lines or []):
        conn.execute(
            """
            INSERT INTO hop_txn_lines (
                workspace_id, source_txn_id, line_no, item_name, item_code, description, hsn,
                qty, unit, rate, discount_amount, discount_pct, section_title,
                tax_pct, tax_amount, line_total, product_id, is_charge, free_qty, cess_pct, cess_amount,
                batch_no, expiry_date, serial_no, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                workspace_id,
                int(source_txn_id),
                int(line.get("line_no") if line.get("line_no") is not None else i + 1),
                _clean(line.get("item_name")),
                _clean(line.get("item_code")),
                _clean(line.get("description")),
                _clean(line.get("hsn")),
                float(line.get("qty") or 0),
                _clean(line.get("unit")) or "Pcs",
                float(line.get("rate") or 0),
                float(line.get("discount_amount") or 0),
                float(line.get("discount_pct") or 0),
                _clean(line.get("section_title")),
                float(line.get("tax_pct") or 0),
                float(line.get("tax_amount") or 0),
                float(line.get("line_total") or 0),
                line.get("product_id"),
                1 if line.get("is_charge") else 0,
                float(line.get("free_qty") or 0),
                float(line.get("cess_pct") or 0) or None,
                float(line.get("cess_amount") or 0) or None,
                _clean(line.get("batch_no")) or None,
                _clean(line.get("expiry_date")) or None,
                _clean(line.get("serial_no")) or None,
            ),
        )
        n += 1
    return n


def get_firm_profile(conn: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM hop_firm_profile WHERE workspace_id=?",
        (workspace_id,),
    ).fetchone()
    if row:
        return dict(row)
    # Sensible HoP defaults until Vyapar firm is imported.
    return {
        "workspace_id": workspace_id,
        "firm_name": "Your Business Name",
        "address": "",
        "phone": "",
        "email": "",
        "gstin": "",
        "state": "",
        "pan": "",
        "bank_name": "",
        "bank_account": "",
        "bank_ifsc": "",
        "bank_holder": "",
        "logo_url": "",
        "terms_default": "",
        "delivery_terms": "",
        "business_type": "",
        "business_category": "",
        "pincode": "",
        "signature_url": "",
    }


def build_txn_preview(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    party_txn_id: int | None = None,
    source_txn_id: int | None = None,
) -> dict[str, Any] | None:
    if party_txn_id:
        header = conn.execute(
            "SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?",
            (workspace_id, int(party_txn_id)),
        ).fetchone()
    elif source_txn_id is not None:
        header = conn.execute(
            "SELECT * FROM hop_party_transactions WHERE workspace_id=? AND source_txn_id=?",
            (workspace_id, int(source_txn_id)),
        ).fetchone()
    else:
        return None
    if not header:
        return None
    h = dict(header)
    src_id = int(h.get("source_txn_id") or 0)
    lines = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM hop_txn_lines
            WHERE workspace_id=? AND source_txn_id=?
            ORDER BY line_no ASC, id ASC
            """,
            (workspace_id, src_id),
        ).fetchall()
    ]

    party: dict[str, Any] = {}
    ptype = _clean(h.get("party_type")).lower()
    pid = h.get("party_id")
    if pid:
        table = "hop_vendors" if ptype == "vendor" else "hop_customers"
        prow = conn.execute(
            f"SELECT * FROM {table} WHERE workspace_id=? AND id=?",
            (workspace_id, int(pid)),
        ).fetchone()
        if prow:
            party = dict(prow)

    firm = get_firm_profile(conn, workspace_id)

    sub_total = 0.0
    tax_total = 0.0
    qty_total = 0.0
    for ln in lines:
        # Batch / expiry / serial / free qty print under the item (every bill layout shows the description).
        extra = [x for x in (
            f"Batch {ln['batch_no']}" if ln.get("batch_no") else "",
            f"Exp {ln['expiry_date']}" if ln.get("expiry_date") else "",
            f"S.No. {ln['serial_no']}" if ln.get("serial_no") else "",
            f"+ {float(ln['free_qty']):g} free" if float(ln.get("free_qty") or 0) > 0 else "",
        ) if x]
        if extra:
            ln["description"] = " · ".join([x for x in [str(ln.get("description") or "").strip(), *extra] if x])
        qty = float(ln.get("qty") or 0)
        rate = float(ln.get("rate") or 0)
        disc = float(ln.get("discount_amount") or 0)
        tax = float(ln.get("tax_amount") or 0)
        line_total = float(ln.get("line_total") or 0)
        taxable = max(0.0, (qty * rate) - disc)
        if line_total <= 0 and taxable:
            line_total = taxable + tax
            ln["line_total"] = round(line_total, 2)
        sub_total += taxable if taxable else max(0.0, line_total - tax)
        tax_total += tax
        qty_total += qty

    header_total = float(h.get("total_amount") or 0)
    shipping = float(h.get("shipping_amount") or 0)
    shipping_tax_pct = float(h.get("shipping_tax_pct") or 0)
    shipping_tax_amount = float(h.get("shipping_tax_amount") or 0)
    shipping, shipping_tax_amount = _shipping_tax_amounts(
        shipping, shipping_tax_pct, shipping_tax_amount
    )
    doc_discount = float(h.get("discount_amount") or 0)
    doc_discount_pct = float(h.get("discount_pct") or 0)
    if doc_discount <= 0.009 and doc_discount_pct > 0.009 and sub_total > 0:
        doc_discount = round(sub_total * doc_discount_pct / 100.0, 2)
    round_off = float(h.get("round_off") or 0)
    if not lines and header_total:
        sub_total = header_total
    line_tax_total = round(tax_total, 2)
    tax_breakdown = tax_breakdown_from_lines(lines)
    if shipping_tax_amount > 0.009:
        tax_breakdown.append(
            {
                "tax_pct": round(shipping_tax_pct, 2),
                "taxable_amount": round(shipping, 2),
                "tax_amount": round(shipping_tax_amount, 2),
                "scope": "shipping",
            }
        )
        tax_breakdown = sorted(tax_breakdown, key=lambda x: float(x.get("tax_pct") or 0))
    tax_total = round(line_tax_total + shipping_tax_amount, 2)
    computed_grand = round(
        sub_total + line_tax_total + shipping + shipping_tax_amount - doc_discount + round_off,
        2,
    )
    grand = header_total if header_total > 0.009 else computed_grand

    tax_pct = 0.0
    if lines:
        # Prefer most common / first non-zero tax %
        for ln in lines:
            if float(ln.get("tax_pct") or 0) > 0:
                tax_pct = float(ln.get("tax_pct") or 0)
                break
    if tax_pct <= 0 and sub_total > 0.009 and tax_total > 0.009:
        tax_pct = round((tax_total / sub_total) * 100, 2)

    doc_title = _bill_title(h)
    party_name = _clean(party.get("company") or h.get("party_name"))
    party_addr = _clean(party.get("shipping_address") or party.get("address"))
    party_gst = _clean(party.get("gst_no"))
    party_state = _clean(party.get("state") or party.get("city"))
    party_phone = _clean(party.get("mobile"))
    party_email = _clean(party.get("email"))

    notes = _clean(h.get("notes"))
    doc_terms = _clean(h.get("doc_terms"))
    delivery_terms = _clean(h.get("delivery_terms"))
    if doc_terms:
        terms = doc_terms
    else:
        terms = _clean(firm.get("terms_default"))

    from app.hop_doc_numbers import format_full_doc_number

    doc_number = format_full_doc_number(
        h.get("txn_number"),
        txn_date=h.get("txn_date"),
        txn_type=h.get("txn_type"),
    )

    return _apply_print_settings(conn, workspace_id, {
        "header": {
            "id": h.get("id"),
            "source_txn_id": src_id,
            "doc_title": doc_title,
            "doc_number": doc_number,
            "doc_date": _clean(h.get("txn_date"))[:10],
            "txn_type": h.get("txn_type"),
            "status": _clean(h.get("status_text")),
            "total_amount": grand,
            "balance_amount": float(h.get("balance_amount") or 0),
            "notes": notes,
            "party_type": ptype,
        },
        "firm": {
            "name": _clean(firm.get("firm_name")) or "Your Business Name",
            "address": _clean(firm.get("address")),
            "phone": _clean(firm.get("phone")),
            "email": _clean(firm.get("email")),
            "gstin": _clean(firm.get("gstin")),
            "state": _clean(firm.get("state")),
            "pan": _clean(firm.get("pan")),
            "bank_name": _clean(firm.get("bank_name")),
            "bank_account": _clean(firm.get("bank_account")),
            "bank_ifsc": _clean(firm.get("bank_ifsc")),
            "bank_holder": _clean(firm.get("bank_holder")),
            "logo_url": _clean(firm.get("logo_url")),
            "signature_url": _clean(firm.get("signature_url")),
        },
        "party": {
            "name": party_name,
            "billing_name": _clean(party.get("billing_name")) or party_name,
            "address": party_addr,
            "gstin": party_gst,
            "state": party_state,
            "phone": party_phone,
            "email": party_email,
            "contact_person": _clean(party.get("contact_person")),
        },
        "lines": [
            {
                "line_no": ln.get("line_no"),
                "item_name": _clean(ln.get("item_name")) or "Item",
                "description": _clean(ln.get("description")),
                "hsn": _clean(ln.get("hsn")),
                "qty": float(ln.get("qty") or 0),
                "unit": _clean(ln.get("unit")) or "Pcs",
                "rate": float(ln.get("rate") or 0),
                "discount_amount": float(ln.get("discount_amount") or 0),
                "discount_pct": float(ln.get("discount_pct") or 0),
                "section_title": _clean(ln.get("section_title")),
                "tax_pct": float(ln.get("tax_pct") or 0),
                "tax_amount": float(ln.get("tax_amount") or 0),
                "cess_amount": float(ln.get("cess_amount") or 0),
                "line_total": float(ln.get("line_total") or 0),
            }
            for ln in lines
        ],
        "totals": {
            "qty": qty_total,
            "sub_total": round(sub_total, 2),
            "taxable_total": round(sub_total, 2),
            "line_tax_total": line_tax_total,
            "tax_total": round(tax_total, 2),
            "tax_pct": tax_pct,
            "tax_breakdown": tax_breakdown,
            "discount_amount": round(doc_discount, 2),
            "discount_pct": doc_discount_pct,
            "shipping_amount": round(shipping, 2),
            "shipping_tax_pct": round(shipping_tax_pct, 2),
            "shipping_tax_amount": round(shipping_tax_amount, 2),
            "round_off": round(round_off, 2),
            "grand_total": round(grand, 2),
            "amount_in_words": amount_in_words_inr(grand),
        },
        "terms": terms,
        "delivery_terms": delivery_terms,
        "lines_missing": len(lines) == 0,
        "lines_missing_hint": (
            "Item rows are not loaded yet. Import the backup again "
            "(Settings → Import old data) to fill quantity, rate, HSN and GST lines."
            if not lines
            else ""
        ),
    })


_PLURAL_FIX = {"sale invoices": "Sale Invoice", "purchase bills": "Purchase Bill", "expenses": "Expense",
               "sale orders": "Sale Order", "purchase orders": "Purchase Order"}


def _bill_title(h: dict[str, Any]) -> str:
    """Title printed on the bill. A label saved as a list name ("Sale Invoices") becomes singular."""
    label = _clean(h.get("txn_label"))
    return _PLURAL_FIX.get(label.lower(), label) or "Document"


def _with_charges(lines: list[Any], payload: dict[str, Any]) -> list[Any]:
    """Additional charges (packing, freight, labour…) become bill lines marked is_charge, each with its own GST."""
    out = list(lines or [])
    for c in payload.get("charges") or []:
        if not isinstance(c, dict):
            continue
        amt = float(c.get("amount") or 0)
        name = _clean(c.get("name"))[:60]
        if amt <= 0 or not name:
            continue
        out.append({"item_name": name, "qty": 1, "unit": "NOS", "rate": amt, "tax_pct": float(c.get("tax_pct") or 0),
                    "hsn": _clean(c.get("hsn")), "is_charge": 1, "section_title": ""})
    return out


def _tcs(payload: dict[str, Any], txn_type: int, grand: float) -> tuple[float, float]:
    if int(txn_type) not in (1, 2):  # sale invoice (we collect) / purchase bill (seller collected)
        return 0.0, 0.0
    pct = float(payload.get("tcs_pct") or 0)
    if pct < 0 or pct > 5:
        raise ValueError("TCS % must be between 0 and 5")
    return pct, round(grand * pct / 100.0, 2)


def _save_header_extras(conn, workspace_id, txn_id, payload, tcs_pct, tcs_amount, is_update):
    """PO no / PO date / time / TCS on the bill (on edit, only what was sent)."""
    sets, vals = ["tcs_pct=?", "tcs_amount=?"], [tcs_pct or None, tcs_amount or None]
    for k in ("po_number", "po_date", "txn_time"):
        if k in payload or not is_update:
            sets.append(f"{k}=?")
            vals.append(_clean(payload.get(k))[:40] or None)
    conn.execute(f"UPDATE hop_party_transactions SET {', '.join(sets)} WHERE workspace_id=? AND id=?",
                 (*vals, workspace_id, int(txn_id)))
    if "godown_id" in payload or not is_update:
        from app import hop_godowns

        conn.execute("UPDATE hop_party_transactions SET godown_id=? WHERE workspace_id=? AND id=?",
                     (hop_godowns.check(conn, workspace_id, payload.get("godown_id")), workspace_id, int(txn_id)))
    # TDS: deducted on the value before GST (purchase bill = we deduct; sale invoice = customer deducts).
    t = conn.execute("SELECT txn_type, source_txn_id, discount_amount FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                     (workspace_id, int(txn_id))).fetchone()
    pct = float(payload.get("tds_pct") or 0) if t and int(t[0]) in (1, 2) else 0.0
    if pct < 0 or pct > 30:
        raise ValueError("TDS % must be between 0 and 30")
    amt = 0.0
    if pct:
        base = conn.execute("SELECT SUM(COALESCE(line_total,0)-COALESCE(tax_amount,0)) FROM hop_txn_lines "
                            "WHERE workspace_id=? AND source_txn_id=?", (workspace_id, int(t[1]))).fetchone()[0] or 0
        amt = round(max(0.0, float(base) - float(t[2] or 0)) * pct / 100.0, 2)
    conn.execute("UPDATE hop_party_transactions SET tds_pct=?, tds_amount=? WHERE workspace_id=? AND id=?",
                 (pct or None, amt or None, workspace_id, int(txn_id)))


def _apply_print_settings(conn: sqlite3.Connection, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Settings → Print: hide amount in words / bank / signature / terms, add footer text;
    composition scheme sale invoices are a "Bill of Supply"."""
    try:
        from app import hop_app_settings

        s = hop_app_settings.get_all(conn, workspace_id)
    except Exception:
        return data
    firm, totals = data.get("firm") or {}, data.get("totals") or {}
    hid = (data.get("header") or {}).get("id")
    if hid:
        r = conn.execute("SELECT po_number, po_date, txn_time, tcs_pct, tcs_amount, tds_pct, tds_amount "
                         "FROM hop_party_transactions WHERE workspace_id=? AND id=?", (workspace_id, int(hid))).fetchone()
        if r:
            data["header"].update({"po_number": r[0], "po_date": r[1], "txn_time": r[2]})
            if r[4]:
                totals["tcs_pct"], totals["tcs_amount"] = r[3], r[4]
            if r[6]:
                totals["tds_pct"], totals["tds_amount"] = r[5], r[6]
    if not s.get("print_amount_words"):
        totals["amount_in_words"] = ""
    if not s.get("print_bank"):
        for k in ("bank_name", "bank_account", "bank_ifsc", "bank_holder"):
            firm[k] = ""
    if not s.get("print_signature"):
        firm["signature_url"] = ""
    if not s.get("print_terms"):
        data["terms"] = ""
        data["delivery_terms"] = ""
    data["footer"] = s.get("print_footer") or ""
    cur = s.get("currency_symbol") or "₹"
    data["print"] = {"currency": "" if cur == "none" else cur, "header": s.get("print_header") or "",
                     "hsn_column": bool(s.get("print_hsn_column", True)),
                     "margin_mm": {"narrow": 5, "wide": 15}.get(s.get("print_margin") or "normal", 10),
                     "paper": s.get("print_paper") or "a4", "copies": int(s.get("print_copies") or 1),
                     "date_format": s.get("date_format") or "dd-mm-yyyy", "decimals": int(s.get("amount_decimals") or 2),
                     "copy_labels": ["Original for Recipient", "Duplicate for Transporter", "Triplicate for Supplier"]
                     [: int(s.get("print_copies") or 1)]}
    if hid:
        try:
            from app import hop_share

            t = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                             (workspace_id, int(hid))).fetchone()
            data["upi"] = hop_share.bill_upi(conn, workspace_id, dict(t)) if t else None
            td = dict(t) if t else {}
            if td.get("irn"):   # e-invoice: IRN, Ack, E-Way Bill no. and the signed QR from the IRP
                qr = td.get("einv_qr") or ""
                data["einvoice"] = {"irn": td.get("irn"), "ack_no": td.get("ack_no"), "ack_date": td.get("ack_date"),
                                    "eway_bill_no": td.get("eway_bill_no"),
                                    "qr_png": hop_share.qr_png_base64(qr) if qr else None}
            elif td.get("eway_bill_no"):
                data["einvoice"] = {"eway_bill_no": td.get("eway_bill_no")}
        except Exception:
            data["upi"] = None
    try:
        inter = _is_interstate(conn, workspace_id, data)
        data["gst_split"] = gst_split_rows(data, inter)
        data["interstate"] = inter
        data["hsn_summary"] = hsn_summary(data, inter) if s.get("print_hsn_summary") else []
    except Exception:
        data["gst_split"], data["hsn_summary"] = [], []
    if hid:
        try:
            from app import hop_loyalty

            used = hop_loyalty.used_on_bill(conn, workspace_id, int(hid))
            data["loyalty_note"] = (f"Discount includes {used['points']:g} loyalty points (₹{used['value']:,.2f})"
                                    if used else "")
        except Exception:
            data["loyalty_note"] = ""
    header = data.get("header") or {}
    if s.get("composition_scheme") and int(header.get("txn_type") or 0) == 1:
        header["doc_title"] = "Bill of Supply"
        header["composition_note"] = "Composition taxable person, not eligible to collect tax on supplies."
    return data


def _apply_line_settings(conn, workspace_id, txn_type, raw_lines, payload):
    """Settings → Transaction "rates include GST" and Taxes "composition scheme" applied to typed lines."""
    try:
        from app import hop_app_settings

        s = hop_app_settings.get_all(conn, workspace_id)
    except Exception:
        return raw_lines
    inclusive = payload.get("rates_include_tax")
    inclusive = s.get("tax_inclusive_rates") if inclusive is None else inclusive in (True, 1, "1", "true")
    composition = bool(s.get("composition_scheme")) and int(txn_type) in (1, 21, 27, 83, 65, 30, 82)
    if not inclusive and not composition:
        return raw_lines
    out = []
    for ln in raw_lines or []:
        if not isinstance(ln, dict):
            out.append(ln)
            continue
        ln = dict(ln)
        tax = float(ln.get("tax_pct") or 0)
        if composition:
            ln["tax_pct"] = 0  # composition dealers don't charge GST on sales
        elif inclusive and tax > 0 and int(txn_type) not in (3, 4, 7, 81):
            ln["rate"] = round(float(ln.get("rate") or 0) / (1 + tax / 100.0), 4)
        out.append(ln)
    return out


def _apply_round_setting(conn, workspace_id, payload, sub_total, line_tax_total):
    """Settings → Transaction "round off to nearest ₹1" when the user didn't type a round-off."""
    if abs(float(payload.get("round_off") or 0)) > 0.0001:
        return payload
    try:
        from app import hop_app_settings

        if not hop_app_settings.get(conn, workspace_id, "auto_round_off"):
            return payload
    except Exception:
        return payload
    base = _finalize_manual_doc_totals(sub_total, line_tax_total, {**payload, "round_off": 0})["grand"]
    return {**payload, "round_off": round(round(base) - base, 2)}


def next_manual_source_txn_id(conn: sqlite3.Connection, workspace_id: str) -> int:
    """Negative ids for Nexora-created docs — Vyapar imports use positive source_txn_id."""
    row = conn.execute(
        """
        SELECT MIN(source_txn_id) AS m FROM hop_party_transactions
        WHERE workspace_id=? AND source_txn_id < 0
        """,
        (workspace_id,),
    ).fetchone()
    m = int((dict(row) if row else {}).get("m") or 0)
    return (m if m < 0 else 0) - 1


def next_manual_doc_number(
    conn: sqlite3.Connection,
    workspace_id: str,
    txn_type: int,
    txn_date: str,
) -> str:
    from app.hop_doc_numbers import HOP_DOC_PREFIX_BY_TYPE, indian_fy_label

    prefix = HOP_DOC_PREFIX_BY_TYPE.get(int(txn_type))
    try:
        from app import hop_app_settings

        custom = hop_app_settings.get(conn, workspace_id, f"prefix_{30 if int(txn_type) == 82 else int(txn_type)}")
        if custom:
            prefix = str(custom)
    except Exception:
        pass
    fy = indian_fy_label(txn_date)
    if not prefix or not fy:
        return str(int(txn_type))
    pattern = f"{prefix}/{fy}/%"
    rows = conn.execute(
        """
        SELECT txn_number FROM hop_party_transactions
        WHERE workspace_id=? AND txn_type=? AND txn_number LIKE ?
        """,
        (workspace_id, int(txn_type), pattern),
    ).fetchall()
    max_serial = 0
    for r in rows:
        num = str(dict(r).get("txn_number") or "")
        tail = num.rsplit("/", 1)[-1]
        if tail.isdigit():
            max_serial = max(max_serial, int(tail))
    return f"{prefix}/{fy}/{max_serial + 1}"


def _parse_manual_doc_lines(raw_lines: list[Any]) -> tuple[list[dict[str, Any]], float, float]:
    computed_lines: list[dict[str, Any]] = []
    sub_total = 0.0
    tax_total = 0.0
    for i, line in enumerate(raw_lines or []):
        if not isinstance(line, dict):
            continue
        qty = float(line.get("qty") or 0)
        rate = float(line.get("rate") or 0)
        if qty <= 0 or rate < 0:
            continue
        tax_pct = float(line.get("tax_pct") or 0)
        disc_pct = float(line.get("discount_pct") or 0)
        disc = float(line.get("discount_amount") or 0)
        amounts = compute_line_amounts(qty, rate, disc_pct, disc, tax_pct)
        taxable = amounts["taxable"]
        tax_amt = amounts["tax_amount"]
        line_total = amounts["line_total"]
        # Additional cess (on top of GST) — kept inside the tax, split out in GST reports.
        cess_pct = float(line.get("cess_pct") or 0)
        if cess_pct < 0 or cess_pct > 300:
            raise ValueError("Cess % must be between 0 and 300")
        cess_amt = round(taxable * cess_pct / 100.0, 2) if cess_pct else 0.0
        exp = _clean(line.get("expiry_date"))
        if exp and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp):
            raise ValueError(f"{_clean(line.get('item_name')) or 'Item'}: expiry date must be YYYY-MM-DD")
        tax_amt = round(tax_amt + cess_amt, 2)
        line_total = round(line_total + cess_amt, 2)
        disc = amounts["discount_amount"]
        sub_total += taxable
        tax_total += tax_amt
        computed_lines.append(
            {
                "line_no": i + 1,
                "item_name": _clean(line.get("item_name")) or "Item",
                "item_code": _clean(line.get("item_code")),
                "product_id": int(line["product_id"]) if str(line.get("product_id") or "").isdigit() else None,
                "description": _clean(line.get("description")),
                "hsn": _clean(line.get("hsn")),
                "qty": qty,
                "unit": _clean(line.get("unit")) or "Pcs",
                "rate": rate,
                "discount_pct": disc_pct,
                "discount_amount": disc,
                "section_title": _clean(line.get("section_title")),
                "is_charge": 1 if line.get("is_charge") else 0,
                "free_qty": float(line.get("free_qty") or 0),
                "cess_pct": cess_pct,
                "cess_amount": cess_amt,
                "batch_no": _clean(line.get("batch_no"))[:40],
                "expiry_date": _clean(line.get("expiry_date"))[:10],
                "serial_no": _clean(line.get("serial_no"))[:300],
                "tax_pct": tax_pct,
                "tax_amount": tax_amt,
                "line_total": line_total,
            }
        )
    return computed_lines, round(sub_total, 2), round(tax_total, 2)


def _finalize_manual_doc_totals(
    sub_total: float,
    line_tax_total: float,
    payload: dict[str, Any],
) -> dict[str, float]:
    shipping = float(payload.get("shipping_amount") or 0)
    shipping_tax_pct = float(payload.get("shipping_tax_pct") or 0)
    shipping, shipping_tax_amount = _shipping_tax_amounts(shipping, shipping_tax_pct, 0)
    doc_discount = float(payload.get("discount_amount") or 0)
    doc_discount_pct = float(payload.get("discount_pct") or 0)
    if doc_discount <= 0.009 and doc_discount_pct > 0.009 and sub_total > 0:
        doc_discount = round(sub_total * doc_discount_pct / 100.0, 2)
    round_off = float(payload.get("round_off") or 0)
    tax_total = round(line_tax_total + shipping_tax_amount, 2)
    grand = round(
        sub_total + line_tax_total + shipping + shipping_tax_amount - doc_discount + round_off,
        2,
    )
    return {
        "shipping": shipping,
        "shipping_tax_pct": shipping_tax_pct,
        "shipping_tax_amount": shipping_tax_amount,
        "doc_discount": doc_discount,
        "doc_discount_pct": doc_discount_pct,
        "round_off": round_off,
        "line_tax_total": line_tax_total,
        "tax_total": tax_total,
        "grand": grand,
    }


def _group_lines_for_edit(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_title = ""
    for ln in lines:
        title = _clean(ln.get("section_title")) or "Items"
        if not sections or title != current_title:
            sections.append({"title": title, "lines": []})
            current_title = title
        sections[-1]["lines"].append(
            {
                "item_name": ln.get("item_name") or "",
                "description": ln.get("description") or "",
                "qty": ln.get("qty"),
                "unit": ln.get("unit") or "MTR",
                "rate": ln.get("rate"),
                "discount_pct": ln.get("discount_pct") or 0,
                "tax_pct": ln.get("tax_pct") or 0,
                "hsn": ln.get("hsn") or "",
                "section_title": title,
                "free_qty": ln.get("free_qty") or 0,
            }
        )
    return sections


# Nexora-created docs (source_txn_id < 0). party_kind: customer | vendor | either
MANUAL_TXN_META: dict[int, dict[str, Any]] = {
    27: {"label": "Estimate / Quotation", "party_kind": "customer"},
    83: {"label": "Proforma Invoice", "party_kind": "customer"},
    1: {"label": "Sale Invoice", "party_kind": "customer", "balance_due": True},
    3: {"label": "Payment-In", "party_kind": "customer", "payment": True},
    65: {"label": "Sale Order", "party_kind": "customer"},
    30: {"label": "Delivery Challan", "party_kind": "customer"},
    82: {"label": "Delivery Challan", "party_kind": "customer"},
    21: {"label": "Sale Return / Credit Note", "party_kind": "customer"},
    2: {"label": "Purchase Bill", "party_kind": "vendor", "balance_due": True},
    4: {"label": "Payment-Out", "party_kind": "vendor", "payment": True},
    7: {"label": "Expense", "party_kind": "either", "payment": True},
    66: {"label": "Purchase Order", "party_kind": "vendor"},
    16: {"label": "Purchase Return / Debit Note", "party_kind": "vendor"},
    81: {"label": "Journal Entry", "party_kind": "either", "payment": True},
    29: {"label": "Other Income", "party_kind": "either", "payment": True},
}
MANUAL_TXN_TYPES = frozenset(MANUAL_TXN_META)


def get_party_transaction_edit_data(
    conn: sqlite3.Connection,
    workspace_id: str,
    party_txn_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM hop_party_transactions
        WHERE workspace_id=? AND id=?
        """,
        (workspace_id, int(party_txn_id)),
    ).fetchone()
    if not row:
        return None
    h = dict(row)
    txn_type = int(h.get("txn_type") or 0)
    source_txn_id = int(h.get("source_txn_id") or 0)
    if txn_type not in MANUAL_TXN_TYPES or source_txn_id >= 0:
        return None
    lines = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM hop_txn_lines
            WHERE workspace_id=? AND source_txn_id=?
            ORDER BY line_no ASC, id ASC
            """,
            (workspace_id, source_txn_id),
        ).fetchall()
    ]
    charges = [{"name": _clean(ln.get("item_name")), "amount": float(ln.get("rate") or 0),
                "tax_pct": float(ln.get("tax_pct") or 0)} for ln in lines if ln.get("is_charge")]
    lines = [ln for ln in lines if not ln.get("is_charge")]
    flat_lines = [
        {
            "item_name": _clean(ln.get("item_name")),
            "item_code": _clean(ln.get("item_code")),
            "product_id": ln.get("product_id"),
            "description": _clean(ln.get("description")),
            "qty": float(ln.get("qty") or 0),
            "unit": _clean(ln.get("unit")) or "MTR",
            "rate": float(ln.get("rate") or 0),
            "discount_pct": float(ln.get("discount_pct") or 0),
            "tax_pct": float(ln.get("tax_pct") or 0),
            "hsn": _clean(ln.get("hsn")),
            "section_title": _clean(ln.get("section_title")),
            "free_qty": float(ln.get("free_qty") or 0),
            "cess_pct": float(ln.get("cess_pct") or 0),
            "batch_no": ln.get("batch_no") or "",
            "expiry_date": ln.get("expiry_date") or "",
            "serial_no": ln.get("serial_no") or "",
        }
        for ln in lines
    ]
    label = _clean(h.get("txn_label"))
    is_commercial = txn_type == 27 and (
        "commercial" in label.lower()
        or any(_clean(ln.get("section_title")) for ln in lines)
    )
    from app import hop_loyalty

    _loy = hop_loyalty.used_on_bill(conn, workspace_id, int(party_txn_id))
    out: dict[str, Any] = {
        "party_txn_id": int(h.get("id") or 0),
        "source_txn_id": source_txn_id,
        "txn_type": txn_type,
        "txn_label": label,
        "txn_number": _clean(h.get("txn_number")),
        "txn_date": _clean(h.get("txn_date"))[:10],
        "customer_id": int(h.get("party_id") or 0),
        "notes": _clean(h.get("notes")),
        "doc_terms": _clean(h.get("doc_terms")),
        "delivery_terms": _clean(h.get("delivery_terms")),
        "shipping_amount": float(h.get("shipping_amount") or 0),
        "shipping_tax_pct": float(h.get("shipping_tax_pct") or 0),
        "shipping_tax_amount": float(h.get("shipping_tax_amount") or 0),
        # Points used on this bill are shown on their own (not inside the discount).
        "discount_amount": round(float(h.get("discount_amount") or 0) - float((_loy or {}).get("value") or 0), 2),
        "discount_pct": float(h.get("discount_pct") or 0),
        "redeem_points": float((_loy or {}).get("points") or 0),
        "round_off": float(h.get("round_off") or 0),
        "mode": "commercial" if is_commercial else "standard",
        "lines": flat_lines,
        "party_type": _clean(h.get("party_type")),
        "party_id": int(h.get("party_id") or 0) or None,
        "received_amount": h.get("received_amount"),
        "payment_mode": _clean(h.get("payment_mode")),
        "payment_account_id": h.get("payment_account_id"),
        "payment_ref": _clean(h.get("payment_ref")),
        "cheque_date": _clean(h.get("cheque_date")),
        "expense_category_id": h.get("expense_category_id"),
        "balance_amount": float(h.get("balance_amount") or 0),
        "unused_amount": h.get("unused_amount"),
        "total_amount": float(h.get("total_amount") or 0),
        "linked_from_txn_id": h.get("linked_from_txn_id"),
        "place_of_supply": h.get("place_of_supply"),
        "charges": charges,
        "po_number": h.get("po_number"),
        "po_date": h.get("po_date"),
        "txn_time": h.get("txn_time"),
        "tcs_pct": h.get("tcs_pct"),
        "tds_pct": h.get("tds_pct"),
        "godown_id": h.get("godown_id"),
        "reverse_charge": bool(h.get("reverse_charge")),
        "converted_to_txn_id": h.get("converted_to_txn_id"),
    }
    from app import hop_money

    out["links"] = hop_money.links_of(conn, workspace_id, int(h["id"]))
    if txn_type in hop_money.PAYMENT_FOR:
        out["allocations"] = [{"txn_id": l["bill_txn_id"], "amount": l["amount"]} for l in out["links"]
                              if int(l["payment_txn_id"]) == int(h["id"])]
    if is_commercial:
        out["sections"] = _group_lines_for_edit(lines)
    return out


def create_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a Nexora manual sales/purchase document with line items — preview + PDF ready."""
    from app.hop_ops import get_customer, get_vendor

    txn_type = int(payload.get("txn_type") or 0)
    meta = MANUAL_TXN_META.get(txn_type)
    if not meta:
        raise ValueError(f"Unsupported txn_type {txn_type} for manual create")

    party_kind = meta["party_kind"]
    party_type = _clean(payload.get("party_type")).lower()
    if party_type not in ("customer", "vendor"):
        party_type = "vendor" if party_kind == "vendor" else "customer"
    if party_kind == "customer" and party_type != "customer":
        raise ValueError("This document requires a customer")
    if party_kind == "vendor" and party_type != "vendor":
        raise ValueError("This document requires a vendor")

    party_id = int(payload.get("party_id") or payload.get("customer_id") or payload.get("vendor_id") or 0)
    from app import hop_money

    category = hop_money.expense_category(conn, workspace_id, payload.get("expense_category_id")) if txn_type in (7, 29) else None
    if txn_type in (7, 29) and payload.get("expense_category_id") and not category:
        raise ValueError("Category not found")
    if not party_id and category:
        # Vyapar-style expense: a category (Rent, Salary…), no party needed.
        party_type, party = "expense", {"company": category["name"]}
    elif not party_id:
        raise ValueError("Choose a party" + (" or a category" if txn_type in (7, 29) else ""))
    elif party_type == "vendor":
        party = get_vendor(conn, workspace_id, party_id)
        if not party:
            raise ValueError("vendor not found")
    else:
        party = get_customer(conn, workspace_id, party_id)
        if not party:
            raise ValueError("customer not found")

    txn_date = _clean(payload.get("txn_date"))
    if not txn_date:
        from datetime import datetime, timezone

        txn_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    from app import hop_phase3_ext

    hop_phase3_ext.assert_period_open(conn, workspace_id, txn_date)

    default_label = meta["label"]
    txn_label = _clean(payload.get("txn_label")) or default_label
    notes = _clean(payload.get("notes"))

    raw_lines = _with_charges(payload.get("lines") or [], payload)
    if (not isinstance(raw_lines, list) or not raw_lines) and meta.get("payment"):
        amount = float(payload.get("total_amount") or payload.get("amount") or 0)
        if amount <= 0:
            raise ValueError("amount is required for payment documents")
        raw_lines = [{"item_name": default_label, "qty": 1, "unit": "NOS", "rate": amount, "tax_pct": 0}]
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("At least one line item is required")

    computed_lines, sub_total, line_tax_total = _parse_manual_doc_lines(
        _apply_line_settings(conn, workspace_id, txn_type, raw_lines, payload))
    if not computed_lines:
        raise ValueError("At least one valid line item is required")
    from app import hop_loyalty

    payload = dict(payload)
    hop_loyalty.apply_redeem(conn, workspace_id, txn_type, party_id if party_type == "customer" else 0, payload, sub_total)

    totals = _finalize_manual_doc_totals(sub_total, line_tax_total, _apply_round_setting(
        conn, workspace_id, payload, sub_total, line_tax_total))
    shipping = totals["shipping"]
    shipping_tax_pct = totals["shipping_tax_pct"]
    shipping_tax_amount = totals["shipping_tax_amount"]
    doc_discount = totals["doc_discount"]
    doc_discount_pct = totals["doc_discount_pct"]
    round_off = totals["round_off"]
    grand = totals["grand"]
    tcs_pct, tcs_amount = _tcs(payload, txn_type, grand)
    grand = round(grand + tcs_amount, 2)
    balance = grand if meta.get("balance_due") else 0.0
    status_text = "Unpaid" if meta.get("balance_due") else ("Paid" if meta.get("payment") else "Open")

    doc_terms = ""
    delivery_terms = ""
    if "doc_terms" in payload or "terms" in payload:
        doc_terms = _clean(payload.get("doc_terms") or payload.get("terms"))
    else:
        firm = get_firm_profile(conn, workspace_id)
        doc_terms = _clean(firm.get("terms_default"))
    if "delivery_terms" in payload:
        delivery_terms = _clean(payload.get("delivery_terms"))
    elif not delivery_terms:
        firm = get_firm_profile(conn, workspace_id)
        delivery_terms = _clean(firm.get("delivery_terms"))

    source_txn_id = next_manual_source_txn_id(conn, workspace_id)
    txn_number = _clean(payload.get("txn_number")) or next_manual_doc_number(
        conn, workspace_id, txn_type, txn_date
    )
    party_name = _clean(party.get("company"))

    cur = conn.execute(
        """
        INSERT INTO hop_party_transactions (
            workspace_id, party_type, party_id, party_name, source_txn_id,
            txn_type, txn_label, txn_number, txn_date, total_amount,
            balance_amount, status_text, notes, shipping_amount, shipping_tax_pct,
            shipping_tax_amount, discount_amount, discount_pct, round_off,
            doc_terms, delivery_terms, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            workspace_id,
            party_type,
            party_id or None,
            party_name,
            source_txn_id,
            txn_type,
            txn_label,
            txn_number,
            txn_date,
            grand,
            balance,
            status_text,
            notes,
            shipping,
            shipping_tax_pct,
            shipping_tax_amount,
            doc_discount,
            doc_discount_pct,
            round_off,
            doc_terms,
            delivery_terms,
        ),
    )
    party_txn_id = int(cur.lastrowid)
    replace_txn_lines(conn, workspace_id, source_txn_id, computed_lines)
    if category:
        conn.execute("UPDATE hop_party_transactions SET expense_category_id=? WHERE id=?", (category["id"], party_txn_id))
    # Due date: explicit payload, else party credit_days from txn_date (sale/purchase bills).
    due_date = _clean(payload.get("due_date"))[:10] or None
    if not due_date and meta.get("balance_due") and party_id and party_type in ("customer", "vendor"):
        from app import hop_settings

        from app import hop_app_settings as _set

        due_date = hop_settings.compute_due_date(
            txn_date, hop_settings.party_credit_days(conn, workspace_id, party_type, party_id)
            or (int(_set.get(conn, workspace_id, "default_due_days") or 0) or None)
        )
    if due_date:
        conn.execute("UPDATE hop_party_transactions SET due_date=? WHERE id=?", (due_date, party_txn_id))
    _save_header_extras(conn, workspace_id, party_txn_id, payload, tcs_pct, tcs_amount, False)
    hop_loyalty.record(conn, workspace_id, party_txn_id, payload)
    hop_money.post_document(conn, workspace_id, party_txn_id, payload)
    conn.commit()
    after = dict(conn.execute("SELECT balance_amount, status_text FROM hop_party_transactions WHERE id=?",
                              (party_txn_id,)).fetchone())
    balance = float(after.get("balance_amount") or 0)

    return {
        "party_txn_id": party_txn_id,
        "source_txn_id": source_txn_id,
        "txn_number": txn_number,
        "txn_type": txn_type,
        "txn_label": txn_label,
        "txn_date": txn_date,
        "due_date": due_date,
        "total_amount": grand,
        "balance_amount": balance,
        "party_type": party_type,
        "party_id": party_id,
        "customer_id": party_id if party_type == "customer" else None,
        "party_name": party_name,
    }


def update_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    party_txn_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Update a Nexora-created manual document (source_txn_id < 0)."""
    from app.hop_ops import get_customer, get_vendor

    row = conn.execute(
        """
        SELECT * FROM hop_party_transactions
        WHERE workspace_id=? AND id=?
        """,
        (workspace_id, int(party_txn_id)),
    ).fetchone()
    if not row:
        raise ValueError("Transaction not found")
    h = dict(row)
    txn_type = int(h.get("txn_type") or 0)
    source_txn_id = int(h.get("source_txn_id") or 0)
    meta = MANUAL_TXN_META.get(txn_type)
    if not meta or source_txn_id >= 0:
        raise ValueError("Only Nexora-created documents can be edited")

    from app import hop_money

    party_type = _clean(payload.get("party_type") or h.get("party_type")).lower() or "customer"
    party_id = int(payload.get("party_id") or payload.get("customer_id") or payload.get("vendor_id") or h.get("party_id") or 0)
    cat_id = payload.get("expense_category_id") if "expense_category_id" in payload else h.get("expense_category_id")
    category = hop_money.expense_category(conn, workspace_id, cat_id) if txn_type in (7, 29) else None
    if txn_type in (7, 29) and "party_id" in payload and not payload.get("party_id") and category:
        party_id = 0
    if not party_id and category:
        party_type, party = "expense", {"company": category["name"]}
    elif not party_id:
        raise ValueError("Choose a party" + (" or a category" if txn_type in (7, 29) else ""))
    elif party_type == "vendor":
        party = get_vendor(conn, workspace_id, party_id)
        if not party:
            raise ValueError("vendor not found")
    else:
        party = get_customer(conn, workspace_id, party_id)
        if not party:
            raise ValueError("customer not found")

    txn_date = _clean(payload.get("txn_date")) or _clean(h.get("txn_date"))[:10]
    default_label = meta["label"]
    txn_label = _clean(payload.get("txn_label")) or _clean(h.get("txn_label")) or default_label
    notes = _clean(payload.get("notes")) if "notes" in payload else _clean(h.get("notes"))

    raw_lines = _with_charges(payload.get("lines") or [], payload)
    if (not isinstance(raw_lines, list) or not raw_lines) and meta.get("payment"):
        amount = float(payload.get("total_amount") or payload.get("amount") or h.get("total_amount") or 0)
        if amount <= 0:
            raise ValueError("amount is required for payment documents")
        raw_lines = [{"item_name": default_label, "qty": 1, "unit": "NOS", "rate": amount, "tax_pct": 0}]
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("At least one line item is required")
    computed_lines, sub_total, line_tax_total = _parse_manual_doc_lines(
        _apply_line_settings(conn, workspace_id, txn_type, raw_lines, payload))
    if not computed_lines:
        raise ValueError("At least one valid line item is required")
    from app import hop_loyalty

    payload = dict(payload)
    if "redeem_points" not in payload:
        # Edit forms that do not know points keep the points already used on this bill.
        old = hop_loyalty.used_on_bill(conn, workspace_id, int(party_txn_id))
        payload["redeem_points"] = old["points"] if old else 0
    hop_loyalty.apply_redeem(conn, workspace_id, txn_type, party_id if party_type == "customer" else 0, payload,
                             sub_total, int(party_txn_id))

    totals = _finalize_manual_doc_totals(sub_total, line_tax_total, _apply_round_setting(
        conn, workspace_id, payload, sub_total, line_tax_total))
    shipping = totals["shipping"]
    shipping_tax_pct = totals["shipping_tax_pct"]
    shipping_tax_amount = totals["shipping_tax_amount"]
    doc_discount = totals["doc_discount"]
    doc_discount_pct = totals["doc_discount_pct"]
    round_off = totals["round_off"]
    grand = totals["grand"]
    tcs_pct, tcs_amount = _tcs(payload, txn_type, grand)
    grand = round(grand + tcs_amount, 2)
    balance = grand if meta.get("balance_due") else 0.0

    if "doc_terms" in payload or "terms" in payload:
        doc_terms = _clean(payload.get("doc_terms") or payload.get("terms"))
    else:
        doc_terms = _clean(h.get("doc_terms"))
    if "delivery_terms" in payload:
        delivery_terms = _clean(payload.get("delivery_terms"))
    else:
        delivery_terms = _clean(h.get("delivery_terms"))
    txn_number = _clean(payload.get("txn_number")) or _clean(h.get("txn_number"))
    party_name = _clean(party.get("company"))

    conn.execute(
        """
        UPDATE hop_party_transactions SET
            party_type=?, party_id=?, party_name=?, txn_label=?, txn_number=?, txn_date=?,
            total_amount=?, balance_amount=?, notes=?, shipping_amount=?, shipping_tax_pct=?,
            shipping_tax_amount=?, discount_amount=?, discount_pct=?, round_off=?,
            doc_terms=?, delivery_terms=?, updated_at=datetime('now')
        WHERE workspace_id=? AND id=?
        """,
        (
            party_type,
            party_id or None,
            party_name,
            txn_label,
            txn_number,
            txn_date,
            grand,
            balance,
            notes,
            shipping,
            shipping_tax_pct,
            shipping_tax_amount,
            doc_discount,
            doc_discount_pct,
            round_off,
            doc_terms,
            delivery_terms,
            workspace_id,
            int(party_txn_id),
        ),
    )
    replace_txn_lines(conn, workspace_id, source_txn_id, computed_lines)
    conn.execute("UPDATE hop_party_transactions SET expense_category_id=? WHERE id=?",
                 (category["id"] if category else None, int(party_txn_id)))
    if "due_date" in payload:
        due_date = _clean(payload.get("due_date"))[:10] or None
        conn.execute("UPDATE hop_party_transactions SET due_date=? WHERE id=?", (due_date, int(party_txn_id)))
    _save_header_extras(conn, workspace_id, int(party_txn_id), payload, tcs_pct, tcs_amount, True)
    hop_loyalty.record(conn, workspace_id, int(party_txn_id), payload)
    hop_money.post_document(conn, workspace_id, int(party_txn_id), payload, is_update=True)
    conn.commit()
    balance = float(conn.execute("SELECT balance_amount FROM hop_party_transactions WHERE id=?",
                                 (int(party_txn_id),)).fetchone()[0] or 0)

    return {
        "party_txn_id": int(party_txn_id),
        "source_txn_id": source_txn_id,
        "txn_number": txn_number,
        "txn_type": txn_type,
        "txn_label": txn_label,
        "txn_date": txn_date,
        "total_amount": grand,
        "balance_amount": balance,
        "party_type": party_type,
        "party_id": party_id,
        "customer_id": party_id if party_type == "customer" else None,
        "party_name": party_name,
    }


def delete_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    party_txn_id: int,
) -> dict[str, Any]:
    """Soft-delete a Nexora-created manual document (moves to recycle bin)."""
    from app import hop_phase3_ext

    return hop_phase3_ext.soft_delete_document(conn, workspace_id, int(party_txn_id))


# ---------------- GST split (CGST + SGST or IGST) and HSN summary for the printed bill ----------------
_PURCHASE_SIDE = {2, 7, 23, 28, 81}


def _is_interstate(conn: sqlite3.Connection, workspace_id: str, data: dict[str, Any]) -> bool | None:
    """True = IGST, False = CGST + SGST, None = states unknown (bill just says "GST").
    Place of supply on the bill wins; otherwise the buyer's state."""
    from app.hop_einvoice import _state

    firm, party, header = data.get("firm") or {}, data.get("party") or {}, data.get("header") or {}
    firm_st = _state(_clean(firm.get("gstin")).upper(), firm.get("state") or "")
    party_st = _state(_clean(party.get("gstin")).upper(), party.get("state") or "")
    pos = ""
    hid = header.get("id")
    if hid:
        try:
            r = conn.execute("SELECT place_of_supply FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                             (workspace_id, int(hid))).fetchone()
            pos = _clean(r[0]) if r else ""
        except Exception:
            pos = ""
    if pos and not pos[:2].isdigit():
        pos = _state("", pos)
    pos = pos[:2]
    purchase = int(header.get("txn_type") or 0) in _PURCHASE_SIDE
    seller, buyer = (party_st, firm_st) if purchase else (firm_st, party_st)
    supply = pos or buyer
    if not seller or not supply:
        return None
    return seller != supply


def _pct_txt(p: float) -> str:
    return (f"{p:.2f}".rstrip("0").rstrip(".")) or "0"


def gst_split_rows(data: dict[str, Any], inter: bool | None) -> list[dict[str, Any]]:
    """Tax rows for the totals box: IGST @ 5% / CGST @ 2.5% + SGST @ 2.5% / Cess."""
    cess_by_pct: dict[float, float] = {}
    for ln in data.get("lines") or []:
        c = float(ln.get("cess_amount") or 0)
        if c:
            k = round(float(ln.get("tax_pct") or 0), 2)
            cess_by_pct[k] = cess_by_pct.get(k, 0.0) + c
    rows: list[dict[str, Any]] = []
    cess_total = 0.0
    for b in (data.get("totals") or {}).get("tax_breakdown") or []:
        pct = round(float(b.get("tax_pct") or 0), 2)
        cess = round(cess_by_pct.get(pct, 0.0), 2)
        amt = round(float(b.get("tax_amount") or 0) - cess, 2)
        cess_total += cess
        if amt <= 0.009:
            continue
        if inter is True:
            rows.append({"label": f"IGST @ {_pct_txt(pct)}%", "amount": amt})
        elif inter is False:
            half = round(amt / 2, 2)
            rows.append({"label": f"CGST @ {_pct_txt(pct / 2)}%", "amount": half})
            rows.append({"label": f"SGST @ {_pct_txt(pct / 2)}%", "amount": round(amt - half, 2)})
        else:
            rows.append({"label": f"GST @ {_pct_txt(pct)}%", "amount": amt})
    if cess_total > 0.009:
        rows.append({"label": "Cess", "amount": round(cess_total, 2)})
    return rows


def hsn_summary(data: dict[str, Any], inter: bool | None) -> list[dict[str, Any]]:
    """One row per HSN + GST rate: taxable value and the tax split."""
    groups: dict[tuple[str, float], dict[str, float]] = {}
    for ln in data.get("lines") or []:
        pct = round(float(ln.get("tax_pct") or 0), 2)
        key = (_clean(ln.get("hsn")) or "—", pct)
        g = groups.setdefault(key, {"taxable": 0.0, "tax": 0.0, "cess": 0.0})
        tax, cess = float(ln.get("tax_amount") or 0), float(ln.get("cess_amount") or 0)
        g["taxable"] += float(ln.get("line_total") or 0) - tax
        g["tax"] += tax - cess
        g["cess"] += cess
    out = []
    for (hsn, pct), g in sorted(groups.items()):
        tax = round(g["tax"], 2)
        half = round(tax / 2, 2)
        igst = tax if inter is True else 0.0
        cgst = half if inter is False else 0.0
        sgst = round(tax - half, 2) if inter is False else 0.0
        out.append({"hsn": hsn, "tax_pct": pct, "taxable": round(g["taxable"], 2), "cgst": cgst, "sgst": sgst,
                    "igst": igst, "gst": tax if inter is None else 0.0, "cess": round(g["cess"], 2),
                    "total_tax": round(tax + g["cess"], 2)})
    return out
