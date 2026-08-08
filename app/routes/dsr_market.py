"""DSR Market Visit — form data + Excel export matching field DSR format."""

from __future__ import annotations

import io
import json
import sqlite3
from calendar import month_name
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.routes.auth import get_workspace_id, require_jwt_auth, require_role

dsr_market_bp = Blueprint("dsr_market", __name__, url_prefix="/api/v1/dsr-market")

DEFAULT_COMPETITOR_BRANDS = [
    "Bombay Dyeing",
    "Ddecor",
    "Portico",
    "Raymonds",
    "Sansar",
    "Spaces",
    "Swayam",
    "Welspun",
]


def _excel_headers(include_owner: bool) -> list[str]:
    headers = [
        "Sr. No.",
        "Date",
        "Day",
        "Name of Customer",
    ]
    if include_owner:
        headers.append("Owner's Name")
    headers.extend(
        [
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
            "Branding in Store - Yes / No",
            "Feed Back from Retailer",
            "Remarks from SM",
        ]
    )
    return headers


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
            location TEXT,
            owner_name TEXT,
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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dsr_market_visits)")}
    if "owner_name" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN owner_name TEXT")
    if "location" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN location TEXT")
    # Full retailer questionnaire (app intelligence). Never exported to HO Excel.
    if "visit_intel_json" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN visit_intel_json TEXT")
    # Draft / day-close / party linkage
    if "is_draft" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0")
    if "draft_party_kind" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN draft_party_kind TEXT")
    if "linked_distributor_id" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN linked_distributor_id INTEGER")
    if "linked_retailer_id" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN linked_retailer_id INTEGER")
    if "area_text" not in cols:
        conn.execute("ALTER TABLE dsr_market_visits ADD COLUMN area_text TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsr_market_ws_date "
        "ON dsr_market_visits(workspace_id, visit_date)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dsr_competitor_brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            brand_key TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(workspace_id, brand_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsr_competitor_ws "
        "ON dsr_competitor_brands(workspace_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dsr_day_closures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            visit_date TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            UNIQUE(workspace_id, user_id, visit_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dsr_day_close "
        "ON dsr_day_closures(workspace_id, user_id, visit_date)"
    )


def _brand_key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _merged_competitor_brands(conn: sqlite3.Connection, workspace_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT brand_name FROM dsr_competitor_brands WHERE workspace_id = ? "
        "ORDER BY brand_name COLLATE NOCASE ASC",
        (workspace_id,),
    ).fetchall()
    custom = [str(r[0]).strip() for r in rows if r and r[0] and str(r[0]).strip()]
    by_key: dict[str, str] = {}
    for name in DEFAULT_COMPETITOR_BRANDS + custom:
        key = _brand_key(name)
        if key and key not in by_key:
            by_key[key] = name.strip()
    return sorted(by_key.values(), key=lambda s: s.lower())


def _current_user() -> dict:
    return getattr(request, "user", None) or {}


def _user_id() -> int | None:
    raw = _current_user().get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "is_draft" in d:
        d["is_draft"] = bool(d.get("is_draft"))
    return d


def _truthy_flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _norm_name(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


def _norm_phone(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _address_tokens(s: str | None) -> set[str]:
    raw = _norm_name(s)
    return {t for t in raw.replace(",", " ").split() if len(t) > 2}


def _address_overlap(a: str | None, b: str | None) -> float:
    ta, tb = _address_tokens(a), _address_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(max(len(ta), len(tb)))


def _open_visit_dates(conn: sqlite3.Connection, workspace_id: str, user_id: int | None) -> list[str]:
    if user_id is None:
        return []
    closed = {
        r[0]
        for r in conn.execute(
            "SELECT visit_date FROM dsr_day_closures WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT DISTINCT visit_date FROM dsr_market_visits "
        "WHERE workspace_id = ? AND user_id = ? ORDER BY visit_date ASC",
        (workspace_id, user_id),
    ).fetchall()
    return [r[0] for r in rows if r[0] and r[0] not in closed]


def _master_db():
    from centralized_db_system.db import CentralizedDB

    return CentralizedDB()


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

    is_draft = 1 if _truthy_flag(str(data.get("is_draft") if data.get("is_draft") is not None else "0")) else 0
    # Also treat explicit draft_party_kind as draft
    draft_party_kind = (data.get("draft_party_kind") or "").strip().lower() or None
    if draft_party_kind in {"retailer", "distributor"}:
        is_draft = 1
    elif draft_party_kind:
        draft_party_kind = None

    linked_distributor_id = data.get("linked_distributor_id")
    linked_retailer_id = data.get("linked_retailer_id")
    try:
        linked_distributor_id = int(linked_distributor_id) if linked_distributor_id not in (None, "") else None
    except (TypeError, ValueError):
        linked_distributor_id = None
    try:
        linked_retailer_id = int(linked_retailer_id) if linked_retailer_id not in (None, "") else None
    except (TypeError, ValueError):
        linked_retailer_id = None

    # Existing party visit is not a draft
    if linked_retailer_id or (linked_distributor_id and draft_party_kind is None and not is_draft):
        if linked_retailer_id:
            is_draft = 0
            draft_party_kind = None

    area_text = (data.get("area_text") or data.get("city_area") or "").strip() or None

    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        uid = _user_id()
        open_dates = _open_visit_dates(conn, workspace_id, uid)
        if visit_date not in open_dates and len(open_dates) >= 2:
            return jsonify(
                {
                    "success": False,
                    "error": {
                        "message": (
                            "Maximum 2 open visit days allowed. "
                            f"Please Day Close one of: {', '.join(open_dates)} before starting {visit_date}."
                        ),
                        "code": "max_open_days",
                        "open_dates": open_dates,
                    },
                }
            ), 400

        # Re-open day if previously closed and user adds another visit
        if uid is not None:
            conn.execute(
                "DELETE FROM dsr_day_closures WHERE workspace_id = ? AND user_id = ? AND visit_date = ?",
                (workspace_id, uid, visit_date),
            )

        intel_raw = data.get("visit_intel_json")
        if isinstance(intel_raw, (dict, list)):
            visit_intel_json = json.dumps(intel_raw, ensure_ascii=False)
        else:
            visit_intel_json = (str(intel_raw).strip() if intel_raw is not None else "") or None

        cur = conn.execute(
            """
            INSERT INTO dsr_market_visits (
                workspace_id, user_id, username, visit_date, customer_name, location, owner_name, contact_nos,
                channel_type, customer_type, address, city_area, existing_or_new,
                order_lacs, bed, bath, tob, others, competitor_brands, branding_yn,
                retailer_feedback, sm_remarks, visit_intel_json,
                is_draft, draft_party_kind, linked_distributor_id, linked_retailer_id, area_text,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                uid,
                user.get("username"),
                visit_date,
                customer_name,
                (data.get("location") or "").strip() or None,
                (data.get("owner_name") or "").strip() or None,
                (data.get("contact_nos") or "").strip() or None,
                (data.get("channel_type") or "").strip() or None,
                (data.get("customer_type") or "").strip() or None,
                (data.get("address") or "").strip() or None,
                (data.get("city_area") or area_text or "").strip() or None,
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
                visit_intel_json,
                is_draft,
                draft_party_kind,
                linked_distributor_id,
                linked_retailer_id,
                area_text,
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


def _build_excel(rows: list[dict], sm_name: str, period_label: str, include_owner: bool) -> bytes:
    """Build DSR Excel. Note: `location` is app-only and must never appear in export."""
    wb = Workbook()
    ws = wb.active
    ws.title = "DSR"
    headers = _excel_headers(include_owner)

    ws["S1"] = "All Remarks received from Retailers"
    ws["B2"] = "Name of the SM :"
    ws["D2"] = sm_name or ""
    ws["H2"] = f"DSR Report from  {period_label}"

    for col, header in enumerate(headers, start=1):
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

        values: list = [
            sr,
            date_display if show_date else "",
            day_name if show_date else "",
            item.get("customer_name") or "",
        ]
        if include_owner:
            values.append(item.get("owner_name") or "")
        values.extend(
            [
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
        )
        for col, val in enumerate(values, start=1):
            ws.cell(row=excel_row, column=col, value=val)
        excel_row += 1

    from openpyxl.utils import get_column_letter

    widths = [8, 12, 12, 28]
    if include_owner:
        widths.append(20)
    widths.extend([14, 10, 12, 28, 16, 12, 12, 8, 8, 8, 10, 28, 12, 32, 32])
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

    include_owner = _truthy_flag(
        request.args.get("include_owner") or request.args.get("includeOwner")
    )

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

    content = _build_excel(
        rows, sm_name=sm_name, period_label=period, include_owner=include_owner
    )
    filename = f"DSR_{from_date}_to_{to_date}.xlsx"
    return send_file(
        io.BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@dsr_market_bp.route("/competitor-brands", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_competitor_brands():
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        brands = _merged_competitor_brands(conn, workspace_id)
    return jsonify(
        {
            "success": True,
            "data": brands,
            "defaults": list(DEFAULT_COMPETITOR_BRANDS),
            "count": len(brands),
        }
    )


@dsr_market_bp.route("/competitor-brands", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def add_competitor_brand():
    workspace_id = get_workspace_id()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or data.get("brand_name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": {"message": "name is required"}}), 400
    key = _brand_key(name)
    if not key:
        return jsonify({"success": False, "error": {"message": "name is required"}}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        existing = conn.execute(
            "SELECT brand_name FROM dsr_competitor_brands "
            "WHERE workspace_id = ? AND brand_key = ?",
            (workspace_id, key),
        ).fetchone()
        if existing is None and key not in {_brand_key(b) for b in DEFAULT_COMPETITOR_BRANDS}:
            conn.execute(
                """
                INSERT INTO dsr_competitor_brands (
                    workspace_id, brand_name, brand_key, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (workspace_id, name, key, _user_id(), created_at),
            )
            conn.commit()
        brands = _merged_competitor_brands(conn, workspace_id)

    return jsonify({"success": True, "data": brands, "count": len(brands)}), 201


@dsr_market_bp.route("/open-days", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def open_days():
    workspace_id = get_workspace_id()
    uid = _user_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        dates = _open_visit_dates(conn, workspace_id, uid)
        draft_count = 0
        if uid is not None:
            draft_count = conn.execute(
                "SELECT COUNT(*) FROM dsr_market_visits "
                "WHERE workspace_id = ? AND user_id = ? AND is_draft = 1",
                (workspace_id, uid),
            ).fetchone()[0]
    return jsonify(
        {
            "success": True,
            "data": {
                "open_dates": dates,
                "count": len(dates),
                "max_open_days": 2,
                "draft_count": draft_count,
            },
        }
    )


@dsr_market_bp.route("/drafts", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_drafts():
    workspace_id = get_workspace_id()
    uid = _user_id()
    visit_date = (request.args.get("date") or "").strip()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        query = "SELECT * FROM dsr_market_visits WHERE workspace_id = ? AND is_draft = 1"
        params: list = [workspace_id]
        if uid is not None and request.args.get("all") != "1":
            query += " AND user_id = ?"
            params.append(uid)
        if visit_date:
            query += " AND visit_date = ?"
            params.append(visit_date)
        query += " ORDER BY visit_date DESC, id DESC"
        rows = conn.execute(query, tuple(params)).fetchall()
    return jsonify({"success": True, "data": [_row_to_dict(r) for r in rows], "count": len(rows)})


def _score_party_candidate(payload: dict, party: dict, kind: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    p_name = _norm_name(payload.get("name"))
    c_name = _norm_name(party.get("name") or party.get("firm_name"))
    if p_name and c_name:
        if p_name == c_name:
            score += 50
            reasons.append("Exact firm name")
        elif p_name in c_name or c_name in p_name:
            score += 30
            reasons.append("Similar firm name")

    p_city = _norm_name(payload.get("city") or payload.get("location") or payload.get("city_area"))
    c_city = _norm_name(party.get("city") or party.get("location") or party.get("territory"))
    if p_city and c_city and (p_city == c_city or p_city in c_city or c_city in p_city):
        score += 20
        reasons.append("City / area match")

    p_phone = _norm_phone(payload.get("phone") or payload.get("contact_nos") or payload.get("phone_number"))
    c_phone = _norm_phone(
        party.get("phone_number") or party.get("phone") or party.get("phone_number_2")
    )
    if p_phone and c_phone and (p_phone == c_phone or p_phone[-10:] == c_phone[-10:]):
        score += 25
        reasons.append("Phone match")

    overlap = _address_overlap(payload.get("address"), party.get("address"))
    if overlap >= 0.5:
        score += 15
        reasons.append("Address strongly similar")
    elif overlap >= 0.25:
        score += 8
        reasons.append("Address partly similar")

    if kind == "retailer":
        try:
            want_dist = int(payload.get("distributor_id") or payload.get("linked_distributor_id") or 0)
        except (TypeError, ValueError):
            want_dist = 0
        try:
            got_dist = int(party.get("distributor_id") or 0)
        except (TypeError, ValueError):
            got_dist = 0
        if want_dist and got_dist and want_dist == got_dist:
            score += 15
            reasons.append("Same distributor")

    return score, reasons


@dsr_market_bp.route("/party-match", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def party_match():
    workspace_id = get_workspace_id()
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or data.get("draft_party_kind") or "retailer").strip().lower()
    if kind not in {"retailer", "distributor"}:
        return jsonify({"success": False, "error": {"message": "kind must be retailer or distributor"}}), 400

    db = _master_db()
    if kind == "retailer":
        parties = db.list_master_retailers(limit=800, workspace_id=workspace_id) or []
    else:
        parties = db.list_master_distributors(limit=800, workspace_id=workspace_id) or []

    scored = []
    for party in parties:
        score, reasons = _score_party_candidate(data, party, kind)
        if score >= 30:
            scored.append(
                {
                    "id": party.get("id"),
                    "kind": kind,
                    "name": party.get("name") or party.get("firm_name"),
                    "city": party.get("city") or party.get("location"),
                    "phone": party.get("phone_number") or party.get("phone"),
                    "address": party.get("address"),
                    "distributor_id": party.get("distributor_id"),
                    "score": score,
                    "reasons": reasons,
                    "needs_user_help": score < 70,
                }
            )
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:10]
    ambiguous = len(top) > 1 and top[0]["score"] < 85
    return jsonify(
        {
            "success": True,
            "data": {
                "candidates": top,
                "ambiguous": ambiguous or any(c.get("needs_user_help") for c in top[:3]),
                "message": (
                    "Possible matches found — please confirm which party, or create new."
                    if top
                    else "No close match — safe to create new party."
                ),
            },
        }
    )


@dsr_market_bp.route("/drafts/<int:visit_id>/resolve", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def resolve_draft(visit_id: int):
    workspace_id = get_workspace_id()
    uid = _user_id()
    data = request.get_json(silent=True) or {}
    force_create = _truthy_flag(str(data.get("force_create") or ""))
    link_party_id = data.get("link_party_id")
    try:
        link_party_id = int(link_party_id) if link_party_id not in (None, "") else None
    except (TypeError, ValueError):
        link_party_id = None

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM dsr_market_visits WHERE id = ? AND workspace_id = ?",
            (visit_id, workspace_id),
        ).fetchone()
        if row is None:
            return jsonify({"success": False, "error": {"message": "Visit not found"}}), 404
        visit = dict(row)
        if uid is not None and visit.get("user_id") not in (None, uid) and request.args.get("all") != "1":
            return jsonify({"success": False, "error": {"message": "Not your visit"}}), 403
        if not visit.get("is_draft"):
            return jsonify({"success": False, "error": {"message": "Visit is not a draft"}}), 400

        kind = (data.get("kind") or visit.get("draft_party_kind") or "retailer").strip().lower()
        if kind not in {"retailer", "distributor"}:
            kind = "retailer"

        name = (data.get("name") or data.get("customer_name") or visit.get("customer_name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": {"message": "name is required"}}), 400

        owner = (data.get("owner_name") or data.get("contact_person") or visit.get("owner_name") or "").strip() or None
        phone = (data.get("phone") or data.get("contact_nos") or visit.get("contact_nos") or "").strip() or None
        address = (data.get("address") or visit.get("address") or "").strip() or None
        city = (
            data.get("city")
            or data.get("location")
            or data.get("city_area")
            or visit.get("city_area")
            or visit.get("area_text")
            or ""
        ).strip() or None
        distributor_id = data.get("distributor_id") or visit.get("linked_distributor_id")
        try:
            distributor_id = int(distributor_id) if distributor_id not in (None, "") else None
        except (TypeError, ValueError):
            distributor_id = None

        match_payload = {
            "name": name,
            "city": city,
            "location": city,
            "phone": phone,
            "address": address,
            "distributor_id": distributor_id,
        }

        db = _master_db()
        if link_party_id is None and not force_create:
            if kind == "retailer":
                parties = db.list_master_retailers(limit=800, workspace_id=workspace_id) or []
            else:
                parties = db.list_master_distributors(limit=800, workspace_id=workspace_id) or []
            scored = []
            for party in parties:
                score, reasons = _score_party_candidate(match_payload, party, kind)
                if score >= 30:
                    scored.append(
                        {
                            "id": party.get("id"),
                            "kind": kind,
                            "name": party.get("name") or party.get("firm_name"),
                            "city": party.get("city") or party.get("location"),
                            "phone": party.get("phone_number") or party.get("phone"),
                            "address": party.get("address"),
                            "score": score,
                            "reasons": reasons,
                            "needs_user_help": score < 70,
                        }
                    )
            scored.sort(key=lambda x: x["score"], reverse=True)
            top = scored[:10]
            if top:
                return jsonify(
                    {
                        "success": False,
                        "error": {
                            "message": "Possible duplicate — confirm existing party or force create.",
                            "code": "possible_duplicate",
                        },
                        "data": {"candidates": top, "ambiguous": True},
                    }
                ), 409

        party_id = link_party_id
        if party_id is None:
            if kind == "retailer":
                party_id = db.add_master_retailer(
                    name=name,
                    distributor_id=distributor_id,
                    location=city,
                    phone_number=phone,
                    address=address,
                    contact_person=owner,
                    workspace_id=workspace_id,
                )
            else:
                party_id = db.add_master_distributor(
                    name=name,
                    firm_name=name,
                    location=city,
                    address=address,
                    phone_number=phone,
                    workspace_id=workspace_id,
                )

        linked_retailer_id = party_id if kind == "retailer" else visit.get("linked_retailer_id")
        linked_distributor_id = (
            party_id if kind == "distributor" else (distributor_id or visit.get("linked_distributor_id"))
        )

        conn.execute(
            """
            UPDATE dsr_market_visits SET
                customer_name = ?,
                owner_name = ?,
                contact_nos = ?,
                address = ?,
                city_area = ?,
                location = COALESCE(?, location),
                is_draft = 0,
                draft_party_kind = NULL,
                linked_distributor_id = ?,
                linked_retailer_id = ?,
                existing_or_new = 'Existing'
            WHERE id = ?
            """,
            (
                name,
                owner,
                phone,
                address,
                city,
                city,
                linked_distributor_id,
                linked_retailer_id,
                visit_id,
            ),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM dsr_market_visits WHERE id = ?", (visit_id,)
        ).fetchone()

    return jsonify(
        {
            "success": True,
            "data": {
                "visit": _row_to_dict(updated),
                "party_id": party_id,
                "party_kind": kind,
            },
            "message": "Draft resolved and party master updated",
        }
    )


@dsr_market_bp.route("/day-close", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def day_close():
    workspace_id = get_workspace_id()
    uid = _user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "User id required"}}), 400
    data = request.get_json(silent=True) or {}
    visit_date = (data.get("date") or data.get("visit_date") or "").strip()
    if not visit_date:
        return jsonify({"success": False, "error": {"message": "date is required (YYYY-MM-DD)"}}), 400

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        open_drafts = conn.execute(
            "SELECT id, customer_name FROM dsr_market_visits "
            "WHERE workspace_id = ? AND user_id = ? AND visit_date = ? AND is_draft = 1",
            (workspace_id, uid, visit_date),
        ).fetchall()
        if open_drafts:
            return jsonify(
                {
                    "success": False,
                    "error": {
                        "message": (
                            f"{len(open_drafts)} draft party(ies) still open for {visit_date}. "
                            "Resolve drafts first, then Day Close."
                        ),
                        "code": "drafts_pending",
                    },
                    "data": {"drafts": [_row_to_dict(r) for r in open_drafts]},
                }
            ), 400

        closed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO dsr_day_closures (workspace_id, user_id, visit_date, closed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace_id, user_id, visit_date) DO UPDATE SET closed_at = excluded.closed_at
            """,
            (workspace_id, uid, visit_date, closed_at),
        )
        conn.commit()
        open_dates = _open_visit_dates(conn, workspace_id, uid)

    return jsonify(
        {
            "success": True,
            "data": {
                "closed_date": visit_date,
                "closed_at": closed_at,
                "open_dates": open_dates,
            },
            "message": f"Day closed for {visit_date}",
        }
    )
