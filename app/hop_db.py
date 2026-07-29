"""House of Prizm data access — workspace-scoped, no NEXORA table writes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row | tuple | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


class _ClosingConnection:
    """sqlite3 context managers commit/rollback but do not close; Windows needs close."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._conn.close()
        return False


def connect(db_path: str) -> _ClosingConnection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return _ClosingConnection(conn)


def list_customers(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = "SELECT * FROM hop_customers WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += """ AND (
            company LIKE ? OR contact_person LIKE ? OR mobile LIKE ?
            OR email LIKE ? OR city LIKE ? OR hotel_brand LIKE ?
        )"""
        params.extend([like, like, like, like, like, like])
    sql += " ORDER BY updated_at DESC, id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_customer(conn: sqlite3.Connection, workspace_id: str, customer_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM hop_customers WHERE workspace_id = ? AND id = ?",
        (workspace_id, customer_id),
    ).fetchone()
    return dict(row) if row else None


def delete_customer(conn: sqlite3.Connection, workspace_id: str, customer_id: int) -> bool:
    try:
        cur = conn.execute(
            "DELETE FROM hop_customers WHERE workspace_id = ? AND id = ?",
            (workspace_id, customer_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(
            "Cannot delete customer — linked to projects or other records. Remove links first."
        ) from exc


def update_customer(conn: sqlite3.Connection, workspace_id: str, customer_id: int, payload: dict) -> dict:
    existing = get_customer(conn, workspace_id, customer_id)
    if not existing:
        raise ValueError("Customer not found")
    company = (payload.get("company") or existing.get("company") or "").strip()
    if not company:
        raise ValueError("company is required")
    now = _now()

    def _pick_str(key: str):
        if key in payload:
            return (payload.get(key) or "").strip() or None
        return existing.get(key)

    def _pick_float(key: str):
        if key not in payload:
            return existing.get(key)
        if payload.get(key) in (None, ""):
            return None
        return float(payload[key])

    def _pick_int(key: str, default: int | None = None):
        if key not in payload:
            return existing.get(key) if existing.get(key) is not None else default
        if payload.get(key) in (None, ""):
            return default
        return int(payload[key])

    conn.execute(
        """
        UPDATE hop_customers SET
            company=?, contact_person=?, mobile=?, email=?, city=?, industry=?,
            architect=?, consultant=?, hotel_brand=?, annual_potential=?, source=?,
            potential_rating=?, remarks=?, customer_type=?, status=?, assigned_to=?,
            address=?, gst_no=?, pan=?,
            billing_name=?, shipping_address=?, state=?, gst_type=?,
            opening_balance=?, opening_balance_date=?, credit_limit=?, credit_no_limit=?,
            additional_fields=?, updated_at=?
        WHERE workspace_id=? AND id=?
        """,
        (
            company,
            _pick_str("contact_person"),
            _pick_str("mobile"),
            _pick_str("email"),
            _pick_str("city"),
            _pick_str("industry"),
            _pick_str("architect"),
            _pick_str("consultant"),
            _pick_str("hotel_brand"),
            _pick_float("annual_potential")
            if "annual_potential" in payload
            else existing.get("annual_potential"),
            _pick_str("source"),
            _pick_str("potential_rating"),
            _pick_str("remarks"),
            _pick_str("customer_type"),
            (_pick_str("status") or "active"),
            _pick_str("assigned_to"),
            _pick_str("address"),
            _pick_str("gst_no"),
            _pick_str("pan"),
            _pick_str("billing_name"),
            _pick_str("shipping_address"),
            _pick_str("state"),
            _pick_str("gst_type"),
            _pick_float("opening_balance"),
            _pick_str("opening_balance_date"),
            _pick_float("credit_limit"),
            _pick_int("credit_no_limit", 1),
            _pick_str("additional_fields"),
            now,
            workspace_id,
            customer_id,
        ),
    )
    conn.commit()
    return get_customer(conn, workspace_id, customer_id) or {}


def delete_customers_bulk(
    conn: sqlite3.Connection, workspace_id: str, customer_ids: list[int]
) -> dict[str, list]:
    deleted: list[int] = []
    errors: list[dict[str, Any]] = []
    for customer_id in customer_ids:
        try:
            if delete_customer(conn, workspace_id, int(customer_id)):
                deleted.append(int(customer_id))
            else:
                errors.append({"id": int(customer_id), "error": "Customer not found"})
        except ValueError as exc:
            errors.append({"id": int(customer_id), "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


def create_customer(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    company = (payload.get("company") or "").strip()
    if not company:
        raise ValueError("company is required")
    cur = conn.execute(
        """
        INSERT INTO hop_customers (
            workspace_id, company, contact_person, mobile, email, city, industry,
            architect, consultant, hotel_brand, annual_potential, source,
            potential_rating, remarks, customer_type, status, assigned_to,
            address, gst_no, pan,
            billing_name, shipping_address, state, gst_type,
            opening_balance, opening_balance_date, credit_limit, credit_no_limit,
            additional_fields, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            company,
            (payload.get("contact_person") or "").strip() or None,
            (payload.get("mobile") or "").strip() or None,
            (payload.get("email") or "").strip() or None,
            (payload.get("city") or "").strip() or None,
            (payload.get("industry") or "").strip() or None,
            (payload.get("architect") or "").strip() or None,
            (payload.get("consultant") or "").strip() or None,
            (payload.get("hotel_brand") or "").strip() or None,
            float(payload["annual_potential"]) if payload.get("annual_potential") not in (None, "") else None,
            (payload.get("source") or "").strip() or None,
            (payload.get("potential_rating") or "").strip() or None,
            (payload.get("remarks") or "").strip() or None,
            (payload.get("customer_type") or "").strip() or None,
            (payload.get("status") or "active").strip() or "active",
            (payload.get("assigned_to") or "").strip() or None,
            (payload.get("address") or "").strip() or None,
            (payload.get("gst_no") or "").strip() or None,
            (payload.get("pan") or "").strip() or None,
            (payload.get("billing_name") or "").strip() or None,
            (payload.get("shipping_address") or "").strip() or None,
            (payload.get("state") or "").strip() or None,
            (payload.get("gst_type") or "").strip() or None,
            float(payload["opening_balance"]) if payload.get("opening_balance") not in (None, "") else None,
            (payload.get("opening_balance_date") or "").strip() or None,
            float(payload["credit_limit"]) if payload.get("credit_limit") not in (None, "") else None,
            int(payload["credit_no_limit"]) if payload.get("credit_no_limit") not in (None, "") else 1,
            (payload.get("additional_fields") or "").strip() or None,
            now,
            now,
        ),
    )
    conn.commit()
    return get_customer(conn, workspace_id, int(cur.lastrowid)) or {}


def list_projects(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = """
        SELECT p.*, c.company AS customer_company
        FROM hop_projects p
        LEFT JOIN hop_customers c ON c.id = p.customer_id
        WHERE p.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (p.project_name LIKE ? OR p.hotel_name LIKE ? OR p.client_name LIKE ? OR c.company LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY p.updated_at DESC, p.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_project(conn: sqlite3.Connection, workspace_id: str, project_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT p.*, c.company AS customer_company
        FROM hop_projects p
        LEFT JOIN hop_customers c ON c.id = p.customer_id
        WHERE p.workspace_id = ? AND p.id = ?
        """,
        (workspace_id, project_id),
    ).fetchone()
    return dict(row) if row else None


def create_project(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    name = (payload.get("project_name") or "").strip()
    if not name:
        raise ValueError("project_name is required")
    customer_id = payload.get("customer_id")
    customer_id = int(customer_id) if customer_id not in (None, "") else None
    if customer_id is not None and not get_customer(conn, workspace_id, customer_id):
        raise ValueError("customer_id not found in this workspace")
    value = payload.get("project_value", payload.get("expected_value"))
    cur = conn.execute(
        """
        INSERT INTO hop_projects (
            workspace_id, project_name, customer_id, client_name, consultant, architect,
            stage, expected_value, project_value, probability_pct, completion_pct,
            delay_days, issues, next_milestone, assigned_to, hotel_name, site_address,
            status, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            name,
            customer_id,
            (payload.get("client_name") or "").strip() or None,
            (payload.get("consultant") or "").strip() or None,
            (payload.get("architect") or "").strip() or None,
            (payload.get("stage") or "lead").strip() or "lead",
            float(value or 0),
            float(value or 0),
            float(payload.get("probability_pct") or 0),
            float(payload.get("completion_pct") or 0),
            int(payload.get("delay_days") or 0),
            (payload.get("issues") or "").strip() or None,
            (payload.get("next_milestone") or "").strip() or None,
            (payload.get("assigned_to") or "").strip() or None,
            (payload.get("hotel_name") or "").strip() or None,
            (payload.get("site_address") or "").strip() or None,
            (payload.get("status") or "open").strip() or "open",
            (payload.get("notes") or "").strip() or None,
            now,
            now,
        ),
    )
    conn.commit()
    return get_project(conn, workspace_id, int(cur.lastrowid)) or {}


def next_lead_number(conn: sqlite3.Connection, workspace_id: str) -> str:
    year = datetime.now(timezone.utc).year
    row = conn.execute(
        """
        SELECT COUNT(*) FROM hop_leads
        WHERE workspace_id = ? AND lead_number LIKE ?
        """,
        (workspace_id, f"HOP-{year}-%"),
    ).fetchone()
    seq = int(row[0] or 0) + 1
    return f"HOP-{year}-{seq:04d}"


def list_leads(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = """
        SELECT l.*, c.company AS customer_company, p.project_name
        FROM hop_leads l
        LEFT JOIN hop_customers c ON c.id = l.customer_id
        LEFT JOIN hop_projects p ON p.id = l.project_id
        WHERE l.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (l.lead_number LIKE ? OR c.company LIKE ? OR p.project_name LIKE ? OR l.source LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY l.updated_at DESC, l.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_lead(conn: sqlite3.Connection, workspace_id: str, lead_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT l.*, c.company AS customer_company, p.project_name
        FROM hop_leads l
        LEFT JOIN hop_customers c ON c.id = l.customer_id
        LEFT JOIN hop_projects p ON p.id = l.project_id
        WHERE l.workspace_id = ? AND l.id = ?
        """,
        (workspace_id, lead_id),
    ).fetchone()
    return dict(row) if row else None


def create_lead(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    customer_id = payload.get("customer_id")
    customer_id = int(customer_id) if customer_id not in (None, "") else None
    project_id = payload.get("project_id")
    project_id = int(project_id) if project_id not in (None, "") else None
    if customer_id is not None and not get_customer(conn, workspace_id, customer_id):
        raise ValueError("customer_id not found")
    if project_id is not None and not get_project(conn, workspace_id, project_id):
        raise ValueError("project_id not found")

    # Project-centric: if no project, create one from lead + customer
    if project_id is None:
        company = None
        if customer_id:
            company = (get_customer(conn, workspace_id, customer_id) or {}).get("company")
        project_name = (payload.get("project_name") or "").strip() or (
            f"{company or 'New'} — Lead" if company else f"Lead {now[:10]}"
        )
        project = create_project(
            conn,
            workspace_id,
            {
                "project_name": project_name,
                "customer_id": customer_id,
                "client_name": company,
                "stage": "lead",
                "expected_value": payload.get("expected_value") or 0,
                "probability_pct": payload.get("probability_pct") or 0,
                "assigned_to": payload.get("assigned_to"),
                "hotel_name": payload.get("hotel_name"),
            },
        )
        project_id = int(project["id"])

    lead_number = (payload.get("lead_number") or "").strip() or next_lead_number(conn, workspace_id)
    stage = (payload.get("stage") or "new_lead").strip() or "new_lead"
    cur = conn.execute(
        """
        INSERT INTO hop_leads (
            workspace_id, lead_number, project_id, customer_id, source, assigned_to,
            priority, expected_value, probability_pct, stage, next_follow_up,
            meeting_notes, discussion, competitor, expected_budget, expected_closure_date,
            products_interested, status, created_at, updated_at, lost_at, won_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            lead_number,
            project_id,
            customer_id,
            (payload.get("source") or "").strip() or None,
            (payload.get("assigned_to") or "").strip() or None,
            (payload.get("priority") or "").strip() or None,
            float(payload.get("expected_value") or 0),
            float(payload.get("probability_pct") or 0),
            stage,
            (payload.get("next_follow_up") or "").strip() or None,
            (payload.get("meeting_notes") or "").strip() or None,
            (payload.get("discussion") or "").strip() or None,
            (payload.get("competitor") or "").strip() or None,
            float(payload["expected_budget"]) if payload.get("expected_budget") not in (None, "") else None,
            (payload.get("expected_closure_date") or "").strip() or None,
            (payload.get("products_interested") or "").strip() or None,
            (payload.get("status") or "open").strip() or "open",
            now,
            now,
            None,
            None,
        ),
    )
    conn.commit()
    return get_lead(conn, workspace_id, int(cur.lastrowid)) or {}


def list_meetings(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = """
        SELECT m.*, c.company AS customer_company, p.project_name, l.lead_number
        FROM hop_meetings m
        LEFT JOIN hop_customers c ON c.id = m.customer_id
        LEFT JOIN hop_projects p ON p.id = m.project_id
        LEFT JOIN hop_leads l ON l.id = m.lead_id
        WHERE m.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (m.title LIKE ? OR c.company LIKE ? OR p.project_name LIKE ? OR m.location LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY m.scheduled_at DESC, m.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def create_meeting(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    scheduled_at = (payload.get("scheduled_at") or "").strip()
    if not scheduled_at:
        raise ValueError("scheduled_at is required")
    project_id = payload.get("project_id")
    project_id = int(project_id) if project_id not in (None, "") else None
    lead_id = payload.get("lead_id")
    lead_id = int(lead_id) if lead_id not in (None, "") else None
    customer_id = payload.get("customer_id")
    customer_id = int(customer_id) if customer_id not in (None, "") else None
    if project_id is not None and not get_project(conn, workspace_id, project_id):
        raise ValueError("project_id not found")
    if lead_id is not None and not get_lead(conn, workspace_id, lead_id):
        raise ValueError("lead_id not found")
    if customer_id is not None and not get_customer(conn, workspace_id, customer_id):
        raise ValueError("customer_id not found")
    # Inherit customer/project from lead when possible
    if lead_id and (project_id is None or customer_id is None):
        lead = get_lead(conn, workspace_id, lead_id) or {}
        project_id = project_id or lead.get("project_id")
        customer_id = customer_id or lead.get("customer_id")

    cur = conn.execute(
        """
        INSERT INTO hop_meetings (
            workspace_id, project_id, lead_id, customer_id, title, scheduled_at, location,
            outcome, next_action, probability_pct, expected_order_value, status,
            agenda, follow_up_at, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            project_id,
            lead_id,
            customer_id,
            (payload.get("title") or "").strip() or "Meeting",
            scheduled_at,
            (payload.get("location") or "").strip() or None,
            (payload.get("outcome") or "").strip() or None,
            (payload.get("next_action") or "").strip() or None,
            float(payload["probability_pct"]) if payload.get("probability_pct") not in (None, "") else None,
            float(payload["expected_order_value"]) if payload.get("expected_order_value") not in (None, "") else None,
            (payload.get("status") or "scheduled").strip() or "scheduled",
            (payload.get("agenda") or "").strip() or None,
            (payload.get("follow_up_at") or "").strip() or None,
            (payload.get("notes") or "").strip() or None,
            now,
            now,
        ),
    )
    conn.commit()
    mid = int(cur.lastrowid)
    row = conn.execute(
        """
        SELECT m.*, c.company AS customer_company, p.project_name, l.lead_number
        FROM hop_meetings m
        LEFT JOIN hop_customers c ON c.id = m.customer_id
        LEFT JOIN hop_projects p ON p.id = m.project_id
        LEFT JOIN hop_leads l ON l.id = m.lead_id
        WHERE m.workspace_id = ? AND m.id = ?
        """,
        (workspace_id, mid),
    ).fetchone()
    return dict(row) if row else {"id": mid}
