"""More HoP reports (Phase 4, to match the full feature set of desktop billing software).

Party-wise P&L, Item-wise P&L, Party report by item, Sale/Purchase by party group,
Item category report, Discount report, Expense item report, Sale/Purchase order report,
GSTR-9 (annual summary). All read documents only; deleted (recycle bin) ones are skipped.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

LIVE = "(t.deleted_at IS NULL OR trim(t.deleted_at)='')"


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _range(frm: str, to: str, col: str = "t.txn_date") -> tuple[str, list[Any]]:
    sql, p = "", []
    if frm:
        sql += f" AND date({col}) >= date(?)"
        p.append(frm)
    if to:
        sql += f" AND date({col}) <= date(?)"
        p.append(to)
    return sql, p


def _lines(conn, ws, types: tuple[int, ...], frm="", to="", party_id: int | None = None):
    rng, p = _range(frm, to)
    sql = f"""SELECT l.*, t.id AS txn_id, t.txn_type, t.txn_number, t.txn_date, t.party_name, t.party_id, t.party_type
              FROM hop_txn_lines l JOIN hop_party_transactions t
                ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
              WHERE l.workspace_id=? AND {LIVE} AND t.txn_type IN ({','.join('?' * len(types))}){rng}"""
    params: list[Any] = [ws, *types, *p]
    if party_id:
        sql += " AND t.party_id=?"
        params.append(int(party_id))
    return _rows(conn, sql, params)


def _costs(conn, ws):
    by_id, by_name, cat_id, cat_name = {}, {}, {}, {}
    for r in _rows(conn, "SELECT id, name, purchase_price, category FROM hop_products WHERE workspace_id=?", (ws,)):
        by_id[int(r["id"])] = _f(r["purchase_price"])
        by_name[str(r["name"] or "").strip().lower()] = _f(r["purchase_price"])
        cat_id[int(r["id"])] = r.get("category") or "Uncategorised"
        cat_name[str(r["name"] or "").strip().lower()] = r.get("category") or "Uncategorised"
    return by_id, by_name, cat_id, cat_name


def _taxable(ln) -> float:
    return _f(ln.get("line_total")) - _f(ln.get("tax_amount"))


def _cost_of(ln, by_id, by_name) -> float | None:
    price = by_id.get(int(ln["product_id"])) if ln.get("product_id") else by_name.get(str(ln.get("item_name") or "").strip().lower())
    return None if price is None or price <= 0 else _f(ln.get("qty")) * price


def party_pl(conn, ws, frm="", to="") -> dict[str, Any]:
    by_id, by_name, *_ = _costs(conn, ws)
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ln in _lines(conn, ws, (1, 21), frm, to):
        s = -1 if ln["txn_type"] == 21 else 1
        a = agg[ln["party_name"] or "—"]
        a["sale"] += s * _taxable(ln)
        c = _cost_of(ln, by_id, by_name)
        if c is None:
            a["no_cost"] += 1
        else:
            a["cost"] += s * c
    rows = [{"party": k, "sale": _r(v["sale"]), "cost": _r(v["cost"]), "profit": _r(v["sale"] - v["cost"]),
             "margin_pct": _r((v["sale"] - v["cost"]) / v["sale"] * 100) if v["sale"] else 0.0,
             "items_without_cost": int(v["no_cost"])} for k, v in agg.items()]
    rows.sort(key=lambda r: -r["profit"])
    return {"rows": rows, "total_sale": _r(sum(r["sale"] for r in rows)), "total_cost": _r(sum(r["cost"] for r in rows)),
            "total_profit": _r(sum(r["profit"] for r in rows)),
            "note": "Cost = qty × the item's purchase price; items with no purchase price count as 0 cost."}


def item_pl(conn, ws, frm="", to="") -> dict[str, Any]:
    by_id, by_name, *_ = _costs(conn, ws)
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ln in _lines(conn, ws, (1, 21), frm, to):
        s = -1 if ln["txn_type"] == 21 else 1
        a = agg[(ln.get("item_name") or "—").strip()]
        a["qty"] += s * _f(ln.get("qty"))
        a["sale"] += s * _taxable(ln)
        c = _cost_of(ln, by_id, by_name)
        a["cost"] += s * (c or 0.0)
        a["known"] += 1 if c is not None else 0
    rows = [{"item": k, "qty": _r(v["qty"]), "sale": _r(v["sale"]), "cost": _r(v["cost"]),
             "profit": _r(v["sale"] - v["cost"]), "cost_known": bool(v["known"])} for k, v in agg.items()]
    rows.sort(key=lambda r: -r["profit"])
    return {"rows": rows, "total_profit": _r(sum(r["profit"] for r in rows))}


def party_item_report(conn, ws, party_id: int, frm="", to="") -> dict[str, Any]:
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    party = None
    for ln in _lines(conn, ws, (1, 21, 2, 16), frm, to, party_id):
        party = ln["party_name"]
        k = {1: "sold", 21: "sold", 2: "bought", 16: "bought"}[ln["txn_type"]]
        s = -1 if ln["txn_type"] in (21, 16) else 1
        a = agg[(ln.get("item_name") or "—").strip()]
        a[f"{k}_qty"] += s * _f(ln.get("qty"))
        a[f"{k}_amount"] += s * _taxable(ln)
        a["last_rate"] = _f(ln.get("rate"))
    rows = [{"item": k, **{x: _r(v[x]) for x in ("sold_qty", "sold_amount", "bought_qty", "bought_amount", "last_rate")}}
            for k, v in agg.items()]
    rows.sort(key=lambda r: -(r["sold_amount"] + r["bought_amount"]))
    return {"party": party, "rows": rows}


def group_report(conn, ws, kind: str = "sale", frm="", to="") -> dict[str, Any]:
    types = (1, 21) if kind == "sale" else (2, 16)
    rng, p = _range(frm, to)
    table = "hop_customers" if kind == "sale" else "hop_vendors"
    gcol = "COALESCE(NULLIF(trim(c.customer_type),''),'No group')" if kind == "sale" else "'Supplier'"
    rows = _rows(conn, f"""SELECT {gcol} AS grp, t.txn_type, COUNT(*) AS bills, SUM(t.total_amount) AS amount
                           FROM hop_party_transactions t LEFT JOIN {table} c ON c.id=t.party_id AND c.workspace_id=t.workspace_id
                           WHERE t.workspace_id=? AND {LIVE} AND t.txn_type IN ({','.join('?' * len(types))}){rng}
                           GROUP BY grp, t.txn_type""", [ws, *types, *p])
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        s = -1 if r["txn_type"] in (21, 16) else 1
        agg[r["grp"]]["amount"] += s * _f(r["amount"])
        agg[r["grp"]]["bills"] += r["bills"] if s > 0 else 0
    out = [{"group": k, "bills": int(v["bills"]), "amount": _r(v["amount"])} for k, v in agg.items()]
    out.sort(key=lambda r: -r["amount"])
    return {"kind": kind, "rows": out, "total": _r(sum(r["amount"] for r in out))}


def category_report(conn, ws, frm="", to="") -> dict[str, Any]:
    *_, cat_id, cat_name = _costs(conn, ws)
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ln in _lines(conn, ws, (1, 21, 2, 16), frm, to):
        cat = cat_id.get(int(ln["product_id"])) if ln.get("product_id") else cat_name.get(str(ln.get("item_name") or "").strip().lower())
        cat = cat or "Not in catalogue"
        s = -1 if ln["txn_type"] in (21, 16) else 1
        k = "sale" if ln["txn_type"] in (1, 21) else "purchase"
        agg[cat][f"{k}_qty"] += s * _f(ln.get("qty"))
        agg[cat][f"{k}_amount"] += s * _taxable(ln)
    rows = [{"category": k, **{x: _r(v[x]) for x in ("sale_qty", "sale_amount", "purchase_qty", "purchase_amount")}}
            for k, v in agg.items()]
    rows.sort(key=lambda r: -r["sale_amount"])
    return {"rows": rows}


def discount_report(conn, ws, frm="", to="") -> dict[str, Any]:
    rng, p = _range(frm, to)
    docs = _rows(conn, f"""SELECT t.id, t.txn_type, t.txn_number, t.txn_date, t.party_name, t.total_amount,
                                  COALESCE(t.discount_amount,0) AS bill_disc,
                                  COALESCE((SELECT SUM(l.discount_amount) FROM hop_txn_lines l
                                            WHERE l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id),0) AS item_disc
                           FROM hop_party_transactions t
                           WHERE t.workspace_id=? AND {LIVE} AND t.txn_type IN (1,2){rng}""", [ws, *p])
    rows = []
    for d in docs:
        tot = _f(d["bill_disc"]) + _f(d["item_disc"])
        if tot > 0.004:
            rows.append({"id": d["id"], "date": (d["txn_date"] or "")[:10], "type": "Sale" if d["txn_type"] == 1 else "Purchase",
                         "number": d["txn_number"], "party": d["party_name"], "item_discount": _r(_f(d["item_disc"])),
                         "bill_discount": _r(_f(d["bill_disc"])), "total_discount": _r(tot), "total": _r(_f(d["total_amount"]))})
    given = sum(r["total_discount"] for r in rows if r["type"] == "Sale")
    got = sum(r["total_discount"] for r in rows if r["type"] == "Purchase")
    return {"rows": rows, "discount_given": _r(given), "discount_received": _r(got)}


def expense_item_report(conn, ws, frm="", to="") -> dict[str, Any]:
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ln in _lines(conn, ws, (7,), frm, to):
        a = agg[(ln.get("item_name") or "—").strip()]
        a["qty"] += _f(ln.get("qty"))
        a["amount"] += _f(ln.get("line_total"))
    rows = [{"item": k, "qty": _r(v["qty"]), "amount": _r(v["amount"])} for k, v in agg.items()]
    rows.sort(key=lambda r: -r["amount"])
    return {"rows": rows, "total": _r(sum(r["amount"] for r in rows))}


def order_report(conn, ws, kind: str = "sale", frm="", to="") -> dict[str, Any]:
    from app.hop_money import LABELS

    ty = 65 if kind == "sale" else 66
    rng, p = _range(frm, to)
    rows = []
    for d in _rows(conn, f"""SELECT t.*, c.txn_number AS conv_number, c.txn_type AS conv_type
                             FROM hop_party_transactions t LEFT JOIN hop_party_transactions c ON c.id=t.converted_to_txn_id
                             WHERE t.workspace_id=? AND {LIVE} AND t.txn_type=?{rng} ORDER BY date(t.txn_date) DESC""",
                   [ws, ty, *p]):
        status = f"Done → {LABELS.get(int(d['conv_type'] or 0), '')} {d['conv_number'] or ''}".strip() if d.get("conv_number") else "Open"
        rows.append({"id": d["id"], "date": (d["txn_date"] or "")[:10], "number": d["txn_number"], "party": d["party_name"],
                     "total": _r(_f(d["total_amount"])), "status": status})
    open_rows = [r for r in rows if r["status"] == "Open"]
    return {"kind": kind, "rows": rows, "open_count": len(open_rows), "open_value": _r(sum(r["total"] for r in open_rows))}


def gstr9(conn, ws, fy_start: str = "", fy_end: str = "") -> dict[str, Any]:
    """Annual GST summary for a financial year (April–March): outward, inward ITC, tax payable, HSN."""
    from datetime import date

    from app import hop_reports as R

    if not fy_start:
        t = date.today()
        y = t.year if t.month >= 4 else t.year - 1
        fy_start, fy_end = f"{y}-04-01", f"{y + 1}-03-31"
    g3 = R.gstr3b(conn, ws, fy_start, fy_end)
    g1 = R.gstr1(conn, ws, fy_start, fy_end)
    return {"from_date": fy_start, "to_date": fy_end,
            "table4_outward": g3["outward_supplies"],
            "b2b_invoices": len(g1["b2b"]), "credit_notes": len(g1["credit_notes"]),
            "table6_itc": g3["eligible_itc"], "table9_tax_payable": g3["tax_payable"],
            "credit_carried_forward": g3["credit_carried_forward"],
            "table17_hsn_outward": R.hsn_summary(conn, ws, "sale", fy_start, fy_end)["rows"],
            "table18_hsn_inward": R.hsn_summary(conn, ws, "purchase", fy_start, fy_end)["rows"],
            "note": "Summary from NEXORA documents for the year; tally with your monthly GSTR-1/3B before filing GSTR-9."}


MORE_REPORTS = {
    "party_pl": "Party-wise Profit & Loss", "item_pl": "Item-wise Profit & Loss",
    "party_item": "Party Report by Item", "group_sale": "Sale by Party Group", "group_purchase": "Purchase by Party Group",
    "category_report": "Item Category Report", "discount_report": "Discount Report",
    "expense_items": "Expense Item Report", "sale_orders": "Sale Order Report", "purchase_orders": "Purchase Order Report",
    "gstr9": "GSTR-9 (Annual)",
}


def run(conn, ws, k: str, a: dict[str, Any]) -> dict[str, Any] | None:
    frm, to = str(a.get("from") or "")[:10], str(a.get("to") or "")[:10]
    if k == "party_pl":
        return party_pl(conn, ws, frm, to)
    if k == "item_pl":
        return item_pl(conn, ws, frm, to)
    if k == "party_item":
        if not a.get("party_id"):
            raise ValueError("Choose a party")
        return party_item_report(conn, ws, int(a["party_id"]), frm, to)
    if k in ("group_sale", "group_purchase"):
        return group_report(conn, ws, "sale" if k == "group_sale" else "purchase", frm, to)
    if k == "category_report":
        return category_report(conn, ws, frm, to)
    if k == "discount_report":
        return discount_report(conn, ws, frm, to)
    if k == "expense_items":
        return expense_item_report(conn, ws, frm, to)
    if k in ("sale_orders", "purchase_orders"):
        return order_report(conn, ws, "sale" if k == "sale_orders" else "purchase", frm, to)
    if k == "gstr9":
        return gstr9(conn, ws, frm, to)
    return None
