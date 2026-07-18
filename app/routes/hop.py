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


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _sum(conn: sqlite3.Connection, sql: str, params: tuple) -> float:
    row = conn.execute(sql, params).fetchone()
    return float(row[0] or 0) if row else 0.0


def _payload():
    return request.get_json(silent=True) or {}


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
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_db.create_customer(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


@hop_bp.route("/customers/<int:customer_id>", methods=["GET"])
@require_jwt_auth
@require_role(HOP_ROLE)
def customers_get(customer_id: int):
    ensure_hop_schema(_db_path())
    with hop_db.connect(_db_path()) as conn:
        row = hop_db.get_customer(conn, _ws(), customer_id)
    if not row:
        return _json_error("Customer not found", "NOT_FOUND", 404)
    return jsonify({"success": True, "data": row})


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
@hop_bp.route("/vendors", methods=["GET", "POST"])
@require_jwt_auth
@require_role(HOP_ROLE)
def vendors_collection():
    ensure_hop_schema(_db_path())
    if request.method == "GET":
        with hop_db.connect(_db_path()) as conn:
            rows = hop_ops.list_vendors(conn, _ws(), q=request.args.get("q"))
        return jsonify({"success": True, "data": rows})
    try:
        with hop_db.connect(_db_path()) as conn:
            row = hop_ops.create_vendor(conn, _ws(), _payload())
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "data": row}), 201


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
