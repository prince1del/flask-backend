"""
Distributor Filled-Order Matching — Flask Routes (per-user isolation)

Reuses article_master_parser.py's header detection / category mapping /
item_key logic, and article_master_db.py's get_article_by_item_key for
matching. Does not duplicate that logic here.

Upload is a multi-step confirm-then-resubmit flow (same UX pattern as
Article Master's upload: confirmation_required -> confirm -> commit), with
two extra confirmation points specific to this feature:

  1. qty_column_confirmation_required — only when multiple quantity-looking
     columns are found and no saved preference exists yet.
  2. season_confirmation_required — every upload, unless season or
     use_last_season is supplied (per spec, season is never guessed from the
     file itself).

Only after both are resolved does the endpoint return the final
`confirmation_required` preview; a `confirm_commit=true` resubmit persists it.
"""

import io
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Blueprint, current_app, jsonify, request, send_file

import article_master_db as amdb
import article_master_parser as amparser
import filled_orders_db as fodb
import filled_orders_distributor as fodist
import filled_orders_parser as foparser
from app.routes.auth import get_workspace_id, require_jwt_auth

filled_orders_bp = Blueprint("filled_orders", __name__, url_prefix="/api/v1/filled-orders")

DEFAULT_KEY_FIELDS = ["brand", "size"]


def _sanitize_for_json(value):
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _cleanup_temp_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _get_db_connection():
    db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    conn = sqlite3.connect(db_path)
    fodb.ensure_schema(conn)
    return conn


def _get_current_user_id():
    user = getattr(request, "user", None)
    if isinstance(user, dict) and user.get("user_id") is not None:
        return int(user["user_id"])
    raise RuntimeError("Authentication required")


def _get_changed_by():
    user = getattr(request, "user", None)
    if isinstance(user, dict) and user.get("username"):
        return str(user["username"])
    return "user"


def _bool_form(value):
    return (value or "").strip().lower() in {"true", "1", "yes"}


def _duplicate_order_response(
    conn, user_id, distributor_id, category, season, distributor_name_raw, message=None,
):
    existing = fodb.find_filled_order_by_distributor_category_season(
        conn, user_id, distributor_id, category, season,
    )
    uploaded_on = (existing or {}).get("created_at", "")[:10] if existing else ""
    default_message = (
        f"{distributor_name_raw} already has a {category} filled order for season "
        f"{season}{f' (uploaded {uploaded_on})' if uploaded_on else ''}. "
        "Replace it with this upload?"
    )
    return jsonify({
        "status": "duplicate_order_confirmation_required",
        "message": message or default_message,
        "existing_order": _sanitize_for_json(existing) if existing else None,
        "category": category,
        "season": season,
        "distributor_id": distributor_id,
        "distributor_name": distributor_name_raw,
    }), 200


def _detect_category_from_upload_file(tmp_path: str, filename: str) -> str | None:
    return foparser.detect_category_from_order_file(tmp_path, filename=filename)


@filled_orders_bp.route("/preview", methods=["POST"])
@require_jwt_auth
def preview_filled_order():
    """Lightweight file preview: suggest distributor + detect category for UI dropdowns."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    workspace_id = get_workspace_id()
    db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    filename = file.filename or "upload.xlsx"

    suggestion = fodist.suggest_distributor_from_filename(filename, workspace_id, db_path=db_path)

    detected_category = None
    preview_warning = None
    suffix = Path(filename).suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    file.save(tmp_path)
    try:
        detected_category = _detect_category_from_upload_file(tmp_path, filename)
    except Exception as exc:
        # Keep filename→distributor suggestion even when sheet headers are odd.
        msg = str(exc)
        if "xlrd" in msg.lower() and ".xls" in (filename or "").lower():
            preview_warning = (
                "Old Excel (.xls) needs xlrd — install with: pip install 'xlrd>=2.0.1'. "
                "Then re-select the file so Bed/Bath can auto-detect for any distributor."
            )
        else:
            preview_warning = msg
        detected_category = amparser.detect_category([], filename=filename)
    finally:
        _cleanup_temp_file(tmp_path)

    payload = {
        "status": "preview",
        "filename": filename,
        "suggested_distributor": _sanitize_for_json(suggestion),
        "detected_category": detected_category,
    }
    if preview_warning:
        payload["warning"] = preview_warning
    return jsonify(payload), 200


@filled_orders_bp.route("/upload", methods=["POST"])
@require_jwt_auth
def upload_filled_order():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()

    distributor_id = request.form.get("distributor_id", type=int)
    distributor_name_raw = (
        (request.form.get("distributor_name_raw") or "").strip()
        or Path(file.filename or "upload").stem
    )
    category = (request.form.get("category") or "").strip() or None
    season = (request.form.get("season") or "").strip() or None
    use_last_season = _bool_form(request.form.get("use_last_season"))
    confirm_commit = _bool_form(request.form.get("confirm_commit"))
    confirm_replace = _bool_form(request.form.get("confirm_replace"))

    filename_stem = fodist.normalize_filename_stem(file.filename or "upload")

    if not distributor_id:
        db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
        suggestion = fodist.suggest_distributor_from_filename(
            file.filename or "upload.xlsx", workspace_id, db_path=db_path,
        )
        return jsonify({
            "status": "distributor_confirmation_required",
            "message": (
                "Distributor suggested from filename — click Yes if correct, "
                "or No to select manually from the dropdown."
            ),
            "filename_hint": filename_stem,
            "suggested_distributor": _sanitize_for_json(suggestion),
        }), 200

    conn = _get_db_connection()
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    file.save(tmp_path)

    try:
        # Detect category from first order tab if caller did not send one.
        if not category:
            category = foparser.detect_category_from_order_file(
                tmp_path, filename=file.filename,
            )
            if not category:
                return jsonify({
                    "error": "Could not detect category — send 'category' as Bed, Bath, TOB, or Pillow.",
                }), 400

        categories = amdb.get_all_categories(conn, user_id)
        key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}
        key_fields = key_fields_lookup.get(category, DEFAULT_KEY_FIELDS)

        pref = fodb.get_qty_column_pref(conn, user_id, distributor_id, category)
        workbook = foparser.parse_filled_order_workbook(
            tmp_path, category, pref_column_name=pref,
        )

        if workbook.get("status") == "qty_column_confirmation_required":
            qty_detection = workbook["qty_detection"]
            return jsonify({
                "status": "qty_column_confirmation_required",
                "message": (
                    "Excel mein multiple quantity-looking columns hain. "
                    "Pieces / Qty chunein — No of Bales nahi."
                ),
                "guidance": qty_detection.get("guidance"),
                "candidates": _sanitize_for_json(qty_detection["candidates"]),
                "relationships": qty_detection.get("relationships") or [],
                "category": category,
                "distributor_id": distributor_id,
                "sheet_name": workbook.get("sheet_name"),
                "sheets_detected": workbook.get("sheet_names"),
            }), 200

        parsed_rows = workbook["parsed_rows"]
        qty_col_label = workbook["quantity_column_used"]
        bales_col_label = workbook.get("bales_column_used")

        if not season and not use_last_season:
            last_season = fodb.get_last_season(conn, user_id)
            return jsonify({
                "status": "season_confirmation_required",
                "last_season": last_season,
                "category": category,
                "distributor_id": distributor_id,
            }), 200

        if use_last_season and not season:
            season = fodb.get_last_season(conn, user_id)
            if not season:
                return jsonify({"error": "No previous season found — please enter a season."}), 400

        category = (category or "").strip()
        season = (season or "").strip()

        matched_items = [
            foparser.match_and_normalize(
                conn, amdb, user_id, row, key_fields, category=category,
                qty_column_label=qty_col_label,
            )
            for row in parsed_rows
        ]
        for parsed_row, item in zip(parsed_rows, matched_items):
            foparser.annotate_item_issues(
                conn, amdb, user_id, item, key_fields, category=category,
                core_fields=parsed_row.get("core_fields"),
                extra_attributes=parsed_row.get("extra_attributes"),
            )

        matched_count = sum(1 for m in matched_items if m["matched"])
        unmatched_count = len(matched_items) - matched_count
        flagged_count = sum(1 for m in matched_items if not m["is_clean_bale_multiple"])
        bale_mismatch_items = [m for m in matched_items if m.get("bale_qty_mismatch")]
        bale_mismatch_count = len(bale_mismatch_items)
        unit_values = {m["detected_unit"] for m in matched_items}
        quantity_unit_used = (
            "mixed" if len(unit_values) > 1 else (next(iter(unit_values)) if unit_values else "pieces")
        )

        # Prefer proper firm name over concern-person name (never nick/short name).
        dist_row = conn.execute(
            "SELECT firm_name, name FROM master_distributors WHERE id = ?",
            (distributor_id,),
        ).fetchone()
        if dist_row:
            firm_name = (dist_row[0] or "").strip()
            contact_name = (dist_row[1] or "").strip()
            distributor_name_raw = firm_name or contact_name or distributor_name_raw

        existing_order = fodb.find_filled_order_by_distributor_category_season(
            conn, user_id, distributor_id, category, season,
        )

        if not confirm_commit:
            return jsonify({
                "status": "confirmation_required",
                "message": "Preview ready — confirm to save.",
                "category": category,
                "season": season,
                "distributor_id": distributor_id,
                "distributor_name": distributor_name_raw,
                "quantity_column_used": qty_col_label,
                "bales_column_used": bales_col_label,
                "quantity_unit_used": quantity_unit_used,
                "sheets_read": workbook.get("sheet_names"),
                "total_lines": len(matched_items),
                "matched_lines": matched_count,
                "unmatched_lines": unmatched_count,
                "flagged_lines": flagged_count,
                "bale_mismatch_lines": bale_mismatch_count,
                "key_fields": key_fields,
                "existing_order": _sanitize_for_json(existing_order) if existing_order else None,
                "issue_items": _sanitize_for_json([m for m in matched_items if m.get("has_issue")]),
                "unmatched_items": _sanitize_for_json([m for m in matched_items if not m["matched"]]),
                "flagged_items": _sanitize_for_json([m for m in matched_items if not m["is_clean_bale_multiple"]]),
                "bale_mismatch_items": _sanitize_for_json(bale_mismatch_items),
                "all_items": _sanitize_for_json(matched_items),
                "sample_items": _sanitize_for_json(matched_items[:10]),
            }), 200

        if existing_order and not confirm_replace:
            return _duplicate_order_response(
                conn, user_id, distributor_id, category, season, distributor_name_raw,
            )

        skip_keys_raw = request.form.get("skip_item_keys") or "[]"
        try:
            skip_keys = {k for k in json.loads(skip_keys_raw) if k}
        except json.JSONDecodeError:
            skip_keys = set()
        if skip_keys:
            matched_items = [m for m in matched_items if m.get("item_key") not in skip_keys]
            matched_count = sum(1 for m in matched_items if m["matched"])
            unmatched_count = len(matched_items) - matched_count
            flagged_count = sum(1 for m in matched_items if not m["is_clean_bale_multiple"])
            bale_mismatch_count = sum(1 for m in matched_items if m.get("bale_qty_mismatch"))

        category = (category or "").strip()
        season = (season or "").strip()
        existing_now = fodb.find_filled_order_by_distributor_category_season(
            conn, user_id, distributor_id, category, season,
        )
        if existing_now and not confirm_replace:
            return _duplicate_order_response(
                conn, user_id, distributor_id, category, season, distributor_name_raw,
            )

        if existing_now and confirm_replace:
            fodb.delete_filled_order(conn, user_id, existing_now["id"])

        try:
            order_id = fodb.create_filled_order(
                conn, user_id, distributor_id, distributor_name_raw, category, season,
                source_filename=file.filename,
                quantity_column_used=qty_col_label,
                quantity_unit_used=quantity_unit_used,
                total_lines=len(matched_items),
                matched_lines=matched_count,
                unmatched_lines=unmatched_count,
                flagged_lines=flagged_count,
            )
        except sqlite3.IntegrityError:
            return _duplicate_order_response(
                conn, user_id, distributor_id, category, season, distributor_name_raw,
            )

        for item in matched_items:
            fodb.insert_filled_order_item(conn, order_id, item)

        order = fodb.get_filled_order(conn, user_id, order_id)
        return jsonify({
            "status": "success",
            "filled_order": order,
            "replaced_existing": bool(existing_now and confirm_replace),
        }), 200

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.IntegrityError:
        category = (request.form.get("category") or "").strip()
        season = (request.form.get("season") or "").strip()
        distributor_id = request.form.get("distributor_id", type=int)
        distributor_name_raw = (request.form.get("distributor_name_raw") or "").strip() or "This distributor"
        return _duplicate_order_response(
            conn, user_id, distributor_id, category, season, distributor_name_raw,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
        _cleanup_temp_file(tmp_path)


@filled_orders_bp.route("/preview/add-to-article-master", methods=["POST"])
@require_jwt_auth
def preview_add_to_article_master():
    """Add one preview (pre-save) unmatched line to Article Master."""
    data = request.get_json() or {}
    category = (data.get("category") or "").strip()
    item = data.get("item") or {}
    if not category or not item:
        return jsonify({"error": "category and item are required"}), 400

    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    conn = _get_db_connection()
    try:
        article_data = {
            "category": category,
            "product_type": item.get("product_type"),
            "brand": item.get("brand"),
            "size": item.get("size"),
            "mrp": item.get("mrp"),
            "ptr": item.get("ptr"),
            "ex_mill_price": item.get("ex_mill_price"),
            "bale_pack_size": item.get("bale_size_used"),
            "item_key": item.get("item_key"),
            "extra_attributes": item.get("extra_attributes") or {},
        }
        if not article_data.get("item_key"):
            return jsonify({"error": "item_key missing from preview item"}), 400
        article, created, _changed = amdb.upsert_article(
            conn, user_id, article_data,
            source_filename="filled_order_preview_add",
            workspace_id=workspace_id,
            changed_by=_get_changed_by(),
        )
        conn.commit()
        return jsonify({
            "status": "success",
            "created": created,
            "article": _sanitize_for_json(article),
        }), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@filled_orders_bp.route("/confirm-qty-column", methods=["POST"])
@require_jwt_auth
def confirm_qty_column():
    data = request.get_json() or {}
    user_id = _get_current_user_id()
    distributor_id = data.get("distributor_id")
    category = data.get("category")
    confirmed_column_name = data.get("confirmed_column_name")

    if not distributor_id or not category or not confirmed_column_name:
        return jsonify({
            "error": "distributor_id, category and confirmed_column_name are required",
        }), 400

    conn = _get_db_connection()
    fodb.save_qty_column_pref(conn, user_id, distributor_id, category, confirmed_column_name)
    conn.close()
    return jsonify({"status": "success"}), 200


@filled_orders_bp.route("/<int:filled_order_id>/so-candidates", methods=["GET"])
@require_jwt_auth
def list_so_candidates(filled_order_id):
    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    conn = _get_db_connection()
    order = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not order:
        conn.close()
        return jsonify({"error": "Filled order not found"}), 404
    candidates = fodb.list_so_candidates_for_filled_order(
        conn, user_id, filled_order_id, workspace_id,
    )
    conn.close()
    return jsonify({
        "filled_order": order,
        "candidates": candidates,
        "count": len(candidates),
    }), 200


@filled_orders_bp.route("/<int:filled_order_id>/link-so", methods=["POST"])
@require_jwt_auth
def link_filled_order_to_so(filled_order_id):
    data = request.get_json() or {}
    tracking_id = data.get("tracking_id")
    if not tracking_id:
        return jsonify({"error": "tracking_id is required"}), 400

    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    conn = _get_db_connection()
    order = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not order:
        conn.close()
        return jsonify({"error": "Filled order not found"}), 404

    from centralized_db_system.db import CentralizedDB
    import filled_orders_reconciliation as forecon

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    tracking = db.get_order_lifecycle_tracking(int(tracking_id), workspace_id=workspace_id)
    if not tracking:
        conn.close()
        return jsonify({"error": "Sales order tracking record not found"}), 404
    if order.get("distributor_id") and tracking.get("distributor_id") != order.get("distributor_id"):
        conn.close()
        return jsonify({"error": "Filled order distributor does not match this Sales Order"}), 400

    try:
        item_results = forecon.apply_filled_order_ordered_items(
            db,
            tracking_id=int(tracking_id),
            filled_order_id=filled_order_id,
            workspace_id=workspace_id,
            conn=conn,
        )
        fodb.link_filled_order_to_tracking(conn, filled_order_id, int(tracking_id))
        db.generate_distributor_reconciliation_excel(int(tracking_id), workspace_id=workspace_id)
    except Exception as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 400

    conn.close()
    return jsonify({
        "status": "success",
        "tracking_id": int(tracking_id),
        "filled_order_id": filled_order_id,
        "item_results": _sanitize_for_json(item_results),
    }), 200


@filled_orders_bp.route("/<int:filled_order_id>/resolve-unmatched", methods=["POST"])
@require_jwt_auth
def resolve_unmatched(filled_order_id):
    data = request.get_json() or {}
    item_id = data.get("item_id")
    action = (data.get("action") or "").strip()
    if not item_id or action not in {"skip", "add_to_article_master"}:
        return jsonify({"error": "item_id and action ('skip' or 'add_to_article_master') are required"}), 400

    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    conn = _get_db_connection()
    try:
        result = fodb.resolve_unmatched_item(
            conn, user_id, filled_order_id, item_id, action,
            workspace_id=workspace_id, changed_by=_get_changed_by(),
        )
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 404
    conn.close()
    return jsonify({"status": "success", **_sanitize_for_json(result)}), 200


@filled_orders_bp.route("/list", methods=["GET"])
@require_jwt_auth
def list_filled_orders():
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    orders = fodb.list_filled_orders(
        conn, user_id,
        distributor_id=request.args.get("distributor_id", type=int),
        category=request.args.get("category") or None,
        season=request.args.get("season") or None,
    )
    enriched = []
    for order in orders:
        totals = fodb.summarize_filled_order_totals(conn, int(order["id"]))
        enriched.append({**order, **totals})
    conn.close()
    return jsonify({
        "filled_orders": [_sanitize_for_json(o) for o in enriched],
        "count": len(enriched),
    }), 200


@filled_orders_bp.route("/season-overview", methods=["GET"])
@require_jwt_auth
def filled_orders_season_overview():
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    seasons = fodb.build_season_overview(conn, user_id)
    conn.close()
    return jsonify({
        "seasons": [_sanitize_for_json(s) for s in seasons],
        "count": len(seasons),
    }), 200


@filled_orders_bp.route("/<int:filled_order_id>", methods=["GET"])
@require_jwt_auth
def get_filled_order_detail(filled_order_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    order = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not order:
        conn.close()
        return jsonify({"error": "Filled order not found"}), 404
    items = fodb.get_filled_order_items(conn, filled_order_id)
    conn.close()
    return jsonify({"filled_order": order, "items": items}), 200


@filled_orders_bp.route("/<int:filled_order_id>/download", methods=["GET"])
@require_jwt_auth
def download_filled_order(filled_order_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    order = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not order:
        conn.close()
        return jsonify({"error": "Filled order not found"}), 404
    items = fodb.get_filled_order_items(conn, filled_order_id)

    distributor_name = order.get("distributor_name_raw") or "Distributor"
    if order.get("distributor_id"):
        row = conn.execute(
            "SELECT firm_name, name FROM master_distributors WHERE id = ?",
            (order["distributor_id"],),
        ).fetchone()
        if row:
            firm_name = (row[0] or "").strip()
            contact_name = (row[1] or "").strip()
            distributor_name = firm_name or contact_name or distributor_name
    conn.close()

    rows = []
    for it in items:
        rows.append({
            "Brand": it["brand"],
            "Size": it["size"],
            "Product": it["product_type"],
            "MRP": it["mrp"],
            "PTR": it["ptr"],
            "Ex-Mill": it["ex_mill_price"],
            "Bale Size": it["bale_size_used"],
            "Raw Order Value": it["raw_qty_value"],
            "Detected Unit": it["detected_unit"],
            "Final Piece Qty": it["final_piece_qty"],
            "Red Flag": "YES — not a clean bale multiple" if not it["is_clean_bale_multiple"] else "",
            "Matched to Article Master": "Yes" if it["matched"] else "No",
            "Item Key": it["item_key"],
        })
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    df.to_excel(output, index=False, engine="openpyxl")
    output.seek(0)

    safe_distributor = re.sub(r"[^A-Za-z0-9_-]+", "_", distributor_name).strip("_") or "Distributor"
    safe_category = (order["category"] or "Category").replace(" ", "_")
    upload_date = (order.get("created_at") or "")[:10]
    try:
        upload_date_fmt = datetime.fromisoformat(upload_date).strftime("%d-%m-%Y")
    except ValueError:
        upload_date_fmt = datetime.utcnow().strftime("%d-%m-%Y")
    filename = f"{safe_distributor}_{safe_category}_{order['season']}_{upload_date_fmt}.xlsx"

    return send_file(
        output, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@filled_orders_bp.route("/delete-selected", methods=["POST"])
@require_jwt_auth
def delete_selected_filled_orders():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or data.get("filled_order_ids") or []
    if not isinstance(raw_ids, list):
        return jsonify({"error": "ids must be a list"}), 400
    if not raw_ids:
        return jsonify({"error": "No ids provided"}), 400
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        deleted = fodb.delete_filled_orders_by_ids(conn, user_id, raw_ids)
    finally:
        conn.close()
    return jsonify({"status": "success", "deleted": deleted}), 200


@filled_orders_bp.route("/<int:filled_order_id>", methods=["DELETE"])
@require_jwt_auth
def delete_filled_order_route(filled_order_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        fodb.delete_filled_order(conn, user_id, filled_order_id)
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 404
    conn.close()
    return jsonify({"status": "success", "deleted_id": filled_order_id}), 200


@filled_orders_bp.route("/<int:filled_order_id>/items/<int:item_id>", methods=["PATCH", "DELETE"])
@require_jwt_auth
def filled_order_item_route(filled_order_id, item_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    if request.method == "PATCH":
        updates = request.get_json(silent=True) or {}
        try:
            item = fodb.update_filled_order_item(conn, user_id, filled_order_id, item_id, updates)
        except ValueError as exc:
            conn.close()
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            conn.close()
            current_app.logger.exception("update_filled_order_item failed")
            return jsonify({"error": str(exc)}), 500
        order = fodb.get_filled_order(conn, user_id, filled_order_id)
        conn.close()
        return jsonify({"status": "success", "item": item, "filled_order": order}), 200

    try:
        fodb.delete_filled_order_item(conn, user_id, filled_order_id, item_id)
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 404
    conn.close()
    return jsonify({"status": "success", "deleted_item_id": item_id}), 200
