"""Fetch Vyapar firm letterhead + line items for HoP document preview."""

from __future__ import annotations

import sqlite3
from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _pick_col(cols: set[str] | list[str], *candidates: str) -> str | None:
    lower = {c.lower(): c for c in cols}
    for name in candidates:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def fetch_firm_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cols = set(_table_cols(conn, "kb_firms"))
    if not cols:
        return out
    name_c = _pick_col(cols, "firm_name", "name", "company_name")
    email_c = _pick_col(cols, "firm_email", "email")
    phone_c = _pick_col(cols, "firm_phone", "phone", "mobile")
    gst_c = _pick_col(cols, "firm_gstin", "gstin", "gst_number", "firm_gst_number")
    addr_c = _pick_col(cols, "firm_address", "address", "firm_billing_address")
    state_c = _pick_col(cols, "firm_state", "state")
    pan_c = _pick_col(cols, "firm_pan", "pan")
    select = [c for c in (name_c, email_c, phone_c, gst_c, addr_c, state_c, pan_c) if c]
    if not select:
        return out
    order = _pick_col(cols, "firm_id", "id") or select[0]
    row = conn.execute(
        f"SELECT {', '.join(select)} FROM kb_firms ORDER BY {order} LIMIT 1"
    ).fetchone()
    if not row:
        return out
    d = dict(row)
    out = {
        "firm_name": _clean(d.get(name_c) if name_c else ""),
        "email": _clean(d.get(email_c) if email_c else ""),
        "phone": _clean(d.get(phone_c) if phone_c else ""),
        "gstin": _clean(d.get(gst_c) if gst_c else ""),
        "address": _clean(d.get(addr_c) if addr_c else ""),
        "state": _clean(d.get(state_c) if state_c else ""),
        "pan": _clean(d.get(pan_c) if pan_c else ""),
    }
    for table in ("kb_firm_bank_details", "kb_bank_details", "kb_banks"):
        bcols = set(_table_cols(conn, table))
        if not bcols:
            continue
        bn = _pick_col(bcols, "bank_name", "name")
        ac = _pick_col(bcols, "account_number", "account_no", "bank_account")
        ifsc = _pick_col(bcols, "ifsc", "ifsc_code", "bank_ifsc")
        holder = _pick_col(bcols, "account_holder", "account_holder_name", "holder_name")
        fields = [c for c in (bn, ac, ifsc, holder) if c]
        if not fields:
            continue
        brow = conn.execute(f"SELECT {', '.join(fields)} FROM {table} LIMIT 1").fetchone()
        if not brow:
            continue
        bd = dict(brow)
        out["bank_name"] = _clean(bd.get(bn) if bn else "")
        out["bank_account"] = _clean(bd.get(ac) if ac else "")
        out["bank_ifsc"] = _clean(bd.get(ifsc) if ifsc else "")
        out["bank_holder"] = _clean(bd.get(holder) if holder else "")
        break
    return out


def fetch_all_line_items(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    li_cols = set(_table_cols(conn, "kb_lineitems"))
    if not li_cols:
        return {}
    txn_c = _pick_col(li_cols, "lineitem_txn_id", "txn_id")
    if not txn_c:
        return {}
    item_c = _pick_col(li_cols, "lineitem_item_id", "item_id")
    unit_c = _pick_col(li_cols, "lineitem_unit_id", "unit_id")
    qty_c = _pick_col(li_cols, "quantity", "qty", "lineitem_quantity")
    # Vyapar stores unit price as priceperunit (no underscores).
    rate_c = _pick_col(
        li_cols,
        "priceperunit",
        "price_per_unit",
        "lineitem_priceperunit",
        "rate",
        "unit_price",
        "price",
    )
    disc_c = _pick_col(
        li_cols,
        "lineitem_discount_amount",
        "discount_amount",
        "lineitem_discount",
    )
    tax_amt_c = _pick_col(li_cols, "lineitem_tax_amount", "tax_amount")
    total_c = _pick_col(li_cols, "total_amount", "line_total", "amount")
    desc_c = _pick_col(li_cols, "lineitem_description", "description", "item_description")
    tax_id_c = _pick_col(li_cols, "tax_id", "lineitem_tax_id")
    line_id_c = _pick_col(li_cols, "lineitem_id", "id")

    select = [txn_c]
    for c in (line_id_c, item_c, unit_c, qty_c, rate_c, disc_c, tax_amt_c, total_c, desc_c, tax_id_c):
        if c and c not in select:
            select.append(c)
    order = line_id_c or txn_c
    rows = conn.execute(
        f"SELECT {', '.join(select)} FROM kb_lineitems ORDER BY {txn_c}, {order}"
    ).fetchall()

    items: dict[int, dict[str, Any]] = {}
    icols = set(_table_cols(conn, "kb_items"))
    iid = _pick_col(icols, "item_id", "id")
    iname = _pick_col(icols, "item_name", "name")
    icode = _pick_col(icols, "item_code", "code")
    ihsn = _pick_col(icols, "item_hsn_sac_code", "hsn", "hsn_sac_code")
    idesc = _pick_col(icols, "item_description", "description")
    if iid and iname:
        fields = [c for c in (iid, iname, icode, ihsn, idesc) if c]
        for r in conn.execute(f"SELECT {', '.join(fields)} FROM kb_items").fetchall():
            d = dict(r)
            items[int(d[iid])] = {
                "name": _clean(d.get(iname)),
                "code": _clean(d.get(icode) if icode else ""),
                "hsn": _clean(d.get(ihsn) if ihsn else ""),
                "description": _clean(d.get(idesc) if idesc else ""),
            }

    units: dict[int, str] = {}
    ucols = set(_table_cols(conn, "kb_units"))
    uid = _pick_col(ucols, "unit_id", "id")
    uname = _pick_col(ucols, "unit_name", "name", "short_name")
    if uid and uname:
        for r in conn.execute(f"SELECT {uid}, {uname} FROM kb_units").fetchall():
            units[int(r[0])] = _clean(r[1])

    tax_pct: dict[int, float] = {}
    tcols = set(_table_cols(conn, "kb_taxes"))
    tid = _pick_col(tcols, "tax_id", "id")
    trate = _pick_col(tcols, "tax_rate", "rate", "percentage")
    if tid and trate:
        for r in conn.execute(f"SELECT {tid}, {trate} FROM kb_taxes").fetchall():
            tax_pct[int(r[0])] = _num(r[1])

    by_txn: dict[int, list[dict[str, Any]]] = {}
    counters: dict[int, int] = {}
    for r in rows:
        d = dict(r)
        txn_id = int(d.get(txn_c) or 0)
        if not txn_id:
            continue
        counters[txn_id] = counters.get(txn_id, 0) + 1
        item = items.get(int(d.get(item_c) or 0), {}) if item_c else {}
        unit_name = units.get(int(d.get(unit_c) or 0), "") if unit_c else ""
        tax_key = int(d.get(tax_id_c) or 0) if tax_id_c else 0
        qty = _num(d.get(qty_c) if qty_c else 0)
        rate = _num(d.get(rate_c) if rate_c else 0)
        disc = _num(d.get(disc_c) if disc_c else 0)
        tax_amt = _num(d.get(tax_amt_c) if tax_amt_c else 0)
        total = _num(d.get(total_c) if total_c else 0)
        # If unit price column was missing/zero, derive from taxable amount.
        if rate <= 0 and qty > 0 and total > 0:
            taxable = max(0.0, total - tax_amt + disc)
            rate = round(taxable / qty, 6)
        name = item.get("name") or _clean(d.get(desc_c) if desc_c else "") or "Item"
        desc = _clean(d.get(desc_c) if desc_c else "") or item.get("description") or ""
        if desc and desc.lower() == name.lower():
            desc = ""
        by_txn.setdefault(txn_id, []).append(
            {
                "line_no": counters[txn_id],
                "item_name": name,
                "item_code": item.get("code") or "",
                "description": desc,
                "hsn": item.get("hsn") or "",
                "qty": qty,
                "unit": unit_name or "Pcs",
                "rate": rate,
                "discount_amount": disc,
                "tax_pct": tax_pct.get(tax_key, 0.0),
                "tax_amount": tax_amt,
                "line_total": total,
            }
        )
    return by_txn
