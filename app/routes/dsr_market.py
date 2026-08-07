"""DSR Market Visit — form data + Excel export matching field DSR format."""

from __future__ import annotations

import io
import sqlite3
from calendar import month_name
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.routes.auth import get_workspace_id, require_jwt_auth, require_role

dsr_market_bp = Blueprint("dsr_market", __name__, url_prefix="/api/v1/dsr-market")

HEADERS = [
    "Sr. No.",
    "Date",
    "Day",
    "Name of Customer",
    "ContactNos.",
    "MBO / ARS",
    "Type (A / B / C)",
    "Complete Address",
    "City and Area",
    "Existing OR New",
    "Order Recd. in Lacs",
    "BED",
    "BATH",
    "TOB",
    "OTHERS",
    "Other Competitor Brands available in store",
    "Branding in Store -  Y / N",
    "Feed Back from Retailer",
    "Remarks from SM",
]


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dsr_market_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            visit_date TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact_nos TEXT,
            channel_type TEXT,
            customer_type TEXT,
            address TEXT,
            city_area TEXT,
            existing_or_new TEXT,
            order_lacs REAL,
            bed TEXT,
            bath TEXT,
            tob TEXT,
            others TEXT,
            competitor_brands TEXT,
            branding_yn TEXT,
            retailer_feedback TEXT,
            sm_remarks TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsr_market_ws_date "
        "ON dsr_market_visits(workspace_id, visit_date)"
    )


def _current_user() -> dict:
    return getattr(request, "user", None) or {}


def _user_id() -> int | None:
    raw = _current_user().get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


@dsr_market_bp.route("/visits", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def create_visit():
    workspace_id = get_workspace_id()
    user = _current_user()
    data = request.get_json(silent=True) or {}

    customer_name = (data.get("customer_name") or "").strip()
    visit_date = (data.get("visit_date") or "").strip()
    if not customer_name or not visit_date:
        return jsonify(
            {"success": False, "error": {"message": "customer_name and visit_date are required"}}
        ), 400

    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        cur = conn.execute(
            """
            INSERT INTO dsr_market_visits (
                workspace_id, user_id, username, visit_date, customer_name, contact_nos,
                channel_type, customer_type, address, city_area, existing_or_new,
                order_lacs, bed, bath, tob, others, competitor_brands, branding_yn,
                retailer_feedback, sm_remarks, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                _user_id(),
                user.get("username"),
                visit_date,
                customer_name,
                (data.get("contact_nos") or "").strip() or None,
                (data.get("channel_type") or "").strip() or None,
                (data.get("customer_type") or "").strip() or None,
                (data.get("address") or "").strip() or None,
                (data.get("city_area") or "").strip() or None,
                (data.get("existing_or_new") or "").strip() or None,
                data.get("order_lacs"),
                (data.get("bed") or "").strip() or None,
                (data.get("bath") or "").strip() or None,
                (data.get("tob") or "").strip() or None,
                (data.get("others") or "").strip() or None,
                (data.get("competitor_brands") or "").strip() or None,
                (data.get("branding_yn") or "").strip() or None,
                (data.get("retailer_feedback") or "").strip() or None,
                (data.get("sm_remarks") or "").strip() or None,
                created_at,
            ),
        )
        visit_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute("SELECT * FROM dsr_market_visits WHERE id = ?", (visit_id,)).fetchone()

    return jsonify({"success": True, "data": _row_to_dict(row)}), 201


@dsr_market_bp.route("/visits", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_visits():
    workspace_id = get_workspace_id()
    from_date = (request.args.get("from") or request.args.get("from_date") or "").strip()
    to_date = (request.args.get("to") or request.args.get("to_date") or "").strip()
    visit_date = (request.args.get("date") or "").strip()

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        query = "SELECT * FROM dsr_market_visits WHERE workspace_id = ?"
        params: list = [workspace_id]
        if visit_date:
            query += " AND visit_date = ?"
            params.append(visit_date)
        else:
            if from_date:
                query += " AND visit_date >= ?"
                params.append(from_date)
            if to_date:
                query += " AND visit_date <= ?"
                params.append(to_date)
        uid = _user_id()
        # BD sees own visits; admin can pass all=1
        if request.args.get("all") != "1" and uid is not None:
            query += " AND (user_id = ? OR user_id IS NULL)"
            params.append(uid)
        query += " ORDER BY visit_date DESC, id DESC LIMIT ?"
        params.append(request.args.get("limit", 500, type=int) or 500)
        rows = conn.execute(query, tuple(params)).fetchall()

    return jsonify({"success": True, "data": [_row_to_dict(r) for r in rows], "count": len(rows)})


def _build_excel(rows: list[dict], sm_name: str, period_label: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "DSR"

    ws["S1"] = "All Remarks received from Retailers"
    ws["B2"] = "Name of the SM :"
    ws["D2"] = sm_name or ""
    ws["H2"] = f"DSR Report from  {period_label}"

    for col, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=5, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    # Group by date so first row of a day gets the date (like sample sheet)
    last_date = None
    sr = 0
    excel_row = 6
    for item in rows:
        sr += 1
        visit_date = item.get("visit_date") or ""
        day_name = ""
        date_display = ""
        try:
            dt = datetime.strptime(visit_date[:10], "%Y-%m-%d")
            day_name = dt.strftime("%A")
            date_display = dt.strftime("%d-%b-%Y")
        except ValueError:
            date_display = visit_date

        show_date = visit_date != last_date
        last_date = visit_date

        values = [
            sr,
            date_display if show_date else "",
            day_name if show_date else "",
            item.get("customer_name") or "",
            item.get("contact_nos") or "",
            item.get("channel_type") or "",
            item.get("customer_type") or "",
            item.get("address") or "",
            item.get("city_area") or "",
            item.get("existing_or_new") or "",
            item.get("order_lacs") if item.get("order_lacs") is not None else "",
            item.get("bed") or "",
            item.get("bath") or "",
            item.get("tob") or "",
            item.get("others") or "",
            item.get("competitor_brands") or "",
            item.get("branding_yn") or "",
            item.get("retailer_feedback") or "",
            item.get("sm_remarks") or "",
        ]
        for col, val in enumerate(values, start=1):
            ws.cell(row=excel_row, column=col, value=val)
        excel_row += 1

    from openpyxl.utils import get_column_letter

    widths = [8, 12, 12, 28, 14, 10, 12, 28, 16, 12, 12, 8, 8, 8, 10, 28, 12, 32, 32]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@dsr_market_bp.route("/export", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def export_excel():
    workspace_id = get_workspace_id()
    from_date = (request.args.get("from") or request.args.get("from_date") or "").strip()
    to_date = (request.args.get("to") or request.args.get("to_date") or "").strip()
    if not from_date or not to_date:
        return jsonify(
            {"success": False, "error": {"message": "from and to dates are required (YYYY-MM-DD)"}}
        ), 400

    user = _current_user()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        query = (
            "SELECT * FROM dsr_market_visits WHERE workspace_id = ? "
            "AND visit_date >= ? AND visit_date <= ?"
        )
        params: list = [workspace_id, from_date, to_date]
        uid = _user_id()
        if request.args.get("all") != "1" and uid is not None:
            query += " AND (user_id = ? OR user_id IS NULL)"
            params.append(uid)
        query += " ORDER BY visit_date ASC, id ASC"
        rows = [dict(r) for r in conn.execute(query, tuple(params)).fetchall()]

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        period = f"{month_name[start.month]} {start.year}"
    except ValueError:
        period = f"{from_date} to {to_date}"

    sm_name = user.get("username") or ""
    if rows and rows[0].get("username"):
        sm_name = rows[0]["username"] or sm_name

    content = _build_excel(rows, sm_name=sm_name, period_label=period)
    filename = f"DSR_{from_date}_to_{to_date}.xlsx"
    return send_file(
        io.BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
