"""HoP item extras: service items, MRP, wholesale price, second unit (1 BOX = 10 PCS),
barcode and custom fields.

Stored as extra columns on hop_products; every behaviour is wired:
* service items never move stock and never trigger "short stock";
* a bill line in the second unit moves ``qty × factor`` base units of stock;
* wholesale price is suggested when the quantity reaches the wholesale minimum;
* barcode finds the item (bill entry, phone scanner).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

COLUMNS = (
    ("item_type", "TEXT"),            # product | service
    ("mrp", "REAL"),
    ("wholesale_price", "REAL"),
    ("wholesale_min_qty", "REAL"),
    ("alt_unit", "TEXT"),             # second unit, e.g. BOX
    ("alt_unit_factor", "REAL"),      # 1 alt unit = factor base units
    ("barcode", "TEXT"),
    ("custom_fields", "TEXT"),        # JSON {"Colour": "Red", ...}
    ("cess_pct", "REAL"),             # additional cess % on top of GST
)


def ensure_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(hop_products)")}
    for col, ddl in COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE hop_products ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hop_products_barcode ON hop_products(workspace_id, barcode)")


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Not a number: {v}") from exc


def _s(v: Any) -> str:
    return str(v or "").strip()


def save_extras(conn: sqlite3.Connection, ws: str, product_id: int, payload: dict[str, Any]) -> None:
    """Save only the extra fields present in ``payload`` (checked first)."""
    ensure_columns(conn)
    sets: dict[str, Any] = {}
    if "item_type" in payload:
        t = _s(payload.get("item_type")).lower() or "product"
        if t not in ("product", "service"):
            raise ValueError("Item type must be product or service")
        sets["item_type"] = t
    for k in ("mrp", "wholesale_price", "wholesale_min_qty", "alt_unit_factor", "cess_pct"):
        if k in payload:
            v = _f(payload.get(k))
            if v is not None and v < 0:
                raise ValueError(f"{k.replace('_', ' ')} cannot be negative")
            sets[k] = v
    if "alt_unit" in payload:
        sets["alt_unit"] = _s(payload.get("alt_unit")).upper()[:12] or None
    if "barcode" in payload:
        code = _s(payload.get("barcode"))[:48] or None
        if code:
            dup = conn.execute("SELECT name FROM hop_products WHERE workspace_id=? AND barcode=? AND id!=? "
                               "AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, code, int(product_id))).fetchone()
            if dup:
                raise ValueError(f"Barcode {code} is already on {dup[0]}")
        sets["barcode"] = code
    if "custom_fields" in payload:
        cf = payload.get("custom_fields") or {}
        if isinstance(cf, str):
            cf = json.loads(cf) if cf.strip() else {}
        if not isinstance(cf, dict):
            raise ValueError("Custom fields must be name → value")
        cf = {_s(k)[:40]: _s(v)[:200] for k, v in list(cf.items())[:20] if _s(k)}
        sets["custom_fields"] = json.dumps(cf, ensure_ascii=False) if cf else None
    if not sets:
        return
    if sets.get("alt_unit") and not (sets.get("alt_unit_factor") or payload.get("alt_unit_factor")):
        cur = conn.execute("SELECT alt_unit_factor FROM hop_products WHERE id=?", (int(product_id),)).fetchone()
        if not cur or not cur[0]:
            raise ValueError("Say how many base units are in one " + sets["alt_unit"])
    conn.execute(f"UPDATE hop_products SET {', '.join(f'{k}=?' for k in sets)} WHERE workspace_id=? AND id=?",
                 (*sets.values(), ws, int(product_id)))


def decorate(row: dict[str, Any]) -> dict[str, Any]:
    """Item row for screens: custom fields as a dict, type defaulted."""
    cf = row.get("custom_fields")
    if isinstance(cf, str):
        try:
            row["custom_fields"] = json.loads(cf) if cf else {}
        except ValueError:
            row["custom_fields"] = {}
    elif cf is None:
        row["custom_fields"] = {}
    row["item_type"] = row.get("item_type") or "product"
    return row


def stock_qty_for_line(conn: sqlite3.Connection, ws: str, product_id: int, line: dict[str, Any]) -> float | None:
    """Base-unit quantity a bill line moves; None for service items (no stock)."""
    try:
        r = conn.execute("SELECT item_type, unit, alt_unit, alt_unit_factor FROM hop_products WHERE workspace_id=? AND id=?",
                         (ws, int(product_id))).fetchone()
    except sqlite3.OperationalError:
        r = None
    qty = float(line.get("qty") or 0) + float(line.get("free_qty") or 0)
    if not r:
        return qty
    if (r[0] or "product") == "service":
        return None
    unit = _s(line.get("unit")).upper()
    if r[2] and r[3] and unit and unit == _s(r[2]).upper() and unit != _s(r[1]).upper():
        return qty * float(r[3])
    return qty


def find_by_barcode(conn: sqlite3.Connection, ws: str, code: str) -> dict[str, Any] | None:
    ensure_columns(conn)
    code = _s(code)
    if not code:
        return None
    r = conn.execute("SELECT * FROM hop_products WHERE workspace_id=? AND (barcode=? OR lower(code)=lower(?)) "
                     "AND (deleted_at IS NULL OR trim(deleted_at)='') ORDER BY barcode=? DESC LIMIT 1",
                     (ws, code, code, code)).fetchone()
    return decorate(dict(r)) if r else None


def price_for_qty(product: dict[str, Any], qty: float, party_type: str = "customer") -> tuple[float | None, str | None]:
    """Catalogue price for this quantity: wholesale once qty reaches the minimum."""
    if party_type == "vendor":
        p = float(product.get("purchase_price") or 0) or float(product.get("selling_price") or 0)
        return (p or None), ("catalogue" if p else None)
    whole = float(product.get("wholesale_price") or 0)
    min_q = float(product.get("wholesale_min_qty") or 0)
    if whole > 0 and qty and qty >= max(min_q, 0.000001):
        return whole, "wholesale"
    p = float(product.get("selling_price") or 0)
    return (p or None), ("catalogue" if p else None)


def item_checks(row: dict[str, Any]) -> list[str]:
    """Plain warnings shown on the item form (rule based, no AI cost)."""
    out = []
    sell, mrp = float(row.get("selling_price") or 0), float(row.get("mrp") or 0)
    buy, whole = float(row.get("purchase_price") or 0), float(row.get("wholesale_price") or 0)
    if mrp and sell > mrp:
        out.append(f"Sale price ₹{sell:g} is above MRP ₹{mrp:g} — selling above MRP is not allowed.")
    if whole and sell and whole > sell:
        out.append("Wholesale price is higher than the normal sale price.")
    if whole and buy and whole < buy:
        out.append(f"Wholesale price ₹{whole:g} is below the purchase price ₹{buy:g} — a loss on every sale.")
    if (row.get("item_type") or "product") == "service" and float(row.get("stock_qty") or 0):
        out.append("Service items do not keep stock; the stock figure will be ignored.")
    if row.get("alt_unit") and not row.get("alt_unit_factor"):
        out.append("Second unit has no conversion (how many base units in one).")
    return out
