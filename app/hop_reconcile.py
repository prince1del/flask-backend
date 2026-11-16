"""HoP bank reconciliation: tick off book entries that appear on the bank statement.

* Each bank entry gets ``cleared_on`` (the date it shows on the statement).
* ``statement()`` — book balance vs cleared (bank) balance, with the uncleared entries.
* ``match_rows()`` — reads statement rows (CSV upload) and pairs them with uncleared entries
  of the same amount and direction within a few days. Nothing is saved until the user ticks.
* Marks survive editing the bill: when a document's money is re-posted, the new entry keeps
  the old one's cleared date (see ``hop_money._undo_money`` / ``_bank_entry``).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime
from typing import Any


def ensure_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(hop_bank_entries)")}
    if "cleared_on" not in have:
        conn.execute("ALTER TABLE hop_bank_entries ADD COLUMN cleared_on TEXT")


def _account(conn, ws, account_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM hop_bank_accounts WHERE workspace_id=? AND id=?", (ws, int(account_id))).fetchone()
    if not r:
        raise ValueError("Bank account not found")
    return dict(r)


def _signed(e: dict[str, Any]) -> float:
    return float(e["amount"] or 0) * (1 if e["entry_type"] == "in" else -1)


def statement(conn: sqlite3.Connection, ws: str, account_id: int, as_of: str = "") -> dict[str, Any]:
    ensure_columns(conn)
    acct = _account(conn, ws, account_id)
    sql = "SELECT * FROM hop_bank_entries WHERE workspace_id=? AND account_id=?"
    params: list[Any] = [ws, int(account_id)]
    if as_of:
        sql += " AND date(entry_date) <= date(?)"
        params.append(as_of)
    entries = [dict(r) for r in conn.execute(sql + " ORDER BY entry_date, id", params)]
    opening = float(acct.get("opening_balance") or 0)
    book = opening + sum(_signed(e) for e in entries)
    cleared = [e for e in entries if e.get("cleared_on") and (not as_of or e["cleared_on"] <= as_of)]
    bank = opening + sum(_signed(e) for e in cleared)
    open_rows = [e for e in entries if e not in cleared]
    return {
        "account": {"id": acct["id"], "name": acct["name"], "account_type": acct.get("account_type")},
        "as_of": as_of or None, "opening_balance": round(opening, 2),
        "book_balance": round(book, 2), "cleared_balance": round(bank, 2),
        "uncleared_in": round(sum(_signed(e) for e in open_rows if e["entry_type"] == "in"), 2),
        "uncleared_out": round(-sum(_signed(e) for e in open_rows if e["entry_type"] != "in"), 2),
        "entries": [{"id": e["id"], "date": (e["entry_date"] or "")[:10], "type": e["entry_type"],
                     "amount": round(float(e["amount"] or 0), 2), "reference": e.get("reference") or "",
                     "notes": e.get("notes") or "", "cleared_on": e.get("cleared_on")} for e in reversed(entries)][:500],
    }


def mark(conn: sqlite3.Connection, ws: str, account_id: int, entry_ids: list[Any], cleared_on: str | None) -> int:
    """Tick (cleared_on = date) or untick (None) entries of this account."""
    ensure_columns(conn)
    _account(conn, ws, account_id)
    if cleared_on:
        cleared_on = _date(cleared_on)
        if not cleared_on:
            raise ValueError("Cleared date must be a date")
    ids = [int(i) for i in entry_ids or [] if str(i).strip().lstrip("-").isdigit()][:500]
    if not ids:
        raise ValueError("Choose at least one entry")
    q = ",".join("?" * len(ids))
    cur = conn.execute(f"UPDATE hop_bank_entries SET cleared_on=? WHERE workspace_id=? AND account_id=? AND id IN ({q})",
                       (cleared_on, ws, int(account_id), *ids))
    conn.commit()
    return cur.rowcount


# ── Statement file → rows ────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y", "%d %b %y",
                 "%d-%b-%y", "%d.%m.%Y")


def _date(v: Any) -> str:
    s = str(v or "").strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return ""


def _num(v: Any) -> float:
    s = re.sub(r"[^\d.\-]", "", str(v or "").replace("Cr", "").replace("Dr", ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Bank statement CSV → [{date, amount (+in / -out), description}]. Finds the header row itself."""
    rows = list(csv.reader(io.StringIO(text or "")))
    head_i, cols = None, {}
    for i, r in enumerate(rows[:40]):
        low = [c.strip().lower() for c in r]
        if any("date" in c for c in low) and any(k in c for c in low for k in ("debit", "credit", "withdraw", "deposit", "amount")):
            head_i = i
            for j, c in enumerate(low):
                if "date" in c and "date" not in cols and "value" not in c:
                    cols["date"] = j
                elif ("debit" in c or "withdraw" in c) and "out" not in cols:
                    cols["out"] = j
                elif ("credit" in c or "deposit" in c) and "in" not in cols:
                    cols["in"] = j
                elif "amount" in c and "amount" not in cols:
                    cols["amount"] = j
                elif any(k in c for k in ("narration", "description", "particular", "remarks", "details")) and "desc" not in cols:
                    cols["desc"] = j
            cols.setdefault("date", low.index(next(c for c in low if "date" in c)))
            break
    if head_i is None:
        raise ValueError("Could not find the Date / Debit / Credit columns in this file")
    out = []
    for r in rows[head_i + 1:]:
        if len(r) <= cols["date"]:
            continue
        d = _date(r[cols["date"]])
        if not d:
            continue
        get = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else ""  # noqa: E731
        amt = _num(get("in")) - _num(get("out")) if ("in" in cols or "out" in cols) else _num(get("amount"))
        if abs(amt) < 0.005:
            continue
        out.append({"date": d, "amount": round(amt, 2), "description": str(get("desc")).strip()[:120]})
    return out[:2000]


def match_rows(conn: sqlite3.Connection, ws: str, account_id: int, rows: list[dict[str, Any]],
               days: int = 5) -> dict[str, Any]:
    """Pair statement rows with uncleared book entries (same amount + direction, date within ``days``)."""
    ensure_columns(conn)
    _account(conn, ws, account_id)
    open_entries = [dict(r) for r in conn.execute(
        "SELECT * FROM hop_bank_entries WHERE workspace_id=? AND account_id=? AND (cleared_on IS NULL OR cleared_on='') "
        "ORDER BY entry_date, id", (ws, int(account_id)))]
    used: set[int] = set()
    matches, missing = [], []
    for row in rows:
        amt, d = float(row.get("amount") or 0), _date(row.get("date")) or str(row.get("date") or "")[:10]
        try:
            rd = date.fromisoformat(d)
        except ValueError:
            continue
        best, best_gap = None, None
        for e in open_entries:
            if e["id"] in used or abs(abs(amt) - float(e["amount"] or 0)) > 0.01:
                continue
            if (amt > 0) != (e["entry_type"] == "in"):
                continue
            try:
                gap = abs((date.fromisoformat((e["entry_date"] or "")[:10]) - rd).days)
            except ValueError:
                continue
            if gap <= days and (best_gap is None or gap < best_gap):
                best, best_gap = e, gap
        if best:
            used.add(best["id"])
            matches.append({"entry_id": best["id"], "entry_date": best["entry_date"][:10], "statement_date": d,
                            "amount": round(abs(amt), 2), "type": best["entry_type"], "notes": best.get("notes") or "",
                            "description": row.get("description") or ""})
        else:
            missing.append({"date": d, "amount": round(amt, 2), "description": row.get("description") or "",
                            "hint": _hint(row.get("description") or "", amt)})
    return {"matches": matches, "not_in_books": missing,
            "summary": f"{len(matches)} matched, {len(missing)} statement line(s) not in your books."}


def _hint(desc: str, amt: float) -> str:
    """Plain suggestion for a statement line that is not in the books (rule based, no AI cost)."""
    d = desc.lower()
    if any(k in d for k in ("charge", "chgs", "fee", "gst on", "sms", "amc")):
        return "Looks like a bank charge — add it as an Expense (Bank charges)."
    if "int" in d and amt > 0:
        return "Looks like interest from the bank — add it as Other Income (Interest received)."
    if any(k in d for k in ("chq", "cheque", "clg")):
        return "A cheque — check the Cheques list; it may still be open."
    return "Add the payment / receipt in the app if it belongs to your business."
