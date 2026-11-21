"""Distributor: one article's card, stock typed by hand with its company, editing a company, cash discount (CD) and
incentives per company, and the per-company report (owner 21-11).

* Item card (D9: "isme aster ya kisi or item pe click kyu nahi ho raha"): an article with its company, prices, terms,
  stock per godown, still to come on company invoices, sold lately, and its last stock lines (each says where it came from).
* Hand stock: + Add / − Reduce in the Distributor UI always says the company (or "Opening stock") → stock line with
  ``vendor_id`` and reference ``manual:dist``.
* CD: a company gives cash discount % when its invoice is paid within the credit days. Per invoice: CD that can be
  earned, by when, and whether it was earned (paid in time), lost (paid late) or still open.
* Incentives: what the company promised (target, scheme, per order) — % or amount — and the company credit note that
  paid it (a credit note is saved as txn 16 from that company). Received vs still due.
* Per company: ordered → invoiced (CI) → received → still to come, and sales, cost, gross margin, CD earned and
  incentives received → net margin, by category.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

LIVE = "(t.deleted_at IS NULL OR trim(t.deleted_at)='')"


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def _now() -> str:
    from app.hop_dist_extra import _now as n

    return n()


def _today() -> date:
    from app.hop_dist_extra import _today as t

    return t()


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_dist_margins as M
    from app import hop_dist_receive as R

    M.ensure_schema(conn)
    R.ensure_schema(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_incentives (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               vendor_id INTEGER NOT NULL,
               title TEXT NOT NULL,
               basis TEXT,
               period TEXT,
               pct REAL,
               amount REAL,
               base_amount REAL,
               cn_txn_id INTEGER,
               note TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_inc ON hop_dist_incentives(workspace_id, vendor_id)")


def _company_name(conn, ws, vid) -> str | None:
    if not vid:
        return None
    r = conn.execute("SELECT company FROM hop_vendors WHERE workspace_id=? AND id=?", (ws, int(vid))).fetchone()
    return r[0] if r else None


def _check_company(conn, ws, vid: int) -> dict:
    r = conn.execute("SELECT id, company, gst_no, address, state, city, contact_person, contact_designation, mobile, email, "
                     "cash_discount_pct, credit_days FROM hop_vendors WHERE workspace_id=? AND id=? AND is_principal=1", (ws, int(vid))).fetchone()
    if not r:
        raise LookupError("Company not found")
    return dict(r)


# ══ Item card ══════════════════════════════════════════════════════════════════════════════════════════════
def item_card(conn: sqlite3.Connection, ws: str, pid: int) -> dict[str, Any]:
    from app import hop_dist_margins as M
    from app import hop_dist_receive as R
    from app import hop_dist_terms as T

    ensure_schema(conn)
    a = conn.execute("SELECT * FROM hop_products WHERE workspace_id=? AND id=? AND (deleted_at IS NULL OR trim(deleted_at)='')",
                     (ws, int(pid))).fetchone()
    if not a:
        raise LookupError("Article not found")
    a = dict(a)
    comps = T._companies(conn, ws)
    ids = {int(c["id"]) for c in comps}
    only = next(iter(ids)) if len(ids) == 1 else None
    vid = T._owner(a, ids, only)
    # terms + prices from the Terms book
    terms, prices, src = None, None, "none"
    if vid:
        row = next((r for r in M.book(conn, ws, vid)["articles"] if int(r["id"]) == int(pid)), None)
        if row:
            terms, prices, src = row.get("terms"), row.get("prices"), row.get("from")
    # godowns
    godowns = []
    try:
        from app import hop_godowns as G

        names = {int(g["id"]): g["name"] for g in G.list_godowns(conn, ws)}
        gd = G.stock_by_godown(conn, ws, int(pid))
        if gd:
            godowns = [{"godown": names.get(int(k), "Godown"), "qty": v} for k, v in gd[0]["by_godown"].items() if abs(v) > 1e-9]
    except Exception:
        godowns = []
    # still to come on company invoices
    to_come = []
    for b in R.to_receive(conn, ws, vid)["bills"] if vid else []:
        for ln in b["lines"]:
            if int(ln["product_id"]) == int(pid) and ln["pending"] > 0:
                to_come.append({"ci_no": b["ci_no"], "ci_date": b["ci_date"], "qty": ln["pending"], "bill_id": b["bill_id"]})
    # sold in the last 90 days
    since = (_today() - timedelta(days=90)).isoformat()
    sold = conn.execute(f"""SELECT COALESCE(SUM(ABS(l.qty)),0) FROM hop_party_transactions t
        JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
        WHERE t.workspace_id=? AND t.txn_type=1 AND {LIVE} AND l.product_id=? AND date(t.txn_date)>=date(?)""",
                        (ws, int(pid), since)).fetchone()[0]
    # last stock lines, each with where it came from
    R.ensure_cols(conn)
    lines = []
    for j in conn.execute("SELECT entry_date, qty_delta, reason, reference, vendor_id, doc_txn_id, so_id FROM hop_stock_journals "
                          "WHERE workspace_id=? AND product_id=? ORDER BY entry_date DESC, id DESC LIMIT 25", (ws, int(pid))):
        j = dict(j)
        ref = _s(j.get("reference"))
        kind = ("Goods received" if ref.startswith("grn:") else "Bill" if ref.startswith("doc:") else
                "By hand" if ref.startswith("manual:") else "Other")
        lines.append({"date": j["entry_date"], "qty": _f(j["qty_delta"]), "reason": j.get("reason"), "kind": kind,
                      "company": _company_name(conn, ws, j.get("vendor_id")), "doc_txn_id": j.get("doc_txn_id")})
    return {"id": int(pid), "code": a.get("code"), "name": a.get("name"), "category": a.get("category"), "season": a.get("season"),
            "unit": a.get("unit"), "hsn": a.get("hsn"), "barcode": a.get("barcode"), "gst_pct": a.get("gst_pct"),
            "mrp": a.get("mrp"), "dp": a.get("purchase_price"), "ptr": a.get("selling_price"),
            "vendor_id": vid, "company": _company_name(conn, ws, vid), "terms": terms, "prices": prices, "terms_from": src,
            "stock": _f(a.get("stock_qty")), "godowns": godowns, "to_come": to_come,
            "to_come_qty": round(sum(x["qty"] for x in to_come), 3), "sold_90_days": _f(sold),
            "stock_lines": lines, "companies": [{"id": int(c["id"]), "company": c["company"]} for c in comps]}


def hand_stock(conn: sqlite3.Connection, ws: str, pid: int, p: dict, *, user: str = "") -> dict[str, Any]:
    """{qty (+ adds, − reduces), vendor_id | opening: true, reason, entry_date, godown_id} — stock by hand, always with
    the company it came from (or "Opening stock")."""
    from app import hop_dist_receive as R

    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(pid))).fetchone():
        raise LookupError("Article not found")
    q = _f(p.get("qty"))
    if abs(q) < 1e-9:
        raise ValueError("Type the quantity (+ to add, − to take out)")
    opening = p.get("opening") in (True, 1, "1", "true")
    vid = p.get("vendor_id")
    if not opening and q > 0 and vid in (None, ""):
        raise ValueError("Which company did this stock come from? Pick the company, or tick Opening stock.")
    if vid not in (None, ""):
        _check_company(conn, ws, int(vid))
    why = _s(p.get("reason"))[:120]
    if q < 0 and not why:
        raise ValueError("Why is stock going out? (damaged, sample, count correction …)")
    label = "Opening stock" if opening else ("Stock added by hand" if q > 0 else "Stock taken out by hand")
    R.ensure_cols(conn)
    day = _s(p.get("entry_date"))[:10] or _now()[:10]
    gid = int(p["godown_id"]) if p.get("godown_id") not in (None, "") else None
    conn.execute("INSERT INTO hop_stock_journals (workspace_id, product_id, entry_date, qty_delta, reason, reference, created_at, "
                 "godown_id, vendor_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ws, int(pid), day, q, " · ".join(x for x in (label, why, user) if x), "manual:dist", _now(), gid,
                  int(vid) if vid not in (None, "") else None))
    conn.execute("UPDATE hop_products SET stock_qty=COALESCE(stock_qty,0)+?, updated_at=? WHERE workspace_id=? AND id=?",
                 (q, _now(), ws, int(pid)))
    conn.commit()
    return item_card(conn, ws, pid)


# ══ Company: edit details ═════════════════════════════════════════════════════════════════════════════════
def company_details(conn: sqlite3.Connection, ws: str, vid: int) -> dict[str, Any]:
    ensure_schema(conn)
    return _check_company(conn, ws, vid)


def edit_company(conn: sqlite3.Connection, ws: str, vid: int, p: dict) -> dict[str, Any]:
    """{company, gst_no, address, state, city, contact_person, mobile, email} — a company's own details."""
    import re

    ensure_schema(conn)
    _check_company(conn, ws, vid)
    sets, vals = [], []
    if "company" in p:
        name = _s(p.get("company"))[:120]
        if not name:
            raise ValueError("Company name is required")
        dup = conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND lower(company)=lower(?) AND id!=?",
                           (ws, name, int(vid))).fetchone()
        if dup:
            raise ValueError(f"Another supplier is already called {name}")
        sets.append("company=?"); vals.append(name)
    if "gst_no" in p:
        g = _s(p.get("gst_no")).upper()
        if g and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", g):
            raise ValueError("GSTIN looks wrong — 15 characters like 27ABCDE1234F1Z5")
        sets.append("gst_no=?"); vals.append(g or None)
    for k, n in (("address", 400), ("state", 60), ("city", 60), ("contact_person", 80), ("contact_designation", 80),
                 ("mobile", 15), ("email", 120)):
        if k in p:
            sets.append(f"{k}=?"); vals.append(_s(p.get(k))[:n] or None)
    if not sets:
        raise ValueError("Nothing to save")
    conn.execute(f"UPDATE hop_vendors SET {', '.join(sets)}, updated_at=? WHERE workspace_id=? AND id=?",
                 [*vals, _now(), ws, int(vid)])
    conn.commit()
    return _check_company(conn, ws, vid)


# ══ CD and incentives ═════════════════════════════════════════════════════════════════════════════════════
def _paid_on(conn, ws, bill_id: int) -> str | None:
    """Last payment date linked to a bill (when it was fully paid)."""
    r = conn.execute("""SELECT MAX(p.txn_date) FROM hop_payment_links l JOIN hop_party_transactions p
                        ON p.workspace_id=l.workspace_id AND p.id=l.payment_txn_id
                        WHERE l.workspace_id=? AND l.bill_txn_id=?""", (ws, int(bill_id))).fetchone()
    return r[0][:10] if r and r[0] else None


def cash_discount(conn: sqlite3.Connection, ws: str, vid: int, frm: str = "", to: str = "") -> dict[str, Any]:
    """Per company invoice: CD that can be earned (CD % of the invoice), pay-by date, and earned / lost / open."""
    ensure_schema(conn)
    c = _check_company(conn, ws, vid)
    pct, days = _f(c.get("cash_discount_pct")), int(c.get("credit_days") or 0)
    rows = []
    if pct <= 0:
        return {"company": c["company"], "cd_pct": 0, "credit_days": days, "rows": [], "earned": 0, "lost": 0, "open": 0,
                "note": "No cash discount % set for this company (Terms → Companies & terms)."}
    sql = f"""SELECT t.id, t.txn_date, t.party_bill_no, t.txn_number, t.total_amount, t.balance_amount FROM hop_party_transactions t
              WHERE t.workspace_id=? AND t.txn_type=2 AND t.party_type='vendor' AND t.party_id=? AND {LIVE}"""
    args: list[Any] = [ws, int(vid)]
    if frm:
        sql += " AND date(t.txn_date)>=date(?)"; args.append(frm)
    if to:
        sql += " AND date(t.txn_date)<=date(?)"; args.append(to)
    today = _today().isoformat()
    for t in conn.execute(sql + " ORDER BY t.txn_date DESC LIMIT 300", args):
        t = dict(t)
        amt = _f(t["total_amount"])
        cd = round(amt * pct / 100, 2)
        by = (date.fromisoformat(str(t["txn_date"])[:10]) + timedelta(days=days)).isoformat()
        bal = _f(t.get("balance_amount")) if t.get("balance_amount") is not None else amt
        paid = _paid_on(conn, ws, int(t["id"])) if bal <= 0.005 else None
        if bal <= 0.005:
            status = "earned" if (paid or today) <= by else "lost"
        else:
            status = "open" if today <= by else "lost"
        rows.append({"bill_id": t["id"], "ci_no": t.get("party_bill_no") or t.get("txn_number"), "ci_date": t["txn_date"],
                     "amount": amt, "cd": cd, "pay_by": by, "paid_on": paid, "balance": bal, "status": status})
    return {"company": c["company"], "cd_pct": pct, "credit_days": days, "rows": rows,
            "earned": round(sum(r["cd"] for r in rows if r["status"] == "earned"), 2),
            "lost": round(sum(r["cd"] for r in rows if r["status"] == "lost"), 2),
            "open": round(sum(r["cd"] for r in rows if r["status"] == "open"), 2),
            "pay_soon": [r for r in rows if r["status"] == "open" and r["pay_by"] <= (_today() + timedelta(days=7)).isoformat()]}


def _credit_notes(conn, ws, vid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(f"""SELECT t.id, t.txn_date, t.party_bill_no, t.txn_number, t.total_amount, t.notes
        FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=16 AND t.party_type='vendor' AND t.party_id=? AND {LIVE}
        ORDER BY t.txn_date DESC LIMIT 200""", (ws, int(vid)))]


def incentives(conn: sqlite3.Connection, ws: str, vid: int) -> dict[str, Any]:
    """What the company promised (incentive / scheme) and which credit note paid it."""
    ensure_schema(conn)
    c = _check_company(conn, ws, vid)
    cns = {int(r["id"]): r for r in _credit_notes(conn, ws, vid)}
    used = set()
    rows = []
    for r in conn.execute("SELECT * FROM hop_dist_incentives WHERE workspace_id=? AND vendor_id=? ORDER BY id DESC", (ws, int(vid))):
        r = dict(r)
        due = _f(r.get("amount")) or round(_f(r.get("base_amount")) * _f(r.get("pct")) / 100, 2)
        cn = cns.get(int(r["cn_txn_id"])) if r.get("cn_txn_id") else None
        got = _f(cn["total_amount"]) if cn else 0.0
        if cn:
            used.add(int(cn["id"]))
        rows.append({**r, "expected": due, "received": got, "still_due": round(max(due - got, 0), 2),
                     "credit_note": {"id": cn["id"], "no": cn.get("party_bill_no") or cn.get("txn_number"), "date": cn["txn_date"]} if cn else None,
                     "status": "received" if cn and got >= due - 0.5 else ("part received" if cn else "due")})
    free = [{"id": k, "no": v.get("party_bill_no") or v.get("txn_number"), "date": v["txn_date"], "amount": _f(v["total_amount"]),
             "note": v.get("notes")} for k, v in cns.items() if k not in used]
    return {"company": c["company"], "rows": rows, "expected": round(sum(r["expected"] for r in rows), 2),
            "received": round(sum(r["received"] for r in rows), 2), "still_due": round(sum(r["still_due"] for r in rows), 2),
            "credit_notes_not_linked": free}


def save_incentive(conn: sqlite3.Connection, ws: str, vid: int, p: dict) -> dict[str, Any]:
    """{id?, title, basis (order / month / season / target / scheme), period, pct, base_amount, amount, cn_txn_id, note}
    or {id, delete: true}."""
    ensure_schema(conn)
    _check_company(conn, ws, vid)
    iid = int(p["id"]) if p.get("id") not in (None, "") else None
    if iid and p.get("delete"):
        conn.execute("DELETE FROM hop_dist_incentives WHERE workspace_id=? AND vendor_id=? AND id=?", (ws, int(vid), iid))
        conn.commit()
        return incentives(conn, ws, vid)
    title = _s(p.get("title"))[:120]
    if not title:
        raise ValueError("Write what the incentive is for (e.g. AW26 target, 2% on orders above ₹5 lakh)")
    pct = _f(p.get("pct")) if p.get("pct") not in (None, "") else None
    amount = _f(p.get("amount")) if p.get("amount") not in (None, "") else None
    if not pct and not amount:
        raise ValueError("Give the incentive as % or as an amount")
    if pct is not None and not 0 < pct <= 50:
        raise ValueError("Incentive % must be between 0 and 50")
    cn = int(p["cn_txn_id"]) if p.get("cn_txn_id") not in (None, "") else None
    if cn and not conn.execute("SELECT 1 FROM hop_party_transactions WHERE workspace_id=? AND id=? AND txn_type=16 AND party_id=?",
                               (ws, cn, int(vid))).fetchone():
        raise LookupError("That credit note is not from this company")
    vals = (title, _s(p.get("basis"))[:20] or None, _s(p.get("period"))[:40] or None, pct, amount,
            _f(p.get("base_amount")) if p.get("base_amount") not in (None, "") else None, cn, _s(p.get("note"))[:300] or None)
    if iid:
        conn.execute("""UPDATE hop_dist_incentives SET title=?, basis=?, period=?, pct=?, amount=?, base_amount=?, cn_txn_id=?, note=?,
                        updated_at=? WHERE workspace_id=? AND vendor_id=? AND id=?""", (*vals, _now(), ws, int(vid), iid))
    else:
        conn.execute("""INSERT INTO hop_dist_incentives (title, basis, period, pct, amount, base_amount, cn_txn_id, note, workspace_id,
                        vendor_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (*vals, ws, int(vid), _now(), _now()))
    conn.commit()
    return incentives(conn, ws, vid)


# ══ Per company report ════════════════════════════════════════════════════════════════════════════════════
def company_report(conn: sqlite3.Connection, ws: str, vid: int, frm: str = "", to: str = "") -> dict[str, Any]:
    """Ordered → invoiced → received → to come (qty, ₹) and sales / cost / margin / CD / incentives, by category."""
    from app import hop_dist_receive as R
    from app import hop_dist_terms as T

    ensure_schema(conn)
    c = _check_company(conn, ws, vid)
    today = _today()
    frm = frm or date(today.year if today.month >= 4 else today.year - 1, 4, 1).isoformat()
    to = to or today.isoformat()
    comps = T._companies(conn, ws)
    ids = {int(x["id"]) for x in comps}
    only = next(iter(ids)) if len(ids) == 1 else None
    arts = {int(a["id"]): a for a in T._articles(conn, ws) if T._owner(a, ids, only) == int(vid)}

    def qty_val(txn_type: int, party: bool) -> tuple[float, float]:
        sql = f"""SELECT COALESCE(SUM(ABS(l.qty)),0), COALESCE(SUM(l.line_total - COALESCE(l.tax_amount,0)),0)
                  FROM hop_party_transactions t JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
                  WHERE t.workspace_id=? AND t.txn_type=? AND {LIVE} AND COALESCE(l.is_charge,0)=0
                  AND date(t.txn_date) BETWEEN date(?) AND date(?)"""
        args: list[Any] = [ws, txn_type, frm, to]
        if party:
            sql += " AND t.party_type='vendor' AND t.party_id=?"
            args.append(int(vid))
        r = conn.execute(sql, args).fetchone()
        return _f(r[0]), _f(r[1])

    ordered = qty_val(66, True)
    invoiced = qty_val(2, True)
    received = _f(conn.execute("""SELECT COALESCE(SUM(l.qty),0) FROM hop_dist_receipt_lines l JOIN hop_dist_receipts r ON r.id=l.receipt_id
                                  WHERE r.workspace_id=? AND r.vendor_id=? AND date(r.received_on) BETWEEN date(?) AND date(?)""",
                                 (ws, int(vid), frm, to)).fetchone()[0])
    to_come = R.to_receive(conn, ws, vid)["pending_qty"]
    # sales of this company's articles, by category
    cat = defaultdict(lambda: {"qty": 0.0, "sales": 0.0, "cost": 0.0})
    for r in conn.execute(f"""SELECT t.txn_type, l.product_id, ABS(COALESCE(l.qty,0)) AS qty,
                                 COALESCE(l.line_total,0) - COALESCE(l.tax_amount,0) AS taxable
                              FROM hop_party_transactions t JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
                              WHERE t.workspace_id=? AND t.txn_type IN (1,21) AND {LIVE} AND COALESCE(l.is_charge,0)=0
                              AND date(t.txn_date) BETWEEN date(?) AND date(?)""", (ws, frm, to)):
        a = arts.get(int(r["product_id"])) if r["product_id"] else None
        if not a:
            continue
        sign = -1 if int(r["txn_type"]) == 21 else 1
        k = cat[_s(a.get("category")) or "No category"]
        k["qty"] += sign * _f(r["qty"]); k["sales"] += sign * _f(r["taxable"]); k["cost"] += sign * _f(r["qty"]) * _f(a.get("purchase_price"))
    cats = []
    for name, k in sorted(cat.items()):
        m = k["sales"] - k["cost"]
        cats.append({"category": name, "qty": round(k["qty"], 3), "sales": round(k["sales"], 2), "cost": round(k["cost"], 2),
                     "margin": round(m, 2), "margin_pct": round(m / k["sales"] * 100, 1) if k["sales"] else None})
    sales = sum(x["sales"] for x in cats)
    cost = sum(x["cost"] for x in cats)
    cd = cash_discount(conn, ws, vid, frm, to)
    inc = incentives(conn, ws, vid)
    gross = sales - cost
    net = gross + cd["earned"] + inc["received"]
    return {"company": c["company"], "vendor_id": int(vid), "from": frm, "to": to,
            "flow": {"ordered_qty": ordered[0], "ordered_value": round(ordered[1], 2),
                     "invoiced_qty": invoiced[0], "invoiced_value": round(invoiced[1], 2),
                     "received_qty": round(received, 3), "to_come_qty": to_come,
                     "not_invoiced_qty": round(max(ordered[0] - invoiced[0], 0), 3)},
            "margin": {"sales": round(sales, 2), "cost": round(cost, 2), "gross": round(gross, 2),
                       "gross_pct": round(gross / sales * 100, 1) if sales else None,
                       "cd_earned": cd["earned"], "cd_lost": cd["lost"], "cd_open": cd["open"],
                       "incentive_received": inc["received"], "incentive_due": inc["still_due"],
                       "net": round(net, 2), "net_pct": round(net / sales * 100, 1) if sales else None},
            "by_category": cats, "cd_pay_soon": cd.get("pay_soon", []),
            "credit_notes_not_linked": inc["credit_notes_not_linked"]}


def summary_text(conn: sqlite3.Connection, ws: str) -> str:
    """For Ask Nexora / AI: per company — flow, margin, CD, incentives."""
    from app import hop_dist_terms as T

    ensure_schema(conn)
    out = []
    for c in T._companies(conn, ws):
        r = company_report(conn, ws, int(c["id"]))
        f, m = r["flow"], r["margin"]
        out.append(f"{r['company']}: ordered {f['ordered_qty']:g}, invoiced {f['invoiced_qty']:g}, received {f['received_qty']:g}, "
                   f"to come {f['to_come_qty']:g}; sales ₹{m['sales']:,.0f}, gross margin ₹{m['gross']:,.0f}, CD earned "
                   f"₹{m['cd_earned']:,.0f} (lost ₹{m['cd_lost']:,.0f}), incentive received ₹{m['incentive_received']:,.0f} "
                   f"(due ₹{m['incentive_due']:,.0f}), net ₹{m['net']:,.0f}")
    return "\n".join(out) or "No company set yet."
