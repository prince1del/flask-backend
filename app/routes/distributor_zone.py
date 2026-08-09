"""Distributor Zone — per-distributor working cards (primary / secondary / feedbacks).

GET /api/v1/executive/distributor-zone?year_id=
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from app.fiscal_year import normalize_fiscal_year
from app.routes.auth import get_workspace_id, require_jwt_auth, require_role
from centralized_db_system.db import CentralizedDB

distributor_zone_bp = Blueprint(
    "distributor_zone",
    __name__,
    url_prefix="/api/v1/executive",
)

_MONTH_SHORT = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _db() -> CentralizedDB:
    return CentralizedDB(current_app.config["DATABASE_PATH"])


def _user() -> dict:
    return getattr(request, "user", None) or {}


def _user_id() -> int | None:
    raw = _user().get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _lakhs_to_rupees(lakhs) -> float:
    try:
        return float(lakhs or 0) * 100_000.0
    except (TypeError, ValueError):
        return 0.0


def _narrate_inr(rupees: float) -> str:
    n = int(round(float(rupees or 0)))
    if n <= 0:
        return "0"
    crore = n // 10_000_000
    rem = n % 10_000_000
    lakh = rem // 100_000
    parts: list[str] = []
    if crore:
        parts.append(f"{crore} Crore")
    if lakh:
        parts.append(f"{lakh} Lakh")
    if not parts:
        return f"₹{n:,}"
    return " ".join(parts)


def _money_from_lakhs(lakhs) -> dict[str, Any]:
    rupees = _lakhs_to_rupees(lakhs)
    return {
        "lakhs": float(lakhs or 0),
        "rupees": rupees,
        "narration": _narrate_inr(rupees),
    }


def _norm_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _parse_amount_lacs(raw: Any) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text.lower() == "null":
        return 0.0
    try:
        return float(text)
    except ValueError:
        m = re.search(r"[\d.]+", text)
        return float(m.group(0)) if m else 0.0


def _fy_bounds_from_label(fy_label: str) -> tuple[str, str] | None:
    """Indian FY Apr–Mar → (from_date, to_date)."""
    label = (fy_label or "").strip()
    m = re.match(r"^(\d{4})\s*[-–/]\s*(\d{2,4})$", label)
    if not m:
        return None
    start = int(m.group(1))
    end_raw = m.group(2)
    end = int(end_raw) if len(end_raw) == 4 else 2000 + int(end_raw)
    if end < start:
        end = start + 1
    return f"{start}-04-01", f"{end}-03-31"


def _pick_year(db: CentralizedDB, workspace_id: str, year_id: int | None) -> dict | None:
    db.ensure_target_achievement_tables()
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        if year_id:
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?",
                (year_id, workspace_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM target_achievement_years WHERE workspace_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
    if not row:
        return None
    data = dict(row)
    raw = data.get("financial_year") or data.get("year") or ""
    label = normalize_fiscal_year(raw) or raw or ""
    data["fy_label"] = label
    data["year_id"] = int(data["id"])
    return data


def _breakup_index(breakup: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_code: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for row in breakup:
        code = str(row.get("distributor_id") or "").strip()
        if code:
            by_code[code.lower()] = row
        for key in (
            row.get("distributor_name"),
            row.get("display_label"),
            row.get("source_distributor_name"),
            row.get("nick"),
        ):
            n = _norm_name(key if isinstance(key, str) else None)
            if n and n != "others":
                by_name[n] = row
    return by_code, by_name


def _match_breakup(
    dist: dict,
    by_code: dict[str, dict],
    by_name: dict[str, dict],
) -> dict | None:
    code = str(dist.get("distributor_id") or dist.get("distributor_code") or "").strip()
    if code and code.lower() in by_code:
        return by_code[code.lower()]
    for key in (dist.get("firm_name"), dist.get("name"), dist.get("firm_nick_name")):
        n = _norm_name(key if isinstance(key, str) else None)
        if n and n in by_name:
            return by_name[n]
    return None


def _load_dsr_secondary_and_feedback(
    workspace_id: str,
    from_date: str | None,
    to_date: str | None,
) -> tuple[dict[int, dict[str, float]], dict[int, list[dict]], dict[str, dict[str, float]], dict[str, list[dict]]]:
    """Returns (by_id months, by_id feedbacks, by_name months, by_name feedbacks)."""
    months_by_id: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    feedbacks_by_id: dict[int, list[dict]] = defaultdict(list)
    months_by_name: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    feedbacks_by_name: dict[str, list[dict]] = defaultdict(list)

    db_path = current_app.config["DATABASE_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Table may not exist on brand-new DBs.
        try:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(dsr_market_visits)").fetchall()
            }
        except sqlite3.OperationalError:
            return {}, {}, {}, {}
        if not cols:
            return {}, {}, {}, {}

        query = "SELECT * FROM dsr_market_visits WHERE workspace_id = ?"
        params: list[Any] = [workspace_id]
        if from_date:
            query += " AND visit_date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND visit_date <= ?"
            params.append(to_date)
        uid = _user_id()
        role = (_user().get("role") or "").strip().lower()
        # Sales exec sees own DSR; admin sees workspace.
        if role != "admin" and request.args.get("all") != "1" and uid is not None:
            query += " AND (user_id = ? OR user_id IS NULL)"
            params.append(uid)
        query += " ORDER BY visit_date DESC, id DESC LIMIT 2000"
        try:
            rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            return {}, {}, {}, {}

    for row in rows:
        d = dict(row)
        linked_id = d.get("linked_distributor_id")
        try:
            linked_id_int = int(linked_id) if linked_id not in (None, "") else None
        except (TypeError, ValueError):
            linked_id_int = None
        cust_name = _norm_name(d.get("customer_name") or d.get("party_name"))

        intel_raw = d.get("visit_intel_json")
        intel: dict = {}
        if isinstance(intel_raw, str) and intel_raw.strip():
            try:
                parsed = json.loads(intel_raw)
                if isinstance(parsed, dict):
                    intel = parsed
            except json.JSONDecodeError:
                intel = {}
        elif isinstance(intel_raw, dict):
            intel = intel_raw

        entries = intel.get("secondary_sales_entries") or []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                try:
                    year = int(entry.get("year") or 0)
                    month = int(entry.get("month") or 0)
                except (TypeError, ValueError):
                    continue
                if year < 2000 or month not in range(1, 13):
                    continue
                amount = _parse_amount_lacs(entry.get("amount_lacs"))
                if amount <= 0:
                    continue
                key = f"{year:04d}-{month:02d}"
                if linked_id_int:
                    months_by_id[linked_id_int][key] += amount
                if cust_name:
                    months_by_name[cust_name][key] += amount

        fb = (d.get("retailer_feedback") or "").strip()
        sm = (d.get("sm_remarks") or "").strip()
        text = fb or sm
        if text:
            item = {
                "visit_id": d.get("id"),
                "visit_date": d.get("visit_date"),
                "text": text[:400],
                "source": "retailer_feedback" if fb else "sm_remarks",
            }
            if linked_id_int:
                if len(feedbacks_by_id[linked_id_int]) < 5:
                    feedbacks_by_id[linked_id_int].append(item)
            if cust_name and len(feedbacks_by_name[cust_name]) < 5:
                feedbacks_by_name[cust_name].append(item)

    return months_by_id, feedbacks_by_id, months_by_name, feedbacks_by_name


def _month_rows(month_map: dict[str, float]) -> list[dict]:
    rows = []
    for key, lakhs in sorted(month_map.items(), reverse=True):
        try:
            y_s, m_s = key.split("-")
            year = int(y_s)
            month = int(m_s)
        except (ValueError, TypeError):
            continue
        money = _money_from_lakhs(lakhs)
        rows.append(
            {
                "year": year,
                "month": month,
                "month_key": key,
                "label": f"{_MONTH_SHORT[month]} {year}",
                "amount_lakhs": money["lakhs"],
                "amount_rupees": money["rupees"],
                "amount_narration": money["narration"],
            }
        )
    return rows


@distributor_zone_bp.route("/distributor-zone", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def distributor_zone():
    """Individual distributor working cards: primary FY, secondary M/Y, feedbacks."""
    try:
        workspace_id = get_workspace_id()
        db = _db()
        year_id = request.args.get("year_id", type=int)
        year = _pick_year(db, workspace_id, year_id)
        fy_label = (year or {}).get("fy_label") or ""
        resolved_year_id = (year or {}).get("year_id")

        distributors = db.list_master_distributors(
            limit=5000,
            workspace_id=workspace_id,
            offset=0,
            include_inactive=True,
        )

        breakup: list[dict] = []
        if resolved_year_id:
            breakup = db.list_target_distributor_breakup(workspace_id, int(resolved_year_id))
        by_code, by_name = _breakup_index(breakup)

        bounds = _fy_bounds_from_label(fy_label) if fy_label else None
        from_date, to_date = bounds if bounds else (None, None)
        months_by_id, fb_by_id, months_by_name, fb_by_name = _load_dsr_secondary_and_feedback(
            workspace_id, from_date, to_date
        )

        cards: list[dict] = []
        for dist in distributors:
            pk = dist.get("id")
            try:
                pk_int = int(pk) if pk is not None else None
            except (TypeError, ValueError):
                pk_int = None
            name = (
                (dist.get("firm_name") or dist.get("name") or "").strip()
                or f"Distributor #{pk}"
            )
            nick = (dist.get("firm_nick_name") or "").strip() or None
            status = (dist.get("status") or "active").strip().lower() or "active"

            matched = _match_breakup(dist, by_code, by_name)
            target_lakhs = float((matched or {}).get("target_lakhs") or 0)
            ach_lakhs = float((matched or {}).get("achievement_lakhs") or 0)
            if matched and ach_lakhs <= 0:
                ach_lakhs = (
                    float(matched.get("achievement_excel") or 0)
                    + float(matched.get("achievement_ci") or 0)
                    + float(matched.get("achievement_manual") or 0)
                )
            pct = round((ach_lakhs / target_lakhs) * 100, 1) if target_lakhs > 0 else 0.0
            target_money = _money_from_lakhs(target_lakhs)
            ach_money = _money_from_lakhs(ach_lakhs)

            month_map: dict[str, float] = {}
            if pk_int and pk_int in months_by_id:
                month_map = dict(months_by_id[pk_int])
            else:
                n = _norm_name(name)
                if n and n in months_by_name:
                    month_map = dict(months_by_name[n])
            secondary_months = _month_rows(month_map)
            secondary_year_lakhs = sum(month_map.values())
            secondary_year_money = _money_from_lakhs(secondary_year_lakhs)

            feedbacks: list[dict] = []
            if pk_int and pk_int in fb_by_id:
                feedbacks = list(fb_by_id[pk_int])
            else:
                n = _norm_name(name)
                if n and n in fb_by_name:
                    feedbacks = list(fb_by_name[n])

            # Latest secondary month snapshot for card face.
            latest_secondary = secondary_months[0] if secondary_months else None

            cards.append(
                {
                    "id": pk_int,
                    "distributor_code": dist.get("distributor_code") or dist.get("distributor_id"),
                    "name": name,
                    "nick": nick,
                    "city": (dist.get("location") or "").strip() or None,
                    "zone": (dist.get("zone") or "").strip() or None,
                    "phone": (dist.get("phone_number") or "").strip() or None,
                    "status": status,
                    "is_active": status != "inactive",
                    "primary_year": {
                        "fy_label": fy_label or None,
                        "year_id": resolved_year_id,
                        "target_lakhs": target_money["lakhs"],
                        "target_rupees": target_money["rupees"],
                        "target_narration": target_money["narration"],
                        "achievement_lakhs": ach_money["lakhs"],
                        "achievement_rupees": ach_money["rupees"],
                        "achievement_narration": ach_money["narration"],
                        "percentage": pct,
                        # Month-grain primary sell-in not yet on JWT path — reserved.
                        "months": [],
                    },
                    "secondary_year": {
                        "fy_label": fy_label or None,
                        "amount_lakhs": secondary_year_money["lakhs"],
                        "amount_rupees": secondary_year_money["rupees"],
                        "amount_narration": secondary_year_money["narration"],
                    },
                    "secondary_months": secondary_months[:12],
                    "latest_secondary_month": latest_secondary,
                    "important_feedbacks": feedbacks[:3],
                }
            )

        # Active first, then name.
        cards.sort(
            key=lambda c: (
                0 if c.get("is_active") else 1,
                (c.get("name") or "").lower(),
            )
        )

        years_out: list[dict] = []
        with sqlite3.connect(db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                yrows = conn.execute(
                    "SELECT id, financial_year, year FROM target_achievement_years "
                    "WHERE workspace_id = ? ORDER BY id DESC",
                    (workspace_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                yrows = []
        for yr in yrows:
            raw = yr["financial_year"] or yr["year"] or ""
            years_out.append(
                {
                    "id": int(yr["id"]),
                    "fy_label": normalize_fiscal_year(raw) or raw,
                }
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "year_id": resolved_year_id,
                    "fy_label": fy_label or None,
                    "years": years_out,
                    "unit": "lakhs",
                    "cards": cards,
                    "count": len(cards),
                },
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "error": {"message": str(exc), "code": "DISTRIBUTOR_ZONE_ERROR"},
            }
        ), 500
