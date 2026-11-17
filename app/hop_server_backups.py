"""Backups on the server, per firm — like Vyapar's backup on the login, so the owner sees them from any phone / PC.

* Every 6 hours a timer saves each firm's locked backup (.kunwar) on the server, only when the data changed.
* Before risky actions (old-software import, wipe, replace from a backup) a copy is always saved.
* Kept 30 days (the newest 5 are always kept). Settings → Backup → Backups on server: download or restore.
* Files live under <data disk>/hop_backups/<firm workspace>/ — one folder per firm, never mixed.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KEEP_DAYS = 30
KEEP_MIN = 5
REASONS = {"auto": "Automatic", "before_import": "Before import", "before_wipe": "Before wipe",
           "before_replace": "Before replace", "manual": "Saved by you"}
_NAME = re.compile(r"^\d{8}_\d{6}_[a-z_]+\.kunwar$")


def root() -> Path:
    base = os.getenv("HOP_BACKUP_DIR") or os.path.join((os.getenv("PERSISTENT_DISK_PATH") or "/var/data").strip(),
                                                         "hop_backups")
    return Path(base)


def _dir(ws: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(ws or ""))
    if not safe:
        raise ValueError("No firm")
    return root() / safe


def _fingerprint(conn: sqlite3.Connection, ws: str) -> str:
    """Hash of the data only (not of the zip's date lines) — no new copy when nothing changed."""
    from app import hop_backup

    data, _ = hop_backup.build_zip(conn, ws)
    z = zipfile.ZipFile(io.BytesIO(data))
    h = hashlib.sha256()
    for n in sorted(z.namelist()):
        if n.endswith(".csv"):
            h.update(n.encode())
            h.update(z.read(n))
    return h.hexdigest()


def snapshot(conn: sqlite3.Connection, ws: str, reason: str = "auto", force: bool = False) -> dict[str, Any] | None:
    from app import hop_backup

    d = _dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    fp = _fingerprint(conn, ws)
    last = d / ".last"
    if not force and last.exists() and last.read_text().strip() == fp:
        return None
    blob, counts = hop_backup.build_locked(conn, ws)
    if not force and sum(counts.values()) == 0:
        return None                                   # empty firm: nothing to keep
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{reason if reason in REASONS else 'auto'}.kunwar"
    tmp = d / (name + ".part")
    tmp.write_bytes(blob)
    tmp.replace(d / name)
    last.write_text(fp)
    prune(ws)
    return {"name": name, "size": len(blob), "rows": sum(counts.values())}


def prune(ws: str) -> None:
    files = sorted(_dir(ws).glob("*.kunwar"), reverse=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    for f in files[KEEP_MIN:]:
        try:
            at = datetime.strptime(f.name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if at < cutoff:
            f.unlink(missing_ok=True)


def list_backups(ws: str) -> list[dict[str, Any]]:
    d = _dir(ws)
    out = []
    for f in sorted(d.glob("*.kunwar"), reverse=True) if d.exists() else []:
        if not _NAME.match(f.name):
            continue
        at = datetime.strptime(f.name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        reason = f.name[16:-7]
        out.append({"name": f.name, "at": at.isoformat(), "at_ist": (at + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p"),
                    "reason": REASONS.get(reason, reason), "size_kb": round(f.stat().st_size / 1024, 1)})
    return out


def read(ws: str, name: str) -> bytes:
    if not _NAME.match(name or ""):
        raise ValueError("Backup not found")
    f = _dir(ws) / name
    if not f.exists():
        raise ValueError("Backup not found")
    return f.read_bytes()


def run_all(db_path: str) -> list[dict[str, Any]]:
    """Every firm of every company (timer, every 6 hours)."""
    out = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        wss: set[str] = set()
        for sql in ("SELECT DISTINCT workspace_id FROM hop_party_transactions",
                    "SELECT DISTINCT workspace_id FROM hop_customers",
                    "SELECT DISTINCT workspace_id FROM hop_products"):
            try:
                wss |= {r[0] for r in conn.execute(sql).fetchall() if r[0]}
            except sqlite3.Error:
                pass
        for ws in sorted(wss):
            try:
                res = snapshot(conn, ws, "auto")
                out.append({"workspace": ws, "saved": bool(res)})
            except Exception as exc:  # one firm failing never stops the others
                out.append({"workspace": ws, "error": str(exc)})
    return out


if __name__ == "__main__":
    from app.hop_schema import ensure_hop_schema

    path = os.getenv("DATABASE_PATH") or os.path.join((os.getenv("PERSISTENT_DISK_PATH") or "/var/data").strip(),
                                                      "centralized_db.sqlite3")
    ensure_hop_schema(path)
    for r in run_all(path):
        print(r)
