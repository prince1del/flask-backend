"""HoP accounts & reports, Vyapar-style (Phase 2).

A double-entry ledger is built on the fly from the documents, bank entries,
cheques and opening balances — nothing is stored, so every report is right
right after any edit or delete. All reports read that ledger (or the documents):

* Trial Balance, Profit & Loss (opening / closing stock at purchase price),
  Balance Sheet (always balances), Cash Flow
* Day Book, All Transactions, Party Statement, party-wise outstanding
* Sale / Purchase report, item-wise report, Stock summary, Low stock,
  Stock movement, Bill-wise profit, Expense report
* GSTR-1, GSTR-2, GSTR-3B, HSN summary

Imported Vyapar documents (``source_txn_id > 0``) post with their totals; what
was already paid on them goes to "Cash & Bank (imported)" because Vyapar
did not tell us which account.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from app.hop_money import LABELS

OPENING = "0000-00-00"
DEBTORS, CREDITORS = "Sundry Debtors", "Sundry Creditors"
SALES, SALES_RET, PURCH, PURCH_RET = "Sales", "Sales Returns", "Purchases", "Purchase Returns"
OUT_GST, IN_GST = "Output GST", "Input GST"
CAPITAL, DRAWINGS, OBE, OPEN_STOCK = "Capital", "Drawings", "Opening Balance Equity", "Opening Stock"
CHQ_IN, CHQ_OUT, IMPORT_CASH = "Cheques in hand", "Cheques issued", "Cash & Bank (imported)"

INCOME = {SALES, PURCH_RET}
EXPENSE_FIXED = {SALES_RET, PURCH, OPEN_STOCK}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(v: Any) -> str:
    return str(v or "")[:10]


def _rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _in_range(d: str, frm: str, to: str) -> bool:
    return (not frm or d >= frm) and (not to or d <= to)


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


# ── Building the ledger ──────────────────────────────────────────────────────

def _doc_tax(conn, ws) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for r in _rows(conn, "SELECT source_txn_id, SUM(COALESCE(tax_amount,0)) AS t FROM hop_txn_lines "
                         "WHERE workspace_id=? GROUP BY source_txn_id", (ws,)):
        out[int(r["source_txn_id"])] = _f(r["t"])
    return out


FIXED_ASSETS, ACC_DEP = "Fixed Assets", "Accumulated Depreciation"


def _account_kind(name: str, bank_names: set[str]) -> str:
    if name in bank_names or name in (CHQ_IN, IMPORT_CASH):
        return "cash"
    if name in (FIXED_ASSETS, ACC_DEP):
        return "asset"
    if name.startswith("Loan: "):
        return "liability"
    if name in (DEBTORS, IN_GST, "TCS receivable"):
        return "asset"
    if name in (CREDITORS, OUT_GST, CHQ_OUT, "TCS payable"):
        return "liability"
    if name in (CAPITAL, DRAWINGS, OBE):
        return "equity"
    if name in INCOME:
        return "income"
    if name.startswith("Other Income: "):
        return "income"
    return "expense"  # Sales Returns, Purchases, Opening Stock, Expense: …


def ledger(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """Every posting: {date, account, dr, cr, party, party_type, party_id, txn_id, kind, number}."""
    from app import hop_money

    hop_money.ensure_cash_account(conn, ws)
    banks = {int(a["id"]): a for a in _rows(conn, "SELECT * FROM hop_bank_accounts WHERE workspace_id=?", (ws,))}
    bank_names = {a["name"] for a in banks.values()}
    cats = {int(c["id"]): c["name"] for c in _rows(conn, "SELECT id, name FROM hop_expense_categories WHERE workspace_id=?", (ws,))}
    tax = _doc_tax(conn, ws)
    P: list[dict[str, Any]] = []

    def post(day, account, dr=0.0, cr=0.0, *, t=None, kind="", party=None):
        if abs(dr) < 0.005 and abs(cr) < 0.005:
            return
        P.append({"date": day, "account": account, "dr": _r2(dr), "cr": _r2(cr),
                  "txn_id": (t or {}).get("id"), "number": (t or {}).get("txn_number") or "",
                  "kind": kind, "party": (party or {}).get("name"), "party_type": (party or {}).get("type"),
                  "party_id": (party or {}).get("id")})

    def party_acct(t):
        return CREDITORS if t.get("party_type") == "vendor" else DEBTORS

    def party_of(t):
        return {"name": t.get("party_name"), "type": t.get("party_type"), "id": t.get("party_id")}

    def money_acct(t, direction):
        mode = (t.get("payment_mode") or "").lower()
        if mode == "cheque":
            return CHQ_IN if direction == "in" else CHQ_OUT
        aid = t.get("payment_account_id")
        if aid and int(aid) in banks:
            return banks[int(aid)]["name"]
        if int(t.get("source_txn_id") or 0) > 0:
            return IMPORT_CASH
        return next((a["name"] for a in banks.values() if a.get("account_type") == "cash"), "Cash in hand")

    # Opening balances: bank accounts, parties, stock.
    for a in banks.values():
        ob = _f(a.get("opening_balance"))
        if ob:
            post(OPENING, a["name"], dr=max(ob, 0), cr=max(-ob, 0), kind="Opening balance")
            post(OPENING, OBE, cr=max(ob, 0), dr=max(-ob, 0), kind="Opening balance")
    for kind_, table, acct in (("customer", "hop_customers", DEBTORS), ("vendor", "hop_vendors", CREDITORS)):
        for p in _rows(conn, f"SELECT id, company, opening_balance FROM {table} WHERE workspace_id=? "
                             "AND COALESCE(opening_balance,0) != 0", (ws,)):
            ob = _f(p["opening_balance"])
            party = {"name": p["company"], "type": kind_, "id": p["id"]}
            if acct == DEBTORS:
                post(OPENING, DEBTORS, dr=max(ob, 0), cr=max(-ob, 0), kind="Opening balance", party=party)
                post(OPENING, OBE, cr=max(ob, 0), dr=max(-ob, 0), kind="Opening balance")
            else:
                post(OPENING, CREDITORS, cr=max(ob, 0), dr=max(-ob, 0), kind="Opening balance", party=party)
                post(OPENING, OBE, dr=max(ob, 0), cr=max(-ob, 0), kind="Opening balance")
    open_stock = stock_value(conn, ws, None, opening=True)
    if open_stock:
        post(OPENING, OPEN_STOCK, dr=open_stock, kind="Opening stock")
        post(OPENING, OBE, cr=open_stock, kind="Opening stock")

    # Documents.
    docs = _rows(conn, "SELECT * FROM hop_party_transactions WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='') ORDER BY date(txn_date), id", (ws,))
    for t in docs:
        ty = int(t.get("txn_type") or 0)
        day = _d(t.get("txn_date"))
        total = _f(t.get("total_amount"))
        imported = int(t.get("source_txn_id") or 0) > 0
        gst = min(max(tax.get(int(t.get("source_txn_id") or 0), 0.0) + _f(t.get("shipping_tax_amount")), 0.0), total) \
            if total >= 0 else 0.0
        tcs = _f(t.get("tcs_amount")) if ty in (1, 2) else 0.0
        taxable = total - gst - tcs
        party = party_of(t)
        label = LABELS.get(ty, t.get("txn_label") or "Document")
        pa = party_acct(t)
        if ty in (1, 2, 16, 21):
            if t.get("received_amount") is not None:
                paid_now = _f(t.get("received_amount"))
            elif imported:
                paid_now = max(0.0, total - _f(t.get("balance_amount")))
            else:
                paid_now = 0.0
            if ty == 1:
                post(day, pa, dr=total, t=t, kind=label, party=party)
                post(day, SALES, cr=taxable, t=t, kind=label)
                post(day, OUT_GST, cr=gst, t=t, kind=label)
                post(day, "TCS payable", cr=tcs, t=t, kind=label)
                if paid_now:
                    post(day, money_acct(t, "in"), dr=paid_now, t=t, kind=label, party=party)
                    post(day, pa, cr=paid_now, t=t, kind=label, party=party)
            elif ty == 21:
                post(day, SALES_RET, dr=taxable, t=t, kind=label)
                post(day, OUT_GST, dr=gst, t=t, kind=label)
                post(day, pa, cr=total, t=t, kind=label, party=party)
                if paid_now:
                    post(day, pa, dr=paid_now, t=t, kind=label, party=party)
                    post(day, money_acct(t, "out"), cr=paid_now, t=t, kind=label, party=party)
            elif ty == 2:
                post(day, PURCH, dr=taxable, t=t, kind=label)
                post(day, IN_GST, dr=gst, t=t, kind=label)
                post(day, "TCS receivable", dr=tcs, t=t, kind=label)
                post(day, pa, cr=total, t=t, kind=label, party=party)
                if paid_now:
                    post(day, pa, dr=paid_now, t=t, kind=label, party=party)
                    post(day, money_acct(t, "out"), cr=paid_now, t=t, kind=label, party=party)
            else:  # 16 purchase return
                post(day, pa, dr=total, t=t, kind=label, party=party)
                post(day, PURCH_RET, cr=taxable, t=t, kind=label)
                post(day, IN_GST, cr=gst, t=t, kind=label)
                if paid_now:
                    post(day, money_acct(t, "in"), dr=paid_now, t=t, kind=label, party=party)
                    post(day, pa, cr=paid_now, t=t, kind=label, party=party)
        elif ty in (3, 4) and imported:
            # Vyapar keeps the part of a payment not set against any bill (an advance) as its
            # balance; what was set against bills is already inside those bills' balances.
            unused = _f(t.get("balance_amount"))
            if ty == 3:
                post(day, IMPORT_CASH, dr=unused, t=t, kind=label, party=party)
                post(day, pa, cr=unused, t=t, kind=label, party=party)
            else:
                post(day, pa, dr=unused, t=t, kind=label, party=party)
                post(day, IMPORT_CASH, cr=unused, t=t, kind=label, party=party)
        # Imported journal entries (81/82) are not posted: their effect on bills is already
        # inside those bills' balances, and paired entries cancel each other.
        elif ty in (3, 4) and not imported:
            if ty == 3:
                post(day, money_acct(t, "in"), dr=total, t=t, kind=label, party=party)
                post(day, pa, cr=total, t=t, kind=label, party=party)
            else:
                post(day, pa, dr=total, t=t, kind=label, party=party)
                post(day, money_acct(t, "out"), cr=total, t=t, kind=label, party=party)
        elif ty == 7:
            cat = cats.get(int(t.get("expense_category_id") or 0)) or (
                "Imported" if imported else (t.get("party_name") or "Other"))
            if t.get("party_type") == "expense":
                party = None
            post(day, f"Expense: {cat}", dr=total, t=t, kind=f"Expense · {cat}", party=party)
            post(day, money_acct(t, "out"), cr=total, t=t, kind=f"Expense · {cat}", party=party)
        elif ty == 29:
            cat = cats.get(int(t.get("expense_category_id") or 0)) or (t.get("party_name") or "Other")
            if t.get("party_type") == "expense":
                party = None
            post(day, money_acct(t, "in"), dr=total, t=t, kind=f"Other Income · {cat}", party=party)
            post(day, f"Other Income: {cat}", cr=total, t=t, kind=f"Other Income · {cat}", party=party)
        # Estimates, orders, challans, proformas, journal entries: no postings.

    # Loans: opening outstanding, money received, EMI / principal, interest & fees.
    cash_name = next((a["name"] for a in banks.values() if a.get("account_type") == "cash"), "Cash in hand")
    try:
        loans = {int(l["id"]): l for l in _rows(conn, "SELECT * FROM hop_loan_accounts WHERE workspace_id=?", (ws,))}
        loan_entries = _rows(conn, "SELECT * FROM hop_loan_entries WHERE workspace_id=? ORDER BY entry_date, id", (ws,))
    except sqlite3.OperationalError:
        loans, loan_entries = {}, []
    for l in loans.values():
        ob = _f(l.get("opening_balance"))
        if ob:
            post(OPENING, OBE, dr=ob, kind="Loan opening")
            post(OPENING, f"Loan: {l['name']}", cr=ob, kind="Loan opening")
    for le in loan_entries:
        l = loans.get(int(le["loan_id"] or 0))
        if not l:
            continue
        acct_name = banks.get(int(le.get("bank_account_id") or 0), {}).get("name") or cash_name
        loan_acct, day, amt, et = f"Loan: {l['name']}", _d(le["entry_date"]), _f(le["amount"]), le["entry_type"]
        t = {"id": None, "txn_number": f"loan:{l['id']}"}
        if et == "disbursement":
            post(day, acct_name, dr=amt, t=t, kind=f"Loan received · {l['name']}")
            post(day, loan_acct, cr=amt, t=t, kind=f"Loan received · {l['name']}")
        elif et in ("emi", "principal"):
            post(day, loan_acct, dr=amt, t=t, kind=f"Loan repaid · {l['name']}")
            post(day, acct_name, cr=amt, t=t, kind=f"Loan repaid · {l['name']}")
        else:  # interest / fee
            post(day, "Expense: Loan interest & fees", dr=amt, t=t, kind=f"Loan {et} · {l['name']}")
            post(day, acct_name, cr=amt, t=t, kind=f"Loan {et} · {l['name']}")

    # Fixed assets: cost (paid from an account, or brought in as owner capital) and depreciation.
    try:
        assets = _rows(conn, "SELECT * FROM hop_fixed_assets WHERE workspace_id=? AND COALESCE(is_active,1)=1", (ws,))
        deps = _rows(conn, "SELECT * FROM hop_asset_depreciations WHERE workspace_id=?", (ws,))
    except sqlite3.OperationalError:
        assets, deps = [], []
    for a in assets:
        cost = _f(a.get("purchase_cost"))
        day = _d(a.get("purchase_date")) or OPENING
        paid = banks.get(int(a.get("paid_account_id") or 0), {}).get("name") if a.get("paid_account_id") else None
        t = {"id": None, "txn_number": f"asset:{a['id']}"}
        post(day, FIXED_ASSETS, dr=cost, t=t, kind=f"Asset bought · {a['name']}")
        post(day, paid or OBE, cr=cost, t=t, kind=f"Asset bought · {a['name']}")
    asset_ids = {int(a["id"]) for a in assets}
    for d in deps:
        if int(d["asset_id"] or 0) not in asset_ids:
            continue
        per = str(d.get("period") or "")[:7]
        try:
            y, m = int(per[:4]), int(per[5:7])
            day = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)).isoformat()
        except ValueError:
            day = OPENING
        post(day, "Expense: Depreciation", dr=_f(d["amount"]), kind="Depreciation")
        post(day, ACC_DEP, cr=_f(d["amount"]), kind="Depreciation")

    # Cash / bank movements not tied to a document's own posting.
    for e in _rows(conn, "SELECT * FROM hop_bank_entries WHERE workspace_id=? ORDER BY entry_date, id", (ws,)):
        src = (e.get("source") or "").lower()
        acct = banks.get(int(e["account_id"] or 0), {}).get("name") or "Bank"
        day, amt = _d(e.get("entry_date")), _f(e.get("amount"))
        t = {"id": e.get("party_txn_id"), "txn_number": e.get("reference") or ""}
        if src in ("doc", "loan", "asset") or str(e.get("reference") or "").startswith(("loan:", "asset:")):
            continue  # posted with its document / loan / fixed asset
        if src == "cheque":
            if e["entry_type"] == "in":
                post(day, acct, dr=amt, t=t, kind="Cheque deposited")
                post(day, CHQ_IN, cr=amt, t=t, kind="Cheque deposited")
            else:
                post(day, CHQ_OUT, dr=amt, t=t, kind="Cheque withdrawn")
                post(day, acct, cr=amt, t=t, kind="Cheque withdrawn")
            continue
        if e["entry_type"] == "transfer":
            to = banks.get(int(e.get("counterparty_account_id") or 0), {}).get("name") or "Bank"
            post(day, to, dr=amt, t=t, kind=f"Transfer from {acct}")
            post(day, acct, cr=amt, t=t, kind=f"Transfer to {to}")
        elif e["entry_type"] == "in":
            if e.get("counterparty_account_id"):
                continue  # the receiving half of a transfer
            post(day, acct, dr=amt, t=t, kind=e.get("notes") or "Money in")
            post(day, CAPITAL, cr=amt, t=t, kind=e.get("notes") or "Money in")
        else:
            post(day, DRAWINGS, dr=amt, t=t, kind=e.get("notes") or "Money out")
            post(day, acct, cr=amt, t=t, kind=e.get("notes") or "Money out")
    return {"postings": P, "bank_names": bank_names | {"Cash in hand"}}


# ── Stock value ──────────────────────────────────────────────────────────────

def stock_value(conn: sqlite3.Connection, ws: str, as_of: str | None, *, opening: bool = False) -> float:
    """Value at purchase price. opening=True: before any stock movement ever.
    as_of: end of that day (later movements taken back out)."""
    prods = _rows(conn, "SELECT id, stock_qty, purchase_price FROM hop_products WHERE workspace_id=?", (ws,))
    if opening:
        moved = {int(r["product_id"]): _f(r["q"]) for r in _rows(
            conn, "SELECT product_id, SUM(qty_delta) AS q FROM hop_stock_journals WHERE workspace_id=? GROUP BY product_id", (ws,))}
    elif as_of:
        moved = {int(r["product_id"]): _f(r["q"]) for r in _rows(
            conn, "SELECT product_id, SUM(qty_delta) AS q FROM hop_stock_journals WHERE workspace_id=? "
                  "AND date(entry_date) > date(?) GROUP BY product_id", (ws, as_of))}
    else:
        moved = {}
    total = 0.0
    for p in prods:
        qty = _f(p["stock_qty"]) - moved.get(int(p["id"]), 0.0)
        total += max(qty, 0.0) * _f(p["purchase_price"])
    return _r2(total)


# ── Financial statements ─────────────────────────────────────────────────────

def _balances(postings, upto: str = "", frm: str = "") -> dict[str, float]:
    bal: dict[str, float] = defaultdict(float)
    for p in postings:
        if _in_range(p["date"], frm, upto) or (p["date"] == OPENING and not frm):
            bal[p["account"]] += p["dr"] - p["cr"]
    return bal


def trial_balance(conn: sqlite3.Connection, ws: str, as_of: str = "") -> dict[str, Any]:
    L = ledger(conn, ws)
    bal = _balances(L["postings"], as_of)
    rows = []
    for name in sorted(bal, key=lambda n: (_account_kind(n, L["bank_names"]), n)):
        v = _r2(bal[name])
        if abs(v) < 0.005:
            continue
        rows.append({"account": name, "group": _account_kind(name, L["bank_names"]),
                     "debit": v if v > 0 else 0.0, "credit": -v if v < 0 else 0.0})
    td, tc = _r2(sum(r["debit"] for r in rows)), _r2(sum(r["credit"] for r in rows))
    return {"as_of": as_of or None, "rows": rows, "total_debit": td, "total_credit": tc,
            "balanced": abs(td - tc) < 0.05}


def profit_and_loss(conn: sqlite3.Connection, ws: str, frm: str = "", to: str = "") -> dict[str, Any]:
    L = ledger(conn, ws)
    mv: dict[str, float] = defaultdict(float)
    for p in L["postings"]:
        if p["date"] != OPENING and _in_range(p["date"], frm, to):
            mv[p["account"]] += p["dr"] - p["cr"]
    if frm:
        before = (date.fromisoformat(frm) - timedelta(days=1)).isoformat()
        opening_stock = stock_value(conn, ws, before)
    else:
        opening_stock = stock_value(conn, ws, None, opening=True)
    closing_stock = stock_value(conn, ws, to or None) if to else stock_value(conn, ws, None)
    sales, sales_ret = -mv[SALES], mv[SALES_RET]
    purch, purch_ret = mv[PURCH], -mv[PURCH_RET]
    net_sales, net_purch = sales - sales_ret, purch - purch_ret
    cogs = opening_stock + net_purch - closing_stock
    gross = net_sales - cogs
    expenses = sorted(({"category": k.replace("Expense: ", ""), "amount": _r2(v)}
                       for k, v in mv.items() if k.startswith("Expense: ") and abs(v) > 0.005),
                      key=lambda x: -x["amount"])
    exp_total = sum(e["amount"] for e in expenses)
    other_income = sorted(({"category": k.replace("Other Income: ", ""), "amount": _r2(-v)}
                           for k, v in mv.items() if k.startswith("Other Income: ") and abs(v) > 0.005),
                          key=lambda x: -x["amount"])
    oi_total = sum(e["amount"] for e in other_income)
    return {
        "from_date": frm or None, "to_date": to or None,
        "sales": _r2(sales), "sale_returns": _r2(sales_ret), "net_sales": _r2(net_sales),
        "opening_stock": _r2(opening_stock), "purchases": _r2(purch), "purchase_returns": _r2(purch_ret),
        "net_purchases": _r2(net_purch), "closing_stock": _r2(closing_stock), "cost_of_goods_sold": _r2(cogs),
        "gross_profit": _r2(gross), "other_income": other_income, "total_other_income": _r2(oi_total),
        "expenses": expenses, "total_expenses": _r2(exp_total),
        "net_profit": _r2(gross + oi_total - exp_total),
    }


def balance_sheet(conn: sqlite3.Connection, ws: str, as_of: str = "") -> dict[str, Any]:
    L = ledger(conn, ws)
    bal = _balances(L["postings"], as_of)
    banks = L["bank_names"]
    cash = [{"name": n, "amount": _r2(v)} for n, v in sorted(bal.items())
            if _account_kind(n, banks) == "cash" and abs(v) > 0.005]
    # Party balances: a customer who paid in advance is a liability, a vendor we overpaid an asset.
    parties = party_balances(L["postings"], as_of)
    debtors = _r2(sum(p["balance"] for p in parties if p["balance"] > 0 and p["account"] == DEBTORS))
    adv_from_cust = _r2(-sum(p["balance"] for p in parties if p["balance"] < 0 and p["account"] == DEBTORS))
    creditors = _r2(-sum(p["balance"] for p in parties if p["balance"] < 0 and p["account"] == CREDITORS))
    adv_to_vendor = _r2(sum(p["balance"] for p in parties if p["balance"] > 0 and p["account"] == CREDITORS))
    net_gst = bal[IN_GST] + bal[OUT_GST]  # +ve = credit to claim, -ve = tax to pay
    closing_stock = stock_value(conn, ws, as_of or None) if as_of else stock_value(conn, ws, None)
    pnl_all = sum(-(v) for n, v in bal.items() if _account_kind(n, banks) in ("income", "expense")) + closing_stock
    cheques_issued = -bal[CHQ_OUT]
    # Fixed assets and loans come from the ledger (so the sheet always balances).
    fa_assets = [{"name": "Fixed Assets (cost)", "amount": _r2(bal[FIXED_ASSETS])}] if abs(bal[FIXED_ASSETS]) > 0.005 else []
    fa_contra = [{"name": "Less: Accumulated Depreciation", "amount": _r2(bal[ACC_DEP])}] if abs(bal[ACC_DEP]) > 0.005 else []
    loan_liab = [{"name": n.replace("Loan: ", "Loan — ", 1), "amount": _r2(-v)} for n, v in sorted(bal.items())
                 if n.startswith("Loan: ") and abs(v) > 0.005]
    assets = [*cash,
              {"name": "Sundry Debtors (to receive)", "amount": debtors},
              {"name": "Advances to vendors", "amount": adv_to_vendor},
              {"name": "Closing stock", "amount": closing_stock},
              {"name": "GST credit (input)", "amount": _r2(net_gst) if net_gst > 0 else 0.0},
              {"name": "TCS paid (to claim)", "amount": _r2(bal["TCS receivable"])},
              *fa_assets, *fa_contra]
    liabilities = [{"name": "Sundry Creditors (to pay)", "amount": creditors},
                   {"name": "Advances from customers", "amount": adv_from_cust},
                   {"name": "GST payable", "amount": _r2(-net_gst) if net_gst < 0 else 0.0},
                   {"name": "Cheques issued (not yet withdrawn)", "amount": _r2(cheques_issued)},
                   {"name": "TCS collected (to deposit)", "amount": _r2(-bal["TCS payable"])},
                   *loan_liab]
    equity = [{"name": "Opening balance equity", "amount": _r2(-bal[OBE])},
              {"name": "Capital added", "amount": _r2(-bal[CAPITAL])},
              {"name": "Drawings", "amount": _r2(-bal[DRAWINGS])},
              {"name": "Profit till date", "amount": _r2(pnl_all)}]
    assets = [a for a in assets if abs(a["amount"]) > 0.005]
    liabilities = [x for x in liabilities if abs(x["amount"]) > 0.005]
    equity = [x for x in equity if abs(x["amount"]) > 0.005]
    ta = _r2(sum(a["amount"] for a in assets))
    tl = _r2(sum(x["amount"] for x in liabilities) + sum(x["amount"] for x in equity))
    return {"as_of": as_of or None, "assets": assets, "liabilities": liabilities, "equity": equity,
            "total_assets": ta, "total_liabilities_equity": tl, "balanced": abs(ta - tl) < 0.05}


def cash_flow(conn: sqlite3.Connection, ws: str, frm: str = "", to: str = "") -> dict[str, Any]:
    L = ledger(conn, ws)
    banks = L["bank_names"]
    by_txn: dict[Any, list[dict]] = defaultdict(list)
    for p in L["postings"]:
        by_txn[(p["txn_id"], p["date"], p["kind"])].append(p)
    opening = 0.0
    inflow: dict[str, float] = defaultdict(float)
    outflow: dict[str, float] = defaultdict(float)
    for p in L["postings"]:
        if _account_kind(p["account"], banks) != "cash":
            continue
        amt = p["dr"] - p["cr"]
        if p["date"] == OPENING or (frm and p["date"] < frm):
            opening += amt
            continue
        if not _in_range(p["date"], frm, to):
            continue
        # Moves between our own cash/bank accounts are not cash flow.
        if p["kind"].startswith("Transfer") or p["kind"] == "Cheque deposited" or p["kind"] == "Cheque withdrawn":
            continue
        key = p["kind"] or "Other"
        if amt >= 0:
            inflow[key] += amt
        else:
            outflow[key] += -amt
    tin, tout = sum(inflow.values()), sum(outflow.values())
    return {"from_date": frm or None, "to_date": to or None, "opening_cash": _r2(opening),
            "inflows": [{"head": k, "amount": _r2(v)} for k, v in sorted(inflow.items(), key=lambda x: -x[1])],
            "outflows": [{"head": k, "amount": _r2(v)} for k, v in sorted(outflow.items(), key=lambda x: -x[1])],
            "total_in": _r2(tin), "total_out": _r2(tout), "closing_cash": _r2(opening + tin - tout)}


# ── Parties ──────────────────────────────────────────────────────────────────

def party_balances(postings, as_of: str = "") -> list[dict[str, Any]]:
    agg: dict[tuple, dict[str, Any]] = {}
    for p in postings:
        if p["account"] not in (DEBTORS, CREDITORS) or not (p["date"] == OPENING or _in_range(p["date"], "", as_of)):
            continue
        k = (p["account"], p["party_type"], p["party_id"], p["party"])
        row = agg.setdefault(k, {"account": p["account"], "party": p["party"], "party_type": p["party_type"],
                                 "party_id": p["party_id"], "balance": 0.0})
        row["balance"] += p["dr"] - p["cr"]
    out = []
    for r in agg.values():
        r["balance"] = _r2(r["balance"])
        if abs(r["balance"]) > 0.005:
            out.append(r)
    return out


def outstanding(conn: sqlite3.Connection, ws: str, as_of: str = "") -> dict[str, Any]:
    rows = party_balances(ledger(conn, ws)["postings"], as_of)
    receive = sorted(({"party": r["party"], "party_id": r["party_id"], "party_type": r["party_type"],
                       "amount": r["balance"] if r["account"] == DEBTORS else r["balance"]}
                      for r in rows if r["balance"] > 0), key=lambda x: -x["amount"])
    pay = sorted(({"party": r["party"], "party_id": r["party_id"], "party_type": r["party_type"], "amount": -r["balance"]}
                  for r in rows if r["balance"] < 0), key=lambda x: -x["amount"])
    return {"as_of": as_of or None, "to_receive": receive, "to_pay": pay,
            "total_to_receive": _r2(sum(r["amount"] for r in receive)),
            "total_to_pay": _r2(sum(r["amount"] for r in pay))}


def party_statement(conn: sqlite3.Connection, ws: str, party_type: str, party_id: int,
                    frm: str = "", to: str = "") -> dict[str, Any]:
    acct = CREDITORS if party_type == "vendor" else DEBTORS
    ps = [p for p in ledger(conn, ws)["postings"]
          if p["account"] == acct and p["party_type"] == party_type and int(p["party_id"] or 0) == int(party_id)]
    opening = sum(p["dr"] - p["cr"] for p in ps if p["date"] == OPENING or (frm and p["date"] < frm))
    run = opening
    lines = []
    for p in sorted((p for p in ps if p["date"] != OPENING and _in_range(p["date"], frm, to)),
                    key=lambda p: (p["date"], p["txn_id"] or 0)):
        run += p["dr"] - p["cr"]
        lines.append({"date": p["date"], "type": p["kind"], "number": p["number"], "txn_id": p["txn_id"],
                      "debit": p["dr"], "credit": p["cr"], "balance": _r2(run)})
    name = ps[0]["party"] if ps else None
    if not name:
        r = conn.execute(f"SELECT company FROM {'hop_vendors' if party_type == 'vendor' else 'hop_customers'} "
                         "WHERE workspace_id=? AND id=?", (ws, int(party_id))).fetchone()
        name = r[0] if r else None
    return {"party": name, "party_type": party_type, "party_id": int(party_id), "from_date": frm or None,
            "to_date": to or None, "opening_balance": _r2(opening), "lines": lines,
            "total_debit": _r2(sum(l["debit"] for l in lines)), "total_credit": _r2(sum(l["credit"] for l in lines)),
            "closing_balance": _r2(run),
            "meaning": "Positive = party owes us (to receive); negative = we owe the party (to pay)."}


# ── Lists ────────────────────────────────────────────────────────────────────

def _docs(conn, ws, frm="", to="", types: tuple[int, ...] | None = None, party_id: int | None = None):
    sql = "SELECT * FROM hop_party_transactions WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='')"
    params: list[Any] = [ws]
    if types:
        sql += f" AND txn_type IN ({','.join('?' * len(types))})"
        params += list(types)
    if frm:
        sql += " AND date(txn_date) >= date(?)"
        params.append(frm)
    if to:
        sql += " AND date(txn_date) <= date(?)"
        params.append(to)
    if party_id:
        sql += " AND party_id=?"
        params.append(int(party_id))
    return _rows(conn, sql + " ORDER BY date(txn_date), id", params)


def _doc_row(t: dict[str, Any], tax: dict[int, float]) -> dict[str, Any]:
    total = _f(t.get("total_amount"))
    gst = min(tax.get(int(t.get("source_txn_id") or 0), 0.0) + _f(t.get("shipping_tax_amount")), total)
    return {"id": t["id"], "date": _d(t.get("txn_date")), "type": LABELS.get(int(t["txn_type"]), t.get("txn_label")),
            "txn_type": int(t["txn_type"]), "number": t.get("txn_number"), "party": t.get("party_name"),
            "taxable": _r2(total - gst), "gst": _r2(gst), "total": _r2(total),
            "balance": _r2(_f(t.get("balance_amount"))), "status": t.get("status_text") or ""}


def all_transactions(conn, ws, frm="", to="", txn_type: int | None = None, party_id: int | None = None) -> dict[str, Any]:
    tax = _doc_tax(conn, ws)
    rows = [_doc_row(t, tax) for t in _docs(conn, ws, frm, to, (int(txn_type),) if txn_type else None, party_id)]
    return {"from_date": frm or None, "to_date": to or None, "rows": rows, "count": len(rows),
            "total": _r2(sum(r["total"] for r in rows))}


def day_book(conn, ws, day: str) -> dict[str, Any]:
    L = ledger(conn, ws)
    banks = L["bank_names"]
    tax = _doc_tax(conn, ws)
    docs = [_doc_row(t, tax) for t in _docs(conn, ws, day, day)]
    money_in = money_out = 0.0
    moves: dict[Any, float] = defaultdict(float)
    for p in L["postings"]:
        if p["date"] == day and _account_kind(p["account"], banks) == "cash" and not p["kind"].startswith("Transfer") \
                and p["kind"] not in ("Cheque deposited", "Cheque withdrawn"):
            amt = p["dr"] - p["cr"]
            moves[p["txn_id"]] += amt
            if amt > 0:
                money_in += amt
            else:
                money_out -= amt
    for d in docs:
        m = moves.get(d["id"], 0.0)
        d["money_in"], d["money_out"] = (_r2(m), 0.0) if m > 0 else (0.0, _r2(-m))
    other = [{"type": p["kind"], "account": p["account"], "amount": _r2(p["dr"] - p["cr"])}
             for p in L["postings"] if p["date"] == day and p["txn_id"] is None
             and _account_kind(p["account"], banks) == "cash"]
    return {"date": day, "rows": docs, "other_money": other, "money_in": _r2(money_in),
            "money_out": _r2(money_out), "net": _r2(money_in - money_out)}


def sale_purchase_report(conn, ws, kind: str, frm="", to="", party_id: int | None = None) -> dict[str, Any]:
    tax = _doc_tax(conn, ws)
    types = (1, 21) if kind == "sale" else (2, 16)
    rows = [_doc_row(t, tax) for t in _docs(conn, ws, frm, to, types, party_id)]
    sign = {types[0]: 1, types[1]: -1}
    tot = {k: _r2(sum(r[k] * sign[r["txn_type"]] for r in rows)) for k in ("taxable", "gst", "total")}
    by_party: dict[str, float] = defaultdict(float)
    for r in rows:
        by_party[r["party"] or "—"] += r["total"] * sign[r["txn_type"]]
    return {"kind": kind, "from_date": frm or None, "to_date": to or None, "rows": rows, **{f"net_{k}": v for k, v in tot.items()},
            "by_party": [{"party": k, "amount": _r2(v)} for k, v in sorted(by_party.items(), key=lambda x: -x[1])]}


def item_report(conn, ws, frm="", to="") -> dict[str, Any]:
    sql = """SELECT l.item_name, l.product_id, t.txn_type, SUM(l.qty) AS qty,
                    SUM(COALESCE(l.line_total,0) - COALESCE(l.tax_amount,0)) AS amount
             FROM hop_txn_lines l JOIN hop_party_transactions t
               ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
             WHERE l.workspace_id=? AND t.txn_type IN (1,2,16,21) AND COALESCE(l.is_charge,0)=0"""
    params: list[Any] = [ws]
    if frm:
        sql += " AND date(t.txn_date) >= date(?)"
        params.append(frm)
    if to:
        sql += " AND date(t.txn_date) <= date(?)"
        params.append(to)
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in _rows(conn, sql + " GROUP BY lower(trim(l.item_name)), t.txn_type", params):
        a = agg[(r["item_name"] or "—").strip()]
        k = {1: "sale", 21: "sale_return", 2: "purchase", 16: "purchase_return"}[int(r["txn_type"])]
        a[f"{k}_qty"] += _f(r["qty"])
        a[f"{k}_amount"] += _f(r["amount"])
    rows = []
    for name, a in agg.items():
        rows.append({"item": name, "sale_qty": _r2(a["sale_qty"] - a["sale_return_qty"]),
                     "sale_amount": _r2(a["sale_amount"] - a["sale_return_amount"]),
                     "purchase_qty": _r2(a["purchase_qty"] - a["purchase_return_qty"]),
                     "purchase_amount": _r2(a["purchase_amount"] - a["purchase_return_amount"])})
    rows.sort(key=lambda r: -r["sale_amount"])
    return {"from_date": frm or None, "to_date": to or None, "rows": rows}


def stock_summary(conn, ws, low_at: float | None = None) -> dict[str, Any]:
    rows = []
    for p in _rows(conn, "SELECT * FROM hop_products WHERE workspace_id=? AND COALESCE(status,'active')='active' "
                         "ORDER BY lower(name)", (ws,)):
        qty = _f(p.get("stock_qty"))
        if low_at is not None and qty > low_at:
            continue
        rows.append({"id": p["id"], "item": p["name"], "code": p.get("code"), "category": p.get("category"),
                     "qty": _r2(qty), "purchase_price": _r2(_f(p.get("purchase_price"))),
                     "sale_price": _r2(_f(p.get("selling_price"))),
                     "stock_value": _r2(max(qty, 0) * _f(p.get("purchase_price")))})
    return {"rows": rows, "total_value": _r2(sum(r["stock_value"] for r in rows)),
            "low_at": low_at}


def stock_movement(conn, ws, product_id: int, frm="", to="") -> dict[str, Any]:
    p = conn.execute("SELECT name, stock_qty FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))).fetchone()
    if not p:
        raise LookupError("Item not found")
    j = _rows(conn, "SELECT * FROM hop_stock_journals WHERE workspace_id=? AND product_id=? ORDER BY date(entry_date), id",
              (ws, int(product_id)))
    opening = _f(p[1]) - sum(_f(x["qty_delta"]) for x in j)
    run = opening + sum(_f(x["qty_delta"]) for x in j if frm and _d(x["entry_date"]) < frm)
    start = run
    lines = []
    for x in j:
        if not _in_range(_d(x["entry_date"]), frm, to):
            continue
        run += _f(x["qty_delta"])
        lines.append({"date": _d(x["entry_date"]), "details": x.get("reason") or "", "in": max(_f(x["qty_delta"]), 0),
                      "out": max(-_f(x["qty_delta"]), 0), "balance": _r2(run)})
    return {"item": p[0], "opening_qty": _r2(start), "lines": lines, "closing_qty": _r2(run)}


def bill_wise_profit(conn, ws, frm="", to="") -> dict[str, Any]:
    tax = _doc_tax(conn, ws)
    prices = {int(r["id"]): _f(r["purchase_price"]) for r in _rows(conn, "SELECT id, purchase_price FROM hop_products WHERE workspace_id=?", (ws,))}
    by_name = {str(r["name"]).strip().lower(): _f(r["purchase_price"]) for r in _rows(conn, "SELECT name, purchase_price FROM hop_products WHERE workspace_id=?", (ws,))}
    rows = []
    for t in _docs(conn, ws, frm, to, (1,)):
        d = _doc_row(t, tax)
        cost, unknown = 0.0, 0
        for ln in _rows(conn, "SELECT * FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=?", (ws, int(t["source_txn_id"]))):
            price = prices.get(int(ln["product_id"])) if ln.get("product_id") else by_name.get(str(ln.get("item_name") or "").strip().lower())
            if price is None or price <= 0:
                unknown += 1
                continue
            cost += _f(ln["qty"]) * price
        rows.append({**d, "cost": _r2(cost), "profit": _r2(d["taxable"] - cost), "lines_without_cost": unknown})
    return {"from_date": frm or None, "to_date": to or None, "rows": rows,
            "total_sale": _r2(sum(r["taxable"] for r in rows)), "total_cost": _r2(sum(r["cost"] for r in rows)),
            "total_profit": _r2(sum(r["profit"] for r in rows)),
            "note": "Cost = qty × the item's purchase price. Lines whose item has no purchase price count as 0 cost."}


def other_income_report(conn, ws, frm="", to="") -> dict[str, Any]:
    cats = {int(c["id"]): c["name"] for c in _rows(conn, "SELECT id, name FROM hop_expense_categories WHERE workspace_id=?", (ws,))}
    rows, by_cat = [], defaultdict(float)
    for t in _docs(conn, ws, frm, to, (29,)):
        cat = cats.get(int(t.get("expense_category_id") or 0)) or "Other"
        rows.append({"id": t["id"], "date": _d(t["txn_date"]), "number": t.get("txn_number"), "category": cat,
                     "party": t.get("party_name") if t.get("party_type") != "expense" else "",
                     "amount": _r2(_f(t["total_amount"])), "notes": t.get("notes") or ""})
        by_cat[cat] += _f(t["total_amount"])
    return {"from_date": frm or None, "to_date": to or None, "rows": rows,
            "by_category": [{"category": k, "amount": _r2(v)} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])],
            "total": _r2(sum(by_cat.values()))}


def expense_report(conn, ws, frm="", to="") -> dict[str, Any]:
    cats = {int(c["id"]): c["name"] for c in _rows(conn, "SELECT id, name FROM hop_expense_categories WHERE workspace_id=?", (ws,))}
    rows, by_cat = [], defaultdict(float)
    for t in _docs(conn, ws, frm, to, (7,)):
        cat = cats.get(int(t.get("expense_category_id") or 0)) or ("Imported" if int(t.get("source_txn_id") or 0) > 0 else "Other")
        rows.append({"id": t["id"], "date": _d(t["txn_date"]), "number": t.get("txn_number"), "category": cat,
                     "party": t.get("party_name") if t.get("party_type") != "expense" else "",
                     "amount": _r2(_f(t["total_amount"])), "notes": t.get("notes") or ""})
        by_cat[cat] += _f(t["total_amount"])
    return {"from_date": frm or None, "to_date": to or None, "rows": rows,
            "by_category": [{"category": k, "amount": _r2(v)} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])],
            "total": _r2(sum(by_cat.values()))}


# ── GST ──────────────────────────────────────────────────────────────────────

def _state_code(gstin: str | None) -> str:
    g = (gstin or "").strip()
    return g[:2] if len(g) >= 2 and g[:2].isdigit() else ""


def _gst_docs(conn, ws, types, frm, to):
    firm = conn.execute("SELECT gstin, state FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
    firm_state = _state_code(firm[0]) if firm else ""
    firm_state_name = (firm[1] or "").strip().lower() if firm else ""
    out = []
    for t in _docs(conn, ws, frm, to, types):
        table = "hop_vendors" if t.get("party_type") == "vendor" else "hop_customers"
        party = conn.execute(f"SELECT gst_no, state FROM {table} WHERE workspace_id=? AND id=?",
                             (ws, int(t.get("party_id") or 0))).fetchone() if t.get("party_id") else None
        gstin = (party[0] or "").strip().upper() if party else ""
        pstate = _state_code(gstin)
        pos = str(t.get("place_of_supply") or "").strip()
        if pos:
            # Place of supply on the bill decides: supplier state vs where goods/services are supplied.
            supplier = firm_state if int(t["txn_type"]) in (1, 21) else pstate
            inter = bool(supplier and supplier != pos)
        else:
            inter = bool(pstate and firm_state and pstate != firm_state) or bool(
                not pstate and party and party[1] and firm_state_name and party[1].strip().lower() != firm_state_name)
        lines = _rows(conn, "SELECT tax_pct, SUM(COALESCE(line_total,0)-COALESCE(tax_amount,0)) AS taxable, "
                            "SUM(COALESCE(tax_amount,0)-COALESCE(cess_amount,0)) AS tax, SUM(COALESCE(cess_amount,0)) AS cess "
                            "FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=? "
                            "GROUP BY tax_pct", (ws, int(t["source_txn_id"])))
        if _f(t.get("shipping_amount")):
            lines.append({"tax_pct": _f(t.get("shipping_tax_pct")), "taxable": _f(t.get("shipping_amount")),
                          "tax": _f(t.get("shipping_tax_amount"))})
        for ln in lines:
            tax = _f(ln["tax"])
            out.append({"id": t["id"], "date": _d(t["txn_date"]), "number": t.get("txn_number"), "party": t.get("party_name"),
                        "gstin": gstin, "txn_type": int(t["txn_type"]), "rate": _f(ln["tax_pct"]),
                        "taxable": _r2(_f(ln["taxable"])), "igst": _r2(tax if inter else 0),
                        "cgst": _r2(0 if inter else tax / 2), "sgst": _r2(0 if inter else tax / 2),
                        "cess": _r2(_f(ln.get("cess"))),
                        "invoice_value": _r2(_f(t["total_amount"])), "place_of_supply": pos or None,
                        "reverse_charge": bool(t.get("reverse_charge"))})
    return out


def _sum(rows, sign=None):
    s = lambda r: (sign or {}).get(r["txn_type"], 1)
    return {k: _r2(sum(r.get(k, 0) * s(r) for r in rows)) for k in ("taxable", "igst", "cgst", "sgst", "cess")}


def gstr1(conn, ws, frm="", to="") -> dict[str, Any]:
    rows = _gst_docs(conn, ws, (1, 21), frm, to)
    b2b = [r for r in rows if r["txn_type"] == 1 and r["gstin"]]
    b2c = [r for r in rows if r["txn_type"] == 1 and not r["gstin"]]
    cdn = [r for r in rows if r["txn_type"] == 21]
    b2c_rate: dict[float, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in b2c:
        for k in ("taxable", "igst", "cgst", "sgst", "cess"):
            b2c_rate[r["rate"]][k] += r.get(k, 0)
    return {"from_date": frm or None, "to_date": to or None, "b2b": b2b,
            "b2c_by_rate": [{"rate": k, **{x: _r2(v) for x, v in d.items()}} for k, d in sorted(b2c_rate.items())],
            "credit_notes": cdn, "totals": _sum(rows, {21: -1})}


def gstr2(conn, ws, frm="", to="") -> dict[str, Any]:
    rows = _gst_docs(conn, ws, (2, 16), frm, to)
    return {"from_date": frm or None, "to_date": to or None,
            "with_gstin": [r for r in rows if r["gstin"]], "without_gstin": [r for r in rows if not r["gstin"]],
            "totals": _sum(rows, {16: -1}),
            "itc_note": "Only bills from GST-registered vendors (with GSTIN) give input tax credit."}


def gstr3b(conn, ws, frm="", to="") -> dict[str, Any]:
    out_rows = _gst_docs(conn, ws, (1, 21), frm, to)
    all_in = _gst_docs(conn, ws, (2, 16), frm, to)
    rcm_rows = [r for r in all_in if r["reverse_charge"]]
    # Reverse charge: we pay that GST in cash, and it is also eligible as ITC.
    in_rows = [r for r in all_in if r["gstin"] or r["reverse_charge"]]
    outward, itc = _sum(out_rows, {21: -1}), _sum(in_rows, {16: -1})
    rcm = _sum(rcm_rows, {16: -1})
    payable, carry = _set_off(outward, itc)
    return {"from_date": frm or None, "to_date": to or None, "outward_supplies": outward, "eligible_itc": itc,
            "tax_payable": payable, "credit_carried_forward": carry,
            "reverse_charge_inward": rcm,
            "rcm_tax_to_pay_in_cash": _r2(rcm["igst"] + rcm["cgst"] + rcm["sgst"]),
            "total_payable": _r2(sum(payable.values()) + rcm["igst"] + rcm["cgst"] + rcm["sgst"]),
            "note": "ITC used in the GST order: IGST credit → IGST, CGST, SGST; CGST credit → CGST, IGST; "
                    "SGST credit → SGST, IGST (CGST and SGST never against each other). Check before filing."}


def _set_off(tax: dict[str, float], itc: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    due = {k: max(_f(tax.get(k)), 0.0) for k in ("igst", "cgst", "sgst")}
    credit = {k: max(_f(itc.get(k)), 0.0) for k in ("igst", "cgst", "sgst")}
    order = (("igst", ("igst", "cgst", "sgst")), ("cgst", ("cgst", "igst")), ("sgst", ("sgst", "igst")))
    for head, against in order:
        for t in against:
            use = min(credit[head], due[t])
            credit[head] -= use
            due[t] -= use
    return {k: _r2(v) for k, v in due.items()}, {k: _r2(v) for k, v in credit.items()}


def hsn_summary(conn, ws, kind: str = "sale", frm="", to="") -> dict[str, Any]:
    types = (1, 21) if kind == "sale" else (2, 16)
    sql = f"""SELECT COALESCE(NULLIF(trim(l.hsn),''),'(blank)') AS hsn, l.tax_pct AS rate, t.txn_type,
                     SUM(l.qty) AS qty, SUM(COALESCE(l.line_total,0)-COALESCE(l.tax_amount,0)) AS taxable,
                     SUM(COALESCE(l.tax_amount,0)) AS tax
              FROM hop_txn_lines l JOIN hop_party_transactions t
                ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
              WHERE l.workspace_id=? AND t.txn_type IN ({','.join('?' * len(types))})"""
    params: list[Any] = [ws, *types]
    if frm:
        sql += " AND date(t.txn_date) >= date(?)"
        params.append(frm)
    if to:
        sql += " AND date(t.txn_date) <= date(?)"
        params.append(to)
    agg: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in _rows(conn, sql + " GROUP BY hsn, rate, t.txn_type", params):
        s = -1 if int(r["txn_type"]) in (21, 16) else 1
        a = agg[(r["hsn"], _f(r["rate"]))]
        for k in ("qty", "taxable", "tax"):
            a[k] += s * _f(r[k])
    rows = [{"hsn": h, "rate": rt, **{k: _r2(v) for k, v in a.items()}} for (h, rt), a in sorted(agg.items())]
    return {"kind": kind, "from_date": frm or None, "to_date": to or None, "rows": rows,
            "taxable_total": _r2(sum(r["taxable"] for r in rows)), "tax_total": _r2(sum(r["tax"] for r in rows))}


# ── One entry point for the route ────────────────────────────────────────────

REPORTS = {
    "trial_balance": "Trial Balance", "profit_loss": "Profit & Loss", "balance_sheet": "Balance Sheet",
    "cash_flow": "Cash Flow", "day_book": "Day Book", "all_transactions": "All Transactions",
    "party_statement": "Party Statement", "outstanding": "Party-wise Outstanding",
    "sale_report": "Sale Report", "purchase_report": "Purchase Report", "item_report": "Item-wise Report",
    "stock_summary": "Stock Summary", "low_stock": "Low Stock", "stock_movement": "Stock Movement",
    "bill_profit": "Bill-wise Profit", "expense_report": "Expense Report", "other_income": "Other Income",
    "gstr1": "GSTR-1", "gstr2": "GSTR-2", "gstr3b": "GSTR-3B", "hsn_sale": "HSN Summary (Sales)",
    "hsn_purchase": "HSN Summary (Purchases)",
}
from app.hop_reports_more import MORE_REPORTS as _MORE  # noqa: E402

REPORTS.update(_MORE)


def run(conn: sqlite3.Connection, ws: str, key: str, a: dict[str, Any]) -> dict[str, Any]:
    from app.hop_schema import ensure_hop_schema  # noqa: F401  (callers ensure schema)

    frm, to = _d(a.get("from")), _d(a.get("to"))
    k = key.replace("-", "_")
    from app import hop_reports_more

    more = hop_reports_more.run(conn, ws, k, a)
    if more is not None:
        return more
    if k == "trial_balance":
        return trial_balance(conn, ws, to)
    if k == "profit_loss":
        return profit_and_loss(conn, ws, frm, to)
    if k == "balance_sheet":
        return balance_sheet(conn, ws, to)
    if k == "cash_flow":
        return cash_flow(conn, ws, frm, to)
    if k == "day_book":
        return day_book(conn, ws, _d(a.get("date")) or to or date.today().isoformat())
    if k == "all_transactions":
        return all_transactions(conn, ws, frm, to, int(a["txn_type"]) if a.get("txn_type") else None,
                                int(a["party_id"]) if a.get("party_id") else None)
    if k == "party_statement":
        if not a.get("party_id"):
            raise ValueError("Choose a party")
        return party_statement(conn, ws, (a.get("party_type") or "customer"), int(a["party_id"]), frm, to)
    if k == "outstanding":
        return outstanding(conn, ws, to)
    if k in ("sale_report", "purchase_report"):
        return sale_purchase_report(conn, ws, "sale" if k == "sale_report" else "purchase", frm, to,
                                    int(a["party_id"]) if a.get("party_id") else None)
    if k == "item_report":
        return item_report(conn, ws, frm, to)
    if k == "stock_summary":
        return stock_summary(conn, ws)
    if k == "low_stock":
        return stock_summary(conn, ws, low_at=_f(a.get("low_at") or 0))
    if k == "stock_movement":
        if not a.get("product_id"):
            raise ValueError("Choose an item")
        return stock_movement(conn, ws, int(a["product_id"]), frm, to)
    if k == "bill_profit":
        return bill_wise_profit(conn, ws, frm, to)
    if k == "expense_report":
        return expense_report(conn, ws, frm, to)
    if k == "other_income":
        return other_income_report(conn, ws, frm, to)
    if k == "gstr1":
        return gstr1(conn, ws, frm, to)
    if k == "gstr2":
        return gstr2(conn, ws, frm, to)
    if k == "gstr3b":
        return gstr3b(conn, ws, frm, to)
    if k in ("hsn_sale", "hsn_purchase"):
        return hsn_summary(conn, ws, "sale" if k == "hsn_sale" else "purchase", frm, to)
    raise LookupError(f"Unknown report: {key}")
