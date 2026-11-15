"""HoP Phase 3 remaining: print themes, recycle bin, loans, fixed assets, FY close, Tally export."""
from __future__ import annotations

import csv
import io
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any
from xml.dom import minidom

PRINT_THEMES = {
    "classic_blue": {"id": "classic_blue", "name": "Classic blue", "accent": [29, 78, 216]},
    "slate": {"id": "slate", "name": "Slate", "accent": [51, 65, 85]},
    "forest": {"id": "forest", "name": "Forest", "accent": [22, 101, 52]},
    "maroon": {"id": "maroon", "name": "Maroon", "accent": [127, 29, 29]},
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _s(v: Any) -> str:
    return str(v or "").strip()


def _f(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int | None:
    try:
        if v in (None, ""):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


# ── Print themes ─────────────────────────────────────────────────────────────

def list_print_themes() -> list[dict[str, Any]]:
    return list(PRINT_THEMES.values())


def get_print_theme(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT print_theme_id FROM hop_firm_profile WHERE workspace_id=? LIMIT 1", (ws,)
    ).fetchone()
    tid = _s(row[0] if row else "") or "classic_blue"
    theme = dict(PRINT_THEMES.get(tid) or PRINT_THEMES["classic_blue"])
    theme["active"] = True
    return theme


def set_print_theme(conn: sqlite3.Connection, ws: str, theme_id: str) -> dict[str, Any]:
    tid = _s(theme_id) or "classic_blue"
    if tid not in PRINT_THEMES:
        raise ValueError("Unknown print theme")
    now = _now()
    exists = conn.execute("SELECT 1 FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
    if exists:
        conn.execute(
            "UPDATE hop_firm_profile SET print_theme_id=?, updated_at=? WHERE workspace_id=?",
            (tid, now, ws),
        )
    else:
        conn.execute(
            "INSERT INTO hop_firm_profile (workspace_id, print_theme_id, updated_at) VALUES (?, ?, ?)",
            (ws, tid, now),
        )
    conn.commit()
    return get_print_theme(conn, ws)


# ── Recycle bin ───────────────────────────────────────────────────────────────

def soft_delete_document(conn: sqlite3.Connection, ws: str, txn_id: int, *, actor: str | None = None) -> dict[str, Any]:
    from app import hop_money
    from app.hop_doc_preview import MANUAL_TXN_TYPES

    row = conn.execute(
        "SELECT id, source_txn_id, txn_type, txn_number, deleted_at FROM hop_party_transactions WHERE workspace_id=? AND id=?",
        (ws, int(txn_id)),
    ).fetchone()
    if not row:
        raise LookupError("Transaction not found")
    h = dict(row)
    if h.get("deleted_at"):
        return {"party_txn_id": int(txn_id), "already_deleted": True}
    txn_type = int(h.get("txn_type") or 0)
    source_txn_id = int(h.get("source_txn_id") or 0)
    if txn_type not in MANUAL_TXN_TYPES or source_txn_id >= 0:
        raise ValueError("Only Nexora-created documents can be deleted")
    hop_money.unpost_document(conn, ws, int(txn_id))
    conn.execute(
        "UPDATE hop_party_transactions SET deleted_at=?, deleted_by=?, updated_at=? WHERE id=?",
        (_now(), _s(actor) or None, _now(), int(txn_id)),
    )
    conn.commit()
    return {"party_txn_id": int(txn_id), "txn_number": h.get("txn_number"), "deleted": True, "soft": True}


def restore_document(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any]:
    from app import hop_money

    row = conn.execute(
        "SELECT id, deleted_at FROM hop_party_transactions WHERE workspace_id=? AND id=?",
        (ws, int(txn_id)),
    ).fetchone()
    if not row:
        raise LookupError("Transaction not found")
    if not row["deleted_at"]:
        return {"party_txn_id": int(txn_id), "restored": False, "reason": "not in recycle bin"}
    conn.execute(
        "UPDATE hop_party_transactions SET deleted_at=NULL, deleted_by=NULL, updated_at=? WHERE id=?",
        (_now(), int(txn_id)),
    )
    hop_money.post_document(conn, ws, int(txn_id), {})
    conn.commit()
    return {"party_txn_id": int(txn_id), "restored": True}


def soft_delete_product(conn: sqlite3.Connection, ws: str, product_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, deleted_at FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))
    ).fetchone()
    if not row:
        raise LookupError("Product not found")
    if row["deleted_at"]:
        return {"product_id": int(product_id), "already_deleted": True}
    conn.execute(
        "UPDATE hop_products SET deleted_at=?, updated_at=? WHERE id=?",
        (_now(), _now(), int(product_id)),
    )
    conn.commit()
    return {"product_id": int(product_id), "deleted": True, "soft": True}


def restore_product(conn: sqlite3.Connection, ws: str, product_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, deleted_at FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))
    ).fetchone()
    if not row:
        raise LookupError("Product not found")
    conn.execute(
        "UPDATE hop_products SET deleted_at=NULL, updated_at=? WHERE id=?", (_now(), int(product_id))
    )
    conn.commit()
    return {"product_id": int(product_id), "restored": True}


def list_recycle_bin(conn: sqlite3.Connection, ws: str, kind: str = "all") -> dict[str, Any]:
    docs, products = [], []
    if kind in ("all", "docs"):
        docs = [
            dict(r)
            for r in conn.execute(
                """SELECT id, txn_label, txn_number, txn_date, party_name, total_amount, deleted_at, deleted_by
                   FROM hop_party_transactions
                   WHERE workspace_id=? AND deleted_at IS NOT NULL AND trim(deleted_at)!=''
                   ORDER BY deleted_at DESC LIMIT 200""",
                (ws,),
            ).fetchall()
        ]
    if kind in ("all", "products"):
        products = [
            dict(r)
            for r in conn.execute(
                """SELECT id, name, code, category, selling_price, deleted_at
                   FROM hop_products
                   WHERE workspace_id=? AND deleted_at IS NOT NULL AND trim(deleted_at)!=''
                   ORDER BY deleted_at DESC LIMIT 200""",
                (ws,),
            ).fetchall()
        ]
    return {"docs": docs, "products": products, "count": len(docs) + len(products)}


# ── Loan accounts ─────────────────────────────────────────────────────────────

def list_loan_accounts(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM hop_loan_accounts WHERE workspace_id=? AND COALESCE(is_active,1)=1 ORDER BY lower(name)",
        (ws,),
    ).fetchall()]
    for r in rows:
        r["outstanding"] = loan_outstanding(conn, ws, int(r["id"]))
    return rows


def create_loan_account(conn: sqlite3.Connection, ws: str, payload: dict) -> dict[str, Any]:
    name = _s(payload.get("name"))
    if not name:
        raise ValueError("Loan name is required")
    opening = _f(payload.get("opening_balance") or payload.get("principal"))
    now = _now()
    cur = conn.execute(
        """INSERT INTO hop_loan_accounts (
             workspace_id, name, lender, loan_type, principal, opening_balance, interest_pct,
             emi_amount, start_date, end_date, linked_bank_account_id, is_active, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            ws, name, _s(payload.get("lender")), _s(payload.get("loan_type")) or "term",
            _f(payload.get("principal")), opening, _f(payload.get("interest_pct")),
            _f(payload.get("emi_amount")), _s(payload.get("start_date"))[:10] or None,
            _s(payload.get("end_date"))[:10] or None, _i(payload.get("linked_bank_account_id")),
            now, now,
        ),
    )
    conn.commit()
    lid = int(cur.lastrowid)
    return next(x for x in list_loan_accounts(conn, ws) if x["id"] == lid)


def add_loan_entry(conn: sqlite3.Connection, ws: str, loan_id: int, payload: dict) -> dict[str, Any]:
    loan = conn.execute(
        "SELECT * FROM hop_loan_accounts WHERE workspace_id=? AND id=?", (ws, int(loan_id))
    ).fetchone()
    if not loan:
        raise LookupError("Loan not found")
    entry_type = _s(payload.get("entry_type")).lower() or "emi"
    if entry_type not in ("disbursement", "emi", "interest", "principal", "fee"):
        raise ValueError("Invalid entry_type")
    amount = abs(_f(payload.get("amount")))
    if amount <= 0:
        raise ValueError("amount must be > 0")
    entry_date = _s(payload.get("entry_date"))[:10] or date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO hop_loan_entries (
             workspace_id, loan_id, entry_date, entry_type, amount, bank_account_id, notes, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ws, int(loan_id), entry_date, entry_type, amount, _i(payload.get("bank_account_id")),
         _s(payload.get("notes")), _now()),
    )
    # EMI / principal payment: optionally reduce cash via bank entry
    bank_id = _i(payload.get("bank_account_id"))
    if bank_id and entry_type in ("emi", "principal", "interest", "fee"):
        from app import hop_books
        hop_books.add_bank_entry(conn, ws, {
            "account_id": bank_id, "entry_type": "out", "amount": amount,
            "entry_date": entry_date, "notes": f"Loan {_s(loan['name'])} {entry_type}",
            "reference": f"loan:{loan_id}",
        })
    conn.commit()
    return {"id": int(cur.lastrowid), "loan_id": int(loan_id), "outstanding": loan_outstanding(conn, ws, int(loan_id))}


def loan_outstanding(conn: sqlite3.Connection, ws: str, loan_id: int) -> float:
    row = conn.execute(
        "SELECT opening_balance FROM hop_loan_accounts WHERE workspace_id=? AND id=?",
        (ws, int(loan_id)),
    ).fetchone()
    if not row:
        return 0.0
    bal = _f(row[0])
    for e in conn.execute(
        "SELECT entry_type, amount FROM hop_loan_entries WHERE workspace_id=? AND loan_id=?",
        (ws, int(loan_id)),
    ).fetchall():
        et, amt = e[0], _f(e[1])
        if et == "disbursement":
            bal += amt
        elif et in ("emi", "principal"):
            bal -= amt
        # interest/fee do not reduce principal outstanding
    return round(max(bal, 0), 2)


def loans_for_balance_sheet(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    out = []
    for loan in list_loan_accounts(conn, ws):
        amt = _f(loan.get("outstanding"))
        if amt > 0.005:
            out.append({"name": f"Loan — {loan['name']}", "amount": amt})
    return out


# ── Fixed assets ──────────────────────────────────────────────────────────────

def list_fixed_assets(conn: sqlite3.Connection, ws: str, *, include_disposed: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM hop_fixed_assets WHERE workspace_id=?"
    if not include_disposed:
        sql += " AND (disposal_date IS NULL OR trim(disposal_date)='')"
    sql += " AND COALESCE(is_active,1)=1 ORDER BY lower(name)"
    rows = [dict(r) for r in conn.execute(sql, (ws,)).fetchall()]
    for r in rows:
        cost = _f(r.get("purchase_cost"))
        accum = _f(r.get("accumulated_dep"))
        r["book_value"] = round(max(cost - accum, 0), 2)
    return rows


def create_fixed_asset(conn: sqlite3.Connection, ws: str, payload: dict) -> dict[str, Any]:
    name = _s(payload.get("name"))
    cost = _f(payload.get("purchase_cost"))
    if not name:
        raise ValueError("Asset name is required")
    if cost <= 0:
        raise ValueError("purchase_cost must be > 0")
    now = _now()
    cur = conn.execute(
        """INSERT INTO hop_fixed_assets (
             workspace_id, name, category, purchase_date, purchase_cost, salvage_value,
             life_months, method, accumulated_dep, is_active, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)""",
        (
            ws, name, _s(payload.get("category")),
            _s(payload.get("purchase_date"))[:10] or date.today().isoformat(),
            cost, _f(payload.get("salvage_value")),
            int(payload.get("life_months") or 60),
            _s(payload.get("method")) or "slm", now, now,
        ),
    )
    conn.commit()
    aid = int(cur.lastrowid)
    return next(x for x in list_fixed_assets(conn, ws, include_disposed=True) if x["id"] == aid)


def compute_monthly_dep(asset: dict) -> float:
    cost = _f(asset.get("purchase_cost"))
    salvage = _f(asset.get("salvage_value"))
    months = max(int(asset.get("life_months") or 1), 1)
    return round(max(cost - salvage, 0) / months, 2)


def run_depreciation(conn: sqlite3.Connection, ws: str, as_of: str = "") -> dict[str, Any]:
    as_of = _s(as_of)[:10] or date.today().isoformat()
    period = as_of[:7]  # YYYY-MM
    posted = []
    for asset in list_fixed_assets(conn, ws):
        amt = compute_monthly_dep(asset)
        if amt <= 0:
            continue
        exists = conn.execute(
            "SELECT 1 FROM hop_asset_depreciations WHERE workspace_id=? AND asset_id=? AND period=?",
            (ws, int(asset["id"]), period),
        ).fetchone()
        if exists:
            continue
        book = _f(asset.get("book_value"))
        salvage = _f(asset.get("salvage_value"))
        amt = min(amt, max(book - salvage, 0))
        if amt <= 0:
            continue
        conn.execute(
            "INSERT INTO hop_asset_depreciations (workspace_id, asset_id, period, amount, created_at) VALUES (?,?,?,?,?)",
            (ws, int(asset["id"]), period, amt, _now()),
        )
        conn.execute(
            "UPDATE hop_fixed_assets SET accumulated_dep=COALESCE(accumulated_dep,0)+?, updated_at=? WHERE id=?",
            (amt, _now(), int(asset["id"])),
        )
        posted.append({"asset_id": asset["id"], "name": asset["name"], "amount": amt, "period": period})
    conn.commit()
    return {"as_of": as_of, "period": period, "posted": posted, "count": len(posted)}


def fixed_assets_for_balance_sheet(conn: sqlite3.Connection, ws: str) -> tuple[list[dict], list[dict]]:
    assets, contra = [], []
    total_cost = total_dep = 0.0
    for a in list_fixed_assets(conn, ws):
        total_cost += _f(a.get("purchase_cost"))
        total_dep += _f(a.get("accumulated_dep"))
    if total_cost > 0.005:
        assets.append({"name": "Fixed Assets (cost)", "amount": round(total_cost, 2)})
    if total_dep > 0.005:
        contra.append({"name": "Less: Accumulated Depreciation", "amount": -round(total_dep, 2)})
    return assets, contra


def depreciation_expense_ytd(conn: sqlite3.Connection, ws: str, frm: str, to: str) -> float:
    params: list[Any] = [ws]
    sql = "SELECT COALESCE(SUM(amount),0) FROM hop_asset_depreciations WHERE workspace_id=?"
    if frm:
        sql += " AND period >= ?"
        params.append(frm[:7])
    if to:
        sql += " AND period <= ?"
        params.append(to[:7])
    return _f(conn.execute(sql, params).fetchone()[0])


# ── Financial year ────────────────────────────────────────────────────────────

def indian_fy_bounds(ref: date | None = None) -> tuple[str, str, str]:
    d = ref or date.today()
    start_year = d.year if d.month >= 4 else d.year - 1
    start = date(start_year, 4, 1)
    end = date(start_year + 1, 3, 31)
    label = f"FY{start_year}-{str(start_year + 1)[-2:]}"
    return start.isoformat(), end.isoformat(), label


def ensure_current_fy(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    start, end, label = indian_fy_bounds()
    row = conn.execute(
        "SELECT * FROM hop_financial_years WHERE workspace_id=? AND label=?", (ws, label)
    ).fetchone()
    if row:
        return dict(row)
    conn.execute(
        """INSERT INTO hop_financial_years (workspace_id, label, start_date, end_date, status, created_at)
           VALUES (?, ?, ?, ?, 'open', ?)""",
        (ws, label, start, end, _now()),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM hop_financial_years WHERE workspace_id=? AND label=?", (ws, label)
    ).fetchone())


def list_financial_years(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    ensure_current_fy(conn, ws)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM hop_financial_years WHERE workspace_id=? ORDER BY start_date DESC", (ws,)
    ).fetchall()]


def assert_period_open(conn: sqlite3.Connection, ws: str, txn_date: str) -> None:
    d = _s(txn_date)[:10]
    if not d:
        return
    closed = conn.execute(
        """SELECT label FROM hop_financial_years
           WHERE workspace_id=? AND status='closed' AND date(?) BETWEEN date(start_date) AND date(end_date)
           LIMIT 1""",
        (ws, d),
    ).fetchone()
    if closed:
        raise ValueError(f"Period {closed[0]} is closed — reopen it before saving documents in that year.")


def close_financial_year(conn: sqlite3.Connection, ws: str, fy_id: int, *, actor: str | None = None) -> dict[str, Any]:
    from app import hop_reports

    row = conn.execute(
        "SELECT * FROM hop_financial_years WHERE workspace_id=? AND id=?", (ws, int(fy_id))
    ).fetchone()
    if not row:
        raise LookupError("Financial year not found")
    fy = dict(row)
    if fy.get("status") == "closed":
        return fy
    pl = hop_reports.profit_and_loss(conn, ws, fy["start_date"], fy["end_date"])
    profit = _f(pl.get("net_profit") if isinstance(pl, dict) else 0)
    conn.execute(
        """UPDATE hop_financial_years SET status='closed', closed_at=?, closed_by=?,
           opening_equity_snapshot=? WHERE id=?""",
        (_now(), _s(actor) or None, profit, int(fy_id)),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM hop_financial_years WHERE id=?", (int(fy_id),)).fetchone())


# ── Tally export ──────────────────────────────────────────────────────────────

def build_tally_daybook_csv(conn: sqlite3.Connection, ws: str, frm: str, to: str) -> str:
    from app import hop_reports

    if frm and to and frm == to:
        data = hop_reports.day_book(conn, ws, frm)
    else:
        data = hop_reports.all_transactions(conn, ws, frm or "", to or "")
    rows = data.get("lines") or data.get("rows") or data.get("transactions") or data.get("entries") or []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Voucher", "Number", "Party", "Debit", "Credit", "Narration"])
    for r in rows:
        if not isinstance(r, dict):
            continue
        w.writerow([
            r.get("txn_date") or r.get("date") or "",
            r.get("txn_label") or r.get("voucher") or "",
            r.get("txn_number") or r.get("number") or "",
            r.get("party_name") or r.get("party") or "",
            r.get("debit") or r.get("total_amount") or "",
            r.get("credit") or "",
            r.get("notes") or r.get("narration") or "",
        ])
    return buf.getvalue()


def build_tally_xml(conn: sqlite3.Connection, ws: str, frm: str, to: str) -> bytes:
    """Minimal Tally-compatible envelope with ledgers + vouchers from party txns."""
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
    body = ET.SubElement(root, "BODY")
    importdata = ET.SubElement(body, "IMPORTDATA")
    reqdesc = ET.SubElement(importdata, "REQUESTDESC")
    ET.SubElement(reqdesc, "REPORTNAME").text = "Vouchers"
    reqdata = ET.SubElement(importdata, "REQUESTDATA")

    params: list[Any] = [ws]
    sql = """SELECT id, txn_date, txn_label, txn_number, party_name, total_amount, txn_type
             FROM hop_party_transactions
             WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='')"""
    if frm:
        sql += " AND date(txn_date) >= date(?)"
        params.append(frm[:10])
    if to:
        sql += " AND date(txn_date) <= date(?)"
        params.append(to[:10])
    sql += " ORDER BY txn_date, id LIMIT 2000"
    for r in conn.execute(sql, params).fetchall():
        msg = ET.SubElement(reqdata, "TALLYMESSAGE")
        vch = ET.SubElement(msg, "VOUCHER", {"VCHTYPE": _s(r["txn_label"]) or "Journal", "ACTION": "Create"})
        ET.SubElement(vch, "DATE").text = (_s(r["txn_date"])[:10] or "").replace("-", "")
        ET.SubElement(vch, "VOUCHERNUMBER").text = _s(r["txn_number"])
        ET.SubElement(vch, "NARRATION").text = f"{_s(r['party_name'])} · {_s(r['txn_label'])}"
        ET.SubElement(vch, "AMOUNT").text = str(_f(r["total_amount"]))

    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    return pretty
