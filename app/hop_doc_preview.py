"""HoP document preview payloads (Vyapar-style Estimate / Invoice / Bill)."""

from __future__ import annotations

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
            terms_default=COALESCE(NULLIF(excluded.terms_default,''), hop_firm_profile.terms_default),
            delivery_terms=COALESCE(NULLIF(excluded.delivery_terms,''), hop_firm_profile.delivery_terms),
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
        merged["firm_name"] = "House of Prizm"
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
                tax_pct, tax_amount, line_total,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
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
        "firm_name": "House of Prizm",
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
        "terms_default": "Thanks for doing business with us!",
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

    doc_title = _clean(h.get("txn_label")) or "Document"
    party_name = _clean(party.get("company") or h.get("party_name"))
    party_addr = _clean(party.get("shipping_address") or party.get("address"))
    party_gst = _clean(party.get("gst_no"))
    party_state = _clean(party.get("state") or party.get("city"))
    party_phone = _clean(party.get("mobile"))
    party_email = _clean(party.get("email"))

    notes = _clean(h.get("notes"))
    doc_terms = _clean(h.get("doc_terms"))
    delivery_terms = _clean(h.get("delivery_terms"))
    terms = doc_terms or _clean(firm.get("terms_default")) or "Thanks for doing business with us!"

    from app.hop_doc_numbers import format_full_doc_number

    doc_number = format_full_doc_number(
        h.get("txn_number"),
        txn_date=h.get("txn_date"),
        txn_type=h.get("txn_type"),
    )

    return {
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
            "name": _clean(firm.get("firm_name")) or "House of Prizm",
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
            "Item rows are not loaded yet. Re-import your Vyapar .vyb backup "
            "(Settings → Import from Vyapar) to fill quantity, rate, HSN and GST lines."
            if not lines
            else ""
        ),
    }


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
        disc = amounts["discount_amount"]
        sub_total += taxable
        tax_total += tax_amt
        computed_lines.append(
            {
                "line_no": i + 1,
                "item_name": _clean(line.get("item_name")) or "Item",
                "item_code": _clean(line.get("item_code")),
                "description": _clean(line.get("description")),
                "hsn": _clean(line.get("hsn")),
                "qty": qty,
                "unit": _clean(line.get("unit")) or "Pcs",
                "rate": rate,
                "discount_pct": disc_pct,
                "discount_amount": disc,
                "section_title": _clean(line.get("section_title")),
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
            }
        )
    return sections


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
    if txn_type not in (27, 83) or source_txn_id >= 0:
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
    flat_lines = [
        {
            "item_name": _clean(ln.get("item_name")),
            "description": _clean(ln.get("description")),
            "qty": float(ln.get("qty") or 0),
            "unit": _clean(ln.get("unit")) or "MTR",
            "rate": float(ln.get("rate") or 0),
            "discount_pct": float(ln.get("discount_pct") or 0),
            "tax_pct": float(ln.get("tax_pct") or 0),
            "hsn": _clean(ln.get("hsn")),
            "section_title": _clean(ln.get("section_title")),
        }
        for ln in lines
    ]
    label = _clean(h.get("txn_label"))
    is_commercial = txn_type == 27 and (
        "commercial" in label.lower()
        or any(_clean(ln.get("section_title")) for ln in lines)
    )
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
        "discount_amount": float(h.get("discount_amount") or 0),
        "discount_pct": float(h.get("discount_pct") or 0),
        "round_off": float(h.get("round_off") or 0),
        "mode": "commercial" if is_commercial else "standard",
        "lines": flat_lines,
    }
    if is_commercial:
        out["sections"] = _group_lines_for_edit(lines)
    return out


def create_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create Estimate (27) or Proforma (83) with line items — preview + PDF ready."""
    from app.hop_ops import get_customer

    txn_type = int(payload.get("txn_type") or 0)
    if txn_type not in (27, 83):
        raise ValueError("txn_type must be 27 (estimate) or 83 (proforma)")

    customer_id = int(payload.get("customer_id") or 0)
    if not customer_id:
        raise ValueError("customer_id is required")
    customer = get_customer(conn, workspace_id, customer_id)
    if not customer:
        raise ValueError("customer_id not found")

    txn_date = _clean(payload.get("txn_date"))
    if not txn_date:
        from datetime import datetime, timezone

        txn_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    default_label = "Estimate / Quotation" if txn_type == 27 else "Proforma Invoice"
    txn_label = _clean(payload.get("txn_label")) or default_label
    notes = _clean(payload.get("notes"))

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("At least one line item is required")

    computed_lines, sub_total, line_tax_total = _parse_manual_doc_lines(raw_lines)
    if not computed_lines:
        raise ValueError("At least one valid line item is required")

    totals = _finalize_manual_doc_totals(sub_total, line_tax_total, payload)
    shipping = totals["shipping"]
    shipping_tax_pct = totals["shipping_tax_pct"]
    shipping_tax_amount = totals["shipping_tax_amount"]
    doc_discount = totals["doc_discount"]
    doc_discount_pct = totals["doc_discount_pct"]
    round_off = totals["round_off"]
    grand = totals["grand"]

    doc_terms = _clean(payload.get("doc_terms") or payload.get("terms"))
    delivery_terms = _clean(payload.get("delivery_terms"))
    if not doc_terms:
        firm = get_firm_profile(conn, workspace_id)
        doc_terms = _clean(firm.get("terms_default"))
        if not delivery_terms:
            delivery_terms = _clean(firm.get("delivery_terms"))
    elif not delivery_terms:
        firm = get_firm_profile(conn, workspace_id)
        delivery_terms = _clean(firm.get("delivery_terms"))

    source_txn_id = next_manual_source_txn_id(conn, workspace_id)
    txn_number = _clean(payload.get("txn_number")) or next_manual_doc_number(
        conn, workspace_id, txn_type, txn_date
    )
    party_name = _clean(customer.get("company"))

    cur = conn.execute(
        """
        INSERT INTO hop_party_transactions (
            workspace_id, party_type, party_id, party_name, source_txn_id,
            txn_type, txn_label, txn_number, txn_date, total_amount,
            balance_amount, status_text, notes, shipping_amount, shipping_tax_pct,
            shipping_tax_amount, discount_amount, discount_pct, round_off,
            doc_terms, delivery_terms, created_at, updated_at
        ) VALUES (?, 'customer', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            workspace_id,
            customer_id,
            party_name,
            source_txn_id,
            txn_type,
            txn_label,
            txn_number,
            txn_date,
            grand,
            "Draft",
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
    conn.commit()

    return {
        "party_txn_id": party_txn_id,
        "source_txn_id": source_txn_id,
        "txn_number": txn_number,
        "txn_type": txn_type,
        "txn_label": txn_label,
        "txn_date": txn_date,
        "total_amount": grand,
        "customer_id": customer_id,
        "party_name": party_name,
    }


def update_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    party_txn_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Update Nexora-created Estimate (27) or Proforma (83)."""
    from app.hop_ops import get_customer

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
    if txn_type not in (27, 83) or source_txn_id >= 0:
        raise ValueError("Only Nexora-created estimates/proformas can be edited")

    customer_id = int(payload.get("customer_id") or h.get("party_id") or 0)
    if not customer_id:
        raise ValueError("customer_id is required")
    customer = get_customer(conn, workspace_id, customer_id)
    if not customer:
        raise ValueError("customer_id not found")

    txn_date = _clean(payload.get("txn_date")) or _clean(h.get("txn_date"))[:10]
    default_label = "Estimate / Quotation" if txn_type == 27 else "Proforma Invoice"
    txn_label = _clean(payload.get("txn_label")) or _clean(h.get("txn_label")) or default_label
    notes = _clean(payload.get("notes")) if "notes" in payload else _clean(h.get("notes"))

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("At least one line item is required")
    computed_lines, sub_total, line_tax_total = _parse_manual_doc_lines(raw_lines)
    if not computed_lines:
        raise ValueError("At least one valid line item is required")

    totals = _finalize_manual_doc_totals(sub_total, line_tax_total, payload)
    shipping = totals["shipping"]
    shipping_tax_pct = totals["shipping_tax_pct"]
    shipping_tax_amount = totals["shipping_tax_amount"]
    doc_discount = totals["doc_discount"]
    doc_discount_pct = totals["doc_discount_pct"]
    round_off = totals["round_off"]
    grand = totals["grand"]

    doc_terms = _clean(payload.get("doc_terms") or payload.get("terms") or h.get("doc_terms"))
    delivery_terms = _clean(payload.get("delivery_terms") or h.get("delivery_terms"))
    txn_number = _clean(payload.get("txn_number")) or _clean(h.get("txn_number"))
    party_name = _clean(customer.get("company"))

    conn.execute(
        """
        UPDATE hop_party_transactions SET
            party_id=?, party_name=?, txn_label=?, txn_number=?, txn_date=?,
            total_amount=?, notes=?, shipping_amount=?, shipping_tax_pct=?,
            shipping_tax_amount=?, discount_amount=?, discount_pct=?, round_off=?,
            doc_terms=?, delivery_terms=?, updated_at=datetime('now')
        WHERE workspace_id=? AND id=?
        """,
        (
            customer_id,
            party_name,
            txn_label,
            txn_number,
            txn_date,
            grand,
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
    conn.commit()

    return {
        "party_txn_id": int(party_txn_id),
        "source_txn_id": source_txn_id,
        "txn_number": txn_number,
        "txn_type": txn_type,
        "txn_label": txn_label,
        "txn_date": txn_date,
        "total_amount": grand,
        "customer_id": customer_id,
        "party_name": party_name,
    }


def delete_manual_party_document(
    conn: sqlite3.Connection,
    workspace_id: str,
    party_txn_id: int,
) -> dict[str, Any]:
    """Delete Nexora-created Estimate (27) / Proforma (83) only (source_txn_id < 0)."""
    row = conn.execute(
        """
        SELECT id, source_txn_id, txn_type, txn_number FROM hop_party_transactions
        WHERE workspace_id=? AND id=?
        """,
        (workspace_id, int(party_txn_id)),
    ).fetchone()
    if not row:
        raise ValueError("Transaction not found")
    h = dict(row)
    txn_type = int(h.get("txn_type") or 0)
    source_txn_id = int(h.get("source_txn_id") or 0)
    if txn_type not in (27, 83) or source_txn_id >= 0:
        raise ValueError("Only Nexora-created estimates/proformas can be deleted")
    conn.execute(
        "DELETE FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=?",
        (workspace_id, source_txn_id),
    )
    conn.execute(
        "DELETE FROM hop_party_transactions WHERE workspace_id=? AND id=?",
        (workspace_id, int(party_txn_id)),
    )
    conn.commit()
    return {
        "party_txn_id": int(party_txn_id),
        "source_txn_id": source_txn_id,
        "txn_number": _clean(h.get("txn_number")),
        "deleted": True,
    }
