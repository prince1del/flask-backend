"""
NEXORA Ask — learned phrase mappings (train without Python code changes).

Isolation rules:
- Phrases are scoped by (workspace_id, owner_user_id) — never leak across users/workspaces.
- House of Prizm workspace never receives Bombay Dyeing seed phrases.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz

_MATCH_THRESHOLD = 86
_HOP_WORKSPACE = "house_of_prizm"
_SCHEMA_READY = False
_SEEDED_WORKSPACES: set[str] = set()


def _normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        # Fast path: table already migrated this process — still cheap IF NOT EXISTS is OK,
        # but skip PRAGMA + migration work on every Ask.
        try:
            conn.execute("SELECT 1 FROM nexora_learned_phrases LIMIT 1")
            return
        except sqlite3.OperationalError:
            _SCHEMA_READY = False

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexora_learned_phrases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL DEFAULT 'default',
            user_phrase TEXT NOT NULL,
            canonical_question TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nexora_learned_phrases)").fetchall()}
    if "owner_user_id" not in cols:
        conn.execute(
            "ALTER TABLE nexora_learned_phrases ADD COLUMN owner_user_id INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            """
            UPDATE nexora_learned_phrases
            SET owner_user_id = COALESCE(created_by, 0)
            WHERE owner_user_id = 0 AND created_by IS NOT NULL
            """
        )

    _ensure_owner_unique(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nexora_learned_ws_owner "
        "ON nexora_learned_phrases(workspace_id, owner_user_id)"
    )
    conn.commit()
    _SCHEMA_READY = True


def _ensure_owner_unique(conn: sqlite3.Connection) -> None:
    """Migrate to UNIQUE(workspace_id, owner_user_id, user_phrase) so users never clash."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nexora_learned_phrases'"
    ).fetchone()
    table_sql = (row[0] or "") if row else ""
    compact = table_sql.replace(" ", "").replace("\n", "")
    if "UNIQUE(workspace_id,owner_user_id,user_phrase)" in compact:
        return

    # Drop legacy unique indexes that only key (workspace_id, user_phrase)
    for idx in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='nexora_learned_phrases'"
    ).fetchall():
        name, sql = idx[0], (idx[1] or "")
        if "owner_user_id" not in sql and "user_phrase" in sql:
            conn.execute(f'DROP INDEX IF EXISTS "{name}"')

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS nexora_learned_phrases_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL DEFAULT 'default',
            owner_user_id INTEGER NOT NULL DEFAULT 0,
            user_phrase TEXT NOT NULL,
            canonical_question TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, owner_user_id, user_phrase)
        );
        INSERT OR IGNORE INTO nexora_learned_phrases_v2 (
            id, workspace_id, owner_user_id, user_phrase, canonical_question,
            source, hit_count, created_by, created_at, updated_at
        )
        SELECT
            id,
            workspace_id,
            COALESCE(NULLIF(owner_user_id, 0), created_by, 0),
            user_phrase,
            canonical_question,
            source,
            hit_count,
            created_by,
            created_at,
            updated_at
        FROM nexora_learned_phrases;
        DROP TABLE nexora_learned_phrases;
        ALTER TABLE nexora_learned_phrases_v2 RENAME TO nexora_learned_phrases;
        """
    )


def seed_default_phrases(conn: sqlite3.Connection, workspace_id: str | None, owner_user_id: int = 0) -> None:
    """BD built-in training — never seed into House of Prizm."""
    ensure_schema(conn)
    ws = workspace_id or "default"
    if ws == _HOP_WORKSPACE:
        # Safety: remove any BD seeds that may have been written earlier
        conn.execute(
            "DELETE FROM nexora_learned_phrases WHERE workspace_id = ? AND source = 'seed'",
            (_HOP_WORKSPACE,),
        )
        conn.commit()
        return
    if ws in _SEEDED_WORKSPACES:
        return
    defaults = [
        ("jatin kaun h", "jatin arora kon hai"),
        ("jatin arora kaun", "jatin arora kon hai"),
        ("aster mrp kitna", "aster ki mrp kitni hai"),
        ("florentine ksbs", "florentine ks bs ki mrp"),
        ("florentine ks bs", "florentine ks bs ki mrp"),
        ("aster ki kimat", "aster ki mrp kitni hai"),
        ("bernina gst number", "bernina ka gst number aur address"),
        ("bernina ka pata", "bernina ka gst number aur address"),
    ]
    now = _now()
    for phrase, canonical in defaults:
        conn.execute(
            """
            INSERT OR IGNORE INTO nexora_learned_phrases
            (workspace_id, owner_user_id, user_phrase, canonical_question, source,
             created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'seed', ?, ?, ?)
            """,
            (ws, int(owner_user_id or 0), _normalize_phrase(phrase), canonical.strip(), int(owner_user_id or 0) or None, now, now),
        )
    conn.commit()
    _SEEDED_WORKSPACES.add(ws)


def find_canonical_question(
    conn: sqlite3.Connection,
    question: str,
    workspace_id: str | None,
    owner_user_id: int | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Match learned phrases only inside this workspace + this user (plus shared seeds for non-hop)."""
    ensure_schema(conn)
    ws = workspace_id or "default"
    uid = int(owner_user_id or 0)

    if ws == _HOP_WORKSPACE:
        # Prizm: only this user's taught phrases — no BD seeds
        rows = conn.execute(
            """
            SELECT id, user_phrase, canonical_question, hit_count
            FROM nexora_learned_phrases
            WHERE workspace_id = ? AND owner_user_id = ?
            """,
            (ws, uid),
        ).fetchall()
    else:
        seed_default_phrases(conn, ws, owner_user_id=0)
        rows = conn.execute(
            """
            SELECT id, user_phrase, canonical_question, hit_count
            FROM nexora_learned_phrases
            WHERE workspace_id = ?
              AND (owner_user_id = ? OR (owner_user_id = 0 AND source = 'seed'))
            """,
            (ws, uid),
        ).fetchall()

    normalized = _normalize_phrase(question)
    if not normalized:
        return None, None

    best: tuple[int, int | None, str | None] = (0, None, None)
    for row_id, phrase, canonical, _hits in rows:
        if len(phrase) < 8 and len(normalized) > len(phrase) * 2:
            continue
        score = max(
            fuzz.ratio(normalized, phrase),
            fuzz.partial_ratio(phrase, normalized),
            fuzz.token_set_ratio(normalized, phrase),
        )
        if phrase in normalized:
            score = max(score, 96)
        if score > best[0]:
            best = (score, row_id, canonical)

    if best[0] >= _MATCH_THRESHOLD and best[2]:
        conn.execute(
            "UPDATE nexora_learned_phrases SET hit_count = hit_count + 1, updated_at = ? WHERE id = ?",
            (_now(), best[1]),
        )
        conn.commit()
        return best[2], {"learned_id": best[1], "match_score": best[0], "workspace_id": ws, "owner_user_id": uid}
    return None, None


def teach_phrase(
    conn: sqlite3.Connection,
    *,
    workspace_id: str | None,
    user_phrase: str,
    canonical_question: str,
    created_by: int | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    ensure_schema(conn)
    ws = workspace_id or "default"
    uid = int(created_by or 0)
    if uid <= 0:
        raise ValueError("created_by (user id) is required to teach phrases")
    phrase = _normalize_phrase(user_phrase)
    canonical = re.sub(r"\s+", " ", (canonical_question or "").strip())
    if not phrase or not canonical:
        raise ValueError("user_phrase and canonical_question are required")

    now = _now()
    conn.execute(
        """
        INSERT INTO nexora_learned_phrases
        (workspace_id, owner_user_id, user_phrase, canonical_question, source,
         created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, owner_user_id, user_phrase) DO UPDATE SET
            canonical_question = excluded.canonical_question,
            source = excluded.source,
            updated_at = excluded.updated_at,
            created_by = excluded.created_by
        """,
        (ws, uid, phrase, canonical, source, uid, now, now),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT id FROM nexora_learned_phrases
        WHERE workspace_id = ? AND owner_user_id = ? AND user_phrase = ?
        """,
        (ws, uid, phrase),
    ).fetchone()
    return {
        "id": row[0] if row else None,
        "user_phrase": phrase,
        "canonical_question": canonical,
        "workspace_id": ws,
        "owner_user_id": uid,
    }


def list_phrases(
    conn: sqlite3.Connection,
    workspace_id: str | None,
    *,
    owner_user_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    ws = workspace_id or "default"
    uid = int(owner_user_id or 0)
    if uid > 0:
        rows = conn.execute(
            """
            SELECT id, user_phrase, canonical_question, source, hit_count, created_at, owner_user_id
            FROM nexora_learned_phrases
            WHERE workspace_id = ? AND owner_user_id = ?
            ORDER BY hit_count DESC, id DESC
            LIMIT ?
            """,
            (ws, uid, max(1, int(limit))),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, user_phrase, canonical_question, source, hit_count, created_at, owner_user_id
            FROM nexora_learned_phrases
            WHERE workspace_id = ?
            ORDER BY hit_count DESC, id DESC
            LIMIT ?
            """,
            (ws, max(1, int(limit))),
        ).fetchall()
    return [
        {
            "id": r[0],
            "user_phrase": r[1],
            "canonical_question": r[2],
            "source": r[3],
            "hit_count": r[4],
            "created_at": r[5],
            "owner_user_id": r[6],
        }
        for r in rows
    ]


def get_gemini_api_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    try:
        from flask import current_app, has_request_context

        if has_request_context():
            val = (current_app.config.get("GEMINI_API_KEY") or "").strip()
            if val:
                return val
    except Exception:
        pass
    return ""


def gemini_configured() -> bool:
    return bool(get_gemini_api_key())


def llm_suggest_canonical(question: str, api_key: str | None = None) -> tuple[str | None, str | None]:
    key = (api_key or get_gemini_api_key()).strip()
    if not key:
        return None, "missing_api_key"

    prompt = f"""You help NEXORA, a B2B order / ERP chatbot (Hindi/English/Hinglish).
Rewrite the user question into ONE clear canonical question.
Do NOT invent numbers. Keep the user's business context.
User question: {question}
Reply with ONLY the canonical question text, nothing else."""

    # Keep Ask fast: one primary model, short timeout. Used only as help-fallback.
    models = (
        "gemini-2.0-flash-lite-001",
        "gemini-2.0-flash",
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 120},
    }).encode("utf-8")

    last_error = "api_error"
    for model in models:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            text = re.sub(r"\s+", " ", text.strip().strip('"'))
            if text:
                return text, None
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                msg = (detail.get("error", {}) or {}).get("message", "")
                if exc.code == 429 or "quota" in msg.lower():
                    return None, "quota_exceeded"
                if exc.code in (503, 529) or "high demand" in msg.lower():
                    last_error = "model_busy"
                    continue
                if "API key" in msg or exc.code in (401, 403):
                    return None, "invalid_api_key"
            except Exception:
                pass
            last_error = "api_error"
            if exc.code == 404:
                continue
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, IndexError):
            last_error = "api_error"
            continue
    return None, last_error
