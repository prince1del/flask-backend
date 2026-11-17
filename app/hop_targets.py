"""Sales targets — yearly (financial year, April to March) AND monthly.

* Yearly target: period ``FY2026-27``. Monthly target: period ``2026-04`` … (same hop_targets table).
* A month without its own target shows its share of the yearly target (yearly ÷ 12) marked "from yearly".
* Achieved = sale invoices − sale returns of that period (by bill date, deleted bills not counted) — real billing,
  not CRM orders.
* Also: what is still needed per remaining month to reach the yearly target.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any

MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]


def fy_of(d: date) -> int:
    """Start year of the financial year that contains ``d`` (Apr–Mar)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start: int) -> str:
    return f"FY{start}-{str(start + 1)[-2:]}"


def parse_period(p: str) -> str:
    p = (p or "").strip().upper().replace(" ", "")
    m = re.fullmatch(r"FY(\d{4})-(\d{2})", p)
    if m:
        s = int(m.group(1))
        if int(m.group(2)) != (s + 1) % 100:
            raise ValueError("Year must look like FY2026-27")
        return f"FY{s}-{m.group(2)}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", p)
    if m and 1 <= int(m.group(2)) <= 12:
        return p
    raise ValueError("Period must be a month (2026-11) or a financial year (FY2026-27)")


def _achieved(conn: sqlite3.Connection, ws: str, start: str, end: str) -> float:
    r = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN txn_type=21 THEN -total_amount ELSE total_amount END), 0)
           FROM hop_party_transactions WHERE workspace_id=? AND txn_type IN (1, 21)
             AND (deleted_at IS NULL OR trim(deleted_at)='') AND date(txn_date) BETWEEN date(?) AND date(?)""",
        (ws, start, end)).fetchone()
    return round(float(r[0] or 0), 2)


def _targets(conn, ws) -> dict[str, float]:
    return {r[0]: float(r[1] or 0) for r in conn.execute(
        "SELECT period_label, target_amount FROM hop_targets WHERE workspace_id=?", (ws,)).fetchall()}


def summary(conn: sqlite3.Connection, ws: str, fy_start: int | None = None, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    fy = fy_start if fy_start is not None else fy_of(today)
    t = _targets(conn, ws)
    yearly = t.get(fy_label(fy), 0.0)
    months = []
    for i, name in enumerate(MONTHS):
        y, m = (fy, 4 + i) if i < 9 else (fy + 1, i - 8)
        key = f"{y}-{m:02d}"
        start = date(y, m, 1)
        end = date(y + (m == 12), (m % 12) + 1, 1)
        end = date.fromordinal(end.toordinal() - 1)
        own = t.get(key)
        target = own if own is not None else (round(yearly / 12, 2) if yearly else 0.0)
        ach = _achieved(conn, ws, start.isoformat(), end.isoformat()) if start <= today else 0.0
        months.append({"period": key, "label": f"{name} {y}", "target": target, "from_yearly": own is None and bool(yearly),
                       "achieved": ach, "pct": round(ach / target * 100, 1) if target else None,
                       "current": start <= today <= end, "future": start > today})
    ytd = round(sum(m["achieved"] for m in months), 2)
    cur = next((m for m in months if m["current"]), None)
    left = [m for m in months if m["future"] or m["current"]]
    need = max(yearly - sum(m["achieved"] for m in months if not (m["future"] or m["current"])), 0.0)
    return {"fy": fy_label(fy), "fy_start": fy, "yearly_target": yearly, "achieved_ytd": ytd,
            "yearly_pct": round(ytd / yearly * 100, 1) if yearly else None, "gap": round(max(yearly - ytd, 0), 2),
            "per_month_needed": round(need / len(left), 2) if yearly and left else None,
            "current_month": cur, "months": months}


def set_target(conn: sqlite3.Connection, ws: str, period: str, amount: float) -> dict[str, Any]:
    from app import hop_ops

    p = parse_period(period)
    if amount < 0:
        raise ValueError("Target cannot be negative")
    if amount == 0:
        conn.execute("DELETE FROM hop_targets WHERE workspace_id=? AND period_label=?", (ws, p))
        conn.commit()
        return {"period_label": p, "deleted": True}
    return hop_ops.upsert_target(conn, ws, p, amount)


def split_yearly(conn: sqlite3.Connection, ws: str, fy_start: int, amount: float) -> None:
    """Yearly target + the same share in every month (owner can change any month after)."""
    set_target(conn, ws, fy_label(fy_start), amount)
    for i in range(12):
        y, m = (fy_start, 4 + i) if i < 9 else (fy_start + 1, i - 8)
        set_target(conn, ws, f"{y}-{m:02d}", round(amount / 12, 2))
