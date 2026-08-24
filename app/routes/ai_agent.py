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
