"""Executive workspace APIs — real data from existing NEXORA tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app.business_platform.business_brain import BusinessBrain
from app.routes.auth import get_workspace_id, require_jwt_auth, require_role
from app.fiscal_year import normalize_fiscal_year
from centralized_db_system.db import CentralizedDB

executive_bp = Blueprint("executive", __name__, url_prefix="/api/v1/executive")


def _db() -> CentralizedDB:
    return CentralizedDB(current_app.config["DATABASE_PATH"])


def _current_user() -> dict:
    return getattr(request, "user", None) or {}


def _user_id() -> int | None:
    raw = _current_user().get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _target_achievement_summary(workspace_id: str) -> dict:
    """Read target data using whatever column names exist in this workspace DB."""
    empty = {
        "has_target": False,
        "label": None,
        "target": 0,
        "achievement": 0,
        "percentage": 0,
        "unit": "lakhs",
    }
    db_path = current_app.config["DATABASE_PATH"]
    db = CentralizedDB(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE workspace_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            if not row:
                return empty
            data = dict(row)
            year_id = int(data["id"])
            raw_label = data.get("financial_year") or data.get("year") or ""
            label = normalize_fiscal_year(raw_label) or raw_label or None
            target = float(data.get("target_amount") or data.get("target") or 0)

            achievement = sum(
                item.get("achievement_lakhs") or 0
                for item in db.list_target_distributor_breakup(workspace_id, year_id)
            )
            if not achievement:
                achievement = float(data.get("achievement_amount") or data.get("achievement") or 0)

            pct = round((achievement / target) * 100, 2) if target > 0 else 0.0
            return {
                "has_target": target > 0,
                "year_id": year_id,
                "label": label,
                "target": target,
                "achievement": achievement,
                "percentage": pct,
                "unit": "lakhs",
            }
    except sqlite3.OperationalError:
        return empty


@executive_bp.route("/home", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def executive_home():
    try:
        workspace_id = get_workspace_id()
        user = _current_user()
        db = _db()
        counts = db.count_master_parties(workspace_id)
        tracking = db.list_order_lifecycle_tracking(workspace_id=workspace_id, limit=200)
        pending = db.build_executive_pending_actions(workspace_id, user_id=_user_id())
        visits = db.list_executive_visits(workspace_id=workspace_id, limit=10)
        target = _target_achievement_summary(workspace_id)

        article_count = 0
        with sqlite3.connect(db.db_path) as conn:
            try:
                if db._table_has_column("article_master", "workspace_id"):
                    row = conn.execute(
                        "SELECT COUNT(*) FROM article_master WHERE workspace_id = ?",
                        (workspace_id,),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM article_master").fetchone()
                article_count = int(row[0] or 0) if row else 0
            except sqlite3.OperationalError:
                article_count = 0

        filled_count = 0
        uid = _user_id()
        if uid is not None:
            with sqlite3.connect(db.db_path) as conn:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM filled_orders WHERE user_id = ?",
                        (uid,),
                    ).fetchone()
                    filled_count = int(row[0] or 0) if row else 0
                except sqlite3.OperationalError:
                    filled_count = 0

        alerts = [a for a in pending if a.get("severity") in {"high", "medium"}][:15]

        return jsonify(
            {
                "success": True,
                "data": {
                    "user": {
                        "username": user.get("username"),
                        "role": user.get("role"),
                        "workspace_id": workspace_id,
                    },
                    "counts": {
                        **counts,
                        "tracking_records": len(tracking),
                        "filled_orders": filled_count,
                        "articles": article_count,
                        "pending_actions": len(pending),
                    },
                    "target_achievement": target,
                    "pending_actions": pending,
                    "alerts": alerts,
                    "order_status": tracking,
                    "recent_visits": visits,
                },
            }
        )
    except Exception as exc:
        return jsonify(
            {"success": False, "error": {"message": str(exc), "code": "EXECUTIVE_HOME_ERROR"}}
        ), 500


@executive_bp.route("/party/<party_type>/<int:party_id>", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def party_360(party_type: str, party_id: int):
    workspace_id = get_workspace_id()
    db = _db()
    party_type = party_type.lower().strip()
    if party_type not in {"distributor", "retailer"}:
        return jsonify({"success": False, "error": {"message": "party_type must be distributor or retailer"}}), 400

    table = "master_distributors" if party_type == "distributor" else "master_retailers"
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        query = f"SELECT * FROM {table} WHERE id = ?"
        params: list = [party_id]
        if db._table_has_column(table, "workspace_id"):
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        row = conn.execute(query, tuple(params)).fetchone()
    if not row:
        fallback_table = "distributors" if party_type == "distributor" else "retailers"
        with sqlite3.connect(db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    f"SELECT * FROM {fallback_table} WHERE id = ? AND workspace_id = ?",
                    (party_id, workspace_id),
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
    if not row:
        return jsonify({"success": False, "error": {"message": "Party not found"}}), 404

    party = dict(row)
    tracking: list[dict] = []
    filled_orders: list[dict] = []
    if party_type == "distributor":
        tracking = [
            t
            for t in db.list_order_lifecycle_tracking(workspace_id=workspace_id, limit=200)
            if t.get("distributor_id") == party_id
        ]
        uid = _user_id()
        if uid is not None:
            with sqlite3.connect(db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """
                        SELECT id, category, season, total_lines, matched_lines, created_at
                        FROM filled_orders
                        WHERE user_id = ? AND distributor_id = ?
                        ORDER BY id DESC LIMIT 20
                        """,
                        (uid, party_id),
                    ).fetchall()
                    filled_orders = [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    filled_orders = []

    visits = db.list_executive_visits(
        workspace_id=workspace_id, party_type=party_type, party_id=party_id, limit=20
    )

    outstanding = None
    try:
        outstanding = BusinessBrain.calculate_outstanding(workspace_id, party_id)
    except Exception:
        outstanding = None

    phone = (
        party.get("phone_number")
        or party.get("phone")
        or party.get("phone_number_2")
        or party.get("contact_phone")
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "party": party,
                "party_type": party_type,
                "phone": phone,
                "tracking": tracking,
                "filled_orders": filled_orders,
                "visits": visits,
                "outstanding": outstanding,
            },
        }
    )


@executive_bp.route("/visits", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_visits():
    workspace_id = get_workspace_id()
    limit = request.args.get("limit", 50, type=int)
    party_type = request.args.get("party_type") or None
    party_id = request.args.get("party_id", type=int)
    visits = _db().list_executive_visits(
        workspace_id=workspace_id,
        limit=limit,
        party_type=party_type,
        party_id=party_id,
    )
    return jsonify({"success": True, "data": {"visits": visits}})


@executive_bp.route("/visits", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def create_visit():
    workspace_id = get_workspace_id()
    user = _current_user()
    data = request.get_json(silent=True) or {}
    party_type = (data.get("party_type") or "").strip().lower()
    party_name = (data.get("party_name") or "").strip()
    visit_date = (data.get("visit_date") or "").strip()
    if party_type not in {"distributor", "retailer"} or not party_name or not visit_date:
        return jsonify(
            {
                "success": False,
                "error": {"message": "party_type, party_name, and visit_date are required"},
            }
        ), 400

    visit_id = _db().create_executive_visit(
        workspace_id=workspace_id,
        user_id=_user_id(),
        username=user.get("username"),
        party_type=party_type,
        party_id=data.get("party_id"),
        party_name=party_name,
        visit_date=visit_date,
        notes=(data.get("notes") or "").strip() or None,
        follow_up_date=(data.get("follow_up_date") or "").strip() or None,
    )
    return jsonify({"success": True, "data": {"visit_id": visit_id}}), 201
