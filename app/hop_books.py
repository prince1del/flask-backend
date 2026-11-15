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
    """Simple TB from party transactions mapped to default sales/purchase/cash buckets."""
    ensure_default_accounts(conn, workspace_id)
    sales = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=1",
        (workspace_id,),
    ).fetchone()[0]
    purchases = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=2",
        (workspace_id,),
    ).fetchone()[0]
    expenses = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=7",
        (workspace_id,),
    ).fetchone()[0]
    ar = conn.execute(
        "SELECT COALESCE(SUM(balance_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=1",
        (workspace_id,),
    ).fetchone()[0]
    ap = conn.execute(
        "SELECT COALESCE(SUM(balance_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=2",
        (workspace_id,),
    ).fetchone()[0]
    cash = cash_available(conn, workspace_id)
    rows = [
        {"code": "1000", "name": "Cash / Bank", "debit": round(cash, 2), "credit": 0.0},
        {"code": "1100", "name": "Accounts Receivable", "debit": round(float(ar or 0), 2), "credit": 0.0},
        {"code": "2000", "name": "Accounts Payable", "debit": 0.0, "credit": round(float(ap or 0), 2)},
        {"code": "4000", "name": "Sales", "debit": 0.0, "credit": round(float(sales or 0), 2)},
        {"code": "5000", "name": "Purchases", "debit": round(float(purchases or 0), 2), "credit": 0.0},
        {"code": "5100", "name": "Expenses", "debit": round(float(expenses or 0), 2), "credit": 0.0},
    ]
    td = round(sum(r["debit"] for r in rows), 2)
    tc = round(sum(r["credit"] for r in rows), 2)
    # Balancing figure into Capital / Suspense
    if abs(td - tc) > 0.009:
        if td > tc:
            rows.append({"code": "3000", "name": "Capital / Balancing", "debit": 0.0, "credit": round(td - tc, 2)})
        else:
            rows.append({"code": "3000", "name": "Capital / Balancing", "debit": round(tc - td, 2), "credit": 0.0})
        td = round(sum(r["debit"] for r in rows), 2)
        tc = round(sum(r["credit"] for r in rows), 2)
    return {"rows": rows, "total_debit": td, "total_credit": tc}


def profit_and_loss(conn: sqlite3.Connection, workspace_id: str, from_date: str = "", to_date: str = "") -> dict:
    def _sum(txn_types: tuple[int, ...]) -> float:
        sql = f"SELECT COALESCE(SUM(total_amount),0) FROM hop_party_transactions WHERE workspace_id=? AND txn_type IN ({','.join('?'*len(txn_types))})"
        params: list[Any] = [workspace_id, *txn_types]
        if from_date:
            sql += " AND date(txn_date) >= date(?)"
            params.append(from_date[:10])
        if to_date:
            sql += " AND date(txn_date) <= date(?)"
            params.append(to_date[:10])
        return float(conn.execute(sql, params).fetchone()[0] or 0)

    income = _sum((1,))
    cogs = _sum((2,))
    expense = _sum((7,))
    returns_in = _sum((21,))
    returns_out = _sum((16,))
    net_sales = income - returns_in
    net_purchases = cogs - returns_out
    gross = net_sales - net_purchases
    net = gross - expense
    return {
        "from_date": from_date or None,
        "to_date": to_date or None,
        "sales": round(income, 2),
        "sale_returns": round(returns_in, 2),
        "net_sales": round(net_sales, 2),
        "purchases": round(cogs, 2),
        "purchase_returns": round(returns_out, 2),
        "net_purchases": round(net_purchases, 2),
        "gross_profit": round(gross, 2),
        "expenses": round(expense, 2),
        "net_profit": round(net, 2),
    }


def balance_sheet(conn: sqlite3.Connection, workspace_id: str) -> dict:
    tb = trial_balance(conn, workspace_id)
    by_code = {r["code"]: r for r in tb["rows"]}
    assets = round(
        (by_code.get("1000", {}).get("debit") or 0) + (by_code.get("1100", {}).get("debit") or 0),
        2,
    )
    liabilities = round(by_code.get("2000", {}).get("credit") or 0, 2)
    equity = round(
        (by_code.get("3000", {}).get("credit") or 0) - (by_code.get("3000", {}).get("debit") or 0),
        2,
    )
    pnl = profit_and_loss(conn, workspace_id)
    equity = round(equity + float(pnl.get("net_profit") or 0), 2)
    return {
        "assets": {"cash_bank": by_code.get("1000", {}).get("debit") or 0,
                   "receivables": by_code.get("1100", {}).get("debit") or 0,
                   "total": assets},
        "liabilities": {"payables": liabilities, "total": liabilities},
        "equity": {"capital_and_reserves": equity, "total": equity},
        "balanced": abs(assets - (liabilities + equity)) < 1.0,
    }
