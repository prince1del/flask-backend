"""Distributor: Goods received — stock comes in when the goods arrive, not when the company's invoice (CI) is saved.

Owner 21-11: "jab uski inward hi align nahi hogi to hamari app ka kya kaam" — every inward must say which company
(company B, company C …), which CI / bill and which SO it belongs to.

  * A purchase bill / CI from a company I distribute for (``hop_vendors.is_principal=1``) saved from now on is marked
    ``stock_mode='receipt'``: it moves no stock when saved — the goods are **in transit**.
  * **Goods received** against that CI: tick what came (part is fine). Each receipt writes stock journal rows with the
    company (``vendor_id``), the CI (``doc_txn_id``) and the SO (``so_id``), reference ``grn:<id>``.
  * Bills saved before this keep their stock as it was (``stock_mode`` empty → stock on save, as before).
Quantities are matched per article (not per bill line), so editing the CI later does not lose what was received.
"""

from __future__ import annotations

import sqlite3
from typing import Any


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


def ensure_cols(conn: sqlite3.Connection) -> None:
    """Columns other modules need early (hop_money calls this before deciding a bill waits for the goods)."""
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(hop_party_transactions)")}
    if "stock_mode" not in tcols:
        conn.execute("ALTER TABLE hop_party_transactions ADD COLUMN stock_mode TEXT")
    jcols = {r[1] for r in conn.execute("PRAGMA table_info(hop_stock_journals)")}
    for col in ("vendor_id", "doc_txn_id", "so_id"):
        if col not in jcols:
            conn.execute(f"ALTER TABLE hop_stock_journals ADD COLUMN {col} INTEGER")


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_dist_brand as BR

    BR.ensure_schema(conn)
    ensure_cols(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_receipts (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               bill_txn_id INTEGER NOT NULL,
               vendor_id INTEGER,
               so_id INTEGER,
               received_on TEXT NOT NULL,
               godown_id INTEGER,
               note TEXT,
               by_user TEXT,
               created_at TEXT NOT NULL
           )""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_receipt_lines (
               receipt_id INTEGER NOT NULL,
               product_id INTEGER NOT NULL,
               qty REAL NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_rcpt ON hop_dist_receipts(workspace_id, bill_txn_id)")


def waits_for_goods(conn: sqlite3.Connection, ws: str, txn: dict[str, Any], new: bool) -> bool:
    """Called by hop_money before it moves stock for a document: True = this CI waits for Goods received."""
    if int(txn.get("txn_type") or 0) != 2 or _s(txn.get("party_type")) != "vendor" or not txn.get("party_id"):
        return False
    try:
        r = conn.execute("SELECT is_principal FROM hop_vendors WHERE workspace_id=? AND id=?",
                         (ws, int(txn["party_id"]))).fetchone()
    except sqlite3.OperationalError:
        return False   # no companies in this firm (Business UI)
    if not r or not r[0]:
        return False
    ensure_cols(conn)
    mode = conn.execute("SELECT stock_mode FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                        (ws, int(txn["id"]))).fetchone()
    if mode and mode[0] == "receipt":
        return True
    if new:
        conn.execute("UPDATE hop_party_transactions SET stock_mode='receipt' WHERE workspace_id=? AND id=?",
                     (ws, int(txn["id"])))
        return True
    return False


def _so_of(conn, ws, bill_id: int) -> int | None:
    try:
        r = conn.execute("SELECT so_id FROM hop_dist_so_bills WHERE workspace_id=? AND bill_txn_id=? LIMIT 1",
                         (ws, int(bill_id))).fetchone()
        return int(r[0]) if r else None
    except sqlite3.OperationalError:
        return None


def _billed(conn, ws, bill_id: int) -> tuple[dict[int, dict], list[str]]:
    """Per article: name and qty on the CI (base units). Lines not in Article Master are listed apart."""
    from app import hop_items
    from app.hop_money import _match_product

    out: dict[int, dict] = {}
    unmatched: list[str] = []
    src = conn.execute("SELECT source_txn_id FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                       (ws, int(bill_id))).fetchone()
    if not src:
        return out, unmatched
    for ln in conn.execute("SELECT * FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=?", (ws, int(src[0]))).fetchall():
        ln = dict(ln)
        if ln.get("is_charge"):
            continue
        pid = _match_product(conn, ws, ln)
        if not pid:
            if _s(ln.get("item_name")):
                unmatched.append(_s(ln.get("item_name")))
            continue
        q = hop_items.stock_qty_for_line(conn, ws, pid, ln)
        if q is None or q <= 0:
            continue
        o = out.setdefault(pid, {"product_id": pid, "item": _s(ln.get("item_name")), "billed": 0.0})
        o["billed"] += q
    return out, unmatched


def _received(conn, ws, bill_id: int) -> dict[int, float]:
    return {int(r[0]): _f(r[1]) for r in conn.execute(
        "SELECT l.product_id, SUM(l.qty) FROM hop_dist_receipt_lines l JOIN hop_dist_receipts r ON r.id=l.receipt_id "
        "WHERE r.workspace_id=? AND r.bill_txn_id=? GROUP BY l.product_id", (ws, int(bill_id)))}


def bill_status(conn: sqlite3.Connection, ws: str, bill_id: int) -> dict[str, Any]:
    """One CI: each article billed / received / still to come, the receipts so far, the shipment."""
    ensure_schema(conn)
    t = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(bill_id))).fetchone()
    if not t:
        raise LookupError("Invoice not found")
    t = dict(t)
    billed, unmatched = _billed(conn, ws, bill_id)
    got = _received(conn, ws, bill_id)
    lines = []
    for pid, b in billed.items():
        r = got.get(pid, 0.0)
        lines.append({**b, "received": round(r, 3), "pending": round(max(b["billed"] - r, 0.0), 3)})
    pend = sum(x["pending"] for x in lines)
    recv = sum(x["received"] for x in lines)
    company = conn.execute("SELECT company FROM hop_vendors WHERE workspace_id=? AND id=?", (ws, int(t.get("party_id") or 0))).fetchone()
    ship = conn.execute("SELECT lr_no, transporter, eta, received_on FROM hop_dist_shipments WHERE workspace_id=? AND bill_txn_id=?",
                        (ws, int(bill_id))).fetchone()
    receipts = [dict(r) for r in conn.execute(
        "SELECT id, received_on, note, by_user FROM hop_dist_receipts WHERE workspace_id=? AND bill_txn_id=? ORDER BY id",
        (ws, int(bill_id)))]
    for rc in receipts:
        rc["lines"] = [{"product_id": int(x[0]), "qty": _f(x[1])} for x in conn.execute(
            "SELECT product_id, qty FROM hop_dist_receipt_lines WHERE receipt_id=?", (rc["id"],))]
    return {"bill_id": int(bill_id), "vendor_id": t.get("party_id"), "company": company[0] if company else None,
            "ci_no": t.get("party_bill_no") or t.get("txn_number"), "ci_date": t.get("party_bill_date") or t.get("txn_date"),
            "total": _f(t.get("total_amount") or t.get("total")), "waits_for_goods": t.get("stock_mode") == "receipt",
            "status": "received" if lines and pend <= 1e-9 else ("part received" if recv > 0 else "in transit"),
            "lines": lines, "not_in_article_master": unmatched, "receipts": receipts,
            "shipment": dict(zip(("lr_no", "transporter", "eta", "received_on"), ship)) if ship else None,
            "so_id": _so_of(conn, ws, bill_id)}


def to_receive(conn: sqlite3.Connection, ws: str, vendor_id: int | None = None, include_done: bool = False) -> dict[str, Any]:
    """Every company CI that waits for the goods (in transit / part received), newest first."""
    ensure_schema(conn)
    sql = ("SELECT t.id FROM hop_party_transactions t JOIN hop_vendors v ON v.id=t.party_id AND v.workspace_id=t.workspace_id "
           "WHERE t.workspace_id=? AND t.txn_type=2 AND t.stock_mode='receipt' AND v.is_principal=1 "
           "AND (t.deleted_at IS NULL OR trim(t.deleted_at)='')")
    args: list[Any] = [ws]
    if vendor_id:
        sql += " AND t.party_id=?"
        args.append(int(vendor_id))
    sql += " ORDER BY t.id DESC LIMIT 200"
    ids = [int(r[0]) for r in conn.execute(sql, args)]
    bills = [bill_status(conn, ws, i) for i in ids]
    if not include_done:
        bills = [b for b in bills if b["status"] != "received"]
    return {"bills": bills, "in_transit": sum(b["status"] == "in transit" for b in bills),
            "part_received": sum(b["status"] == "part received" for b in bills),
            "pending_qty": round(sum(x["pending"] for b in bills for x in b["lines"]), 3)}


def receive(conn: sqlite3.Connection, ws: str, bill_id: int, p: dict, *, user: str = "") -> dict[str, Any]:
    """{lines: [{product_id, qty}], received_on, godown_id, note} — what came against this CI. Stock goes up now."""
    ensure_schema(conn)
    st = bill_status(conn, ws, bill_id)
    if not st["waits_for_goods"]:
        raise ValueError("This bill already moved stock when it was saved — nothing to receive.")
    pend = {int(x["product_id"]): x for x in st["lines"]}
    want = []
    for ln in p.get("lines") or []:
        try:
            pid, q = int(ln.get("product_id")), float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            raise ValueError("Type the quantity received as a number")
        if q <= 0:
            continue
        if pid not in pend:
            raise ValueError("That article is not on this invoice")
        if q > pend[pid]["pending"] + 1e-6:
            raise ValueError(f"{pend[pid]['item']}: only {pend[pid]['pending']:g} still to come on this invoice")
        want.append((pid, q))
    if not want:
        raise ValueError("Tick at least one article that came")
    day = _s(p.get("received_on"))[:10] or _now()[:10]
    godown = int(p["godown_id"]) if p.get("godown_id") not in (None, "") else None
    vid = int(st["vendor_id"]) if st.get("vendor_id") else None
    cur = conn.execute(
        "INSERT INTO hop_dist_receipts (workspace_id, bill_txn_id, vendor_id, so_id, received_on, godown_id, note, by_user, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)", (ws, int(bill_id), vid, st.get("so_id"), day, godown, _s(p.get("note"))[:300] or None,
                                       user or None, _now()))
    rid = int(cur.lastrowid)
    for pid, q in want:
        conn.execute("INSERT INTO hop_dist_receipt_lines (receipt_id, product_id, qty) VALUES (?,?,?)", (rid, pid, q))
        conn.execute(
            """INSERT INTO hop_stock_journals (workspace_id, product_id, entry_date, qty_delta, reason, reference, created_at,
                                               godown_id, vendor_id, doc_txn_id, so_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ws, pid, day, q, f"Goods received · {st['company'] or 'company'} · CI {st['ci_no'] or bill_id}", f"grn:{rid}",
             _now(), godown, vid, int(bill_id), st.get("so_id")))
        conn.execute("UPDATE hop_products SET stock_qty=COALESCE(stock_qty,0)+?, updated_at=? WHERE workspace_id=? AND id=?",
                     (q, _now(), ws, pid))
    after = bill_status(conn, ws, bill_id)
    if after["status"] == "received":   # the shipment is in
        conn.execute("""INSERT INTO hop_dist_shipments (workspace_id, bill_txn_id, received_on, updated_at) VALUES (?,?,?,?)
                        ON CONFLICT(workspace_id, bill_txn_id) DO UPDATE SET
                        received_on=COALESCE(hop_dist_shipments.received_on, excluded.received_on), updated_at=excluded.updated_at""",
                     (ws, int(bill_id), day, _now()))
    conn.commit()
    return bill_status(conn, ws, bill_id)


def undo_receipt(conn: sqlite3.Connection, ws: str, receipt_id: int) -> dict[str, Any]:
    """Take back one receipt typed by mistake: its stock goes out again."""
    ensure_schema(conn)
    r = conn.execute("SELECT bill_txn_id FROM hop_dist_receipts WHERE workspace_id=? AND id=?", (ws, int(receipt_id))).fetchone()
    if not r:
        raise LookupError("Receipt not found")
    ref = f"grn:{int(receipt_id)}"
    for j in conn.execute("SELECT product_id, qty_delta FROM hop_stock_journals WHERE workspace_id=? AND reference=?", (ws, ref)).fetchall():
        conn.execute("UPDATE hop_products SET stock_qty=COALESCE(stock_qty,0)-?, updated_at=? WHERE workspace_id=? AND id=?",
                     (_f(j[1]), _now(), ws, int(j[0])))
    conn.execute("DELETE FROM hop_stock_journals WHERE workspace_id=? AND reference=?", (ws, ref))
    conn.execute("DELETE FROM hop_dist_receipt_lines WHERE receipt_id=?", (int(receipt_id),))
    conn.execute("DELETE FROM hop_dist_receipts WHERE workspace_id=? AND id=?", (ws, int(receipt_id)))
    conn.commit()
    return bill_status(conn, ws, int(r[0]))


def stock_by_company(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    """Stock of each company's articles now (qty and value at DP) — company B vs company C."""
    from app import hop_dist_terms as T

    T.ensure_schema(conn)
    comps = T._companies(conn, ws)
    ids = {int(c["id"]) for c in comps}
    only = next(iter(ids)) if len(ids) == 1 else None
    tot: dict[int | None, dict] = {}
    for a in T._articles(conn, ws):
        v = T._owner(a, ids, only)
        q = _f(conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (a["id"],)).fetchone()[0])
        o = tot.setdefault(v, {"qty": 0.0, "value": 0.0, "articles": 0})
        o["qty"] += q
        o["value"] += q * _f(a.get("purchase_price"))
        o["articles"] += 1
    out = [{"vendor_id": int(c["id"]), "company": c["company"], **{k: round(x, 2) for k, x in tot.get(int(c["id"]), {"qty": 0, "value": 0, "articles": 0}).items()}}
           for c in comps]
    if None in tot:
        out.append({"vendor_id": None, "company": "No company", **{k: round(x, 2) for k, x in tot[None].items()}})
    return out


def summary_text(conn: sqlite3.Connection, ws: str) -> str:
    """For Ask Nexora / AI: what is in transit per company, and stock per company."""
    t = to_receive(conn, ws)
    lines = [f"{len(t['bills'])} company invoices wait for goods ({t['in_transit']} in transit, {t['part_received']} part "
             f"received, {t['pending_qty']:g} qty still to come)"]
    for b in t["bills"][:10]:
        lines.append(f"{b['company']} CI {b['ci_no']} {b['ci_date'] or ''}: {b['status']}, "
                     f"{sum(x['pending'] for x in b['lines']):g} qty to come")
    for s in stock_by_company(conn, ws):
        lines.append(f"Stock {s['company']}: {s['qty']:g} qty, ₹{s['value']:,.0f} at DP, {s['articles']} articles")
    return "\n".join(lines)
