"""Count where a HoP party is referenced — used before delete confirmation."""

from __future__ import annotations

import sqlite3
from typing import Any


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0


def _safe_samples(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            if hasattr(r, "keys"):
                out.append(dict(r))
            else:
                out.append({"label": str(r[0]) if r else ""})
        return out
    except sqlite3.Error:
        return []


def _format_summary(counts: dict[str, int]) -> str:
    parts = []
    labels = {
        "deals": "deal(s)",
        "leads": "lead(s)",
        "projects": "project(s)",
        "quotations": "quotation(s)",
        "invoices": "invoice(s)",
        "payments": "payment(s)",
        "orders": "order(s)",
        "meetings": "meeting(s)",
        "samples": "sample(s)",
        "complaints": "complaint(s)",
        "activities": "activit(ies)",
        "party_transactions": "transaction(s)",
        "rate_sheets": "rate sheet(s)",
        "vendor_comparisons": "vendor comparison(s)",
        "products": "product(s)",
    }
    for key, label in labels.items():
        n = int(counts.get(key) or 0)
        if n:
            parts.append(f"{n} {label}")
    return ", ".join(parts) if parts else ""


# Columns to nullify when force-deleting a customer (keeps history rows).
CUSTOMER_NULLIFY = [
    ("hop_deals", "customer_id"),
    ("hop_leads", "customer_id"),
    ("hop_projects", "customer_id"),
    ("hop_quotations", "customer_id"),
    ("hop_invoices", "customer_id"),
    ("hop_payments", "customer_id"),
    ("hop_orders", "customer_id"),
    ("hop_meetings", "customer_id"),
    ("hop_samples", "customer_id"),
    ("hop_complaints", "customer_id"),
    ("hop_activities", "customer_id"),
]

VENDOR_NULLIFY = [
    ("hop_vendor_comparisons", "vendor_id"),
    ("hop_products", "vendor_id"),
    ("hop_rate_sheets", "vendor_id"),
    ("hop_orders", "vendor_id"),
]


def get_customer_usage(
    conn: sqlite3.Connection, workspace_id: str, customer_id: int
) -> dict[str, Any]:
    cid = int(customer_id)
    ws = workspace_id
    counts = {
        "deals": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_deals WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "leads": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_leads WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "projects": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_projects WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "quotations": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_quotations WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "invoices": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_invoices WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "payments": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_payments WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "orders": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_orders WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "meetings": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_meetings WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "samples": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_samples WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "complaints": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_complaints WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "activities": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_activities WHERE workspace_id=? AND customer_id=?", (ws, cid)
        ),
        "party_transactions": _safe_count(
            conn,
            "SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=? AND party_type='customer' AND party_id=?",
            (ws, cid),
        ),
    }
    total = sum(counts.values())
    samples = {
        "deals": _safe_samples(
            conn,
            """SELECT id, deal_number, title, status FROM hop_deals
               WHERE workspace_id=? AND customer_id=? ORDER BY id DESC LIMIT 5""",
            (ws, cid),
        ),
        "party_transactions": _safe_samples(
            conn,
            """SELECT id, txn_number, txn_label, txn_type FROM hop_party_transactions
               WHERE workspace_id=? AND party_type='customer' AND party_id=?
               ORDER BY id DESC LIMIT 5""",
            (ws, cid),
        ),
    }
    return {
        "party_id": cid,
        "party_type": "customer",
        "total": total,
        "in_use": total > 0,
        "counts": counts,
        "summary": _format_summary(counts),
        "samples": samples,
    }


def get_vendor_usage(
    conn: sqlite3.Connection, workspace_id: str, vendor_id: int
) -> dict[str, Any]:
    vid = int(vendor_id)
    ws = workspace_id
    counts = {
        "rate_sheets": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_rate_sheets WHERE workspace_id=? AND vendor_id=?", (ws, vid)
        ),
        "vendor_comparisons": _safe_count(
            conn,
            "SELECT COUNT(*) FROM hop_vendor_comparisons WHERE workspace_id=? AND vendor_id=?",
            (ws, vid),
        ),
        "products": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_products WHERE workspace_id=? AND vendor_id=?", (ws, vid)
        ),
        "orders": _safe_count(
            conn, "SELECT COUNT(*) FROM hop_orders WHERE workspace_id=? AND vendor_id=?", (ws, vid)
        ),
        "party_transactions": _safe_count(
            conn,
            "SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=? AND party_type='vendor' AND party_id=?",
            (ws, vid),
        ),
    }
    total = sum(counts.values())
    return {
        "party_id": vid,
        "party_type": "vendor",
        "total": total,
        "in_use": total > 0,
        "counts": counts,
        "summary": _format_summary(counts),
        "samples": {},
    }


def nullify_customer_refs(conn: sqlite3.Connection, workspace_id: str, customer_id: int) -> None:
    cid = int(customer_id)
    for table, col in CUSTOMER_NULLIFY:
        try:
            conn.execute(
                f"UPDATE {table} SET {col}=NULL WHERE workspace_id=? AND {col}=?",
                (workspace_id, cid),
            )
        except sqlite3.Error:
            pass


def nullify_vendor_refs(conn: sqlite3.Connection, workspace_id: str, vendor_id: int) -> None:
    vid = int(vendor_id)
    for table, col in VENDOR_NULLIFY:
        try:
            conn.execute(
                f"UPDATE {table} SET {col}=NULL WHERE workspace_id=? AND {col}=?",
                (workspace_id, vid),
            )
        except sqlite3.Error:
            pass


class PartyInUseError(ValueError):
    """Raised when delete is blocked pending confirmation."""

    def __init__(self, usage: dict[str, Any]):
        self.usage = usage
        summary = usage.get("summary") or "linked records"
        super().__init__(
            f'This party is used in {summary}. Confirm delete to remove the party link from those records.'
        )
