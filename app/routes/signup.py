"""Phase 2 of multi-tenant SaaS conversion: self-service company signup.

A brand-new customer registers here and gets a fully isolated tenant —
own company row, own workspace_id, own admin login — with zero contact
with House of Prizm's or Bombay Dyeing's data. Phase 1 (companies /
workspace_registry tables) already exists; this just provisions into it.

Phase 3 (auditing every existing query for consistent workspace/company
filtering) is NOT done — until that's complete, a new tenant signed up
here is only as isolated as the rest of the codebase actually enforces.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from app.routes.auth import _get_auth_db, get_jwt_service

signup_bp = Blueprint("signup", __name__, url_prefix="/api/v1")

# Maps the signup form's plain-language category to the actual role the
# rest of the app already understands (UserSession.isBdUser/isHopUser /
# roleLabel() on the Android side route purely off this string).
# "business" now works because hop.py's _ws() was fixed to read the
# caller's real workspace_id instead of hardcoding "house_of_prizm".
CATEGORY_TO_ROLE = {
    "service": "sales_executive",
    "business": "hop_admin",
    "distributor": "distributor",
    "retailer": "retailer",
}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "company"


@signup_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    company_name = (data.get("company_name") or "").strip()
    full_name = (data.get("full_name") or "").strip() or None
    phone = (data.get("phone") or "").strip() or None
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip() or None
    category = (data.get("category") or "").strip().lower()

    if not company_name:
        return jsonify({"success": False, "error": {"message": "company_name is required"}}), 400
    if not username:
        return jsonify({"success": False, "error": {"message": "username is required"}}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": {"message": "password must be at least 8 characters"}}), 400
    role = CATEGORY_TO_ROLE.get(category)
    if not role:
        return jsonify({
            "success": False,
            "error": {"message": f"category must be one of: {', '.join(CATEGORY_TO_ROLE)}"},
        }), 400

    db = _get_auth_db()

    base_workspace = _slugify(company_name)
    workspace_id = base_workspace
    suffix = 1
    while db.workspace_id_taken(workspace_id):
        suffix += 1
        workspace_id = f"{base_workspace}_{suffix}"

    company_id = db.create_company(name=company_name)
    db.register_workspace_for_company(workspace_id, company_id)

    try:
        created = db.create_user(
            username,
            password,
            role=role,
            workspace_id=workspace_id,
            email=email,
        )
    except ValueError as exc:
        # Username/email already taken — the company + workspace rows
        # created above are harmless orphans (no data ever written under
        # them), left for a human to clean up rather than risking a
        # partial-delete bug in a request that's about to 409 anyway.
        return jsonify({"success": False, "error": {"message": str(exc)}}), 409

    user_id = int(created["id"])
    db.set_company_owner(company_id, user_id)
    # Deliberately NOT is_workspace_owner=True: the Android app treats
    # that flag as "show the Founder's multi-shell Hub Chooser" (incl.
    # Admin Zone) — UserSession.usesFounderShellHub() == isWorkspaceOwner.
    # A new tenant only ever has the one shell/role picked at signup, so
    # they should land straight in it, not see admin-hub UI they have no
    # real backend admin rights to use anyway (that's gated separately,
    # by literal ADMIN_USERNAME match, not this flag).
    if full_name or phone:
        try:
            db.update_user_profile(user_id, full_name=full_name, phone=phone)
        except ValueError:
            pass

    service = get_jwt_service()
    access_token, refresh_token = service.create_tokens(
        user_id=user_id,
        username=created["username"],
        role=role,
        workspace_id=workspace_id,
        is_workspace_owner=False,
    )

    return jsonify({
        "success": True,
        "data": {
            "company_id": company_id,
            "company_name": company_name,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "username": created["username"],
            "role": role,
            "full_name": full_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": service.access_token_expiry,
            "token_type": "Bearer",
        },
    }), 201
