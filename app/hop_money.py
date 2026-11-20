"""HoP money flow, Vyapar-style.

Every saved document "posts" its effects here, and every effect can be undone
and posted again, so editing or deleting a document never leaves a wrong
balance behind:

* Cash / bank: money received or paid now goes to a Cash or Bank account
  (``hop_bank_entries`` with ``source='doc'``). A cheque waits in
  ``hop_cheques`` until it is deposited or withdrawn (then ``source='cheque'``).
* Bills: an invoice / bill balance = total − paid now − linked payments
  (``hop_payment_links``). A Payment-In/Out keeps the part not linked to any
  bill as ``unused_amount``.
* Stock: Sale Invoice and Purchase Return take stock out, Purchase Bill and
  Sale Return put it back (``hop_stock_journals`` with reference ``doc:<id>``).
* Convert: Estimate → Sale Order / Invoice, Purchase Order → Bill … the new
  document remembers where it came from and the old one shows "Converted".

Account balances and bill balances are always recomputed from their entries.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# Which documents move money, and which way (amount comes from received_amount
# for invoices/bills/returns, from the total for payments and expenses).
MONEY_IN = {1, 3, 16, 29}  # Sale Invoice, Payment-In, Purchase Return, Other Income
MONEY_OUT = {2, 4, 7, 21}  # Purchase Bill, Payment-Out, Expense, Sale Return
FULL_AMOUNT = {3, 4, 7, 29}  # the whole document is the payment
BILLS = {1, 2, 16, 21}     # documents with a balance that payments can clear
PAYMENT_FOR = {3: 1, 4: 2}  # Payment-In clears Sale Invoices, Payment-Out clears Purchase Bills
STOCK_SIGN = {1: -1, 16: -1, 2: 1, 21: 1}

# What each document can be converted into (Vyapar's "Convert to …").
CONVERT_TO: dict[int, tuple[int, ...]] = {
    27: (1, 65, 83),   # Estimate → Sale Invoice, Sale Order, Proforma
    83: (1, 65),       # Proforma → Sale Invoice, Sale Order
    65: (1, 30),       # Sale Order → Sale Invoice, Delivery Challan
    30: (1,),          # Delivery Challan → Sale Invoice
    82: (1,),
    66: (2,),          # Purchase Order → Purchase Bill
}
LABELS = {
    1: "Sale Invoice", 2: "Purchase Bill", 3: "Payment-In", 4: "Payment-Out", 7: "Expense", 29: "Other Income",
    16: "Purchase Return", 21: "Sale Return", 27: "Estimate", 30: "Delivery Challan",
    65: "Sale Order", 66: "Purchase Order", 81: "Journal Entry", 82: "Delivery Challan",
    83: "Proforma Invoice",
}
EPS = 0.009


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def _row(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                     (ws, int(txn_id))).fetchone()
    return dict(r) if r else None


# ── Cash & bank accounts ─────────────────────────────────────────────────────

def ensure_cash_account(conn: sqlite3.Connection, ws: str) -> int:
    """Every company has one "Cash in hand" account, like Vyapar."""
    r = conn.execute("SELECT id FROM hop_bank_accounts WHERE workspace_id=? AND account_type='cash' "
                     "AND COALESCE(is_active,1)=1 ORDER BY id LIMIT 1", (ws,)).fetchone()
    if r:
        return int(r[0])
    now = _now()
    cur = conn.execute(
        """INSERT INTO hop_bank_accounts (workspace_id, name, account_type, opening_balance,
               current_balance, is_active, created_at, updated_at)
           VALUES (?, 'Cash in hand', 'cash', 0, 0, 1, ?, ?)""", (ws, now, now))
    return int(cur.lastrowid)


def recompute_account(conn: sqlite3.Connection, ws: str, account_id: int | None) -> None:
    if not account_id:
        return
    r = conn.execute(
        """SELECT a.opening_balance,
                  COALESCE((SELECT SUM(CASE WHEN e.entry_type='in' THEN e.amount ELSE -e.amount END)
                            FROM hop_bank_entries e WHERE e.workspace_id=a.workspace_id AND e.account_id=a.id), 0)
           FROM hop_bank_accounts a WHERE a.workspace_id=? AND a.id=?""", (ws, int(account_id))).fetchone()
    if r is None:
        return
    conn.execute("UPDATE hop_bank_accounts SET current_balance=?, updated_at=? WHERE workspace_id=? AND id=?",
                 (round(_f(r[0]) + _f(r[1]), 2), _now(), ws, int(account_id)))


def _account(conn: sqlite3.Connection, ws: str, account_id: Any) -> dict[str, Any]:
    try:
        aid = int(account_id)
    except (TypeError, ValueError):
        aid = 0
    r = conn.execute("SELECT * FROM hop_bank_accounts WHERE workspace_id=? AND id=? AND COALESCE(is_active,1)=1",
                     (ws, aid)).fetchone() if aid else None
    if r is None:
        raise ValueError("Choose a Cash or Bank account for the payment")
    return dict(r)


# Reconciliation marks of a document's old bank entries, kept while its money is re-posted.
_CLEARED_CARRY: dict[tuple[str, int], dict[tuple[int, str, float], str]] = {}


def _bank_entry(conn, ws, account_id, direction, amount, date, txn, source, reference="", notes=""):
    carry = _CLEARED_CARRY.get((ws, int(txn["id"])), {}).pop((int(account_id), direction, round(amount, 2)), None)
    conn.execute(
        """INSERT INTO hop_bank_entries (workspace_id, account_id, entry_date, entry_type, amount,
               party_type, party_id, party_txn_id, reference, notes, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ws, int(account_id), (date or _now())[:10], direction, round(amount, 2),
         txn.get("party_type"), txn.get("party_id"), int(txn["id"]), reference,
         notes or f"{LABELS.get(int(txn.get('txn_type') or 0), 'Document')} {txn.get('txn_number') or ''}".strip(),
         source, _now()))
    if carry:
        conn.execute("UPDATE hop_bank_entries SET cleared_on=? WHERE id=last_insert_rowid()", (carry,))
    recompute_account(conn, ws, account_id)


# ── Bill balances and payment links ──────────────────────────────────────────

def _linked_total(conn, ws, *, bill: int | None = None, payment: int | None = None) -> float:
    if bill is not None:
        r = conn.execute("SELECT COALESCE(SUM(amount),0) FROM hop_payment_links WHERE workspace_id=? AND bill_txn_id=?",
                         (ws, int(bill))).fetchone()
    else:
        r = conn.execute("SELECT COALESCE(SUM(amount),0) FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?",
                         (ws, int(payment))).fetchone()
    return _f(r[0])


def _freeze_received(conn, ws, bill: dict[str, Any]) -> None:
    """Older / imported bills have no received_amount: take what was already paid
    from their balance once, before the first payment is linked."""
    if bill.get("received_amount") is None:
        paid = max(0.0, _f(bill.get("total_amount")) - _f(bill.get("balance_amount")))
        conn.execute("UPDATE hop_party_transactions SET received_amount=? WHERE workspace_id=? AND id=?",
                     (round(paid, 2), ws, int(bill["id"])))
        bill["received_amount"] = round(paid, 2)


def recompute_balance(conn: sqlite3.Connection, ws: str, txn_id: int) -> None:
    t = _row(conn, ws, txn_id)
    if not t:
        return
    ty = int(t.get("txn_type") or 0)
    total = _f(t.get("total_amount"))
    if ty in BILLS:
        if t.get("received_amount") is None and _linked_total(conn, ws, bill=txn_id) <= EPS:
            return  # untouched imported bill: keep its own balance
        _freeze_received(conn, ws, t)
        tds = _f(t.get("tds_amount")) if ty in (1, 2) else 0.0   # TDS deducted is not paid in money
        bal = round(max(0.0, total - tds - _f(t.get("received_amount")) - _linked_total(conn, ws, bill=txn_id)), 2)
        status = "Paid" if bal <= EPS else ("Partial" if bal < total - EPS else "Unpaid")
        if ty in (16, 21):
            status = {"Paid": "Settled", "Partial": "Partly settled", "Unpaid": "Not settled"}[status]
        conn.execute("UPDATE hop_party_transactions SET balance_amount=?, status_text=?, updated_at=datetime('now') "
                     "WHERE workspace_id=? AND id=?", (bal, status, ws, int(txn_id)))
    elif ty in PAYMENT_FOR:
        unused = round(max(0.0, total - _linked_total(conn, ws, payment=txn_id)), 2)
        status = "Used" if unused <= EPS else ("Partly used" if unused < total - EPS else "Unused")
        conn.execute("UPDATE hop_party_transactions SET balance_amount=0, unused_amount=?, status_text=?, "
                     "updated_at=datetime('now') WHERE workspace_id=? AND id=?", (unused, status, ws, int(txn_id)))


def open_bills(conn: sqlite3.Connection, ws: str, payment_type: int, party_type: str, party_id: int,
               payment_txn_id: int | None = None) -> list[dict[str, Any]]:
    """Bills a Payment-In/Out can clear, with what is still due (plus what this
    payment already put on each, so editing a payment shows its own links)."""
    bill_type = PAYMENT_FOR.get(int(payment_type))
    if not bill_type:
        return []
    rows = conn.execute(
        """SELECT * FROM hop_party_transactions WHERE (deleted_at IS NULL OR trim(deleted_at)='') AND workspace_id=? AND txn_type=? AND party_type=? AND party_id=?
           ORDER BY date(txn_date), id""", (ws, bill_type, party_type, int(party_id))).fetchall()
    mine: dict[int, float] = {}
    if payment_txn_id:
        for r in conn.execute("SELECT bill_txn_id, amount FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?",
                              (ws, int(payment_txn_id))):
            mine[int(r[0])] = mine.get(int(r[0]), 0.0) + _f(r[1])
    out = []
    for r in rows:
        d = dict(r)
        due = round(_f(d.get("balance_amount")) + mine.get(int(d["id"]), 0.0), 2)
        if due <= EPS and int(d["id"]) not in mine:
            continue
        out.append({"id": d["id"], "txn_number": d.get("txn_number"), "txn_date": (d.get("txn_date") or "")[:10],
                    "total_amount": _f(d.get("total_amount")), "due": due,
                    "linked_here": round(mine.get(int(d["id"]), 0.0), 2)})
    return out


# ── Posting a document ───────────────────────────────────────────────────────

def _undo_money(conn, ws, txn_id: int, *, keep_closed_cheque: bool) -> None:
    accts = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT account_id FROM hop_bank_entries WHERE workspace_id=? AND party_txn_id=? AND source='doc'",
        (ws, int(txn_id)))}
    try:
        _CLEARED_CARRY[(ws, int(txn_id))] = {
            (int(r[0]), r[1], round(float(r[2]), 2)): r[3] for r in conn.execute(
                "SELECT account_id, entry_type, amount, cleared_on FROM hop_bank_entries WHERE workspace_id=? "
                "AND party_txn_id=? AND source='doc' AND cleared_on IS NOT NULL AND cleared_on!=''", (ws, int(txn_id)))}
    except sqlite3.OperationalError:
        pass
    conn.execute("DELETE FROM hop_bank_entries WHERE workspace_id=? AND party_txn_id=? AND source='doc'", (ws, int(txn_id)))
    for a in accts:
        recompute_account(conn, ws, a)
    if not keep_closed_cheque:
        conn.execute("DELETE FROM hop_cheques WHERE workspace_id=? AND party_txn_id=? AND status='open'", (ws, int(txn_id)))


def _undo_stock(conn, ws, txn_id: int) -> None:
    ref = f"doc:{int(txn_id)}"
    for r in conn.execute("SELECT product_id, qty_delta FROM hop_stock_journals WHERE workspace_id=? AND reference=?",
                          (ws, ref)).fetchall():
        conn.execute("UPDATE hop_products SET stock_qty=COALESCE(stock_qty,0)-?, updated_at=? WHERE workspace_id=? AND id=?",
                     (_f(r[1]), _now(), ws, int(r[0])))
    conn.execute("DELETE FROM hop_stock_journals WHERE workspace_id=? AND reference=?", (ws, ref))


def _match_product(conn, ws, line: dict[str, Any]) -> int | None:
    pid = line.get("product_id")
    if pid:
        r = conn.execute("SELECT id FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(pid))).fetchone()
        if r:
            return int(r[0])
    code = _s(line.get("item_code")) or _s(line.get("barcode"))
    if code:
        r = conn.execute("SELECT id FROM hop_products WHERE workspace_id=? AND (lower(code)=lower(?) OR barcode=?) LIMIT 1",
                         (ws, code, code)).fetchone()
        if r:
            return int(r[0])
    name = _s(line.get("item_name"))
    if name:
        r = conn.execute("SELECT id FROM hop_products WHERE workspace_id=? AND lower(trim(name))=lower(?) LIMIT 1",
                         (ws, name)).fetchone()
        if r:
            return int(r[0])
    return None


def _post_stock(conn, ws, txn: dict[str, Any], new: bool = False) -> None:
    sign = STOCK_SIGN.get(int(txn.get("txn_type") or 0))
    if not sign:
        return
    # Distributor: a company's invoice (CI) is "in transit" — stock comes in on Goods received (owner 21-11).
    from app import hop_dist_receive

    if hop_dist_receive.waits_for_goods(conn, ws, txn, new):
        return
    lines = conn.execute("SELECT * FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=?",
                         (ws, int(txn["source_txn_id"]))).fetchall()
    for ln in lines:
        ln = dict(ln)
        if ln.get("is_charge"):
            continue  # packing / freight charges are not items
        pid = _match_product(conn, ws, ln)
        if not pid:
            continue  # free-text lines do not move stock
        from app import hop_items

        qty = hop_items.stock_qty_for_line(conn, ws, pid, ln)  # None = service item; 2nd unit → base units
        if qty is None or qty <= 0:
            continue
        if not ln.get("product_id"):
            conn.execute("UPDATE hop_txn_lines SET product_id=? WHERE id=?", (pid, int(ln["id"])))
        delta = sign * qty
        if delta < 0:
            # Settings → "When stock is not enough": block the sale.
            try:
                from app import hop_app_settings

                if hop_app_settings.get(conn, ws, "short_stock") == "block":
                    have = _f(conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (pid,)).fetchone()[0])
                    if have + delta < -1e-9:
                        raise ValueError(f"Only {have:g} {ln.get('unit') or ''} of {ln.get('item_name')} in stock "
                                         "(Settings: don't allow the sale).")
            except sqlite3.OperationalError:
                pass
        # every stock line says which document and (for a supplier) which company it came from
        hop_dist_receive.ensure_cols(conn)
        conn.execute(
            """INSERT INTO hop_stock_journals (workspace_id, product_id, entry_date, qty_delta, reason, reference, created_at,
                                               godown_id, doc_txn_id, vendor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ws, pid, (txn.get("txn_date") or _now())[:10], delta,
             f"{LABELS.get(int(txn['txn_type']), 'Document')} {txn.get('txn_number') or ''}".strip(),
             f"doc:{int(txn['id'])}", _now(), txn.get("godown_id") or None, int(txn["id"]),
             int(txn["party_id"]) if txn.get("party_type") == "vendor" and txn.get("party_id") else None))
        conn.execute("UPDATE hop_products SET stock_qty=COALESCE(stock_qty,0)+?, updated_at=? WHERE workspace_id=? AND id=?",
                     (delta, _now(), ws, pid))


def _set_links(conn, ws, pay: dict[str, Any], allocations: list[dict[str, Any]]) -> None:
    bill_type = PAYMENT_FOR[int(pay["txn_type"])]
    old = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT bill_txn_id FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?",
        (ws, int(pay["id"])))]
    conn.execute("DELETE FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?", (ws, int(pay["id"])))
    for b in old:
        recompute_balance(conn, ws, b)
    used = 0.0
    for a in allocations or []:
        amt = round(_f(a.get("amount")), 2)
        if amt <= EPS:
            continue
        bill = _row(conn, ws, int(a.get("txn_id") or a.get("bill_txn_id") or 0))
        if bill and bill.get("deleted_at"):
            raise ValueError(f"{bill.get('txn_number') or 'That bill'} is in the recycle bin")
        if not bill or int(bill.get("txn_type") or 0) != bill_type or bill.get("party_type") != pay.get("party_type") \
                or int(bill.get("party_id") or 0) != int(pay.get("party_id") or 0):
            raise ValueError("A linked bill does not belong to this party")
        _freeze_received(conn, ws, bill)
        recompute_balance(conn, ws, int(bill["id"]))
        due = _f(_row(conn, ws, int(bill["id"]))["balance_amount"])
        if amt > due + EPS:
            raise ValueError(f"{bill.get('txn_number') or 'Bill'} has only ₹{due:,.2f} due")
        used += amt
        if used > _f(pay.get("total_amount")) + EPS:
            raise ValueError("Linked amount is more than the payment")
        conn.execute("INSERT INTO hop_payment_links (workspace_id, payment_txn_id, bill_txn_id, amount, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (ws, int(pay["id"]), int(bill["id"]), amt, _now()))
        recompute_balance(conn, ws, int(bill["id"]))


def unused_payments(conn: sqlite3.Connection, ws: str, bill_type: int, party_type: str, party_id: int) -> list[dict[str, Any]]:
    """Payment-In (sale side) / Payment-Out (purchase side) of this party with money not yet put on any bill."""
    pay_type = next((p for p, b in PAYMENT_FOR.items() if b == int(bill_type)), None)
    if not pay_type:
        return []
    rows = conn.execute(
        """SELECT id, txn_number, txn_date, total_amount, unused_amount, payment_mode, payment_ref FROM hop_party_transactions
           WHERE workspace_id=? AND txn_type=? AND party_type=? AND party_id=? AND (deleted_at IS NULL OR trim(deleted_at)='')
           ORDER BY date(txn_date), id""", (ws, int(pay_type), party_type, int(party_id))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        unused = round(_f(d.get("total_amount")) - _linked_total(conn, ws, payment=int(d["id"])), 2)
        if unused > EPS:
            d["unused_amount"] = unused
            out.append(d)
    return out


def link_payments_to_bill(conn: sqlite3.Connection, ws: str, bill_id: int, links: list[dict[str, Any]]) -> None:
    """Put money from earlier Payment-In / Out on this bill (Vyapar: link payment on the invoice).
    Each amount ≤ what that payment still has unused and ≤ what the bill still owes."""
    bill = _row(conn, ws, int(bill_id))
    if not bill:
        raise ValueError("Bill not found")
    bill_type = int(bill.get("txn_type") or 0)
    pay_type = next((p for p, b in PAYMENT_FOR.items() if b == bill_type), None)
    if not pay_type:
        raise ValueError("Payments can be linked only to sale invoices or purchase bills")
    for ln in links or []:
        amt = round(_f(ln.get("amount")), 2)
        if amt <= EPS:
            continue
        pay = _row(conn, ws, int(ln.get("payment_txn_id") or 0))
        if not pay or int(pay.get("txn_type") or 0) != pay_type or pay.get("deleted_at") \
                or pay.get("party_type") != bill.get("party_type") or int(pay.get("party_id") or 0) != int(bill.get("party_id") or 0):
            raise ValueError("That payment does not belong to this party")
        unused = round(_f(pay.get("total_amount")) - _linked_total(conn, ws, payment=int(pay["id"])), 2)
        if amt > unused + EPS:
            raise ValueError(f"{pay.get('txn_number') or 'Payment'} has only ₹{unused:,.2f} unused")
        _freeze_received(conn, ws, bill)
        recompute_balance(conn, ws, int(bill["id"]))
        due = _f(_row(conn, ws, int(bill["id"]))["balance_amount"])
        if amt > due + EPS:
            raise ValueError(f"The bill has only ₹{due:,.2f} due")
        conn.execute("INSERT INTO hop_payment_links (workspace_id, payment_txn_id, bill_txn_id, amount, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (ws, int(pay["id"]), int(bill["id"]), amt, _now()))
        recompute_balance(conn, ws, int(bill["id"]))
        recompute_balance(conn, ws, int(pay["id"]))
    conn.commit()


def post_document(conn: sqlite3.Connection, ws: str, txn_id: int, payload: dict[str, Any],
                  *, is_update: bool = False) -> None:
    """Apply money, cheque, stock, links and Convert effects for a saved manual
    document. Safe to call again after an edit: old effects are undone first."""
    t = _row(conn, ws, txn_id)
    if not t:
        return
    if t.get("deleted_at"):
        raise ValueError("This document is in the recycle bin. Restore it first.")
    if "place_of_supply" in payload or "reverse_charge" in payload:
        pos = _s(payload.get("place_of_supply"))[:2] if "place_of_supply" in payload else t.get("place_of_supply")
        if pos and not (len(pos) == 2 and pos.isdigit()):
            raise ValueError("Place of supply must be a 2-digit state code (e.g. 07 for Delhi)")
        rcm = (1 if payload.get("reverse_charge") in (True, 1, "1", "true", "yes") else 0) \
            if "reverse_charge" in payload else t.get("reverse_charge")
        conn.execute("UPDATE hop_party_transactions SET place_of_supply=?, reverse_charge=? WHERE workspace_id=? AND id=?",
                     (pos or None, rcm, ws, int(txn_id)))
        t = _row(conn, ws, txn_id)
    ty = int(t.get("txn_type") or 0)
    total = _f(t.get("total_amount"))

    # 1) What was paid / received now, and how.
    has_money_fields = any(k in payload for k in ("received_amount", "payment_mode", "payment_account_id"))
    if ty == 1 and not is_update and not has_money_fields:
        # Settings → "New sale invoice = fully received (cash sale)"
        try:
            from app import hop_app_settings

            if hop_app_settings.get(conn, ws, "cash_sale_default"):
                payload = {**payload, "received_amount": total, "payment_mode": "cash"}
                has_money_fields = True
        except sqlite3.OperationalError:
            pass
    if ty in FULL_AMOUNT:
        amount = total
    elif ty in MONEY_IN | MONEY_OUT:
        amount = _f(payload.get("received_amount")) if (has_money_fields or not is_update) else _f(t.get("received_amount"))
        if amount > total + EPS:
            raise ValueError("Received / paid amount cannot be more than the total")
    else:
        amount = 0.0
    mode = (_s(payload.get("payment_mode")) if has_money_fields or not is_update else _s(t.get("payment_mode"))).lower()
    account_id = payload.get("payment_account_id") if has_money_fields or not is_update else t.get("payment_account_id")
    ref = _s(payload.get("payment_ref")) if "payment_ref" in payload else _s(t.get("payment_ref"))
    cheque_date = _s(payload.get("cheque_date")) if "cheque_date" in payload else _s(t.get("cheque_date"))
    if amount > EPS:
        if mode not in ("cash", "bank", "cheque"):
            mode = "cash" if not account_id else "bank"
        if mode == "cash" and not account_id:
            account_id = ensure_cash_account(conn, ws)
        if mode != "cheque":
            acct = _account(conn, ws, account_id)
            mode = "cash" if acct.get("account_type") == "cash" else "bank"
            account_id = int(acct["id"])
        else:
            account_id = None
    else:
        mode, account_id, ref, cheque_date = "", None, "", ""

    # A cheque that is already deposited / withdrawn cannot silently change.
    closed = conn.execute("SELECT * FROM hop_cheques WHERE workspace_id=? AND party_txn_id=? AND status='closed'",
                          (ws, int(txn_id))).fetchone()
    if closed is not None and (mode != "cheque" or abs(_f(closed["amount"]) - amount) > EPS):
        raise ValueError("This payment's cheque is already deposited / withdrawn. Reopen it in Cash & Bank → "
                         "Cheques before changing the payment.")

    _undo_money(conn, ws, txn_id, keep_closed_cheque=closed is not None)
    _undo_stock(conn, ws, txn_id)
    conn.execute("""UPDATE hop_party_transactions SET received_amount=?, payment_mode=?, payment_account_id=?,
                    payment_ref=?, cheque_date=? WHERE workspace_id=? AND id=?""",
                 (round(amount, 2) if ty in MONEY_IN | MONEY_OUT else None, mode or None, account_id,
                  ref or None, cheque_date or None, ws, int(txn_id)))
    t = _row(conn, ws, txn_id)
    direction = "in" if ty in MONEY_IN else "out"
    if amount > EPS and mode == "cheque":
        if closed is None:
            conn.execute("""INSERT INTO hop_cheques (workspace_id, party_txn_id, direction, amount, cheque_no,
                            cheque_date, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
                         (ws, int(txn_id), direction, round(amount, 2), ref or None,
                          cheque_date or (t.get("txn_date") or "")[:10], _now()))
    elif amount > EPS:
        _bank_entry(conn, ws, account_id, direction, amount, t.get("txn_date"), t, "doc", reference=ref)

    # Settings → credit limit "don't allow the bill": enforced here too, not only as a warning.
    if ty == 1 and not is_update and t.get("party_type") == "customer":
        try:
            from app import hop_settings

            w = hop_settings.invoice_entry_warnings(conn, ws, {**payload, "txn_type": 1, "party_type": "customer",
                                                               "party_id": t.get("party_id"), "exclude_txn_id": txn_id,
                                                               "received_amount": amount})
            blocked = next((x for x in w if x.get("code") == "credit_limit" and x.get("level") == "block"), None)
            if blocked:
                raise ValueError(blocked["message"])
        except sqlite3.OperationalError:
            pass

    # 2) Stock.
    _post_stock(conn, ws, t, new=not is_update)

    # 3) Payment-In / Out: which bills it clears.
    if ty in PAYMENT_FOR:
        if "allocations" in payload or not is_update:
            _set_links(conn, ws, t, payload.get("allocations") or [])
        elif _linked_total(conn, ws, payment=txn_id) > total + EPS:
            raise ValueError("This payment is linked to bills for more than its new amount. Unlink some bills first.")
    if ty in BILLS and _linked_total(conn, ws, bill=txn_id) > EPS:
        if _linked_total(conn, ws, bill=txn_id) + _f(t.get("received_amount")) > total + EPS:
            raise ValueError("Payments linked to this bill are more than its new total. Edit those payments first.")
    recompute_balance(conn, ws, txn_id)
    if ty in BILLS and t.get("received_amount") is not None and _linked_total(conn, ws, bill=txn_id) <= EPS:
        recompute_balance(conn, ws, txn_id)

    # 4) Convert: remember the source and close it.
    src = payload.get("linked_from_txn_id")
    if src and not is_update:
        s = _row(conn, ws, int(src))
        if s and ty in CONVERT_TO.get(int(s.get("txn_type") or 0), ()):
            conn.execute("UPDATE hop_party_transactions SET linked_from_txn_id=? WHERE workspace_id=? AND id=?",
                         (int(src), ws, int(txn_id)))
            conn.execute("UPDATE hop_party_transactions SET converted_to_txn_id=?, status_text=?, updated_at=datetime('now') "
                         "WHERE workspace_id=? AND id=?",
                         (int(txn_id), f"Converted to {LABELS.get(ty, 'document')} {t.get('txn_number') or ''}".strip(),
                          ws, int(src)))


def unpost_document(conn: sqlite3.Connection, ws: str, txn_id: int) -> None:
    """Before a manual document is deleted: undo all its effects."""
    t = _row(conn, ws, txn_id)
    if not t:
        return
    if conn.execute("SELECT 1 FROM hop_cheques WHERE workspace_id=? AND party_txn_id=? AND status='closed'",
                    (ws, int(txn_id))).fetchone():
        raise ValueError("This payment's cheque is already deposited / withdrawn. Reopen it in Cash & Bank → "
                         "Cheques before deleting.")
    _undo_money(conn, ws, txn_id, keep_closed_cheque=False)
    _undo_stock(conn, ws, txn_id)
    ty = int(t.get("txn_type") or 0)
    if ty in PAYMENT_FOR:
        bills = [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT bill_txn_id FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?", (ws, int(txn_id)))]
        conn.execute("DELETE FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=?", (ws, int(txn_id)))
        for b in bills:
            recompute_balance(conn, ws, b)
    if ty in BILLS:
        pays = [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT payment_txn_id FROM hop_payment_links WHERE workspace_id=? AND bill_txn_id=?", (ws, int(txn_id)))]
        conn.execute("DELETE FROM hop_payment_links WHERE workspace_id=? AND bill_txn_id=?", (ws, int(txn_id)))
        for p in pays:
            recompute_balance(conn, ws, p)
    if t.get("linked_from_txn_id"):
        conn.execute("UPDATE hop_party_transactions SET converted_to_txn_id=NULL, status_text='Open', "
                     "updated_at=datetime('now') WHERE workspace_id=? AND id=? AND converted_to_txn_id=?",
                     (ws, int(t["linked_from_txn_id"]), int(txn_id)))


def links_of(conn: sqlite3.Connection, ws: str, txn_id: int) -> list[dict[str, Any]]:
    """For a payment: the bills it clears. For a bill: the payments on it."""
    rows = conn.execute(
        """SELECT l.amount, l.payment_txn_id, l.bill_txn_id,
                  p.txn_number AS payment_number, p.txn_date AS payment_date,
                  b.txn_number AS bill_number, b.txn_date AS bill_date
           FROM hop_payment_links l
           JOIN hop_party_transactions p ON p.id=l.payment_txn_id
           JOIN hop_party_transactions b ON b.id=l.bill_txn_id
           WHERE l.workspace_id=? AND (l.payment_txn_id=? OR l.bill_txn_id=?) ORDER BY l.id""",
        (ws, int(txn_id), int(txn_id))).fetchall()
    return [dict(r) for r in rows]


# ── Cheques ──────────────────────────────────────────────────────────────────

def list_cheques(conn: sqlite3.Connection, ws: str, status: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT c.*, t.party_name, t.txn_number, t.txn_type FROM hop_cheques c
             JOIN hop_party_transactions t ON t.id=c.party_txn_id WHERE c.workspace_id=?"""
    params: list[Any] = [ws]
    if status in ("open", "closed"):
        sql += " AND c.status=?"
        params.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY c.status DESC, date(c.cheque_date), c.id", params)]


def close_cheque(conn: sqlite3.Connection, ws: str, cheque_id: int, account_id: Any, date: str = "") -> dict[str, Any]:
    c = conn.execute("SELECT * FROM hop_cheques WHERE workspace_id=? AND id=?", (ws, int(cheque_id))).fetchone()
    if not c:
        raise LookupError("Cheque not found")
    c = dict(c)
    if c["status"] == "closed":
        raise ValueError("This cheque is already closed")
    acct = _account(conn, ws, account_id)
    t = _row(conn, ws, int(c["party_txn_id"]))
    day = (date or _now())[:10]
    _bank_entry(conn, ws, acct["id"], c["direction"], _f(c["amount"]), day, t, "cheque",
                reference=c.get("cheque_no") or "",
                notes=f"Cheque {c.get('cheque_no') or ''} · {t.get('party_name') or ''}".strip(" ·"))
    conn.execute("UPDATE hop_cheques SET status='closed', account_id=?, closed_on=? WHERE workspace_id=? AND id=?",
                 (int(acct["id"]), day, ws, int(cheque_id)))
    conn.commit()
    return {"id": int(cheque_id), "status": "closed", "account_id": int(acct["id"])}


def reopen_cheque(conn: sqlite3.Connection, ws: str, cheque_id: int) -> dict[str, Any]:
    c = conn.execute("SELECT * FROM hop_cheques WHERE workspace_id=? AND id=?", (ws, int(cheque_id))).fetchone()
    if not c:
        raise LookupError("Cheque not found")
    c = dict(c)
    if c["status"] != "closed":
        raise ValueError("This cheque is already open")
    conn.execute("DELETE FROM hop_bank_entries WHERE workspace_id=? AND party_txn_id=? AND source='cheque'",
                 (ws, int(c["party_txn_id"])))
    recompute_account(conn, ws, c.get("account_id"))
    conn.execute("UPDATE hop_cheques SET status='open', account_id=NULL, closed_on=NULL WHERE workspace_id=? AND id=?",
                 (ws, int(cheque_id)))
    conn.commit()
    return {"id": int(cheque_id), "status": "open"}


# ── Convert ──────────────────────────────────────────────────────────────────

def convert_draft(conn: sqlite3.Connection, ws: str, txn_id: int, to_type: int) -> dict[str, Any]:
    """A ready-to-edit copy of a document as another type (the form opens prefilled)."""
    from app.hop_doc_preview import _clean

    t = _row(conn, ws, txn_id)
    if not t:
        raise LookupError("Document not found")
    ty = int(t.get("txn_type") or 0)
    if int(to_type) not in CONVERT_TO.get(ty, ()):
        raise ValueError(f"{LABELS.get(ty, 'This document')} cannot be converted to {LABELS.get(int(to_type), 'that')}")
    done = _row(conn, ws, int(t["converted_to_txn_id"])) if t.get("converted_to_txn_id") else None
    if done and not _s(done.get("deleted_at")):
        raise ValueError(f"{t.get('txn_number') or 'This document'} is already converted to "
                         f"{done.get('txn_label') or 'a bill'} {done.get('txn_number') or ''} — open that one instead.".replace("  ", " "))
    lines = conn.execute("SELECT * FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=? ORDER BY line_no, id",
                         (ws, int(t["source_txn_id"]))).fetchall()
    return {
        "txn_type": int(to_type),
        "txn_label": LABELS.get(int(to_type)),
        "party_type": t.get("party_type"),
        "party_id": t.get("party_id"),
        "customer_id": t.get("party_id"),
        "notes": _clean(t.get("notes")),
        "doc_terms": _clean(t.get("doc_terms")),
        "delivery_terms": _clean(t.get("delivery_terms")),
        "shipping_amount": _f(t.get("shipping_amount")),
        "shipping_tax_pct": _f(t.get("shipping_tax_pct")),
        "discount_amount": _f(t.get("discount_amount")),
        "discount_pct": _f(t.get("discount_pct")),
        "round_off": _f(t.get("round_off")),
        "linked_from_txn_id": int(txn_id),
        "linked_from_label": f"{LABELS.get(ty, '')} {t.get('txn_number') or ''}".strip(),
        "lines": [{
            "item_name": _clean(ln["item_name"]), "item_code": _clean(ln["item_code"]),
            "product_id": ln["product_id"] if "product_id" in ln.keys() else None,
            "description": _clean(ln["description"]), "hsn": _clean(ln["hsn"]),
            "qty": _f(ln["qty"]), "unit": _clean(ln["unit"]), "rate": _f(ln["rate"]),
            "discount_pct": _f(ln["discount_pct"]), "tax_pct": _f(ln["tax_pct"]),
            "section_title": _clean(ln["section_title"]),
        } for ln in lines],
    }


# ── Expense categories ───────────────────────────────────────────────────────

DEFAULT_INCOME_CATEGORIES = ("Interest received", "Rent received", "Commission received", "Scrap sale",
                             "Discount received", "Other income")
DEFAULT_EXPENSE_CATEGORIES = ("Rent", "Salary", "Electricity", "Transport", "Office supplies",
                              "Telephone & Internet", "Repairs", "Commission", "Petrol", "Interest paid", "Income tax",
                              "Other")
# Suggested (not auto-created) in the Expenses form for firms made before these were defaults — EBITDA reads them.
EBITDA_CATEGORY_HINTS = ("Interest paid", "Income tax", "Amortisation")


def _kind(kind: Any) -> str:
    k = _s(kind).lower() or "expense"
    if k not in ("expense", "income"):
        raise ValueError("Category kind must be expense or income")
    return k


def list_expense_categories(conn: sqlite3.Connection, ws: str, kind: str = "expense") -> list[dict[str, Any]]:
    """Expense categories (Rent, Salary…) or, with kind='income', other-income categories."""
    kind = _kind(kind)
    where = "COALESCE(kind,'expense')=?"
    if not conn.execute(f"SELECT 1 FROM hop_expense_categories WHERE workspace_id=? AND {where} LIMIT 1", (ws, kind)).fetchone():
        for n in (DEFAULT_EXPENSE_CATEGORIES if kind == "expense" else DEFAULT_INCOME_CATEGORIES):
            conn.execute("INSERT OR IGNORE INTO hop_expense_categories (workspace_id, name, created_at, kind) VALUES (?, ?, ?, ?)",
                         (ws, n, _now(), kind))
        conn.commit()
    ty = 7 if kind == "expense" else 29
    rows = conn.execute(
        f"""SELECT c.id, c.name, COALESCE(c.kind,'expense') AS kind,
                  COALESCE((SELECT SUM(t.total_amount) FROM hop_party_transactions t
                            WHERE (t.deleted_at IS NULL OR trim(t.deleted_at)='') AND t.workspace_id=c.workspace_id AND t.txn_type={ty} AND t.expense_category_id=c.id), 0) AS total
           FROM hop_expense_categories c WHERE c.workspace_id=? AND {where} ORDER BY lower(c.name)""", (ws, kind)).fetchall()
    return [dict(r) for r in rows]


def create_expense_category(conn: sqlite3.Connection, ws: str, name: str, kind: str = "expense") -> dict[str, Any]:
    name = _s(name)[:60]
    kind = _kind(kind)
    if not name:
        raise ValueError("Category name is required")
    conn.execute("INSERT OR IGNORE INTO hop_expense_categories (workspace_id, name, created_at, kind) VALUES (?, ?, ?, ?)",
                 (ws, name, _now(), kind))
    conn.commit()
    r = conn.execute("SELECT id, name FROM hop_expense_categories WHERE workspace_id=? AND name=?", (ws, name)).fetchone()
    return dict(r)


def expense_category(conn: sqlite3.Connection, ws: str, category_id: Any) -> dict[str, Any] | None:
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return None
    r = conn.execute("SELECT id, name FROM hop_expense_categories WHERE workspace_id=? AND id=?", (ws, cid)).fetchone()
    return dict(r) if r else None
