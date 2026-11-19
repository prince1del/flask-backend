"""House of Prizm APIs — isolated from NEXORA executive routes.

Role gate: hop_admin only.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from app import hop_db, hop_ops, hop_deals
from app.hop_schema import (
    DEAL_STAGES,
    DEAL_STEPS,
    HOP_ROLE,
    LEAD_STAGES,
    PROJECT_STAGES,
    ensure_hop_schema,
)
from app.routes.auth import get_workspace_id, require_jwt_auth, require_role

hop_bp = Blueprint("hop", __name__, url_prefix="/api/v1/hop")


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _ws() -> str:
    """The calling user's own workspace — was hardcoded to
    HOP_WORKSPACE_ID ("house_of_prizm") regardless of who was actually
    logged in, so every hop_admin-role account (present or future)
    operated on House of Prizm's real data. House of Prizm's own login
    already carries workspace_id="house_of_prizm" in its JWT, so this is
    a no-op for that account — only a NEW hop_admin signup (a different
    "business" category tenant) now correctly gets its own workspace
    instead of colliding with this one.

    Multiple firms: the phone / web send the chosen firm in ``X-Hop-Firm``; only a firm of
    this login's own company is accepted (see app/hop_firms.py)."""
    base = get_workspace_id()
    # Cache on this request only (never flask.g: an app context can outlive one request).
    cached = getattr(request, "_hop_active_ws", None)
    if cached and cached[0] == base:
        return cached[1]
    req = request.headers.get("X-Hop-Firm") or ""
    ws = base
    if req and req != base:
        from app import hop_firms

        try:
            with hop_db.connect(_db_path()) as conn:
                ws = hop_firms.active_ws(conn, base, req)
        except Exception:
            ws = base
    try:
        request._hop_active_ws = (base, ws)
    except Exception:
        pass
    return ws


@hop_bp.before_request
def _audit_before():
    """Keep a bill's old number / amount before it is edited or deleted (audit trail)."""
    from flask import g

    from app import hop_audit

    g.hop_audit_before = None
    if request.method in ("PATCH", "PUT", "DELETE") and hop_audit.should_log(request.method, request.path):
        try:
            with hop_db.connect(_db_path()) as conn:
                g.hop_audit_before = hop_audit.snapshot(conn, request.path)
        except Exception:
            g.hop_audit_before = None


@hop_bp.after_request
def _audit_after(response):
    """Audit trail: record every successful change (phone and web)."""
    from flask import g

    from app import hop_audit

    try:
        if 200 <= response.status_code < 300 and hop_audit.should_log(request.method, request.path):
            user = getattr(request, "user", None) or {}
            if isinstance(user, dict) and user:
                ws = _ws()
                before = getattr(g, "hop_audit_before", None)
                if before and before.pop("workspace_id", None) != ws:
                    before = None
                body = request.get_json(silent=True) if request.is_json else None
                result = None
                if response.is_json:
                    result = (response.get_json(silent=True) or {}).get("data")
                with hop_db.connect(_db_path()) as conn:
                    hop_audit.record(conn, ws, user, request.method, request.path, body, result, before)
    except Exception:  # the audit log must never break the real request
        current_app.logger.exception("hop audit log failed")
    return response


def _json_error(message: str, code: str = "BAD_REQUEST", status: int = 400):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _force_flag(payload: dict | None = None) -> bool:
    """Accept force / force_delete from query or JSON body."""
    data = payload if isinstance(payload, dict) else {}
    raw = (
        request.args.get("force")
        or request.args.get("force_delete")
        or data.get("force")
        or data.get("force_delete")
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y"}


def _party_in_use_response(exc) -> tuple:
    usage = getattr(exc, "usage", None) or {}
    summary = usage.get("summary") or "linked records"
    return (
        jsonify(
            {
                "success": False,
                "requires_confirmation": True,
                "message": (
                    f"This party is used in {summary}. "
                    "Delete anyway? Linked records will keep names but the party link will be removed."
                ),
                "error": {"code": "PARTY_IN_USE", "message": str(exc)},
                "data": {"usage": usage},
            }
        ),
        409,
    )


def _unique_party_names(matches: list) -> list[str]:
    """Distinct company names for user-facing duplicate copy (no scores)."""
    seen: set[str] = set()
    names: list[str] = []
    for m in matches or []:
        name = str((m or {}).get("company") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _party_duplicate_confirm_payload(matches: list) -> tuple[dict, int]:
    """Professional 409 body — names only, no % / fuzzy jargon."""
    names = _unique_party_names(matches)
    if not names:
        message = "A party with a similar name is already saved. Do you really want to save this as a new party?"
    elif len(names) == 1:
        message = (
            f'A party is already saved as "{names[0]}". '
            "Do you really want to save this as a new party?"
        )
    else:
        listed = ", ".join(f'"{n}"' for n in names[:3])
        message = (
            f"Similar parties are already saved: {listed}. "
            "Do you really want to save this as a new party?"
        )
    # Deduped matches for UI (keep best score per name, drop technical noise from message).
    deduped = []
    seen_keys: set[str] = set()
    for m in matches or []:
        key = str((m or {}).get("company") or "").strip().casefold()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(m)
    return (
        {
            "success": False,
            "requires_confirmation": True,
            "message": message,
            "data": {"matches": deduped},
        },
        409,
    )


def _maybe_party_duplicate_response(
    payload: dict,
    *,
    exclude_id: int | None = None,
    exclude_party_type: str | None = None,
):
    """Return 409 jsonify tuple if similar parties exist (unless force_save)."""
    if payload.get("force_save"):
        return None
    from app.hop_party_match import find_party_matches

    with hop_db.connect(_db_path()) as conn:
        matches = find_party_matches(
            conn,
            _ws(),
            company=str(payload.get("company") or ""),
            gst_no=str(payload.get("gst_no") or ""),
            mobile=str(payload.get("mobile") or ""),
            party_type="both",
            exclude_id=exclude_id,
            exclude_party_type=exclude_party_type,
        )
    if not matches:
        return None
    body, status = _party_duplicate_confirm_payload(matches)
    return jsonify(body), status


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _sum(conn: sqlite3.Connection, sql: str, params: tuple) -> float:
    row = conn.execute(sql, params).fetchone()
    return float(row[0] or 0) if row else 0.0


def _ebitda_fy(ws: str) -> dict | None:
    """EBITDA for this financial year so far (India date), from the books."""
    try:
        from app import hop_reports, hop_targets
        from app.hop_time import today_ist

        t = today_ist()
        fy = hop_targets.fy_of(t)
        with hop_db.connect(_db_path()) as conn:
            pl = hop_reports.profit_and_loss(conn, ws, f"{fy}-04-01", t.isoformat())
        return {"amount": pl["ebitda"], "net_profit": pl["net_profit"], "fy": hop_targets.fy_label(fy), **pl["ebitda_parts"]}
    except Exception:
        return None


def _cash_available(ws: str) -> float | None:
    """Cash in hand + all bank balances (as on the Cash & bank screen)."""
    try:
        from app import hop_books

        with hop_db.connect(_db_path()) as conn:
            hop_books.list_bank_accounts(conn, ws)   # makes sure the Cash account exists
            return round(hop_books.cash_available(conn, ws), 2)
    except Exception:
        return None


def _payload():
    return request.get_json(silent=True) or {}


def _uploaded_backup_file():
    f = request.files.get("backup_file") or request.files.get("file")
    if not f or not getattr(f, "filename", None):
        return None, _json_error("backup_file is required", "VALIDATION_ERROR", 400)
    return f, None


def _business_name() -> str:
    from app import hop_firms

    try:
        with hop_db.connect(_db_path()) as conn:
            return hop_firms.business_name(conn, _ws())
    except Exception:
        return ""


@hop_bp.route("/meta", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_meta():
    ensure_hop_schema(_db_path())
    return jsonify(
        {
            "success": True,
            "data": {
                "product": "NEXORA Business",
                "business_name": _business_name(),
                "architecture": "project_centric",
                "workspace_id": _ws(),
                "project_stages": PROJECT_STAGES,
                "lead_stages": LEAD_STAGES,
                "deal_steps": DEAL_STEPS,
                "deal_stages": DEAL_STAGES,
            },
        }
    )


@hop_bp.route("/health", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_health():
    ensure_hop_schema(_db_path())
    return jsonify(
        {
            "success": True,
            "data": {
                "product": "NEXORA Business",
                "business_name": _business_name(),
                "role": HOP_ROLE,
                "workspace_id": _ws(),
                "schema": "ready",
            },
        }
    )


@hop_bp.route("/gstin-lookup", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_gstin_lookup():
    """Decode GSTIN (state/PAN) and optionally fetch taxpayer address online."""
    from app.services.gstin_lookup import lookup_gstin, normalize_gstin

    body = _payload() if request.method == "POST" else {}
    gstin = normalize_gstin(body.get("gstin") or request.args.get("gstin") or "")
    if not gstin:
        return _json_error("gstin is required", "VALIDATION_ERROR", 400)
    try:   # live from the GST portal when the IRP login is switched on
        from app import hop_gov_einvoice

        with hop_db.connect(_db_path()) as conn:
            live = hop_gov_einvoice.gstin_details(conn, _ws(), gstin)
        if live:
            base = lookup_gstin(gstin) or {}
            return jsonify({"success": True, "data": {**base, **{k: v for k, v in live.items() if v}}})
    except Exception:
        pass
    data = lookup_gstin(gstin)
    return jsonify({"success": True, "data": data})


@hop_bp.route("/vyapar-import/preview", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vyapar_import_preview():
    ensure_hop_schema(_db_path())
    uploaded, err = _uploaded_backup_file()
    if err:
        return err
    from app.services.vyapar_importer import preview_vyapar_backup

    try:
        data = preview_vyapar_backup(uploaded.read(), uploaded.filename or "backup.vyb")
    except ValueError as exc:
        return _json_error(str(exc), "BAD_BACKUP", 400)
    except Exception as exc:
        return _json_error(f"Preview failed: {exc}", "IMPORT_PREVIEW_FAILED", 500)
    return jsonify({"success": True, "data": data})


@hop_bp.route("/vyapar-import/apply", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vyapar_import_apply():
    ensure_hop_schema(_db_path())
    uploaded, err = _uploaded_backup_file()
    if err:
        return err
    from app import hop_server_backups
    from app.services.vyapar_importer import import_vyapar_backup

    try:
        with hop_db.connect(_db_path()) as conn:
            try:
                hop_server_backups.snapshot(conn, _ws(), "before_import")   # can be put back from Backups on server
            except Exception:
                pass
            result = import_vyapar_backup(
                file_bytes=uploaded.read(),
                filename=uploaded.filename or "backup.vyb",
                target_conn=conn,
                workspace_id=_ws(),
                hop_db_module=hop_db,
                hop_ops_module=hop_ops,
            )
    except ValueError as exc:
        return _json_error(str(exc), "BAD_BACKUP", 400)
    except Exception as exc:
        return _json_error(f"Import failed: {exc}", "IMPORT_FAILED", 500)
    return jsonify({"success": True, "data": result})


@hop_bp.route("/settings/wipe-data", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def settings_wipe_data():
    """Wipe all hop_* business data after verifying the signed-in user's password."""
    ensure_hop_schema(_db_path())
    payload = _payload()
    password = str(payload.get("password") or "")
    if not password.strip():
        return _json_error(
            "Password is required to wipe this business's data.",
            "PASSWORD_REQUIRED",
            403,
        )

    user = getattr(request, "user", None) or {}
    username = str(user.get("username") or "").strip()
    if not username:
        return _json_error(
            "Authenticated user could not be resolved for password check.",
            "UNAUTHORIZED",
            401,
        )

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(_db_path())
    if not db.authenticate_user(username, password):
        return _json_error(
            "Incorrect password. Wipe blocked.",
            "INVALID_PASSWORD",
            403,
        )

    from app import hop_permissions, hop_server_backups

    try:
        with hop_db.connect(_db_path()) as conn:
            if not hop_permissions.is_owner(conn, user):
                return _json_error("Only the owner can wipe the company's data", "FORBIDDEN", 403)
            hop_server_backups.snapshot(conn, _ws(), "before_wipe", force=True)   # can be put back from Backups on server
            result = hop_ops.wipe_hop_data(conn, _ws())
    except Exception as exc:
        return _json_error(f"Wipe failed: {exc}", "WIPE_FAILED", 500)
    return jsonify({"success": True, "data": result})


@hop_bp.route("/executive/snapshot", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def executive_snapshot():
    ensure_hop_schema(_db_path())
    ws = _ws()
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)   # India date for "today" / "this month"
    today_prefix = now.strftime("%Y-%m-%d")
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).strftime("%Y-%m-%d")
    last_day = monthrange(now.year, now.month)[1]
    month_end = datetime(now.year, now.month, last_day, tzinfo=timezone.utc).strftime("%Y-%m-%d")
    period_label = f"{now.year}-{now.month:02d}"

    with hop_db.connect(_db_path()) as conn:
        new_leads = _count(
            conn,
            "SELECT COUNT(*) FROM hop_leads WHERE workspace_id = ? AND date(created_at) = date(?)",
            (ws, today_prefix),
        )
        meetings_today = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_meetings
            WHERE workspace_id = ? AND date(scheduled_at) = date(?)
              AND lower(coalesce(status,'')) != 'cancelled'
            """,
            (ws, today_prefix),
        )
        pending_followups = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_leads
            WHERE workspace_id = ?
              AND next_follow_up IS NOT NULL AND trim(next_follow_up) != ''
              AND date(next_follow_up) <= date(?)
              AND lower(coalesce(status,'open')) NOT IN ('closed','won','lost')
              AND lower(coalesce(stage,'')) NOT IN ('order_won','lost')
            """,
            (ws, today_prefix),
        )
        quotations_pending = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_quotations
            WHERE workspace_id = ? AND lower(status) IN ('draft', 'pending', 'pending_approval')
            """,
            (ws,),
        )
        quotations_sent = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_quotations
            WHERE workspace_id = ? AND lower(status) IN ('sent', 'negotiation', 'follow_up')
            """,
            (ws,),
        )
        orders_won = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_orders
            WHERE workspace_id = ? AND won_at IS NOT NULL
              AND date(won_at) BETWEEN date(?) AND date(?)
            """,
            (ws, month_start, month_end),
        ) + _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_leads
            WHERE workspace_id = ? AND (
                won_at IS NOT NULL AND date(won_at) BETWEEN date(?) AND date(?)
                OR lower(stage) = 'order_won'
            )
            """,
            (ws, month_start, month_end),
        )
        orders_lost = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_orders
            WHERE workspace_id = ? AND lost_at IS NOT NULL
              AND date(lost_at) BETWEEN date(?) AND date(?)
            """,
            (ws, month_start, month_end),
        ) + _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_leads
            WHERE workspace_id = ? AND (
                lost_at IS NOT NULL AND date(lost_at) BETWEEN date(?) AND date(?)
                OR lower(stage) = 'lost'
            )
            """,
            (ws, month_start, month_end),
        )
        production_orders = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_orders
            WHERE workspace_id = ?
              AND lower(coalesce(production_status, '')) IN
                  ('ordered', 'in_production', 'production', 'qc', 'packed')
            """,
            (ws,),
        ) + _count(
            conn,
            "SELECT COUNT(*) FROM hop_projects WHERE workspace_id = ? AND lower(stage) = 'production'",
            (ws,),
        )
        dispatches_due = _count(
            conn,
            """
            SELECT COUNT(*) FROM hop_dispatches
            WHERE workspace_id = ?
              AND (
                (due_date IS NOT NULL AND date(due_date) <= date(?))
                OR lower(status) IN ('ready', 'ready_to_dispatch')
              )
              AND lower(coalesce(delivery_status, '')) NOT IN ('delivered')
            """,
            (ws, today_prefix),
        )
        outstanding = _sum(
            conn,
            "SELECT COALESCE(SUM(balance), 0) FROM hop_invoices WHERE workspace_id = ? AND balance > 0",
            (ws,),
        )
        payments_today = _sum(
            conn,
            "SELECT COALESCE(SUM(amount), 0) FROM hop_payments WHERE workspace_id = ? AND date(paid_at) = date(?)",
            (ws, today_prefix),
        )
        # Businesses billing in NEXORA (sale bills exist): Home counts come from the real books, not the old CRM lists.
        _live = "(deleted_at IS NULL OR trim(deleted_at)='')"
        if _count(conn, f"SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=? AND txn_type IN (1, 27, 65, 83) AND {_live}", (ws,)):
            quotations_pending = _count(conn, f"""SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=?
                AND txn_type IN (27, 83) AND {_live} AND lower(coalesce(status_text,'')) NOT LIKE 'converted%'
                AND lower(coalesce(status_text,'')) NOT LIKE 'closed%'""", (ws,))
            quotations_sent = _count(conn, f"""SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=?
                AND txn_type IN (27, 83) AND {_live} AND date(txn_date) BETWEEN date(?) AND date(?)""", (ws, month_start, month_end))
            orders_won = _count(conn, f"""SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=?
                AND txn_type=65 AND {_live} AND date(txn_date) BETWEEN date(?) AND date(?)""", (ws, month_start, month_end))
            outstanding = _sum(conn, f"""SELECT COALESCE(SUM(balance_amount), 0) FROM hop_party_transactions
                WHERE workspace_id=? AND txn_type=1 AND {_live} AND COALESCE(balance_amount,0)>0""", (ws,))
            payments_today = float(hop_ops.report_daily_activity(conn, ws, day=today_prefix).get("collections") or 0)
        monthly_sales = _sum(
            conn,
            """
            SELECT COALESCE(SUM(order_value), 0) FROM hop_orders
            WHERE workspace_id = ? AND won_at IS NOT NULL
              AND date(won_at) BETWEEN date(?) AND date(?)
            """,
            (ws, month_start, month_end),
        )
        target_row = conn.execute(
            "SELECT target_amount FROM hop_targets WHERE workspace_id = ? AND period_label = ?",
            (ws, period_label),
        ).fetchone()
        monthly_target = float(target_row[0]) if target_row else 0.0
        # Achieved = sale invoices − sale returns (real billing); CRM orders only when there are no bills at all.
        from app import hop_targets

        billed = hop_targets._achieved(conn, ws, month_start, month_end)
        if billed:
            monthly_sales = billed
        tsum = hop_targets.summary(conn, ws)
        if not monthly_target and tsum["current_month"]:
            monthly_target = tsum["current_month"]["target"]
        profit = hop_ops.report_profitability(conn, ws)

    return jsonify(
        {
            "success": True,
            "data": {
                "workspace_id": ws,
                "as_of": now.isoformat(),
                "today": {
                    "new_leads": new_leads,
                    "meetings_today": meetings_today,
                    "pending_followups": pending_followups,
                    "quotations_pending": quotations_pending,
                    "quotations_sent": quotations_sent,
                    "orders_won": orders_won,
                    "orders_lost": orders_lost,
                    "production_orders": production_orders,
                    "dispatches_due": dispatches_due,
                    "outstanding_receivables": outstanding,
                    "payments_received_today": payments_today,
                },
                "monthly": {
                    "sales": monthly_sales,
                    "target": monthly_target,
                    "period_label": period_label,
                },
                "yearly": {"fy": tsum["fy"], "target": tsum["yearly_target"], "achieved": tsum["achieved_ytd"],
                           "pct": tsum["yearly_pct"]},
                "gross_profit_pct": profit.get("gross_margin_pct"),
                "ebitda": _ebitda_fy(ws),
                "cash_available": _cash_available(ws),   # same figure as Cash & bank
                "notes": {
                    "gross_profit_pct": profit.get("notes", {}).get("cogs"),
                },
            },
        }
    )


# ---------- Customers ----------
@hop_bp.route("/customers", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_list():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_db.list_customers(conn, _ws(), q=request.args.get("q"))
        try:   # quick "money due" for the list: unpaid bills + opening − advances not used yet
            due = {int(r[0]): float(r[1] or 0) for r in conn.execute(
                """SELECT party_id, SUM(CASE WHEN txn_type IN (1, 83) THEN balance_amount
                                             WHEN txn_type = 3 THEN -COALESCE(unused_amount, 0)
                                             WHEN txn_type = 21 THEN -balance_amount ELSE 0 END)
                   FROM hop_party_transactions WHERE workspace_id=? AND party_type='customer'
                     AND (deleted_at IS NULL OR trim(deleted_at)='') GROUP BY party_id""", (_ws(),)).fetchall() if r[0]}
            rows = [dict(r) | {"current_balance": round(due.get(int(r["id"]), 0.0) + float(r.get("opening_balance") or 0), 2)}
                    for r in rows]
        except Exception:
            pass
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/customers", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_create():
    ensure_hop_schema(_db_path())
    payload = _payload()
    blocked = _maybe_party_duplicate_response(payload)
    if blocked:
        return blocked
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.create_customer(conn, _ws(), payload)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/customers/scan-card", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_scan_card():
    """OCR a visiting card image → structured fields. Does NOT save — user confirms in UI."""
    upload = request.files.get("card_image") or request.files.get("file") or request.files.get("image")
    if not upload or not getattr(upload, "filename", None):
        return _json_error("card_image file is required", "VALIDATION_ERROR", 400)
    from app.services.visiting_card_ocr import save_upload_temp, scan_visiting_card

    path = None
    try:
        path = save_upload_temp(upload)
        result = scan_visiting_card(path)
        return jsonify({"success": True, "data": result})
    except MemoryError:
        return _json_error(
            "Server ran out of memory reading the card. Add GEMINI_API_KEY on Render.",
            "OCR_OOM",
            503,
        )
    except Exception as exc:
        return _json_error(f"Card scan failed: {exc}", "OCR_ERROR", 500)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


@hop_bp.route("/customers/<int:customer_id>", methods=["GET", "PATCH", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_get_or_delete(customer_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "PATCH":
        payload = _payload()
        blocked = _maybe_party_duplicate_response(
            payload,
            exclude_id=customer_id,
            exclude_party_type="customer",
        )
        if blocked:
            return blocked
        try:
            with hop_db.connect(_db_path()) as conn:
                row = hop_db.update_customer(conn, _ws(), customer_id, payload)
        except ValueError as exc:
            return _json_error(str(exc), "NOT_FOUND" if "not found" in str(exc).lower() else "BAD_REQUEST", 404 if "not found" in str(exc).lower() else 400)
        except Exception as exc:
            return _json_error(f"Update failed: {exc}", "UPDATE_ERROR", 500)
        return jsonify({"success": True, "data": row})
    if request.method == "DELETE":
        force = _force_flag(_payload())
        try:
            with hop_db.connect(_db_path()) as conn:
                ok = hop_db.delete_customer(conn, _ws(), customer_id, force=force)
        except Exception as exc:
            from app.hop_party_usage import PartyInUseError

            if isinstance(exc, PartyInUseError):
                return _party_in_use_response(exc)
            return _json_error(str(exc), "DELETE_BLOCKED", 409)
        if not ok:
            return _json_error("Customer not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": {"deleted": customer_id}})
    with hop_db.connect(_db_path()) as conn:
        row = hop_db.get_customer(conn, _ws(), customer_id)
        if row:
            row = dict(row)
            try:   # what this customer owes now (+) or has paid in advance (−) — from their statement
                from app import hop_reports

                row["current_balance"] = hop_reports.party_statement(conn, _ws(), "customer", customer_id).get("closing_balance", 0.0)
            except Exception:
                pass
    if not row:
        return _json_error("Customer not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/customers/<int:customer_id>/usage", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_usage(customer_id: int):
    ensure_hop_schema(_db_path())
    from app.hop_party_usage import get_customer_usage

    with hop_db.connect(_db_path()) as conn:
        row = hop_db.get_customer(conn, _ws(), customer_id)
        if not row:
            return _json_error("Customer not found", "NOT_FOUND", 404)
        usage = get_customer_usage(conn, _ws(), customer_id)
    usage["company"] = row.get("company")
    return jsonify({"success": True, "data": usage})


@hop_bp.route("/customers/bulk-delete", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_bulk_delete():
    ensure_hop_schema(_db_path())
    payload = _payload()
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _json_error("ids list is required", "VALIDATION_ERROR", 400)
    try:
        id_list = [int(x) for x in ids]
    except (TypeError, ValueError):
        return _json_error("ids must be integers", "VALIDATION_ERROR", 400)
    force = _force_flag(payload)
    with hop_db.connect(_db_path()) as conn:
        result = hop_db.delete_customers_bulk(conn, _ws(), id_list, force=force)
    if result.get("blocked") and not force:
        return (
            jsonify(
                {
                    "success": False,
                    "requires_confirmation": True,
                    "message": (
                        f"{len(result['blocked'])} contact(s) are used in deals, invoices, or other records. "
                        "Delete anyway? Party links will be removed from those records."
                    ),
                    "error": {"code": "PARTY_IN_USE", "message": "Some contacts are in use"},
                    "data": result,
                }
            ),
            409,
        )
    return jsonify({"success": True, "data": result})


# ---------- Projects ----------
@hop_bp.route("/projects", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def projects_list():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_db.list_projects(conn, _ws(), q=request.args.get("q"))
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/projects", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def projects_create():
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.create_project(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/projects/<int:project_id>", methods=["GET", "PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def projects_get_or_patch(project_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.get_project(conn, _ws(), project_id)
        if not row:
            return _json_error("Project not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": row})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.update_project(conn, _ws(), project_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), "NOT_FOUND" if "not found" in str(exc).lower() else "BAD_REQUEST", 404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/projects/<int:project_id>/hub", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def projects_hub(project_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        hub = hop_ops.get_project_hub(conn, _ws(), project_id)
    if not hub:
        return _json_error("Project not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": hub})


# ---------- Leads ----------
@hop_bp.route("/leads", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def leads_list():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_db.list_leads(conn, _ws(), q=request.args.get("q"))
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/leads", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def leads_create():
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.create_lead(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/leads/<int:lead_id>", methods=["PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def leads_patch(lead_id: int):
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.update_lead(conn, _ws(), lead_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


# ---------- Deals (CRM v2 — 8-stage pipeline) ----------
@hop_bp.route("/deals/meta", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_meta():
    ensure_hop_schema(_db_path())
    return jsonify({"success": True, "data": hop_deals.deal_meta()})


@hop_bp.route("/deals/board", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_board():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        data = hop_deals.board_deals(conn, _ws(), q=request.args.get("q"))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/deals/attention", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_attention():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        data = hop_deals.attention_deals(conn, _ws())
    return jsonify({"success": True, "data": data})


@hop_bp.route("/deals", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_list():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_deals.list_deals(
            conn,
            _ws(),
            q=request.args.get("q"),
            stage=request.args.get("stage"),
            status=request.args.get("status"),
            owner=request.args.get("owner"),
            health=request.args.get("health"),
        )
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/deals", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_create():
    ensure_hop_schema(_db_path())
    payload = _payload()
    new_party = payload.get("new_party") if isinstance(payload.get("new_party"), dict) else None
    if new_party and (new_party.get("company") or "").strip():
        blocked = _maybe_party_duplicate_response(
            {
                **new_party,
                "force_save": payload.get("force_save") or new_party.get("force_save"),
            }
        )
        if blocked:
            return blocked
        payload = {
            **payload,
            "new_party": {**new_party, "force_save": True},
            "force_save": True,
        }
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.create_deal(conn, _ws(), payload)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/deals/<int:deal_id>", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_get(deal_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        row = hop_deals.get_deal(conn, _ws(), deal_id)
        if not row:
            return _json_error("Deal not found", "NOT_FOUND", 404)
        events = hop_deals.list_deal_events(conn, _ws(), deal_id)
        activities = hop_deals.list_activities(conn, _ws(), deal_id)
        lines = hop_deals.list_deal_lines(conn, _ws(), deal_id)
        appointments = hop_deals.list_appointments(conn, _ws(), deal_id)
        stage_history = hop_deals.list_stage_history(conn, _ws(), deal_id)
    row = dict(row)
    row["events"] = events
    row["activities"] = activities
    row["lines"] = lines
    row["appointments"] = appointments
    row["stage_history"] = stage_history
    row["stages"] = DEAL_STAGES
    row["steps"] = DEAL_STEPS  # legacy compat
    return jsonify({"success": True, "data": row})


@hop_bp.route("/deals/<int:deal_id>", methods=["PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_patch(deal_id: int):
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.update_deal(conn, _ws(), deal_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/deals/<int:deal_id>/stage", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_move_stage(deal_id: int):
    ensure_hop_schema(_db_path())
    payload = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.move_stage(
                conn,
                _ws(),
                deal_id,
                payload.get("to_stage") or "",
                reason=payload.get("reason"),
                actor=payload.get("actor"),
            )
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/deals/<int:deal_id>/activities", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_activities(deal_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_deals.list_activities(conn, _ws(), deal_id)
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.log_activity(conn, _ws(), deal_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/deals/<int:deal_id>/appointments", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_appointments(deal_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_deals.list_appointments(conn, _ws(), deal_id)
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.create_appointment(conn, _ws(), deal_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/deals/<int:deal_id>/lines", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_lines(deal_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_deals.list_deal_lines(conn, _ws(), deal_id)
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.add_deal_line(conn, _ws(), deal_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/deals/<int:deal_id>/lines/bulk", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_lines_bulk(deal_id: int):
    ensure_hop_schema(_db_path())
    payload = _payload()
    actor = None
    try:
        from flask_jwt_extended import get_jwt_identity

        actor = get_jwt_identity()
    except Exception:
        actor = None
    if isinstance(actor, dict):
        actor = actor.get("name") or actor.get("email") or actor.get("sub")
    try:
        with hop_db.connect(_db_path()) as conn:
            result = hop_deals.bulk_save_deal_lines(
                conn, _ws(), deal_id, payload, actor=str(actor) if actor else None
            )
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": result})


@hop_bp.route("/deals/<int:deal_id>/lines/<int:line_id>", methods=["PATCH", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_line_item(deal_id: int, line_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "DELETE":
        with hop_db.connect(_db_path()) as conn:
            ok = hop_deals.soft_delete_deal_line(conn, _ws(), deal_id, line_id)
        if not ok:
            return _json_error("Requirement line not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": {"deleted": True, "id": line_id, "soft": True}})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_deals.update_deal_line(conn, _ws(), deal_id, line_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/deals/<int:deal_id>", methods=["DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def deals_delete(deal_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        ok = hop_deals.delete_deal(conn, _ws(), deal_id)
    if not ok:
        return _json_error("Deal not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": {"deleted": True, "id": deal_id, "soft": True}})


# ---------- Meetings ----------
@hop_bp.route("/meetings", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def meetings_list():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_db.list_meetings(conn, _ws(), q=request.args.get("q"))
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/meetings", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def meetings_create():
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.create_meeting(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


# ---------- Quotations ----------
@hop_bp.route("/quotations", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def quotations_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_quotations(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_quotation(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/quotations/<int:quote_id>", methods=["GET", "PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def quotations_item(quote_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.get_quotation(conn, _ws(), quote_id)
        if not row:
            return _json_error("Quotation not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": row})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.update_quotation(conn, _ws(), quote_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/quotations/<int:quote_id>/revise", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def quotations_revise(quote_id: int):
    ensure_hop_schema(_db_path())
    payload = _payload()
    payload["parent_quote_id"] = quote_id
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_quotation(conn, _ws(), payload)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


# ---------- Vendors ----------
@hop_bp.route("/vendors/<int:vendor_id>", methods=["GET", "PATCH", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_get_or_delete(vendor_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "PATCH":
        payload = _payload()
        blocked = _maybe_party_duplicate_response(
            payload,
            exclude_id=vendor_id,
            exclude_party_type="vendor",
        )
        if blocked:
            return blocked
        try:
            with hop_db.connect(_db_path()) as conn:
                row = hop_ops.update_vendor(conn, _ws(), vendor_id, payload)
        except ValueError as exc:
            return _json_error(str(exc), "NOT_FOUND" if "not found" in str(exc).lower() else "BAD_REQUEST", 404 if "not found" in str(exc).lower() else 400)
        except Exception as exc:
            return _json_error(f"Update failed: {exc}", "UPDATE_ERROR", 500)
        return jsonify({"success": True, "data": row})
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.get_vendor(conn, _ws(), vendor_id)
        if not row:
            return _json_error("Vendor not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": row})
    force = _force_flag(_payload())
    try:
        with hop_db.connect(_db_path()) as conn:
            ok = hop_ops.delete_vendor(conn, _ws(), vendor_id, force=force)
    except Exception as exc:
        from app.hop_party_usage import PartyInUseError

        if isinstance(exc, PartyInUseError):
            return _party_in_use_response(exc)
        return _json_error(str(exc), "DELETE_BLOCKED", 409)
    if not ok:
        return _json_error("Vendor not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": {"deleted": vendor_id}})


@hop_bp.route("/vendors/<int:vendor_id>/usage", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_usage(vendor_id: int):
    ensure_hop_schema(_db_path())
    from app.hop_party_usage import get_vendor_usage

    with hop_db.connect(_db_path()) as conn:
        row = hop_ops.get_vendor(conn, _ws(), vendor_id)
        if not row:
            return _json_error("Vendor not found", "NOT_FOUND", 404)
        usage = get_vendor_usage(conn, _ws(), vendor_id)
    usage["company"] = row.get("company")
    return jsonify({"success": True, "data": usage})


@hop_bp.route("/vendors/bulk-delete", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_bulk_delete():
    ensure_hop_schema(_db_path())
    payload = _payload()
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _json_error("ids list is required", "VALIDATION_ERROR", 400)
    try:
        id_list = [int(x) for x in ids]
    except (TypeError, ValueError):
        return _json_error("ids must be integers", "VALIDATION_ERROR", 400)
    force = _force_flag(payload)
    with hop_db.connect(_db_path()) as conn:
        result = hop_ops.delete_vendors_bulk(conn, _ws(), id_list, force=force)
    if result.get("blocked") and not force:
        return (
            jsonify(
                {
                    "success": False,
                    "requires_confirmation": True,
                    "message": (
                        f"{len(result['blocked'])} vendor(s) are used in rate sheets or other records. "
                        "Delete anyway? Party links will be removed from those records."
                    ),
                    "error": {"code": "PARTY_IN_USE", "message": "Some vendors are in use"},
                    "data": result,
                }
            ),
            409,
        )
    return jsonify({"success": True, "data": result})


@hop_bp.route("/vendors", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_vendors(conn, _ws(), q=request.args.get("q"))
        return jsonify({"success": True, "data": rows})
    payload = _payload()
    blocked = _maybe_party_duplicate_response(payload)
    if blocked:
        return blocked
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_vendor(conn, _ws(), payload)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/parties/check-duplicates", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def parties_check_duplicates():
    """Preview fuzzy/exact duplicate parties before save."""
    ensure_hop_schema(_db_path())
    from app.hop_party_match import find_party_matches

    payload = _payload()
    company = str(payload.get("company") or payload.get("name") or "").strip()
    if not company:
        return _json_error("company is required", "VALIDATION_ERROR", 400)
    # Search typing uses a softer floor so Sidhi/Siddhi/sidh still surface.
    purpose = str(payload.get("purpose") or "").strip().lower()
    min_score = payload.get("min_score")
    try:
        min_score_f = float(min_score) if min_score is not None else (
            55.0 if purpose == "search" else None
        )
    except (TypeError, ValueError):
        min_score_f = 55.0 if purpose == "search" else None
    with hop_db.connect(_db_path()) as conn:
        kwargs = dict(
            company=company,
            gst_no=str(payload.get("gst_no") or ""),
            mobile=str(payload.get("mobile") or ""),
            party_type=str(payload.get("party_type") or "both"),
            exclude_id=payload.get("exclude_id"),
            exclude_party_type=payload.get("exclude_party_type"),
            limit=int(payload.get("limit") or (12 if purpose == "search" else 8)),
        )
        if min_score_f is not None:
            kwargs["min_score"] = min_score_f
        matches = find_party_matches(conn, _ws(), **kwargs)
    return jsonify({"success": True, "data": {"matches": matches, "count": len(matches)}})


@hop_bp.route("/vendor-comparisons", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendor_comparisons_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_vendor_comparisons(
                conn, _ws(), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_vendor_comparison(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


# ---------- Rate sheets / multi-supplier compare ----------
@hop_bp.route("/rate-sheets", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_sheets_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_rate_sheets(conn, _ws())
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_rate_sheet(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/rate-sheets/seed-samples", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_sheets_seed_samples():
    """Disabled — rate compare must only use user-uploaded files, never bundled demo quotes."""
    return _json_error(
        "Sample quotes are disabled. Upload your supplier files — data comes only from uploads.",
        "SAMPLES_DISABLED",
        410,
    )


@hop_bp.route("/rate-sheets/upload", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_sheets_upload():
    """Upload supplier rate file: PDF, Excel, Word/RTF, images (jpg/jpeg/bmp/…), CSV/TXT."""
    from datetime import datetime
    from pathlib import Path
    from werkzeug.utils import secure_filename

    from app.hop_rate_upload import accept_attr, allowed_rate_upload, parse_rate_upload_file

    ensure_hop_schema(_db_path())
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return _json_error("file is required")
    original = uploaded.filename
    if not allowed_rate_upload(original):
        return _json_error(
            f"Unsupported file type. Allowed: {accept_attr()}",
            "UNSUPPORTED_TYPE",
        )

    supplier_name = (request.form.get("supplier_name") or "").strip()
    if not supplier_name:
        # derive from filename stem
        supplier_name = Path(original).stem.replace("_", " ").strip() or "Supplier"
    title = (request.form.get("title") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    create_sheet = (request.form.get("create_sheet") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    safe = secure_filename(Path(original).name) or f"rate{Path(original).suffix.lower()}"
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    upload_root = Path(current_app.root_path).parent / "data" / "hop_rate_uploads" / _ws()
    upload_root.mkdir(parents=True, exist_ok=True)
    dest = upload_root / f"{stamp}_{safe}"
    uploaded.save(dest)

    try:
        parsed = parse_rate_upload_file(dest, supplier_hint=supplier_name)
    except ValueError as exc:
        return _json_error(str(exc))

    result = {
        "filename": original,
        "stored_as": str(dest),
        "accepted": accept_attr(),
        **parsed,
    }

    lines = parsed.get("lines") or []
    if create_sheet and not lines:
        # File kept on disk for reference, but do not pollute matrix with empty/default sheets
        result["created"] = False
        warns = list(parsed.get("warnings") or [])
        warns.append(
            "No product rates detected in this file — sheet not saved. "
            "Paste lines in the form and click Save pasted lines, or upload Excel/PDF with clear rates."
        )
        result["warnings"] = warns
        return jsonify({"success": True, "data": result}), 200

    if create_sheet:
        warn_text = "; ".join(parsed.get("warnings") or []) or None
        form_source = (request.form.get("source_type") or "").strip().lower()
        source_type = (
            form_source
            if form_source and form_source not in ("", "manual", "upload")
            else (parsed.get("source_type") or "upload")
        )
        try:
            with hop_db.connect(_db_path()) as conn:
                sheet = hop_ops.create_rate_sheet(
                    conn,
                    _ws(),
                    {
                        "supplier_name": supplier_name,
                        "title": title or f"{supplier_name} — {original}",
                        "source_type": source_type,
                        "notes": notes or warn_text,
                        "lines": lines,
                        "allow_empty_lines": False,
                        "source_filename": original,
                        "source_file_path": str(dest),
                        "parse_method": parsed.get("parse_method"),
                        "parse_warnings": warn_text,
                    },
                )
            result["sheet"] = sheet
            result["created"] = True
            result["line_count"] = len(lines)
            result["source_type"] = source_type
        except ValueError as exc:
            return _json_error(str(exc))

    return jsonify({"success": True, "data": result}), 201


@hop_bp.route("/rate-sheets/upload-formats", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_sheets_upload_formats():
    from app.hop_rate_upload import ALLOWED_EXTENSIONS, accept_attr

    return jsonify(
        {
            "success": True,
            "data": {
                "accept": accept_attr(),
                "extensions": sorted(ALLOWED_EXTENSIONS),
                "note": "PDF, Excel, Word/WordPad (doc/docx/rtf), images (jpg/jpeg/bmp/png/…), CSV/TXT",
            },
        }
    )


@hop_bp.route("/rate-sheets/<int:sheet_id>", methods=["GET", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_sheet_detail(sheet_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        if request.method == "DELETE":
            ok = hop_ops.delete_rate_sheet(conn, _ws(), sheet_id)
            if not ok:
                return _json_error("Rate sheet not found", "NOT_FOUND", 404)
            return jsonify({"success": True, "data": {"deleted": sheet_id}})
        row = hop_ops.get_rate_sheet(conn, _ws(), sheet_id)
    if not row:
        return _json_error("Rate sheet not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/rate-compare", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_compare_matrix():
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        matrix = hop_ops.rate_comparison_matrix(conn, _ws())
    return jsonify({"success": True, "data": matrix})


@hop_bp.route("/rate-lines/clear", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_lines_clear():
    """Clear unwanted rates: single product, bulk selected, one supplier sheet, or all."""
    ensure_hop_schema(_db_path())
    payload = _payload()
    clear_all = payload.get("clear_all") in (True, 1, "1", "true", "yes")
    product_keys = payload.get("product_keys")
    line_ids = payload.get("line_ids")
    sheet_id = payload.get("sheet_id")
    try:
        with hop_db.connect(_db_path()) as conn:
            result = hop_ops.clear_rate_lines(
                conn,
                _ws(),
                product_keys=product_keys if isinstance(product_keys, list) else None,
                line_ids=line_ids if isinstance(line_ids, list) else None,
                sheet_id=int(sheet_id) if sheet_id not in (None, "") else None,
                clear_all=clear_all,
            )
            result["matrix"] = hop_ops.rate_comparison_matrix(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": result})


@hop_bp.route("/rate-cart/place-orders", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def rate_cart_place_orders():
    """Create one supplier PO per cart group (items chosen across rate sheets)."""
    ensure_hop_schema(_db_path())
    payload = _payload()
    groups = payload.get("groups") or []
    if not isinstance(groups, list) or not groups:
        return _json_error("groups required — add items to quote cart first")

    created = []
    try:
        with hop_db.connect(_db_path()) as conn:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            for i, group in enumerate(groups, start=1):
                supplier = (group.get("supplier_name") or "Supplier").strip()
                items = group.get("items") or []
                if not items:
                    continue
                lines = []
                for it in items:
                    label = it.get("label") or it.get("product_name") or "Item"
                    size = it.get("size") or ""
                    qty = float(it.get("qty") or 1)
                    rate = float(it.get("rate") or 0)
                    gst = float(it.get("gst_pct") or 0)
                    landed = float(it.get("landed_rate") or rate)
                    lines.append(
                        f"• {label}"
                        + (f" ({size})" if size else "")
                        + f" × {qty:g} @ ₹{rate:,.2f} +{gst:g}% → ₹{landed * qty:,.2f}"
                    )
                order_value = group.get("order_value")
                if order_value in (None, ""):
                    order_value = sum(
                        float(it.get("landed_rate") or it.get("rate") or 0) * float(it.get("qty") or 1)
                        for it in items
                    )
                # Prefer linked vendor if sheet has vendor_id
                vendor_id = None
                sheet_id = group.get("sheet_id")
                if sheet_id:
                    sheet = hop_ops.get_rate_sheet(conn, _ws(), int(sheet_id))
                    if sheet and sheet.get("vendor_id"):
                        vendor_id = sheet.get("vendor_id")
                if not vendor_id:
                    vendors = hop_ops.list_vendors(conn, _ws(), q=supplier)
                    for v in vendors:
                        if (v.get("company") or "").strip().lower() == supplier.lower():
                            vendor_id = v.get("id")
                            break
                po_number = f"RC-{stamp}-{i:02d}"
                notes = (
                    f"Rate-compare quote cart → supplier order\n"
                    f"Supplier: {supplier}\n"
                    + "\n".join(lines)
                )
                row = hop_ops.create_order(
                    conn,
                    _ws(),
                    {
                        "po_number": po_number,
                        "supplier": supplier,
                        "vendor_id": vendor_id,
                        "order_value": round(float(order_value), 2),
                        "order_type": "supplier_po",
                        "status": "open",
                        "production_status": "pending",
                        "notes": notes,
                        "client_name": f"{__import__('app.hop_firms', fromlist=['x']).business_name(conn, _ws(), 'Own')} — procurement",
                    },
                )
                created.append(row)
    except ValueError as exc:
        return _json_error(str(exc))
    if not created:
        return _json_error("No valid cart groups to place")
    return jsonify({"success": True, "data": {"orders": created, "count": len(created)}}), 201


# ---------- Samples / Products ----------
@hop_bp.route("/samples", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def samples_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_samples(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_sample(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/products", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def products_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_products(conn, _ws(), q=request.args.get("q"))
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_product(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/products/<int:product_id>/photo", methods=["GET", "POST", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def product_photo(product_id: int):
    """Product photo: POST a file (field "photo") from camera or gallery, GET the JPEG, DELETE to remove."""
    import os

    from flask import send_file

    from app import hop_product_photos as PH

    ensure_hop_schema(_db_path())
    if request.method == "GET":
        p = PH.path_for(_db_path(), _ws(), product_id)
        if not os.path.exists(p):
            return _json_error("No photo yet", "NOT_FOUND", 404)
        return send_file(p, mimetype="image/jpeg", max_age=3600)
    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "DELETE":
                PH.remove(conn, _db_path(), _ws(), product_id)
                return jsonify({"success": True, "data": {"product_id": product_id, "photo_v": None}})
            f = request.files.get("photo") or request.files.get("file") or request.files.get("image")
            data = PH.save(conn, _db_path(), _ws(), product_id, f.read() if f else b"")
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/products/<int:product_id>", methods=["GET", "PATCH", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def products_item(product_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        if request.method == "GET":
            rows = hop_ops.list_products(conn, _ws())
            row = next((r for r in rows if int(r.get("id") or 0) == int(product_id)), None)
            if not row:
                return _json_error("Product not found", "NOT_FOUND", 404)
            return jsonify({"success": True, "data": row})
        if request.method == "DELETE":
            from app import hop_phase3_ext
            try:
                out = hop_phase3_ext.soft_delete_product(conn, _ws(), product_id)
            except LookupError as exc:
                return _json_error(str(exc), "NOT_FOUND", 404)
            return jsonify({"success": True, "data": out})
        try:
            row = hop_ops.update_product(conn, _ws(), product_id, _payload())
        except ValueError as exc:
            return _json_error(str(exc), "NOT_FOUND" if "not found" in str(exc).lower() else "VALIDATION_ERROR",
                               404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


# ---------- Orders / Dispatch / Invoices / Payments ----------
@hop_bp.route("/orders", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def orders_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_orders(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_order(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/orders/<int:order_id>", methods=["PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def orders_patch(order_id: int):
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.update_order(conn, _ws(), order_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), status=404 if "not found" in str(exc).lower() else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/dispatches", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def dispatches_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_dispatches(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_dispatch(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/samples/<int:item_id>", methods=["PATCH", "PUT", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def sample_update(item_id: int):
    return _patch_row(hop_ops.update_sample, item_id, "hop_samples")


@hop_bp.route("/complaints/<int:item_id>", methods=["PATCH", "PUT", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def complaint_update(item_id: int):
    return _patch_row(hop_ops.update_complaint, item_id, "hop_complaints")


def _patch_row(fn, item_id: int, table: str = ""):
    ensure_hop_schema(_db_path())
    if request.method == "DELETE":
        try:
            with hop_db.connect(_db_path()) as conn:
                hop_ops.delete_ops_row(conn, _ws(), table, item_id)
        except ValueError as exc:
            return _json_error(str(exc), "NOT_FOUND", 404)
        return jsonify({"success": True, "data": {"deleted": item_id}})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = fn(conn, _ws(), item_id, _payload())
    except ValueError as exc:
        nf = "not found" in str(exc)
        return _json_error(str(exc), "NOT_FOUND" if nf else "VALIDATION_ERROR", 404 if nf else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/dispatches/<int:dispatch_id>", methods=["PATCH", "PUT", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def dispatch_update(dispatch_id: int):
    ensure_hop_schema(_db_path())
    if request.method == "DELETE":
        return _patch_row(hop_ops.update_dispatch, dispatch_id, "hop_dispatches")
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.update_dispatch(conn, _ws(), dispatch_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc), "NOT_FOUND" if "not found" in str(exc) else "VALIDATION_ERROR",
                           404 if "not found" in str(exc) else 400)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/invoices", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def invoices_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_invoices(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_invoice(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/payments", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def payments_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_payments(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_payment(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/commission/invoices", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_invoices_list():
    """Tax invoices for commission worksheet (with saved entry flags)."""
    ensure_hop_schema(_db_path())
    q = (request.args.get("q") or "").strip() or None
    with hop_db.connect(_db_path()) as conn:
        hop_ops.sync_commission_from_expenses(conn, _ws())
        rows = hop_ops.list_tax_invoices_for_commission(conn, _ws(), q=q)
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/commission/by-agent", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_by_agent():
    """Who got how much commission, and when (grouped by agent)."""
    ensure_hop_schema(_db_path())
    q = (request.args.get("q") or "").strip() or None
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    payment_status = (request.args.get("payment_status") or "").strip() or None
    with hop_db.connect(_db_path()) as conn:
        hop_ops.sync_commission_from_expenses(conn, _ws())
        data = hop_ops.list_commission_by_agent(
            conn,
            _ws(),
            q=q,
            date_from=date_from,
            date_to=date_to,
            payment_status=payment_status,
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/commission/records", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_records():
    """Commission payment history — filter by date / party / search."""
    ensure_hop_schema(_db_path())
    q = (request.args.get("q") or "").strip() or None
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    agent_party_id = request.args.get("agent_party_id", type=int)
    agent_party_type = (request.args.get("agent_party_type") or "").strip() or None
    agent_name = (request.args.get("agent_name") or "").strip() or None
    payment_status = (request.args.get("payment_status") or "").strip() or None
    with hop_db.connect(_db_path()) as conn:
        hop_ops.sync_commission_from_expenses(conn, _ws())
        data = hop_ops.list_commission_records(
            conn,
            _ws(),
            q=q,
            date_from=date_from,
            date_to=date_to,
            agent_party_id=agent_party_id,
            agent_party_type=agent_party_type,
            agent_name=agent_name,
            payment_status=payment_status,
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/commission/worksheet", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_worksheet_get():
    """Load one tax invoice + saved/manual commission & TDS fields."""
    ensure_hop_schema(_db_path())
    party_txn_id = request.args.get("party_txn_id", type=int)
    if not party_txn_id:
        return _json_error("party_txn_id is required", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        data = hop_ops.get_commission_worksheet(conn, _ws(), party_txn_id)
    if not data:
        return _json_error("Tax invoice not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": data})


@hop_bp.route("/commission/worksheet", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_worksheet_save():
    """Save manual commission % + TDS % against a tax invoice."""
    ensure_hop_schema(_db_path())
    payload = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_ops.upsert_commission_entry(conn, _ws(), payload)
            if payload.get("remember_for_party"):
                hop_ops.set_customer_commission_agent(conn, _ws(), int(payload.get("party_txn_id") or 0),
                                                      str(payload.get("agent_name") or ""),
                                                      float(payload.get("commission_pct") or 0), float(payload.get("tds_pct") or 0))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/notes", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def notes_collection():
    """Business notes: GET ?q=&party= (search), POST {text, title, party_name, remind_on, pinned}."""
    from app import hop_notes

    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        if request.method == "GET":
            return jsonify({"success": True, "data": hop_notes.search(conn, _ws(), request.args.get("q") or "",
                                                                       request.args.get("party") or "")})
        try:
            p = _payload()
            row = hop_notes.create(conn, _ws(), p, user=str(getattr(g, "user_id", "") or ""))
        except ValueError as exc:
            return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/unused-payments", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def unused_payments_list():
    """?bill_type=1&party_type=customer&party_id=N → that party's payments with money not yet on a bill."""
    from app import hop_money

    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        rows = hop_money.unused_payments(conn, _ws(), request.args.get("bill_type", type=int) or 1,
                                         (request.args.get("party_type") or "customer").strip(),
                                         request.args.get("party_id", type=int) or 0)
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/notes/<int:note_id>/comments", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def note_comment(note_id: int):
    """Reply in a note: {text, by, kind: 'comment' | 'plan'} (Claude writes what was done / a plan here)."""
    from app import hop_notes

    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_notes.add_comment(conn, _ws(), note_id, _payload())
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row})


@hop_bp.route("/notes/<int:note_id>", methods=["PATCH", "PUT", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def note_item(note_id: int):
    from app import hop_notes

    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "DELETE":
                hop_notes.delete(conn, _ws(), note_id)
                return jsonify({"success": True, "data": {"deleted": note_id}})
            row = hop_notes.update(conn, _ws(), note_id, _payload())
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row})


@hop_bp.route("/commission/entries/<int:entry_id>", methods=["DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_entry_delete(entry_id: int):
    ensure_hop_schema(_db_path())
    try:
        with hop_db.connect(_db_path()) as conn:
            hop_ops.delete_commission_entry(conn, _ws(), entry_id)
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    return jsonify({"success": True, "data": {"deleted": entry_id}})


@hop_bp.route("/commission/sale-invoices", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def commission_sale_invoices():
    """Legacy list alias — returns invoices with any saved commission."""
    ensure_hop_schema(_db_path())
    q = (request.args.get("q") or "").strip() or None
    with hop_db.connect(_db_path()) as conn:
        rows = hop_ops.list_tax_invoices_for_commission(conn, _ws(), q=q)
    return jsonify({"success": True, "data": {"rows": rows}})


@hop_bp.route("/firm-profile", methods=["GET", "PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def firm_profile_route():
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import get_firm_profile, patch_firm_profile

    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            row = get_firm_profile(conn, _ws())
        return jsonify({"success": True, "data": row})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = patch_firm_profile(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row})


@hop_bp.route("/party-transactions/next-number", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_next_number():
    """Next serial for any Nexora-manual document type — user may override on POST."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import MANUAL_TXN_TYPES, next_manual_doc_number
    from datetime import datetime, timezone

    txn_type = request.args.get("txn_type", type=int) or 27
    if txn_type not in MANUAL_TXN_TYPES:
        return _json_error("Unsupported txn_type for manual documents", "VALIDATION_ERROR", 400)
    txn_date = (request.args.get("txn_date") or "").strip()
    if not txn_date:
        txn_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with hop_db.connect(_db_path()) as conn:
        txn_number = next_manual_doc_number(conn, _ws(), txn_type, txn_date)
    return jsonify({"success": True, "data": {"txn_number": txn_number, "txn_type": txn_type}})


@hop_bp.route("/party-transactions", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transactions_collection():
    ensure_hop_schema(_db_path())
    if request.method == "POST":
        from app.hop_doc_preview import create_manual_party_document

        payload = _payload()
        client_ref = str(payload.get("client_ref") or "").strip()[:64]
        try:
            with hop_db.connect(_db_path()) as conn:
                if client_ref:
                    # Offline bill sent again (phone lost the answer): return the one already saved.
                    old = conn.execute("SELECT id, txn_number, source_txn_id FROM hop_party_transactions "
                                       "WHERE workspace_id=? AND client_ref=?", (_ws(), client_ref)).fetchone()
                    if old:
                        return jsonify({"success": True, "data": {"party_txn_id": old[0], "txn_number": old[1],
                                                                  "source_txn_id": old[2], "already_saved": True}})
                row = create_manual_party_document(conn, _ws(), payload)
                if client_ref and row.get("party_txn_id"):
                    conn.execute("UPDATE hop_party_transactions SET client_ref=? WHERE workspace_id=? AND id=?",
                                 (client_ref, _ws(), int(row["party_txn_id"])))
                    conn.commit()
        except ValueError as exc:
            return _json_error(str(exc))
        return jsonify({"success": True, "data": row}), 201

    party_type = (request.args.get("party_type") or "").strip().lower()
    party_id = request.args.get("party_id", type=int)
    txn_types_raw = (request.args.get("txn_types") or request.args.get("txn_type") or "").strip()
    txn_types: list[int] = []
    if txn_types_raw:
        for part in txn_types_raw.replace(" ", "").split(","):
            if part.isdigit():
                txn_types.append(int(part))
    label_q = (request.args.get("label_q") or "").strip()
    with hop_db.connect(_db_path()) as conn:
        sql = """
            SELECT *
            FROM hop_party_transactions
            WHERE (deleted_at IS NULL OR trim(deleted_at)='') AND workspace_id=?
        """
        params: list[object] = [_ws()]
        if party_type:
            sql += " AND party_type=?"
            params.append(party_type)
        if party_id:
            sql += " AND party_id=?"
            params.append(int(party_id))
        if txn_types:
            placeholders = ",".join("?" for _ in txn_types)
            sql += f" AND txn_type IN ({placeholders})"
            params.extend(txn_types)
        if label_q:
            sql += " AND LOWER(COALESCE(txn_label, '')) LIKE ?"
            params.append(f"%{label_q.lower()}%")
        sql += " ORDER BY date(txn_date) DESC, id DESC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/party-transactions/<int:txn_id>", methods=["GET", "PATCH", "DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_detail(txn_id: int):
    """Load / update / delete Nexora-created Estimate / Proforma."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import (
        delete_manual_party_document,
        get_party_transaction_edit_data,
        update_manual_party_document,
    )

    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            row = get_party_transaction_edit_data(conn, _ws(), txn_id)
        if not row:
            return _json_error("Editable estimate/proforma not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": row})

    if request.method == "DELETE":
        try:
            with hop_db.connect(_db_path()) as conn:
                row = delete_manual_party_document(conn, _ws(), txn_id)
        except ValueError as exc:
            return _json_error(str(exc))
        return jsonify({"success": True, "data": row})

    try:
        with hop_db.connect(_db_path()) as conn:
            row = update_manual_party_document(conn, _ws(), txn_id, _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row})


@hop_bp.route("/party-transactions/<int:txn_id>/preview", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_preview(txn_id: int):
    """Vyapar-style document preview (header + firm + party + line items)."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import build_txn_preview

    with hop_db.connect(_db_path()) as conn:
        data = build_txn_preview(conn, _ws(), party_txn_id=txn_id)
    if not data:
        return _json_error("Transaction not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": data})


@hop_bp.route("/party-transactions/<int:txn_id>/unused-payments", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_unused_payments(txn_id: int):
    """Payments of this bill's party that still have money not on any bill (to link to this bill)."""
    from app import hop_money

    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        bill = conn.execute("SELECT txn_type, party_type, party_id, balance_amount FROM hop_party_transactions "
                            "WHERE workspace_id=? AND id=?", (_ws(), txn_id)).fetchone()
        if not bill:
            return _json_error("Bill not found", "NOT_FOUND", 404)
        rows = hop_money.unused_payments(conn, _ws(), int(bill[0] or 0), bill[1] or "customer", int(bill[2] or 0))             if bill[2] else []
    return jsonify({"success": True, "data": {"payments": rows, "bill_due": float(bill[3] or 0)}})


@hop_bp.route("/party-transactions/<int:txn_id>/link-payments", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_link_payments(txn_id: int):
    """Put an earlier / later Payment-In (or Out) on a saved bill: {links: [{payment_txn_id, amount}]}."""
    from app import hop_money

    ensure_hop_schema(_db_path())
    links = (_payload() or {}).get("links") or []
    if not links:
        return _json_error("Choose a payment to link")
    try:
        with hop_db.connect(_db_path()) as conn:
            hop_money.link_payments_to_bill(conn, _ws(), txn_id, links)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"linked": len(links)}})


@hop_bp.route("/party-transactions/<int:txn_id>/download.pdf", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_download_pdf(txn_id: int):
    """Direct PDF download for a party transaction preview."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import build_txn_preview
    from app.hop_doc_pdf import _safe_filename, build_preview_pdf
    from app import hop_phase3_ext

    with hop_db.connect(_db_path()) as conn:
        data = build_txn_preview(conn, _ws(), party_txn_id=txn_id)
        theme = hop_phase3_ext.get_print_theme(conn, _ws())
    if not data:
        return _json_error("Transaction not found", "NOT_FOUND", 404)
    try:
        pdf_bytes = build_preview_pdf(data, theme=theme)
    except Exception as exc:
        return _json_error(f"PDF generation failed ({exc})", "PDF_ERROR", 500)
    fname = f"{_safe_filename(data)}.pdf"
    from flask import Response

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@hop_bp.route("/documents/download.pdf", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def documents_download_pdf():
    """Direct PDF download by party_txn_id or Vyapar source_txn_id."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import build_txn_preview
    from app.hop_doc_pdf import _safe_filename, build_preview_pdf

    party_txn_id = request.args.get("party_txn_id", type=int)
    source_txn_id = request.args.get("source_txn_id", type=int)
    if not party_txn_id and not source_txn_id:
        return _json_error("party_txn_id or source_txn_id is required", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        from app import hop_phase3_ext

        data = build_txn_preview(
            conn,
            _ws(),
            party_txn_id=party_txn_id,
            source_txn_id=source_txn_id,
        )
        theme = hop_phase3_ext.get_print_theme(conn, _ws())
    if not data:
        return _json_error("Transaction not found", "NOT_FOUND", 404)
    try:
        pdf_bytes = build_preview_pdf(data, theme=theme)
    except Exception as exc:
        return _json_error(f"PDF generation failed ({exc})", "PDF_ERROR", 500)
    fname = f"{_safe_filename(data)}.pdf"
    from flask import Response

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@hop_bp.route("/documents/preview", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def documents_preview():
    """Preview by party_txn_id or Vyapar source_txn_id (for Sale Invoice list extras)."""
    ensure_hop_schema(_db_path())
    from app.hop_doc_preview import build_txn_preview

    party_txn_id = request.args.get("party_txn_id", type=int)
    source_txn_id = request.args.get("source_txn_id", type=int)
    if not party_txn_id and not source_txn_id:
        return _json_error("party_txn_id or source_txn_id is required", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        data = build_txn_preview(
            conn,
            _ws(),
            party_txn_id=party_txn_id,
            source_txn_id=source_txn_id,
        )
    if not data:
        return _json_error("Transaction not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": data})


@hop_bp.route("/complaints", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def complaints_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_complaints(
                conn, _ws(), q=request.args.get("q"), project_id=request.args.get("project_id", type=int)
            )
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_complaint(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/targets", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def targets_upsert():
    ensure_hop_schema(_db_path())
    payload = _payload()
    period = (payload.get("period_label") or "").strip()
    if not period:
        return _json_error("period_label is required")
    try:
        amount = float(payload.get("target_amount") or 0)
    except (TypeError, ValueError):
        return _json_error("target_amount must be a number")
    from app import hop_targets

    try:
        with hop_db.connect(_db_path()) as conn:
            if payload.get("split_months"):
                hop_targets.split_yearly(conn, _ws(), int(hop_targets.parse_period(period)[2:6]), amount)
                row = {"period_label": hop_targets.parse_period(period), "split": True}
            else:
                row = hop_targets.set_target(conn, _ws(), period, amount)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row})


@hop_bp.route("/targets/summary", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def targets_summary():
    """Yearly (Apr–Mar) + monthly targets with achieved from sale invoices. ?fy=2026 for another year."""
    ensure_hop_schema(_db_path())
    from app import hop_targets

    fy = request.args.get("fy", type=int)
    with hop_db.connect(_db_path()) as conn:
        return jsonify({"success": True, "data": hop_targets.summary(conn, _ws(), fy)})


# ---------- Reports ----------
@hop_bp.route("/reports/<report_key>", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def reports_get(report_key: str):
    ensure_hop_schema(_db_path())
    key = (report_key or "").strip().lower().replace("-", "_")
    with hop_db.connect(_db_path()) as conn:
        ws = _ws()
        if key in ("lead_pipeline", "pipeline"):
            data = hop_ops.report_lead_pipeline(conn, ws)
        elif key == "funnel":
            data = hop_ops.report_funnel(conn, ws)
        elif key in ("meetings", "meeting_dashboard"):
            data = hop_ops.report_meetings_dashboard(conn, ws)
        elif key in ("receivables", "ageing"):
            data = hop_ops.report_receivables(conn, ws)
        elif key in ("customers", "customer_dashboard"):
            data = hop_ops.report_customer_dashboard(conn, ws)
        elif key in ("daily", "daily_activity"):
            data = hop_ops.report_daily_activity(conn, ws, day=request.args.get("day"))
        elif key in ("profit", "profitability"):
            data = hop_ops.report_profitability(conn, ws)
        elif key in ("quotations", "quotation_kpis"):
            data = hop_ops.report_quotation_kpis(conn, ws)
        elif key in ("repeat", "repeat_business"):
            data = hop_ops.report_repeat_business(conn, ws)
        elif key in ("salesperson", "salespeople"):
            data = hop_ops.report_salesperson(conn, ws)
        else:
            return _json_error(f"Unknown report: {report_key}", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": data})


# ---------- Fabric Preview (field demo → paid AI later) ----------
@hop_bp.route("/fabric-preview/fabrics", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def fabric_preview_fabrics():
    """Demo fabric bank + catalogue product names (swatch upload still via camera)."""
    from app.services.fabric_preview import list_demo_fabrics, preview_engine

    ensure_hop_schema(_db_path())
    products = []
    with hop_db.connect(_db_path()) as conn:
        rows = hop_ops.list_products(conn, _ws(), q=request.args.get("q"))
    for row in rows[:80]:
        products.append(
            {
                "id": f"product-{row.get('id')}",
                "product_id": row.get("id"),
                "name": row.get("name") or row.get("code") or f"SKU {row.get('id')}",
                "category": row.get("category") or row.get("brand") or "Catalogue",
                "source": "catalogue",
                "needs_swatch_photo": True,
            }
        )
    return jsonify(
        {
            "success": True,
            "data": {
                "engine": preview_engine(),
                "demo_fabrics": list_demo_fabrics(),
                "catalogue": products,
                "hint": "Pick a fabric to preview it. Your own catalogue fabrics show once their photos are added.",
            },
        }
    )


@hop_bp.route("/fabric-preview/render", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def fabric_preview_render():
    """Apply fabric onto furniture photo. DEMO by default (partner pitch)."""
    import base64
    import time
    from pathlib import Path

    from app.services.fabric_preview import (
        apply_fabric,
        ensure_preview_dir,
        get_demo_fabric_bytes,
        preview_engine,
    )

    ensure_hop_schema(_db_path())

    item_file = request.files.get("item_image") or request.files.get("sofa_image")
    fabric_file = request.files.get("fabric_image")
    demo_fabric_id = (request.form.get("demo_fabric_id") or "").strip()
    product_label = (request.form.get("fabric_label") or "").strip()

    if not item_file or not item_file.filename:
        return _json_error("Sofa / furniture photo required (item_image)")

    item_bytes = item_file.read()
    if not item_bytes:
        return _json_error("Empty furniture photo")

    fabric_bytes = None
    fabric_source = "upload"
    if fabric_file and fabric_file.filename:
        fabric_bytes = fabric_file.read()
        fabric_source = "camera_or_gallery"
    elif demo_fabric_id:
        fabric_bytes = get_demo_fabric_bytes(demo_fabric_id)
        fabric_source = "demo_bank"
        if not product_label:
            product_label = demo_fabric_id
    if not fabric_bytes:
        return _json_error("Fabric photo required, or pick a demo fabric from the bank")

    try:
        out_bytes, meta = apply_fabric(item_bytes, fabric_bytes)
    except Exception as exc:
        return _json_error(f"Render failed: {exc}", "RENDER_FAILED", 500)

    # Persist under data disk / project for share-back
    root = Path(current_app.root_path).resolve().parent
    preview_dir = ensure_preview_dir(root / "data")
    stamp = int(time.time())
    filename = f"preview_{stamp}.jpg"
    out_path = preview_dir / filename
    out_path.write_bytes(out_bytes)

    b64 = base64.b64encode(out_bytes).decode("ascii")
    return jsonify(
        {
            "success": True,
            "data": {
                **meta,
                "engine_requested": preview_engine(),
                "fabric_source": fabric_source,
                "fabric_label": product_label or demo_fabric_id or "Fabric",
                "image_base64": b64,
                "mime": "image/jpeg",
                "saved_as": str(out_path.name),
                "partner_pitch": meta.get("engine") == "demo",
            },
        }
    )


# ---------- Books: banking, stock, GST, accounts ----------
@hop_bp.route("/bank-accounts", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def bank_accounts_collection():
    ensure_hop_schema(_db_path())
    from app import hop_books

    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_books.list_bank_accounts(conn, _ws())
            cash = hop_books.cash_available(conn, _ws())
        return jsonify({"success": True, "data": {"accounts": rows, "cash_available": cash}})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_books.create_bank_account(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/bank-accounts/<int:account_id>/reconcile", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def bank_reconcile(account_id: int):
    """GET: book vs bank balance + entries. POST {entry_ids, cleared_on|null}: tick / untick."""
    ensure_hop_schema(_db_path())
    from app import hop_reconcile

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                body = _payload()
                hop_reconcile.mark(conn, _ws(), account_id, body.get("entry_ids") or [], body.get("cleared_on"))
            data = hop_reconcile.statement(conn, _ws(), account_id, request.args.get("as_of") or "")
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/bank-accounts/<int:account_id>/reconcile/match", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def bank_reconcile_match(account_id: int):
    """Upload the bank statement (CSV) → suggested matches; nothing is saved."""
    ensure_hop_schema(_db_path())
    from app import hop_reconcile

    try:
        f = request.files.get("file")
        if f:
            raw = f.read(3 * 1024 * 1024 + 1)
            if len(raw) > 3 * 1024 * 1024:
                raise ValueError("File is too big (max 3 MB)")
            rows = hop_reconcile.parse_csv(raw.decode("utf-8-sig", errors="replace"))
        else:
            rows = (_payload().get("rows") or [])[:2000]
        with hop_db.connect(_db_path()) as conn:
            data = hop_reconcile.match_rows(conn, _ws(), account_id, rows)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/bank-entries", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def bank_entries_collection():
    ensure_hop_schema(_db_path())
    from app import hop_books

    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_books.list_bank_entries(conn, _ws(), request.args.get("account_id", type=int))
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_books.add_bank_entry(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/stock", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def stock_list():
    ensure_hop_schema(_db_path())
    from app import hop_books

    low = (request.args.get("low") or "").strip() in ("1", "true", "yes")
    with hop_db.connect(_db_path()) as conn:
        rows = hop_books.list_stock(conn, _ws(), low_only=low)
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/products/<int:product_id>/bom", methods=["GET", "PUT"])
@require_jwt_auth
@require_role(HOP_ROLE)
def product_bom(product_id: int):
    """Raw materials for one piece of an item (manufacturing)."""
    ensure_hop_schema(_db_path())
    from app import hop_manufacture

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "PUT":
                data = hop_manufacture.save_bom(conn, _ws(), product_id, (_payload() or {}).get("raw") or [])
            else:
                data = hop_manufacture.get_bom(conn, _ws(), product_id)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/products/<int:product_id>/bom/ai-suggest", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def product_bom_ai(product_id: int):
    ensure_hop_schema(_db_path())
    from app.services import hop_ai

    try:
        with hop_db.connect(_db_path()) as conn:
            user = getattr(request, "user", None) or {}
            data = hop_ai.suggest_bom(conn, _ws(), product_id, user_id=int(user.get("user_id") or 0))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/loyalty", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def loyalty_points():
    """?customer_id= → that customer's points; no id → every customer with points."""
    ensure_hop_schema(_db_path())
    from app import hop_loyalty

    cid = request.args.get("customer_id", type=int)
    exclude = request.args.get("bill_id", type=int)
    with hop_db.connect(_db_path()) as conn:
        data = (hop_loyalty.balance(conn, _ws(), cid, exclude_txn=exclude) if cid
                else hop_loyalty.top_customers(conn, _ws(), request.args.get("q") or ""))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/manufacture/plan", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def manufacture_plan():
    ensure_hop_schema(_db_path())
    from app import hop_manufacture

    body = _payload() or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_manufacture.plan(conn, _ws(), int(body.get("product_id") or 0), body.get("qty"), body.get("extra_cost"))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/manufacture", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def manufacture_entries():
    ensure_hop_schema(_db_path())
    from app import hop_manufacture

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                return jsonify({"success": True, "data": hop_manufacture.manufacture(conn, _ws(), _payload() or {})}), 201
            data = {"rows": hop_manufacture.list_entries(conn, _ws())}
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/manufacture/<int:mid>", methods=["DELETE"])
@require_jwt_auth
@require_role(HOP_ROLE)
def manufacture_undo(mid: int):
    ensure_hop_schema(_db_path())
    from app import hop_manufacture

    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_manufacture.undo(conn, _ws(), mid)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/stock/journal", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def stock_journal():
    ensure_hop_schema(_db_path())
    from app import hop_books

    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_books.add_stock_journal(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


# ── Money flow (Vyapar-style): bills a payment clears, cheques, expense categories, Convert ──

@hop_bp.route("/open-bills", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def open_bills_list():
    """?payment_type=3|4&party_type=customer|vendor&party_id=…[&payment_txn_id=…]"""
    ensure_hop_schema(_db_path())
    from app import hop_money

    pay_type = request.args.get("payment_type", type=int) or 3
    party_type = (request.args.get("party_type") or ("vendor" if pay_type == 4 else "customer")).strip().lower()
    party_id = request.args.get("party_id", type=int)
    if not party_id:
        return _json_error("party_id is required")
    with hop_db.connect(_db_path()) as conn:
        rows = hop_money.open_bills(conn, _ws(), pay_type, party_type, party_id,
                                    request.args.get("payment_txn_id", type=int))
    return jsonify({"success": True, "data": {"bills": rows, "total_due": round(sum(r["due"] for r in rows), 2)}})


@hop_bp.route("/cheques", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def cheques_list():
    ensure_hop_schema(_db_path())
    from app import hop_money

    with hop_db.connect(_db_path()) as conn:
        rows = hop_money.list_cheques(conn, _ws(), (request.args.get("status") or "").strip().lower() or None)
    return jsonify({"success": True, "data": {"cheques": rows}})


@hop_bp.route("/cheques/<int:cheque_id>/<action>", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def cheque_action(cheque_id: int, action: str):
    """close = deposit (received) / withdraw (issued) into a bank or cash account; reopen = undo."""
    ensure_hop_schema(_db_path())
    from app import hop_money

    body = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            if action == "close":
                out = hop_money.close_cheque(conn, _ws(), cheque_id, body.get("account_id"), body.get("date") or "")
            elif action == "reopen":
                out = hop_money.reopen_cheque(conn, _ws(), cheque_id)
            else:
                return _json_error("Unknown cheque action", "NOT_FOUND", 404)
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": out})


@hop_bp.route("/expense-categories", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def expense_categories():
    ensure_hop_schema(_db_path())
    from app import hop_money

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                body = _payload()
                return jsonify({"success": True, "data": hop_money.create_expense_category(
                    conn, _ws(), body.get("name"), body.get("kind") or request.args.get("kind") or "expense")}), 201
            rows = hop_money.list_expense_categories(conn, _ws(), request.args.get("kind") or "expense")
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"categories": rows}})


@hop_bp.route("/item-categories", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def item_categories():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                return jsonify({"success": True, "data": hop_settings.create_item_category(
                    conn, _ws(), _payload().get("name"))}), 201
            rows = hop_settings.list_item_categories(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"categories": rows}})


@hop_bp.route("/units", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def units_collection():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                return jsonify({"success": True, "data": hop_settings.create_unit(
                    conn, _ws(), _payload().get("name"))}), 201
            rows = hop_settings.list_units(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"units": rows}})


@hop_bp.route("/products/by-barcode", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def product_by_barcode():
    """Phone scanner / web: find the item for a barcode (or item code)."""
    ensure_hop_schema(_db_path())
    from app import hop_items

    with hop_db.connect(_db_path()) as conn:
        row = hop_items.find_by_barcode(conn, _ws(), request.args.get("code") or "")
    if not row:
        return _json_error("No item has this barcode", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/import/<kind>/preview", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_import_preview(kind: str):
    """Excel / CSV of items or parties → matched columns + checked rows. Nothing is saved."""
    ensure_hop_schema(_db_path())
    from app import hop_import

    f = request.files.get("file")
    if not f:
        return _json_error("Choose an Excel or CSV file")
    raw = f.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        return _json_error("File is too big (max 5 MB)")
    try:
        table = hop_import.read_table(raw, f.filename or "")
        with hop_db.connect(_db_path()) as conn:
            data = hop_import.preview(conn, _ws(), kind, table)
    except ValueError as exc:
        return _json_error(str(exc))
    except Exception:  # unreadable spreadsheet
        return _json_error("Could not read this file. Save it as .xlsx or .csv and try again.")
    return jsonify({"success": True, "data": data})


@hop_bp.route("/import/<kind>/commit", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_import_commit(kind: str):
    """Save the rows the user confirmed in the preview."""
    ensure_hop_schema(_db_path())
    from app import hop_import

    body = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_import.commit(conn, _ws(), kind, body.get("rows") or [], bool(body.get("update_existing")))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/audit-log", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_audit_log():
    """Who created / changed / deleted what (filters: q, entity, user, from, to)."""
    ensure_hop_schema(_db_path())
    from app import hop_audit

    a = request.args
    with hop_db.connect(_db_path()) as conn:
        data = hop_audit.list_log(conn, _ws(), q=a.get("q") or "", entity=a.get("entity") or "", user=a.get("user") or "",
                                  frm=a.get("from") or "", to=a.get("to") or "", limit=int(a.get("limit") or 200))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/backup", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_backup_download():
    """Owner only: this firm's business data (rows of this firm's workspace only — never other firms,
    other companies or the rest of the server), locked as .kunwar."""
    ensure_hop_schema(_db_path())
    from datetime import date

    from flask import Response

    from app import hop_backup, hop_permissions

    user = getattr(request, "user", None) or {}
    with hop_db.connect(_db_path()) as conn:
        owner = hop_permissions.is_owner(conn, user)
        if not owner and not hop_permissions.perms_for(conn, user).get("can_backup"):
            return _json_error("Only the owner (or staff the owner allowed) can download the backup", "FORBIDDEN", 403)
        data, _counts = hop_backup.build_locked(conn, _ws())
        if not owner:
            hop_backup.note_staff_download(conn, _ws(), user)
    name = hop_backup.file_name(date.today().isoformat())   # .kunwar — opens only in NEXORA
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@hop_bp.route("/godowns", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_godowns_route():
    """GET: godowns with item count + stock value, and stock per godown. POST {name, address}: add one."""
    ensure_hop_schema(_db_path())
    from app import hop_godowns

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                b = _payload()
                return jsonify({"success": True, "data": hop_godowns.create(conn, _ws(), b.get("name"), b.get("address") or "")}), 201
            data = {"godowns": hop_godowns.list_godowns(conn, _ws()), "stock": hop_godowns.stock_by_godown(conn, _ws())}
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/godowns/transfer", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_godown_transfer():
    ensure_hop_schema(_db_path())
    from app import hop_godowns

    b = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_godowns.transfer(conn, _ws(), int(b.get("product_id") or 0), int(b.get("from_godown_id") or 0),
                                        int(b.get("to_godown_id") or 0), float(b.get("qty") or 0), b.get("date") or "",
                                        b.get("note") or "")
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/firms", methods=["GET", "POST", "PATCH"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_firms_route():
    """Firms (companies) of this login. POST {name, gstin, state} adds one (owner only); PATCH renames."""
    from app import hop_firms, hop_permissions

    base = get_workspace_id()
    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method in ("POST", "PATCH"):
                if not hop_permissions.is_owner(conn, user):
                    return _json_error("Only the owner can add or rename firms", "FORBIDDEN", 403)
                b = _payload()
                if request.method == "POST":
                    hop_firms.create(conn, base, b.get("name"), b.get("gstin") or "", b.get("state") or "")
                else:
                    hop_firms.rename(conn, base, str(b.get("firm_ws") or ""), b.get("name"))
            firms = hop_firms.list_firms(conn, base)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"firms": firms, "active": _ws()}})


@hop_bp.route("/firms/restore", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_firms_restore():
    """Restore a NEXORA backup zip into a NEW firm (owner only). ?preview=1 only reads the file."""
    from app import hop_permissions, hop_restore

    uploaded, err = _uploaded_backup_file()
    if err:
        return err
    data = uploaded.read()
    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            if not hop_permissions.is_owner(conn, user):
                return _json_error("Only the owner can restore a backup", "FORBIDDEN", 403)
            if request.args.get("preview") in ("1", "true"):
                return jsonify({"success": True, "data": hop_restore.preview(conn, data, get_workspace_id())})
            if (request.form.get("mode") or "new") == "replace":
                bad = _check_password(user, request.form.get("password") or "")
                if bad:
                    return bad
                res = hop_restore.restore_replace(conn, _ws(), data)
            else:
                name = (request.form.get("name") or "").strip()
                res = hop_restore.restore_into_new_firm(conn, get_workspace_id(), data, name)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": res})


def _check_password(user: dict, password: str):
    """Replace / wipe need the owner's own login password. None = OK, else the error response."""
    from centralized_db_system.db import CentralizedDB

    if not str(password or "").strip():
        return _json_error("Type your login password to confirm", "PASSWORD_REQUIRED", 403)
    if not CentralizedDB(_db_path()).authenticate_user(str(user.get("username") or ""), password):
        return _json_error("Incorrect password", "INVALID_PASSWORD", 403)
    return None


@hop_bp.route("/backups", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_server_backups_route():
    """Backups on the server for the firm in use (owner, or staff allowed to download). POST = save one now (owner)."""
    from app import hop_permissions, hop_server_backups

    user = getattr(request, "user", None) or {}
    with hop_db.connect(_db_path()) as conn:
        owner = hop_permissions.is_owner(conn, user)
        if not owner and not (request.method == "GET" and hop_permissions.perms_for(conn, user).get("can_backup")):
            return _json_error("Only the owner can see the backups", "FORBIDDEN", 403)
        saved = None
        if request.method == "POST":
            saved = hop_server_backups.snapshot(conn, _ws(), "manual", force=True)
    return jsonify({"success": True, "data": {"backups": hop_server_backups.list_backups(_ws()), "saved": saved,
                                              "keep_days": hop_server_backups.KEEP_DAYS, "owner": owner}})


@hop_bp.route("/backups/<string:name>", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_server_backup_download(name: str):
    from flask import Response

    from app import hop_backup, hop_permissions, hop_server_backups

    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            owner = hop_permissions.is_owner(conn, user)
            if not owner and not hop_permissions.perms_for(conn, user).get("can_backup"):
                return _json_error("Only the owner can download backups", "FORBIDDEN", 403)
            data = hop_server_backups.read(_ws(), name)
            if not owner:
                hop_backup.note_staff_download(conn, _ws(), user)
    except ValueError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="nexora_backup_{name}"', "Cache-Control": "no-store"})


@hop_bp.route("/backups/<string:name>/restore", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_server_backup_restore(name: str):
    """Owner only. {mode: "new", name} → a new firm; {mode: "replace", password} → put it back into this firm."""
    from app import hop_permissions, hop_restore, hop_server_backups

    user = getattr(request, "user", None) or {}
    b = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            if not hop_permissions.is_owner(conn, user):
                return _json_error("Only the owner can restore a backup", "FORBIDDEN", 403)
            data = hop_server_backups.read(_ws(), name)
            if (b.get("mode") or "new") == "replace":
                bad = _check_password(user, b.get("password") or "")
                if bad:
                    return bad
                res = hop_restore.restore_replace(conn, _ws(), data)
            else:
                res = hop_restore.restore_into_new_firm(conn, get_workspace_id(), data, str(b.get("name") or "").strip())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": res})


@hop_bp.route("/catalog-link", methods=["GET", "PUT"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_catalog_link_route():
    """Online catalogue link of this firm. PUT {active, show_prices, hide_out_of_stock, message, new_code} (creates it)."""
    from app import hop_catalog_link

    with hop_db.connect(_db_path()) as conn:
        link = hop_catalog_link.save(conn, _ws(), _payload()) if request.method == "PUT" else hop_catalog_link.get(conn, _ws())
    if link:
        link["url"] = request.host_url.rstrip("/") + "/c/" + link["token"]
    return jsonify({"success": True, "data": link})


@hop_bp.route("/services-on", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_services_on():
    """Which approval services are switched on (any login) — screens show those buttons only then."""
    from app import hop_gov

    with hop_db.connect(_db_path()) as conn:
        return jsonify({"success": True, "data": {s: bool(hop_gov.live(conn, _ws(), s)) for s in hop_gov.SERVICES}})


@hop_bp.route("/gov", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_gov_status():
    """Owner: Government & approvals — each service, what to apply for, mode and which keys are saved (never the keys)."""
    from app import hop_gov

    with hop_db.connect(_db_path()) as conn:
        return jsonify({"success": True, "data": hop_gov.status(conn, _ws(), request.host_url)})


@hop_bp.route("/gov/<string:service>", methods=["PUT"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_gov_save(service: str):
    from app import hop_gov

    b = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            hop_gov.save(conn, _ws(), service, b.get("mode"), b.get("values") or {})
            return jsonify({"success": True, "data": hop_gov.status(conn, _ws(), request.host_url)})
    except ValueError as exc:
        return _json_error(str(exc))


@hop_bp.route("/gov/<string:service>/test", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_gov_test(service: str):
    from app import hop_gov, hop_gov_einvoice, hop_gov_payments, hop_gov_sms

    b = _payload()
    with hop_db.connect(_db_path()) as conn:
        if service == "einvoice":
            ok, msg = hop_gov_einvoice.test_keys(conn, _ws())
        elif service == "payments":
            ok, msg = hop_gov_payments.test_keys(conn, _ws())
        elif service == "sms":
            ok, msg = hop_gov_sms.test_keys(conn, _ws(), str(b.get("phone") or ""))
        else:
            return _json_error("Unknown service")
        res = hop_gov.note_test(conn, _ws(), service, ok, msg)
    return jsonify({"success": True, "data": res})


@hop_bp.route("/party-transactions/<int:txn_id>/pay-link", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_pay_link(txn_id: int):
    """Online payment link (business's own Razorpay) for the money due on a sale invoice."""
    from app import hop_gov_payments

    try:
        with hop_db.connect(_db_path()) as conn:
            return jsonify({"success": True, "data": hop_gov_payments.create_link(conn, _ws(), txn_id)})
    except ValueError as exc:
        return _json_error(str(exc))


@hop_bp.route("/party-transactions/<int:txn_id>/irn", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_irn(txn_id: int):
    """IRP API: {action: generate | cancel (reason, remark) | eway (distance, vehicle_no, transporter_*)}."""
    from app import hop_gov_einvoice

    b = _payload()
    action = b.get("action") or "generate"
    try:
        with hop_db.connect(_db_path()) as conn:
            if action == "cancel":
                data = hop_gov_einvoice.cancel_irn(conn, _ws(), txn_id, str(b.get("reason") or "2"), str(b.get("remark") or ""))
            elif action == "eway":
                data = hop_gov_einvoice.eway_by_irn(conn, _ws(), txn_id, b)
            else:
                data = hop_gov_einvoice.generate_irn(conn, _ws(), txn_id)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/sms/send", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_sms_send():
    """NEXORA sends the SMS itself (DLT provider) — only when switched on; else the phone's SMS app is used."""
    from app import hop_gov_sms

    b = _payload()
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_gov_sms.send(conn, _ws(), str(b.get("phone") or ""), str(b.get("text") or ""), str(b.get("kind") or "bill"))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/qa-marks", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_qa_marks():
    """T&P (Tested & Passed) badges for screens checked on the phone — app/qa_marks.json; show=false hides them all."""
    import json as _json
    import os as _os

    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "qa_marks.json")
    try:
        data = _json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        data = {"show": False, "screens": {}}
    if not data.get("show"):
        data = {"show": False, "screens": {}}
    return jsonify({"success": True, "data": data})


@hop_bp.route("/help", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_help_route():
    """In-app guide for every HoP screen (same text on phone and web). ?screen=<key> for one."""
    from app import hop_help

    key = (request.args.get("screen") or "").strip()
    if key:
        t = hop_help.topic(key)
        return jsonify({"success": True, "data": t}) if t else _json_error("No guide for this screen yet", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": hop_help.all_topics()})


@hop_bp.route("/my-permissions", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_my_permissions():
    """What this login may open (screens hide the rest)."""
    from app import hop_permissions

    with hop_db.connect(_db_path()) as conn:
        data = hop_permissions.perms_for(conn, getattr(request, "user", None) or {})
    return jsonify({"success": True, "data": data})


@hop_bp.route("/staff-permissions", methods=["GET", "PUT"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_staff_permissions():
    """Owner only: staff logins with their permissions; PUT {user_id, areas | preset} saves one."""
    from app import hop_permissions

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "PUT":
                b = _payload()
                hop_permissions.save(conn, get_workspace_id(), getattr(request, "user", None) or {}, int(b.get("user_id") or 0),
                                     b.get("areas") or {}, b.get("preset"),
                                     None if "can_backup" not in b else b.get("can_backup") in (True, 1, "1", "true"))
            data = hop_permissions.staff_list(conn, get_workspace_id())
    except PermissionError as exc:
        return _json_error(str(exc), "FORBIDDEN", 403)
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/staff-permissions/check", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_staff_permissions_check():
    """Plain summary + warnings for a set of permissions before saving (no change)."""
    from app import hop_permissions

    areas = (_payload().get("areas") or {})
    return jsonify({"success": True, "data": {"summary": hop_permissions.summary(areas),
                                              "warnings": hop_permissions.risky(areas)}})


@hop_bp.route("/suggest-rate", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def suggest_rate():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_settings.suggest_party_rate(
                conn, _ws(),
                party_type=request.args.get("party_type") or "customer",
                party_id=int(request.args.get("party_id") or 0),
                product_id=int(request.args["product_id"]) if request.args.get("product_id") else None,
                item_name=request.args.get("item_name") or "",
                qty=float(request.args.get("qty") or 0),
            )
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/entry-warnings", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def entry_warnings():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    with hop_db.connect(_db_path()) as conn:
        warnings = hop_settings.invoice_entry_warnings(conn, _ws(), _payload())
    return jsonify({"success": True, "data": {"warnings": warnings}})


@hop_bp.route("/overdue-bills", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def overdue_bills():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    with hop_db.connect(_db_path()) as conn:
        data = hop_settings.overdue_bills(
            conn, _ws(),
            as_of=request.args.get("as_of"),
            party_type=request.args.get("party_type"),
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/party-transactions/<int:party_txn_id>/receipt", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_txn_receipt(party_txn_id: int):
    """Thermal receipt lines for the phone's Bluetooth printer (?width=32 for 58 mm, 48 for 80 mm)."""
    ensure_hop_schema(_db_path())
    from app import hop_receipt
    from app.hop_doc_preview import build_txn_preview

    with hop_db.connect(_db_path()) as conn:
        data = build_txn_preview(conn, _ws(), party_txn_id=party_txn_id)
    if not data:
        return _json_error("Bill not found", "NOT_FOUND", 404)
    width = request.args.get("width", type=int)
    return jsonify({"success": True, "data": hop_receipt.build(data, width)})


@hop_bp.route("/party-transactions/<int:party_txn_id>/share", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_txn_share(party_txn_id: int):
    """WhatsApp text (owner's template), party number, wa.me link and UPI payment link for a bill."""
    ensure_hop_schema(_db_path())
    from app import hop_share

    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_share.share_payload(conn, _ws(), party_txn_id, request.args.get("kind") or "bill")
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reminders", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def reminders_list():
    """Money to collect: overdue, due today, due soon — each with reminder text, WhatsApp link, email."""
    ensure_hop_schema(_db_path())
    from app import hop_reminders

    with hop_db.connect(_db_path()) as conn:
        data = hop_reminders.due_list(conn, _ws())
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reminders/<int:txn_id>/<string:channel>", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def reminders_send(txn_id: int, channel: str):
    """channel = email (send now) or whatsapp (the phone opened WhatsApp — just note it)."""
    ensure_hop_schema(_db_path())
    from app import hop_firms, hop_reminders

    if channel not in ("email", "whatsapp"):
        return _json_error("Unknown channel", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        if channel == "whatsapp":
            hop_reminders.mark_sent(conn, _ws(), txn_id, "whatsapp")
            return jsonify({"success": True, "data": {"noted": True}})
        dl = hop_reminders.due_list(conn, _ws())
        item = next((x for g in ("overdue", "today", "soon") for x in dl[g] if int(x["id"]) == txn_id), None)
        if not item:
            return _json_error("This bill has nothing due right now", "NOT_FOUND", 404)
        ok, err = hop_reminders.email_reminder(conn, _ws(), item, hop_firms.business_name(conn, _ws(), "Your business"),
                                               hop_reminders._owner_email(conn, _ws()))
    if not ok:
        return _json_error(err, "EMAIL_FAILED", 400)
    return jsonify({"success": True, "data": {"sent_to": item["email"]}})


@hop_bp.route("/reminders/send-me/<string:what>", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def reminders_send_me(what: str):
    """Owner taps "Send me today's summary now" / "Email me a backup now"."""
    ensure_hop_schema(_db_path())
    from app import hop_reminders

    if what not in ("summary", "backup"):
        return _json_error("Unknown option", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        res = hop_reminders.run_for_workspace(conn, _ws(), only={what})
        to = hop_reminders._owner_email(conn, _ws())
    if not res.get(what):
        return _json_error("Nothing was sent — " + (", ".join(res.get("skipped") or []) or
                           ("no bills to collect" if what == "summary" else "no email on your profile")), "NOT_SENT", 400)
    return jsonify({"success": True, "data": {"sent_to": to}})


@hop_bp.route("/reminders/service", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def service_reminders_list():
    """Services due (items with 'service every N days' sold on invoices): overdue / today / soon."""
    ensure_hop_schema(_db_path())
    from app import hop_service_reminders

    with hop_db.connect(_db_path()) as conn:
        data = hop_service_reminders.due_list(conn, _ws())
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reminders/service/<int:line_id>/<string:action>", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def service_reminders_mark(line_id: int, action: str):
    """action = done (service finished; body done_on optional) or whatsapp (the phone opened WhatsApp)."""
    ensure_hop_schema(_db_path())
    from app import hop_service_reminders

    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_service_reminders.mark(conn, _ws(), line_id, action, (_payload().get("done_on") or None))
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/items/service-suggest", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def item_service_suggest():
    """Typical service gap for an item name (rule based) — shown as a tip on the item form."""
    from app import hop_service_reminders

    return jsonify({"success": True, "data": {"days": hop_service_reminders.suggest_days(request.args.get("name") or "")}})


@hop_bp.route("/ai/payment-reminder", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def ai_payment_reminder():
    ensure_hop_schema(_db_path())
    from app.services import hop_ai

    payload = _payload()
    txn_id = int(payload.get("txn_id") or payload.get("party_txn_id") or 0)
    if not txn_id:
        return _json_error("txn_id is required")
    user = getattr(request, "user", None) or {}
    with hop_db.connect(_db_path()) as conn:
        bill = conn.execute(
            """SELECT id, party_name, txn_number, due_date, balance_amount, txn_date, total_amount
               FROM hop_party_transactions WHERE workspace_id=? AND id=?""",
            (_ws(), txn_id),
        ).fetchone()
        if not bill:
            return _json_error("Bill not found", "NOT_FOUND", 404)
        firm = conn.execute(
            "SELECT firm_name FROM hop_firm_profile WHERE workspace_id=? LIMIT 1", (_ws(),)
        ).fetchone()
        firm_name = (firm["firm_name"] if firm else "") or ""
        try:
            out = hop_ai.payment_reminder_text(
                dict(bill), firm_name=firm_name, user_id=int(user.get("user_id") or 0)
            )
        except ValueError as exc:
            return _json_error(str(exc))
    return jsonify({"success": True, "data": out})


@hop_bp.route("/stock/reorder", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def stock_reorder():
    ensure_hop_schema(_db_path())
    from app import hop_settings

    with hop_db.connect(_db_path()) as conn:
        data = hop_settings.low_stock_reorder(
            conn, _ws(),
            days=int(request.args.get("days") or 30),
            low_at=float(request.args.get("low_at") or 0),
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/print-theme", methods=["GET", "PUT"])
@require_jwt_auth
@require_role(HOP_ROLE)
def print_theme():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    with hop_db.connect(_db_path()) as conn:
        if request.method == "PUT":
            try:
                theme = hop_phase3_ext.set_print_theme(conn, _ws(), _payload().get("theme_id"))
            except ValueError as exc:
                return _json_error(str(exc))
            return jsonify({"success": True, "data": theme})
        return jsonify({"success": True, "data": {
            "active": hop_phase3_ext.get_print_theme(conn, _ws()),
            "presets": hop_phase3_ext.list_print_themes(),
        }})


@hop_bp.route("/recycle-bin", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def recycle_bin():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    with hop_db.connect(_db_path()) as conn:
        data = hop_phase3_ext.list_recycle_bin(conn, _ws(), kind=request.args.get("kind") or "all")
    return jsonify({"success": True, "data": data})


@hop_bp.route("/recycle-bin/<kind>/<int:item_id>/restore", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def recycle_bin_restore(kind: str, item_id: int):
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    try:
        with hop_db.connect(_db_path()) as conn:
            if kind == "docs":
                out = hop_phase3_ext.restore_document(conn, _ws(), item_id)
            elif kind == "products":
                out = hop_phase3_ext.restore_product(conn, _ws(), item_id)
            else:
                return _json_error("kind must be docs or products")
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    return jsonify({"success": True, "data": out})


@hop_bp.route("/loan-accounts", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def loan_accounts():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                return jsonify({"success": True, "data": hop_phase3_ext.create_loan_account(
                    conn, _ws(), _payload())}), 201
            rows = hop_phase3_ext.list_loan_accounts(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"loans": rows}})


@hop_bp.route("/loan-accounts/<int:loan_id>/entries", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def loan_account_entries(loan_id: int):
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    try:
        with hop_db.connect(_db_path()) as conn:
            out = hop_phase3_ext.add_loan_entry(conn, _ws(), loan_id, _payload())
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": out}), 201


@hop_bp.route("/fixed-assets", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def fixed_assets():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method == "POST":
                return jsonify({"success": True, "data": hop_phase3_ext.create_fixed_asset(
                    conn, _ws(), _payload())}), 201
            rows = hop_phase3_ext.list_fixed_assets(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"assets": rows}})


@hop_bp.route("/fixed-assets/run-depreciation", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def fixed_assets_run_dep():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    with hop_db.connect(_db_path()) as conn:
        out = hop_phase3_ext.run_depreciation(conn, _ws(), (_payload().get("as_of") if request.is_json else None) or request.args.get("as_of") or "")
    return jsonify({"success": True, "data": out})


@hop_bp.route("/financial-years", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def financial_years():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    with hop_db.connect(_db_path()) as conn:
        rows = hop_phase3_ext.list_financial_years(conn, _ws())
    return jsonify({"success": True, "data": {"years": rows}})


@hop_bp.route("/financial-years/<int:fy_id>/close", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def financial_year_close(fy_id: int):
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext

    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            out = hop_phase3_ext.close_financial_year(
                conn, _ws(), fy_id, actor=str(user.get("username") or user.get("user_id") or "")
            )
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    return jsonify({"success": True, "data": out})


@hop_bp.route("/export/tally.xml", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def export_tally_xml():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext
    from flask import Response

    frm = request.args.get("from") or ""
    to = request.args.get("to") or ""
    with hop_db.connect(_db_path()) as conn:
        xml_bytes = hop_phase3_ext.build_tally_xml(conn, _ws(), frm, to)
    return Response(
        xml_bytes,
        mimetype="application/xml",
        headers={"Content-Disposition": 'attachment; filename="nexora_tally_export.xml"'},
    )


@hop_bp.route("/export/tally.csv", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def export_tally_csv():
    ensure_hop_schema(_db_path())
    from app import hop_phase3_ext
    from flask import Response

    frm = request.args.get("from") or ""
    to = request.args.get("to") or frm
    with hop_db.connect(_db_path()) as conn:
        csv_text = hop_phase3_ext.build_tally_daybook_csv(conn, _ws(), frm, to)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="nexora_tally_daybook.csv"'},
    )


@hop_bp.route("/party-transactions/<int:txn_id>/convert", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_convert(txn_id: int):
    """?to=<txn_type> → a prefilled draft; save it with POST /party-transactions (keeps linked_from_txn_id)."""
    ensure_hop_schema(_db_path())
    from app import hop_money

    to_type = request.args.get("to", type=int)
    try:
        with hop_db.connect(_db_path()) as conn:
            draft = hop_money.convert_draft(conn, _ws(), txn_id, int(to_type or 0))
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": draft})


@hop_bp.route("/party-transactions/<int:txn_id>/links", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transaction_links(txn_id: int):
    ensure_hop_schema(_db_path())
    from app import hop_money

    with hop_db.connect(_db_path()) as conn:
        t = conn.execute("SELECT txn_type, converted_to_txn_id, linked_from_txn_id FROM hop_party_transactions "
                         "WHERE workspace_id=? AND id=?", (_ws(), txn_id)).fetchone()
        if not t:
            return _json_error("Document not found", "NOT_FOUND", 404)
        links = hop_money.links_of(conn, _ws(), txn_id)
    return jsonify({"success": True, "data": {
        "links": links,
        "convert_to": [{"txn_type": k, "label": hop_money.LABELS[k]} for k in hop_money.CONVERT_TO.get(int(t[0]), ())]
        if not t[1] else [],
        "converted_to_txn_id": t[1], "linked_from_txn_id": t[2], "txn_type": int(t[0] or 0),
    }})


@hop_bp.route("/ai/read-bill", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def ai_read_bill():
    """Multipart file (PDF / photo, printed or handwritten) → form values. Nothing is saved.
    form field kind = purchase (bill / expense) | sale (sale invoice) | quote (quotation / estimate)."""
    ensure_hop_schema(_db_path())
    from app.services import hop_ai

    f = request.files.get("file")
    if not f or not getattr(f, "filename", None):
        return _json_error("Choose a bill photo or PDF")
    mime = (f.mimetype or "").lower()
    if not (mime.startswith("image/") or mime == "application/pdf"):
        return _json_error("Use a PDF or a photo (JPG / PNG)")
    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            kind = (request.form.get("kind") or request.args.get("kind") or "purchase").strip().lower()
            if kind not in ("purchase", "sale", "quote"):
                kind = "purchase"
            out = hop_ai.read_bill(conn, _ws(), f.read(), mime, user_id=int(user.get("user_id") or 0), kind=kind)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": out})


@hop_bp.route("/gst-files/<kind>", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def gst_files(kind: str):
    """kind = einvoice | eway. Body: {txn_ids: [...], transport: {...}} → government JSON + problems to fix."""
    ensure_hop_schema(_db_path())
    from app import hop_einvoice

    body = _payload()
    ids = [int(x) for x in (body.get("txn_ids") or []) if str(x).isdigit()][:100]
    if not ids:
        return _json_error("Choose at least one sale invoice")
    try:
        with hop_db.connect(_db_path()) as conn:
            if kind == "einvoice":
                out = hop_einvoice.einvoice_json(conn, _ws(), ids)
            elif kind == "eway":
                out = hop_einvoice.eway_json(conn, _ws(), ids, body.get("transport") or {})
            else:
                return _json_error("Unknown file type", "NOT_FOUND", 404)
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": out})


@hop_bp.route("/party-transactions/<int:txn_id>/gst-refs", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def gst_refs(txn_id: int):
    """Save IRN / Ack no / Ack date / E-Way Bill no after uploading on the government portal."""
    ensure_hop_schema(_db_path())
    from app import hop_einvoice

    try:
        with hop_db.connect(_db_path()) as conn:
            out = hop_einvoice.save_refs(conn, _ws(), txn_id, _payload())
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    return jsonify({"success": True, "data": out})


@hop_bp.route("/app-settings", methods=["GET", "PUT", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def app_settings():
    """Settings screen (web + phone are built from the same list). PUT/POST {key: value, …}."""
    ensure_hop_schema(_db_path())
    from app import hop_app_settings

    try:
        with hop_db.connect(_db_path()) as conn:
            if request.method != "GET":
                hop_app_settings.save(conn, _ws(), _payload())
            data = hop_app_settings.payload(conn, _ws())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/books", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def books_report_list():
    from app import hop_reports

    return jsonify({"success": True, "data": {"reports": [{"key": k, "title": v} for k, v in hop_reports.REPORTS.items()]}})


@hop_bp.route("/books/<key>", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def books_report(key: str):
    """Vyapar-style reports from the double-entry ledger. Query: from, to, date, party_id,
    party_type, product_id, txn_type, low_at."""
    ensure_hop_schema(_db_path())
    from app import hop_reports

    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_reports.run(conn, _ws(), key, request.args.to_dict())
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": data})


@hop_bp.route("/books/<key>/insights", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def books_report_insights(key: str):
    """✨ AI insights: 3–5 short points about the report (one capped Gemini call)."""
    ensure_hop_schema(_db_path())
    from app import hop_reports
    from app.services import hop_ai

    user = getattr(request, "user", None) or {}
    try:
        with hop_db.connect(_db_path()) as conn:
            data = hop_reports.run(conn, _ws(), key, request.args.to_dict())
        points = hop_ai.report_insights(hop_reports.REPORTS.get(key, key), data, user_id=int(user.get("user_id") or 0))
    except LookupError as exc:
        return _json_error(str(exc), "NOT_FOUND", 404)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": {"points": points}})


@hop_bp.route("/reports/gst-hsn", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def report_gst_hsn():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        data = hop_books.gst_hsn_summary(
            conn, _ws(), request.args.get("from") or "", request.args.get("to") or ""
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reports/gstr1", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def report_gstr1():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        data = hop_books.gstr1_sales(
            conn, _ws(), request.args.get("from") or "", request.args.get("to") or ""
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/accounts", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def accounts_list():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        rows = hop_books.ensure_default_accounts(conn, _ws())
    return jsonify({"success": True, "data": rows})


@hop_bp.route("/reports/trial-balance", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def report_trial_balance():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        data = hop_books.trial_balance(conn, _ws())
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reports/profit-loss", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def report_profit_loss():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        data = hop_books.profit_and_loss(
            conn, _ws(), request.args.get("from") or "", request.args.get("to") or ""
        )
    return jsonify({"success": True, "data": data})


@hop_bp.route("/reports/balance-sheet", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def report_balance_sheet():
    ensure_hop_schema(_db_path())
    from app import hop_books

    with hop_db.connect(_db_path()) as conn:
        data = hop_books.balance_sheet(conn, _ws())
    return jsonify({"success": True, "data": data})
