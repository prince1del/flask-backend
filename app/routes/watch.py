"""Galaxy Watch (Wear OS): a glance tile and voice questions without the phone.

Pairing: the phone (normal login) calls POST /api/v1/watch/pair and gets a
watch key, which it hands to the watch over Bluetooth. The key only reads the
summary and asks Ask Nexora as that user; it cannot use any other route and
can be switched off any time (DELETE /api/v1/watch/pair). Only a hash of the
key is stored.

GET  /api/v1/watch/summary   (X-Watch-Key)  numbers for the tile
POST /api/v1/watch/ask       (X-Watch-Key)  {"question"} -> short answer
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import date

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth

watch_bp = Blueprint("watch", __name__, url_prefix="/api/v1/watch")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watch_keys (
            key_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            role TEXT,
            username TEXT,
            is_workspace_owner INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0
        )"""
    )
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@watch_bp.route("/pair", methods=["POST"])
@require_jwt_auth
def pair():
    """New watch key for the logged-in user (older keys keep working until revoked)."""
    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    user = getattr(request, "user", None) or {}
    key = "nxw_" + secrets.token_urlsafe(32)
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO watch_keys (key_hash, user_id, workspace_id, role, username, is_workspace_owner) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_hash(key), uid, str(get_workspace_id() or "default"), user.get("role"),
             user.get("username"), 1 if user.get("is_workspace_owner") else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True, "data": {"watch_key": key}}), 200


@watch_bp.route("/pair", methods=["DELETE"])
@require_jwt_auth
def unpair():
    """Switch off every watch key of this user."""
    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    conn = _conn()
    try:
        n = conn.execute("UPDATE watch_keys SET revoked = 1 WHERE user_id = ? AND revoked = 0", (uid,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True, "data": {"revoked": n}}), 200


def _watch_user():
    """The user behind X-Watch-Key, set on request.user like a normal login."""
    key = (request.headers.get("X-Watch-Key") or "").strip()
    if not key:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT user_id, workspace_id, role, username, is_workspace_owner FROM watch_keys "
            "WHERE key_hash = ? AND revoked = 0", (_hash(key),)
        ).fetchone()
        if row:
            conn.execute("UPDATE watch_keys SET last_used_at = datetime('now') WHERE key_hash = ?", (_hash(key),))
            conn.commit()
    finally:
        conn.close()
    if not row:
        return None
    user = {"user_id": int(row[0]), "workspace_id": row[1], "role": row[2] or "unassigned",
            "username": row[3], "is_workspace_owner": bool(row[4])}
    request.user = user
    return user


def _unauthorized():
    return jsonify({"success": False, "error": {"code": "WATCH_KEY", "message": "Connect the watch again from the phone"}}), 401


def build_summary(db, conn: sqlite3.Connection, *, user_id: int, workspace_id: str, today: date | None = None) -> dict:
    """Tile numbers: active season, money to collect, SOs to bill, overdue, today's work."""
    from app.services import distributor_ledger as dl
    from app.services.seasons import season_overview

    today = today or date.today()
    ov = season_overview(db, conn, user_id=user_id, workspace_id=workspace_id)
    active = ov.get("active")
    season = next((s for s in ov.get("seasons") or [] if s["season"] == active), None) or {}
    ledger = dl.build_ledger(db, conn, user_id=user_id, workspace_id=workspace_id, today=today)
    overdue = sum(
        1 for d in ledger.get("distributors") or [] for a in d.get("alerts") or [] if a.get("level") == "alert"
    )
    todos_due = 0
    try:
        todos_due = conn.execute(
            "SELECT count(*) FROM personal_todos WHERE user_id = ? AND workspace_id = ? AND status != 'done' "
            "AND due_date IS NOT NULL AND due_date <= ?", (user_id, workspace_id, today.isoformat())
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    pjp = None
    try:
        row = conn.execute(
            "SELECT place_to_visit, day_type FROM monthly_pjp_days WHERE user_id = ? AND workspace_id = ? "
            "AND plan_date = ?", (user_id, workspace_id, today.isoformat())
        ).fetchone()
        if row:
            pjp = row[0] or row[1]
    except sqlite3.OperationalError:
        pass
    so_mails = 0
    try:
        so_mails = conn.execute(
            "SELECT count(*) FROM so_mail_imports WHERE user_id = ? AND workspace_id = ? AND status = 'review'",
            (user_id, workspace_id),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    return {
        "season": active,
        "to_collect": round(float(season.get("to_collect") or 0), 2),
        "pending_so_count": int(season.get("pending_so_count") or 0),
        "pending_so_value": round(float(season.get("pending_so_value") or 0), 2),
        "overdue_count": overdue,
        "todos_due": int(todos_due or 0),
        "pjp_today": pjp,
        "so_mails_waiting": int(so_mails or 0),
        "as_of": today.isoformat(),
    }


@watch_bp.route("/summary", methods=["GET"])
def summary():
    user = _watch_user()
    if user is None:
        return _unauthorized()
    from app.routes.data import _db_path
    from centralized_db_system.db import CentralizedDB

    conn = sqlite3.connect(_db_path())
    try:
        data = build_summary(CentralizedDB(_db_path()), conn, user_id=user["user_id"], workspace_id=user["workspace_id"])
    finally:
        conn.close()
    return jsonify({"success": True, "data": data}), 200


@watch_bp.route("/ask", methods=["POST"])
def ask():
    user = _watch_user()
    if user is None:
        return _unauthorized()
    question = str((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question:
        return jsonify({"success": False, "error": {"message": "question is required"}}), 400
    from app.services import nexora_actions
    from app.services import nexora_ai_agent as agent

    uid, ws = user["user_id"], user["workspace_id"]
    db_path = str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3")
    # "haan" / "nahi" after a proposed change works from the watch too.
    reply = nexora_actions.answer_reply(db_path, user_id=uid, workspace_id=ws, text=question)
    if reply is not None:
        agent.record_turn(uid, ws, question, reply)
        return jsonify({"success": True, "data": {"answer": reply}}), 200
    turns = agent.recent_turns(uid, ws)
    follow = agent.is_follow_up(question, turns)
    try:
        answer = agent.ask_order_desk(question, user_id=uid, workspace_id=ws, history=turns if follow else None)
    except agent.NexoraAiAgentError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)[:200]}}), 503
    agent.record_turn(uid, ws, question, answer)
    from flask import g

    action = getattr(g, "nexora_pending_action", None)
    return jsonify({"success": True, "data": {"answer": answer, "action": action}}), 200


@watch_bp.route("/voice", methods=["POST"])
def voice():
    """Body {text} -> WAV of the answer in this user's saved Ask Nexora voice."""
    user = _watch_user()
    if user is None:
        return _unauthorized()
    text = str((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": {"message": "text is required"}}), 400
    from flask import Response

    from app.routes.ai_agent import _saved_voice
    from app.services import nexora_ai_agent as agent
    from app.services.nexora_voice import VoiceError, synthesize
    from app.services.visiting_card_ocr import _gemini_key

    uid = user["user_id"]
    try:
        audio, info = synthesize(
            text[:6000], voice=_saved_voice(uid), api_key=_gemini_key(),
            db_path=str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3"),
            cache_only=agent.over_daily_limit(uid, "voice"),
        )
    except VoiceError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 503
    if not info.get("cached"):
        agent._log_usage(
            user_id=uid, feature="nexora_voice", model=str(info.get("model") or ""), rounds=1,
            prompt_tokens=int(info.get("prompt_tokens") or 0), output_tokens=int(info.get("output_tokens") or 0),
            total_tokens=int(info.get("prompt_tokens") or 0) + int(info.get("output_tokens") or 0),
            query=f"watch voice {info.get('voice')} {info.get('chars')} chars",
        )
    return Response(audio, mimetype="audio/wav")
