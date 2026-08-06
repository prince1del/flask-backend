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
            bank_name, bank_account, bank_ifsc, bank_holder, logo_url, terms_default, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
        ),
    )


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
                qty, unit, rate, discount_amount, tax_pct, tax_amount, line_total,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
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
    if not lines and header_total:
        sub_total = header_total
    grand = header_total if header_total > 0.009 else (sub_total + tax_total)

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
    terms = _clean(firm.get("terms_default")) or "Thanks for doing business with us!"

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
                "tax_pct": float(ln.get("tax_pct") or 0),
                "tax_amount": float(ln.get("tax_amount") or 0),
                "line_total": float(ln.get("line_total") or 0),
            }
            for ln in lines
        ],
        "totals": {
            "qty": qty_total,
            "sub_total": round(sub_total, 2),
            "tax_total": round(tax_total, 2),
            "tax_pct": tax_pct,
            "grand_total": round(grand, 2),
            "amount_in_words": amount_in_words_inr(grand),
        },
        "terms": terms,
        "lines_missing": len(lines) == 0,
        "lines_missing_hint": (
            "Item rows are not loaded yet. Re-import your Vyapar .vyb backup "
            "(Settings → Import from Vyapar) to fill quantity, rate, HSN and GST lines."
            if not lines
            else ""
        ),
    }
