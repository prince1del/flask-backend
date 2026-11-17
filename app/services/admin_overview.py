"""Admin Zone home: one summary for the founder (every company + server health) or a company owner
(only their own team). Every number is read defensively — a missing table never breaks the page."""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_STARTED = time.time()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Any:
    try:
        r = conn.execute(sql, args).fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _people(conn: sqlite3.Connection, where: str, args: tuple) -> dict[str, Any]:
    cols = _cols(conn, "users")
    today = _now().strftime("%Y-%m-%d")
    week = (_now() + timedelta(days=7)).isoformat()
    out: dict[str, Any] = {"logins": _one(conn, f"SELECT COUNT(*) FROM users WHERE {where}", args) or 0}
    if "active_session_at" in cols:
        out["active_today"] = _one(conn, f"SELECT COUNT(*) FROM users WHERE {where} AND substr(active_session_at,1,10)=?",
                                   args + (today,)) or 0
    if "trial_ends_at" in cols:
        paid = "AND (paid_until IS NULL OR paid_until < ?)" if "paid_until" in cols else ""
        pargs = (_now().isoformat(),) if paid else ()
        rows = _rows(conn, f"SELECT username, trial_ends_at FROM users WHERE {where} AND trial_ends_at IS NOT NULL "
                           f"AND trial_ends_at >= ? AND trial_ends_at <= ? {paid} ORDER BY trial_ends_at LIMIT 20",
                     args + (_now().isoformat(), week) + pargs)
        out["trials_ending"] = [{"login": r[0], "ends": r[1]} for r in rows]
    if "status" in cols:
        out["switched_off"] = _one(conn, f"SELECT COUNT(*) FROM users WHERE {where} AND lower(status) IN ('blocked','disabled','inactive')",
                                   args) or 0
    return out


def _health(db_path: str) -> dict[str, Any]:
    disk = Path((os.getenv("PERSISTENT_DISK_PATH") or "").strip() or "/var/data")
    h: dict[str, Any] = {"server_up_hours": round((time.time() - _STARTED) / 3600, 1)}
    try:
        backups = sorted(disk.glob("backup_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        if backups:
            h["last_backup_at"] = datetime.fromtimestamp(backups[0].stat().st_mtime, timezone.utc).isoformat()
            h["backups_kept"] = len(backups)
    except OSError:
        pass
    try:
        h["database_mb"] = round(Path(db_path).stat().st_size / 1_048_576, 1)
        du = shutil.disk_usage(Path(db_path).parent)
        h["disk_free_gb"] = round(du.free / 1_073_741_824, 1)
        h["disk_used_pct"] = round(100 * du.used / du.total)
    except OSError:
        pass
    try:
        import json

        rel = disk / "app-releases" / "android" / "latest.json"
        if rel.is_file():
            meta = json.loads(rel.read_text(encoding="utf-8"))
            h["app_version"] = meta.get("version_name")
            h["app_published_at"] = meta.get("published_at")
    except (OSError, ValueError):
        pass
    h["email_ready"] = bool((os.getenv("SMTP_HOST") or "").strip())
    return h


def overview(db_path: str, user: dict[str, Any]) -> dict[str, Any]:
    from app.services import subscriptions

    founder = subscriptions.is_super_admin(user)
    ws = str(user.get("workspace_id") or "")
    today = _now().strftime("%Y-%m-%d")
    with sqlite3.connect(db_path) as conn:
        if founder:
            data: dict[str, Any] = {"scope": "founder", "companies": _one(conn, "SELECT COUNT(*) FROM companies") or 0}
            data.update(_people(conn, "1=1", ()))
            ai = _rows(conn, "SELECT COUNT(*), COALESCE(SUM(total_tokens),0) FROM ai_agent_usage_log WHERE substr(created_at,1,10)=?",
                       (today,))
            if ai:
                data["ai_calls_today"], data["ai_tokens_today"] = int(ai[0][0] or 0), int(ai[0][1] or 0)
            data["team_requests_pending"] = _one(conn, "SELECT COUNT(*) FROM team_requests WHERE status='pending'") or 0
            data["health"] = _health(db_path)
        else:
            data = {"scope": "owner"}
            data.update(_people(conn, "workspace_id = ?", (ws,)))
            data["ai_calls_today"] = _one(
                conn, "SELECT COUNT(*) FROM ai_agent_usage_log l JOIN users u ON u.id=l.user_id "
                      "WHERE u.workspace_id=? AND substr(l.created_at,1,10)=?", (ws, today)) or 0
        data["sign_in_waiting"] = _one(
            conn, "SELECT COUNT(*) FROM login_approvals a JOIN users u ON u.id=a.user_id WHERE a.status='pending'"
                  + ("" if founder else " AND u.workspace_id=?"), () if founder else (ws,)) or 0
    data["generated_at"] = _now().isoformat()
    return data
