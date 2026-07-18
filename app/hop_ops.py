"""House of Prizm — extended CRUD, updates, project hub, report aggregates."""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import datetime, timezone, timedelta
from typing import Any

from app.hop_db import (
    _now,
    create_project,
    get_customer,
    get_lead,
    get_project,
    list_customers,
    list_leads,
    list_meetings,
    list_projects,
)
from app.hop_schema import LEAD_STAGES, PROJECT_STAGES


def _f(val, default=0.0):
    if val in (None, ""):
        return default
    return float(val)


def _i(val):
    if val in (None, ""):
        return None
    return int(val)


def _s(val):
    if val is None:
        return None
    t = str(val).strip()
    return t or None


def log_activity(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    activity_type: str,
    title: str,
    project_id: int | None = None,
    customer_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: str | None = None,
    created_by: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO hop_activities (
            workspace_id, project_id, customer_id, entity_type, entity_id,
            activity_type, title, detail, activity_at, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            customer_id,
            entity_type,
            entity_id,
            activity_type,
            title,
            detail,
            now,
            created_by,
            now,
        ),
    )


def update_project(conn: sqlite3.Connection, workspace_id: str, project_id: int, payload: dict) -> dict:
    existing = get_project(conn, workspace_id, project_id)
    if not existing:
        raise ValueError("project not found")
    fields = {
        "project_name": _s(payload.get("project_name")) or existing["project_name"],
        "customer_id": _i(payload["customer_id"]) if "customer_id" in payload else existing.get("customer_id"),
        "client_name": _s(payload.get("client_name")) if "client_name" in payload else existing.get("client_name"),
        "consultant": _s(payload.get("consultant")) if "consultant" in payload else existing.get("consultant"),
        "architect": _s(payload.get("architect")) if "architect" in payload else existing.get("architect"),
        "stage": _s(payload.get("stage")) or existing.get("stage") or "lead",
        "expected_value": _f(payload.get("expected_value"), existing.get("expected_value") or 0)
        if "expected_value" in payload
        else (existing.get("expected_value") or 0),
        "project_value": _f(payload.get("project_value"), existing.get("project_value") or 0)
        if "project_value" in payload
        else (existing.get("project_value") or 0),
        "probability_pct": _f(payload.get("probability_pct"), existing.get("probability_pct") or 0)
        if "probability_pct" in payload
        else (existing.get("probability_pct") or 0),
        "completion_pct": _f(payload.get("completion_pct"), existing.get("completion_pct") or 0)
        if "completion_pct" in payload
        else (existing.get("completion_pct") or 0),
        "delay_days": int(payload.get("delay_days") or existing.get("delay_days") or 0)
        if "delay_days" in payload
        else int(existing.get("delay_days") or 0),
        "issues": _s(payload.get("issues")) if "issues" in payload else existing.get("issues"),
        "next_milestone": _s(payload.get("next_milestone"))
        if "next_milestone" in payload
        else existing.get("next_milestone"),
        "assigned_to": _s(payload.get("assigned_to")) if "assigned_to" in payload else existing.get("assigned_to"),
        "hotel_name": _s(payload.get("hotel_name")) if "hotel_name" in payload else existing.get("hotel_name"),
        "site_address": _s(payload.get("site_address")) if "site_address" in payload else existing.get("site_address"),
        "status": _s(payload.get("status")) or existing.get("status") or "open",
        "notes": _s(payload.get("notes")) if "notes" in payload else existing.get("notes"),
    }
    if fields["customer_id"] is not None and not get_customer(conn, workspace_id, int(fields["customer_id"])):
        raise ValueError("customer_id not found")
    now = _now()
    conn.execute(
        """
        UPDATE hop_projects SET
            project_name=?, customer_id=?, client_name=?, consultant=?, architect=?,
            stage=?, expected_value=?, project_value=?, probability_pct=?, completion_pct=?,
            delay_days=?, issues=?, next_milestone=?, assigned_to=?, hotel_name=?,
            site_address=?, status=?, notes=?, updated_at=?
        WHERE workspace_id=? AND id=?
        """,
        (
            fields["project_name"],
            fields["customer_id"],
            fields["client_name"],
            fields["consultant"],
            fields["architect"],
            fields["stage"],
            fields["expected_value"],
            fields["project_value"],
            fields["probability_pct"],
            fields["completion_pct"],
            fields["delay_days"],
            fields["issues"],
            fields["next_milestone"],
            fields["assigned_to"],
            fields["hotel_name"],
            fields["site_address"],
            fields["status"],
            fields["notes"],
            now,
            workspace_id,
            project_id,
        ),
    )
    if fields["stage"] != existing.get("stage"):
        log_activity(
            conn,
            workspace_id,
            activity_type="stage_change",
            title=f"Stage → {fields['stage']}",
            project_id=project_id,
            customer_id=fields["customer_id"],
            entity_type="project",
            entity_id=project_id,
            detail=f"From {existing.get('stage')} to {fields['stage']}",
        )
    conn.commit()
    return get_project(conn, workspace_id, project_id) or {}


def update_lead(conn: sqlite3.Connection, workspace_id: str, lead_id: int, payload: dict) -> dict:
    existing = get_lead(conn, workspace_id, lead_id)
    if not existing:
        raise ValueError("lead not found")
    stage = _s(payload.get("stage")) or existing.get("stage")
    now = _now()
    won_at = existing.get("won_at")
    lost_at = existing.get("lost_at")
    if stage == "order_won" and not won_at:
        won_at = now
    if stage == "lost" and not lost_at:
        lost_at = now
    conn.execute(
        """
        UPDATE hop_leads SET
            source=?, assigned_to=?, priority=?, expected_value=?, probability_pct=?,
            stage=?, next_follow_up=?, discussion=?, competitor=?, expected_budget=?,
            expected_closure_date=?, products_interested=?, status=?, updated_at=?,
            won_at=?, lost_at=?
        WHERE workspace_id=? AND id=?
        """,
        (
            _s(payload.get("source")) if "source" in payload else existing.get("source"),
            _s(payload.get("assigned_to")) if "assigned_to" in payload else existing.get("assigned_to"),
            _s(payload.get("priority")) if "priority" in payload else existing.get("priority"),
            _f(payload.get("expected_value"), existing.get("expected_value") or 0)
            if "expected_value" in payload
            else (existing.get("expected_value") or 0),
            _f(payload.get("probability_pct"), existing.get("probability_pct") or 0)
            if "probability_pct" in payload
            else (existing.get("probability_pct") or 0),
            stage,
            _s(payload.get("next_follow_up")) if "next_follow_up" in payload else existing.get("next_follow_up"),
            _s(payload.get("discussion")) if "discussion" in payload else existing.get("discussion"),
            _s(payload.get("competitor")) if "competitor" in payload else existing.get("competitor"),
            (
                _f(payload["expected_budget"])
                if payload.get("expected_budget") not in (None, "")
                else None
            )
            if "expected_budget" in payload
            else existing.get("expected_budget"),
            _s(payload.get("expected_closure_date"))
            if "expected_closure_date" in payload
            else existing.get("expected_closure_date"),
            _s(payload.get("products_interested"))
            if "products_interested" in payload
            else existing.get("products_interested"),
            _s(payload.get("status")) or existing.get("status") or "open",
            now,
            won_at,
            lost_at,
            workspace_id,
            lead_id,
        ),
    )
    # Sync project stage when lead moves
    if existing.get("project_id") and stage != existing.get("stage"):
        stage_map = {
            "new_lead": "lead",
            "contacted": "lead",
            "meeting_scheduled": "meeting",
            "samples_sent": "sample",
            "boq_received": "boq",
            "quotation_sent": "quotation",
            "negotiation": "negotiation",
            "po_expected": "po",
            "order_won": "po",
            "lost": "lost",
        }
        mapped = stage_map.get(stage or "", "lead")
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            (mapped, now, workspace_id, existing["project_id"]),
        )
        log_activity(
            conn,
            workspace_id,
            activity_type="lead_stage",
            title=f"Lead stage → {stage}",
            project_id=existing.get("project_id"),
            customer_id=existing.get("customer_id"),
            entity_type="lead",
            entity_id=lead_id,
        )
    conn.commit()
    return get_lead(conn, workspace_id, lead_id) or {}


# ----- Quotations -----
def next_quote_no(conn: sqlite3.Connection, workspace_id: str) -> str:
    year = datetime.now(timezone.utc).year
    row = conn.execute(
        "SELECT COUNT(*) FROM hop_quotations WHERE workspace_id=? AND quote_no LIKE ?",
        (workspace_id, f"Q-{year}-%"),
    ).fetchone()
    return f"Q-{year}-{int(row[0] or 0) + 1:04d}"


def list_quotations(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT qt.*, c.company AS customer_company, p.project_name
        FROM hop_quotations qt
        LEFT JOIN hop_customers c ON c.id = qt.customer_id
        LEFT JOIN hop_projects p ON p.id = qt.project_id
        WHERE qt.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND qt.project_id = ?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (qt.quote_no LIKE ? OR c.company LIKE ? OR p.project_name LIKE ? OR qt.status LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY qt.updated_at DESC, qt.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_quotation(conn: sqlite3.Connection, workspace_id: str, quote_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT qt.*, c.company AS customer_company, p.project_name
        FROM hop_quotations qt
        LEFT JOIN hop_customers c ON c.id = qt.customer_id
        LEFT JOIN hop_projects p ON p.id = qt.project_id
        WHERE qt.workspace_id=? AND qt.id=?
        """,
        (workspace_id, quote_id),
    ).fetchone()
    return dict(row) if row else None


def create_quotation(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    project_id = _i(payload.get("project_id"))
    customer_id = _i(payload.get("customer_id"))
    if project_id and not get_project(conn, workspace_id, project_id):
        raise ValueError("project_id not found")
    if customer_id and not get_customer(conn, workspace_id, customer_id):
        raise ValueError("customer_id not found")
    if project_id and not customer_id:
        customer_id = (get_project(conn, workspace_id, project_id) or {}).get("customer_id")
    parent_id = _i(payload.get("parent_quote_id"))
    version = 1
    quote_no = _s(payload.get("quote_no"))
    if parent_id:
        parent = get_quotation(conn, workspace_id, parent_id)
        if not parent:
            raise ValueError("parent_quote_id not found")
        version = int(parent.get("version") or 1) + 1
        quote_no = parent.get("quote_no") or quote_no
        project_id = project_id or parent.get("project_id")
        customer_id = customer_id or parent.get("customer_id")
    quote_no = quote_no or next_quote_no(conn, workspace_id)
    cur = conn.execute(
        """
        INSERT INTO hop_quotations (
            workspace_id, project_id, quote_no, customer_id, quote_date, value, margin_pct,
            status, last_follow_up, expected_closure_date, version, terms, payment_terms,
            delivery_terms, warranty, sales_person, notes, parent_quote_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            quote_no,
            customer_id,
            _s(payload.get("quote_date")) or now[:10],
            _f(payload.get("value")),
            _f(payload["margin_pct"]) if payload.get("margin_pct") not in (None, "") else None,
            _s(payload.get("status")) or "draft",
            _s(payload.get("last_follow_up")),
            _s(payload.get("expected_closure_date")),
            version,
            _s(payload.get("terms")),
            _s(payload.get("payment_terms")),
            _s(payload.get("delivery_terms")),
            _s(payload.get("warranty")),
            _s(payload.get("sales_person")),
            _s(payload.get("notes")),
            parent_id,
            now,
            now,
        ),
    )
    qid = int(cur.lastrowid)
    log_activity(
        conn,
        workspace_id,
        activity_type="quotation",
        title=f"Quotation {quote_no} v{version}",
        project_id=project_id,
        customer_id=customer_id,
        entity_type="quotation",
        entity_id=qid,
        detail=f"Status {_s(payload.get('status')) or 'draft'} · value {_f(payload.get('value'))}",
    )
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=? AND lower(stage) IN ('lead','meeting','requirement','sample','boq','vendor')",
            ("quotation", now, workspace_id, project_id),
        )
    conn.commit()
    return get_quotation(conn, workspace_id, qid) or {}


def update_quotation(conn: sqlite3.Connection, workspace_id: str, quote_id: int, payload: dict) -> dict:
    existing = get_quotation(conn, workspace_id, quote_id)
    if not existing:
        raise ValueError("quotation not found")
    now = _now()
    conn.execute(
        """
        UPDATE hop_quotations SET
            value=?, margin_pct=?, status=?, last_follow_up=?, expected_closure_date=?,
            terms=?, payment_terms=?, delivery_terms=?, warranty=?, sales_person=?, notes=?, updated_at=?
        WHERE workspace_id=? AND id=?
        """,
        (
            _f(payload.get("value"), existing.get("value") or 0) if "value" in payload else (existing.get("value") or 0),
            _f(payload["margin_pct"]) if payload.get("margin_pct") not in (None, "") else existing.get("margin_pct")
            if "margin_pct" in payload
            else existing.get("margin_pct"),
            _s(payload.get("status")) or existing.get("status"),
            _s(payload.get("last_follow_up")) if "last_follow_up" in payload else existing.get("last_follow_up"),
            _s(payload.get("expected_closure_date"))
            if "expected_closure_date" in payload
            else existing.get("expected_closure_date"),
            _s(payload.get("terms")) if "terms" in payload else existing.get("terms"),
            _s(payload.get("payment_terms")) if "payment_terms" in payload else existing.get("payment_terms"),
            _s(payload.get("delivery_terms")) if "delivery_terms" in payload else existing.get("delivery_terms"),
            _s(payload.get("warranty")) if "warranty" in payload else existing.get("warranty"),
            _s(payload.get("sales_person")) if "sales_person" in payload else existing.get("sales_person"),
            _s(payload.get("notes")) if "notes" in payload else existing.get("notes"),
            now,
            workspace_id,
            quote_id,
        ),
    )
    conn.commit()
    return get_quotation(conn, workspace_id, quote_id) or {}


# ----- Vendors -----
def list_vendors(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = "SELECT * FROM hop_vendors WHERE workspace_id=?"
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (company LIKE ? OR products LIKE ? OR contact_person LIKE ? OR city LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY updated_at DESC, id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_vendor(conn: sqlite3.Connection, workspace_id: str, vendor_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM hop_vendors WHERE workspace_id=? AND id=?",
        (workspace_id, vendor_id),
    ).fetchone()
    return dict(row) if row else None


def create_vendor(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    company = _s(payload.get("company"))
    if not company:
        raise ValueError("company is required")
    cur = conn.execute(
        """
        INSERT INTO hop_vendors (
            workspace_id, company, products, gst_no, contact_person, mobile, email,
            rating, payment_terms, lead_time_days, certificates, quality_rating,
            on_time_pct, price_notes, city, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            company,
            _s(payload.get("products")),
            _s(payload.get("gst_no")),
            _s(payload.get("contact_person")),
            _s(payload.get("mobile")),
            _s(payload.get("email")),
            _f(payload["rating"]) if payload.get("rating") not in (None, "") else None,
            _s(payload.get("payment_terms")),
            int(payload["lead_time_days"]) if payload.get("lead_time_days") not in (None, "") else None,
            _s(payload.get("certificates")),
            _f(payload["quality_rating"]) if payload.get("quality_rating") not in (None, "") else None,
            _f(payload["on_time_pct"]) if payload.get("on_time_pct") not in (None, "") else None,
            _s(payload.get("price_notes")),
            _s(payload.get("city")),
            _s(payload.get("status")) or "active",
            now,
            now,
        ),
    )
    conn.commit()
    return get_vendor(conn, workspace_id, int(cur.lastrowid)) or {}


def list_vendor_comparisons(conn: sqlite3.Connection, workspace_id: str, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT vc.*, v.company AS vendor_company, p.project_name
        FROM hop_vendor_comparisons vc
        LEFT JOIN hop_vendors v ON v.id = vc.vendor_id
        LEFT JOIN hop_projects p ON p.id = vc.project_id
        WHERE vc.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND vc.project_id=?"
        params.append(project_id)
    sql += " ORDER BY vc.updated_at DESC, vc.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_vendor_comparison(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    project_id = _i(payload.get("project_id"))
    vendor_id = _i(payload.get("vendor_id"))
    if not project_id:
        raise ValueError("project_id is required")
    if not vendor_id:
        raise ValueError("vendor_id is required")
    if not get_project(conn, workspace_id, project_id):
        raise ValueError("project_id not found")
    if not get_vendor(conn, workspace_id, vendor_id):
        raise ValueError("vendor_id not found")
    is_winner = 1 if payload.get("is_winner") in (True, 1, "1", "true", "yes") else 0
    if is_winner:
        conn.execute(
            "UPDATE hop_vendor_comparisons SET is_winner=0, updated_at=? WHERE workspace_id=? AND project_id=? AND product_name=?",
            (now, workspace_id, project_id, _s(payload.get("product_name")) or ""),
        )
    cur = conn.execute(
        """
        INSERT INTO hop_vendor_comparisons (
            workspace_id, project_id, product_name, vendor_id, rate, lead_time_days,
            moq, quality_note, certification, payment_terms, is_winner, recommendation,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            _s(payload.get("product_name")) or "Product",
            vendor_id,
            _f(payload.get("rate")),
            int(payload["lead_time_days"]) if payload.get("lead_time_days") not in (None, "") else None,
            _s(payload.get("moq")),
            _s(payload.get("quality_note")),
            _s(payload.get("certification")),
            _s(payload.get("payment_terms")),
            is_winner,
            _s(payload.get("recommendation")),
            now,
            now,
        ),
    )
    conn.commit()
    cid = int(cur.lastrowid)
    row = conn.execute(
        """
        SELECT vc.*, v.company AS vendor_company, p.project_name
        FROM hop_vendor_comparisons vc
        LEFT JOIN hop_vendors v ON v.id = vc.vendor_id
        LEFT JOIN hop_projects p ON p.id = vc.project_id
        WHERE vc.id=?
        """,
        (cid,),
    ).fetchone()
    return dict(row) if row else {"id": cid}


# ----- Multi-supplier rate sheets -----
def list_rate_sheets(conn: sqlite3.Connection, workspace_id: str, status: str = "active") -> list[dict]:
    sql = """
        SELECT s.*, v.company AS vendor_company,
               (SELECT COUNT(*) FROM hop_rate_lines l WHERE l.sheet_id=s.id) AS line_count
        FROM hop_rate_sheets s
        LEFT JOIN hop_vendors v ON v.id = s.vendor_id
        WHERE s.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if status:
        sql += " AND s.status=?"
        params.append(status)
    sql += " ORDER BY s.updated_at DESC, s.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_rate_sheet(conn: sqlite3.Connection, workspace_id: str, sheet_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT s.*, v.company AS vendor_company
        FROM hop_rate_sheets s
        LEFT JOIN hop_vendors v ON v.id = s.vendor_id
        WHERE s.workspace_id=? AND s.id=?
        """,
        (workspace_id, sheet_id),
    ).fetchone()
    if not row:
        return None
    sheet = dict(row)
    sheet["lines"] = list_rate_lines(conn, workspace_id, sheet_id)
    return sheet


def list_rate_lines(conn: sqlite3.Connection, workspace_id: str, sheet_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM hop_rate_lines
            WHERE workspace_id=? AND sheet_id=?
            ORDER BY sort_order ASC, id ASC
            """,
            (workspace_id, sheet_id),
        ).fetchall()
    ]


def create_rate_sheet(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    from app.hop_rate_compare import lines_from_structured, landed_rate as _land

    now = _now()
    supplier = _s(payload.get("supplier_name"))
    if not supplier:
        raise ValueError("supplier_name is required")
    vendor_id = _i(payload.get("vendor_id"))
    if vendor_id and not get_vendor(conn, workspace_id, vendor_id):
        raise ValueError("vendor_id not found")

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list):
        raise ValueError("lines must be a list")
    normalized = lines_from_structured(raw_lines)
    # Never create empty sheet headers just because a file was uploaded — that
    # leaves ghost UMD/Bharat columns (₹0 not quoted) after failed OCR.
    allow_empty = bool(payload.get("allow_empty_lines"))
    if not normalized and not allow_empty:
        raise ValueError("at least one valid rate line is required (product_name + rate)")

    cur = conn.execute(
        """
        INSERT INTO hop_rate_sheets (
            workspace_id, vendor_id, supplier_name, title, source_type, quote_date,
            notes, freight_note, payment_terms, validity_days, status,
            source_filename, source_file_path, parse_method, parse_warnings,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            vendor_id,
            supplier,
            _s(payload.get("title")) or f"{supplier} rates",
            _s(payload.get("source_type")) or "manual",
            _s(payload.get("quote_date")),
            _s(payload.get("notes")),
            _s(payload.get("freight_note")),
            _s(payload.get("payment_terms")),
            _i(payload.get("validity_days")),
            _s(payload.get("status")) or "active",
            _s(payload.get("source_filename")),
            _s(payload.get("source_file_path")),
            _s(payload.get("parse_method")),
            _s(payload.get("parse_warnings")),
            now,
            now,
        ),
    )
    sheet_id = int(cur.lastrowid)
    for line in normalized:
        gst = line.get("gst_pct")
        rate = line.get("rate")
        conn.execute(
            """
            INSERT INTO hop_rate_lines (
                workspace_id, sheet_id, product_key, product_name, display_name, category,
                size, brand, quality, rate, gst_pct, landed_rate, qty, uom, notes,
                sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                sheet_id,
                line["product_key"],
                line["product_name"],
                line.get("display_name"),
                line.get("category"),
                line.get("size"),
                line.get("brand"),
                line.get("quality"),
                rate,
                gst,
                line.get("landed_rate") or _land(rate, gst),
                line.get("qty"),
                line.get("uom") or "Pcs",
                line.get("notes"),
                line.get("sort_order") or 0,
                now,
                now,
            ),
        )
    conn.commit()
    return get_rate_sheet(conn, workspace_id, sheet_id) or {"id": sheet_id}


def delete_rate_sheet(conn: sqlite3.Connection, workspace_id: str, sheet_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM hop_rate_sheets WHERE workspace_id=? AND id=?",
        (workspace_id, sheet_id),
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM hop_rate_lines WHERE workspace_id=? AND sheet_id=?", (workspace_id, sheet_id))
    conn.execute("DELETE FROM hop_rate_sheets WHERE workspace_id=? AND id=?", (workspace_id, sheet_id))
    conn.commit()
    return True


def rematch_rate_line_keys(conn: sqlite3.Connection, workspace_id: str | None = None) -> int:
    """Keep stored product_key / size / display fields in sync with live match rules.

    Call on schema boot, matrix load, and before key-based deletes so UI keys never
    diverge from DB (the historical Delete/Clear-all bug).
    """
    from app.hop_rate_compare import (
        classify_product,
        extract_quality_tags,
        extract_size_from_text,
        normalize_size,
        product_match_key,
    )

    sql = """
        SELECT id, workspace_id, product_key, product_name, display_name, category,
               size, quality, brand
        FROM hop_rate_lines
    """
    params: list[Any] = []
    if workspace_id:
        sql += " WHERE workspace_id=?"
        params.append(workspace_id)
    rows = conn.execute(sql, params).fetchall()
    updated = 0
    now = _now()
    for row in rows:
        if isinstance(row, sqlite3.Row):
            rid = int(row["id"])
            name = row["product_name"]
            size = row["size"]
            quality = row["quality"]
            brand = row["brand"]
            stored_key = str(row["product_key"] or "")
            stored_size = row["size"]
            stored_display = row["display_name"]
            stored_cat = row["category"]
        else:
            rid = int(row[0])
            # id, workspace_id, product_key, product_name, display_name, category, size, quality, brand
            stored_key = str(row[2] or "")
            name = row[3]
            stored_display = row[4]
            stored_cat = row[5]
            size, quality, brand = row[6], row[7], row[8]
            stored_size = size

        sz_norm = normalize_size(size) or extract_size_from_text(name, quality) or None
        cat, _label = classify_product(name, quality)
        live_key = product_match_key(name, sz_norm or size, quality, brand)
        display = (name or stored_display or _label or "Item").strip()
        q_hint = quality or extract_quality_tags(name, quality) or None

        changes: dict[str, Any] = {}
        if live_key and live_key != stored_key:
            changes["product_key"] = live_key
        if sz_norm and sz_norm != (normalize_size(stored_size) or stored_size):
            changes["size"] = sz_norm
        if display and display != (stored_display or ""):
            changes["display_name"] = display
        if cat and cat != (stored_cat or ""):
            changes["category"] = cat
        if q_hint and not quality:
            changes["quality"] = q_hint

        if not changes:
            continue
        sets = ", ".join(f"{k}=?" for k in changes)
        conn.execute(
            f"UPDATE hop_rate_lines SET {sets}, updated_at=? WHERE id=?",
            (*changes.values(), now, rid),
        )
        updated += 1
    if updated:
        conn.commit()
    return updated


def sync_all_rate_line_identities(conn: sqlite3.Connection) -> int:
    """Rematch every workspace — used from schema ensure so upgrades heal old rows."""
    return rematch_rate_line_keys(conn, workspace_id=None)


def clear_rate_lines(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    product_keys: list[str] | None = None,
    line_ids: list[int] | None = None,
    sheet_id: int | None = None,
    clear_all: bool = False,
) -> dict:
    """Remove rate lines. Prefer line_ids (stable); product_keys rematch as fallback."""
    from app.hop_rate_compare import product_match_key

    deleted = 0
    if clear_all:
        cur = conn.execute("DELETE FROM hop_rate_lines WHERE workspace_id=?", (workspace_id,))
        deleted = int(cur.rowcount or 0)
        conn.execute("DELETE FROM hop_rate_sheets WHERE workspace_id=?", (workspace_id,))
        conn.commit()
        return {"deleted_lines": deleted, "cleared": "all"}

    if sheet_id is not None and not product_keys and not line_ids:
        cur = conn.execute(
            "DELETE FROM hop_rate_lines WHERE workspace_id=? AND sheet_id=?",
            (workspace_id, int(sheet_id)),
        )
        deleted = int(cur.rowcount or 0)
        conn.execute(
            "DELETE FROM hop_rate_sheets WHERE workspace_id=? AND id=?",
            (workspace_id, int(sheet_id)),
        )
        conn.commit()
        return {"deleted_lines": deleted, "sheet_id": int(sheet_id), "sheet_deleted": True}

    id_set: set[int] = set()
    raw_ids: list[int] = []
    for x in line_ids or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0:
            raw_ids.append(n)
    if raw_ids:
        placeholders = ",".join("?" for _ in raw_ids)
        params: list[Any] = [workspace_id, *raw_ids]
        sql = f"SELECT id FROM hop_rate_lines WHERE workspace_id=? AND id IN ({placeholders})"
        if sheet_id is not None:
            sql += " AND sheet_id=?"
            params.append(int(sheet_id))
        for row in conn.execute(sql, params).fetchall():
            id_set.add(int(row["id"] if isinstance(row, sqlite3.Row) else row[0]))

    keys = [str(k).strip() for k in (product_keys or []) if str(k).strip()]
    if keys:
        rematch_rate_line_keys(conn, workspace_id)
        key_set = set(keys)
        sql = """
            SELECT id, sheet_id, product_key, product_name, size, quality, brand
            FROM hop_rate_lines
            WHERE workspace_id=?
        """
        params = [workspace_id]
        if sheet_id is not None:
            sql += " AND sheet_id=?"
            params.append(int(sheet_id))
        for row in conn.execute(sql, params).fetchall():
            if isinstance(row, sqlite3.Row):
                rid = int(row["id"])
                stored = str(row["product_key"] or "")
                live = product_match_key(row["product_name"], row["size"], row["quality"], row["brand"])
            else:
                rid = int(row[0])
                stored = str(row[2] or "")
                live = product_match_key(row[3], row[4], row[5], row[6])
            if stored in key_set or live in key_set:
                id_set.add(rid)

    if not id_set and not keys and not raw_ids:
        raise ValueError("product_keys or line_ids required (or clear_all / sheet_id)")

    if id_set:
        placeholders = ",".join("?" for _ in id_set)
        cur = conn.execute(
            f"DELETE FROM hop_rate_lines WHERE workspace_id=? AND id IN ({placeholders})",
            [workspace_id, *sorted(id_set)],
        )
        deleted = int(cur.rowcount or 0)
        conn.commit()

    pruned = prune_empty_rate_sheets(conn, workspace_id)
    return {
        "deleted_lines": deleted,
        "product_keys": keys,
        "line_ids": sorted(id_set),
        "sheet_id": sheet_id,
        "pruned_empty_sheets": pruned,
    }


def prune_empty_rate_sheets(conn: sqlite3.Connection, workspace_id: str) -> int:
    """Remove rate sheet headers that have zero lines (ghost vendor columns)."""
    rows = conn.execute(
        """
        SELECT s.id
        FROM hop_rate_sheets s
        LEFT JOIN hop_rate_lines l
          ON l.sheet_id = s.id AND l.workspace_id = s.workspace_id
        WHERE s.workspace_id = ?
        GROUP BY s.id
        HAVING COUNT(l.id) = 0
        """,
        (workspace_id,),
    ).fetchall()
    removed = 0
    for row in rows:
        sid = int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
        if delete_rate_sheet(conn, workspace_id, sid):
            removed += 1
    return removed


def rate_comparison_matrix(conn: sqlite3.Connection, workspace_id: str) -> dict:
    from app.hop_rate_compare import build_comparison_matrix

    rematch_rate_line_keys(conn, workspace_id)
    prune_empty_rate_sheets(conn, workspace_id)
    sheets = list_rate_sheets(conn, workspace_id, status="active")
    packed = []
    for sh in sheets:
        full = get_rate_sheet(conn, workspace_id, int(sh["id"]))
        if full:
            packed.append(full)
    return build_comparison_matrix(packed)


def seed_sample_rate_sheets(conn: sqlite3.Connection, workspace_id: str, replace: bool = False) -> dict:
    """Load Ambala / UMD / handwritten / Jalandhar / GSB sample sheets for demos."""
    from app.hop_rate_compare import SAMPLE_SHEETS, parse_gsb_text, parse_jalandhar_text

    if replace:
        ids = [int(r["id"]) for r in list_rate_sheets(conn, workspace_id, status="")]
        # list with empty status still filters — use raw
        ids = [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM hop_rate_sheets WHERE workspace_id=?", (workspace_id,)
            ).fetchall()
        ]
        for sid in ids:
            delete_rate_sheet(conn, workspace_id, sid)

    created = []
    for key, sample in SAMPLE_SHEETS.items():
        existing = conn.execute(
            "SELECT id FROM hop_rate_sheets WHERE workspace_id=? AND supplier_name=?",
            (workspace_id, sample["supplier_name"]),
        ).fetchone()
        if existing and not replace:
            created.append({"supplier_name": sample["supplier_name"], "id": int(existing["id"]), "skipped": True})
            continue
        sheet = create_rate_sheet(
            conn,
            workspace_id,
            {
                "supplier_name": sample["supplier_name"],
                "title": sample.get("title"),
                "source_type": sample.get("source_type") or "sample",
                "notes": sample.get("notes"),
                "payment_terms": sample.get("payment_terms"),
                "lines": sample.get("lines") or [],
            },
        )
        created.append({"supplier_name": sample["supplier_name"], "id": sheet.get("id"), "lines": len(sheet.get("lines") or [])})

    # Jalandhar + GSB from curated parsers
    for supplier, title, source, lines in [
        ("Jalandhar", "Jalandhar product rate list", "pdf", parse_jalandhar_text("")),
        ("GSB ENTERPRISE", "GSB Enterprise invoice / rates", "pdf", parse_gsb_text("")),
    ]:
        existing = conn.execute(
            "SELECT id FROM hop_rate_sheets WHERE workspace_id=? AND supplier_name=?",
            (workspace_id, supplier),
        ).fetchone()
        if existing and not replace:
            created.append({"supplier_name": supplier, "id": int(existing["id"]), "skipped": True})
            continue
        # parse_* already returns normalized lines — wrap as raw for create
        raw = [
            {
                "product_name": ln["product_name"],
                "size": ln.get("size"),
                "quality": ln.get("quality"),
                "brand": ln.get("brand"),
                "rate": ln["rate"],
                "gst_pct": ln.get("gst_pct"),
                "qty": ln.get("qty"),
            }
            for ln in lines
        ]
        sheet = create_rate_sheet(
            conn,
            workspace_id,
            {
                "supplier_name": supplier,
                "title": title,
                "source_type": source,
                "lines": raw,
            },
        )
        created.append({"supplier_name": supplier, "id": sheet.get("id"), "lines": len(sheet.get("lines") or [])})

    return {"created": created, "matrix": rate_comparison_matrix(conn, workspace_id)}


# ----- Samples -----
def list_samples(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT s.*, c.company AS customer_company, p.project_name
        FROM hop_samples s
        LEFT JOIN hop_customers c ON c.id = s.customer_id
        LEFT JOIN hop_projects p ON p.id = s.project_id
        WHERE s.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND s.project_id=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (s.sample_name LIKE ? OR s.tracking_number LIKE ? OR c.company LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY s.updated_at DESC, s.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_sample(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    name = _s(payload.get("sample_name"))
    if not name:
        raise ValueError("sample_name is required")
    project_id = _i(payload.get("project_id"))
    customer_id = _i(payload.get("customer_id"))
    if project_id and not customer_id:
        customer_id = (get_project(conn, workspace_id, project_id) or {}).get("customer_id")
    cur = conn.execute(
        """
        INSERT INTO hop_samples (
            workspace_id, project_id, customer_id, sample_name, sent_at, courier,
            tracking_number, return_status, approval_status, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            customer_id,
            name,
            _s(payload.get("sent_at")) or now[:10],
            _s(payload.get("courier")),
            _s(payload.get("tracking_number")),
            _s(payload.get("return_status")),
            _s(payload.get("approval_status")) or "pending",
            _s(payload.get("notes")),
            now,
            now,
        ),
    )
    sid = int(cur.lastrowid)
    log_activity(
        conn,
        workspace_id,
        activity_type="sample",
        title=f"Sample: {name}",
        project_id=project_id,
        customer_id=customer_id,
        entity_type="sample",
        entity_id=sid,
    )
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=? AND lower(stage) IN ('lead','meeting','requirement')",
            ("sample", now, workspace_id, project_id),
        )
    conn.commit()
    rows = list_samples(conn, workspace_id, project_id=project_id)
    return next((r for r in rows if r["id"] == sid), {"id": sid})


# ----- Products -----
def list_products(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = """
        SELECT pr.*, v.company AS vendor_company
        FROM hop_products pr
        LEFT JOIN hop_vendors v ON v.id = pr.vendor_id
        WHERE pr.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (pr.name LIKE ? OR pr.code LIKE ? OR pr.brand LIKE ? OR pr.category LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY pr.updated_at DESC, pr.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        sell = float(r.get("selling_price") or 0)
        buy = float(r.get("purchase_price") or 0)
        logi = float(r.get("logistics_cost") or 0)
        gst = float(r.get("gst_pct") or 0)
        comm = float(r.get("commission_pct") or 0)
        net = sell - buy - logi - (sell * gst / 100.0) - (sell * comm / 100.0)
        r["net_profit"] = round(net, 2)
        r["margin_pct"] = round((net / sell * 100.0), 2) if sell else None
    return rows


def create_product(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    name = _s(payload.get("name"))
    if not name:
        raise ValueError("name is required")
    vendor_id = _i(payload.get("vendor_id"))
    cur = conn.execute(
        """
        INSERT INTO hop_products (
            workspace_id, code, name, brand, category, collection, selling_price,
            purchase_price, logistics_cost, gst_pct, commission_pct, moq, lead_time_days,
            vendor_id, stock_qty, specs, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            _s(payload.get("code")),
            name,
            _s(payload.get("brand")),
            _s(payload.get("category")),
            _s(payload.get("collection")),
            _f(payload.get("selling_price")),
            _f(payload.get("purchase_price")),
            _f(payload.get("logistics_cost")),
            _f(payload.get("gst_pct")),
            _f(payload.get("commission_pct")),
            _s(payload.get("moq")),
            int(payload["lead_time_days"]) if payload.get("lead_time_days") not in (None, "") else None,
            vendor_id,
            _f(payload.get("stock_qty")),
            _s(payload.get("specs")),
            _s(payload.get("status")) or "active",
            now,
            now,
        ),
    )
    conn.commit()
    pid = int(cur.lastrowid)
    return next((r for r in list_products(conn, workspace_id) if r["id"] == pid), {"id": pid})


# ----- Orders -----
def list_orders(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT o.*, p.project_name, c.company AS customer_company, v.company AS vendor_company
        FROM hop_orders o
        LEFT JOIN hop_projects p ON p.id = o.project_id
        LEFT JOIN hop_customers c ON c.id = o.customer_id
        LEFT JOIN hop_vendors v ON v.id = o.vendor_id
        WHERE o.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND o.project_id=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (o.po_number LIKE ? OR o.client_name LIKE ? OR o.supplier LIKE ? OR p.project_name LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY o.updated_at DESC, o.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_order(conn: sqlite3.Connection, workspace_id: str, order_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT o.*, p.project_name, c.company AS customer_company, v.company AS vendor_company
        FROM hop_orders o
        LEFT JOIN hop_projects p ON p.id = o.project_id
        LEFT JOIN hop_customers c ON c.id = o.customer_id
        LEFT JOIN hop_vendors v ON v.id = o.vendor_id
        WHERE o.workspace_id=? AND o.id=?
        """,
        (workspace_id, order_id),
    ).fetchone()
    return dict(row) if row else None


def create_order(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    project_id = _i(payload.get("project_id"))
    customer_id = _i(payload.get("customer_id"))
    vendor_id = _i(payload.get("vendor_id"))
    if project_id and not get_project(conn, workspace_id, project_id):
        raise ValueError("project_id not found")
    if project_id and not customer_id:
        proj = get_project(conn, workspace_id, project_id) or {}
        customer_id = proj.get("customer_id")
    won = now if payload.get("mark_won") in (True, 1, "1", "true", "yes") else _s(payload.get("won_at"))
    cur = conn.execute(
        """
        INSERT INTO hop_orders (
            workspace_id, project_id, po_number, client_name, order_value, supplier,
            expected_delivery, production_status, dispatch_status, invoice_status,
            won_at, lost_at, customer_id, vendor_id, order_type, status, notes,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            _s(payload.get("po_number")),
            _s(payload.get("client_name")),
            _f(payload.get("order_value")),
            _s(payload.get("supplier")),
            _s(payload.get("expected_delivery")),
            _s(payload.get("production_status")) or "pending",
            _s(payload.get("dispatch_status")) or "pending",
            _s(payload.get("invoice_status")) or "pending",
            won,
            _s(payload.get("lost_at")),
            customer_id,
            vendor_id,
            _s(payload.get("order_type")) or "customer_po",
            _s(payload.get("status")) or "open",
            _s(payload.get("notes")),
            now,
            now,
        ),
    )
    oid = int(cur.lastrowid)
    log_activity(
        conn,
        workspace_id,
        activity_type="order",
        title=f"Order { _s(payload.get('po_number')) or oid }",
        project_id=project_id,
        customer_id=customer_id,
        entity_type="order",
        entity_id=oid,
    )
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("po", now, workspace_id, project_id),
        )
    conn.commit()
    return get_order(conn, workspace_id, oid) or {}


def update_order(conn: sqlite3.Connection, workspace_id: str, order_id: int, payload: dict) -> dict:
    existing = get_order(conn, workspace_id, order_id)
    if not existing:
        raise ValueError("order not found")
    now = _now()
    conn.execute(
        """
        UPDATE hop_orders SET
            production_status=?, dispatch_status=?, invoice_status=?, expected_delivery=?,
            order_value=?, status=?, notes=?, updated_at=?
        WHERE workspace_id=? AND id=?
        """,
        (
            _s(payload.get("production_status")) or existing.get("production_status"),
            _s(payload.get("dispatch_status")) or existing.get("dispatch_status"),
            _s(payload.get("invoice_status")) or existing.get("invoice_status"),
            _s(payload.get("expected_delivery")) if "expected_delivery" in payload else existing.get("expected_delivery"),
            _f(payload.get("order_value"), existing.get("order_value") or 0)
            if "order_value" in payload
            else (existing.get("order_value") or 0),
            _s(payload.get("status")) or existing.get("status"),
            _s(payload.get("notes")) if "notes" in payload else existing.get("notes"),
            now,
            workspace_id,
            order_id,
        ),
    )
    prod = (_s(payload.get("production_status")) or existing.get("production_status") or "").lower()
    if existing.get("project_id") and prod in ("in_production", "production", "qc", "packed"):
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("production", now, workspace_id, existing["project_id"]),
        )
    conn.commit()
    return get_order(conn, workspace_id, order_id) or {}


# ----- Dispatches -----
def list_dispatches(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT d.*, p.project_name, o.po_number
        FROM hop_dispatches d
        LEFT JOIN hop_projects p ON p.id = d.project_id
        LEFT JOIN hop_orders o ON o.id = d.order_id
        WHERE d.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND d.project_id=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (d.tracking_number LIKE ? OR d.courier LIKE ? OR d.docket_number LIKE ? OR p.project_name LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY d.updated_at DESC, d.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_dispatch(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    project_id = _i(payload.get("project_id"))
    order_id = _i(payload.get("order_id"))
    if order_id and not project_id:
        project_id = (get_order(conn, workspace_id, order_id) or {}).get("project_id")
    cur = conn.execute(
        """
        INSERT INTO hop_dispatches (
            workspace_id, order_id, project_id, status, tracking_number, courier,
            delivery_status, dispatched_at, delivered_at, installation_pending, due_date,
            invoice_no_link, eway_bill, docket_number, pod_received, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            order_id,
            project_id,
            _s(payload.get("status")) or "ready",
            _s(payload.get("tracking_number")),
            _s(payload.get("courier")),
            _s(payload.get("delivery_status")) or "pending",
            _s(payload.get("dispatched_at")),
            _s(payload.get("delivered_at")),
            1 if payload.get("installation_pending") in (True, 1, "1", "true") else 0,
            _s(payload.get("due_date")),
            _s(payload.get("invoice_no_link")),
            _s(payload.get("eway_bill")),
            _s(payload.get("docket_number")),
            1 if payload.get("pod_received") in (True, 1, "1", "true") else 0,
            _s(payload.get("notes")),
            now,
            now,
        ),
    )
    did = int(cur.lastrowid)
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("dispatch", now, workspace_id, project_id),
        )
    log_activity(
        conn,
        workspace_id,
        activity_type="dispatch",
        title=f"Dispatch {_s(payload.get('tracking_number')) or did}",
        project_id=project_id,
        entity_type="dispatch",
        entity_id=did,
    )
    conn.commit()
    return next((r for r in list_dispatches(conn, workspace_id) if r["id"] == did), {"id": did})


# ----- Invoices & Payments -----
def next_invoice_no(conn: sqlite3.Connection, workspace_id: str) -> str:
    year = datetime.now(timezone.utc).year
    row = conn.execute(
        "SELECT COUNT(*) FROM hop_invoices WHERE workspace_id=? AND invoice_no LIKE ?",
        (workspace_id, f"INV-{year}-%"),
    ).fetchone()
    return f"INV-{year}-{int(row[0] or 0) + 1:04d}"


def list_invoices(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT i.*, c.company AS customer_company, p.project_name
        FROM hop_invoices i
        LEFT JOIN hop_customers c ON c.id = i.customer_id
        LEFT JOIN hop_projects p ON p.id = i.project_id
        WHERE i.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND i.project_id=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (i.invoice_no LIKE ? OR c.company LIKE ? OR p.project_name LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY i.updated_at DESC, i.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_invoice(conn: sqlite3.Connection, workspace_id: str, invoice_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT i.*, c.company AS customer_company, p.project_name
        FROM hop_invoices i
        LEFT JOIN hop_customers c ON c.id = i.customer_id
        LEFT JOIN hop_projects p ON p.id = i.project_id
        WHERE i.workspace_id=? AND i.id=?
        """,
        (workspace_id, invoice_id),
    ).fetchone()
    return dict(row) if row else None


def create_invoice(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    amount = _f(payload.get("amount"))
    paid = _f(payload.get("paid_amount"))
    balance = amount - paid
    project_id = _i(payload.get("project_id"))
    customer_id = _i(payload.get("customer_id"))
    order_id = _i(payload.get("order_id"))
    if project_id and not customer_id:
        customer_id = (get_project(conn, workspace_id, project_id) or {}).get("customer_id")
    inv_no = _s(payload.get("invoice_no")) or next_invoice_no(conn, workspace_id)
    cur = conn.execute(
        """
        INSERT INTO hop_invoices (
            workspace_id, project_id, order_id, invoice_no, customer_id, amount, due_date,
            paid_amount, balance, status, invoice_date, gst_amount, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            order_id,
            inv_no,
            customer_id,
            amount,
            _s(payload.get("due_date")),
            paid,
            balance,
            _s(payload.get("status")) or ("paid" if balance <= 0 else "open"),
            _s(payload.get("invoice_date")) or now[:10],
            _f(payload.get("gst_amount")),
            _s(payload.get("notes")),
            now,
            now,
        ),
    )
    iid = int(cur.lastrowid)
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("invoice", now, workspace_id, project_id),
        )
    log_activity(
        conn,
        workspace_id,
        activity_type="invoice",
        title=f"Invoice {inv_no}",
        project_id=project_id,
        customer_id=customer_id,
        entity_type="invoice",
        entity_id=iid,
    )
    conn.commit()
    return get_invoice(conn, workspace_id, iid) or {}


def list_payments(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT pay.*, i.invoice_no, c.company AS customer_company, p.project_name
        FROM hop_payments pay
        LEFT JOIN hop_invoices i ON i.id = pay.invoice_id
        LEFT JOIN hop_customers c ON c.id = COALESCE(pay.customer_id, i.customer_id)
        LEFT JOIN hop_projects p ON p.id = COALESCE(pay.project_id, i.project_id)
        WHERE pay.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND COALESCE(pay.project_id, i.project_id)=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (i.invoice_no LIKE ? OR c.company LIKE ? OR pay.method LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY pay.paid_at DESC, pay.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_payment(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    invoice_id = _i(payload.get("invoice_id"))
    amount = _f(payload.get("amount"))
    if amount <= 0:
        raise ValueError("amount must be > 0")
    if not invoice_id:
        raise ValueError("invoice_id is required")
    inv = get_invoice(conn, workspace_id, invoice_id)
    if not inv:
        raise ValueError("invoice not found")
    paid_at = _s(payload.get("paid_at")) or now
    customer_id = _i(payload.get("customer_id")) or inv.get("customer_id")
    project_id = _i(payload.get("project_id")) or inv.get("project_id")
    cur = conn.execute(
        """
        INSERT INTO hop_payments (
            workspace_id, invoice_id, amount, paid_at, method, notes, created_at,
            customer_id, project_id, reminder_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            invoice_id,
            amount,
            paid_at,
            _s(payload.get("method")),
            _s(payload.get("notes")),
            now,
            customer_id,
            project_id,
            _s(payload.get("reminder_at")),
        ),
    )
    new_paid = float(inv.get("paid_amount") or 0) + amount
    new_balance = max(0.0, float(inv.get("amount") or 0) - new_paid)
    status = "paid" if new_balance <= 0 else "partial"
    conn.execute(
        "UPDATE hop_invoices SET paid_amount=?, balance=?, status=?, updated_at=? WHERE id=? AND workspace_id=?",
        (new_paid, new_balance, status, now, invoice_id, workspace_id),
    )
    if project_id and new_balance <= 0:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("payment", now, workspace_id, project_id),
        )
    log_activity(
        conn,
        workspace_id,
        activity_type="payment",
        title=f"Payment {amount}",
        project_id=project_id,
        customer_id=customer_id,
        entity_type="payment",
        entity_id=int(cur.lastrowid),
    )
    conn.commit()
    return next((r for r in list_payments(conn, workspace_id) if r["id"] == int(cur.lastrowid)), {"id": int(cur.lastrowid)})


# ----- Complaints -----
def list_complaints(conn: sqlite3.Connection, workspace_id: str, q: str | None = None, project_id: int | None = None) -> list[dict]:
    sql = """
        SELECT cp.*, c.company AS customer_company, p.project_name
        FROM hop_complaints cp
        LEFT JOIN hop_customers c ON c.id = cp.customer_id
        LEFT JOIN hop_projects p ON p.id = cp.project_id
        WHERE cp.workspace_id=?
    """
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND cp.project_id=?"
        params.append(project_id)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (cp.issue LIKE ? OR c.company LIKE ? OR cp.status LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY cp.updated_at DESC, cp.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_complaint(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    issue = _s(payload.get("issue"))
    if not issue:
        raise ValueError("issue is required")
    project_id = _i(payload.get("project_id"))
    customer_id = _i(payload.get("customer_id"))
    if project_id and not customer_id:
        customer_id = (get_project(conn, workspace_id, project_id) or {}).get("customer_id")
    cur = conn.execute(
        """
        INSERT INTO hop_complaints (
            workspace_id, project_id, customer_id, complaint_date, issue, assigned_to,
            status, resolution_time_hours, feedback, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            customer_id,
            _s(payload.get("complaint_date")) or now[:10],
            issue,
            _s(payload.get("assigned_to")),
            _s(payload.get("status")) or "open",
            _f(payload["resolution_time_hours"]) if payload.get("resolution_time_hours") not in (None, "") else None,
            _s(payload.get("feedback")),
            now,
            now,
        ),
    )
    if project_id:
        conn.execute(
            "UPDATE hop_projects SET stage=?, updated_at=? WHERE workspace_id=? AND id=?",
            ("after_sales", now, workspace_id, project_id),
        )
    conn.commit()
    cid = int(cur.lastrowid)
    return next((r for r in list_complaints(conn, workspace_id) if r["id"] == cid), {"id": cid})


def list_activities(conn: sqlite3.Connection, workspace_id: str, project_id: int | None = None, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM hop_activities WHERE workspace_id=?"
    params: list[Any] = [workspace_id]
    if project_id:
        sql += " AND project_id=?"
        params.append(project_id)
    sql += " ORDER BY activity_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def upsert_target(conn: sqlite3.Connection, workspace_id: str, period_label: str, target_amount: float) -> dict:
    now = _now()
    conn.execute(
        """
        INSERT INTO hop_targets (workspace_id, period_label, target_amount, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, period_label) DO UPDATE SET
            target_amount=excluded.target_amount, updated_at=excluded.updated_at
        """,
        (workspace_id, period_label, float(target_amount), now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM hop_targets WHERE workspace_id=? AND period_label=?",
        (workspace_id, period_label),
    ).fetchone()
    return dict(row) if row else {}


# ----- Project hub -----
def get_project_hub(conn: sqlite3.Connection, workspace_id: str, project_id: int) -> dict | None:
    project = get_project(conn, workspace_id, project_id)
    if not project:
        return None
    return {
        "project": project,
        "customer": get_customer(conn, workspace_id, int(project["customer_id"])) if project.get("customer_id") else None,
        "leads": [r for r in list_leads(conn, workspace_id) if r.get("project_id") == project_id],
        "meetings": [r for r in list_meetings(conn, workspace_id) if r.get("project_id") == project_id],
        "quotations": list_quotations(conn, workspace_id, project_id=project_id),
        "samples": list_samples(conn, workspace_id, project_id=project_id),
        "vendor_comparisons": list_vendor_comparisons(conn, workspace_id, project_id=project_id),
        "orders": list_orders(conn, workspace_id, project_id=project_id),
        "dispatches": list_dispatches(conn, workspace_id, project_id=project_id),
        "invoices": list_invoices(conn, workspace_id, project_id=project_id),
        "payments": list_payments(conn, workspace_id, project_id=project_id),
        "complaints": list_complaints(conn, workspace_id, project_id=project_id),
        "timeline": list_activities(conn, workspace_id, project_id=project_id, limit=80),
        "funnel_stages": PROJECT_STAGES,
    }


# ----- Reports -----
def report_lead_pipeline(conn: sqlite3.Connection, workspace_id: str) -> dict:
    rows = []
    total_count = 0
    total_value = 0.0
    won = 0
    lost = 0
    for stage in LEAD_STAGES:
        r = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(expected_value),0)
            FROM hop_leads WHERE workspace_id=? AND lower(stage)=?
            """,
            (workspace_id, stage),
        ).fetchone()
        count = int(r[0] or 0)
        value = float(r[1] or 0)
        total_count += count
        total_value += value
        if stage == "order_won":
            won = count
        if stage == "lost":
            lost = count
        rows.append({"stage": stage, "count": count, "value": value})
    closed = won + lost
    return {
        "stages": rows,
        "kpis": {
            "conversion_rate_pct": round(won / total_count * 100, 1) if total_count else 0,
            "win_ratio_pct": round(won / closed * 100, 1) if closed else 0,
            "average_sales_cycle_days": None,
            "total_open_value": total_value,
            "notes": {
                "average_sales_cycle_days": "Available after won leads have reliable created_at → won_at spans",
            },
        },
    }


def report_funnel(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    out = []
    for stage in PROJECT_STAGES:
        r = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(COALESCE(project_value, expected_value)),0)
            FROM hop_projects WHERE workspace_id=? AND lower(stage)=?
            """,
            (workspace_id, stage),
        ).fetchone()
        out.append({"stage": stage, "count": int(r[0] or 0), "value": float(r[1] or 0)})
    return out


def report_meetings_dashboard(conn: sqlite3.Connection, workspace_id: str) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meetings = list_meetings(conn, workspace_id)
    today_list = [m for m in meetings if (m.get("scheduled_at") or "")[:10] == today and (m.get("status") or "").lower() != "cancelled"]
    upcoming = [m for m in meetings if (m.get("scheduled_at") or "")[:10] > today and (m.get("status") or "").lower() == "scheduled"]
    missed = [
        m
        for m in meetings
        if (m.get("scheduled_at") or "")[:10] < today
        and (m.get("status") or "").lower() == "scheduled"
        and not m.get("outcome")
    ]
    followups = [
        m
        for m in meetings
        if m.get("follow_up_at") and (m.get("follow_up_at") or "")[:10] <= today
    ]
    return {
        "today": today_list,
        "upcoming": upcoming[:50],
        "missed": missed[:50],
        "follow_up_due": followups[:50],
        "counts": {
            "today": len(today_list),
            "upcoming": len(upcoming),
            "missed": len(missed),
            "follow_up_due": len(followups),
        },
    }


def report_receivables(conn: sqlite3.Connection, workspace_id: str) -> dict:
    today = datetime.now(timezone.utc).date()
    invoices = [i for i in list_invoices(conn, workspace_id) if float(i.get("balance") or 0) > 0]
    buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}
    by_customer: dict[str, float] = {}
    for inv in invoices:
        bal = float(inv.get("balance") or 0)
        due = inv.get("due_date") or inv.get("invoice_date") or inv.get("created_at")
        try:
            due_d = datetime.fromisoformat(str(due)[:10]).date()
            age = (today - due_d).days
        except Exception:
            age = 0
        if age <= 30:
            buckets["0_30"] += bal
        elif age <= 60:
            buckets["31_60"] += bal
        elif age <= 90:
            buckets["61_90"] += bal
        else:
            buckets["90_plus"] += bal
        key = inv.get("customer_company") or f"Customer #{inv.get('customer_id')}"
        by_customer[key] = by_customer.get(key, 0) + bal
    top = sorted(({"customer": k, "outstanding": v} for k, v in by_customer.items()), key=lambda x: -x["outstanding"])[:20]
    return {"ageing": buckets, "top_customers": top, "invoices": invoices}


def report_customer_dashboard(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    customers = list_customers(conn, workspace_id)
    projects = list_projects(conn, workspace_id)
    orders = list_orders(conn, workspace_id)
    invoices = list_invoices(conn, workspace_id)
    meetings = list_meetings(conn, workspace_id)
    out = []
    for c in customers:
        cid = c["id"]
        cps = [p for p in projects if p.get("customer_id") == cid]
        cos = [o for o in orders if o.get("customer_id") == cid]
        cins = [i for i in invoices if i.get("customer_id") == cid]
        cms = [m for m in meetings if m.get("customer_id") == cid]
        revenue = sum(float(o.get("order_value") or 0) for o in cos)
        outstanding = sum(float(i.get("balance") or 0) for i in cins)
        last_meeting = max((m.get("scheduled_at") or "" for m in cms), default=None) or None
        last_purchase = max((o.get("won_at") or o.get("created_at") or "" for o in cos), default=None) or None
        out.append(
            {
                "customer_id": cid,
                "company": c.get("company"),
                "city": c.get("city"),
                "potential_rating": c.get("potential_rating"),
                "total_business": revenue,
                "projects": len(cps),
                "average_order_value": round(revenue / len(cos), 2) if cos else 0,
                "outstanding": outstanding,
                "last_meeting": last_meeting,
                "last_purchase": last_purchase,
            }
        )
    return out


def report_daily_activity(conn: sqlite3.Connection, workspace_id: str, day: str | None = None) -> dict:
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meetings = _count_day(conn, "hop_meetings", "scheduled_at", workspace_id, day)
    leads = _count_day(conn, "hop_leads", "created_at", workspace_id, day)
    samples = _count_day(conn, "hop_samples", "sent_at", workspace_id, day)
    quotes = _count_day(conn, "hop_quotations", "quote_date", workspace_id, day)
    orders = conn.execute(
        "SELECT COUNT(*) FROM hop_orders WHERE workspace_id=? AND date(won_at)=date(?)",
        (workspace_id, day),
    ).fetchone()
    collections = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM hop_payments WHERE workspace_id=? AND date(paid_at)=date(?)",
        (workspace_id, day),
    ).fetchone()
    followups = conn.execute(
        """
        SELECT COUNT(*) FROM hop_leads
        WHERE workspace_id=? AND date(next_follow_up)=date(?)
        """,
        (workspace_id, day),
    ).fetchone()
    return {
        "day": day,
        "calls": None,
        "meetings": int(meetings),
        "samples_sent": int(samples),
        "follow_ups": int(followups[0] or 0),
        "quotes_sent": int(quotes),
        "leads_created": int(leads),
        "orders_closed": int(orders[0] or 0),
        "collections": float(collections[0] or 0),
        "notes": {"calls": "Call logging not connected yet"},
    }


def _count_day(conn, table, col, workspace_id, day) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id=? AND date({col})=date(?)",
        (workspace_id, day),
    ).fetchone()
    return int(row[0] or 0)


def report_profitability(conn: sqlite3.Connection, workspace_id: str) -> dict:
    revenue = conn.execute(
        "SELECT COALESCE(SUM(order_value),0) FROM hop_orders WHERE workspace_id=? AND won_at IS NOT NULL",
        (workspace_id,),
    ).fetchone()
    rev = float(revenue[0] or 0)
    products = list_products(conn, workspace_id)
    # Approximate COGS from product catalogue margins when order lines don't exist yet
    cogs = sum(float(p.get("purchase_price") or 0) * float(p.get("stock_qty") or 0) for p in products)
    gp = rev - cogs if rev else None
    return {
        "revenue": rev,
        "cogs": cogs,
        "gross_profit": gp if rev else None,
        "expenses": None,
        "net_profit": None,
        "gross_margin_pct": round((gp / rev * 100), 2) if rev and gp is not None else None,
        "notes": {
            "cogs": "COGS uses catalogue purchase×stock until order line items exist",
            "expenses": "Expense ledger not connected",
        },
    }


def report_quotation_kpis(conn: sqlite3.Connection, workspace_id: str) -> dict:
    now = datetime.now(timezone.utc)
    month_start = f"{now.year}-{now.month:02d}-01"
    last = monthrange(now.year, now.month)[1]
    month_end = f"{now.year}-{now.month:02d}-{last:02d}"
    quotes = list_quotations(conn, workspace_id)
    sent_month = [
        q
        for q in quotes
        if (q.get("quote_date") or "")[:10] >= month_start
        and (q.get("quote_date") or "")[:10] <= month_end
        and (q.get("status") or "").lower() in ("sent", "negotiation", "follow_up", "converted")
    ]
    pending = [q for q in quotes if (q.get("status") or "").lower() in ("draft", "pending", "pending_approval")]
    converted = [q for q in quotes if (q.get("status") or "").lower() == "converted"]
    values = [float(q.get("value") or 0) for q in sent_month]
    return {
        "quotes_sent_this_month": len(sent_month),
        "quotes_pending": len(pending),
        "quotes_converted": len(converted),
        "average_quote_value": round(sum(values) / len(values), 2) if values else 0,
        "rows": quotes,
    }


def report_repeat_business(conn: sqlite3.Connection, workspace_id: str) -> dict:
    today = datetime.now(timezone.utc).date()
    customers = report_customer_dashboard(conn, workspace_id)
    buckets = {"30": [], "60": [], "90": [], "180": []}
    for c in customers:
        lp = c.get("last_purchase")
        if not lp:
            buckets["180"].append(c)
            continue
        try:
            d = datetime.fromisoformat(str(lp)[:10]).date()
            days = (today - d).days
        except Exception:
            buckets["180"].append(c)
            continue
        if days >= 180:
            buckets["180"].append(c)
        elif days >= 90:
            buckets["90"].append(c)
        elif days >= 60:
            buckets["60"].append(c)
        elif days >= 30:
            buckets["30"].append(c)
    return buckets


def report_salesperson(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    leads = list_leads(conn, workspace_id)
    meetings = list_meetings(conn, workspace_id)
    quotes = list_quotations(conn, workspace_id)
    orders = list_orders(conn, workspace_id)
    people: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        key = (name or "Unassigned").strip() or "Unassigned"
        if key not in people:
            people[key] = {
                "salesperson": key,
                "leads": 0,
                "meetings": 0,
                "quotes": 0,
                "orders": 0,
                "revenue": 0.0,
                "conversion_pct": 0.0,
            }
        return people[key]

    for l in leads:
        bucket(l.get("assigned_to"))["leads"] += 1
    for m in meetings:
        # meetings don't have salesperson — attribute via project assigned later if needed
        pass
    for q in quotes:
        bucket(q.get("sales_person"))["quotes"] += 1
    for o in orders:
        # client orders — use project assigned
        proj = get_project(conn, workspace_id, int(o["project_id"])) if o.get("project_id") else None
        b = bucket((proj or {}).get("assigned_to"))
        if o.get("won_at"):
            b["orders"] += 1
            b["revenue"] += float(o.get("order_value") or 0)
    for b in people.values():
        b["conversion_pct"] = round(b["orders"] / b["leads"] * 100, 1) if b["leads"] else 0
    return sorted(people.values(), key=lambda x: -x["revenue"])
