"""House of Prizm Deals CRM — linked to hop_customers (Vyapar parties)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.hop_db import create_customer, get_customer
from app.hop_schema import DEAL_STEP_IDS, DEAL_STEPS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_deal_number(conn: sqlite3.Connection, workspace_id: str) -> str:
    year = datetime.now(timezone.utc).year
    row = conn.execute(
        """
        SELECT COUNT(*) FROM hop_deals
        WHERE workspace_id = ? AND deal_number LIKE ?
        """,
        (workspace_id, f"D-{year}-%"),
    ).fetchone()
    seq = int((row[0] if row else 0) or 0) + 1
    return f"D-{year}-{seq:04d}"


def list_deals(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> list[dict]:
    sql = """
        SELECT d.*, c.company AS customer_company, c.mobile AS customer_mobile,
               c.gst_no AS customer_gst, c.source AS customer_source
        FROM hop_deals d
        LEFT JOIN hop_customers c ON c.id = d.customer_id
        WHERE d.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if q:
        like = f"%{q.strip()}%"
        sql += """ AND (
            d.deal_number LIKE ? OR d.title LIKE ? OR d.party_name LIKE ?
            OR c.company LIKE ? OR d.source LIKE ? OR d.current_step LIKE ?
        )"""
        params.extend([like, like, like, like, like, like])
    sql += " ORDER BY d.updated_at DESC, d.id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT d.*, c.company AS customer_company, c.mobile AS customer_mobile,
               c.email AS customer_email, c.gst_no AS customer_gst,
               c.city AS customer_city, c.address AS customer_address,
               c.source AS customer_source
        FROM hop_deals d
        LEFT JOIN hop_customers c ON c.id = d.customer_id
        WHERE d.workspace_id = ? AND d.id = ?
        """,
        (workspace_id, deal_id),
    ).fetchone()
    return dict(row) if row else None


def list_deal_events(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM hop_deal_events
        WHERE workspace_id = ? AND deal_id = ?
        ORDER BY id DESC
        """,
        (workspace_id, deal_id),
    ).fetchall()
    return [dict(r) for r in rows]


def add_deal_event(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_id: int,
    event_type: str,
    title: str | None = None,
    detail: str | None = None,
    step_id: str | None = None,
) -> dict:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO hop_deal_events (
            workspace_id, deal_id, step_id, event_type, title, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            step_id,
            event_type,
            (title or "").strip() or None,
            (detail or "").strip() or None,
            now,
        ),
    )
    eid = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM hop_deal_events WHERE id = ?", (eid,)).fetchone()
    return dict(row) if row else {"id": eid}


def create_deal(conn: sqlite3.Connection, workspace_id: str, payload: dict) -> dict:
    now = _now()
    customer_id = payload.get("customer_id")
    customer_id = int(customer_id) if customer_id not in (None, "") else None
    party_name = (payload.get("party_name") or "").strip() or None

    new_party = payload.get("new_party") if isinstance(payload.get("new_party"), dict) else None
    if customer_id is None and new_party and (new_party.get("company") or "").strip():
        cust = create_customer(
            conn,
            workspace_id,
            {
                "company": new_party.get("company"),
                "contact_person": new_party.get("contact_person"),
                "mobile": new_party.get("mobile"),
                "email": new_party.get("email"),
                "city": new_party.get("city"),
                "gst_no": new_party.get("gst_no"),
                "source": new_party.get("source") or payload.get("source") or "CRM Deal",
            },
        )
        customer_id = int(cust["id"])
        party_name = cust.get("company") or party_name

    if customer_id is not None:
        cust = get_customer(conn, workspace_id, customer_id)
        if not cust:
            raise ValueError("customer_id not found — select a Party from list (Vyapar import parties included)")
        party_name = party_name or cust.get("company")

    title = (payload.get("title") or "").strip()
    if not title:
        title = f"{party_name or 'New'} deal" if party_name else f"Deal {now[:10]}"

    step = (payload.get("current_step") or "lead").strip() or "lead"
    if step not in DEAL_STEP_IDS:
        step = "lead"
    step_index = DEAL_STEP_IDS.index(step)

    deal_number = (payload.get("deal_number") or "").strip() or _next_deal_number(conn, workspace_id)
    cur = conn.execute(
        """
        INSERT INTO hop_deals (
            workspace_id, deal_number, title, customer_id, party_name, source, assigned_to,
            expected_value, current_step, step_index, status, fulfillment_mode,
            products_interested, requirement_notes, next_follow_up,
            advance_amount, advance_received, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_number,
            title,
            customer_id,
            party_name,
            (payload.get("source") or "").strip() or None,
            (payload.get("assigned_to") or "").strip() or None,
            float(payload.get("expected_value") or 0),
            step,
            step_index,
            (payload.get("status") or "open").strip() or "open",
            (payload.get("fulfillment_mode") or "").strip() or None,
            (payload.get("products_interested") or "").strip() or None,
            (payload.get("requirement_notes") or "").strip() or None,
            (payload.get("next_follow_up") or "").strip() or None,
            float(payload.get("advance_amount") or 0),
            float(payload.get("advance_received") or 0),
            (payload.get("notes") or "").strip() or None,
            now,
            now,
        ),
    )
    deal_id = int(cur.lastrowid)
    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        "created",
        title="Deal created",
        detail=f"Linked party: {party_name or '—'}",
        step_id=step,
    )
    conn.commit()
    return get_deal(conn, workspace_id, deal_id) or {}


def update_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    existing = get_deal(conn, workspace_id, deal_id)
    if not existing:
        raise ValueError("Deal not found")

    def _pick_str(key: str):
        if key not in payload:
            return existing.get(key)
        return (payload.get(key) or "").strip() or None

    fields = {
        "title": (payload["title"].strip() if payload.get("title") is not None else existing.get("title")),
        "source": _pick_str("source") if "source" in payload else existing.get("source"),
        "assigned_to": _pick_str("assigned_to") if "assigned_to" in payload else existing.get("assigned_to"),
        "expected_value": (
            float(payload.get("expected_value") or 0)
            if "expected_value" in payload
            else float(existing.get("expected_value") or 0)
        ),
        "products_interested": (
            _pick_str("products_interested") if "products_interested" in payload else existing.get("products_interested")
        ),
        "requirement_notes": (
            _pick_str("requirement_notes") if "requirement_notes" in payload else existing.get("requirement_notes")
        ),
        "next_follow_up": (
            _pick_str("next_follow_up") if "next_follow_up" in payload else existing.get("next_follow_up")
        ),
        "notes": _pick_str("notes") if "notes" in payload else existing.get("notes"),
        "fulfillment_mode": (
            _pick_str("fulfillment_mode") if "fulfillment_mode" in payload else existing.get("fulfillment_mode")
        ),
        "advance_amount": (
            float(payload.get("advance_amount") or 0)
            if "advance_amount" in payload
            else float(existing.get("advance_amount") or 0)
        ),
        "advance_received": (
            float(payload.get("advance_received") or 0)
            if "advance_received" in payload
            else float(existing.get("advance_received") or 0)
        ),
        "party_name": _pick_str("party_name") if "party_name" in payload else existing.get("party_name"),
    }

    if not fields["title"]:
        raise ValueError("title is required")

    if "customer_id" in payload:
        cid = payload.get("customer_id")
        cid = int(cid) if cid not in (None, "") else None
        if cid is not None and not get_customer(conn, workspace_id, cid):
            raise ValueError("customer_id not found")
        fields["customer_id"] = cid
        if cid and not fields.get("party_name"):
            fields["party_name"] = (get_customer(conn, workspace_id, cid) or {}).get("company")
    else:
        fields["customer_id"] = existing.get("customer_id")

    status = existing.get("status") or "open"
    lost_at = existing.get("lost_at")
    closed_at = existing.get("closed_at")
    lost_reason = existing.get("lost_reason")
    current_step = existing.get("current_step") or "lead"
    step_index = int(existing.get("step_index") or 0)
    action = (payload.get("action") or "").strip()

    if action == "complete_step":
        if status != "open":
            raise ValueError("Only open deals can advance steps")
        note = (payload.get("step_note") or "").strip() or None
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            "step_done",
            title=f"Completed: {current_step}",
            detail=note,
            step_id=current_step,
        )
        if current_step == "closed":
            status = "closed"
            closed_at = _now()
        else:
            next_i = min(step_index + 1, len(DEAL_STEP_IDS) - 1)
            mode = fields.get("fulfillment_mode") or existing.get("fulfillment_mode")
            if mode == "drop_ship":
                while next_i < len(DEAL_STEP_IDS) and DEAL_STEP_IDS[next_i] in ("godown", "repack"):
                    next_i += 1
            current_step = DEAL_STEP_IDS[next_i]
            step_index = next_i
            if current_step == "closed":
                status = "closed"
                closed_at = _now()
    elif action == "skip_step":
        if status != "open":
            raise ValueError("Only open deals can skip steps")
        meta = next((s for s in DEAL_STEPS if s["id"] == current_step), None)
        if meta and not meta.get("optional"):
            raise ValueError(f"Step '{current_step}' cannot be skipped")
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            "step_skipped",
            title=f"Skipped: {current_step}",
            detail=(payload.get("step_note") or "").strip() or None,
            step_id=current_step,
        )
        next_i = min(step_index + 1, len(DEAL_STEP_IDS) - 1)
        current_step = DEAL_STEP_IDS[next_i]
        step_index = next_i
    elif action == "mark_lost":
        status = "lost"
        lost_at = _now()
        lost_reason = (payload.get("lost_reason") or "").strip() or "Lost"
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            "lost",
            title="Deal marked lost",
            detail=lost_reason,
            step_id=current_step,
        )
    elif action == "reopen":
        status = "open"
        lost_at = None
        lost_reason = None
        closed_at = None
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            "reopened",
            title="Deal reopened",
            step_id=current_step,
        )
    elif "current_step" in payload and payload.get("current_step"):
        step = str(payload.get("current_step")).strip()
        if step in DEAL_STEP_IDS:
            current_step = step
            step_index = DEAL_STEP_IDS.index(step)

    if "status" in payload and payload.get("status") in ("open", "lost", "closed") and not action:
        status = payload.get("status")

    now = _now()
    conn.execute(
        """
        UPDATE hop_deals SET
            title = ?, customer_id = ?, party_name = ?, source = ?, assigned_to = ?,
            expected_value = ?, current_step = ?, step_index = ?, status = ?,
            fulfillment_mode = ?, products_interested = ?, requirement_notes = ?,
            next_follow_up = ?, advance_amount = ?, advance_received = ?,
            lost_reason = ?, lost_at = ?, closed_at = ?, notes = ?, updated_at = ?
        WHERE workspace_id = ? AND id = ?
        """,
        (
            fields["title"],
            fields["customer_id"],
            fields["party_name"],
            fields["source"],
            fields["assigned_to"],
            fields["expected_value"],
            current_step,
            step_index,
            status,
            fields["fulfillment_mode"],
            fields["products_interested"],
            fields["requirement_notes"],
            fields["next_follow_up"],
            fields["advance_amount"],
            fields["advance_received"],
            lost_reason,
            lost_at,
            closed_at,
            fields["notes"],
            now,
            workspace_id,
            deal_id,
        ),
    )
    if action not in ("complete_step", "skip_step", "mark_lost", "reopen"):
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            "updated",
            title="Deal updated",
            step_id=current_step,
        )
    conn.commit()
    return get_deal(conn, workspace_id, deal_id) or {}


def delete_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> bool:
    existing = get_deal(conn, workspace_id, deal_id)
    if not existing:
        return False
    conn.execute(
        "DELETE FROM hop_deal_events WHERE workspace_id = ? AND deal_id = ?",
        (workspace_id, deal_id),
    )
    cur = conn.execute(
        "DELETE FROM hop_deals WHERE workspace_id = ? AND id = ?",
        (workspace_id, deal_id),
    )
    conn.commit()
    return cur.rowcount > 0
