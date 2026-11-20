"""Distributor UI — a distributor's own business on the Business (HoP) books.

Owner's choice (19-11-2026): the distributor runs their own firm. The company they buy from (e.g. the brand) is a
**principal** — a supplier marked as such. The flow:

    My order to the company (Purchase Order, type 66)
        → Company's Sales Order (SO) — number / date / lines, read from the SO PDF by AI or typed
        → Company's invoice (CI) = Purchase Bill (type 2), converted from the order or linked to the SO
        → Company ledger (party statement of the principal) + payment status (what I still owe, overdue)
    Retailers (customers): sale invoices / challans → "which retailer got what", outstanding
    Warehouse: stock now, stock value, godowns
    Forecast: sale speed per item → days of stock left → what to order from the company, and when

Everything is computed from the same tables as the Business UI (one backend; phone and web read the same routes).
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

PO, BILL, SALE, CHALLAN, SALE_RETURN, PAY_OUT, DEBIT_NOTE = 66, 2, 1, 30, 21, 4, 16
LIVE = "(t.deleted_at IS NULL OR trim(t.deleted_at)='')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()   # IST


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(float(v or 0) + 0.0, 2)


def _s(v: Any) -> str:
    return str(v or "").strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_vendors)")}
    if "is_principal" not in cols:
        conn.execute("ALTER TABLE hop_vendors ADD COLUMN is_principal INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_so (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               vendor_id INTEGER,
               po_txn_id INTEGER,              -- my Purchase Order this SO answers (optional)
               so_number TEXT NOT NULL,
               so_date TEXT,
               amount REAL DEFAULT 0,
               lines_json TEXT,                -- [{item_name, qty, rate, amount}]
               notes TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               deleted_at TEXT
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_so_ws ON hop_dist_so(workspace_id, deleted_at)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_so_bills (
               workspace_id TEXT NOT NULL,
               so_id INTEGER NOT NULL,
               bill_txn_id INTEGER NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY (workspace_id, so_id, bill_txn_id)
           )""")


# ── principals (the companies I distribute for) ──────────────────────────────────────────────────────────
def principals(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, company, gst_no, city, mobile, payment_terms, lead_time_days FROM hop_vendors "
        "WHERE workspace_id=? AND is_principal=1 ORDER BY lower(company)", (ws,)).fetchall()]


def set_principal(conn: sqlite3.Connection, ws: str, vendor_id: int, on: bool) -> list[dict[str, Any]]:
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND id=?", (ws, int(vendor_id))).fetchone():
        raise LookupError("Supplier not found")
    conn.execute("UPDATE hop_vendors SET is_principal=?, updated_at=? WHERE workspace_id=? AND id=?",
                 (1 if on else 0, _now(), ws, int(vendor_id)))
    conn.commit()
    return principals(conn, ws)


def _principal_ids(conn: sqlite3.Connection, ws: str) -> list[int]:
    ids = [int(p["id"]) for p in principals(conn, ws)]
    return ids


def _in_ids(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids) or "-1"


# ── Sales Orders received from the company ───────────────────────────────────────────────────────────────
def _so_row(r: sqlite3.Row | dict) -> dict[str, Any]:
    d = dict(r)
    try:
        d["lines"] = json.loads(d.pop("lines_json") or "[]")
    except Exception:
        d["lines"] = []
    return d


def _clean_lines(lines: Any) -> list[dict[str, Any]]:
    out = []
    for ln in lines or []:
        name = _s(ln.get("item_name") or ln.get("name"))[:160]
        qty, rate = _f(ln.get("qty")), _f(ln.get("rate"))
        amt = _f(ln.get("amount")) or round(qty * rate, 2)
        if name or qty or amt:
            out.append({"item_name": name, "qty": qty, "rate": rate, "amount": _r2(amt)})
    return out[:300]


def create_so(conn: sqlite3.Connection, ws: str, payload: dict) -> dict[str, Any]:
    ensure_schema(conn)
    so_no = _s(payload.get("so_number"))[:60]
    if not so_no:
        raise ValueError("Type the SO number")
    po = payload.get("po_txn_id")
    vendor_id = payload.get("vendor_id")
    if po:
        r = conn.execute("SELECT party_id, txn_type FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                         (ws, int(po))).fetchone()
        if not r or int(r[1] or 0) != PO:
            raise ValueError("That order was not found")
        vendor_id = vendor_id or r[0]
    if conn.execute("SELECT 1 FROM hop_dist_so WHERE workspace_id=? AND lower(so_number)=lower(?) AND deleted_at IS NULL",
                    (ws, so_no)).fetchone():
        raise ValueError(f"SO {so_no} is already saved")
    lines = _clean_lines(payload.get("lines"))
    amount = _f(payload.get("amount")) or sum(ln["amount"] for ln in lines)
    now = _now()
    cur = conn.execute(
        "INSERT INTO hop_dist_so (workspace_id, vendor_id, po_txn_id, so_number, so_date, amount, lines_json, notes, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ws, int(vendor_id) if vendor_id else None, int(po) if po else None, so_no, _s(payload.get("so_date"))[:10] or None,
         _r2(amount), json.dumps(lines, ensure_ascii=False), _s(payload.get("notes"))[:500] or None, now, now))
    conn.commit()
    return get_so(conn, ws, int(cur.lastrowid))


def get_so(conn: sqlite3.Connection, ws: str, so_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM hop_dist_so WHERE workspace_id=? AND id=? AND deleted_at IS NULL", (ws, int(so_id))).fetchone()
    if not r:
        raise LookupError("SO not found")
    d = _so_row(r)
    d["bills"] = [int(x[0]) for x in conn.execute(
        "SELECT bill_txn_id FROM hop_dist_so_bills WHERE workspace_id=? AND so_id=?", (ws, int(so_id)))]
    return d


def delete_so(conn: sqlite3.Connection, ws: str, so_id: int) -> None:
    get_so(conn, ws, so_id)
    conn.execute("UPDATE hop_dist_so SET deleted_at=? WHERE workspace_id=? AND id=?", (_now(), ws, int(so_id)))
    conn.execute("DELETE FROM hop_dist_so_bills WHERE workspace_id=? AND so_id=?", (ws, int(so_id)))
    conn.commit()


def link_bill(conn: sqlite3.Connection, ws: str, so_id: int, bill_txn_id: int) -> dict[str, Any]:
    """The company's invoice (CI, saved as a Purchase Bill) for this SO."""
    get_so(conn, ws, so_id)
    r = conn.execute("SELECT txn_type FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(bill_txn_id))).fetchone()
    if not r or int(r[0] or 0) != BILL:
        raise ValueError("Pick a purchase bill (the company's invoice)")
    conn.execute("INSERT OR IGNORE INTO hop_dist_so_bills (workspace_id, so_id, bill_txn_id, created_at) VALUES (?,?,?,?)",
                 (ws, int(so_id), int(bill_txn_id), _now()))
    conn.commit()
    return get_so(conn, ws, so_id)


def _txn_list(conn, ws, sql: str, args: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _qty_of(conn, ws, txn_ids: list[int]) -> dict[int, float]:
    if not txn_ids:
        return {}
    q = f"""SELECT t.id, SUM(ABS(COALESCE(l.qty,0))) FROM hop_party_transactions t
            JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
            WHERE t.workspace_id=? AND t.id IN ({_in_ids(txn_ids)}) GROUP BY t.id"""
    return {int(r[0]): _f(r[1]) for r in conn.execute(q, (ws,))}


def pipeline(conn: sqlite3.Connection, ws: str, vendor_id: int | None = None) -> dict[str, Any]:
    """My orders to the company → SO → CI (purchase bill), with a status for each order."""
    ensure_schema(conn)
    pids = [int(vendor_id)] if vendor_id else _principal_ids(conn, ws)
    vend = f"AND t.party_type='vendor' AND t.party_id IN ({_in_ids(pids)})" if pids else "AND t.party_type='vendor'"
    orders = _txn_list(conn, ws, f"""SELECT t.id, t.txn_number, t.txn_date, t.total_amount, t.party_id, t.party_name
        FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type={PO} AND {LIVE} {vend}
        ORDER BY date(t.txn_date) DESC, t.id DESC""", (ws,))
    bills = _txn_list(conn, ws, f"""SELECT t.id, t.txn_number, t.txn_date, t.total_amount, t.balance_amount, t.party_id,
        t.party_bill_no, t.linked_from_txn_id FROM hop_party_transactions t
        WHERE t.workspace_id=? AND t.txn_type={BILL} AND {LIVE} {vend} ORDER BY date(t.txn_date), t.id""", (ws,))
    sos = [_so_row(r) for r in conn.execute(
        "SELECT * FROM hop_dist_so WHERE workspace_id=? AND deleted_at IS NULL ORDER BY date(so_date), id", (ws,))]
    if pids:
        sos = [s for s in sos if not s.get("vendor_id") or int(s["vendor_id"]) in pids]
    so_bills: dict[int, list[int]] = {}
    for r in conn.execute("SELECT so_id, bill_txn_id FROM hop_dist_so_bills WHERE workspace_id=?", (ws,)):
        so_bills.setdefault(int(r[0]), []).append(int(r[1]))
    bill_by_id = {int(b["id"]): b for b in bills}
    qty = _qty_of(conn, ws, [int(o["id"]) for o in orders] + list(bill_by_id))
    used_bills: set[int] = set()
    out = []
    for o in orders:
        oid = int(o["id"])
        my_sos = [s for s in sos if s.get("po_txn_id") and int(s["po_txn_id"]) == oid]
        b_ids = {int(b["id"]) for b in bills if b.get("linked_from_txn_id") and int(b["linked_from_txn_id"]) == oid}
        for s in my_sos:
            b_ids.update(so_bills.get(int(s["id"]), []))
        my_bills = [bill_by_id[i] for i in sorted(b_ids) if i in bill_by_id]
        used_bills.update(b_ids)
        ordered = _f(o["total_amount"])
        billed = sum(_f(b["total_amount"]) for b in my_bills)
        o_qty = qty.get(oid, 0.0)
        b_qty = sum(qty.get(int(b["id"]), 0.0) for b in my_bills)
        if my_bills:
            status = "Billed" if (b_qty >= o_qty - 1e-6 if o_qty else billed >= ordered - 1) else "Part billed"
        elif my_sos:
            status = "SO received"
        else:
            status = "Ordered"
        out.append({**o, "status": status, "ordered_qty": _r2(o_qty), "billed_qty": _r2(b_qty),
                    "billed_amount": _r2(billed), "pending_amount": _r2(max(0.0, ordered - billed)),
                    "sos": [{"id": s["id"], "so_number": s["so_number"], "so_date": s.get("so_date"), "amount": s.get("amount")}
                            for s in my_sos],
                    "bills": [{"id": b["id"], "txn_number": b["txn_number"], "party_bill_no": b.get("party_bill_no"),
                               "txn_date": b["txn_date"], "amount": b["total_amount"], "balance": b["balance_amount"]}
                              for b in my_bills],
                    "days_waiting": (_today() - date.fromisoformat(str(o["txn_date"])[:10])).days
                    if status in ("Ordered", "SO received") and o.get("txn_date") else None})
    loose_sos = [s for s in sos if not s.get("po_txn_id")]
    for s in loose_sos:
        used_bills.update(so_bills.get(int(s["id"]), []))
    counts: dict[str, int] = {}
    for o in out:
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    return {"principals": principals(conn, ws), "orders": out, "counts": counts,
            "sos_without_order": [{**s, "bills": so_bills.get(int(s["id"]), [])} for s in loose_sos],
            "bills_without_order": [b for b in bills if int(b["id"]) not in used_bills][-50:]}


# ── company ledger + payment status ──────────────────────────────────────────────────────────────────────
def company_ledger(conn: sqlite3.Connection, ws: str, vendor_id: int, frm: str = "", to: str = "") -> dict[str, Any]:
    from app import hop_reports

    st = hop_reports.party_statement(conn, ws, "vendor", int(vendor_id), frm, to)
    today = _today().isoformat()
    bills = _txn_list(conn, ws, f"""SELECT t.id, t.txn_number, t.party_bill_no, t.txn_date, t.due_date, t.total_amount,
        t.balance_amount FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type={BILL} AND {LIVE}
        AND t.party_type='vendor' AND t.party_id=? ORDER BY date(t.txn_date), t.id""", (ws, int(vendor_id)))
    open_bills = [b for b in bills if _f(b["balance_amount"]) > 0.009]
    for b in open_bills:
        due = _s(b.get("due_date"))[:10]
        b["overdue_days"] = (_today() - date.fromisoformat(due)).days if due and due < today else 0
    pays = _txn_list(conn, ws, f"""SELECT t.id, t.txn_number, t.txn_date, t.total_amount, t.payment_ref FROM hop_party_transactions t
        WHERE t.workspace_id=? AND t.txn_type={PAY_OUT} AND {LIVE} AND t.party_type='vendor' AND t.party_id=?
        ORDER BY date(t.txn_date) DESC, t.id DESC""", (ws, int(vendor_id)))
    billed = sum(_f(b["total_amount"]) for b in bills)
    paid = sum(_f(p["total_amount"]) for p in pays)
    owe = sum(_f(b["balance_amount"]) for b in open_bills)
    overdue = sum(_f(b["balance_amount"]) for b in open_bills if b["overdue_days"] > 0)
    return {**st, "summary": {"billed": _r2(billed), "paid": _r2(paid), "to_pay": _r2(owe), "overdue": _r2(overdue),
                              "open_bills": len(open_bills), "last_payment": pays[0] if pays else None},
            "open_bills": open_bills, "payments": pays[:30]}


# ── retailers: which retailer got what ───────────────────────────────────────────────────────────────────
def retailer_goods(conn: sqlite3.Connection, ws: str, frm: str = "", to: str = "", party_id: int | None = None,
                   q: str = "") -> dict[str, Any]:
    args: list[Any] = [ws]
    where = f"t.workspace_id=? AND t.txn_type IN ({SALE},{CHALLAN}) AND {LIVE} AND t.party_type='customer'"
    if frm:
        where += " AND date(t.txn_date) >= date(?)"; args.append(frm)
    if to:
        where += " AND date(t.txn_date) <= date(?)"; args.append(to)
    if party_id:
        where += " AND t.party_id=?"; args.append(int(party_id))
    if q:
        where += " AND (lower(COALESCE(l.item_name,'')) LIKE ? OR lower(COALESCE(t.party_name,'')) LIKE ?)"
        args += [f"%{q.lower()}%", f"%{q.lower()}%"]
    rows = conn.execute(f"""SELECT t.party_id, COALESCE(c.company, t.party_name, '') AS party, t.id AS txn_id, t.txn_type,
               t.txn_number, t.txn_date, l.item_name, l.unit, ABS(COALESCE(l.qty,0)) AS qty, COALESCE(l.line_total,0) AS amount
        FROM hop_party_transactions t
        JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
        LEFT JOIN hop_customers c ON c.workspace_id=t.workspace_id AND c.id=t.party_id
        WHERE {where} ORDER BY date(t.txn_date) DESC, t.id DESC""", args).fetchall()
    by: dict[int, dict[str, Any]] = {}
    for r in rows:
        r = dict(r)
        pid = int(r["party_id"] or 0)
        p = by.setdefault(pid, {"party_id": pid, "party": r["party"], "qty": 0.0, "amount": 0.0, "bills": set(),
                                "last_date": r["txn_date"], "items": {}})
        p["qty"] += _f(r["qty"])
        p["amount"] += _f(r["amount"]) if r["txn_type"] == SALE else 0.0   # a challan carries goods, not money
        p["bills"].add(r["txn_id"])
        it = p["items"].setdefault((r["item_name"] or "").strip() or "Item",
                                   {"item": (r["item_name"] or "").strip() or "Item", "unit": r.get("unit"), "qty": 0.0,
                                    "amount": 0.0, "last_date": r["txn_date"], "last_bill": r["txn_number"], "last_txn_id": r["txn_id"]})
        it["qty"] += _f(r["qty"])
        it["amount"] += _f(r["amount"]) if r["txn_type"] == SALE else 0.0
    out = []
    for p in by.values():
        items = sorted(p["items"].values(), key=lambda i: -i["qty"])
        for i in items:
            i["qty"], i["amount"] = _r2(i["qty"]), _r2(i["amount"])
        out.append({**p, "qty": _r2(p["qty"]), "amount": _r2(p["amount"]), "bills": len(p["bills"]), "items": items})
    out.sort(key=lambda p: -p["amount"])
    return {"from": frm, "to": to, "retailers": out, "total_qty": _r2(sum(p["qty"] for p in out)),
            "total_amount": _r2(sum(p["amount"] for p in out))}


# ── retailer forecast: what each retailer buys most and when they will need it again ──────────────────────
def retailer_forecast(conn: sqlite3.Connection, ws: str, lookback_days: int = 180, party_id: int | None = None,
                      soon_days: int = 7) -> dict[str, Any]:
    """Per retailer: top items, how often they buy each (every N days), usual qty per order, last bought, the next
    order date + qty expected from their own rhythm, and whether it is due now / soon. Rules on the bills (no AI
    cost); ✨ AI insights / Ask Nexora explain it. A challan counts as goods sent."""
    since = (_today() - timedelta(days=max(14, int(lookback_days)))).isoformat()
    args: list[Any] = [ws, since]
    extra = ""
    if party_id:
        extra = " AND t.party_id=?"; args.append(int(party_id))
    rows = conn.execute(f"""SELECT t.party_id, COALESCE(c.company, t.party_name, '') AS party, c.mobile AS phone,
               date(t.txn_date) AS d, lower(trim(COALESCE(l.item_name,''))) AS k, l.item_name, l.unit,
               SUM(ABS(COALESCE(l.qty,0))) AS qty, SUM(CASE WHEN t.txn_type={SALE} THEN COALESCE(l.line_total,0) ELSE 0 END) AS amount
        FROM hop_party_transactions t
        JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
        LEFT JOIN hop_customers c ON c.workspace_id=t.workspace_id AND c.id=t.party_id
        WHERE t.workspace_id=? AND t.txn_type IN ({SALE},{CHALLAN}) AND {LIVE} AND t.party_type='customer'
          AND date(t.txn_date) >= date(?){extra}
        GROUP BY t.party_id, date(t.txn_date), lower(trim(COALESCE(l.item_name,'')))
        ORDER BY date(t.txn_date)""", args).fetchall()
    today = _today()
    by: dict[int, dict[str, Any]] = {}
    for r in rows:
        r = dict(r)
        pid = int(r["party_id"] or 0)
        p = by.setdefault(pid, {"party_id": pid, "party": r["party"], "phone": r.get("phone"), "amount": 0.0, "items": {}})
        p["amount"] += _f(r["amount"])
        it = p["items"].setdefault(r["k"] or "item", {"item": (r["item_name"] or "").strip() or "Item", "unit": r.get("unit"),
                                                     "dates": [], "qtys": [], "amount": 0.0})
        it["dates"].append(r["d"]); it["qtys"].append(_f(r["qty"])); it["amount"] += _f(r["amount"])
    out, due_now, due_soon = [], [], []
    for p in by.values():
        items = []
        for it in p["items"].values():
            ds = [date.fromisoformat(d) for d in it["dates"]]
            gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
            every = round(sum(gaps) / len(gaps)) if gaps else None
            per_order = _r2(sum(it["qtys"]) / len(it["qtys"]))
            last = ds[-1]
            nxt = last + timedelta(days=every) if every else None
            late = (today - nxt).days if nxt else None
            status = ("due_now" if late is not None and late >= 0 else
                      "due_soon" if late is not None and late >= -soon_days else "ok" if every else "once")
            row = {"item": it["item"], "unit": it["unit"], "orders": len(ds), "total_qty": _r2(sum(it["qtys"])),
                   "amount": _r2(it["amount"]), "every_days": every, "usual_qty": per_order, "last_date": last.isoformat(),
                   "next_date": nxt.isoformat() if nxt else None, "days_late": late if late and late > 0 else 0,
                   "status": status}
            items.append(row)
            if status in ("due_now", "due_soon"):
                (due_now if status == "due_now" else due_soon).append({"party_id": p["party_id"], "party": p["party"],
                                                                      "phone": p.get("phone"), **row})
        items.sort(key=lambda i: (-i["total_qty"], i["item"]))
        regular = [i for i in items if i["every_days"]]
        out.append({"party_id": p["party_id"], "party": p["party"], "phone": p.get("phone"), "amount": _r2(p["amount"]),
                    "top_items": items[:8], "item_count": len(items),
                    "due_now": sum(1 for i in items if i["status"] == "due_now"),
                    "due_soon": sum(1 for i in items if i["status"] == "due_soon"),
                    "buys_every_days": round(sum(i["every_days"] for i in regular) / len(regular)) if regular else None})
    out.sort(key=lambda p: (-(p["due_now"] * 1000 + p["due_soon"]), -p["amount"]))
    due_now.sort(key=lambda r: -r["days_late"])
    due_soon.sort(key=lambda r: r["next_date"] or "")
    return {"lookback_days": int(lookback_days), "retailers": out, "due_now": due_now[:60], "due_soon": due_soon[:60],
            "note": "From each retailer's own buying rhythm (sale bills + challans). An item bought only once has no rhythm yet."}


# ── forecast: what to order from the company, and when ───────────────────────────────────────────────────
def _sold_by_item(conn, ws, since: str, until: str | None = None) -> dict[str, float]:
    args: list[Any] = [ws, since]
    extra = ""
    if until:
        extra = " AND date(t.txn_date) < date(?)"; args.append(until)
    out: dict[str, float] = {}
    for r in conn.execute(f"""SELECT l.product_id, lower(trim(COALESCE(l.item_name,''))), t.txn_type, SUM(ABS(COALESCE(l.qty,0)))
            FROM hop_party_transactions t
            JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
            WHERE t.workspace_id=? AND t.txn_type IN ({SALE},{CHALLAN},{SALE_RETURN}) AND {LIVE}
              AND date(t.txn_date) >= date(?){extra}
            GROUP BY l.product_id, lower(trim(COALESCE(l.item_name,''))), t.txn_type""", args):
        key = f"p{int(r[0])}" if r[0] else f"n{r[1]}"
        out[key] = out.get(key, 0.0) + (-_f(r[3]) if int(r[2]) == SALE_RETURN else _f(r[3]))
    return out


def _on_order(conn, ws) -> dict[str, float]:
    """Qty in my open orders to the company (orders not yet fully billed)."""
    pl = pipeline(conn, ws)
    open_ids = [int(o["id"]) for o in pl["orders"] if o["status"] in ("Ordered", "SO received", "Part billed")]
    if not open_ids:
        return {}
    part = {int(o["id"]): (o["billed_qty"] / o["ordered_qty"] if o["ordered_qty"] else 0.0)
            for o in pl["orders"] if o["status"] == "Part billed"}
    out: dict[str, float] = {}
    for r in conn.execute(f"""SELECT t.id, l.product_id, lower(trim(COALESCE(l.item_name,''))), ABS(COALESCE(l.qty,0))
            FROM hop_party_transactions t
            JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
            WHERE t.workspace_id=? AND t.id IN ({_in_ids(open_ids)})""", (ws,)):
        key = f"p{int(r[1])}" if r[1] else f"n{r[2]}"
        out[key] = out.get(key, 0.0) + _f(r[3]) * (1 - part.get(int(r[0]), 0.0))
    return out


def forecast(conn: sqlite3.Connection, ws: str, lookback_days: int = 90, cover_days: int = 30) -> dict[str, Any]:
    lookback_days = max(30, min(int(lookback_days or 90), 365))
    cover_days = max(7, min(int(cover_days or 30), 120))
    today = _today()
    since = (today - timedelta(days=lookback_days)).isoformat()
    last30 = _sold_by_item(conn, ws, (today - timedelta(days=30)).isoformat())
    prev30 = _sold_by_item(conn, ws, (today - timedelta(days=60)).isoformat(), (today - timedelta(days=30)).isoformat())
    sold = _sold_by_item(conn, ws, since)
    on_order = _on_order(conn, ws)
    plead = {int(p["id"]): p.get("lead_time_days") for p in principals(conn, ws)}
    default_lead = next((int(v) for v in plead.values() if v), 7)
    items = []
    for p in conn.execute("""SELECT p.id, p.name, p.code, p.category, p.unit, p.stock_qty, p.purchase_price, p.lead_time_days,
                                    v.lead_time_days AS v_lead
                             FROM hop_products p LEFT JOIN hop_vendors v ON v.workspace_id=p.workspace_id AND v.id=p.vendor_id
                             WHERE p.workspace_id=? AND COALESCE(p.status,'active')='active'""", (ws,)).fetchall():
        p = dict(p)
        keys = [f"p{int(p['id'])}", f"n{(p['name'] or '').strip().lower()}"]
        s = sum(sold.get(k, 0.0) for k in keys)
        l30 = sum(last30.get(k, 0.0) for k in keys)
        p30 = sum(prev30.get(k, 0.0) for k in keys)
        oo = sum(on_order.get(k, 0.0) for k in keys)
        stock = _f(p.get("stock_qty"))
        per_day = s / lookback_days
        lead = int(p.get("lead_time_days") or p.get("v_lead") or default_lead)
        days_left = (stock / per_day) if per_day > 0 else None
        need = per_day * (lead + cover_days) - stock - oo
        order_qty = max(0, math.ceil(need)) if per_day > 0 else 0
        if per_day <= 0:
            flag = "No sale" if stock > 0 else "—"
        elif days_left is not None and days_left <= lead:
            flag = "Order now"
        elif days_left is not None and days_left <= lead + 7:
            flag = "Order soon"
        else:
            flag = "OK"
        order_by = (today + timedelta(days=max(0, int((days_left or 0) - lead)))).isoformat() if per_day > 0 else None
        trend = round((l30 - p30) / p30 * 100) if p30 > 0 else (100 if l30 > 0 else 0)
        if s <= 0 and stock <= 0 and oo <= 0:
            continue
        items.append({"id": p["id"], "item": p["name"], "code": p.get("code"), "category": p.get("category"),
                      "unit": p.get("unit") or "Pcs", "stock": _r2(stock), "on_order": _r2(oo),
                      "sold": _r2(s), "per_day": round(per_day, 2), "last30": _r2(l30), "prev30": _r2(p30),
                      "trend_pct": trend, "days_left": None if days_left is None else round(days_left),
                      "lead_days": lead, "order_qty": order_qty, "order_by": order_by, "flag": flag,
                      "order_value": _r2(order_qty * _f(p.get("purchase_price")))})
    rank = {"Order now": 0, "Order soon": 1, "OK": 2, "No sale": 3, "—": 4}
    items.sort(key=lambda i: (rank.get(i["flag"], 9), i["days_left"] if i["days_left"] is not None else 99999))
    return {"lookback_days": lookback_days, "cover_days": cover_days, "items": items,
            "order_now": sum(1 for i in items if i["flag"] == "Order now"),
            "order_soon": sum(1 for i in items if i["flag"] == "Order soon"),
            "no_sale": sum(1 for i in items if i["flag"] == "No sale"),
            "suggested_value": _r2(sum(i["order_value"] for i in items if i["flag"] in ("Order now", "Order soon")))}


def warehouse(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    from app import hop_reports

    st = hop_reports.stock_summary(conn, ws)
    rows = st["rows"]
    try:
        from app import hop_godowns

        gd = hop_godowns.stock_by_godown(conn, ws)
    except Exception:
        gd = []
    return {"items": rows, "total_value": st["total_value"], "count": len(rows),
            "out_of_stock": sum(1 for r in rows if _f(r["qty"]) <= 0),
            "total_qty": _r2(sum(max(_f(r["qty"]), 0) for r in rows)), "godowns": gd}


def home(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    ensure_schema(conn)
    pl = pipeline(conn, ws)
    pr = principals(conn, ws)
    owe = 0.0
    overdue = 0.0
    if pr:
        for p in pr:
            s = company_ledger(conn, ws, int(p["id"]))["summary"]
            owe += s["to_pay"]
            overdue += s["overdue"]
    m0 = _today().replace(day=1).isoformat()
    ci_month = conn.execute(f"""SELECT COALESCE(SUM(t.total_amount),0) FROM hop_party_transactions t WHERE t.workspace_id=?
        AND t.txn_type={BILL} AND {LIVE} AND t.party_type='vendor' AND t.party_id IN ({_in_ids([int(p['id']) for p in pr])})
        AND date(t.txn_date) >= date(?)""", (ws, m0)).fetchone()[0] if pr else 0
    sales_month = conn.execute(f"""SELECT COALESCE(SUM(t.total_amount),0) FROM hop_party_transactions t WHERE t.workspace_id=?
        AND t.txn_type={SALE} AND {LIVE} AND date(t.txn_date) >= date(?)""", (ws, m0)).fetchone()[0]
    recv = conn.execute(f"""SELECT COALESCE(SUM(t.balance_amount),0) FROM hop_party_transactions t WHERE t.workspace_id=?
        AND t.txn_type={SALE} AND {LIVE}""", (ws,)).fetchone()[0]
    fc = forecast(conn, ws)
    rg = retailer_goods(conn, ws, m0)
    wh = warehouse(conn, ws)
    waiting = [o for o in pl["orders"] if o["status"] in ("Ordered", "SO received")]
    return {"principals": pr, "orders_open": len(waiting),
            "orders_waiting_so": sum(1 for o in waiting if o["status"] == "Ordered"),
            "so_waiting_ci": sum(1 for o in waiting if o["status"] == "SO received"),
            "oldest_wait_days": max([o["days_waiting"] or 0 for o in waiting], default=0),
            "ci_this_month": _r2(_f(ci_month)), "to_pay_company": _r2(owe), "overdue_company": _r2(overdue),
            "sales_this_month": _r2(_f(sales_month)), "retailer_outstanding": _r2(_f(recv)),
            "stock_value": wh["total_value"], "out_of_stock": wh["out_of_stock"],
            "order_now": fc["order_now"], "order_soon": fc["order_soon"], "suggested_value": fc["suggested_value"],
            "top_retailers": [{"party": r["party"], "party_id": r["party_id"], "amount": r["amount"], "qty": r["qty"]}
                              for r in rg["retailers"][:5]],
            "alerts": _alerts(pl, fc, owe, overdue, pr)}


def _alerts(pl, fc, owe, overdue, pr) -> list[str]:
    a = []
    if not pr:
        a.append("Mark the company you buy from: Orders → SO → CI → Set company. Then its orders, SO, CI and ledger show here.")
    late = [o for o in pl["orders"] if o["status"] == "Ordered" and (o["days_waiting"] or 0) >= 7]
    if late:
        a.append(f"{len(late)} order(s) waiting 7+ days for the company's SO — oldest {late[-1]['txn_number']}.")
    so_wait = [o for o in pl["orders"] if o["status"] == "SO received" and (o["days_waiting"] or 0) >= 10]
    if so_wait:
        a.append(f"{len(so_wait)} SO(s) not billed yet (no CI) after 10+ days.")
    if overdue > 0:
        a.append(f"₹{overdue:,.0f} overdue to the company — pay to keep your credit.")
    if fc["order_now"]:
        a.append(f"{fc['order_now']} item(s) will run out before a new order can arrive — order now.")
    if fc["no_sale"]:
        a.append(f"{fc['no_sale']} item(s) in stock with no sale in {fc['lookback_days']} days — push or return them.")
    return a
