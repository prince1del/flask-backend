"""Distributor: one desk per company (brand) — owner 20-11: "chaar brand ke liye distribution karta hai … kaha unki saari
details fill karega, margins, article master link, SO/PO tally, CI / bills, payments, shipment tracking, kya aagaya kya
rehgaya, kya billing huyi kya pending hai — neat and clean".

``brand_desk`` puts it on one page for one company:
  - setup checklist (terms, price rule, articles linked, first order …) that says where to fill what;
  - orders → SO → CI counts, what is waiting;
  - **pending items**: per article, qty on the company's SOs vs qty received on its invoices (CI) → still to come;
  - shipments: LR / transporter / dispatch date / expected date per company invoice, "in transit" until marked received;
  - money: billed, paid, to pay, overdue, last payment.
Nothing here saves on its own; shipments are typed by the user.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

BILL, PO = 2, 66
LIVE = "(t.deleted_at IS NULL OR trim(t.deleted_at)='')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def _key(name: Any) -> str:
    return " ".join(_s(name).lower().split())


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_dist_terms as T

    T.ensure_schema(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_shipments (
               workspace_id TEXT NOT NULL,
               bill_txn_id INTEGER NOT NULL,
               lr_no TEXT, transporter TEXT, vehicle_no TEXT,
               dispatched_on TEXT, eta TEXT, received_on TEXT,
               cases REAL, note TEXT, updated_at TEXT NOT NULL,
               PRIMARY KEY (workspace_id, bill_txn_id)
           )""")


def _company(conn, ws, vid: int) -> dict[str, Any]:
    from app import hop_dist_terms as T

    c = next((x for x in T.overview(conn, ws)["companies"] if int(x["id"]) == int(vid)), None)
    if not c:
        raise LookupError("Company not found — tick it in Orders → SO → CI → Set company")
    return c


def pending_items(conn: sqlite3.Connection, ws: str, vid: int) -> list[dict[str, Any]]:
    """Per article: qty on this company's SOs, qty received on the invoices linked to them, still to come."""
    from app import hop_distributor as D

    D.ensure_schema(conn)
    sos = [dict(r) for r in conn.execute(
        "SELECT id, so_number, so_date, lines_json FROM hop_dist_so WHERE workspace_id=? AND deleted_at IS NULL "
        "AND (vendor_id=? OR vendor_id IS NULL)", (ws, int(vid)))]
    links: dict[int, list[int]] = {}
    for so_id, bill in conn.execute("SELECT so_id, bill_txn_id FROM hop_dist_so_bills WHERE workspace_id=?", (ws,)):
        links.setdefault(int(so_id), []).append(int(bill))
    items: dict[str, dict[str, Any]] = {}
    for so in sos:
        for ln in json.loads(so.get("lines_json") or "[]"):
            k = _key(ln.get("item_name"))
            if not k:
                continue
            it = items.setdefault(k, {"item": _s(ln.get("item_name")), "on_so": 0.0, "received": 0.0, "rate": _f(ln.get("rate")),
                                      "sos": []})
            it["on_so"] += _f(ln.get("qty"))
            if so["so_number"] not in it["sos"]:
                it["sos"].append(so["so_number"])
        bills = links.get(int(so["id"]), [])
        if bills:
            q = ",".join(str(int(b)) for b in bills)
            for name, qty in conn.execute(
                    f"""SELECT COALESCE(p.name, l.item_name), SUM(ABS(COALESCE(l.qty,0)))
                        FROM hop_party_transactions t
                        JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
                        LEFT JOIN hop_products p ON p.workspace_id=t.workspace_id AND p.id=l.product_id
                        WHERE t.workspace_id=? AND t.id IN ({q}) AND {LIVE} GROUP BY 1""", (ws,)):
                k = _key(name)
                if k in items:
                    items[k]["received"] += _f(qty)
    out = []
    for it in items.values():
        left = max(0.0, it["on_so"] - it["received"])
        if left > 1e-6:
            out.append({**it, "on_so": round(it["on_so"], 2), "received": round(it["received"], 2),
                        "pending": round(left, 2), "pending_value": round(left * it["rate"], 2), "sos": it["sos"][:5]})
    return sorted(out, key=lambda x: -x["pending_value"])


def shipments(conn: sqlite3.Connection, ws: str, vid: int) -> dict[str, Any]:
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        f"""SELECT t.id AS bill_txn_id, t.txn_number, t.party_bill_no, t.txn_date, t.total_amount,
                   s.lr_no, s.transporter, s.vehicle_no, s.dispatched_on, s.eta, s.received_on, s.cases, s.note
            FROM hop_party_transactions t LEFT JOIN hop_dist_shipments s
              ON s.workspace_id=t.workspace_id AND s.bill_txn_id=t.id
            WHERE t.workspace_id=? AND t.txn_type={BILL} AND {LIVE} AND t.party_type='vendor' AND t.party_id=?
            ORDER BY date(t.txn_date) DESC, t.id DESC LIMIT 200""", (ws, int(vid)))]
    today = _today().isoformat()
    for r in rows:
        r["state"] = ("received" if r.get("received_on") else "in_transit" if (r.get("lr_no") or r.get("dispatched_on"))
                      else "no_info")
        r["late"] = bool(r["state"] == "in_transit" and r.get("eta") and str(r["eta"])[:10] < today)
    return {"in_transit": [r for r in rows if r["state"] == "in_transit"],
            "no_info": [r for r in rows if r["state"] == "no_info"][:30],
            "received": [r for r in rows if r["state"] == "received"][:20],
            "late": sum(1 for r in rows if r["late"])}


def save_shipment(conn: sqlite3.Connection, ws: str, bill_txn_id: int, p: dict) -> dict[str, Any]:
    """LR / transporter / vehicle / dispatch + expected date / received date for one company invoice."""
    ensure_schema(conn)
    r = conn.execute(f"SELECT party_id FROM hop_party_transactions t WHERE t.workspace_id=? AND t.id=? AND t.txn_type={BILL} "
                     f"AND {LIVE}", (ws, int(bill_txn_id))).fetchone()
    if not r:
        raise LookupError("Company invoice not found")
    cur = conn.execute("SELECT * FROM hop_dist_shipments WHERE workspace_id=? AND bill_txn_id=?",
                       (ws, int(bill_txn_id))).fetchone()
    row = dict(cur) if cur else {}
    for k in ("lr_no", "transporter", "vehicle_no", "note"):
        if k in p:
            row[k] = _s(p.get(k))[:120] or None
    for k in ("dispatched_on", "eta", "received_on"):
        if k in p:
            v = _s(p.get(k))[:10]
            if v:
                try:
                    date.fromisoformat(v)
                except ValueError as exc:
                    raise ValueError(f"{k.replace('_', ' ')}: use YYYY-MM-DD") from exc
            row[k] = v or None
    if "cases" in p:
        row["cases"] = _f(p.get("cases")) or None
    conn.execute(
        "INSERT OR REPLACE INTO hop_dist_shipments (workspace_id, bill_txn_id, lr_no, transporter, vehicle_no, dispatched_on, "
        "eta, received_on, cases, note, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ws, int(bill_txn_id), row.get("lr_no"), row.get("transporter"), row.get("vehicle_no"), row.get("dispatched_on"),
         row.get("eta"), row.get("received_on"), row.get("cases"), row.get("note"), _now()))
    conn.commit()
    return shipments(conn, ws, int(r[0]))


def brand_desk(conn: sqlite3.Connection, ws: str, vid: int) -> dict[str, Any]:
    from app import hop_distributor as D

    ensure_schema(conn)
    c = _company(conn, ws, vid)
    pipe = D.pipeline(conn, ws, int(vid))
    led = D.company_ledger(conn, ws, int(vid))
    pend = pending_items(conn, ws, int(vid))
    ship = shipments(conn, ws, int(vid))
    orders = pipe["orders"]
    so_wait = sum(1 for o in orders if o["status"] == "SO received") + sum(1 for s in pipe["sos_without_order"] if not s["bills"])
    steps = [
        {"key": "terms", "label": "Payment terms and credit days", "done": bool(c.get("payment_terms") or c.get("credit_days") is not None),
         "where": "Companies & terms → tap the company"},
        {"key": "rule", "label": "Price rule (mark-up / margin / mark-down)", "done": bool(c.get("rule")),
         "where": "Companies & terms → tap the company"},
        {"key": "articles", "label": "Articles of this company", "done": int(c.get("articles") or 0) > 0,
         "where": "Article Master → ✨ Read company price list (pick this company), or Select → Set company"},
        {"key": "order", "label": "First order to the company", "done": bool(orders),
         "where": "Orders → SO → CI → + New order"},
        {"key": "so", "label": "Company's SO", "done": bool(any(o["sos"] for o in orders) or pipe["sos_without_order"]),
         "where": "Orders → + SO received, or Bulk import → SO"},
        {"key": "ci", "label": "Company's invoices (CI)", "done": bool(led["summary"]["billed"]),
         "where": "Purchase Bills, or Bulk import → CI"},
    ]
    return {"company": c, "steps": steps, "setup_done": sum(1 for s in steps if s["done"]), "setup_total": len(steps),
            "orders": {"open": sum(1 for o in orders if o["status"] in ("Ordered", "SO received", "Part billed")),
                       "waiting_so": sum(1 for o in orders if o["status"] == "Ordered"),
                       "waiting_ci": so_wait, "part_billed": sum(1 for o in orders if o["status"] == "Part billed"),
                       "not_billed_amount": round(sum(_f(o["pending_amount"]) for o in orders), 2),
                       "latest": orders[:8]},
            "pending_items": pend[:40], "pending_value": round(sum(p["pending_value"] for p in pend), 2),
            "pending_qty": round(sum(p["pending"] for p in pend), 2),
            "shipments": ship, "money": led["summary"], "open_bills": led["open_bills"][:10]}


def desks(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """Every company in one short list (for the chips + Ask Nexora)."""
    from app import hop_dist_terms as T

    ensure_schema(conn)
    out = []
    for c in T.overview(conn, ws)["companies"]:
        d = brand_desk(conn, ws, int(c["id"]))
        out.append({"id": c["id"], "company": c["company"], "setup": f"{d['setup_done']}/{d['setup_total']}",
                    "open_orders": d["orders"]["open"], "waiting_ci": d["orders"]["waiting_ci"],
                    "pending_value": d["pending_value"], "in_transit": len(d["shipments"]["in_transit"]),
                    "late": d["shipments"]["late"], "to_pay": d["money"]["to_pay"], "overdue": d["money"]["overdue"]})
    # GSTIN, city and contact for the brand cards (owner 21-11: one card per brand, tap for details)
    if out:
        ids = [int(x["id"]) for x in out]
        rows = conn.execute(
            f"SELECT id, gst_no, city, state, contact_person, mobile FROM hop_vendors WHERE workspace_id=? "
            f"AND id IN ({','.join('?' * len(ids))})", [ws, *ids]).fetchall()
        extra = {int(r[0]): r for r in rows}
        for x in out:
            r = extra.get(int(x["id"]))
            if r:
                x.update({"gst_no": r[1] or "", "city": r[2] or r[3] or "", "contact_person": r[4] or "", "mobile": r[5] or ""})
    return {"companies": out}
