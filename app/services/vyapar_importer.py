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


def _fetch_txns(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.txn_id, t.txn_type, t.txn_date, t.txn_due_date, t.txn_ref_number_char,
               t.txn_display_name, t.txn_description, t.txn_balance_amount, t.txn_current_balance,
               t.txn_tax_amount, t.txn_discount_amount, t.txn_name_id,
               n.full_name AS party_name
        FROM kb_transactions t
        LEFT JOIN kb_names n ON n.name_id = t.txn_name_id
        WHERE t.txn_type IN (27, 2, 4)
        ORDER BY t.txn_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        sums = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(lineitem_tax_amount),0) FROM kb_lineitems WHERE lineitem_txn_id=?",
            (r["txn_id"],),
        ).fetchone()
        d["line_total"] = float(sums[0] or 0)
        d["line_tax_total"] = float(sums[1] or 0)
        out.append(d)
    return out


def _fetch_txn_payments(conn: sqlite3.Connection) -> dict[int, float]:
    rows = conn.execute(
        "SELECT txn_id, COALESCE(SUM(amount),0) FROM txn_payment_mapping GROUP BY txn_id"
    ).fetchall()
    return {int(r[0]): float(r[1] or 0) for r in rows}


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
        txns = _fetch_txns(src)
        txn_pay = _fetch_txn_payments(src)
        by_group: dict[str, int] = {}
        for p in parties:
            gid = str(p.get("name_group_id") or "unknown")
            by_group[gid] = by_group.get(gid, 0) + 1
        txn_types: dict[str, int] = {}
        for t in txns:
            k = str(t.get("txn_type"))
            txn_types[k] = txn_types.get(k, 0) + 1
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
                "transactions_total": len(txns),
                "group_split": by_group,
                "txn_type_split": txn_types,
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
                "transactions": [
                    {
                        "txn_id": int(t.get("txn_id") or 0),
                        "txn_type": int(t.get("txn_type") or 0),
                        "party_name": _clean(t.get("party_name") or t.get("txn_display_name")),
                        "amount": float(t.get("line_total") or t.get("txn_balance_amount") or 0),
                        "paid": float(txn_pay.get(int(t.get("txn_id") or 0), 0)),
                    }
                    for t in txns[:8]
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
        txns = _fetch_txns(src)
        txn_payments = _fetch_txn_payments(src)

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
            "invoices_created": 0,
            "invoices_skipped": 0,
            "payments_created": 0,
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

        existing_invoices = {
            _clean(r.get("invoice_no")).lower(): r
            for r in hop_ops_module.list_invoices(target_conn, workspace_id)
            if _clean(r.get("invoice_no"))
        }

        customer_by_name = {
            _clean(r.get("company")).lower(): r for r in hop_db_module.list_customers(target_conn, workspace_id)
        }

        for t in txns:
            txn_id = int(t.get("txn_id") or 0)
            party_name = _clean(t.get("party_name") or t.get("txn_display_name"))
            if not party_name:
                out["invoices_skipped"] += 1
                continue
            customer = customer_by_name.get(party_name.lower())
            if not customer:
                try:
                    customer = hop_db_module.create_customer(
                        target_conn,
                        workspace_id,
                        {
                            "company": party_name,
                            "contact_person": party_name if len(party_name.split()) <= 4 else "",
                            "customer_type": _guess_customer_type(party_name),
                            "status": "active",
                        },
                    )
                    customer_by_name[party_name.lower()] = customer
                except Exception as exc:
                    out["errors"].append(f"Txn {txn_id}: customer create failed ({exc})")
                    out["invoices_skipped"] += 1
                    continue

            inv_no = _clean(t.get("txn_ref_number_char")) or f"VYP-{txn_id}"
            inv_key = inv_no.lower()
            if inv_key in existing_invoices:
                out["invoices_skipped"] += 1
                continue

            amount = float(t.get("line_total") or t.get("txn_balance_amount") or t.get("txn_current_balance") or 0)
            tax_amt = float(t.get("line_tax_total") or t.get("txn_tax_amount") or 0)
            paid_amt = float(txn_payments.get(txn_id, 0))
            due = _clean(t.get("txn_due_date"))
            inv_date = _clean(t.get("txn_date"))
            note = _clean(t.get("txn_description"))
            try:
                inv = hop_ops_module.create_invoice(
                    target_conn,
                    workspace_id,
                    {
                        "invoice_no": inv_no,
                        "customer_id": customer.get("id"),
                        "amount": amount,
                        "paid_amount": paid_amt,
                        "due_date": due,
                        "invoice_date": inv_date[:10] if inv_date else "",
                        "gst_amount": tax_amt,
                        "notes": f"Imported from Vyapar txn {txn_id}. {note}".strip(),
                    },
                )
                existing_invoices[inv_key] = inv
                out["invoices_created"] += 1
                if paid_amt > 0:
                    try:
                        hop_ops_module.create_payment(
                            target_conn,
                            workspace_id,
                            {
                                "invoice_id": inv.get("id"),
                                "amount": paid_amt,
                                "paid_at": inv_date[:10] if inv_date else "",
                                "method": "imported",
                                "notes": f"Imported from txn_payment_mapping for txn {txn_id}",
                                "customer_id": customer.get("id"),
                            },
                        )
                        out["payments_created"] += 1
                    except Exception as exc:
                        out["errors"].append(f"Txn {txn_id}: payment import failed ({exc})")
            except Exception as exc:
                out["errors"].append(f"Txn {txn_id}: invoice import failed ({exc})")
                out["invoices_skipped"] += 1

        out["source_items"] = len(items)
        out["source_parties"] = len(parties)
        out["source_transactions"] = len(txns)
        out["imported_ok"] = (
            out["customers_created"]
            + out["vendors_created"]
            + out["products_created"]
            + out["invoices_created"]
            + out["payments_created"]
        )
        return out
    finally:
        src.close()
        src_path.unlink(missing_ok=True)
