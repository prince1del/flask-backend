"""
Article Master — Flask Routes (per-user isolation)

Each logged-in user sees and edits only their own catalog.
"""

import io
import os
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from flask import Blueprint, current_app, jsonify, request, send_file

import article_master_db as amdb
import article_master_parser as amparser
from app.routes.auth import get_workspace_id, require_jwt_auth

article_master_bp = Blueprint("article_master", __name__, url_prefix="/api/v1/article-master")

DEFAULT_KEY_FIELDS = ["brand", "size"]

# Exact original booking-form column layout per category (order + names as
# they appear in the source Excel). Used by /download so a category-specific
# export looks like the file it was uploaded from, not a generic flat table.
# Verified against the real files: Order_sheet_AW26.xlsx (Bed),
# AW-26_TOB_Revised_Booking_Sheet (TOB), Pillow_Booking_Sheet (TOB Pillow),
# AW-26_Towel_Phase-2_Booking_Sheet (Bath).
ORIGINAL_TEMPLATES = {
    "Bed": [
        "Brand", "TC", "Size", "Units", "BS Size", "Pillow Size", "Pillow Stitching Style",
        "Product", "Print Style", "Bale Size", "Color", "Aug - Sep Delivery",
        "Sep - Oct Delivery", "No of Design", "Qnty Per Color", "Qnty pre Design", "MRP",
        "Perceived", "Selling Price", "PTR", "Retailer Margin", "AWD Mark up on Exmill",
        "ExMill Price", "Qnty",
    ],
    "TOB": [
        "Product", "Brand", "Size", "Quality", "Ply", "Print/Dyed/Weave",
        "Dyed / Printed Option", "Print Colorways", "Weight in gram", "Bale Pack Size",
        "MOQ Per Design / Color", "MRP", "PTR", "Ex-Mill", "Retail Mark down", "AWD MD",
        "Booking Qnty", "Delivery Months (No. of Bales)",
    ],
    "Bath": [
        "SL NO", "Product", "Brand", "Shade", "Description", "Size", "Bale Pack Sizes",
        "AWD MU", "Retailer MD", "MRP", "PTR", "Ex-Mill Per Pcs", "Qty in Bales",
        "Delivery Date",
    ],
    "TOB Pillow": [
        "Product", "Brand", "Size", "Quality", "Unit", "Print/Dyed/Weave", "Option",
        "Weight in gram", "Bale Pack Size (No. of Bales)", "MOQ Per Design/Color", "EX-Mill",
        "MRP", "PTR", "Retail Mark down", "AWD MD", "AWDs order in no of Bales",
    ],
}


def _resolve_export_value(article, column_name):
    """
    For a given original-template column name, find its value on an article:
    core fields (brand/size/mrp/...) resolve via the same alias/keyword rules
    the parser uses, everything else is looked up in extra_attributes (which
    stores every non-core source column under its original stripped header).
    """
    core_field = amparser.resolve_core_field_for_name(column_name)
    if core_field:
        return article.get(core_field)
    stripped = column_name.strip()
    extra = article.get("extra_attributes") or {}
    if stripped in extra:
        return extra[stripped]
    for k, v in extra.items():
        if k.strip().lower() == stripped.lower():
            return v
    return None


def _sanitize_for_json(value):
    """Convert pandas/numpy scalars so jsonify never crashes on upload results."""
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
    amdb.ensure_schema(conn)
    return conn


def _price_change_meta(existing_val, upload_val):
    try:
        old = float(existing_val)
        new = float(upload_val)
        delta = new - old
        if abs(delta) < 0.005:
            return {"direction": "same", "delta": 0, "pct": 0}
        pct = (delta / old * 100) if old else None
        return {
            "direction": "increase" if delta > 0 else "decrease",
            "delta": round(delta, 2),
            "pct": round(pct, 1) if pct is not None else None,
        }
    except (TypeError, ValueError):
        return {"direction": "changed", "delta": None, "pct": None}


def _build_price_revision_summary(price_comparisons):
    parts = []
    increases = 0
    decreases = 0
    for row in price_comparisons:
        if row.get("status") != "mismatch":
            continue
        meta = row.get("change") or {}
        field = str(row.get("field", "")).upper().replace("_", "-")
        direction = meta.get("direction")
        if direction == "increase":
            increases += 1
            pct = meta.get("pct")
            parts.append(f"{field} ↑" + (f" {pct}%" if pct is not None else ""))
        elif direction == "decrease":
            decreases += 1
            pct = meta.get("pct")
            parts.append(f"{field} ↓" + (f" {pct}%" if pct is not None else ""))
    if not parts:
        return "Seasonal price revision detected."
    trend = "increase" if increases >= decreases else "decrease"
    return f"Seasonal price {trend}: " + ", ".join(parts)


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


def _build_upload_conflict_payload(upload_index, article, classification, key_fields):
    existing = classification["existing"]
    core_fields = amdb._article_core_fields(article)
    extra = article.get("extra_attributes") or {}

    field_comparisons = []
    for field in key_fields:
        file_val = amparser.extract_key_field_value(field, core_fields, extra)
        cand_core = amdb._article_core_fields(existing)
        cand_extra = existing.get("extra_attributes") or {}
        master_val = amparser.extract_key_field_value(field, cand_core, cand_extra)
        field_l = field.lower()
        if field_l == "brand":
            if amparser.brands_match_fuzzy(file_val, master_val):
                status = "match"
            elif not file_val and not master_val:
                status = "both_empty"
            else:
                status = "mismatch"
        else:
            n_file = amparser.normalize_key_part_value(field, file_val)
            n_master = amparser.normalize_key_part_value(field, master_val)
            if n_file and n_master:
                status = "match" if n_file == n_master else "mismatch"
            elif not n_file and not n_master:
                status = "both_empty"
            elif not n_file:
                status = "missing_in_file"
            else:
                status = "missing_in_master"
        field_comparisons.append({
            "field": field,
            "upload_value": file_val if file_val not in (None, "") else None,
            "existing_value": master_val if master_val not in (None, "") else None,
            "status": status,
        })

    price_comparisons = []
    for field in amdb.TRACKED_PRICE_FIELDS:
        upload_val = article.get(field)
        existing_val = existing.get(field)
        change = _price_change_meta(existing_val, upload_val)
        price_comparisons.append({
            "field": field,
            "upload_value": upload_val,
            "existing_value": existing_val,
            "status": "match" if amdb._values_equal(field, existing_val, upload_val) else "mismatch",
            "change": change,
        })

    price_diffs = classification.get("price_diffs") or []
    key_differs = existing["item_key"] != article["item_key"]
    revision_summary = _build_price_revision_summary(price_comparisons)
    parts = []
    if classification.get("duplicate_ids"):
        parts.append(f"{len(classification['duplicate_ids'])} duplicate row(s) in Article Master (e.g. Blumen/Bluman)")
    if price_diffs:
        parts.append("price revision on " + ", ".join(price_diffs))
    if key_differs:
        parts.append("item key differs (brand spelling normalized on replace)")
    if classification.get("conflict_reason") == "duplicate_entries_in_master" and not price_diffs:
        issue_summary = (
            "Duplicate entries for the same product in Article Master. "
            "Replace will keep one row and remove duplicates."
        )
    elif price_diffs:
        issue_summary = revision_summary
    else:
        issue_summary = "; ".join(parts) if parts else "Conflict with existing article"

    return {
        "upload_index": upload_index,
        "upload_item_key": article["item_key"],
        "existing_id": existing["id"],
        "existing_item_key": existing["item_key"],
        "brand": article.get("brand"),
        "size": article.get("size"),
        "category": article["category"],
        "product_type": article.get("product_type"),
        "price_diffs": price_diffs,
        "conflict_reason": classification.get("conflict_reason"),
        "can_create_new": key_differs,
        "field_comparisons": field_comparisons,
        "price_comparisons": price_comparisons,
        "issue_summary": issue_summary,
        "recommended_action": "replace",
        "duplicate_ids": classification.get("duplicate_ids") or [],
    }


def _build_upload_success_message(created, updated, skipped):
    if created > 0:
        suffix = f" ({updated} updated)" if updated else ""
        return f"Successfully added {created} item(s){suffix}."
    if updated > 0:
        return f"Successfully updated {updated} item(s)."
    if skipped > 0:
        return "0 items updated — items already available in Article Master."
    return "Upload complete — no changes made."


@article_master_bp.route("/upload", methods=["POST"])
@require_jwt_auth
def upload_article_sheet():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    # confirmed_category:
    #   "" / missing  → preview only (no save)
    #   "AUTO"        → save with per-row category detection (mixed sheets OK)
    #   "Bed"/etc     → force EVERY row into that one category
    confirmed_category = (request.form.get("confirmed_category") or "").strip()
    conn = _get_db_connection()

    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    file.save(tmp_path)

    try:
        with pd.ExcelFile(tmp_path) as xl:
            sheet_name = xl.sheet_names[0]

        categories = amdb.get_all_categories(conn, user_id)
        key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}

        force = None
        if confirmed_category and confirmed_category.upper() != "AUTO":
            force = confirmed_category

        articles, suggested_category, is_new_category, needs_review, category_breakdown = (
            amparser.parse_article_sheet(
                tmp_path,
                sheet_name,
                key_fields_lookup,
                DEFAULT_KEY_FIELDS,
                forced_category=force,
            )
        )
        articles = amdb.apply_brand_aliases_to_articles(
            conn, user_id, articles, key_fields_lookup, DEFAULT_KEY_FIELDS,
        )

        if not confirmed_category:
            breakdown_text = ", ".join(
                f"{cat}: {count}" for cat, count in sorted(category_breakdown.items())
            )
            return jsonify({
                "status": "confirmation_required",
                "message": (
                    "Each row will be saved under its own category (mixed sheets OK). "
                    "Confirm: AUTO (recommended) or force one category (Bed/Bath/TOB/TOB Pillow)."
                ),
                "detected_category": suggested_category,
                "category_breakdown": category_breakdown,
                "breakdown_text": breakdown_text,
                "suggested_key_fields": DEFAULT_KEY_FIELDS,
                "sample_articles": _sanitize_for_json(articles[:5]),
                "article_count": len(articles),
            }), 200

        if is_new_category:
            unknown = [
                cat for cat in category_breakdown
                if cat not in key_fields_lookup
            ]
            return jsonify({
                "error": (
                    f"{'This category is' if len(unknown) == 1 else 'These categories are'} not configured: "
                    f"{', '.join(unknown)}. Please create them via confirm-new-category first."
                ),
            }), 400

        created, updated, skipped = 0, 0, 0
        created_by_category = {}
        changed_summary = []
        conflicts = []
        changed_by = _get_changed_by()

        import json as json_module
        conflict_resolutions = {}
        raw_resolutions = (request.form.get("conflict_resolutions") or "").strip()
        if raw_resolutions:
            try:
                parsed = json_module.loads(raw_resolutions)
                if isinstance(parsed, dict):
                    conflict_resolutions = {str(k): str(v) for k, v in parsed.items()}
            except json_module.JSONDecodeError:
                return jsonify({"error": "Invalid conflict_resolutions JSON"}), 400

        for idx, article in enumerate(articles):
            cat = article["category"]
            key_fields = key_fields_lookup.get(cat, DEFAULT_KEY_FIELDS)
            classification = amdb.classify_upload_article(conn, user_id, article, key_fields)

            if classification["action"] == "skip":
                skipped += 1
                continue

            if classification["action"] == "create":
                _, was_created, changed_fields = amdb.upsert_article(
                    conn, user_id, article,
                    source_filename=file.filename,
                    workspace_id=workspace_id,
                    changed_by=changed_by,
                )
                if was_created:
                    created += 1
                    created_by_category[cat] = created_by_category.get(cat, 0) + 1
                elif changed_fields:
                    updated += 1
                    changed_summary.append({"item_key": article["item_key"], "changed": changed_fields})
                continue

            resolution = conflict_resolutions.get(str(idx))
            if not resolution:
                conflicts.append(
                    _build_upload_conflict_payload(idx, article, classification, key_fields)
                )
                continue

            existing = classification["existing"]
            if resolution == "replace":
                updated_row, removed_dupes = amdb.replace_article_and_consolidate(
                    conn, user_id, existing["id"], article,
                    key_fields,
                    source_filename=file.filename,
                    workspace_id=workspace_id,
                    changed_by=changed_by,
                )
                updated += 1
                if removed_dupes:
                    changed_summary.append({
                        "item_key": article["item_key"],
                        "changed": ["consolidated_duplicates"],
                        "removed_duplicates": removed_dupes,
                    })
            elif resolution == "create_new":
                if not amdb.get_article_by_item_key(conn, user_id, article["item_key"]):
                    amdb.insert_article(
                        conn, user_id, article,
                        source_filename=file.filename,
                        workspace_id=workspace_id,
                    )
                    created += 1
                    created_by_category[cat] = created_by_category.get(cat, 0) + 1
                else:
                    skipped += 1
            elif resolution == "skip":
                skipped += 1

        if conflicts:
            return jsonify({
                "status": "price_mismatch_confirmation_required",
                "message": (
                    f"{len(conflicts)} item(s) need review — seasonal price changes and/or duplicate rows "
                    f"(e.g. Blumen vs Bluman). Use Replace all to apply new prices and merge duplicates."
                ),
                "conflicts": _sanitize_for_json(conflicts),
                "category": suggested_category if not force else force,
                "category_breakdown": category_breakdown,
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "created_by_category": created_by_category,
                "total_parsed": len(articles) + len(needs_review),
                "needs_manual_review": _sanitize_for_json(needs_review),
            }), 200

        return jsonify({
            "status": "success",
            "message": _build_upload_success_message(created, updated, skipped),
            "category": suggested_category if not force else force,
            "category_breakdown": category_breakdown,
            "created_by_category": created_by_category,
            "total_parsed": len(articles) + len(needs_review),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "changed_details": changed_summary,
            "needs_manual_review": _sanitize_for_json(needs_review),
            "duplicate_groups_remaining": len(
                amdb.find_duplicate_groups(conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS)
            ),
        }), 200

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    finally:
        conn.close()
        _cleanup_temp_file(tmp_path)


@article_master_bp.route("/duplicates", methods=["GET"])
@require_jwt_auth
def list_duplicate_articles():
    try:
        user_id = _get_current_user_id()
        conn = _get_db_connection()
        categories = amdb.get_all_categories(conn, user_id)
        key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}
        amdb.normalize_catalog_brand_names(conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS)
        groups = amdb.find_duplicate_groups(conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS)
        conn.close()
        return jsonify({
            "groups": _sanitize_for_json(groups),
            "group_count": len(groups),
            "duplicate_row_count": sum(len(g["articles"]) for g in groups),
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        current_app.logger.exception("list_duplicate_articles failed")
        return jsonify({"error": str(e)}), 500


@article_master_bp.route("/merge-duplicates", methods=["POST"])
@require_jwt_auth
def merge_duplicate_articles():
    conn = None
    try:
        data = request.get_json() or {}
        merges = data.get("merges")
        auto = data.get("auto", False)
        user_id = _get_current_user_id()
        conn = _get_db_connection()
        changed_by = _get_changed_by()
        categories = amdb.get_all_categories(conn, user_id)
        key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}

        if auto:
            groups = amdb.find_duplicate_groups(conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS)
            merges = []
            for group in groups:
                article_ids = [a["id"] for a in group["articles"]]
                keep_id = group["suggested_keep_id"]
                remove_ids = [aid for aid in article_ids if aid != keep_id]
                merges.append({
                    "keep_id": keep_id,
                    "remove_ids": remove_ids,
                    "price_from_id": group["suggested_price_from_id"],
                })

        if not merges:
            normalized = amdb.normalize_catalog_brand_names(
                conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS,
            )
            conn.close()
            conn = None
            msg = "No duplicate articles found."
            if normalized:
                msg += f" Renamed {normalized} row(s) to canonical brand spelling."
            return jsonify({
                "status": "success",
                "merged_groups": 0,
                "removed_rows": 0,
                "brands_normalized": normalized,
                "message": msg,
            }), 200

        merged_groups = 0
        removed_rows = 0
        for spec in merges:
            keep_id = spec.get("keep_id")
            remove_ids = spec.get("remove_ids") or []
            price_from_id = spec.get("price_from_id")
            if not keep_id or not remove_ids:
                continue
            amdb.merge_articles(
                conn, user_id, int(keep_id), remove_ids,
                price_from_id=price_from_id, changed_by=changed_by,
            )
            merged_groups += 1
            removed_rows += len(remove_ids)

        normalized = amdb.normalize_catalog_brand_names(
            conn, user_id, key_fields_lookup, DEFAULT_KEY_FIELDS,
        )
        conn.close()
        conn = None
        msg = f"Merged {merged_groups} duplicate group(s), removed {removed_rows} row(s)."
        if normalized:
            msg += f" Renamed {normalized} row(s) to canonical brand spelling."
        return jsonify({
            "status": "success",
            "merged_groups": merged_groups,
            "removed_rows": removed_rows,
            "brands_normalized": normalized,
            "message": msg,
        }), 200
    except ValueError as e:
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        if conn:
            conn.close()
        current_app.logger.exception("merge_duplicate_articles failed")
        return jsonify({"error": str(e)}), 500


@article_master_bp.route("/<int:article_id>", methods=["DELETE"])
@require_jwt_auth
def delete_one_article(article_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        amdb.delete_article(conn, user_id, article_id)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 404
    conn.close()
    return jsonify({"status": "success", "deleted_id": article_id}), 200


@article_master_bp.route("/delete-all", methods=["POST"])
@require_jwt_auth
def delete_all_user_articles():
    data = request.get_json(silent=True) or {}
    category = (data.get("category") or request.args.get("category") or "All").strip() or "All"
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    deleted = amdb.delete_all_articles(conn, user_id, category=category)
    conn.close()
    return jsonify({
        "status": "success",
        "deleted": deleted,
        "category": category,
    }), 200


@article_master_bp.route("/confirm-new-category", methods=["POST"])
@require_jwt_auth
def confirm_new_category():
    data = request.get_json() or {}
    user_id = _get_current_user_id()
    workspace_id = get_workspace_id()
    conn = _get_db_connection()

    category_name = data.get("category_name")
    key_fields = data.get("key_fields", DEFAULT_KEY_FIELDS)

    if not category_name:
        conn.close()
        return jsonify({"error": "category_name is required"}), 400

    category = amdb.create_category(
        conn, user_id, category_name, key_fields,
        is_confirmed=True, workspace_id=workspace_id,
    )
    conn.close()
    return jsonify({"status": "success", "category": category}), 200


@article_master_bp.route("/list", methods=["GET"])
@require_jwt_auth
def list_articles():
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    amdb.ensure_default_brand_aliases(conn, user_id)
    category = request.args.get("category")

    if category and category != "All":
        articles = amdb.get_articles_by_category(conn, user_id, category)
    else:
        articles = amdb.get_all_articles(conn, user_id)

    conn.close()
    return jsonify({"articles": articles, "count": len(articles)}), 200


@article_master_bp.route("/brand-aliases", methods=["GET"])
@require_jwt_auth
def list_brand_aliases():
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    amdb.ensure_default_brand_aliases(conn, user_id)
    aliases = amdb.list_brand_aliases(conn, user_id)
    conn.close()
    return jsonify({"aliases": aliases}), 200


@article_master_bp.route("/brand-aliases", methods=["POST"])
@require_jwt_auth
def create_brand_alias():
    data = request.get_json() or {}
    alias = data.get("alias")
    canonical_brand = data.get("canonical_brand")
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        amdb.upsert_brand_alias(conn, user_id, alias, canonical_brand)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return jsonify({"status": "success"}), 200


@article_master_bp.route("/<int:article_id>/edit", methods=["PATCH"])
@require_jwt_auth
def edit_article(article_id):
    data = request.get_json() or {}
    field = data.get("field")
    new_value = data.get("value")

    if not field or new_value is None:
        return jsonify({"error": "field and value are required"}), 400

    user_id = _get_current_user_id()
    conn = _get_db_connection()

    try:
        updated = amdb.manual_edit_article(
            conn, user_id, article_id, field, new_value,
            changed_by=_get_changed_by(),
        )
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

    conn.close()
    return jsonify({"status": "success", "article": updated}), 200


@article_master_bp.route("/<int:article_id>/edit-full", methods=["PATCH"])
@require_jwt_auth
def edit_article_full(article_id):
    data = request.get_json() or {}
    updates = data.get("updates") or {}
    if not isinstance(updates, dict) or not updates:
        return jsonify({"error": "updates object with at least one field is required"}), 400

    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        cols = ", ".join(amdb.ARTICLE_MASTER_COLUMNS)
        row = conn.execute(
            f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
            (article_id, user_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Article not found"}), 404
        article = amdb._row_to_article_dict(row)
        categories = amdb.get_all_categories(conn, user_id)
        key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}
        key_fields = key_fields_lookup.get(article["category"], DEFAULT_KEY_FIELDS)

        updated, changed_fields = amdb.update_article_full(
            conn, user_id, article_id, updates, key_fields,
            changed_by=_get_changed_by(),
        )
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

    conn.close()
    return jsonify({
        "status": "success",
        "article": updated,
        "changed_fields": changed_fields,
    }), 200


@article_master_bp.route("/<int:article_id>/price-history", methods=["GET"])
@require_jwt_auth
def price_history(article_id):
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    try:
        history = amdb.get_price_history(conn, article_id, user_id)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 404
    conn.close()
    return jsonify({"history": history}), 200


@article_master_bp.route("/download", methods=["GET"])
@require_jwt_auth
def download_articles():
    """
    Category-wise (or 'All') export.

    For a specific category (Bed/Bath/TOB/TOB Pillow), the export exactly
    matches that category's ORIGINAL booking-form layout (same column names,
    same order) via ORIGINAL_TEMPLATES - only columns relevant to that
    category appear, nothing from other categories leaks in.

    For 'All' (mixed categories, no single original layout applies), falls
    back to the generic flat export: core fields + every extra_attributes
    key that appears anywhere in the selection.
    """
    user_id = _get_current_user_id()
    conn = _get_db_connection()
    category = request.args.get("category", "All")

    if category != "All":
        articles = amdb.get_articles_by_category(conn, user_id, category)
    else:
        articles = amdb.get_all_articles(conn, user_id)

    conn.close()

    if not articles:
        return jsonify({"error": "No articles found for this selection"}), 404

    if category in ORIGINAL_TEMPLATES:
        columns = ORIGINAL_TEMPLATES[category]
        rows = [
            {col: _resolve_export_value(a, col) for col in columns}
            for a in articles
        ]
        df = pd.DataFrame(rows, columns=columns)
    else:
        rows = []
        for a in articles:
            row = {
                "Category": a["category"], "Product": a["product_type"], "Brand": a["brand"],
                "Size": a["size"], "MRP": a["mrp"], "PTR": a["ptr"], "Ex-Mill": a["ex_mill_price"],
                "Bale Pack Size": a["bale_pack_size"], "Season": a["season_tag"],
            }
            row.update(a["extra_attributes"] or {})
            rows.append(row)
        df = pd.DataFrame(rows).dropna(axis=1, how="all")

    output = io.BytesIO()
    df.to_excel(output, index=False, engine="openpyxl")
    output.seek(0)

    filename = f"Article_Master_{category.replace(' ', '_')}.xlsx"
    return send_file(
        output, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
