"""Vyapar backup (.vyb/.vyp) to HoP converter/importer."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
import re
from collections import Counter
from pathlib import Path
from typing import Any

# Only tax Sale Invoices mirror into hop_invoices.
# NOTE: In this Vyapar dataset, txn_type 27 is Estimate (UI "Estimate", series HOPPI…),
# not a Sale bill. Real sales are txn_type 1 (Sale Invoice).
INVOICE_TYPES = {1}
# Estimates / quotations (Vyapar type 27 + legacy type 30)
QUOTATION_TYPES = {27, 30}


def _txn_label(txn_type: int) -> str:
    labels = {
        1: "Sale Invoice",
        2: "Purchase Bill",
        # Vyapar: 3 = Payment-In, 4 = Payment-Out (was swapped earlier).
        3: "Payment In",
        4: "Payment Out",
        7: "Expense",
        16: "Purchase Return",
        21: "Sale Return",
        # Vyapar stores Estimates as type 27 for this firm (shown as Estimate + HOPPI prefix in UI).
        27: "Estimate",
        30: "Estimate/Quotation",
        65: "Sales Order",
        # In this Vyapar firm, type 81 is Journal Entry (not PO / Sale Order).
        81: "Journal Entry",
        82: "Delivery Challan",
        83: "Proforma Invoice",
    }
    return labels.get(int(txn_type or 0), f"Txn Type {txn_type}")


def _status_label(status_code: Any) -> str:
    try:
        code = int(status_code)
    except Exception:
        return "Unknown"
    return {
        1: "Open",
        2: "Approved",
        3: "Draft",
        4: "Cancelled",
    }.get(code, f"Status {code}")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _num_or_none(value: Any) -> float | None:
    """Parse number; treat missing/blank as None. Zero is a valid value (e.g. Paid balance)."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any, default: float = 0.0) -> float:
    n = _num_or_none(value)
    return default if n is None else n


def _vyapar_txn_amount(t: dict[str, Any], paid_mapped: float = 0.0) -> float:
    """Invoice/doc total as shown in Vyapar Sale Invoices.

    Critical: txn_balance_amount is the *bill total* (not only 'due'). When the bill
    is paid, current_balance=0 but balance_amount often still holds the original total.
    Tax-inclusive bills (txn_tax_inclusive=2) must NOT use line+tax — that double-counts.
    """
    bal = _num(t.get("txn_balance_amount"))
    if bal > 0.009:
        return bal
    mapped = max(0.0, float(paid_mapped or 0))
    if mapped > 0.009:
        return mapped
    cur = _num_or_none(t.get("txn_current_balance"))
    if cur is not None and cur > 0.009:
        return cur
    line = _num(t.get("line_total"))
    tax = _num(t.get("line_tax_total")) or _num(t.get("txn_tax_amount"))
    round_off = _num(t.get("txn_round_off_amount"))
    inclusive = int(t.get("txn_tax_inclusive") or 0) in (1, 2)
    if line > 0.009:
        if inclusive:
            return line + round_off
        return line + tax + round_off
    cash = abs(_num(t.get("txn_cash_amount"))) or abs(_num(t.get("txn_received_amount"))) or abs(
        _num(t.get("txn_paid_amount"))
    )
    if cash > 0.009:
        return cash
    return max(bal, line)


def _vyapar_due_balance(t: dict[str, Any], amount: float, paid_from_mapping: float) -> float:
    """Remaining due. CRITICAL: current_balance=0 means Paid — must not fall through via `or`."""
    # Journals are adjustments, not open bills — never store a "due" on the journal row.
    if int(t.get("txn_type") or 0) == 81:
        return 0.0
    cur = _num_or_none(t.get("txn_current_balance"))
    if cur is not None:
        return max(0.0, cur)
    paid = max(0.0, paid_from_mapping)
    if paid > 0.009 and amount > 0.009:
        return max(0.0, amount - paid)
    bal = _num_or_none(t.get("txn_balance_amount"))
    if bal is not None:
        if paid > 0.009 and abs(bal - amount) < 0.05:
            return max(0.0, amount - paid)
        return max(0.0, bal)
    return max(0.0, amount - paid)


def _vyapar_status_label(
    raw_status: Any,
    due_amt: float,
    amount: float,
    *,
    txn_type: int = 0,
    paid_mapped: float = 0.0,
) -> str:
    """Partial only when money was actually received against the bill."""
    base = _status_label(raw_status)
    # Estimates / orders / proforma — not payment-tracked like invoices.
    # Journals: Posted (write-off / adjustment), never Partial/Paid.
    if txn_type in (27, 30, 65, 83):
        return base or "Approved"
    if txn_type == 81:
        return "Posted"
    if due_amt <= 0.009 and amount > 0.009:
        return "Paid"
    # Never mark Partial just because line+tax inflated total > balance.
    if float(paid_mapped or 0) > 0.009 and 0.009 < due_amt < amount - 0.05:
        return "Partial"
    return base or "Open"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _load_txn_type_prefixes(conn: sqlite3.Connection) -> dict[int, str]:
    """Best-effort map of Vyapar txn_type -> invoice/doc prefix."""
    prefixes: dict[int, str] = {}
    tables = _table_names(conn)

    # 1) Any table that looks like a prefix / number-series table
    for tname in tables:
        low = tname.lower()
        if not any(tok in low for tok in ("prefix", "series", "number_series", "txn_number")):
            continue
        cols = {c.lower(): c for c in _table_cols(conn, tname)}
        type_col = (
            cols.get("txn_type")
            or cols.get("transaction_type")
            or cols.get("txn_type_id")
            or cols.get("type_id")
            or cols.get("type")
        )
        pfx_col = (
            cols.get("prefix")
            or cols.get("invoice_prefix")
            or cols.get("number_prefix")
            or cols.get("txn_prefix")
            or cols.get("series_prefix")
        )
        if not type_col or not pfx_col:
            continue
        try:
            for row in conn.execute(f"SELECT {type_col}, {pfx_col} FROM [{tname}]").fetchall():
                try:
                    ty = int(row[0])
                except Exception:
                    continue
                pfx = _clean(row[1])
                if pfx:
                    prefixes[ty] = pfx
        except Exception:
            continue

    # 2) kb_settings keys that mention prefix + type id
    if "kb_settings" in tables:
        cols = {c.lower(): c for c in _table_cols(conn, "kb_settings")}
        key_c = cols.get("setting_key") or cols.get("key")
        val_c = cols.get("setting_value") or cols.get("value")
        if key_c and val_c:
            label_to_type = {
                "saleinvoice": 1,
                "sale_invoice": 1,
                "estimate": 27,
                "quotation": 27,
                "estimatequotation": 27,
                "proforma": 83,
                "proformainvoice": 83,
                "salesorder": 65,
                "saleorder": 65,
                "purchaseorder": 66,
                "purchase_order": 66,
                "journal": 81,
                "journalentry": 81,
                "deliverychallan": 82,
                "challan": 82,
                "salereturn": 21,
                "creditnote": 21,
                "paymentin": 3,
                "paymentout": 4,
            }
            try:
                for row in conn.execute(f"SELECT {key_c}, {val_c} FROM kb_settings").fetchall():
                    key = str(row[0] or "")
                    val = _clean(row[1])
                    if not val or "prefix" not in key.lower():
                        continue
                    m = re.search(r"(?:txn[_\s-]?type|type)[_\s-]?(\d+)|(?:^|[_\-.])(\d+)(?:$|[_\-.])", key, re.I)
                    if m:
                        ty = int(m.group(1) or m.group(2))
                        prefixes.setdefault(ty, val)
                        continue
                    low = re.sub(r"[^a-z0-9]+", "", key.lower())
                    for token, ty in label_to_type.items():
                        if token in low:
                            prefixes.setdefault(ty, val)
                            break
            except Exception:
                pass
    return prefixes


def _infer_prefixes_from_refs(txns: list[dict[str, Any]]) -> dict[int, str]:
    """Infer prefix from full refs already present (e.g. HOPPI327 → HOPPI)."""
    by_type: dict[int, list[str]] = {}
    for t in txns:
        ty = int(t.get("txn_type") or 0)
        ref = _clean(t.get("txn_ref_number_char") or t.get("doc_number"))
        if not ref or not re.search(r"[A-Za-z]", ref):
            continue
        m = re.match(r"^(.*?)(\d+)$", ref)
        if m and _clean(m.group(1)):
            by_type.setdefault(ty, []).append(m.group(1))
    out: dict[int, str] = {}
    for ty, cands in by_type.items():
        if cands:
            out[ty] = Counter(cands).most_common(1)[0][0]
    return out


def _compose_doc_number(ref: str, txn_type: int, prefixes: dict[int, str]) -> str:
    """Return full document number (prefix + sequence) when possible."""
    ref = _clean(ref)
    if not ref:
        return ""
    # Already a full-looking number (letters and/or slash path)
    if re.search(r"[A-Za-z]", ref) or "/" in ref:
        return ref
    pfx = _clean(prefixes.get(int(txn_type or 0), ""))
    if not pfx:
        return ref
    if ref.startswith(pfx):
        return ref
    return f"{pfx}{ref}"


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


def _update_if_empty(update_fn, conn, workspace_id, existing: dict, new_data: dict):
    """Fill in blank fields on an existing record with data from Vyapar."""
    patch: dict[str, Any] = {}
    for field in ("mobile", "email", "city", "address", "gst_no"):
        old_val = _clean(existing.get(field))
        new_val = _clean(new_data.get(field))
        if not old_val and new_val:
            patch[field] = new_val
    if _clean(existing.get("products")) == "Imported from Vyapar":
        patch["products"] = ""
    if patch:
        try:
            update_fn(conn, workspace_id, int(existing["id"]), patch)
        except Exception:
            pass


def _parse_city_from_address(address: str) -> str:
    """Try to extract city name from a full Indian address string."""
    if not address:
        return ""
    parts = [p.strip() for p in address.replace("\n", ",").split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return ""


def _fetch_parties(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT n.name_id, n.full_name, n.phone_number, n.email, n.name_type,
               n.name_group_id, n.name_gstin_number, n.name_state, n.address, n.pincode
        FROM kb_names n
        WHERE n.name_type = 1 AND trim(coalesce(n.full_name, '')) != ''
        ORDER BY n.name_id
        """
    ).fetchall()
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["name_id"] in seen:
            continue
        seen.add(r["name_id"])
        party = dict(r)
        addr_row = conn.execute(
            "SELECT address, city, pincode, state_name FROM kb_address WHERE name_id=? ORDER BY address_id DESC LIMIT 1",
            (r["name_id"],),
        ).fetchone()
        if addr_row:
            if _clean(addr_row["address"]):
                party["address"] = _clean(addr_row["address"])
            if _clean(addr_row["city"]):
                party["_city"] = _clean(addr_row["city"])
            if _clean(addr_row["pincode"]):
                party["pincode"] = _clean(addr_row["pincode"])
        if not party.get("_city"):
            party["_city"] = _parse_city_from_address(_clean(party.get("address")))
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
    cols = set(_table_cols(conn, "kb_transactions"))
    select_cols = [
        "t.txn_id",
        "t.txn_type",
        "t.txn_date",
        "t.txn_due_date",
        "t.txn_ref_number_char",
        "t.txn_display_name",
        "t.txn_description",
        "t.txn_balance_amount",
        "t.txn_current_balance",
        "t.txn_tax_amount",
        "t.txn_discount_amount",
        "t.txn_name_id",
        "t.txn_status",
    ]
    # Optional columns that may hold a fuller document number / prefix / cash
    for opt in (
        "additional_details_json",
        "txn_prefix",
        "invoice_prefix",
        "prefix",
        "full_ref_number",
        "txn_full_ref_number",
        "bill_number",
        "invoice_number",
        "txn_cash_amount",
        "txn_received_amount",
        "txn_paid_amount",
        "txn_tax_inclusive",
        "txn_round_off_amount",
    ):
        if opt in cols:
            select_cols.append(f"t.{opt}")

    sql = f"""
        SELECT {", ".join(select_cols)},
               n.full_name AS party_name, n.name_group_id
        FROM kb_transactions t
        LEFT JOIN kb_names n ON n.name_id = t.txn_name_id
        WHERE trim(coalesce(t.txn_ref_number_char, '')) != '' OR t.txn_name_id IS NOT NULL
        ORDER BY t.txn_id
    """
    rows = conn.execute(sql).fetchall()
    schema_prefixes = _load_txn_type_prefixes(conn)
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        sums = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(lineitem_tax_amount),0) FROM kb_lineitems WHERE lineitem_txn_id=?",
            (r["txn_id"],),
        ).fetchone()
        d["line_total"] = float(sums[0] or 0)
        d["line_tax_total"] = float(sums[1] or 0)
        d["txn_label"] = _txn_label(int(d.get("txn_type") or 0))
        d["status_label"] = _status_label(d.get("txn_status"))

        # Prefer any explicit prefix column on the row itself
        row_pfx = _clean(d.get("txn_prefix") or d.get("invoice_prefix") or d.get("prefix"))
        raw_ref = _clean(
            d.get("full_ref_number")
            or d.get("txn_full_ref_number")
            or d.get("invoice_number")
            or d.get("bill_number")
            or d.get("txn_ref_number_char")
        )
        # Parse JSON blob if present
        blob = d.get("additional_details_json")
        if blob and not re.search(r"[A-Za-z/]", raw_ref):
            try:
                import json

                j = json.loads(blob) if isinstance(blob, str) else blob
                if isinstance(j, dict):
                    for k in (
                        "invoiceNumber",
                        "invoice_number",
                        "fullNumber",
                        "full_number",
                        "txnNumber",
                        "number",
                        "billNumber",
                        "refNumber",
                    ):
                        if _clean(j.get(k)):
                            raw_ref = _clean(j.get(k))
                            break
                    if not row_pfx:
                        row_pfx = _clean(j.get("prefix") or j.get("invoicePrefix") or j.get("numberPrefix"))
            except Exception:
                pass

        ty = int(d.get("txn_type") or 0)
        local_prefixes = dict(schema_prefixes)
        if row_pfx:
            local_prefixes[ty] = row_pfx
        d["doc_number"] = _compose_doc_number(raw_ref, ty, local_prefixes)
        d["txn_ref_number_char"] = d["doc_number"] or raw_ref
        out.append(d)

    # Second pass: infer prefixes from full numbers already present, fill remaining short refs.
    inferred = _infer_prefixes_from_refs(out)
    merged = {**schema_prefixes, **inferred}
    for d in out:
        ty = int(d.get("txn_type") or 0)
        current = _clean(d.get("doc_number") or d.get("txn_ref_number_char"))
        full = _compose_doc_number(current, ty, merged)
        d["doc_number"] = full
        d["txn_ref_number_char"] = full
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
                        "txn_label": _clean(t.get("txn_label")),
                        "status": _clean(t.get("status_label")),
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
        existing_products_by_code = {
            _clean(r.get("code")).lower(): r
            for r in hop_ops_module.list_products(target_conn, workspace_id)
            if _clean(r.get("code"))
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
            "quotations_created": 0,
            "quotations_skipped": 0,
            "payments_created": 0,
            "party_txns_created": 0,
            "party_txns_skipped": 0,
            "party_fuzzy_matched": 0,
            "errors": [],
        }

        from app.hop_party_match import resolve_existing_party

        def _lookup_in_maps(existing_row: dict, prefer: str) -> dict | None:
            if not existing_row:
                return None
            maps = existing_vendors if prefer == "vendor" else existing_customers
            eid = int(existing_row.get("id") or 0)
            for row in maps.values():
                if int(row.get("id") or 0) == eid:
                    return row
            key2 = _clean(existing_row.get("company")).lower()
            return maps.get(key2)

        for p in parties:
            name = _clean(p.get("full_name"))
            if not name:
                continue
            key = name.lower()
            city = _clean(p.get("_city")) or _clean(p.get("name_state"))
            addr = _clean(p.get("address"))
            pincode = _clean(p.get("pincode"))
            if pincode and addr and pincode not in addr:
                addr = f"{addr}, {pincode}"
            payload = {
                "company": name,
                "contact_person": name if len(name.split()) <= 4 else "",
                "mobile": _digits(p.get("phone_number")),
                "email": _clean(p.get("email")),
                "city": city,
                "address": addr,
                "gst_no": _clean(p.get("name_gstin_number")).upper(),
            }
            prefer = "vendor" if p.get("name_group_id") == 2 else "customer"
            try:
                existing, match_kind = resolve_existing_party(
                    target_conn,
                    workspace_id,
                    company=name,
                    gst_no=payload["gst_no"],
                    mobile=payload["mobile"],
                    prefer_type=prefer,
                    customers_by_key=existing_customers,
                    vendors_by_key=existing_vendors,
                )
                hit = _lookup_in_maps(existing, prefer) if existing else None
                if hit:
                    if prefer == "vendor":
                        _update_if_empty(hop_ops_module.update_vendor, target_conn, workspace_id, hit, payload)
                        existing_vendors[key] = hit
                        out["vendors_skipped"] += 1
                    else:
                        _update_if_empty(hop_db_module.update_customer, target_conn, workspace_id, hit, payload)
                        existing_customers[key] = hit
                        out["customers_skipped"] += 1
                    if match_kind and match_kind != "exact":
                        out["party_fuzzy_matched"] += 1
                    continue
                if prefer == "vendor":
                    payload["products"] = ""
                    payload["remarks"] = "Source: Vyapar Import"
                    payload["rating"] = 3
                    row = hop_ops_module.create_vendor(target_conn, workspace_id, payload)
                    existing_vendors[key] = row
                    out["vendors_created"] += 1
                else:
                    payload["customer_type"] = _guess_customer_type(name)
                    payload["status"] = "active"
                    payload["source"] = "Vyapar Import"
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
            code = _clean(i.get("item_code"))
            code_key = code.lower() if code else ""
            if key in existing_products or (code_key and code_key in existing_products_by_code):
                out["products_skipped"] += 1
                continue
            payload = {
                "name": name,
                "code": code,
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
                if code_key:
                    existing_products_by_code[code_key] = row
                out["products_created"] += 1
            except Exception as exc:
                out["errors"].append(f"Item '{name}': {exc}")

        def _txn_id_from_notes(notes: Any) -> int | None:
            m = re.search(r"Vyapar txn\s+(\d+)", _clean(notes), flags=re.I)
            if not m:
                m = re.search(r"txn_payment_mapping for txn\s+(\d+)", _clean(notes), flags=re.I)
            if not m:
                return None
            try:
                return int(m.group(1))
            except ValueError:
                return None

        existing_invoices = {
            f"{_clean(r.get('invoice_no')).lower()}|{_clean(r.get('invoice_date'))[:10]}": r
            for r in hop_ops_module.list_invoices(target_conn, workspace_id)
            if _clean(r.get("invoice_no"))
        }
        existing_invoices_by_txn: dict[int, dict] = {}
        for r in hop_ops_module.list_invoices(target_conn, workspace_id):
            sid = r.get("source_txn_id")
            if sid is None or sid == "":
                sid = _txn_id_from_notes(r.get("notes"))
            if sid is not None:
                existing_invoices_by_txn[int(sid)] = r

        existing_quotes = {
            f"{_clean(r.get('quote_no')).lower()}|{_clean(r.get('quote_date'))[:10]}": r
            for r in hop_ops_module.list_quotations(target_conn, workspace_id)
            if _clean(r.get("quote_no"))
        }
        existing_quotes_by_txn: dict[int, dict] = {}
        for r in hop_ops_module.list_quotations(target_conn, workspace_id):
            sid = _txn_id_from_notes(r.get("notes"))
            if sid is not None:
                existing_quotes_by_txn[sid] = r

        existing_payment_txns: set[int] = set()
        for r in hop_ops_module.list_payments(target_conn, workspace_id):
            sid = r.get("source_txn_id")
            if sid is None or sid == "":
                sid = _txn_id_from_notes(r.get("notes"))
            if sid is not None:
                existing_payment_txns.add(int(sid))

        out["payments_skipped"] = 0
        out["invoices_updated"] = 0

        customer_by_name = {
            _clean(r.get("company")).lower(): r for r in hop_db_module.list_customers(target_conn, workspace_id)
        }
        vendor_by_name = {
            _clean(r.get("company")).lower(): r for r in hop_ops_module.list_vendors(target_conn, workspace_id)
        }

        existing_party_txn_ids = {
            int(r[0])
            for r in target_conn.execute(
                "SELECT source_txn_id FROM hop_party_transactions WHERE workspace_id=?",
                (workspace_id,),
            ).fetchall()
        }

        for t in txns:
            txn_id = int(t.get("txn_id") or 0)
            party_name = _clean(t.get("party_name") or t.get("txn_display_name"))
            if not party_name:
                out["invoices_skipped"] += 1
                continue
            party_type = "vendor" if int(t.get("name_group_id") or 0) == 2 else "customer"
            customer = customer_by_name.get(party_name.lower())
            vendor = vendor_by_name.get(party_name.lower())
            if party_type == "customer" and not customer:
                existing, match_kind = resolve_existing_party(
                    target_conn,
                    workspace_id,
                    company=party_name,
                    prefer_type="customer",
                    customers_by_key=customer_by_name,
                    vendors_by_key=vendor_by_name,
                )
                if existing:
                    eid = int(existing.get("id") or 0)
                    for c in customer_by_name.values():
                        if int(c.get("id") or 0) == eid:
                            customer = c
                            break
                    if customer is None:
                        customer = existing
                    customer_by_name[party_name.lower()] = customer
                    if match_kind and match_kind != "exact":
                        out["party_fuzzy_matched"] += 1
                else:
                    try:
                        customer = hop_db_module.create_customer(
                            target_conn,
                            workspace_id,
                            {
                                "company": party_name,
                                "contact_person": party_name if len(party_name.split()) <= 4 else "",
                                "customer_type": _guess_customer_type(party_name),
                                "status": "active",
                                "source": "Vyapar Import",
                            },
                        )
                        customer_by_name[party_name.lower()] = customer
                    except Exception as exc:
                        out["errors"].append(f"Txn {txn_id}: customer create failed ({exc})")
            elif party_type == "vendor" and not vendor:
                existing, match_kind = resolve_existing_party(
                    target_conn,
                    workspace_id,
                    company=party_name,
                    prefer_type="vendor",
                    customers_by_key=customer_by_name,
                    vendors_by_key=vendor_by_name,
                )
                if existing:
                    eid = int(existing.get("id") or 0)
                    for v in vendor_by_name.values():
                        if int(v.get("id") or 0) == eid:
                            vendor = v
                            break
                    if vendor is None:
                        vendor = existing
                    vendor_by_name[party_name.lower()] = vendor
                    if match_kind and match_kind != "exact":
                        out["party_fuzzy_matched"] += 1
                else:
                    try:
                        vendor = hop_ops_module.create_vendor(
                            target_conn,
                            workspace_id,
                            {
                                "company": party_name,
                                "contact_person": party_name if len(party_name.split()) <= 4 else "",
                                "products": "",
                                "remarks": "Source: Vyapar Import",
                                "rating": 3,
                            },
                        )
                        vendor_by_name[party_name.lower()] = vendor
                    except Exception as exc:
                        out["errors"].append(f"Txn {txn_id}: vendor create failed ({exc})")

            paid_mapped = float(txn_payments.get(txn_id, 0) or 0)
            amount = _vyapar_txn_amount(t, paid_mapped)
            due_amt = _vyapar_due_balance(t, amount, paid_mapped)
            inv_no = _clean(t.get("doc_number") or t.get("txn_ref_number_char")) or f"VYP-{txn_id}"
            inv_date = _clean(t.get("txn_date"))
            note = _clean(t.get("txn_description"))
            txn_label = _clean(t.get("txn_label"))
            status_label = _vyapar_status_label(
                t.get("txn_status"),
                due_amt,
                amount,
                txn_type=int(t.get("txn_type") or 0),
                paid_mapped=paid_mapped,
            )
            inv_date_key = inv_date[:10] if inv_date else ""
            party_id_val = (customer or {}).get("id") if party_type == "customer" else (vendor or {}).get("id")
            # Upsert party transaction — always refresh amount/balance so Paid status stays correct.
            try:
                if txn_id in existing_party_txn_ids:
                    target_conn.execute(
                        """
                        UPDATE hop_party_transactions SET
                            party_type=?, party_id=?, party_name=?,
                            txn_type=?, txn_label=?, txn_number=?, txn_date=?,
                            total_amount=?, balance_amount=?, status_text=?, notes=?,
                            updated_at=datetime('now')
                        WHERE workspace_id=? AND source_txn_id=?
                        """,
                        (
                            party_type,
                            party_id_val,
                            party_name,
                            int(t.get("txn_type") or 0),
                            txn_label,
                            inv_no,
                            inv_date_key,
                            amount,
                            due_amt,
                            status_label,
                            note,
                            workspace_id,
                            txn_id,
                        ),
                    )
                    out["party_txns_skipped"] += 1  # counted as refreshed existing
                else:
                    try:
                        target_conn.execute(
                            """
                            INSERT INTO hop_party_transactions (
                                workspace_id, party_type, party_id, party_name, source_txn_id,
                                txn_type, txn_label, txn_number, txn_date, total_amount,
                                balance_amount, status_text, notes, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                            """,
                            (
                                workspace_id,
                                party_type,
                                party_id_val,
                                party_name,
                                txn_id,
                                int(t.get("txn_type") or 0),
                                txn_label,
                                inv_no,
                                inv_date_key,
                                amount,
                                due_amt,
                                status_label,
                                note,
                            ),
                        )
                        existing_party_txn_ids.add(txn_id)
                        out["party_txns_created"] += 1
                    except sqlite3.IntegrityError:
                        # Re-import race / already present — update instead of duplicating.
                        target_conn.execute(
                            """
                            UPDATE hop_party_transactions SET
                                party_type=?, party_id=?, party_name=?,
                                txn_type=?, txn_label=?, txn_number=?, txn_date=?,
                                total_amount=?, balance_amount=?, status_text=?, notes=?,
                                updated_at=datetime('now')
                            WHERE workspace_id=? AND source_txn_id=?
                            """,
                            (
                                party_type,
                                party_id_val,
                                party_name,
                                int(t.get("txn_type") or 0),
                                txn_label,
                                inv_no,
                                inv_date_key,
                                amount,
                                due_amt,
                                status_label,
                                note,
                                workspace_id,
                                txn_id,
                            ),
                        )
                        existing_party_txn_ids.add(txn_id)
                        out["party_txns_skipped"] += 1
            except Exception as exc:
                out["errors"].append(f"Txn {txn_id}: party transaction import failed ({exc})")

            # Route each Vyapar type to its correct native module (ledger already stored above).
            txn_type = int(t.get("txn_type") or 0)
            if party_type != "customer" or not customer:
                out["invoices_skipped"] += 1
                continue

            tax_amt = _num(t.get("line_tax_total")) or _num(t.get("txn_tax_amount"))
            # Paid amount ONLY from Vyapar txn_payment_mapping — never invent from total−due.
            paid_amt = max(0.0, float(paid_mapped or 0))
            due = _clean(t.get("txn_due_date"))
            inv_key = f"{inv_no.lower()}|{inv_date_key}"
            inv_status = "paid" if due_amt <= 0.009 else ("partial" if paid_amt > 0.009 else "open")
            inv_notes = f"[{txn_label}] Imported from Vyapar txn {txn_id}. {note}".strip()
            if txn_type in QUOTATION_TYPES:
                existing_q = existing_quotes_by_txn.get(txn_id) or existing_quotes.get(inv_key)
                if existing_q:
                    try:
                        qid = existing_q.get("id")
                        if qid:
                            target_conn.execute(
                                """
                                UPDATE hop_quotations SET
                                    value=?, notes=?, quote_date=?, updated_at=datetime('now')
                                WHERE workspace_id=? AND id=?
                                """,
                                (amount, inv_notes, inv_date_key, workspace_id, qid),
                            )
                    except Exception as exc:
                        out["errors"].append(f"Txn {txn_id}: quotation refresh failed ({exc})")
                    out["quotations_skipped"] += 1
                    continue
                try:
                    quote = hop_ops_module.create_quotation(
                        target_conn,
                        workspace_id,
                        {
                            "quote_no": inv_no,
                            "customer_id": customer.get("id"),
                            "quote_date": inv_date_key,
                            "value": amount,
                            "status": "sent",
                            "notes": inv_notes,
                        },
                    )
                    existing_quotes[inv_key] = quote
                    existing_quotes_by_txn[txn_id] = quote
                    out["quotations_created"] += 1
                except Exception as exc:
                    out["errors"].append(f"Txn {txn_id}: quotation import failed ({exc})")
                    out["quotations_skipped"] += 1
                continue

            if txn_type not in INVOICE_TYPES:
                # Proforma / Sale Return / Sale Order / Challan / Journal stay in party ledger only.
                # Never create hop_invoices / hop_payments for these — no synthetic settlements.
                out["invoices_skipped"] += 1
                continue

            existing_inv = existing_invoices_by_txn.get(txn_id) or existing_invoices.get(inv_key)
            if existing_inv:
                try:
                    iid = existing_inv.get("id")
                    if iid:
                        target_conn.execute(
                            """
                            UPDATE hop_invoices SET
                                invoice_no=?, amount=?, paid_amount=?, balance=?, status=?,
                                gst_amount=?, notes=?, invoice_date=?, source_txn_id=?,
                                customer_id=COALESCE(?, customer_id),
                                updated_at=datetime('now')
                            WHERE workspace_id=? AND id=?
                            """,
                            (
                                inv_no,
                                amount,
                                paid_amt,
                                due_amt,
                                inv_status,
                                tax_amt,
                                inv_notes,
                                inv_date_key,
                                txn_id,
                                customer.get("id"),
                                workspace_id,
                                iid,
                            ),
                        )
                        existing_inv = dict(existing_inv)
                        existing_inv.update(
                            {
                                "id": iid,
                                "invoice_no": inv_no,
                                "amount": amount,
                                "paid_amount": paid_amt,
                                "balance": due_amt,
                                "status": inv_status,
                                "source_txn_id": txn_id,
                            }
                        )
                        existing_invoices[inv_key] = existing_inv
                        existing_invoices_by_txn[txn_id] = existing_inv
                    out["invoices_updated"] += 1
                    out["invoices_skipped"] += 1
                except Exception as exc:
                    out["errors"].append(f"Txn {txn_id}: invoice refresh failed ({exc})")
                # Do NOT create another payment on refresh — totals already set from Vyapar.
                if txn_id in existing_payment_txns:
                    out["payments_skipped"] += 1
                continue

            try:
                # If we will insert a hop_payments row, keep invoice paid=0 so create_payment
                # doesn't double-count paid_amount.
                will_add_payment = paid_amt > 0.009 and txn_id not in existing_payment_txns
                inv = hop_ops_module.create_invoice(
                    target_conn,
                    workspace_id,
                    {
                        "invoice_no": inv_no,
                        "customer_id": customer.get("id"),
                        "amount": amount,
                        "paid_amount": 0.0 if will_add_payment else paid_amt,
                        "due_date": due,
                        "invoice_date": inv_date_key,
                        "gst_amount": tax_amt,
                        "status": ("open" if will_add_payment else inv_status),
                        "notes": inv_notes,
                        "source_txn_id": txn_id,
                    },
                )
                existing_invoices[inv_key] = inv
                existing_invoices_by_txn[txn_id] = inv
                out["invoices_created"] += 1
                if will_add_payment:
                    try:
                        hop_ops_module.create_payment(
                            target_conn,
                            workspace_id,
                            {
                                "invoice_id": inv.get("id"),
                                "amount": paid_amt,
                                "paid_at": inv_date_key,
                                "method": "imported",
                                "notes": f"Imported from txn_payment_mapping for txn {txn_id}",
                                "customer_id": customer.get("id"),
                                "source_txn_id": txn_id,
                            },
                        )
                        existing_payment_txns.add(txn_id)
                        out["payments_created"] += 1
                    except Exception as exc:
                        out["payments_skipped"] += 1
                        # Restore invoice totals if payment insert failed.
                        try:
                            target_conn.execute(
                                """
                                UPDATE hop_invoices SET paid_amount=?, balance=?, status=?,
                                    updated_at=datetime('now')
                                WHERE workspace_id=? AND id=?
                                """,
                                (paid_amt, due_amt, inv_status, workspace_id, inv.get("id")),
                            )
                        except Exception:
                            pass
                        if "unique" not in str(exc).lower():
                            out["errors"].append(f"Txn {txn_id}: payment import failed ({exc})")
                elif paid_amt > 0.009:
                    out["payments_skipped"] += 1
            except Exception as exc:
                msg = str(exc).lower()
                if "unique" in msg or "source_txn" in msg:
                    out["invoices_skipped"] += 1
                else:
                    out["errors"].append(f"Txn {txn_id}: invoice import failed ({exc})")
                    out["invoices_skipped"] += 1

        out["source_items"] = len(items)
        out["source_parties"] = len(parties)
        out["source_transactions"] = len(txns)

        # Firm letterhead + line items for Vyapar-style document preview.
        out["txn_lines_imported"] = 0
        try:
            from app.hop_doc_preview import replace_txn_lines, upsert_firm_profile
            from app.services.vyapar_line_items import fetch_all_line_items, fetch_firm_profile

            firm = fetch_firm_profile(src)
            if firm.get("firm_name") or firm.get("gstin"):
                upsert_firm_profile(target_conn, workspace_id, firm)
            lines_by_txn = fetch_all_line_items(src)
            for txn_id, lines in lines_by_txn.items():
                out["txn_lines_imported"] += replace_txn_lines(
                    target_conn, workspace_id, int(txn_id), lines
                )
        except Exception as exc:
            out["errors"].append(f"Document preview data import failed ({exc})")

        target_conn.commit()
        out["imported_ok"] = (
            out["customers_created"]
            + out["vendors_created"]
            + out["products_created"]
            + out["invoices_created"]
            + out["quotations_created"]
            + out["payments_created"]
            + out["party_txns_created"]
            + out["txn_lines_imported"]
        )
        return out
    finally:
        src.close()
        src_path.unlink(missing_ok=True)
