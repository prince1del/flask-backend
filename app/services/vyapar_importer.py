"""Vyapar backup (.vyb/.vyp) to HoP converter/importer."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _guess_customer_type(name: str) -> str:
    n = (name or "").lower()
    if "hotel" in n or "inn" in n or "resort" in n:
        return "Hotel"
    if "architect" in n or "design" in n or "interior" in n:
        return "Designer"
    return "Customer"


def _extract_sqlite_bytes(raw: bytes, filename: str) -> bytes:
    ext = Path(filename).suffix.lower()
    if ext == ".vyp":
        return raw
    if ext != ".vyb":
        raise ValueError("Unsupported backup format. Upload .vyb or .vyp")
    try:
        from io import BytesIO

        zf = zipfile.ZipFile(BytesIO(raw))
        names = zf.namelist()
        if not names:
            raise ValueError("Backup ZIP is empty")
        return zf.read(names[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid .vyb backup file") from exc


def _with_source_db(sqlite_bytes: bytes):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
    tmp.write(sqlite_bytes)
    tmp.flush()
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    return conn, Path(tmp.name)


def _fetch_parties(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT n.name_id, n.full_name, n.phone_number, n.email, n.name_type, n.name_group_id,
               n.name_gstin_number, n.name_state, n.address
        FROM kb_names n
        WHERE n.name_type = 1 AND trim(coalesce(n.full_name, '')) != ''
        ORDER BY n.name_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        party = dict(r)
        addr = conn.execute(
            "SELECT address FROM kb_address WHERE name_id=? ORDER BY address_id DESC LIMIT 1",
            (r["name_id"],),
        ).fetchone()
        if addr and _clean(addr["address"]):
            party["address"] = _clean(addr["address"])
        out.append(party)
    return out


def _fetch_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT i.item_id, i.item_name, i.item_code, i.item_sale_unit_price, i.item_purchase_unit_price,
               i.item_stock_quantity, i.item_hsn_sac_code, i.item_tax_id, i.item_description,
               c.item_category_name AS category_name
        FROM kb_items i
        LEFT JOIN kb_item_categories_mapping m ON m.item_id = i.item_id
        LEFT JOIN kb_item_categories c ON c.item_category_id = m.category_id
        WHERE i.item_is_active = 1 AND trim(coalesce(i.item_name,'')) != ''
        ORDER BY i.item_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def preview_vyapar_backup(file_bytes: bytes, filename: str) -> dict[str, Any]:
    sqlite_bytes = _extract_sqlite_bytes(file_bytes, filename)
    src, src_path = _with_source_db(sqlite_bytes)
    try:
        table_count = src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        firm = src.execute(
            "SELECT firm_name, firm_email, firm_phone FROM kb_firms ORDER BY firm_id LIMIT 1"
        ).fetchone()
        parties = _fetch_parties(src)
        items = _fetch_items(src)
        by_group: dict[str, int] = {}
        for p in parties:
            gid = str(p.get("name_group_id") or "unknown")
            by_group[gid] = by_group.get(gid, 0) + 1
        return {
            "source": {
                "filename": filename,
                "sqlite_bytes": len(sqlite_bytes),
                "tables": int(table_count),
                "firm_name": _clean(firm["firm_name"]) if firm else "",
                "firm_email": _clean(firm["firm_email"]) if firm else "",
                "firm_phone": _clean(firm["firm_phone"]) if firm else "",
            },
            "detected": {
                "parties_total": len(parties),
                "items_total": len(items),
                "group_split": by_group,
            },
            "samples": {
                "parties": [
                    {
                        "name": _clean(p.get("full_name")),
                        "mobile": _clean(p.get("phone_number")),
                        "email": _clean(p.get("email")),
                        "group": p.get("name_group_id"),
                        "gst": _clean(p.get("name_gstin_number")),
                    }
                    for p in parties[:8]
                ],
                "items": [
                    {
                        "name": _clean(i.get("item_name")),
                        "code": _clean(i.get("item_code")),
                        "sell": float(i.get("item_sale_unit_price") or 0),
                        "buy": float(i.get("item_purchase_unit_price") or 0),
                        "category": _clean(i.get("category_name")),
                    }
                    for i in items[:8]
                ],
            },
        }
    finally:
        src.close()
        src_path.unlink(missing_ok=True)


def import_vyapar_backup(
    *,
    file_bytes: bytes,
    filename: str,
    target_conn: sqlite3.Connection,
    workspace_id: str,
    hop_db_module,
    hop_ops_module,
) -> dict[str, Any]:
    sqlite_bytes = _extract_sqlite_bytes(file_bytes, filename)
    src, src_path = _with_source_db(sqlite_bytes)
    try:
        parties = _fetch_parties(src)
        items = _fetch_items(src)

        existing_customers = {
            _clean(r.get("company")).lower(): r for r in hop_db_module.list_customers(target_conn, workspace_id)
        }
        existing_vendors = {
            _clean(r.get("company")).lower(): r for r in hop_ops_module.list_vendors(target_conn, workspace_id)
        }
        existing_products = {
            _clean(r.get("name")).lower(): r for r in hop_ops_module.list_products(target_conn, workspace_id)
        }

        out = {
            "customers_created": 0,
            "customers_skipped": 0,
            "vendors_created": 0,
            "vendors_skipped": 0,
            "products_created": 0,
            "products_skipped": 0,
            "errors": [],
        }

        for p in parties:
            name = _clean(p.get("full_name"))
            if not name:
                continue
            key = name.lower()
            payload = {
                "company": name,
                "contact_person": name if len(name.split()) <= 4 else "",
                "mobile": _digits(p.get("phone_number")),
                "email": _clean(p.get("email")),
                "city": _clean(p.get("name_state")),
                "address": _clean(p.get("address")),
                "gst_no": _clean(p.get("name_gstin_number")).upper(),
            }
            try:
                if p.get("name_group_id") == 2:
                    if key in existing_vendors:
                        out["vendors_skipped"] += 1
                        continue
                    payload["products"] = "Imported from Vyapar"
                    payload["rating"] = 3
                    row = hop_ops_module.create_vendor(target_conn, workspace_id, payload)
                    existing_vendors[key] = row
                    out["vendors_created"] += 1
                else:
                    if key in existing_customers:
                        out["customers_skipped"] += 1
                        continue
                    payload["customer_type"] = _guess_customer_type(name)
                    payload["status"] = "active"
                    row = hop_db_module.create_customer(target_conn, workspace_id, payload)
                    existing_customers[key] = row
                    out["customers_created"] += 1
            except Exception as exc:  # defensive import loop
                out["errors"].append(f"Party '{name}': {exc}")

        for i in items:
            name = _clean(i.get("item_name"))
            if not name:
                continue
            key = name.lower()
            if key in existing_products:
                out["products_skipped"] += 1
                continue
            payload = {
                "name": name,
                "code": _clean(i.get("item_code")),
                "category": _clean(i.get("category_name")) or "Imported",
                "selling_price": float(i.get("item_sale_unit_price") or 0),
                "purchase_price": float(i.get("item_purchase_unit_price") or 0),
                "stock_qty": float(i.get("item_stock_quantity") or 0),
                "gst_pct": 5,
                "specs": _clean(i.get("item_description"))[:500],
            }
            try:
                row = hop_ops_module.create_product(target_conn, workspace_id, payload)
                existing_products[key] = row
                out["products_created"] += 1
            except Exception as exc:
                out["errors"].append(f"Item '{name}': {exc}")

        out["source_items"] = len(items)
        out["source_parties"] = len(parties)
        out["imported_ok"] = (
            out["customers_created"] + out["vendors_created"] + out["products_created"]
        )
        return out
    finally:
        src.close()
        src_path.unlink(missing_ok=True)
