"""Credit notes (CN) given to a distributor: returns, rate difference, schemes.

A CN lowers what the distributor still has to pay for one season + category:

    Balance to collect = Order value − Payment received − Credit notes

Invoices (CI) stay as they are. A CN can be typed in by hand or read from its
PDF (Gemini, one short call, counted in the user's daily AI limit).
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS distributor_credit_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            distributor_id INTEGER NOT NULL,
            season TEXT NOT NULL,
            category TEXT NOT NULL,
            cn_no TEXT,
            cn_date TEXT,
            amount REAL NOT NULL,
            reason TEXT,
            file_name TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cn_slot ON distributor_credit_notes "
        "(user_id, distributor_id, season, category)"
    )
    conn.commit()


def files_dir() -> Path:
    base = (os.getenv("PERSISTENT_DISK_PATH") or "").strip() or "/var/data"
    d = Path(base) / "credit-notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _row(r: sqlite3.Row | tuple) -> dict[str, Any]:
    keys = ("id", "distributor_id", "season", "category", "cn_no", "cn_date", "amount", "reason", "file_name")
    d = dict(zip(keys, r))
    d["has_file"] = bool(d.pop("file_name"))
    d["amount"] = round(float(d["amount"] or 0), 2)
    return d


def add(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    user_id: int,
    distributor_id: int,
    season: str,
    category: str,
    amount: float,
    cn_no: str | None = None,
    cn_date: str | None = None,
    reason: str | None = None,
    file_name: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    season = (season or "").strip()
    category = (category or "").strip()
    if not season or not category:
        raise ValueError("Season and category are required")
    if not amount or float(amount) <= 0:
        raise ValueError("Amount must be more than 0")
    if file_name and Path(file_name).name != file_name:
        raise ValueError("Bad file")
    ensure_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO distributor_credit_notes
            (workspace_id, user_id, distributor_id, season, category, cn_no, cn_date, amount, reason,
             file_name, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, int(user_id), int(distributor_id), season, category,
         (cn_no or "").strip() or None, (cn_date or "").strip()[:10] or None, round(float(amount), 2),
         (reason or "").strip()[:200] or None, file_name, created_by),
    )
    conn.commit()
    return get(conn, user_id=user_id, cn_id=int(cur.lastrowid)) or {}


def get(conn: sqlite3.Connection, *, user_id: int, cn_id: int) -> dict[str, Any] | None:
    ensure_schema(conn)
    r = conn.execute(
        "SELECT id, distributor_id, season, category, cn_no, cn_date, amount, reason, file_name "
        "FROM distributor_credit_notes WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (int(cn_id), int(user_id)),
    ).fetchone()
    return _row(r) if r else None


def file_name_of(conn: sqlite3.Connection, *, user_id: int, cn_id: int) -> str | None:
    ensure_schema(conn)
    r = conn.execute(
        "SELECT file_name FROM distributor_credit_notes WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (int(cn_id), int(user_id)),
    ).fetchone()
    return r[0] if r and r[0] else None


def delete(conn: sqlite3.Connection, *, user_id: int, cn_id: int) -> bool:
    ensure_schema(conn)
    n = conn.execute(
        "UPDATE distributor_credit_notes SET deleted_at = datetime('now') "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (int(cn_id), int(user_id)),
    ).rowcount
    conn.commit()
    return n > 0


def by_slot(conn: sqlite3.Connection, user_id: int) -> dict[tuple[int, str, str], list[dict[str, Any]]]:
    """Live CNs grouped by (distributor, season, category), oldest first."""
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, distributor_id, season, category, cn_no, cn_date, amount, reason, file_name "
            "FROM distributor_credit_notes WHERE user_id = ? AND deleted_at IS NULL "
            "ORDER BY COALESCE(cn_date, created_at), id",
            (int(user_id),),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        d = _row(r)
        out.setdefault((int(d["distributor_id"]), d["season"], d["category"]), []).append(d)
    return out


# ── Read a CN PDF / photo with Gemini ────────────────────────────────────────

_PROMPT = (
    "This is a credit note (CN) issued to a distributor. Return ONLY JSON: "
    '{"cn_no":"","cn_date":"YYYY-MM-DD","amount":0,"reason":"","party":"","invoice_ref":""}. '
    "amount = the final credit amount in rupees (grand total incl. GST), a number. "
    "reason = short (return, rate difference, scheme, damage…). "
    "invoice_ref = invoice/CI number it refers to if shown. Use \"\" when not on the document."
)


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def read_document(data: bytes, mime: str, *, user_id: int) -> dict[str, Any]:
    """Pull CN number, date, amount and reason out of a PDF or photo.
    One Gemini call with a small output cap; raises ValueError for the user."""
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached. Type the credit note in by hand.")
    key = gemini_api_key()
    if not key:
        raise ValueError("Document reading is not set up on the server.")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("File is too big (max 8 MB).")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [
            {"text": _PROMPT},
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 300,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    last = "Could not read the credit note."
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue  # model not available: try the next one
            last = "Could not read the credit note. Type it in by hand."
            break
        except Exception:
            last = "Could not read the credit note. Type it in by hand."
            break
        usage = out.get("usageMetadata") or {}
        agent._log_usage(
            user_id=int(user_id), feature="nexora_agent_cn_read", model=model, rounds=1,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
            total_tokens=int(usage.get("totalTokenCount") or 0), query="credit note read",
        )
        parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        fields = _parse_json("".join(p.get("text", "") for p in parts if isinstance(p, dict)))
        try:
            amount = round(float(str(fields.get("amount") or 0).replace(",", "")), 2)
        except ValueError:
            amount = 0.0
        date = str(fields.get("cn_date") or "")
        return {
            "cn_no": str(fields.get("cn_no") or "").strip(),
            "cn_date": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "",
            "amount": amount,
            "reason": str(fields.get("reason") or "").strip()[:200],
            "party": str(fields.get("party") or "").strip()[:120],
            "invoice_ref": str(fields.get("invoice_ref") or "").strip()[:40],
        }
    raise ValueError(last)
