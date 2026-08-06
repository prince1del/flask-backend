"""Canonical full document numbers (e.g. HOP/2026-27/110) for HoP / Vyapar docs."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# House of Prizm series prefixes (Vyapar UI / expense refs use HOP/FY/serial for sales).
HOP_DOC_PREFIX_BY_TYPE: dict[int, str] = {
    1: "HOP",  # Sale Invoice — expense text: HOP/2026-27/110
    27: "HOPPI",  # Estimate / Quotation
    83: "HOPPR",  # Proforma Invoice
    3: "RCPT",  # Payment-In
    65: "HOPSO",  # Sale Order
    30: "HOPDC",  # Delivery Challan
    82: "HOPDC",
    21: "HOPCN",  # Sale Return / Credit Note
}

_SHORT_NUMERIC_RE = re.compile(r"^\d{1,6}$")
_ALREADY_FULL_RE = re.compile(r"[A-Za-z].*/|/.*\d{2,4}")


def indian_fy_label(ymd: str | None) -> str | None:
    """Indian FY label from date: 2026-07-29 → 2026-27."""
    s = (ymd or "")[:10]
    if len(s) < 7:
        return None
    try:
        y = int(s[0:4])
        m = int(s[5:7])
    except ValueError:
        return None
    start = y if m >= 4 else y - 1
    return f"{start}-{str(start + 1)[-2:]}"


def format_full_doc_number(
    raw: str | None,
    *,
    txn_date: str | None = None,
    txn_type: int | None = None,
    prefix: str | None = None,
) -> str:
    """Expand short serials to PREFIX/FY/serial; leave already-full numbers alone."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if _ALREADY_FULL_RE.search(s) or "/" in s:
        return s
    if not _SHORT_NUMERIC_RE.match(s):
        # Prefix+digits without slash (legacy HOPPI12) — leave unless we know type+date
        if re.search(r"[A-Za-z]", s):
            return s
        return s
    ty = int(txn_type or 0)
    pfx = (prefix or "").strip() or HOP_DOC_PREFIX_BY_TYPE.get(ty)
    fy = indian_fy_label(txn_date)
    if not pfx or not fy:
        return s
    serial = s.lstrip("0") or s
    return f"{pfx}/{fy}/{serial}"


def _row_dict(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    if isinstance(r, dict):
        return r
    if isinstance(r, sqlite3.Row):
        return dict(r)
    # Plain tuple from connection without row_factory — unsupported here
    try:
        return dict(r)
    except Exception:
        return {}


def backfill_full_doc_numbers(
    conn: sqlite3.Connection,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Rewrite short txn / invoice numbers to full PREFIX/FY/serial form."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    updated_txn = 0
    updated_inv = 0
    updated_comm = 0

    sql = """
        SELECT id, txn_type, txn_number, txn_date, workspace_id
        FROM hop_party_transactions
        WHERE txn_number IS NOT NULL AND TRIM(txn_number) != ''
    """
    params: list[Any] = []
    if workspace_id:
        sql += " AND workspace_id=?"
        params.append(workspace_id)
    rows = conn.execute(sql, params).fetchall()
    for r in rows:
        d = _row_dict(r)
        raw = str(d.get("txn_number") or "").strip()
        full = format_full_doc_number(
            raw,
            txn_date=d.get("txn_date"),
            txn_type=d.get("txn_type"),
        )
        if full and full != raw:
            conn.execute(
                "UPDATE hop_party_transactions SET txn_number=?, updated_at=datetime('now') WHERE id=?",
                (full, int(d["id"])),
            )
            updated_txn += 1

    inv_sql = """
        SELECT id, invoice_no, invoice_date, source_txn_id, workspace_id
        FROM hop_invoices
        WHERE invoice_no IS NOT NULL AND TRIM(invoice_no) != ''
    """
    inv_params: list[Any] = []
    if workspace_id:
        inv_sql += " AND workspace_id=?"
        inv_params.append(workspace_id)
    for r in conn.execute(inv_sql, inv_params).fetchall():
        d = _row_dict(r)
        raw = str(d.get("invoice_no") or "").strip()
        # Prefer date from linked party txn when available
        txn_date = d.get("invoice_date")
        sid = d.get("source_txn_id")
        if sid is not None:
            tr = conn.execute(
                """
                SELECT txn_date, txn_type, txn_number FROM hop_party_transactions
                WHERE workspace_id=? AND source_txn_id=? AND txn_type=1
                LIMIT 1
                """,
                (d.get("workspace_id"), int(sid)),
            ).fetchone()
            if tr:
                td = _row_dict(tr)
                txn_date = td.get("txn_date") or txn_date
                # If party txn already expanded, copy it
                pt_no = str(td.get("txn_number") or "").strip()
                if pt_no and ("/" in pt_no or re.search(r"[A-Za-z]", pt_no)):
                    if pt_no != raw:
                        conn.execute(
                            "UPDATE hop_invoices SET invoice_no=?, updated_at=datetime('now') WHERE id=?",
                            (pt_no, int(d["id"])),
                        )
                        updated_inv += 1
                    continue
        full = format_full_doc_number(raw, txn_date=txn_date, txn_type=1)
        if full and full != raw:
            conn.execute(
                "UPDATE hop_invoices SET invoice_no=?, updated_at=datetime('now') WHERE id=?",
                (full, int(d["id"])),
            )
            updated_inv += 1

    comm_sql = """
        SELECT id, invoice_no, invoice_date, party_txn_id, source_txn_id, workspace_id
        FROM hop_commission_entries
        WHERE invoice_no IS NOT NULL AND TRIM(invoice_no) != ''
    """
    comm_params: list[Any] = []
    if workspace_id:
        comm_sql += " AND workspace_id=?"
        comm_params.append(workspace_id)
    for r in conn.execute(comm_sql, comm_params).fetchall():
        d = _row_dict(r)
        raw = str(d.get("invoice_no") or "").strip()
        if raw.lower().startswith("comm/"):
            continue
        txn_date = d.get("invoice_date")
        full_from_txn = None
        if d.get("party_txn_id"):
            tr = conn.execute(
                "SELECT txn_number, txn_date FROM hop_party_transactions WHERE id=?",
                (int(d["party_txn_id"]),),
            ).fetchone()
            if tr:
                td = _row_dict(tr)
                full_from_txn = str(td.get("txn_number") or "").strip()
                txn_date = td.get("txn_date") or txn_date
        elif d.get("source_txn_id") is not None:
            tr = conn.execute(
                """
                SELECT txn_number, txn_date FROM hop_party_transactions
                WHERE workspace_id=? AND source_txn_id=? AND txn_type=1 LIMIT 1
                """,
                (d.get("workspace_id"), int(d["source_txn_id"])),
            ).fetchone()
            if tr:
                td = _row_dict(tr)
                full_from_txn = str(td.get("txn_number") or "").strip()
                txn_date = td.get("txn_date") or txn_date
        full = full_from_txn if (full_from_txn and "/" in full_from_txn) else format_full_doc_number(
            raw, txn_date=txn_date, txn_type=1
        )
        if full and full != raw:
            conn.execute(
                "UPDATE hop_commission_entries SET invoice_no=?, updated_at=datetime('now') WHERE id=?",
                (full, int(d["id"])),
            )
            updated_comm += 1

    conn.commit()
    return {
        "party_transactions": updated_txn,
        "invoices": updated_inv,
        "commission_entries": updated_comm,
    }
