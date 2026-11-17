"""Restore a NEXORA backup (Settings → Backup zip) into a NEW firm — never over existing data.

The zip holds one CSV per hop_* table (only one company's rows). Restoring:
1. makes a new firm (its own workspace) under this login's company;
2. copies every table's rows into it with NEW ids: every id and every link column (``*_id``) is
   moved up by the same number, so all links (bill → party, line → item, payment → bill…) stay right
   and nothing can clash with rows that already exist;
3. keeps keys that are not row ids (source_txn_id, theme ids, text ids) as they are.

Only tables and columns that exist in NEXORA today are filled; anything else is listed as skipped.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from typing import Any

MAX_ZIP = 60 * 1024 * 1024
# Not row ids of another hop table: keep as they are.
KEEP = {"workspace_id", "source_txn_id", "expense_source_txn_id", "print_theme_id", "submission_id"}
# Never restored: bookkeeping of the backup / import itself, or firm list (the new firm is made here).
SKIP_TABLES = {"hop_firms", "hop_import_map", "hop_deal_bulk_submissions"}


def _tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hop_%'"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        if "workspace_id" in cols:
            out[t] = cols
    return out


def _is_int(v: str) -> bool:
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


def read_zip(data: bytes, ws: str = "") -> dict[str, list[dict[str, str]]]:
    if len(data) > MAX_ZIP:
        raise ValueError("Backup file is too big (max 60 MB)")
    from app import hop_backup

    if hop_backup.is_locked(data):          # .kunwar — opens only for this company
        data = hop_backup.unlock(data, ws)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("This is not a NEXORA backup (.zip)") from exc
    names = set(z.namelist())
    if "counts.json" not in names or not any(n.startswith("hop_") and n.endswith(".csv") for n in names):
        raise ValueError("This is not a NEXORA backup — download it from Settings → Backup")
    out: dict[str, list[dict[str, str]]] = {}
    for n in names:
        if n.startswith("hop_") and n.endswith(".csv") and "/" not in n:
            text = z.read(n).decode("utf-8-sig", errors="replace")
            out[n[:-4]] = list(csv.DictReader(io.StringIO(text)))
    return out


def preview(conn: sqlite3.Connection, data: bytes, ws: str = "") -> dict[str, Any]:
    tabs = read_zip(data, ws)
    have = _tables(conn)
    counts = {t: len(rows) for t, rows in tabs.items() if rows}
    firm = next(iter(tabs.get("hop_firm_profile") or []), {})
    return {"firm_name": firm.get("firm_name") or "", "gstin": firm.get("gstin") or "",
            "tables": len(counts), "rows": sum(counts.values()),
            "parties": counts.get("hop_customers", 0) + counts.get("hop_vendors", 0),
            "items": counts.get("hop_products", 0), "documents": counts.get("hop_party_transactions", 0),
            "not_in_nexora": sorted(t for t in counts if t not in have and t not in SKIP_TABLES)}


def _load(conn: sqlite3.Connection, tabs: dict[str, list[dict[str, str]]], target_ws: str,
          have: dict[str, list[str]]) -> tuple[dict[str, int], list[str]]:
    """Insert the backup's rows into ``target_ws`` with new ids (one shift for every id / link column).
    No commit here — the caller commits or rolls back the whole restore."""
    top = 0
    for t, cols in have.items():
        if "id" in cols:
            top = max(top, conn.execute(f"SELECT COALESCE(MAX(id),0) FROM {t}").fetchone()[0] or 0)
    for rows in tabs.values():
        for r in rows:
            if _is_int(r.get("id", "")):
                top = max(top, int(r["id"]))
    shift = (int(top) // 1000 + 1) * 1000
    done: dict[str, int] = {}
    skipped: list[str] = []
    for t, rows in sorted(tabs.items()):
        if not rows or t in SKIP_TABLES:
            continue
        if t not in have:
            skipped.append(f"{t} ({len(rows)} rows) — not in this NEXORA version")
            continue
        cols = [c for c in rows[0].keys() if c in have[t]]
        moved = [c for c in cols if (c == "id" or c.endswith("_id")) and c not in KEEP]
        if t == "hop_firm_profile":
            conn.execute("DELETE FROM hop_firm_profile WHERE workspace_id=?", (target_ws,))
        n = 0
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c)
                if c == "workspace_id":
                    v = target_ws                      # always this firm, whatever the file says
                elif v == "" or v is None:
                    v = None
                elif c in moved and _is_int(v):
                    v = int(v) + shift
                vals.append(v)
            conn.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            n += 1
        done[t] = n
    return done, skipped


def _result(ws: str, name: str, done: dict[str, int], skipped: list[str]) -> dict[str, Any]:
    return {"firm_ws": ws, "name": name, "rows": sum(done.values()), "tables": len(done),
            "parties": done.get("hop_customers", 0) + done.get("hop_vendors", 0),
            "items": done.get("hop_products", 0), "documents": done.get("hop_party_transactions", 0),
            "skipped": skipped, "counts": json.loads(json.dumps(done))}


def restore_into_new_firm(conn: sqlite3.Connection, base_ws: str, data: bytes, name: str) -> dict[str, Any]:
    from app import hop_firms

    tabs = read_zip(data, base_ws)
    have = _tables(conn)
    profile = next(iter(tabs.get("hop_firm_profile") or []), {})
    firm = hop_firms.create(conn, base_ws, name or profile.get("firm_name") or "Restored firm",
                            "", "", copy_settings=False)
    new_ws = firm["firm_ws"]
    conn.commit()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")   # tables arrive in any order; links are all shifted together
    try:
        done, skipped = _load(conn, tabs, new_ws, have)
        conn.execute("UPDATE hop_firm_profile SET firm_name=? WHERE workspace_id=?", (firm["name"], new_ws))
        conn.execute("UPDATE hop_firms SET name=? WHERE firm_ws=?", (firm["name"], new_ws))
        conn.commit()
    except Exception:
        conn.rollback()
        # leave no half-restored firm behind
        for t in have:
            conn.execute(f"DELETE FROM {t} WHERE workspace_id=?", (new_ws,))
        conn.execute("DELETE FROM hop_firms WHERE firm_ws=?", (new_ws,))
        conn.commit()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk else 'OFF'}")
    return _result(new_ws, firm["name"], done, skipped)


def restore_replace(conn: sqlite3.Connection, ws: str, data: bytes) -> dict[str, Any]:
    """Put a backup back INTO this firm (data went wrong). First a safety copy of today's data is saved on the
    server (it is listed in Backups on server), then the firm's rows are swapped in ONE transaction —
    if anything fails, nothing changes."""
    from app import hop_firms, hop_server_backups

    tabs = read_zip(data, ws)                      # also checks the lock: same company only
    have = _tables(conn)
    name = hop_firms.business_name(conn, ws, "")
    safety = hop_server_backups.snapshot(conn, ws, "before_replace", force=True)
    conn.commit()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        for t in have:
            if t not in SKIP_TABLES:
                conn.execute(f"DELETE FROM {t} WHERE workspace_id=?", (ws,))
        done, skipped = _load(conn, tabs, ws, have)
        if name:
            conn.execute("UPDATE hop_firm_profile SET firm_name=COALESCE(NULLIF(firm_name,''), ?) WHERE workspace_id=?",
                         (name, ws))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk else 'OFF'}")
    out = _result(ws, name, done, skipped)
    out["safety_copy"] = safety.get("name") if safety else None
    return out
