"""HoP audit trail: who created, changed or deleted what, and when.

Recorded automatically for every successful change (POST / PUT / PATCH / DELETE) made
through ``/api/v1/hop`` — phone and web alike. Only a short, safe summary is kept (doc
number, amount, name, party); passwords, files and AI requests are never stored. For a
bill that is edited or deleted, the old number / amount / party are kept next to the new.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

SKIP = re.compile(r"/(ai|wipe|password|suggest-rate|entry-warnings|gst-files|import/[a-z]+/preview|reconcile/match|"
                  r"login|token|export|tally|pdf|preview)(/|$)")
ENTITY = {
    "party-transactions": "Document", "products": "Item", "customers": "Party", "vendors": "Vendor",
    "bank-accounts": "Bank account", "bank-entries": "Bank entry", "cheques": "Cheque", "app-settings": "Settings",
    "expense-categories": "Category", "import": "Import", "loans": "Loan", "fixed-assets": "Fixed asset",
    "stock": "Stock", "firm-profile": "Company profile", "financial-year": "Financial year", "recycle-bin": "Recycle bin",
}
SAFE_KEYS = ("txn_type", "txn_number", "txn_date", "total_amount", "amount", "received_amount", "name", "company",
             "party_name", "party_id", "customer_id", "vendor_id", "selling_price", "purchase_price", "stock_qty",
             "entry_type", "account_id", "status", "kind")
ACTION = {"POST": "created", "PUT": "changed", "PATCH": "changed", "DELETE": "deleted"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, at TEXT NOT NULL,
        user_id INTEGER, user_name TEXT, action TEXT, entity TEXT, entity_id TEXT, path TEXT,
        summary TEXT, before TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hop_audit_ws ON hop_audit_log(workspace_id, at)")


def should_log(method: str, path: str) -> bool:
    return method in ACTION and not SKIP.search(path)


def _entity(path: str) -> tuple[str, str]:
    parts = [p for p in path.split("/api/v1/hop/", 1)[-1].split("/") if p]
    if not parts:
        return "HoP", ""
    ent = ENTITY.get(parts[0], parts[0].replace("-", " ").capitalize())
    ids = [p for p in parts[1:] if p.isdigit()]
    if len(parts) > 2 and not parts[-1].isdigit():
        ent = f"{ent} · {parts[-1].replace('-', ' ')}"
    return ent, ids[0] if ids else ""


def snapshot(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    """Old values of a bill before it is edited / deleted (runs before login is checked, so the
    caller must compare ``workspace_id`` with the user's own before using it)."""
    m = re.search(r"/party-transactions/(\d+)$", path)
    if not m:
        return None
    try:
        r = conn.execute("SELECT workspace_id, txn_number, txn_type, total_amount, party_name, txn_date "
                         "FROM hop_party_transactions WHERE id=?", (int(m.group(1)),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(r) if r else None


def _summary(body: Any, result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src in (body if isinstance(body, dict) else {}, result if isinstance(result, dict) else {}):
        for k in SAFE_KEYS:
            if k in src and src[k] not in (None, "") and not isinstance(src[k], (dict, list)):
                out.setdefault(k, src[k] if not isinstance(src[k], str) else src[k][:80])
    if isinstance(body, dict) and isinstance(body.get("lines"), list):
        out["lines"] = len(body["lines"])
    if isinstance(body, dict) and isinstance(body.get("rows"), list):
        out["rows"] = len(body["rows"])
    if isinstance(body, dict) and set(body) and "app-settings" in str(body.get("_path", "")):
        out["keys"] = sorted(body)[:20]
    return out


def record(conn: sqlite3.Connection, ws: str, user: dict[str, Any], method: str, path: str,
           body: Any, result: Any, before: dict[str, Any] | None) -> None:
    ensure_schema(conn)
    ent, eid = _entity(path)
    if not eid and isinstance(result, dict):
        eid = str(result.get("party_txn_id") or result.get("id") or "")
    summ = _summary(body, result)
    if "app-settings" in path and isinstance(body, dict):
        summ = {"changed": sorted(body)[:20]}
    conn.execute(
        "INSERT INTO hop_audit_log (workspace_id, at, user_id, user_name, action, entity, entity_id, path, summary, before) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ws, datetime.now(timezone.utc).isoformat(timespec="seconds"), user.get("user_id"),
         str(user.get("username") or user.get("email") or "")[:80], ACTION[method], ent, eid,
         path.split("/api/v1/hop", 1)[-1][:120], json.dumps(summ, ensure_ascii=False, default=str)[:1500],
         json.dumps(before, ensure_ascii=False, default=str)[:800] if before else None))
    conn.commit()


def list_log(conn: sqlite3.Connection, ws: str, *, q: str = "", entity: str = "", user: str = "",
             frm: str = "", to: str = "", limit: int = 200) -> dict[str, Any]:
    ensure_schema(conn)
    sql, params = "SELECT * FROM hop_audit_log WHERE workspace_id=?", [ws]
    if entity:
        sql += " AND entity LIKE ?"
        params.append(f"{entity}%")
    if user:
        sql += " AND user_name LIKE ?"
        params.append(f"%{user}%")
    if frm:
        sql += " AND substr(at,1,10) >= ?"
        params.append(frm)
    if to:
        sql += " AND substr(at,1,10) <= ?"
        params.append(to)
    if q:
        sql += " AND (summary LIKE ? OR before LIKE ? OR entity LIKE ?)"
        params.extend([f"%{q}%"] * 3)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 1000))))]
    for r in rows:
        for k in ("summary", "before"):
            try:
                r[k] = json.loads(r[k]) if r[k] else None
            except ValueError:
                pass
        r["text"] = describe(r)
    users = [x[0] for x in conn.execute("SELECT DISTINCT user_name FROM hop_audit_log WHERE workspace_id=? AND user_name!='' "
                                        "ORDER BY user_name", (ws,))]
    return {"rows": rows, "users": users}


def describe(r: dict[str, Any]) -> str:
    """One plain line, e.g. 'ravi changed Document HOP/2026-27/5 — ₹1,200 → ₹1,500'."""
    s, b = r.get("summary") or {}, r.get("before") or {}
    what = s.get("txn_number") or b.get("txn_number") or s.get("name") or s.get("company") or s.get("party_name") or r.get("entity_id") or ""
    line = f"{r.get('user_name') or 'Someone'} {r.get('action')} {r.get('entity')} {what}".strip()
    new_amt = s.get("total_amount", s.get("amount"))
    if b and b.get("total_amount") is not None:
        if r.get("action") == "deleted":
            line += f" — was ₹{float(b['total_amount']):,.2f}"
        elif new_amt is not None and abs(float(new_amt) - float(b["total_amount"])) > 0.005:
            line += f" — ₹{float(b['total_amount']):,.2f} → ₹{float(new_amt):,.2f}"
    elif new_amt is not None:
        line += f" — ₹{float(new_amt):,.2f}"
    return line
