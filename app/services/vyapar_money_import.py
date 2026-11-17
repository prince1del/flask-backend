"""Vyapar backup → NEXORA: the rest of the books after parties, items and bills.

Money: bank accounts (opening balance, A/c no., IFSC), the cash / bank / cheque of every payment,
bank-to-bank transfers and bank adjustments, cash adjustments, cheques (open / deposited), loan
accounts and their entries. Links: which payment cleared which bill. Masters: party groups (and each
party's group), units + second units on items, payment terms (default due days), terms & conditions,
TDS rates.

Safe to run again on the same backup: every row brought in is noted in ``hop_import_map`` and never
added twice. Vyapar codes whose meaning is not certain are imported with the most likely meaning and
listed under ``check`` so the owner can look at them; anything not understood is listed under
``skipped`` — nothing is guessed silently.

Column names come from a real Vyapar backup (kb_paymentTypes, kb_bank_adjustments, kb_cheque_status,
loan_accounts, loan_transactions, kb_txn_links, kb_item_units, kb_item_units_mapping, kb_party_groups,
kb_payment_terms, terms_and_conditions, tnc_txn_type_mapping, tds_tax_rates).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Vyapar transaction types: money comes IN / goes OUT with these.
MONEY_IN = {1, 3, 23, 29}      # sale, payment-in, purchase return, other income
MONEY_OUT = {2, 4, 7, 21}      # purchase, payment-out, expense, sale return
# kb_bank_adjustments.bank_adj_type — 25 is proven (both banks on the row); the rest are the likely meaning.
BANK_ADJ = {25: "bank_to_bank", 18: "bank_to_cash", 17: "cash_to_bank", 14: "bank_add", 15: "bank_reduce"}
BANK_ADJ_SURE = {25}
CASH_ADJ = {19: "cash_add", 20: "cash_reduce"}
LOAN_TXN = {41: "disbursement", 42: "emi", 43: "fee", 44: "interest"}  # 40 = loan opened (opening balance only)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def _tables(src: sqlite3.Connection) -> set[str]:
    return {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(src: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    cur = src.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _cols(src: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in src.execute(f"PRAGMA table_info('{table}')")}


def ensure_map(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_import_map (
        workspace_id TEXT NOT NULL, kind TEXT NOT NULL, src_id TEXT NOT NULL, dst_id INTEGER,
        created_at TEXT, UNIQUE(workspace_id, kind, src_id))""")


def _mapped(conn, ws, kind, src_id) -> int | None:
    r = conn.execute("SELECT dst_id FROM hop_import_map WHERE workspace_id=? AND kind=? AND src_id=?",
                     (ws, kind, str(src_id))).fetchone()
    return None if r is None else (int(r[0]) if r[0] is not None else 0)


def _remember(conn, ws, kind, src_id, dst_id) -> None:
    conn.execute("INSERT OR IGNORE INTO hop_import_map (workspace_id, kind, src_id, dst_id, created_at) VALUES (?,?,?,?,?)",
                 (ws, kind, str(src_id), dst_id, _now()))


class _Ctx:
    def __init__(self, src, conn, ws):
        self.src, self.conn, self.ws = src, conn, ws
        self.tables = _tables(src)
        self.out: dict[str, Any] = {"bank_accounts": 0, "payments_posted": 0, "cheques": 0, "transfers": 0,
                                    "bank_adjustments": 0, "cash_adjustments": 0, "loans": 0, "loan_entries": 0,
                                    "payment_links": 0, "units": 0, "items_units_set": 0, "party_groups": 0,
                                    "parties_grouped": 0, "terms": 0, "check": [], "skipped": []}
        self.accounts: dict[int, int] = {}      # Vyapar paymentType_id → hop_bank_accounts.id
        self.ptype: dict[int, str] = {}          # Vyapar paymentType_id → CASH / BANK / CHEQUE
        self.txn: dict[int, dict[str, Any]] = {}  # Vyapar txn_id → hop_party_transactions row

    def has(self, t: str) -> bool:
        return t in self.tables


def _load_txns(c: _Ctx) -> None:
    for r in c.conn.execute("SELECT id, source_txn_id, txn_type, party_type, party_id, txn_number, txn_date "
                            "FROM hop_party_transactions WHERE workspace_id=? AND source_txn_id > 0", (c.ws,)).fetchall():
        c.txn[int(r[1])] = {"id": int(r[0]), "txn_type": int(r[2] or 0), "party_type": r[3], "party_id": r[4],
                            "txn_number": r[5], "txn_date": r[6]}


def _bank_accounts(c: _Ctx) -> None:
    from app import hop_money

    if not c.has("kb_paymentTypes"):
        return
    cash_id = hop_money.ensure_cash_account(c.conn, c.ws)
    for p in _rows(c.src, "SELECT * FROM kb_paymentTypes"):
        pid, kind = int(p["paymentType_id"]), _s(p.get("paymentType_type")).upper()
        c.ptype[pid] = kind
        if kind == "CASH":
            c.accounts[pid] = cash_id
            ob = _f(p.get("paymentType_opening_balance"))
            if ob and _mapped(c.conn, c.ws, "cash_opening", pid) is None:
                c.conn.execute("UPDATE hop_bank_accounts SET opening_balance=COALESCE(opening_balance,0)+? WHERE id=?",
                               (ob, cash_id))
                _remember(c.conn, c.ws, "cash_opening", pid, cash_id)
            continue
        if kind != "BANK":
            continue
        got = _mapped(c.conn, c.ws, "bank", pid)
        if got:
            c.accounts[pid] = got
            continue
        name = _s(p.get("paymentType_name")) or _s(p.get("paymentType_bankName")) or f"Bank {pid}"
        same = c.conn.execute("SELECT id FROM hop_bank_accounts WHERE workspace_id=? AND lower(name)=lower(?) "
                              "AND account_type!='cash'", (c.ws, name)).fetchone()
        if same:
            aid = int(same[0])
        else:
            ob = _f(p.get("paymentType_opening_balance"))
            now = _now()
            aid = int(c.conn.execute(
                """INSERT INTO hop_bank_accounts (workspace_id, name, account_type, account_number, ifsc,
                       opening_balance, current_balance, is_active, created_at, updated_at)
                   VALUES (?, ?, 'bank', ?, ?, ?, ?, 1, ?, ?)""",
                (c.ws, name[:80], _s(p.get("paymentType_accountNumber")), _s(p.get("pt_bank_ifsc_code")),
                 ob, ob, now, now)).lastrowid)
            c.out["bank_accounts"] += 1
        c.accounts[pid] = aid
        _remember(c.conn, c.ws, "bank", pid, aid)


def _entry(c: _Ctx, account_id: int, direction: str, amount: float, day: str, t: dict | None, ref: str,
           notes: str, counterparty: int | None = None, source: str = "doc") -> None:
    """Bank / cash movement. source 'doc' = part of an imported document (reports already count it)."""
    c.conn.execute(
        """INSERT INTO hop_bank_entries (workspace_id, account_id, entry_date, entry_type, amount,
               counterparty_account_id, party_type, party_id, party_txn_id, reference, notes, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (c.ws, int(account_id), _s(day)[:10], direction, round(amount, 2), counterparty,
         (t or {}).get("party_type"), (t or {}).get("party_id"), (t or {}).get("id"), ref, notes[:200], source, _now()))


def _payments(c: _Ctx) -> None:
    """Every payment row of Vyapar → the right cash / bank account, or a cheque."""
    if not c.has("txn_payment_mapping"):
        return
    status: dict[int, dict] = {}
    if c.has("kb_cheque_status"):
        for s in _rows(c.src, "SELECT * FROM kb_cheque_status"):
            status[int(s["cheque_id"])] = s
    for m in _rows(c.src, "SELECT * FROM txn_payment_mapping"):
        mid, amt = int(m["id"]), _f(m.get("amount"))
        if amt <= 0.009 or _mapped(c.conn, c.ws, "pay", mid) is not None:
            continue
        t = c.txn.get(int(m.get("txn_id") or 0))
        if not t:
            continue
        ty = t["txn_type"]
        direction = "in" if ty in MONEY_IN else "out" if ty in MONEY_OUT else None
        if not direction:
            _remember(c.conn, c.ws, "pay", mid, None)   # orders / estimates: advance noted on the doc itself
            continue
        pid = int(m.get("payment_id") or 0)
        kind = c.ptype.get(pid, "")
        label = f"{t.get('txn_number') or ''}".strip()
        if kind == "CHEQUE" or (m.get("cheque_id") and kind != "BANK" and kind != "CASH"):
            st = status.get(int(m.get("cheque_id") or 0), {})
            closed = int(st.get("cheque_current_status") or 0) == 1
            acct = c.accounts.get(int(st.get("transferred_To_Account") or 0))
            cid = c.conn.execute(
                """INSERT INTO hop_cheques (workspace_id, party_txn_id, direction, amount, cheque_no, cheque_date,
                       status, account_id, closed_on, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (c.ws, t["id"], direction, round(amt, 2), _s(m.get("payment_reference")),
                 _s(st.get("cheque_issued_date") or t.get("txn_date"))[:10],
                 "closed" if closed and acct else "open", acct if closed else None,
                 _s(st.get("cheque_transfer_date"))[:10] if closed and acct else None, _now())).lastrowid
            if closed and acct:
                _entry(c, acct, direction, amt, _s(st.get("cheque_transfer_date") or t.get("txn_date")), t,
                       _s(m.get("payment_reference")), f"Cheque {label}", source="cheque")
            c.conn.execute("UPDATE hop_party_transactions SET payment_mode='cheque' WHERE id=?", (t["id"],))
            _remember(c.conn, c.ws, "pay", mid, int(cid))
            c.out["cheques"] += 1
            continue
        acct = c.accounts.get(pid)
        if not acct:
            c.out["skipped"].append(f"Payment of ₹{amt:,.2f} on {label}: unknown payment mode {pid}")
            _remember(c.conn, c.ws, "pay", mid, None)
            continue
        _entry(c, acct, direction, amt, t.get("txn_date") or "", t, f"vyapar:pay:{mid}",
               f"Imported {label}".strip())
        c.conn.execute("UPDATE hop_party_transactions SET payment_account_id=COALESCE(payment_account_id, ?) WHERE id=?",
                       (acct, t["id"]))
        _remember(c.conn, c.ws, "pay", mid, acct)
        c.out["payments_posted"] += 1


def _bank_adjustments(c: _Ctx) -> None:
    from app import hop_money

    cash = hop_money.ensure_cash_account(c.conn, c.ws)
    if c.has("kb_bank_adjustments"):
        for a in _rows(c.src, "SELECT * FROM kb_bank_adjustments"):
            aid, amt = int(a["bank_adj_id"]), _f(a.get("bank_adj_amount"))
            if amt <= 0.009 or _mapped(c.conn, c.ws, "bank_adj", aid) is not None:
                continue
            kind = BANK_ADJ.get(int(a.get("bank_adj_type") or 0))
            frm, to = c.accounts.get(int(a.get("bank_adj_bank_id") or 0)), c.accounts.get(int(a.get("bank_adj_to_bank_id") or 0))
            day, note = _s(a.get("bank_adj_date")), _s(a.get("bank_adj_description"))
            ref = f"vyapar:bank_adj:{aid}"
            if kind == "bank_to_bank" and frm and to:
                _entry(c, frm, "out", amt, day, None, ref, note or "Bank transfer", counterparty=to)
                _entry(c, to, "in", amt, day, None, ref, note or "Bank transfer", counterparty=frm)
                c.out["transfers"] += 1
            elif kind in ("bank_to_cash", "cash_to_bank") and frm:
                a1, a2 = (frm, cash) if kind == "bank_to_cash" else (cash, frm)
                _entry(c, a1, "out", amt, day, None, ref, note or "Bank / cash transfer", counterparty=a2)
                _entry(c, a2, "in", amt, day, None, ref, note or "Bank / cash transfer", counterparty=a1)
                c.out["bank_adjustments"] += 1
            elif kind in ("bank_add", "bank_reduce") and frm:
                _entry(c, frm, "in" if kind == "bank_add" else "out", amt, day, None, ref, note or "Bank adjustment")
                c.out["bank_adjustments"] += 1
            else:
                c.out["skipped"].append(f"Bank entry ₹{amt:,.2f} on {day[:10]} (Vyapar type {a.get('bank_adj_type')})")
                _remember(c.conn, c.ws, "bank_adj", aid, None)
                continue
            if int(a.get("bank_adj_type") or 0) not in BANK_ADJ_SURE:
                c.out["check"].append(f"{day[:10]} ₹{amt:,.2f} — imported as {kind.replace('_', ' ')}: {note or 'no note'}")
            _remember(c.conn, c.ws, "bank_adj", aid, frm)
    if c.has("kb_cash_adjustments"):
        for a in _rows(c.src, "SELECT * FROM kb_cash_adjustments"):
            aid, amt = int(a["cash_adj_id"]), _f(a.get("cash_adj_amount"))
            if amt <= 0.009 or _mapped(c.conn, c.ws, "cash_adj", aid) is not None:
                continue
            kind = CASH_ADJ.get(int(a.get("cash_adj_type") or 0))
            day, note = _s(a.get("cash_adj_date")), _s(a.get("cash_adj_description"))
            if not kind:
                c.out["skipped"].append(f"Cash entry ₹{amt:,.2f} on {day[:10]} (Vyapar type {a.get('cash_adj_type')})")
            else:
                _entry(c, cash, "in" if kind == "cash_add" else "out", amt, day, None, f"vyapar:cash_adj:{aid}",
                       note or "Cash adjustment")
                c.out["cash_adjustments"] += 1
                c.out["check"].append(f"{day[:10]} ₹{amt:,.2f} — cash {'added' if kind == 'cash_add' else 'reduced'}: {note or 'no note'}")
            _remember(c.conn, c.ws, "cash_adj", aid, cash)
    for acct in set(c.accounts.values()) | {cash}:
        hop_money.recompute_account(c.conn, c.ws, acct)


def _loans(c: _Ctx) -> None:
    if not c.has("loan_accounts"):
        return
    now = _now()
    loans: dict[int, int] = {}
    for la in _rows(c.src, "SELECT * FROM loan_accounts"):
        lid = int(la["loan_account_id"])
        got = _mapped(c.conn, c.ws, "loan", lid)
        if got:
            loans[lid] = got
            continue
        name = _s(la.get("loan_account_name")) or f"Loan {lid}"
        nid = int(c.conn.execute(
            """INSERT INTO hop_loan_accounts (workspace_id, name, lender, loan_type, principal, opening_balance,
                   interest_pct, emi_amount, start_date, end_date, linked_bank_account_id, is_active, created_at, updated_at)
               VALUES (?, ?, ?, 'term', ?, ?, ?, 0, ?, NULL, NULL, 1, ?, ?)""",
            (c.ws, name[:80], _s(la.get("lender")), _f(la.get("opening_bal")), _f(la.get("opening_bal")),
             _f(la.get("interest_rate")), _s(la.get("opening_date"))[:10] or None, now, now)).lastrowid)
        loans[lid] = nid
        _remember(c.conn, c.ws, "loan", lid, nid)
        c.out["loans"] += 1
    if not c.has("loan_transactions"):
        return
    from app import hop_phase3_ext

    for lt in _rows(c.src, "SELECT * FROM loan_transactions"):
        tid = int(lt["loan_txn_id"])
        if _mapped(c.conn, c.ws, "loan_txn", tid) is not None:
            continue
        loan = loans.get(int(lt.get("loan_account_id") or 0))
        ty = int(lt.get("loan_txn_type") or 0)
        prin, intr = _f(lt.get("principal_amount")), _f(lt.get("interest_amount"))
        _remember(c.conn, c.ws, "loan_txn", tid, loan)
        if not loan or (prin + intr) <= 0.009:
            continue   # e.g. type 40 = loan opened (its opening balance is on the loan)
        kind = LOAN_TXN.get(ty)
        bank = c.accounts.get(int(lt.get("payment_acc_id") or 0))
        day = _s(lt.get("txn_date"))[:10]
        if not kind:
            c.out["skipped"].append(f"Loan entry ₹{prin + intr:,.2f} on {day} (Vyapar type {ty})")
            continue
        parts = [("principal" if kind == "emi" else kind, prin)] + ([("interest", intr)] if intr > 0.009 else [])
        for k, amt in parts:
            if amt > 0.009:
                hop_phase3_ext.add_loan_entry(c.conn, c.ws, loan, {"entry_type": k, "amount": amt, "entry_date": day,
                                                                    "bank_account_id": bank, "notes": "Imported from Vyapar"})
                c.out["loan_entries"] += 1
        c.out["check"].append(f"Loan entry {day}: ₹{prin + intr:,.2f} imported as {kind}")


def _links(c: _Ctx) -> None:
    """Which payment (or return) cleared which bill — shown on the bill and the payment."""
    if not c.has("kb_txn_links"):
        return
    pay_types = {3, 4, 21, 23}
    for l in _rows(c.src, "SELECT * FROM kb_txn_links"):
        lid, amt = int(l["txn_links_id"]), _f(l.get("txn_links_amount"))
        if amt <= 0.009 or _mapped(c.conn, c.ws, "link", lid) is not None:
            continue
        a, b = c.txn.get(int(l.get("txn_links_txn_1_id") or 0)), c.txn.get(int(l.get("txn_links_txn_2_id") or 0))
        _remember(c.conn, c.ws, "link", lid, None)
        if not a or not b:
            continue
        pay, bill = (a, b) if a["txn_type"] in pay_types and b["txn_type"] not in pay_types else (b, a)
        if pay["txn_type"] not in pay_types:
            continue
        if c.conn.execute("SELECT 1 FROM hop_payment_links WHERE workspace_id=? AND payment_txn_id=? AND bill_txn_id=?",
                          (c.ws, pay["id"], bill["id"])).fetchone():
            continue
        c.conn.execute("INSERT INTO hop_payment_links (workspace_id, payment_txn_id, bill_txn_id, amount, created_at) "
                       "VALUES (?,?,?,?,?)", (c.ws, pay["id"], bill["id"], round(amt, 2), _now()))
        c.out["payment_links"] += 1


def _masters(c: _Ctx) -> None:
    from app import hop_app_settings

    settings = hop_app_settings.get_all(c.conn, c.ws)
    changes: dict[str, Any] = {}
    # Party groups: the list in Settings → Party, and each party's group.
    if c.has("kb_party_groups"):
        groups = {int(g["party_group_id"]): _s(g["party_group_name"]) for g in _rows(c.src, "SELECT * FROM kb_party_groups")}
        have = list(settings.get("party_groups") or [])
        new = [g for g in groups.values() if g and g.lower() not in {x.lower() for x in have}]
        if new:
            changes["party_groups"] = have + new
            c.out["party_groups"] = len(new)
        if c.has("kb_names") and "name_group_id" in _cols(c.src, "kb_names"):
            for n in _rows(c.src, "SELECT full_name, name_group_id FROM kb_names WHERE name_group_id IS NOT NULL"):
                g = groups.get(int(n.get("name_group_id") or 0))
                if not g or not _s(n.get("full_name")):
                    continue
                cur = c.conn.execute("UPDATE hop_customers SET customer_type=? WHERE workspace_id=? AND lower(company)=lower(?) "
                                     "AND (customer_type IS NULL OR trim(customer_type)='' OR customer_type IN ('Buyer','Supplier','General'))",
                                     (g, c.ws, _s(n["full_name"])))
                c.out["parties_grouped"] += cur.rowcount
    # Units and second units (1 BOX = 10 PCS).
    if c.has("kb_item_units"):
        units = {int(u["unit_id"]): (_s(u.get("unit_short_name")) or _s(u.get("unit_name"))).upper()[:12]
                 for u in _rows(c.src, "SELECT * FROM kb_item_units")}
        for name in units.values():
            if name and not c.conn.execute("SELECT 1 FROM hop_units WHERE workspace_id=? AND upper(name)=?", (c.ws, name)).fetchone():
                c.conn.execute("INSERT INTO hop_units (workspace_id, name, created_at) VALUES (?,?,?)", (c.ws, name, _now()))
                c.out["units"] += 1
        maps = {int(m["unit_mapping_id"]): m for m in _rows(c.src, "SELECT * FROM kb_item_units_mapping")} \
            if c.has("kb_item_units_mapping") else {}
        if c.has("kb_items") and {"base_unit_id", "item_name"} <= _cols(c.src, "kb_items"):
            from app import hop_items

            hop_items.ensure_columns(c.conn)
            for it in _rows(c.src, "SELECT item_name, base_unit_id, secondary_unit_id, unit_mapping_id FROM kb_items"):
                base = units.get(int(it.get("base_unit_id") or 0))
                if not base:
                    continue
                mp = maps.get(int(it.get("unit_mapping_id") or 0)) or {}
                alt = units.get(int(it.get("secondary_unit_id") or mp.get("secondary_unit_id") or 0))
                factor = _f(mp.get("conversion_rate"))
                cur = c.conn.execute(
                    "UPDATE hop_products SET unit=COALESCE(NULLIF(trim(unit),''), ?), "
                    "alt_unit=COALESCE(NULLIF(trim(alt_unit),''), ?), alt_unit_factor=COALESCE(alt_unit_factor, ?) "
                    "WHERE workspace_id=? AND lower(name)=lower(?) AND (unit IS NULL OR trim(unit)='' "
                    "OR (? IS NOT NULL AND (alt_unit IS NULL OR trim(alt_unit)='')))",
                    (base, alt if alt and factor else None, factor if alt and factor else None, c.ws, _s(it["item_name"]),
                     alt if alt and factor else None))
                c.out["items_units_set"] += cur.rowcount
    # Payment terms: the default term's days become the default due days.
    if c.has("kb_payment_terms"):
        d = next((p for p in _rows(c.src, "SELECT * FROM kb_payment_terms") if int(p.get("is_default") or 0) == 1), None)
        if d and not settings.get("default_due_days") and int(d.get("term_days") or 0) > 0:
            changes["default_due_days"] = int(d["term_days"])
    # Terms & conditions: the sale invoice's terms become the firm's default terms on bills.
    if c.has("terms_and_conditions") and c.has("tnc_txn_type_mapping"):
        tnc = {int(t["tnc_id"]): _s(t.get("description")) for t in _rows(c.src, "SELECT * FROM terms_and_conditions")}
        sale = next((tnc.get(int(m["tnc_id"])) for m in _rows(c.src, "SELECT * FROM tnc_txn_type_mapping")
                     if int(m.get("txn_type") or 0) == 1), None)
        if sale:
            cur = c.conn.execute("UPDATE hop_firm_profile SET terms_default=? WHERE workspace_id=? AND "
                                 "(terms_default IS NULL OR trim(terms_default)='')", (sale[:4000], c.ws))
            c.out["terms"] = cur.rowcount
    if changes:
        hop_app_settings.save(c.conn, c.ws, changes)


NOT_IMPORTED = {
    "audit_trails": "Vyapar's own change log (NEXORA keeps its own audit trail from now on)",
    "urp_activity": "Vyapar user activity", "urp_users": "Vyapar app users (add your team in NEXORA)",
    "recycle_bin": "items deleted in Vyapar", "kb_images": "pictures", "transaction_attachments": "bill attachments",
    "kb_item_images": "item pictures", "kb_txn_message_config": "Vyapar's SMS settings",
    "journal_entry": "journal entries between accounts (shown below — add them in Journal Entry if needed)",
    "kb_party_item_rate": "party-wise item rates (NEXORA suggests each party's last rate from its bills)",
    "other_accounts": "other ledger accounts (capital, assets…) — opening balances are in Vyapar's Balance Sheet",
    "tds_tax_rates": "TDS rate names (type the TDS % on the payment in NEXORA)",
}


def import_money_and_masters(src: sqlite3.Connection, conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    ensure_map(conn)
    c = _Ctx(src, conn, ws)
    _load_txns(c)
    for step in (_bank_accounts, _payments, _bank_adjustments, _loans, _links, _masters):
        try:
            step(c)
        except Exception as exc:   # one part failing never stops the rest
            c.out["skipped"].append(f"{step.__name__.strip('_').replace('_', ' ')}: {exc}")
    for t, why in NOT_IMPORTED.items():
        if c.has(t):
            n = src.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            if n:
                c.out["skipped"].append(f"{n} × {why}")
    conn.commit()
    return c.out


def preview_counts(src: sqlite3.Connection) -> dict[str, int]:
    t = _tables(src)
    q = lambda sql: src.execute(sql).fetchone()[0] if all(x in t for x in sql.split("FROM ")[1].split()[:1]) else 0  # noqa: E731
    out = {}
    for key, sql in (("bank_accounts", "SELECT COUNT(*) FROM kb_paymentTypes WHERE upper(paymentType_type)='BANK'"),
                     ("payments", "SELECT COUNT(*) FROM txn_payment_mapping WHERE amount > 0"),
                     ("bank_transfers", "SELECT COUNT(*) FROM kb_bank_adjustments"),
                     ("cheques", "SELECT COUNT(*) FROM kb_cheque_status"),
                     ("loans", "SELECT COUNT(*) FROM loan_accounts"),
                     ("payment_links", "SELECT COUNT(*) FROM kb_txn_links")):
        try:
            out[key] = q(sql)
        except sqlite3.Error:
            out[key] = 0
    return out


def summary_lines(b: dict[str, Any]) -> list[str]:
    """Plain lines for the import result screen (phone + web)."""
    if not b:
        return []
    lines = [f"Bank accounts: {b.get('bank_accounts', 0)} added · payments put in their bank / cash: {b.get('payments_posted', 0)}",
             f"Cheques: {b.get('cheques', 0)} · bank transfers: {b.get('transfers', 0)} · bank / cash adjustments: "
             f"{b.get('bank_adjustments', 0) + b.get('cash_adjustments', 0)}",
             f"Loans: {b.get('loans', 0)} (entries {b.get('loan_entries', 0)}) · payment ↔ bill links: {b.get('payment_links', 0)}",
             f"Units: {b.get('units', 0)} · items with units: {b.get('items_units_set', 0)} · party groups: {b.get('party_groups', 0)}"]
    if b.get("check"):
        lines.append("Please check in Cash & Bank:")
        lines += [f"  • {x}" for x in b["check"][:20]]
    if b.get("skipped"):
        lines.append("Not brought in:")
        lines += [f"  • {x}" for x in b["skipped"][:20]]
    return lines
