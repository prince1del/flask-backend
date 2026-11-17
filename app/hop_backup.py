"""HoP backup download: every hop_* table of this company as CSV, in one zip — locked.

Only this company's rows (``workspace_id``) are included. The file people download / get by email is the
zip LOCKED with AES-256-GCM (``.kunwar``): it opens only in NEXORA (Settings → Restore into a new firm),
and only for the same company — every company has its own key, made from the server's backup key
(``NEXORA_BACKUP_KEY`` in the server env; keep a copy of it safe — without it old backups cannot be opened).
Restoring always makes a NEW firm, so a backup can never overwrite live data.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import base64
import hashlib
import os
import zipfile
from datetime import datetime, timezone

SKIP_COLUMNS = {"password", "password_hash", "token", "secret"}


def build_zip(conn: sqlite3.Connection, ws: str) -> tuple[bytes, dict[str, int]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hop_%' ORDER BY name")]
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            if "workspace_id" not in cols:
                continue
            keep = [c for c in cols if c.lower() not in SKIP_COLUMNS]
            rows = conn.execute(f"SELECT {', '.join(keep)} FROM {t} WHERE workspace_id=?", (ws,)).fetchall()
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(keep)
            w.writerows([list(r) for r in rows])
            z.writestr(f"{t}.csv", out.getvalue())
            counts[t] = len(rows)
        z.writestr("README.txt", "NEXORA business backup\n"
                   f"Made: {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC\n"
                   "One CSV per table. Keep this file safe; it has your business data.\n")
        z.writestr("counts.json", json.dumps(counts, indent=1))
    return buf.getvalue(), counts


MAGIC = b"NEXORA-KUNWAR-1\n"
EXT = ".kunwar"


def _master_key() -> bytes:
    raw = (os.getenv("NEXORA_BACKUP_KEY") or "").strip()
    if raw:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(key) >= 32:
            return key[:32]
    # No backup key set (dev / tests): fall back to the app secret.
    return hashlib.sha256(b"nexora-backup|" + (os.getenv("SECRET_KEY") or "dev").encode()).digest()


def company_key(ws: str) -> bytes:
    """One key per company (all its firms share it, so a backup restores into a new firm of the same company)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    base = str(ws or "").split("__f")[0]
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"nexora-backup-v1", info=base.encode()).derive(_master_key())


def is_locked(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC


def lock(data: bytes, ws: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(company_key(ws)).encrypt(nonce, data, MAGIC)


def unlock(blob: bytes, ws: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_locked(blob):
        raise ValueError("This is not a NEXORA backup (.kunwar)")
    body = blob[len(MAGIC):]
    try:
        return AESGCM(company_key(ws)).decrypt(body[:12], body[12:], MAGIC)
    except InvalidTag as exc:
        raise ValueError("This backup belongs to another company, or the file is damaged") from exc


def build_locked(conn: sqlite3.Connection, ws: str) -> tuple[bytes, dict[str, int]]:
    data, counts = build_zip(conn, ws)
    return lock(data, ws), counts


def file_name(day: str) -> str:
    return f"nexora_backup_{day}{EXT}"


def note_staff_download(conn: sqlite3.Connection, ws: str, user: dict) -> None:
    """A staff login downloaded the backup: audit trail line + email to the owner (never blocks the download)."""
    who = str(user.get("username") or user.get("email") or "A staff login")[:80]
    try:
        from app import hop_audit

        hop_audit.ensure_schema(conn)
        conn.execute("INSERT INTO hop_audit_log (workspace_id, at, user_id, user_name, action, entity, entity_id, path, "
                     "summary, before) VALUES (?, ?, ?, ?, 'downloaded', 'Backup', '', '/backup', ?, NULL)",
                     (ws, datetime.now(timezone.utc).isoformat(timespec="seconds"), user.get("user_id"), who,
                      json.dumps({"name": "Backup (.kunwar)"})))
        conn.commit()
    except sqlite3.Error:
        pass
    try:
        from app import hop_firms, hop_reminders
        from app.services.outbound_mail import send_plain_email, smtp_configured

        to = hop_reminders._owner_email(conn, ws)
        if to and smtp_configured():
            firm = hop_firms.business_name(conn, ws, "your business")
            send_plain_email(to=to, subject=f"NEXORA - {who} downloaded the backup of {firm}",
                             body=(f"{who} downloaded the backup of {firm} just now.\n"
                                   "The file is locked and opens only in NEXORA for your company.\n"
                                   "If this was not expected, turn off 'Can download backup' in Staff permissions."))
    except Exception:
        pass
