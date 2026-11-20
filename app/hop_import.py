"""HoP import from Excel / CSV: items and parties, with a preview before anything is saved.

1. ``read_table`` — .xlsx or .csv → rows (the header row is found by itself).
2. ``preview`` — columns matched to NEXORA fields by their names (English / Hinglish
   variants), each row checked (missing name, bad GSTIN / mobile, negative price,
   already in the app). Nothing is saved.
3. ``commit`` — the user's confirmed rows are saved; existing items can be bulk-updated
   (price, stock, GST…) when the user ticks "update existing".
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from typing import Any

FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "items": {
        "name": ("item name", "item", "product", "product name", "name", "description", "maal", "article"),
        "code": ("item code", "code", "sku", "product code"),
        "hsn": ("hsn", "hsn/sac", "hsn code", "sac"),
        "gst_pct": ("gst", "gst %", "gst rate", "tax", "tax %", "tax rate", "gst%"),
        "selling_price": ("sale price", "selling price", "sales price", "rate", "price", "sp", "bechne ka rate"),
        "purchase_price": ("purchase price", "cost", "cost price", "buy price", "kharid rate", "pp"),
        "mrp": ("mrp",),
        "wholesale_price": ("wholesale price", "wholesale", "dealer price"),
        "stock_qty": ("opening stock", "stock", "qty", "quantity", "opening qty", "current stock"),
        "unit": ("unit", "uom", "units"),
        "category": ("category", "group", "item category"),
        "barcode": ("barcode", "ean", "upc"),
        "item_type": ("type", "item type"),
    },
    "parties": {
        "company": ("party name", "firm name", "company name", "business name", "customer name",
                    "company", "customer", "party", "firm", "name"),
        "mobile": ("mobile", "phone", "phone no", "mobile no", "contact no", "contact number", "whatsapp"),
        "email": ("email", "email id", "e-mail"),
        "gst_no": ("gstin", "gst no", "gst number", "gstin/uin", "gst"),
        "city": ("city", "town"),
        "state": ("state",),
        "address": ("address", "billing address"),
        "customer_type": ("group", "party group", "category"),
        "kind": ("type", "party type", "customer/supplier"),
        "opening_balance": ("opening balance", "balance", "receivable", "opening"),
        "credit_limit": ("credit limit",),
        "contact_person": ("contact person", "contact name", "owner"),
    },
}
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
NUMERIC = {"gst_pct", "selling_price", "purchase_price", "mrp", "wholesale_price", "stock_qty", "opening_balance", "credit_limit"}
MAX_ROWS = 3000
LABEL = {"selling_price": "sale price", "purchase_price": "purchase price", "stock_qty": "stock", "gst_pct": "GST %",
         "mrp": "MRP", "wholesale_price": "wholesale price", "opening_balance": "opening balance",
         "credit_limit": "credit limit"}


def _norm(h: Any) -> str:
    return re.sub(r"[^a-z0-9%/ ]", "", str(h or "").strip().lower().replace("_", " ")).strip()


def read_table(data: bytes, filename: str) -> list[list[Any]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [list(r) for r, _ in zip(ws.iter_rows(values_only=True), range(MAX_ROWS + 50))]
        wb.close()
        return rows
    if name.endswith(".xls"):
        raise ValueError("Old .xls files are not supported — save it as .xlsx or .csv and upload again")
    text = data.decode("utf-8-sig", errors="replace")
    return [r for r, _ in zip(csv.reader(io.StringIO(text)), range(MAX_ROWS + 50))]


def map_columns(kind: str, header: list[Any]) -> dict[int, str]:
    fields = FIELDS[kind]
    out: dict[int, str] = {}
    used: set[str] = set()
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        for f, names in fields.items():
            if f not in used and (n in names or n == f.replace("_", " ")):
                out[i] = f
                used.add(f)
                break
    return out


def _find_header(kind: str, rows: list[list[Any]]) -> tuple[int, dict[int, str]]:
    best = (-1, {})
    for i, r in enumerate(rows[:25]):
        m = map_columns(kind, r)
        if len(m) > len(best[1]):
            best = (i, m)
    key = "name" if kind == "items" else "company"
    if best[0] < 0 or key not in best[1].values():
        raise ValueError("Could not find the " + ("Item name" if kind == "items" else "Party name") +
                         " column. Put column names in the first row.")
    return best


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").replace("%", "").strip())
    except ValueError:
        return float("nan")


def _existing(conn, ws, kind) -> dict[str, int]:
    if kind == "items":
        rows = conn.execute("SELECT id, name FROM hop_products WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='')",
                            (ws,)).fetchall()
        return {str(r[1]).strip().lower(): int(r[0]) for r in rows}
    out = {}
    for t in ("hop_customers", "hop_vendors"):
        for r in conn.execute(f"SELECT id, company FROM {t} WHERE workspace_id=?", (ws,)).fetchall():
            out[str(r[1] or "").strip().lower()] = int(r[0])
    return out


def preview(conn: sqlite3.Connection, ws: str, kind: str, table: list[list[Any]]) -> dict[str, Any]:
    if kind not in FIELDS:
        raise ValueError("Import items or parties")
    hi, mapping = _find_header(kind, table)
    header = table[hi]
    existing = _existing(conn, ws, kind)
    seen: set[str] = set()
    rows = []
    key = "name" if kind == "items" else "company"
    for r in table[hi + 1: hi + 1 + MAX_ROWS]:
        if not any(str(c or "").strip() for c in r):
            continue
        rec: dict[str, Any] = {}
        for i, f in mapping.items():
            v = r[i] if i < len(r) else None
            if f in NUMERIC:
                rec[f] = _num(v)
            else:
                rec[f] = str(v).strip() if v not in (None, "") else ""
        problems = []
        name = str(rec.get(key) or "").strip()
        if not name:
            problems.append("name is missing")
        for f in NUMERIC:
            v = rec.get(f)
            if v is not None and v != v:  # NaN
                problems.append(f"{LABEL.get(f, f)} is not a number")
                rec[f] = None
            elif v is not None and v < 0 and f != "opening_balance":
                problems.append(f"{LABEL.get(f, f)} is negative")
        if rec.get("gst_pct") not in (None,) and rec.get("gst_pct") == rec.get("gst_pct") and rec["gst_pct"] is not None \
                and rec["gst_pct"] not in (0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28):
            problems.append(f"GST {rec['gst_pct']:g}% is not a usual rate")
        if kind == "parties":
            g = re.sub(r"\s", "", str(rec.get("gst_no") or "")).upper()
            rec["gst_no"] = g
            if g and not GSTIN_RE.match(g):
                problems.append("GSTIN looks wrong")
            m = re.sub(r"\D", "", str(rec.get("mobile") or ""))
            if m and len(m[-10:]) != 10:
                problems.append("mobile number looks wrong")
            k = str(rec.get("kind") or "").lower()
            rec["kind"] = "vendor" if any(x in k for x in ("supplier", "vendor", "purchase", "creditor")) else "customer"
        low = name.lower()
        status = "error" if problems and not name else ("duplicate_in_file" if low in seen else
                                                        ("existing" if low in existing else "new"))
        seen.add(low)
        rows.append({"data": rec, "status": status, "problems": problems, "existing_id": existing.get(low)})
    counts: dict[str, int] = {}
    for x in rows:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {"kind": kind, "columns": [{"heading": str(header[i]), "field": f} for i, f in sorted(mapping.items())],
            "ignored_columns": [str(h) for i, h in enumerate(header) if i not in mapping and str(h or "").strip()],
            "rows": rows, "counts": counts}


def commit(conn: sqlite3.Connection, ws: str, kind: str, rows: list[dict[str, Any]], update_existing: bool = False) -> dict[str, Any]:
    from app import hop_db, hop_ops

    if kind not in FIELDS:
        raise ValueError("Import items or parties")
    existing = _existing(conn, ws, kind)
    key = "name" if kind == "items" else "company"
    added = updated = skipped = 0
    errors: list[str] = []
    for i, raw in enumerate((rows or [])[:MAX_ROWS], 1):
        rec = {k: v for k, v in (raw or {}).items() if k in FIELDS[kind] and v not in (None, "")}
        name = str(rec.get(key) or "").strip()
        if not name:
            skipped += 1
            continue
        try:
            eid = existing.get(name.lower())
            if kind == "items":
                if eid:
                    if not update_existing:
                        skipped += 1
                        continue
                    hop_ops.update_product(conn, ws, eid, rec)
                    updated += 1
                else:
                    p = hop_ops.create_product(conn, ws, rec)
                    existing[name.lower()] = int(p["id"])
                    added += 1
            else:
                if eid:
                    skipped += 1   # parties are never overwritten by an import
                    continue
                kind_ = rec.pop("kind", "customer")
                if kind_ == "vendor":
                    hop_ops.create_vendor(conn, ws, rec)
                else:
                    hop_db.create_customer(conn, ws, rec)
                existing[name.lower()] = -1
                added += 1
        except (ValueError, sqlite3.Error) as exc:
            conn.rollback()
            errors.append(f"Row {i} ({name}): {exc}")
    conn.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors[:50]}
