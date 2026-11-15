"""HoP books: banking, stock journals, GST summaries, chart of accounts & trial reports."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _s(v: Any) -> str:
    return str(v or "").strip()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any) -> int | None:
    try:
        if v in (None, ""):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


# ----- Banking -----
def list_bank_accounts(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    from app import hop_money

    hop_money.ensure_cash_account(conn, workspace_id)
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM hop_bank_accounts
            WHERE workspace_id=? AND COALESCE(is_active,1)=1
            ORDER BY account_type, name
            """,
            (workspace_id,),
        ).fetchall()
    ]


def create_bank_account(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    name = _s(payload.get("name"))
    if not name:
        raise ValueError("name is required")
    opening = _f(payload.get("opening_balance"))
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO hop_bank_accounts (
            workspace_id, name, account_type, account_number, ifsc,
            opening_balance, current_balance, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            workspace_id,
            name,
            _s(payload.get("account_type")) or "bank",
            _s(payload.get("account_number")),
            _s(payload.get("ifsc")),
            opening,
            opening,
            now,
            now,
        ),
    )
    conn.commit()
    aid = int(cur.lastrowid)
    return next(a for a in list_bank_accounts(conn, workspace_id) if a["id"] == aid)


def add_bank_entry(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    account_id = _i(payload.get("account_id"))
    if not account_id:
        raise ValueError("account_id is required")
    acct = conn.execute(
        "SELECT * FROM hop_bank_accounts WHERE workspace_id=? AND id=?",
        (workspace_id, account_id),
    ).fetchone()
    if not acct:
        raise ValueError("bank account not found")
    entry_type = _s(payload.get("entry_type")).lower() or "in"
    if entry_type not in ("in", "out", "transfer"):
        raise ValueError("entry_type must be in, out, or transfer")
    amount = abs(_f(payload.get("amount")))
    if amount <= 0:
        raise ValueError("amount must be > 0")
    entry_date = _s(payload.get("entry_date")) or _now()[:10]
    delta = amount if entry_type == "in" else -amount
    counterparty = _i(payload.get("counterparty_account_id"))
    cur = conn.execute(
        """
        INSERT INTO hop_bank_entries (
            workspace_id, account_id, entry_date, entry_type, amount,
            counterparty_account_id, party_type, party_id, party_txn_id,
            reference, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            account_id,
            entry_date,
            entry_type,
            amount,
            counterparty,
            _s(payload.get("party_type")) or None,
            _i(payload.get("party_id")),
            _i(payload.get("party_txn_id")),
            _s(payload.get("reference")),
            _s(payload.get("notes")),
            _now(),
        ),
    )
    from app import hop_money

    if entry_type == "transfer" and not counterparty:
        raise ValueError("Choose the account to transfer to")
    if entry_type == "transfer" and counterparty == account_id:
        raise ValueError("Choose a different account to transfer to")
    if entry_type == "transfer" and not conn.execute(
            "SELECT 1 FROM hop_bank_accounts WHERE workspace_id=? AND id=?", (workspace_id, counterparty)).fetchone():
        raise ValueError("Transfer account not found")
    if entry_type == "transfer" and counterparty:
        conn.execute(
            """
            INSERT INTO hop_bank_entries (
                workspace_id, account_id, entry_date, entry_type, amount,
                counterparty_account_id, reference, notes, created_at
            ) VALUES (?, ?, ?, 'in', ?, ?, ?, ?, ?)
            """,
            (workspace_id, counterparty, entry_date, amount, account_id,
             _s(payload.get("reference")), _s(payload.get("notes")), _now()),
        )
    hop_money.recompute_account(conn, workspace_id, account_id)
    hop_money.recompute_account(conn, workspace_id, counterparty)
    conn.commit()
    return {"id": int(cur.lastrowid), "account_id": account_id, "amount": amount, "entry_type": entry_type}


def list_bank_entries(conn: sqlite3.Connection, workspace_id: str, account_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM hop_bank_entries WHERE workspace_id=?"
    params: list[Any] = [workspace_id]
    if account_id:
        sql += " AND account_id=?"
        params.append(int(account_id))
    sql += " ORDER BY entry_date DESC, id DESC LIMIT 500"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def cash_available(conn: sqlite3.Connection, workspace_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(current_balance),0) AS t FROM hop_bank_accounts WHERE workspace_id=? AND COALESCE(is_active,1)=1",
        (workspace_id,),
    ).fetchone()
    return float(dict(row).get("t") or 0)


# ----- Stock -----
def list_stock(conn: sqlite3.Connection, workspace_id: str, low_only: bool = False) -> list[dict]:
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, code, name, hsn, gst_pct, stock_qty, selling_price, purchase_price
            FROM hop_products WHERE workspace_id=? AND COALESCE(status,'active')='active'
            ORDER BY name
            """,
            (workspace_id,),
        ).fetchall()
    ]
    if low_only:
        rows = [r for r in rows if float(r.get("stock_qty") or 0) <= 0]
    return rows


def add_stock_journal(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    product_id = _i(payload.get("product_id"))
    qty_delta = _f(payload.get("qty_delta"))
    if not product_id:
        raise ValueError("product_id is required")
    if qty_delta == 0:
        raise ValueError("qty_delta must not be 0")
    prod = conn.execute(
        "SELECT id, stock_qty FROM hop_products WHERE workspace_id=? AND id=?",
        (workspace_id, product_id),
    ).fetchone()
    if not prod:
        raise ValueError("product not found")
    entry_date = _s(payload.get("entry_date")) or _now()[:10]
    cur = conn.execute(
        """
        INSERT INTO hop_stock_journals (
            workspace_id, product_id, entry_date, qty_delta, reason, reference, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, product_id, entry_date, qty_delta,
         _s(payload.get("reason")), _s(payload.get("reference")), _now()),
    )
    conn.execute(
        "UPDATE hop_products SET stock_qty = COALESCE(stock_qty,0) + ?, updated_at=? WHERE workspace_id=? AND id=?",
        (qty_delta, _now(), workspace_id, product_id),
    )
    conn.commit()
    new_qty = float(dict(prod).get("stock_qty") or 0) + qty_delta
    return {"id": int(cur.lastrowid), "product_id": product_id, "qty_delta": qty_delta, "stock_qty": new_qty}


# ----- GST -----
def gst_hsn_summary(conn: sqlite3.Connection, workspace_id: str, from_date: str = "", to_date: str = "") -> dict:
    """HSN-wise taxable + tax from hop_txn_lines joined to sale/purchase headers."""
    sql = """
        SELECT COALESCE(NULLIF(trim(l.hsn),''),'(blank)') AS hsn,
               COALESCE(l.tax_pct,0) AS tax_pct,
               SUM(COALESCE(l.line_total,0) - COALESCE(l.tax_amount,0)) AS taxable,
               SUM(COALESCE(l.tax_amount,0)) AS tax_amount,
               SUM(COALESCE(l.line_total,0)) AS line_total,
               COUNT(*) AS lines
        FROM hop_txn_lines l
        JOIN hop_party_transactions t
          ON t.workspace_id = l.workspace_id AND t.source_txn_id = l.source_txn_id
        WHERE l.workspace_id=? AND t.txn_type IN (1,2,21,16)
    """
    params: list[Any] = [workspace_id]
    if from_date:
        sql += " AND date(t.txn_date) >= date(?)"
        params.append(from_date[:10])
    if to_date:
        sql += " AND date(t.txn_date) <= date(?)"
        params.append(to_date[:10])
    sql += " GROUP BY hsn, tax_pct ORDER BY hsn, tax_pct"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return {
        "from_date": from_date or None,
        "to_date": to_date or None,
        "rows": rows,
        "taxable_total": round(sum(float(r.get("taxable") or 0) for r in rows), 2),
        "tax_total": round(sum(float(r.get("tax_amount") or 0) for r in rows), 2),
    }


def gstr1_sales(conn: sqlite3.Connection, workspace_id: str, from_date: str = "", to_date: str = "") -> dict:
    sql = """
        SELECT id, txn_number, txn_date, party_name, total_amount, balance_amount, status_text
        FROM hop_party_transactions
        WHERE workspace_id=? AND txn_type=1
    """
    params: list[Any] = [workspace_id]
    if from_date:
        sql += " AND date(txn_date) >= date(?)"
        params.append(from_date[:10])
    if to_date:
        sql += " AND date(txn_date) <= date(?)"
        params.append(to_date[:10])
    sql += " ORDER BY txn_date, id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return {
        "from_date": from_date or None,
        "to_date": to_date or None,
        "invoices": rows,
        "invoice_count": len(rows),
        "taxable_approx": round(sum(float(r.get("total_amount") or 0) for r in rows), 2),
    }


# ----- Chart of accounts + reports -----
DEFAULT_ACCOUNTS = [
    ("1000", "Cash", "asset"),
    ("1010", "Bank", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Inventory", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("3000", "Capital", "equity"),
    ("4000", "Sales", "income"),
    ("5000", "Purchases", "expense"),
    ("5100", "Expenses", "expense"),
]


def ensure_default_accounts(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    existing = list_accounts(conn, workspace_id)
    if existing:
        return existing
    now = _now()
    for code, name, atype in DEFAULT_ACCOUNTS:
        conn.execute(
            """
            INSERT INTO hop_accounts (workspace_id, code, name, account_type, is_system, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (workspace_id, code, name, atype, now),
        )
    conn.commit()
    return list_accounts(conn, workspace_id)


def list_accounts(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM hop_accounts WHERE workspace_id=? ORDER BY code, id",
            (workspace_id,),
        ).fetchall()
    ]


def trial_balance(conn: sqlite3.Connection, workspace_id: str) -> dict:
    """Old response shape (phone), numbers from the real double-entry ledger (app.hop_reports)."""
    from app import hop_reports

    tb = hop_reports.trial_balance(conn, workspace_id)
    rows = [{"code": "", "name": r["account"], "debit": r["debit"], "credit": r["credit"]} for r in tb["rows"]]
    return {"rows": rows, "total_debit": tb["total_debit"], "total_credit": tb["total_credit"]}


def profit_and_loss(conn: sqlite3.Connection, workspace_id: str, from_date: str = "", to_date: str = "") -> dict:
    """Old response shape (phone): ``expenses`` is the total; full detail in /books/profit_loss."""
    from app import hop_reports

    pl = hop_reports.profit_and_loss(conn, workspace_id, (from_date or "")[:10], (to_date or "")[:10])
    return {
        "from_date": pl["from_date"], "to_date": pl["to_date"],
        "sales": pl["sales"], "sale_returns": pl["sale_returns"], "net_sales": pl["net_sales"],
        "purchases": pl["purchases"], "purchase_returns": pl["purchase_returns"],
        "net_purchases": pl["net_purchases"], "opening_stock": pl["opening_stock"],
        "closing_stock": pl["closing_stock"], "gross_profit": pl["gross_profit"],
        "expenses": pl["total_expenses"], "net_profit": pl["net_profit"],
    }


def balance_sheet(conn: sqlite3.Connection, workspace_id: str) -> dict:
    """Old response shape (phone); full detail in /books/balance_sheet."""
    from app import hop_reports

    bs = hop_reports.balance_sheet(conn, workspace_id)
    a = {x["name"]: x["amount"] for x in bs["assets"]}
    li = {x["name"]: x["amount"] for x in bs["liabilities"]}
    eq = sum(x["amount"] for x in bs["equity"])
    cash = sum(v for k, v in a.items() if k not in ("Sundry Debtors (to receive)", "Advances to vendors",
                                                    "Closing stock", "GST credit (input)"))
    return {
        "assets": {"cash_bank": round(cash, 2), "receivables": a.get("Sundry Debtors (to receive)", 0.0),
                   "stock": a.get("Closing stock", 0.0), "total": bs["total_assets"]},
        "liabilities": {"payables": li.get("Sundry Creditors (to pay)", 0.0),
                        "total": round(sum(li.values()), 2)},
        "equity": {"capital_and_reserves": round(eq, 2), "total": round(eq, 2)},
        "balanced": bs["balanced"],
    }
