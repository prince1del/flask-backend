"""Phase 2 of multi-tenant SaaS conversion: self-service company signup.

A brand-new customer registers here and gets a fully isolated tenant —
own company row, own workspace_id, own admin login — with zero contact
with House of Prizm's or Bombay Dyeing's data. Phase 1 (companies /
workspace_registry tables) already exists; this just provisions into it.

With ``invite_code`` (see app/routes/invites.py) the new login joins the
inviter's existing company and workspace instead. Every self sign-up starts
a 7-day trial (app/services/subscriptions.py).

Phase 3 (auditing every existing query for consistent workspace/company
filtering) is NOT done — until that's complete, a new tenant signed up
here is only as isolated as the rest of the codebase actually enforces.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from app.routes.auth import _get_auth_db, get_jwt_service
from app.services import company_invites as ci
from app.services import subscriptions as subs

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


@signup_bp.route("/signup/send-code", methods=["POST"])
def signup_send_code():
    """Step 1 of sign-up: email a 6-digit code to the address typed on the form."""
    from app.services import signup_email_code as sec
    from app.services.outbound_mail import send_plain_email, smtp_configured

    if not smtp_configured():
        return jsonify({"success": False, "error": {"message": "Email codes are not set up — sign up without a code."}}), 503
    email = (request.get_json(silent=True) or {}).get("email") or ""
    db = _get_auth_db()
    if sec.email_taken(str(db.db_path), email):
        return jsonify({"success": False, "error": {
            "message": "This email already has an account — sign in, or use Forgot user ID / Forgot password."}}), 409
    try:
        code = sec.new_code(str(db.db_path), email)
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    ok, err = send_plain_email(
        to=email.strip(), subject="Your NEXORA sign-up code",
        body=(f"Your NEXORA sign-up code is: {code}\n\n"
              "It is valid for 15 minutes. If you did not try to sign up, ignore this email."))
    if not ok:
        return jsonify({"success": False, "error": {"message": f"Could not send the email: {err}"}}), 502
    return jsonify({"success": True, "message": "Code sent — check your email (and Spam)."}), 200


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
    secret_question = (data.get("secret_question") or "").strip()
    secret_answer = (data.get("secret_answer") or "").strip()
    # With a teammate's invite code the login joins that company instead of
    # creating a new one; company and role then come from the invite.
    invite_code = (data.get("invite_code") or "").strip()

    if not company_name and not invite_code:
        return jsonify({"success": False, "error": {"message": "company_name is required"}}), 400
    if not username:
        return jsonify({"success": False, "error": {"message": "username is required"}}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": {"message": "password must be at least 8 characters"}}), 400
    if not email or "@" not in email:
        return jsonify({"success": False, "error": {"message": "a valid email is required"}}), 400
    if not secret_question or not secret_answer:
        return jsonify({
            "success": False,
            "error": {"message": "secret_question and secret_answer are required"},
        }), 400
    # Email check: with email sending set up, the account is made only with the code from that email.
    from app.services.outbound_mail import smtp_configured

    if smtp_configured():
        from app.services import signup_email_code as sec

        ok_code, code_msg = sec.check_code(str(_get_auth_db().db_path), email, data.get("email_code") or "", consume=False)
        if not ok_code:
            return jsonify({"success": False, "error": {"code": "EMAIL_CODE", "message": code_msg}}), 400
    role = CATEGORY_TO_ROLE.get(category)
    if not role and not invite_code:
        return jsonify({
            "success": False,
            "error": {"message": f"category must be one of: {', '.join(CATEGORY_TO_ROLE)}"},
        }), 400

    db = _get_auth_db()
    invite = None

    if invite_code:
        try:
            invite = ci.claim_invite(db, invite_code)
        except ValueError as exc:
            return jsonify({"success": False, "error": {"message": str(exc)}}), 400
        workspace_id = invite["workspace_id"]
        role = invite["role"]
        company_id = db.get_company_id_for_workspace(workspace_id)
        company_name = ci.company_name_for_workspace(db, workspace_id) or company_name or None
    else:
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
        # Username/email already taken — for a new company the company +
        # workspace rows created above are harmless orphans (no data ever
        # written under them), left for a human to clean up rather than
        # risking a partial-delete bug in a request that's about to 409.
        if invite:
            ci.release_invite(db, invite["id"])
        return jsonify({"success": False, "error": {"message": str(exc)}}), 409

    user_id = int(created["id"])
    try:
        db.set_secret_question(user_id, secret_question, secret_answer)
    except ValueError as exc:
        # Roll back the login so we don't leave an incomplete account.
        try:
            db.delete_login_user(user_id)
        except Exception:
            pass
        if invite:
            ci.release_invite(db, invite["id"])
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400

    if invite:
        ci.record_redemption(db, invite["id"], user_id, created["username"])
        from app.services import member_access

        member_access.grant_on_join(db.db_path, user_id, workspace_id, invite.get("features"))
    else:
        db.set_company_owner(company_id, user_id)
    # A new company gets the 7-day trial; a teammate joining by invite follows
    # the company's plan instead (subscriptions._effective_plan).
    trial_ends_at = None if invite else subs.start_trial(db.db_path, user_id)
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
    session_id = None
    if not invite:
        # every new login is tied to its device from the first sign-in (owner 21-11)
        import uuid

        from app.jwt_service import client_slot

        session_id = uuid.uuid4().hex
        try:
            db.set_active_session(user_id, session_id, data.get("device_label"), slot=client_slot())
        except Exception:
            session_id = None
    if invite:
        # A teammate's first sign-in also waits for the owner's approval.
        import uuid

        from app.services import login_approval

        from app.jwt_service import client_slot

        session_id = uuid.uuid4().hex
        db.set_active_session(user_id, session_id, data.get("device_label"), slot=client_slot())
        login_approval.request_login(db.db_path, user_id, workspace_id, session_id,
                                     data.get("device_label"), request.remote_addr)
    access_token, refresh_token = service.create_tokens(
        user_id=user_id,
        username=created["username"],
        role=role,
        workspace_id=workspace_id,
        is_workspace_owner=False,
        session_id=session_id,
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
            "joined_existing_company": invite is not None,
            "trial_ends_at": trial_ends_at,
            "full_name": full_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": service.access_token_expiry,
            "token_type": "Bearer",
        },
    }), 201
