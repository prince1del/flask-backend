"""NEXORA Ask — chat API over filled orders."""

import os

from flask import Blueprint, current_app, jsonify, request

import nexora_ask
import nexora_ask_learn as learn
from app.routes.auth import get_workspace_id, require_jwt_auth

nexora_ask_bp = Blueprint("nexora_ask", __name__, url_prefix="/api/v1/nexora")


def _get_db_connection():
    import sqlite3

    db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    return sqlite3.connect(db_path)


def _get_current_user_id() -> int:
    user = getattr(request, "user", None)
    if isinstance(user, dict) and user.get("user_id") is not None:
        return int(user["user_id"])
    raise RuntimeError("Authentication required")


@nexora_ask_bp.route("/ask", methods=["POST"])
@require_jwt_auth
def ask_nexora():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    use_llm = bool(payload.get("use_llm")) or (
        (current_app.config.get("NEXORA_ASK_LLM") or os.environ.get("NEXORA_ASK_LLM") or "")
        .lower() in ("1", "true", "yes")
    )

    conn = _get_db_connection()
    db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    try:
        result = nexora_ask.answer_question(
            conn,
            _get_current_user_id(),
            question,
            workspace_id=get_workspace_id(),
            db_path=db_path,
            _use_llm=use_llm,
        )
    finally:
        conn.close()

    return jsonify({
        "status": "success",
        "question": question,
        "answer": result.get("answer"),
        "intent": result.get("intent"),
        "data": {
            **(result.get("data") or {}),
            "isolation": {
                "user_id": _get_current_user_id(),
                "workspace_id": get_workspace_id(),
            },
        },
    }), 200


@nexora_ask_bp.route("/ask/teach", methods=["POST"])
@require_jwt_auth
def teach_nexora_phrase():
    """
    Train Ask NEXORA without code changes.
    Body: { "user_phrase": "jatin kaun h", "canonical_question": "jatin arora kon hai" }
    Optional: "suggest_with_llm": true — uses GEMINI_API_KEY to suggest canonical_question.
    """
    payload = request.get_json(silent=True) or {}
    user_phrase = (payload.get("user_phrase") or payload.get("phrase") or "").strip()
    canonical = (payload.get("canonical_question") or payload.get("canonical") or "").strip()

    if payload.get("suggest_with_llm") and user_phrase and not canonical:
        canonical, _llm_err = learn.llm_suggest_canonical(user_phrase)

    if payload.get("dry_run"):
        if not user_phrase:
            return jsonify({"error": "user_phrase is required"}), 400
        llm_error = None
        if payload.get("suggest_with_llm") and not canonical:
            _, llm_error = learn.llm_suggest_canonical(user_phrase)
        return jsonify({
            "status": "success" if canonical else "error",
            "data": {
                "user_phrase": user_phrase,
                "canonical_question": canonical or None,
                "gemini_configured": learn.gemini_configured(),
                "error": llm_error,
            },
        }), 200

    if not user_phrase or not canonical:
        return jsonify({
            "error": "user_phrase and canonical_question are required (or suggest_with_llm + user_phrase)",
        }), 400

    conn = _get_db_connection()
    try:
        row = learn.teach_phrase(
            conn,
            workspace_id=get_workspace_id(),
            user_phrase=user_phrase,
            canonical_question=canonical,
            created_by=_get_current_user_id(),
            source="llm" if payload.get("suggest_with_llm") else "manual",
        )
    finally:
        conn.close()

    return jsonify({"status": "success", "data": row}), 200


@nexora_ask_bp.route("/ask/gemini-status", methods=["GET"])
@require_jwt_auth
def nexora_gemini_status():
    configured = learn.gemini_configured()
    return jsonify({
        "status": "success",
        "data": {
            "configured": configured,
            "help_url": "https://aistudio.google.com/apikey",
        },
    }), 200


@nexora_ask_bp.route("/ask/learned", methods=["GET"])
@require_jwt_auth
def list_learned_phrases():
    conn = _get_db_connection()
    try:
        rows = learn.list_phrases(
            conn,
            get_workspace_id(),
            owner_user_id=_get_current_user_id(),
            limit=int(request.args.get("limit", 50)),
        )
    finally:
        conn.close()
    return jsonify({"status": "success", "data": {"phrases": rows}}), 200


@nexora_ask_bp.route("/ask/examples", methods=["GET"])
@require_jwt_auth
def ask_examples():
    workspace_id = get_workspace_id()
    user_id = _get_current_user_id()
    if workspace_id == "house_of_prizm":
        import hop_ask

        return jsonify({
            "examples": list(hop_ask.HOP_ASK_EXAMPLES),
            "isolation": {"user_id": user_id, "workspace_id": workspace_id},
        }), 200
    return jsonify({
        "examples": [
            "Bernina ne Florentine King bedsheet mein kitna qty order kiya?",
            "Bernina ka GST number aur address?",
            "FY 2024-2025 target achievement kitna hai?",
            "Kalra Agencies AW26 Towel total kitna hai?",
            "Aster ka ex mill kitna hai?",
            "AW26 Towel season mein sab distributors ka total?",
        ],
        "isolation": {"user_id": user_id, "workspace_id": workspace_id},
    }), 200
