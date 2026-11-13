"""Nexora AI agent routes.

The actual Order Desk agent (app/services/nexora_ai_agent.py) is invoked as a
fallback from inside nexora_ask_routes.py's /api/v1/nexora/ask — when the
rule-based engine can't match an intent, the same Ask Nexora endpoint hands
off to Gemini. There's deliberately no separate "/ask" HTTP route here: one
entry point (the existing Ask Nexora chat) for the user, not two.

This blueprint only exposes usage/cost visibility for the founder.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from app.routes.auth import require_jwt_auth
from app.services.nexora_ai_agent import get_usage_summary

ai_agent_bp = Blueprint("ai_agent", __name__)


@ai_agent_bp.route("/api/v1/ai/usage-summary", methods=["GET"])
@require_jwt_auth
def ai_usage_summary() -> tuple[Response, int]:
    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    if user_id is None:
        return jsonify(
            {"success": False, "error": {"code": "UNAUTHORIZED", "message": "Login required"}}
        ), 401

    is_owner = bool(isinstance(user, dict) and user.get("is_workspace_owner"))
    days = request.args.get("days", default=30, type=int)
    data = get_usage_summary(user_id=user_id, all_users=is_owner, days=days)
    return jsonify({"success": True, "data": data}), 200


@ai_agent_bp.route("/api/v1/ai/voices", methods=["GET"])
@require_jwt_auth
def ai_voices() -> tuple[Response, int]:
    """Voices the app can offer for Ask Nexora's spoken answers."""
    from app.services.nexora_voice import DEFAULT_VOICE, VOICES

    user_id = _request_user_id()
    return jsonify({"success": True, "data": {
        "default": DEFAULT_VOICE,
        "selected": _saved_voice(user_id),
        "voices": [{"id": k, "label": v} for k, v in VOICES.items()],
    }}), 200


@ai_agent_bp.route("/api/v1/ai/voices/select", methods=["POST"])
@require_jwt_auth
def ai_voice_select() -> tuple[Response, int]:
    """Save this user's Ask Nexora voice (Settings → Ask Nexora voice)."""
    from app.services.nexora_voice import VOICES

    user_id = _request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"message": "Login required"}}), 401
    voice = str((request.get_json(silent=True) or {}).get("voice") or "").strip()
    if voice not in VOICES:
        return jsonify({"success": False, "error": {"message": f"Unknown voice '{voice}'"}}), 400
    conn = _voice_conn()
    try:
        conn.execute(
            "INSERT INTO ask_nexora_voice_prefs (user_id, voice, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET voice = excluded.voice, updated_at = excluded.updated_at",
            (user_id, voice),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True, "data": {"selected": voice}}), 200


def _request_user_id() -> int | None:
    user = getattr(request, "user", None)
    return int(user["user_id"]) if isinstance(user, dict) and user.get("user_id") is not None else None


def _voice_conn():
    import sqlite3

    from flask import current_app

    conn = sqlite3.connect(str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ask_nexora_voice_prefs ("
        "user_id INTEGER PRIMARY KEY, voice TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    return conn


def _saved_voice(user_id: int | None) -> str:
    from app.services.nexora_voice import DEFAULT_VOICE, VOICES

    if user_id is None:
        return DEFAULT_VOICE
    conn = _voice_conn()
    try:
        row = conn.execute("SELECT voice FROM ask_nexora_voice_prefs WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] in VOICES else DEFAULT_VOICE


@ai_agent_bp.route("/api/v1/ai/voice", methods=["POST"])
@require_jwt_auth
def ai_voice():
    """Body {text, voice?} -> audio/wav of the answer in a natural voice."""
    from flask import current_app

    from app.services import nexora_ai_agent as agent
    from app.services.nexora_voice import VoiceError, synthesize
    from app.services.visiting_card_ocr import _gemini_key

    user = getattr(request, "user", None)
    user_id = int(user["user_id"]) if isinstance(user, dict) and user.get("user_id") is not None else None
    if user_id is None:
        return jsonify({"success": False, "error": {"message": "Login required"}}), 401
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": {"message": "text is required"}}), 400
    # Replaying a cached answer is free, so only new audio counts toward the
    # daily limit; synthesize() serves cache hits before calling Gemini.
    limit_hit = agent.over_daily_limit(user_id, "voice")
    try:
        audio, info = synthesize(
            text[:6000],
            # Settings sample button sends a voice; answers use the saved one.
            voice=str(payload.get("voice") or "") or _saved_voice(user_id),
            api_key=_gemini_key(),
            db_path=str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3"),
            cache_only=limit_hit,
        )
    except VoiceError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 503
    if not info.get("cached"):
        agent._log_usage(
            user_id=user_id, feature="nexora_voice", model=str(info.get("model") or ""), rounds=1,
            prompt_tokens=int(info.get("prompt_tokens") or 0), output_tokens=int(info.get("output_tokens") or 0),
            total_tokens=int(info.get("prompt_tokens") or 0) + int(info.get("output_tokens") or 0),
            query=f"voice {info.get('voice')} {info.get('chars')} chars",
        )
    resp = Response(audio, mimetype="audio/wav")
    resp.headers["X-Nexora-Voice"] = str(info.get("voice"))
    resp.headers["X-Nexora-Voice-Cached"] = "1" if info.get("cached") else "0"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


# --- Ask Nexora actions: confirm / cancel what the AI proposed -------------


def _actions_ids():
    from flask import current_app

    from app.routes.auth import get_workspace_id

    user_id = _request_user_id()
    return user_id, str(get_workspace_id() or "default"), str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3")


@ai_agent_bp.route("/api/v1/ai/actions/pending", methods=["GET"])
@require_jwt_auth
def ai_action_pending() -> tuple[Response, int]:
    from app.services import nexora_actions

    user_id, ws, db_path = _actions_ids()
    if user_id is None:
        return jsonify({"success": False, "error": {"message": "Login required"}}), 401
    return jsonify({"success": True, "data": {"action": nexora_actions.pending(db_path, user_id=user_id, workspace_id=ws)}}), 200


@ai_agent_bp.route("/api/v1/ai/actions/<int:action_id>/confirm", methods=["POST"])
@require_jwt_auth
def ai_action_confirm(action_id: int) -> tuple[Response, int]:
    from app.services import nexora_actions

    user_id, ws, db_path = _actions_ids()
    if user_id is None:
        return jsonify({"success": False, "error": {"message": "Login required"}}), 401
    out = nexora_actions.confirm(db_path, action_id=action_id, user_id=user_id, workspace_id=ws)
    return jsonify({"success": bool(out.get("ok")), "data": out}), 200


@ai_agent_bp.route("/api/v1/ai/actions/<int:action_id>/cancel", methods=["POST"])
@require_jwt_auth
def ai_action_cancel(action_id: int) -> tuple[Response, int]:
    from app.services import nexora_actions

    user_id, ws, db_path = _actions_ids()
    if user_id is None:
        return jsonify({"success": False, "error": {"message": "Login required"}}), 401
    out = nexora_actions.cancel(db_path, action_id=action_id, user_id=user_id, workspace_id=ws)
    return jsonify({"success": bool(out.get("ok")), "data": out}), 200
