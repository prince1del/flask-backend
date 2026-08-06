"""House of Prizm Deals CRM v2 — 8-stage pipeline linked to hop_customers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from app.hop_db import create_customer, get_customer
from app.hop_schema import (
    DEAL_ACTIVITY_TYPES,
    DEAL_CANCEL_REASONS_POST_PO,
    DEAL_LOSS_REASONS_PRE_PO,
    DEAL_STAGE_IDS,
    DEAL_STAGES,
    DEAL_STEP_IDS,
    DEAL_STEPS,
    LEGACY_STEP_TO_STAGE,
)

# Default "what's next" when entering a stage (avoids stale "Call customer" after Connect)
STAGE_DEFAULT_NEXT_ACTION = {
    "new": "Link party & set next action",
    "connect": "Call / WhatsApp the customer",
    "discover": "Add requirement details",
    "quote": "Create / send quotation",
    "confirm": "Record customer PO & advance",
    "fulfil": "Update vendor / dispatch status",
    "collect": "Follow up for payment",
    "closed": "Deal complete",
}


def _is_stale_connect_next_action(value: str | None) -> bool:
    low = (value or "").strip().lower()
    if not low:
        return False
    return low in {
        "call customer",
        "call the customer",
        "call / whatsapp the customer",
        "call / whatsapp customer",
    } or low.startswith("call customer")


def effective_next_action(deal: dict) -> str:
    """Stage-aware next action for list/board/detail (avoids stuck 'Call customer')."""
    stage = (deal.get("current_stage") or "new").strip() or "new"
    default = STAGE_DEFAULT_NEXT_ACTION.get(stage) or "Review deal"
    saved = (deal.get("next_action_type") or "").strip()
    if not saved:
        return default
    if stage not in ("new", "connect") and _is_stale_connect_next_action(saved):
        return default
    return saved


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_deal_number(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Auto lead id: L-YYYY-#### (legacy D-YYYY-#### still counted for sequence)."""
    year = datetime.now(timezone.utc).year
    rows = conn.execute(
        """
        SELECT deal_number FROM hop_deals
        WHERE workspace_id = ?
          AND (deleted_at IS NULL OR deleted_at = '')
          AND (deal_number LIKE ? OR deal_number LIKE ?)
        """,
        (workspace_id, f"D-{year}-%", f"L-{year}-%"),
    ).fetchall()
    max_seq = 0
    for r in rows:
        raw = r["deal_number"] if hasattr(r, "keys") else r[0]
        parts = str(raw or "").rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            max_seq = max(max_seq, int(parts[1]))
    return f"L-{year}-{max_seq + 1:04d}"


def _normalize_deal_number(raw: Any) -> str:
    return " ".join(str(raw or "").strip().split())


def _assert_deal_number_available(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_number: str,
    *,
    exclude_id: int | None = None,
) -> str:
    num = _normalize_deal_number(deal_number)
    if not num:
        raise ValueError("Lead number is required")
    if len(num) > 64:
        raise ValueError("Lead number is too long (max 64 characters)")
    q = """
        SELECT id, title, deal_number FROM hop_deals
        WHERE workspace_id=? AND lower(deal_number)=lower(?)
          AND (deleted_at IS NULL OR deleted_at='')
    """
    params: list[Any] = [workspace_id, num]
    if exclude_id is not None:
        q += " AND id!=?"
        params.append(int(exclude_id))
    hit = conn.execute(q, params).fetchone()
    if hit:
        other = dict(hit) if hasattr(hit, "keys") else {"id": hit[0], "title": hit[1], "deal_number": hit[2]}
        title = (other.get("title") or "").strip() or f"#{other.get('id')}"
        raise ValueError(
            f"Lead number “{num}” is already used by “{title}”. "
            f"Open that lead and change its number first, or pick a free number."
        )
    return num


def deal_meta() -> dict[str, Any]:
    return {
        "stages": DEAL_STAGES,
        "activity_types": DEAL_ACTIVITY_TYPES,
        "loss_reasons_pre_po": DEAL_LOSS_REASONS_PRE_PO,
        "cancel_reasons_post_po": DEAL_CANCEL_REASONS_POST_PO,
        "legacy_steps": DEAL_STEPS,
    }


def _stage_index(stage: str) -> int:
    try:
        return DEAL_STAGE_IDS.index(stage)
    except ValueError:
        return 0


def _post_po(stage: str) -> bool:
    return _stage_index(stage) >= _stage_index("confirm")


def compute_deal_health(deal: dict[str, Any]) -> str:
    """Healthy / needs_attention / at_risk / critical from next-action + activity age."""
    status = (deal.get("status") or "open").lower()
    if status in ("lost", "cancelled", "written_off", "won") or deal.get("current_stage") == "closed":
        return "healthy"
    if status == "disputed":
        return "critical"
    if status == "on_hold":
        return "needs_attention"

    now = datetime.now(timezone.utc)
    due = (deal.get("next_action_due") or "").strip()
    no_until = (deal.get("no_action_until") or "").strip()
    last = (deal.get("last_activity_at") or deal.get("updated_at") or "").strip()

    overdue = False
    if due:
        try:
            d = datetime.fromisoformat(due.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            overdue = d < now
        except Exception:
            overdue = False

    stale_days = 0
    if last:
        try:
            la = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if la.tzinfo is None:
                la = la.replace(tzinfo=timezone.utc)
            stale_days = max(0, (now - la).days)
        except Exception:
            stale_days = 0

    has_next = bool((deal.get("next_action_type") or "").strip()) or bool(no_until)
    if overdue and stale_days >= 7:
        return "critical"
    if overdue:
        return "at_risk"
    if not has_next and status == "open":
        return "at_risk"
    if stale_days >= 5:
        return "needs_attention"
    return "healthy"


def _audit(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_id: int,
    action: str,
    *,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    actor: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO hop_deal_audit_log (
            workspace_id, deal_id, action, field_name, old_value, new_value, reason, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            action,
            field_name,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            (reason or "").strip() or None,
            (actor or "").strip() or None,
            _now(),
        ),
    )


def add_deal_event(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_id: int,
    *,
    event_type: str,
    title: str,
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
            title,
            (detail or "").strip() or None,
            now,
        ),
    )
    eid = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM hop_deal_events WHERE id = ?", (eid,)).fetchone()
    return dict(row) if row else {"id": eid}


def list_deals(
    conn: sqlite3.Connection,
    workspace_id: str,
    q: str | None = None,
    *,
    stage: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    health: str | None = None,
    include_deleted: bool = False,
) -> list[dict]:
    sql = """
        SELECT d.*, c.company AS customer_company, c.mobile AS customer_mobile,
               c.gst_no AS customer_gst, c.source AS customer_source
        FROM hop_deals d
        LEFT JOIN hop_customers c ON c.id = d.customer_id
        WHERE d.workspace_id = ?
    """
    params: list[Any] = [workspace_id]
    if not include_deleted:
        sql += " AND (d.deleted_at IS NULL OR d.deleted_at = '')"
    if q:
        like = f"%{q.strip()}%"
        sql += """ AND (
            d.deal_number LIKE ? OR d.title LIKE ? OR d.party_name LIKE ?
            OR c.company LIKE ? OR d.source LIKE ? OR d.current_stage LIKE ?
            OR d.current_step LIKE ?
        )"""
        params.extend([like, like, like, like, like, like, like])
    if stage:
        sql += " AND d.current_stage = ?"
        params.append(stage)
    if status:
        sql += " AND d.status = ?"
        params.append(status)
    if owner:
        sql += " AND lower(coalesce(d.assigned_to,'')) = lower(?)"
        params.append(owner)
    if health:
        sql += " AND d.deal_health = ?"
        params.append(health)
    sql += " ORDER BY d.updated_at DESC, d.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    repaired = False
    for row in rows:
        h = compute_deal_health(row)
        if h != (row.get("deal_health") or ""):
            row["deal_health"] = h
        stage = (row.get("current_stage") or "new").strip() or "new"
        saved = (row.get("next_action_type") or "").strip()
        effective = effective_next_action(row)
        if saved != effective:
            row["next_action_type"] = effective
            # Persist repair for stale Connect-era defaults
            if stage not in ("new", "connect") and _is_stale_connect_next_action(saved):
                conn.execute(
                    """
                    UPDATE hop_deals SET next_action_type=?, updated_at=?
                    WHERE workspace_id=? AND id=?
                    """,
                    (effective, _now(), workspace_id, row["id"]),
                )
                repaired = True
    if repaired:
        conn.commit()
    return rows


def board_deals(conn: sqlite3.Connection, workspace_id: str, q: str | None = None) -> dict[str, Any]:
    rows = list_deals(conn, workspace_id, q=q, status="open")
    # also include on_hold in board? Spec: pipeline columns are 8 stages; side exits separate.
    holds = list_deals(conn, workspace_id, q=q, status="on_hold")
    columns: dict[str, list] = {s: [] for s in DEAL_STAGE_IDS}
    for r in rows + holds:
        st = r.get("current_stage") or "new"
        if st not in columns:
            st = "new"
        columns[st].append(r)
    summary = {
        sid: {
            "count": len(columns[sid]),
            "value": sum(float(x.get("expected_value") or 0) for x in columns[sid]),
        }
        for sid in DEAL_STAGE_IDS
    }
    return {"stages": DEAL_STAGES, "columns": columns, "summary": summary}


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
    if not row:
        return None
    deal = dict(row)
    deal["deal_health"] = compute_deal_health(deal)
    saved = (deal.get("next_action_type") or "").strip()
    effective = effective_next_action(deal)
    if saved != effective:
        deal["next_action_type"] = effective
        stage = (deal.get("current_stage") or "new").strip() or "new"
        if stage not in ("new", "connect") and _is_stale_connect_next_action(saved):
            conn.execute(
                """
                UPDATE hop_deals SET next_action_type=?, updated_at=?
                WHERE workspace_id=? AND id=?
                """,
                (effective, _now(), workspace_id, deal_id),
            )
            conn.commit()
    return deal


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


def list_stage_history(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM hop_deal_stage_history
        WHERE workspace_id = ? AND deal_id = ?
        ORDER BY id DESC
        """,
        (workspace_id, deal_id),
    ).fetchall()
    return [dict(r) for r in rows]


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
                "address": new_party.get("address"),
                "gst_no": new_party.get("gst_no"),
                "source": new_party.get("source") or payload.get("source") or "CRM Deal",
            },
        )
        customer_id = int(cust["id"])
        party_name = cust.get("company") or party_name

    if customer_id is not None:
        cust = get_customer(conn, workspace_id, customer_id)
        if not cust:
            raise ValueError("customer_id not found — select a Party from list")
        party_name = party_name or cust.get("company")

    title = (payload.get("title") or "").strip()
    if not title:
        title = f"{party_name or 'New'} deal" if party_name else f"Deal {now[:10]}"

    assigned = (payload.get("assigned_to") or "").strip() or None
    if not assigned:
        raise ValueError("assigned_to (owner) is required")

    stage = (payload.get("current_stage") or "new").strip() or "new"
    if stage not in DEAL_STAGE_IDS:
        stage = "new"

    next_action = (
        (payload.get("next_action_type") or "").strip()
        or STAGE_DEFAULT_NEXT_ACTION.get(stage)
        or "Call / WhatsApp the customer"
    )
    next_due = (payload.get("next_action_due") or "").strip() or None

    # Keep legacy step in sync for old UI compatibility
    legacy = "lead" if stage == "new" else LEGACY_STEP_TO_STAGE and next(
        (k for k, v in LEGACY_STEP_TO_STAGE.items() if v == stage), "lead"
    )
    step_index = DEAL_STEP_IDS.index(legacy) if legacy in DEAL_STEP_IDS else 0

    deal_number_raw = (payload.get("deal_number") or "").strip()
    if deal_number_raw:
        deal_number = _assert_deal_number_available(conn, workspace_id, deal_number_raw)
    else:
        deal_number = _next_deal_number(conn, workspace_id)
    cur = conn.execute(
        """
        INSERT INTO hop_deals (
            workspace_id, deal_number, title, customer_id, party_name, source, assigned_to,
            expected_value, current_step, step_index, status, fulfillment_mode,
            products_interested, requirement_notes, next_follow_up,
            advance_amount, advance_received, notes, created_at, updated_at,
            current_stage, deal_health, priority, expected_close_date, product_category,
            next_action_type, next_action_owner, next_action_due, next_action_note,
            created_by, legacy_step
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_number,
            title,
            customer_id,
            party_name,
            (payload.get("source") or "").strip() or None,
            assigned,
            float(payload.get("expected_value") or 0),
            legacy,
            step_index,
            "open",
            (payload.get("fulfillment_mode") or "").strip() or None,
            (payload.get("products_interested") or "").strip() or None,
            (payload.get("requirement_notes") or payload.get("requirement_summary") or "").strip() or None,
            next_due,
            float(payload.get("advance_amount") or 0),
            float(payload.get("advance_received") or 0),
            (payload.get("notes") or "").strip() or None,
            now,
            now,
            stage,
            "healthy",
            (payload.get("priority") or "normal").strip() or "normal",
            (payload.get("expected_close_date") or "").strip() or None,
            (payload.get("product_category") or "").strip() or None,
            next_action,
            assigned,
            next_due,
            (payload.get("next_action_note") or "").strip() or None,
            (payload.get("created_by") or assigned or "").strip() or None,
            legacy,
        ),
    )
    deal_id = int(cur.lastrowid)
    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        event_type="created",
        title="Lead created",
        detail=f"Stage: New · Owner: {assigned}",
        step_id=legacy,
    )
    conn.execute(
        """
        INSERT INTO hop_deal_stage_history (
            workspace_id, deal_id, from_stage, to_stage, from_status, to_status, reason, changed_by, created_at
        ) VALUES (?, ?, NULL, ?, NULL, 'open', 'created', ?, ?)
        """,
        (workspace_id, deal_id, stage, assigned, now),
    )
    _audit(conn, workspace_id, deal_id, "create", actor=assigned)
    conn.commit()
    return get_deal(conn, workspace_id, deal_id) or {}


def update_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        raise ValueError("Deal not found")
    if deal.get("deleted_at"):
        raise ValueError("Deal is deleted")

    action = (payload.get("action") or "").strip()
    if action:
        return apply_deal_action(conn, workspace_id, deal_id, payload)

    # Field patch
    fields = {
        "title": "title",
        "deal_number": "deal_number",
        "source": "source",
        "assigned_to": "assigned_to",
        "expected_value": "expected_value",
        "fulfillment_mode": "fulfillment_mode",
        "products_interested": "products_interested",
        "requirement_notes": "requirement_notes",
        "requirement_summary": "requirement_summary",
        "notes": "notes",
        "priority": "priority",
        "expected_close_date": "expected_close_date",
        "product_category": "product_category",
        "next_action_type": "next_action_type",
        "next_action_owner": "next_action_owner",
        "next_action_due": "next_action_due",
        "next_action_note": "next_action_note",
        "no_action_until": "no_action_until",
        "customer_id": "customer_id",
        "party_name": "party_name",
        "current_sub_status": "current_sub_status",
        "advance_amount": "advance_amount",
        "advance_received": "advance_received",
    }
    sets: list[str] = []
    params: list[Any] = []
    for key, col in fields.items():
        if key not in payload:
            continue
        old = deal.get(col)
        val = payload.get(key)
        if key in ("expected_value", "advance_amount", "advance_received"):
            val = float(val or 0)
        elif key == "customer_id":
            val = int(val) if val not in (None, "") else None
            if val is not None:
                cust = get_customer(conn, workspace_id, val)
                if not cust:
                    raise ValueError("customer_id not found")
                if "party_name" not in payload:
                    sets.append("party_name=?")
                    params.append(cust.get("company"))
        elif key == "deal_number":
            val = _assert_deal_number_available(
                conn, workspace_id, val, exclude_id=deal_id
            )
        else:
            val = (str(val).strip() if val is not None else None) or None
        if str(old) != str(val):
            _audit(
                conn,
                workspace_id,
                deal_id,
                "field_change",
                field_name=col,
                old_value=old,
                new_value=val,
                actor=payload.get("actor"),
            )
        sets.append(f"{col}=?")
        params.append(val)

    if not sets:
        return deal

    sets.append("updated_at=?")
    params.append(_now())
    params.extend([workspace_id, deal_id])
    conn.execute(
        f"UPDATE hop_deals SET {', '.join(sets)} WHERE workspace_id=? AND id=?",
        params,
    )
    # refresh health
    refreshed = get_deal(conn, workspace_id, deal_id) or {}
    h = compute_deal_health(refreshed)
    conn.execute(
        "UPDATE hop_deals SET deal_health=?, updated_at=? WHERE workspace_id=? AND id=?",
        (h, _now(), workspace_id, deal_id),
    )
    conn.commit()
    return get_deal(conn, workspace_id, deal_id) or {}


def _sync_legacy_step(stage: str) -> tuple[str, int]:
    for step_id, mapped in LEGACY_STEP_TO_STAGE.items():
        if mapped == stage and step_id in DEAL_STEP_IDS:
            return step_id, DEAL_STEP_IDS.index(step_id)
    return "lead", 0


def move_stage(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_id: int,
    to_stage: str,
    *,
    reason: str | None = None,
    actor: str | None = None,
) -> dict:
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        raise ValueError("Deal not found")
    to_stage = (to_stage or "").strip()
    if to_stage not in DEAL_STAGE_IDS:
        raise ValueError(f"Invalid stage: {to_stage}")
    from_stage = deal.get("current_stage") or "new"
    if from_stage == to_stage:
        return deal

    legacy, step_index = _sync_legacy_step(to_stage)
    now = _now()
    new_status = "won" if to_stage == "closed" else (deal.get("status") or "open")
    if to_stage == "closed" and not (reason or "").strip():
        # Closing is allowed in Phase 1 without payment ledger; reason encouraged.
        reason = reason or "Closed"
    if new_status == "won":
        conn.execute(
            """
            UPDATE hop_deals SET current_stage=?, current_step=?, step_index=?, status=?,
                legacy_step=?, closed_at=?, updated_at=?, deal_health='healthy'
            WHERE workspace_id=? AND id=?
            """,
            (to_stage, legacy, step_index, new_status, legacy, now, now, workspace_id, deal_id),
        )
    else:
        if deal.get("status") in ("lost", "cancelled", "written_off"):
            raise ValueError("Reopen the deal before changing stage")
        conn.execute(
            """
            UPDATE hop_deals SET current_stage=?, current_step=?, step_index=?,
                legacy_step=?, updated_at=?
            WHERE workspace_id=? AND id=?
            """,
            (to_stage, legacy, step_index, legacy, now, workspace_id, deal_id),
        )

    conn.execute(
        """
        INSERT INTO hop_deal_stage_history (
            workspace_id, deal_id, from_stage, to_stage, from_status, to_status, reason, changed_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            from_stage,
            to_stage,
            deal.get("status"),
            new_status if to_stage == "closed" else deal.get("status"),
            reason,
            actor,
            now,
        ),
    )
    direction = "forward" if _stage_index(to_stage) > _stage_index(from_stage) else "back"
    from_label = next((s.get("label") for s in DEAL_STAGES if s.get("id") == from_stage), from_stage.title())
    to_label = next((s.get("label") for s in DEAL_STAGES if s.get("id") == to_stage), to_stage.title())
    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        event_type="stage_change",
        title=f"Deal moved from {from_label} to {to_label}",
        detail=reason or direction,
        step_id=legacy,
    )
    _audit(
        conn,
        workspace_id,
        deal_id,
        "stage_change",
        field_name="current_stage",
        old_value=from_stage,
        new_value=to_stage,
        reason=reason,
        actor=actor,
    )
    # Refresh next-action suggestion when moving forward so UI stays stage-aware
    if _stage_index(to_stage) > _stage_index(from_stage) and to_stage != "closed":
        default_next = STAGE_DEFAULT_NEXT_ACTION.get(to_stage)
        if default_next:
            conn.execute(
                """
                UPDATE hop_deals SET next_action_type=?, updated_at=?
                WHERE workspace_id=? AND id=?
                """,
                (default_next, now, workspace_id, deal_id),
            )
    conn.commit()
    return get_deal(conn, workspace_id, deal_id) or {}


def apply_deal_action(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        raise ValueError("Deal not found")
    action = (payload.get("action") or "").strip()
    actor = (payload.get("actor") or deal.get("assigned_to") or "").strip() or None
    note = (payload.get("step_note") or payload.get("note") or payload.get("remarks") or "").strip()
    stage = deal.get("current_stage") or "new"

    if action == "advance_stage":
        idx = _stage_index(stage)
        if idx >= len(DEAL_STAGE_IDS) - 1:
            raise ValueError("Already at final stage")
        # Stage completion gates
        if stage == "new":
            if not deal.get("customer_id") and not deal.get("party_name"):
                raise ValueError("Link a Party before leaving New")
            if not deal.get("assigned_to"):
                raise ValueError("Assign an owner before leaving New")
            if not deal.get("next_action_type") and not deal.get("no_action_until"):
                raise ValueError("Set a next action (or no-action-until date) before leaving New")
        if stage == "connect":
            outcome = (payload.get("outcome") or deal.get("current_sub_status") or "").strip()
            if not outcome:
                raise ValueError("Connect step needs an outcome (e.g. meeting_booked, follow_up_scheduled)")
        if stage == "discover":
            lines = list_deal_lines(conn, workspace_id, deal_id)
            if not lines:
                raise ValueError("Add at least one requirement line before leaving Discover")
        nxt = DEAL_STAGE_IDS[idx + 1]
        return move_stage(conn, workspace_id, deal_id, nxt, reason=note or "stage completed", actor=actor)

    if action == "complete_step":
        # Back-compat with old UI
        return apply_deal_action(conn, workspace_id, deal_id, {**payload, "action": "advance_stage"})

    if action == "set_outcome":
        outcome = (payload.get("outcome") or "").strip()
        if not outcome:
            raise ValueError("outcome required")
        conn.execute(
            "UPDATE hop_deals SET current_sub_status=?, updated_at=? WHERE workspace_id=? AND id=?",
            (outcome, _now(), workspace_id, deal_id),
        )
        add_deal_event(
            conn, workspace_id, deal_id,
            event_type="outcome", title=f"Outcome: {outcome}", detail=note,
        )
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "do_later":
        due = (payload.get("next_action_due") or "").strip()
        ntype = (payload.get("next_action_type") or deal.get("next_action_type") or "Follow up").strip()
        conn.execute(
            """
            UPDATE hop_deals SET next_action_type=?, next_action_due=?, next_action_note=?,
                updated_at=? WHERE workspace_id=? AND id=?
            """,
            (ntype, due or None, note or None, _now(), workspace_id, deal_id),
        )
        add_deal_event(conn, workspace_id, deal_id, event_type="do_later", title="Do later", detail=f"{ntype} · {due}")
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "not_required":
        add_deal_event(conn, workspace_id, deal_id, event_type="not_required", title="Marked not required", detail=note)
        _audit(conn, workspace_id, deal_id, "not_required", reason=note, actor=actor)
        # Optionally advance
        if payload.get("advance"):
            return apply_deal_action(conn, workspace_id, deal_id, {**payload, "action": "advance_stage"})
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "mark_lost":
        reason_code = (payload.get("loss_reason_code") or payload.get("lost_reason") or "").strip()
        if not reason_code:
            raise ValueError("Lost reason is required")
        if _post_po(stage) and reason_code not in DEAL_CANCEL_REASONS_POST_PO and reason_code not in (
            "lost",
        ):
            # After PO prefer cancel taxonomy — still allow if explicitly in cancel list
            if reason_code not in DEAL_LOSS_REASONS_PRE_PO:
                pass  # accept free-text / known codes
        now = _now()
        conn.execute(
            """
            UPDATE hop_deals SET status='lost', loss_reason_code=?, lost_reason=?,
                side_exit_remarks=?, side_exit_at=?, lost_at=?, competitor=?, revival_date=?,
                updated_at=?, deal_health='healthy'
            WHERE workspace_id=? AND id=?
            """,
            (
                reason_code,
                reason_code,
                note or None,
                now,
                now,
                (payload.get("competitor") or "").strip() or None,
                (payload.get("revival_date") or "").strip() or None,
                now,
                workspace_id,
                deal_id,
            ),
        )
        add_deal_event(conn, workspace_id, deal_id, event_type="lost", title=f"Lost: {reason_code}", detail=note)
        _audit(conn, workspace_id, deal_id, "mark_lost", reason=reason_code, actor=actor)
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "mark_on_hold":
        reason_code = (payload.get("cancel_reason_code") or "on_hold").strip()
        now = _now()
        conn.execute(
            """
            UPDATE hop_deals SET status='on_hold', cancel_reason_code=?, side_exit_remarks=?,
                side_exit_at=?, updated_at=?, deal_health='needs_attention'
            WHERE workspace_id=? AND id=?
            """,
            (reason_code, note or None, now, now, workspace_id, deal_id),
        )
        add_deal_event(conn, workspace_id, deal_id, event_type="on_hold", title="On Hold", detail=note)
        _audit(conn, workspace_id, deal_id, "on_hold", reason=note or reason_code, actor=actor)
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "mark_cancelled":
        if not _post_po(stage):
            raise ValueError("Before Customer PO, use Mark Lost instead of Cancelled")
        reason_code = (payload.get("cancel_reason_code") or "").strip()
        if not reason_code:
            raise ValueError("Cancellation reason is required")
        now = _now()
        conn.execute(
            """
            UPDATE hop_deals SET status='cancelled', cancel_reason_code=?, side_exit_remarks=?,
                side_exit_at=?, updated_at=?
            WHERE workspace_id=? AND id=?
            """,
            (reason_code, note or None, now, now, workspace_id, deal_id),
        )
        add_deal_event(conn, workspace_id, deal_id, event_type="cancelled", title=f"Cancelled: {reason_code}", detail=note)
        _audit(conn, workspace_id, deal_id, "cancelled", reason=reason_code, actor=actor)
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "reopen":
        now = _now()
        conn.execute(
            """
            UPDATE hop_deals SET status='open', lost_at=NULL, side_exit_at=NULL,
                closed_at=NULL, updated_at=?
            WHERE workspace_id=? AND id=?
            """,
            (now, workspace_id, deal_id),
        )
        add_deal_event(conn, workspace_id, deal_id, event_type="reopen", title="Deal reopened", detail=note)
        _audit(conn, workspace_id, deal_id, "reopen", reason=note, actor=actor)
        conn.commit()
        return get_deal(conn, workspace_id, deal_id) or {}

    if action == "skip_step":
        # Spec: prefer Not Required — map skip to not_required + advance for optional legacy
        return apply_deal_action(
            conn, workspace_id, deal_id,
            {**payload, "action": "not_required", "advance": True},
        )

    if action == "move_stage":
        to_stage = (payload.get("to_stage") or "").strip()
        return move_stage(conn, workspace_id, deal_id, to_stage, reason=note, actor=actor)

    raise ValueError(f"Unknown action: {action}")


def soft_delete_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int, *, actor: str | None = None) -> bool:
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        return False
    now = _now()
    # Release lead number so it can be reused on a new/active lead
    old_num = _normalize_deal_number(deal.get("deal_number"))
    freed_num = f"{old_num}~del{deal_id}"[:64] if old_num else None
    conn.execute(
        """
        UPDATE hop_deals
        SET deleted_at=?, updated_at=?, deal_number=COALESCE(?, deal_number)
        WHERE workspace_id=? AND id=?
        """,
        (now, now, freed_num, workspace_id, deal_id),
    )
    _audit(
        conn,
        workspace_id,
        deal_id,
        "soft_delete",
        actor=actor,
        field_name="deal_number",
        old_value=old_num or None,
        new_value=freed_num,
    )
    add_deal_event(conn, workspace_id, deal_id, event_type="deleted", title="Lead deleted")
    conn.commit()
    return True


def delete_deal(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> bool:
    """Soft-delete by default (spec)."""
    return soft_delete_deal(conn, workspace_id, deal_id)


# ---------- Lines ----------
def list_deal_lines(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM hop_deal_lines
        WHERE workspace_id=? AND deal_id=? AND (deleted_at IS NULL OR deleted_at='')
        ORDER BY sort_order, id
        """,
        (workspace_id, deal_id),
    ).fetchall()
    return [dict(r) for r in rows]


def add_deal_line(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    if not get_deal(conn, workspace_id, deal_id):
        raise ValueError("Deal not found")
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO hop_deal_lines (
            workspace_id, deal_id, product_category, product_description, catalogue,
            design_code, colour, qty, unit, room_area, notes, line_status, sort_order,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            (payload.get("product_category") or "").strip() or None,
            (payload.get("product_description") or "").strip() or None,
            (payload.get("catalogue") or "").strip() or None,
            (payload.get("design_code") or "").strip() or None,
            (payload.get("colour") or "").strip() or None,
            float(payload.get("qty") or 0),
            (payload.get("unit") or "").strip() or None,
            (payload.get("room_area") or "").strip() or None,
            (payload.get("notes") or "").strip() or None,
            (payload.get("line_status") or "pending").strip() or "pending",
            int(payload.get("sort_order") or 0),
            now,
            now,
        ),
    )
    lid = int(cur.lastrowid)
    add_deal_event(
        conn, workspace_id, deal_id,
        event_type="requirement",
        title="Requirement line added",
        detail=payload.get("product_description") or payload.get("product_category"),
    )
    conn.execute("UPDATE hop_deals SET updated_at=? WHERE id=?", (now, deal_id))
    conn.commit()
    row = conn.execute("SELECT * FROM hop_deal_lines WHERE id=?", (lid,)).fetchone()
    return dict(row) if row else {"id": lid}


def get_deal_line(conn: sqlite3.Connection, workspace_id: str, deal_id: int, line_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM hop_deal_lines
        WHERE workspace_id=? AND deal_id=? AND id=?
          AND (deleted_at IS NULL OR deleted_at='')
        """,
        (workspace_id, deal_id, line_id),
    ).fetchone()
    return dict(row) if row else None


def update_deal_line(
    conn: sqlite3.Connection, workspace_id: str, deal_id: int, line_id: int, payload: dict
) -> dict:
    line = get_deal_line(conn, workspace_id, deal_id, line_id)
    if not line:
        raise ValueError("Requirement line not found")
    if not get_deal(conn, workspace_id, deal_id):
        raise ValueError("Deal not found")

    fields = {
        "product_category": "product_category",
        "product_description": "product_description",
        "catalogue": "catalogue",
        "design_code": "design_code",
        "colour": "colour",
        "unit": "unit",
        "room_area": "room_area",
        "notes": "notes",
        "line_status": "line_status",
    }
    sets: list[str] = []
    vals: list[Any] = []
    for key, col in fields.items():
        if key in payload:
            sets.append(f"{col}=?")
            raw = payload.get(key)
            vals.append((str(raw).strip() or None) if raw is not None else None)
    if "qty" in payload:
        sets.append("qty=?")
        vals.append(float(payload.get("qty") or 0))
    if "sort_order" in payload:
        sets.append("sort_order=?")
        vals.append(int(payload.get("sort_order") or 0))
    if not sets:
        return line

    now = _now()
    sets.append("updated_at=?")
    vals.append(now)
    vals.extend([workspace_id, deal_id, line_id])
    conn.execute(
        f"UPDATE hop_deal_lines SET {', '.join(sets)} WHERE workspace_id=? AND deal_id=? AND id=?",
        vals,
    )
    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        event_type="requirement",
        title="Requirement line updated",
        detail=payload.get("product_description")
        or payload.get("product_category")
        or line.get("product_category"),
    )
    conn.execute("UPDATE hop_deals SET updated_at=? WHERE id=?", (now, deal_id))
    conn.commit()
    return get_deal_line(conn, workspace_id, deal_id, line_id) or line


def soft_delete_deal_line(
    conn: sqlite3.Connection, workspace_id: str, deal_id: int, line_id: int
) -> bool:
    line = get_deal_line(conn, workspace_id, deal_id, line_id)
    if not line:
        return False
    now = _now()
    conn.execute(
        """
        UPDATE hop_deal_lines SET deleted_at=?, updated_at=?
        WHERE workspace_id=? AND deal_id=? AND id=?
        """,
        (now, now, workspace_id, deal_id, line_id),
    )
    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        event_type="requirement",
        title="Requirement line removed",
        detail=line.get("product_description") or line.get("product_category"),
    )
    conn.execute("UPDATE hop_deals SET updated_at=? WHERE id=?", (now, deal_id))
    conn.commit()
    return True


def _line_payload_empty(payload: dict) -> bool:
    keys = (
        "item_name",
        "product_category",
        "product_description",
        "description",
        "catalogue",
        "design_code",
        "colour",
        "qty",
        "quantity",
        "unit",
        "room_area",
        "notes",
        "remarks",
    )
    for k in keys:
        v = payload.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)) and float(v) != 0:
            return False
        if str(v).strip():
            return False
    return True


def _normalize_line_payload(payload: dict, *, mode: str) -> dict:
    item = (payload.get("item_name") or payload.get("product_category") or "").strip()
    category = (payload.get("product_category") or "").strip()
    if item and not category and payload.get("item_name"):
        # Keep category separate when item_name provided
        category = (payload.get("product_category") or "").strip()
    desc = (payload.get("product_description") or payload.get("description") or "").strip()
    qty_raw = payload.get("qty", payload.get("quantity"))
    qty = None
    if qty_raw is not None and str(qty_raw).strip() != "":
        try:
            qty = float(qty_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid quantity: {qty_raw}") from exc
    unit = (payload.get("unit") or "").strip() or None
    room = (payload.get("room_area") or "").strip() or None
    status = (payload.get("line_status") or "").strip()
    if not status:
        status = "draft" if mode == "draft" else "pending"
    sample = (payload.get("sample_status") or "").strip() or None
    if sample and sample.lower() in ("pending",):
        sample = "Pending"
    return {
        "id": payload.get("id") or payload.get("line_id"),
        "item_name": (payload.get("item_name") or "").strip() or item or None,
        "product_category": category or None,
        "product_description": desc or None,
        "catalogue": (payload.get("catalogue") or "").strip() or None,
        "design_code": (payload.get("design_code") or "").strip() or None,
        "colour": (payload.get("colour") or "").strip() or None,
        "qty": qty if qty is not None else 0,
        "unit": unit,
        "room_area": room,
        "notes": (payload.get("notes") or payload.get("remarks") or "").strip() or None,
        "line_status": status,
        "sample_status": sample,
        "required_by": (payload.get("required_by") or "").strip() or None,
        "priority": (payload.get("priority") or "").strip() or None,
        "collection": (payload.get("collection") or "").strip() or None,
        "composition": (payload.get("composition") or "").strip() or None,
        "width": (payload.get("width") or "").strip() or None,
        "finish": (payload.get("finish") or "").strip() or None,
        "measurement_notes": (payload.get("measurement_notes") or "").strip() or None,
        "sample_required": 1 if payload.get("sample_required") else 0,
        "customer_remarks": (payload.get("customer_remarks") or "").strip() or None,
        "sort_order": int(payload.get("sort_order") or 0),
        "_client_row": payload.get("_client_row") or payload.get("row_index"),
    }


def _validate_line_for_final(norm: dict, row_index: int) -> str | None:
    itemish = (norm.get("item_name") or norm.get("product_category") or norm.get("product_description") or "").strip()
    if not itemish:
        return f"Row {row_index}: Item or description is required"
    try:
        qty = float(norm.get("qty") if norm.get("qty") is not None else 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return f"Row {row_index}: Quantity is required"
    if not (norm.get("unit") or "").strip():
        return f"Row {row_index}: Unit is required"
    if not (norm.get("room_area") or "").strip():
        return f"Row {row_index}: Room / Area is required"
    return None


def _insert_deal_line_raw(
    conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict, now: str
) -> int:
    cur = conn.execute(
        """
        INSERT INTO hop_deal_lines (
            workspace_id, deal_id, product_category, product_description, catalogue,
            design_code, colour, qty, unit, room_area, notes, line_status, sort_order,
            item_name, sample_status, required_by, priority, collection, composition,
            width, finish, measurement_notes, sample_required, customer_remarks,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            payload.get("product_category"),
            payload.get("product_description"),
            payload.get("catalogue"),
            payload.get("design_code"),
            payload.get("colour"),
            float(payload.get("qty") or 0),
            payload.get("unit"),
            payload.get("room_area"),
            payload.get("notes"),
            payload.get("line_status") or "pending",
            int(payload.get("sort_order") or 0),
            payload.get("item_name"),
            payload.get("sample_status"),
            payload.get("required_by"),
            payload.get("priority"),
            payload.get("collection"),
            payload.get("composition"),
            payload.get("width"),
            payload.get("finish"),
            payload.get("measurement_notes"),
            int(payload.get("sample_required") or 0),
            payload.get("customer_remarks"),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _update_deal_line_raw(
    conn: sqlite3.Connection, workspace_id: str, deal_id: int, line_id: int, payload: dict, now: str
) -> None:
    conn.execute(
        """
        UPDATE hop_deal_lines SET
            product_category=?, product_description=?, catalogue=?, design_code=?, colour=?,
            qty=?, unit=?, room_area=?, notes=?, line_status=?, sort_order=?,
            item_name=?, sample_status=?, required_by=?, priority=?, collection=?,
            composition=?, width=?, finish=?, measurement_notes=?, sample_required=?,
            customer_remarks=?, updated_at=?
        WHERE workspace_id=? AND deal_id=? AND id=? AND (deleted_at IS NULL OR deleted_at='')
        """,
        (
            payload.get("product_category"),
            payload.get("product_description"),
            payload.get("catalogue"),
            payload.get("design_code"),
            payload.get("colour"),
            float(payload.get("qty") or 0),
            payload.get("unit"),
            payload.get("room_area"),
            payload.get("notes"),
            payload.get("line_status") or "pending",
            int(payload.get("sort_order") or 0),
            payload.get("item_name"),
            payload.get("sample_status"),
            payload.get("required_by"),
            payload.get("priority"),
            payload.get("collection"),
            payload.get("composition"),
            payload.get("width"),
            payload.get("finish"),
            payload.get("measurement_notes"),
            int(payload.get("sample_required") or 0),
            payload.get("customer_remarks"),
            now,
            workspace_id,
            deal_id,
            line_id,
        ),
    )


def bulk_save_deal_lines(
    conn: sqlite3.Connection,
    workspace_id: str,
    deal_id: int,
    payload: dict,
    *,
    actor: str | None = None,
) -> dict:
    """Save many requirement lines in one transaction with idempotency + one timeline event."""
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        raise ValueError("Deal not found")

    submission_id = (payload.get("submission_id") or "").strip()
    mode = (payload.get("mode") or "final").strip().lower()
    if mode not in ("draft", "final"):
        raise ValueError("mode must be draft or final")

    if submission_id:
        prev = conn.execute(
            """
            SELECT result_json FROM hop_deal_bulk_submissions
            WHERE submission_id=? AND workspace_id=? AND deal_id=?
            """,
            (submission_id, workspace_id, deal_id),
        ).fetchone()
        if prev:
            import json as _json

            try:
                return _json.loads(prev[0])
            except Exception:
                pass

    raw_lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    delete_ids = payload.get("delete_ids") if isinstance(payload.get("delete_ids"), list) else []
    notes = (payload.get("notes") or "").strip()

    normalized: list[dict] = []
    row_errors: list[dict] = []
    for idx, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict) or _line_payload_empty(raw):
            continue
        try:
            norm = _normalize_line_payload(raw, mode=mode)
        except ValueError as exc:
            row_errors.append({"row": idx, "message": str(exc)})
            continue
        if mode == "final":
            err = _validate_line_for_final(norm, idx)
            if err:
                row_errors.append({"row": idx, "message": err})
                continue
        elif mode == "draft":
            norm["line_status"] = "draft"
        normalized.append(norm)

    if row_errors and mode == "final":
        raise ValueError(
            "Validation failed: "
            + "; ".join(e["message"] for e in row_errors[:8])
        )

    now = _now()
    created = updated = deleted = 0
    incomplete = 0

    for did in delete_ids:
        try:
            lid = int(did)
        except (TypeError, ValueError):
            continue
        line = get_deal_line(conn, workspace_id, deal_id, lid)
        if not line:
            continue
        conn.execute(
            """
            UPDATE hop_deal_lines SET deleted_at=?, updated_at=?
            WHERE workspace_id=? AND deal_id=? AND id=?
            """,
            (now, now, workspace_id, deal_id, lid),
        )
        deleted += 1

    for i, norm in enumerate(normalized):
        norm["sort_order"] = int(norm.get("sort_order") or (i + 1) * 10)
        if mode == "draft":
            incomplete += 1
        lid = norm.get("id")
        if lid not in (None, "", 0, "0"):
            try:
                lid_i = int(lid)
            except (TypeError, ValueError):
                lid_i = None
            if lid_i and get_deal_line(conn, workspace_id, deal_id, lid_i):
                _update_deal_line_raw(conn, workspace_id, deal_id, lid_i, norm, now)
                updated += 1
                continue
        _insert_deal_line_raw(conn, workspace_id, deal_id, norm, now)
        created += 1

    who = (actor or deal.get("assigned_to") or "team").strip() or "team"
    if created or updated or deleted:
        if created and not updated and not deleted:
            title = f"{created} requirement line{'s' if created != 1 else ''} added by {who}"
        elif updated or deleted or created:
            parts = []
            if updated:
                parts.append(f"{updated} edited")
            if created:
                parts.append(f"{created} added")
            if deleted:
                parts.append(f"{deleted} removed")
            title = f"Requirements updated: {', '.join(parts)}"
        else:
            title = "Requirements saved"
        detail = notes or (f"mode={mode}" if mode == "draft" else None)
        add_deal_event(
            conn,
            workspace_id,
            deal_id,
            event_type="requirement",
            title=title,
            detail=detail,
        )

    conn.execute("UPDATE hop_deals SET updated_at=? WHERE id=?", (now, deal_id))

    result = {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "incomplete": incomplete if mode == "draft" else 0,
        "mode": mode,
        "lines": list_deal_lines(conn, workspace_id, deal_id),
    }

    if submission_id:
        import json as _json

        conn.execute(
            """
            INSERT OR REPLACE INTO hop_deal_bulk_submissions
                (submission_id, workspace_id, deal_id, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (submission_id, workspace_id, deal_id, _json.dumps(result), now),
        )

    conn.commit()
    return result


# ---------- Activities ----------
def list_activities(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM hop_deal_activities
        WHERE workspace_id=? AND deal_id=? AND (deleted_at IS NULL OR deleted_at='')
        ORDER BY activity_at DESC, id DESC
        """,
        (workspace_id, deal_id),
    ).fetchall()
    return [dict(r) for r in rows]


def log_activity(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    deal = get_deal(conn, workspace_id, deal_id)
    if not deal:
        raise ValueError("Deal not found")
    atype = (payload.get("activity_type") or "").strip()
    if not atype:
        raise ValueError("activity_type required")
    if atype not in DEAL_ACTIVITY_TYPES:
        # Custom types allowed — normalize to a stable slug
        cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-", " ") else "" for ch in atype).strip()
        if not cleaned or len(cleaned) > 80:
            raise ValueError("Invalid activity_type")
        atype = cleaned.lower().replace(" ", "_").replace("-", "_")
    outcome = (payload.get("outcome") or "").strip() or None
    no_follow = 1 if payload.get("no_follow_up") else 0
    next_type = (payload.get("next_action_type") or "").strip() or None
    next_due = (payload.get("next_action_due") or "").strip() or None
    if not no_follow and not next_type:
        raise ValueError("After activity: set next action OR mark no follow-up required")

    now = _now()
    activity_at = (payload.get("activity_at") or now).strip()
    cur = conn.execute(
        """
        INSERT INTO hop_deal_activities (
            workspace_id, deal_id, activity_type, activity_at, performed_by, contact_person,
            outcome, related_stage, note, next_action_type, next_action_due, no_follow_up,
            attachment_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            atype,
            activity_at,
            (payload.get("performed_by") or deal.get("assigned_to") or "").strip() or None,
            (payload.get("contact_person") or "").strip() or None,
            outcome,
            deal.get("current_stage"),
            (payload.get("note") or "").strip() or None,
            next_type,
            next_due,
            no_follow,
            (payload.get("attachment_name") or "").strip() or None,
            now,
        ),
    )
    aid = int(cur.lastrowid)

    # Update deal next action + last touch
    if no_follow:
        until = next_due or (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
        conn.execute(
            """
            UPDATE hop_deals SET no_action_until=?, next_action_type=NULL, next_action_due=NULL,
                updated_at=?, current_sub_status=COALESCE(?, current_sub_status)
            WHERE workspace_id=? AND id=?
            """,
            (until, now, outcome, workspace_id, deal_id),
        )
    else:
        conn.execute(
            """
            UPDATE hop_deals SET next_action_type=?, next_action_due=?, no_action_until=NULL,
                next_action_owner=COALESCE(?, next_action_owner, assigned_to),
                updated_at=?, current_sub_status=COALESCE(?, current_sub_status)
            WHERE workspace_id=? AND id=?
            """,
            (
                next_type,
                next_due,
                (payload.get("performed_by") or None),
                now,
                outcome,
                workspace_id,
                deal_id,
            ),
        )

    add_deal_event(
        conn,
        workspace_id,
        deal_id,
        event_type="activity",
        title=f"{atype.replace('_', ' ').title()}" + (f" · {outcome}" if outcome else ""),
        detail=payload.get("note"),
    )
    refreshed = get_deal(conn, workspace_id, deal_id) or {}
    h = compute_deal_health(refreshed)
    conn.execute(
        "UPDATE hop_deals SET deal_health=? WHERE workspace_id=? AND id=?",
        (h, workspace_id, deal_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM hop_deal_activities WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else {"id": aid}


# ---------- Appointments ----------
def list_appointments(conn: sqlite3.Connection, workspace_id: str, deal_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM hop_deal_appointments
        WHERE workspace_id=? AND deal_id=? AND (deleted_at IS NULL OR deleted_at='')
        ORDER BY scheduled_at DESC, id DESC
        """,
        (workspace_id, deal_id),
    ).fetchall()
    return [dict(r) for r in rows]


def create_appointment(conn: sqlite3.Connection, workspace_id: str, deal_id: int, payload: dict) -> dict:
    if not get_deal(conn, workspace_id, deal_id):
        raise ValueError("Deal not found")
    scheduled = (payload.get("scheduled_at") or "").strip()
    if not scheduled:
        raise ValueError("scheduled_at required")
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO hop_deal_appointments (
            workspace_id, deal_id, title, scheduled_at, location, appt_status, outcome, notes,
            created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            deal_id,
            (payload.get("title") or "Appointment").strip() or "Appointment",
            scheduled,
            (payload.get("location") or "").strip() or None,
            (payload.get("appt_status") or "scheduled").strip() or "scheduled",
            (payload.get("outcome") or "").strip() or None,
            (payload.get("notes") or "").strip() or None,
            (payload.get("created_by") or "").strip() or None,
            now,
            now,
        ),
    )
    aid = int(cur.lastrowid)
    add_deal_event(
        conn, workspace_id, deal_id,
        event_type="appointment",
        title="Appointment scheduled",
        detail=scheduled,
    )
    conn.execute(
        """
        UPDATE hop_deals SET next_action_type='Attend appointment', next_action_due=?,
            current_sub_status='meeting_booked', updated_at=?
        WHERE workspace_id=? AND id=?
        """,
        (scheduled[:10], now, workspace_id, deal_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM hop_deal_appointments WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else {"id": aid}


def attention_deals(conn: sqlite3.Connection, workspace_id: str) -> dict[str, list]:
    rows = list_deals(conn, workspace_id, status="open")
    holds = list_deals(conn, workspace_id, status="on_hold")
    needs = []
    followups = []
    no_activity = []
    today = datetime.now(timezone.utc).date().isoformat()
    for r in rows + holds:
        h = r.get("deal_health") or compute_deal_health(r)
        r["deal_health"] = h
        if h in ("needs_attention", "at_risk", "critical"):
            needs.append(r)
        due = (r.get("next_action_due") or "")[:10]
        if due and due <= today:
            followups.append(r)
        updated = (r.get("updated_at") or "")[:10]
        if updated and updated < (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat():
            no_activity.append(r)
    return {
        "needs_attention": needs,
        "followups_due": followups,
        "no_recent_activity": no_activity,
    }
