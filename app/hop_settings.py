"""HoP Phase 3 — items & settings helpers.

Item categories, units, party last-rate suggestions, invoice warnings,
due-date / overdue lists, and low-stock reorder hints. Masters mirror the
expense-category pattern from ``hop_money``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_UNITS = ("Pcs", "NOS", "MTR", "Kg", "Box", "Set", "Pair", "Roll", "Litre", "Dozen")
DEFAULT_ITEM_CATEGORIES = ("Bed Linen", "Fabric", "Furniture", "Curtains", "Carpet", "Accessories", "Other")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _s(v: Any) -> str:
    return str(v or "").strip()


def _f(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int | None:
    try:
        if v in (None, ""):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _seed_item_categories(conn: sqlite3.Connection, ws: str) -> None:
    if conn.execute("SELECT 1 FROM hop_item_categories WHERE workspace_id=? LIMIT 1", (ws,)).fetchone():
        return
    names = list(DEFAULT_ITEM_CATEGORIES)
    for (cat,) in conn.execute(
        "SELECT DISTINCT trim(category) FROM hop_products WHERE workspace_id=? AND trim(COALESCE(category,''))!=''",
        (ws,),
    ):
        if cat and cat not in names:
            names.append(cat)
    now = _now()
    for n in names:
        conn.execute(
            "INSERT OR IGNORE INTO hop_item_categories (workspace_id, name, created_at) VALUES (?, ?, ?)",
            (ws, n[:60], now),
        )
    conn.commit()


def list_item_categories(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    _seed_item_categories(conn, ws)
    rows = conn.execute(
        """SELECT c.id, c.name,
                  COALESCE((SELECT COUNT(*) FROM hop_products p
                            WHERE p.workspace_id=c.workspace_id
                              AND (p.category_id=c.id OR lower(trim(COALESCE(p.category,'')))=lower(c.name))), 0) AS product_count
           FROM hop_item_categories c WHERE c.workspace_id=? ORDER BY lower(c.name)""",
        (ws,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_item_category(conn: sqlite3.Connection, ws: str, name: str) -> dict[str, Any]:
    name = _s(name)[:60]
    if not name:
        raise ValueError("Category name is required")
    conn.execute(
        "INSERT OR IGNORE INTO hop_item_categories (workspace_id, name, created_at) VALUES (?, ?, ?)",
        (ws, name, _now()),
    )
    conn.commit()
    r = conn.execute(
        "SELECT id, name FROM hop_item_categories WHERE workspace_id=? AND name=?", (ws, name)
    ).fetchone()
    return dict(r)


def item_category(conn: sqlite3.Connection, ws: str, category_id: Any) -> dict[str, Any] | None:
    cid = _i(category_id)
    if not cid:
        return None
    r = conn.execute(
        "SELECT id, name FROM hop_item_categories WHERE workspace_id=? AND id=?", (ws, cid)
    ).fetchone()
    return dict(r) if r else None


def _seed_units(conn: sqlite3.Connection, ws: str) -> None:
    if conn.execute("SELECT 1 FROM hop_units WHERE workspace_id=? LIMIT 1", (ws,)).fetchone():
        return
    names = list(DEFAULT_UNITS)
    for (u,) in conn.execute(
        """SELECT DISTINCT trim(unit) FROM hop_txn_lines
           WHERE workspace_id=? AND trim(COALESCE(unit,''))!=''""",
        (ws,),
    ):
        if u and u not in names:
            names.append(u)
    for (u,) in conn.execute(
        "SELECT DISTINCT trim(unit) FROM hop_products WHERE workspace_id=? AND trim(COALESCE(unit,''))!=''",
        (ws,),
    ):
        if u and u not in names:
            names.append(u)
    now = _now()
    for n in names:
        conn.execute(
            "INSERT OR IGNORE INTO hop_units (workspace_id, name, created_at) VALUES (?, ?, ?)",
            (ws, n[:30], now),
        )
    conn.commit()


def list_units(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    _seed_units(conn, ws)
    rows = conn.execute(
        "SELECT id, name FROM hop_units WHERE workspace_id=? ORDER BY lower(name)", (ws,)
    ).fetchall()
    return [dict(r) for r in rows]


def create_unit(conn: sqlite3.Connection, ws: str, name: str) -> dict[str, Any]:
    name = _s(name)[:30]
    if not name:
        raise ValueError("Unit name is required")
    conn.execute(
        "INSERT OR IGNORE INTO hop_units (workspace_id, name, created_at) VALUES (?, ?, ?)",
        (ws, name, _now()),
    )
    conn.commit()
    r = conn.execute("SELECT id, name FROM hop_units WHERE workspace_id=? AND name=?", (ws, name)).fetchone()
    return dict(r)


def unit_row(conn: sqlite3.Connection, ws: str, unit_id: Any) -> dict[str, Any] | None:
    uid = _i(unit_id)
    if not uid:
        return None
    r = conn.execute("SELECT id, name FROM hop_units WHERE workspace_id=? AND id=?", (ws, uid)).fetchone()
    return dict(r) if r else None


def resolve_category_name(conn: sqlite3.Connection, ws: str, payload: dict[str, Any]) -> tuple[str, int | None]:
    """Prefer category_id; else free-text category (auto-create master)."""
    cat = item_category(conn, ws, payload.get("category_id"))
    if cat:
        return cat["name"], int(cat["id"])
    name = _s(payload.get("category"))
    if not name:
        return "", None
    created = create_item_category(conn, ws, name)
    return created["name"], int(created["id"])


def resolve_unit_name(conn: sqlite3.Connection, ws: str, payload: dict[str, Any]) -> tuple[str, int | None]:
    row = unit_row(conn, ws, payload.get("unit_id"))
    if row:
        return row["name"], int(row["id"])
    name = _s(payload.get("unit"))
    if not name:
        return "", None
    created = create_unit(conn, ws, name)
    return created["name"], int(created["id"])


def suggest_party_rate(
    conn: sqlite3.Connection,
    ws: str,
    *,
    party_type: str,
    party_id: int,
    product_id: int | None = None,
    item_name: str = "",
) -> dict[str, Any]:
    """Last rate this party paid/charged for the item, plus catalogue sell/buy."""
    party_type = _s(party_type).lower() or "customer"
    party_id = int(party_id or 0)
    product_id = _i(product_id)
    item_name = _s(item_name)
    if not party_id:
        raise ValueError("party_id is required")
    if not product_id and not item_name:
        raise ValueError("product_id or item_name is required")

    catalogue: dict[str, Any] | None = None
    if product_id:
        r = conn.execute(
            "SELECT id, name, selling_price, purchase_price, unit, hsn, gst_pct FROM hop_products WHERE workspace_id=? AND id=?",
            (ws, product_id),
        ).fetchone()
        if r:
            catalogue = dict(r)
            if not item_name:
                item_name = _s(catalogue.get("name"))

    params: list[Any] = [ws, party_type, party_id]
    where = ["t.workspace_id=?", "t.party_type=?", "t.party_id=?", "t.txn_type IN (1,2,16,21,27,65,83)"]
    if product_id:
        where.append("l.product_id=?")
        params.append(product_id)
    else:
        where.append("lower(trim(l.item_name))=lower(?)")
        params.append(item_name)

    row = conn.execute(
        f"""SELECT l.rate, l.unit, l.tax_pct, l.hsn, t.txn_date, t.txn_number, t.txn_type, t.id AS txn_id
            FROM hop_txn_lines l
            JOIN hop_party_transactions t
              ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
            WHERE {' AND '.join(where)} AND COALESCE(l.rate,0)>0
            ORDER BY t.txn_date DESC, t.id DESC LIMIT 1""",
        params,
    ).fetchone()
    last = dict(row) if row else None

    suggested = None
    source = None
    if last:
        suggested = _f(last.get("rate"))
        source = "party_last"
    elif catalogue:
        if party_type == "vendor":
            suggested = _f(catalogue.get("purchase_price")) or _f(catalogue.get("selling_price"))
        else:
            suggested = _f(catalogue.get("selling_price"))
        source = "catalogue" if suggested else None

    # Typical rates for unusual-price check (same product, any party, last 20)
    rates: list[float] = []
    if product_id:
        rates = [
            _f(r[0])
            for r in conn.execute(
                """SELECT l.rate FROM hop_txn_lines l
                   JOIN hop_party_transactions t
                     ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
                   WHERE t.workspace_id=? AND l.product_id=? AND COALESCE(l.rate,0)>0
                   ORDER BY t.txn_date DESC, t.id DESC LIMIT 20""",
                (ws, product_id),
            ).fetchall()
        ]
    typical = round(sum(rates) / len(rates), 2) if rates else None

    return {
        "suggested_rate": suggested,
        "source": source,
        "unit": (last or {}).get("unit") or (catalogue or {}).get("unit") or "",
        "tax_pct": (last or {}).get("tax_pct") if last else (catalogue or {}).get("gst_pct"),
        "hsn": (last or {}).get("hsn") if last else (catalogue or {}).get("hsn"),
        "last_txn": (
            {
                "txn_id": last["txn_id"],
                "txn_number": last["txn_number"],
                "txn_date": last["txn_date"],
                "rate": _f(last["rate"]),
            }
            if last
            else None
        ),
        "catalogue": (
            {
                "product_id": catalogue["id"],
                "name": catalogue["name"],
                "selling_price": catalogue.get("selling_price"),
                "purchase_price": catalogue.get("purchase_price"),
            }
            if catalogue
            else None
        ),
        "typical_rate": typical,
    }


_COMMON_GST_RATES = {0.0, 0.1, 0.25, 1.0, 1.5, 3.0, 5.0, 7.5, 12.0, 18.0, 28.0}


def invoice_entry_warnings(
    conn: sqlite3.Connection,
    ws: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Soft warnings: duplicate bill no, unusual rates, missing GSTIN/HSN, odd GST%."""
    warnings: list[dict[str, Any]] = []
    txn_type = int(payload.get("txn_type") or 0)
    party_type = _s(payload.get("party_type")).lower() or ("vendor" if txn_type in (2, 4, 16) else "customer")
    party_id = _i(payload.get("party_id") or payload.get("customer_id") or payload.get("vendor_id")) or 0
    txn_number = _s(payload.get("txn_number"))
    exclude_id = _i(payload.get("exclude_txn_id") or payload.get("party_txn_id"))
    tax_doc = txn_type in (1, 2, 16, 21)  # invoices / returns that feed GST reports

    if txn_number:
        q = """SELECT id, txn_number, party_name, txn_date FROM hop_party_transactions
               WHERE (deleted_at IS NULL OR trim(deleted_at)='') AND workspace_id=? AND txn_type=? AND lower(trim(txn_number))=lower(?)"""
        params: list[Any] = [ws, txn_type, txn_number]
        if exclude_id:
            q += " AND id!=?"
            params.append(exclude_id)
        dup = conn.execute(q + " LIMIT 1", params).fetchone()
        if dup:
            warnings.append({
                "code": "duplicate_bill_no",
                "level": "warn",
                "message": f"Bill no {dup['txn_number']} already used ({dup['party_name']} · {dup['txn_date']}).",
                "txn_id": dup["id"],
            })

    if tax_doc and party_id and party_type in ("customer", "vendor"):
        table = "hop_vendors" if party_type == "vendor" else "hop_customers"
        prow = conn.execute(
            f"SELECT company, gst_no, gst_type FROM {table} WHERE workspace_id=? AND id=?",
            (ws, int(party_id)),
        ).fetchone()
        if prow:
            gst_no = _s(prow["gst_no"] if "gst_no" in prow.keys() else "")
            gst_type = _s(prow["gst_type"] if "gst_type" in prow.keys() else "").lower()
            registered = ("registered" in gst_type) or (len(gst_no) == 15)
            if registered and len(gst_no) != 15:
                warnings.append({
                    "code": "missing_gstin",
                    "level": "warn",
                    "message": f"{prow['company'] or 'Party'} looks registered but GSTIN is missing or incomplete.",
                })

    for ln in payload.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        rate = _f(ln.get("rate"))
        pid = _i(ln.get("product_id"))
        name = _s(ln.get("item_name"))
        hsn = _s(ln.get("hsn"))
        tax_pct = ln.get("tax_pct")
        if tax_pct is None:
            tax_pct = ln.get("gst_pct")
        tax_f = _f(tax_pct) if tax_pct not in (None, "") else None
        if not pid and not name:
            continue

        if tax_doc and rate > 0 and not hsn:
            label = name or f"item #{pid}"
            warnings.append({
                "code": "missing_hsn",
                "level": "warn",
                "message": f"{label}: HSN/SAC missing — needed for GST reports.",
                "item_name": name,
                "product_id": pid,
            })

        if tax_doc and tax_f is not None and tax_f not in _COMMON_GST_RATES:
            label = name or f"item #{pid}"
            warnings.append({
                "code": "unusual_gst_rate",
                "level": "warn",
                "message": f"{label}: GST {tax_f:g}% is unusual (common: 0/5/12/18/28).",
                "item_name": name,
                "product_id": pid,
                "tax_pct": tax_f,
            })

        if rate <= 0:
            continue
        try:
            sug = suggest_party_rate(
                conn, ws, party_type=party_type, party_id=party_id or 0,
                product_id=pid, item_name=name,
            ) if party_id else {"typical_rate": None, "suggested_rate": None}
        except ValueError:
            sug = {"typical_rate": None, "suggested_rate": None}
        baseline = sug.get("typical_rate") or sug.get("suggested_rate")
        if baseline and baseline > 0:
            pct = abs(rate - baseline) / baseline * 100.0
            if pct >= 25:
                label = name or f"item #{pid}"
                warnings.append({
                    "code": "unusual_price",
                    "level": "warn",
                    "message": f"{label}: rate ₹{rate:g} is {pct:.0f}% off usual ₹{baseline:g}.",
                    "item_name": name,
                    "product_id": pid,
                    "rate": rate,
                    "baseline": baseline,
                })
    return warnings


def compute_due_date(txn_date: str, credit_days: int | None) -> str | None:
    days = int(credit_days or 0)
    if days <= 0:
        return None
    try:
        base = datetime.strptime(_s(txn_date)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (base + timedelta(days=days)).strftime("%Y-%m-%d")


def party_credit_days(conn: sqlite3.Connection, ws: str, party_type: str, party_id: int) -> int | None:
    table = "hop_vendors" if party_type == "vendor" else "hop_customers"
    r = conn.execute(
        f"SELECT credit_days FROM {table} WHERE workspace_id=? AND id=?", (ws, int(party_id))
    ).fetchone()
    if not r:
        return None
    try:
        return int(r[0]) if r[0] is not None else None
    except (TypeError, ValueError):
        return None


def overdue_bills(
    conn: sqlite3.Connection,
    ws: str,
    *,
    as_of: str | None = None,
    party_type: str | None = None,
) -> dict[str, Any]:
    as_of = _s(as_of)[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params: list[Any] = [ws, as_of]
    where = [
        "workspace_id=?",
        "txn_type IN (1,2)",
        "COALESCE(balance_amount,0)>0.005",
        "due_date IS NOT NULL AND trim(due_date)!=''",
        "date(due_date) < date(?)",
    ]
    if party_type in ("customer", "vendor"):
        where.append("party_type=?")
        params.append(party_type)
    rows = [
        dict(r)
        for r in conn.execute(
            f"""SELECT id, party_type, party_id, party_name, txn_type, txn_label, txn_number,
                       txn_date, due_date, total_amount, balance_amount, status_text,
                       CAST(julianday(?) - julianday(due_date) AS INTEGER) AS days_overdue
                FROM hop_party_transactions
                WHERE {' AND '.join(where)}
                ORDER BY due_date ASC, id ASC""",
            [as_of] + params,
        ).fetchall()
    ]
    total = round(sum(_f(r.get("balance_amount")) for r in rows), 2)
    return {"as_of": as_of, "count": len(rows), "total_overdue": total, "bills": rows}


def low_stock_reorder(conn: sqlite3.Connection, ws: str, *, days: int = 30, low_at: float = 0) -> dict[str, Any]:
    """Items at/under low_at with suggested reorder from recent sale speed."""
    days = max(7, min(int(days or 30), 180))
    low_at = _f(low_at)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    products = [
        dict(r)
        for r in conn.execute(
            """SELECT id, name, code, category, unit, stock_qty, selling_price, purchase_price
               FROM hop_products WHERE workspace_id=? AND status!='inactive'
               ORDER BY lower(name)""",
            (ws,),
        ).fetchall()
    ]
    sold = {
        int(r[0]): _f(r[1])
        for r in conn.execute(
            """SELECT l.product_id, SUM(ABS(l.qty))
               FROM hop_txn_lines l
               JOIN hop_party_transactions t
                 ON t.workspace_id=l.workspace_id AND t.source_txn_id=l.source_txn_id
               WHERE t.workspace_id=? AND t.txn_type IN (1,21) AND l.product_id IS NOT NULL
                 AND t.txn_date >= ?
               GROUP BY l.product_id""",
            (ws, since),
        ).fetchall()
        if r[0]
    }
    out = []
    for p in products:
        qty = _f(p.get("stock_qty"))
        if qty > low_at:
            continue
        pid = int(p["id"])
        sold_qty = sold.get(pid, 0.0)
        daily = sold_qty / float(days) if days else 0.0
        # Cover ~30 days of sales, at least 1 if anything sold
        suggest = max(1.0, round(daily * 30, 1)) if sold_qty > 0 else None
        out.append({
            **p,
            "stock_qty": qty,
            "sold_last_days": sold_qty,
            "days": days,
            "daily_sale_rate": round(daily, 3),
            "suggested_reorder_qty": suggest,
            "hint": (
                f"Sold {sold_qty:g} in {days} days — reorder ~{suggest:g} {p.get('unit') or 'Pcs'}"
                if suggest
                else "No recent sales — set reorder by eye"
            ),
        })
    return {"days": days, "low_at": low_at, "count": len(out), "items": out}
