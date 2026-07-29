"""House of Prizm APIs — isolated from NEXORA executive routes.

Role gate: hop_admin only.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app import hop_db, hop_ops
from app.hop_schema import HOP_ROLE, HOP_WORKSPACE_ID, LEAD_STAGES, PROJECT_STAGES, ensure_hop_schema
from app.routes.auth import require_jwt_auth, require_role

hop_bp = Blueprint("hop", __name__, url_prefix="/api/v1/hop")


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _ws() -> str:
    return HOP_WORKSPACE_ID


def _json_error(message: str, code: str = "BAD_REQUEST", status: int = 400):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


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


def _payload():
    return request.get_json(silent=True) or {}


def _uploaded_backup_file():
    f = request.files.get("backup_file") or request.files.get("file")
    if not f or not getattr(f, "filename", None):
        return None, _json_error("backup_file is required", "VALIDATION_ERROR", 400)
    return f, None


@hop_bp.route("/meta", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def hop_meta():
    ensure_hop_schema(_db_path())
    return jsonify(
        {
            "success": True,
            "data": {
                "product": "House of Prizm",
                "architecture": "project_centric",
                "workspace_id": HOP_WORKSPACE_ID,
                "project_stages": PROJECT_STAGES,
                "lead_stages": LEAD_STAGES,
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
                "product": "House of Prizm",
                "role": HOP_ROLE,
                "workspace_id": HOP_WORKSPACE_ID,
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
    from app.services.vyapar_importer import import_vyapar_backup

    try:
        with hop_db.connect(_db_path()) as conn:
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
    """Wipe all hop_* business data. Password gate reserved for later."""
    ensure_hop_schema(_db_path())
    payload = _payload()
    # Future: require password when HOP_WIPE_PASSWORD is configured.
    # For now the UI is open; password field is accepted but not enforced.
    _ = str(payload.get("password") or "").strip()
    try:
        with hop_db.connect(_db_path()) as conn:
            result = hop_ops.wipe_hop_data(conn)
    except Exception as exc:
        return _json_error(f"Wipe failed: {exc}", "WIPE_FAILED", 500)
    return jsonify({"success": True, "data": result})


@hop_bp.route("/executive/snapshot", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def executive_snapshot():
    ensure_hop_schema(_db_path())
    ws = _ws()
    now = datetime.now(timezone.utc)
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
                "gross_profit_pct": profit.get("gross_margin_pct"),
                "cash_available": None,
                "notes": {
                    "gross_profit_pct": profit.get("notes", {}).get("cogs"),
                    "cash_available": "Unavailable until banking/cash ledger is connected",
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
        try:
            with hop_db.connect(_db_path()) as conn:
                ok = hop_db.delete_customer(conn, _ws(), customer_id)
        except ValueError as exc:
            return _json_error(str(exc), "DELETE_BLOCKED", 409)
        if not ok:
            return _json_error("Customer not found", "NOT_FOUND", 404)
        return jsonify({"success": True, "data": {"deleted": customer_id}})
    with hop_db.connect(_db_path()) as conn:
        row = hop_db.get_customer(conn, _ws(), customer_id)
    if not row:
        return _json_error("Customer not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": row})


@hop_bp.route("/customers/bulk-delete", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_bulk_delete():
    ensure_hop_schema(_db_path())
    ids = _payload().get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _json_error("ids list is required", "VALIDATION_ERROR", 400)
    try:
        id_list = [int(x) for x in ids]
    except (TypeError, ValueError):
        return _json_error("ids must be integers", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        result = hop_db.delete_customers_bulk(conn, _ws(), id_list)
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
    try:
        with hop_db.connect(_db_path()) as conn:
            ok = hop_ops.delete_vendor(conn, _ws(), vendor_id)
    except ValueError as exc:
        return _json_error(str(exc), "DELETE_BLOCKED", 409)
    if not ok:
        return _json_error("Vendor not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": {"deleted": vendor_id}})


@hop_bp.route("/vendors/bulk-delete", methods=["POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_bulk_delete():
    ensure_hop_schema(_db_path())
    ids = _payload().get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _json_error("ids list is required", "VALIDATION_ERROR", 400)
    try:
        id_list = [int(x) for x in ids]
    except (TypeError, ValueError):
        return _json_error("ids must be integers", "VALIDATION_ERROR", 400)
    with hop_db.connect(_db_path()) as conn:
        result = hop_ops.delete_vendors_bulk(conn, _ws(), id_list)
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
    with hop_db.connect(_db_path()) as conn:
        matches = find_party_matches(
            conn,
            _ws(),
            company=company,
            gst_no=str(payload.get("gst_no") or ""),
            mobile=str(payload.get("mobile") or ""),
            party_type=str(payload.get("party_type") or "both"),
            exclude_id=payload.get("exclude_id"),
            exclude_party_type=payload.get("exclude_party_type"),
        )
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
                        "client_name": "House of Prizm — procurement",
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


@hop_bp.route("/party-transactions", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def party_transactions_collection():
    ensure_hop_schema(_db_path())
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
            WHERE workspace_id=?
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
    with hop_db.connect(_db_path()) as conn:
        row = hop_ops.upsert_target(conn, _ws(), period, amount)
    return jsonify({"success": True, "data": row})


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
                "hint": "Demo bank works offline/free. Catalogue row still needs a fabric photo until swatches are stored.",
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
