"""Salary — staff, monthly salary sheet, advances (owner 20-11: "salary wala option … wire karo jaha transaction
details matter karti hai"). Same books engine for the Business and the Distributor UI (each in its own firm).

* **Staff**: name, phone, designation, monthly or daily pay, joining date, bank / UPI, optional NEXORA login
  (then present days come from Team duty attendance).
* **Salary sheet** for a month: days present (attendance or typed), earned, bonus, deductions, advance recovered,
  net. **Pay** saves an Expense (category "Salary") from cash or a bank account — so salary is in Cash & Bank, the
  Day book, P&L, cash flow, expense reports, distributor profitability and NEXORA Guard like every other payment.
* **Advance**: an Expense in category "Salary advance" (money out today); it is recovered from a later salary, so
  advance + net paid = the month's salary cost.
* ✨ Checks: paid twice, net below zero, a big jump from last month, an advance bigger than a month's pay,
  present days more than the month. Owner-only area "salary" (staff need the Salary permission).
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

SALARY_CAT, ADVANCE_CAT = "Salary", "Salary advance"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(float(v or 0) + 0.0, 2)


def _s(v: Any) -> str:
    return str(v or "").strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hop_staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT,
            designation TEXT, pay_type TEXT NOT NULL DEFAULT 'monthly', salary REAL NOT NULL DEFAULT 0,
            joined_on TEXT, left_on TEXT, bank_details TEXT, user_id INTEGER, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_hop_staff_ws ON hop_staff(workspace_id, active);
        CREATE TABLE IF NOT EXISTS hop_salary_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, staff_id INTEGER NOT NULL,
            month TEXT NOT NULL, days_in_month INTEGER, present_days REAL, earned REAL, bonus REAL, deductions REAL,
            advance_recovered REAL, net REAL, paid_on TEXT, payment_mode TEXT, expense_txn_id INTEGER, note TEXT,
            by_user TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS ix_hop_salary_runs ON hop_salary_runs(workspace_id, month, staff_id);
        CREATE TABLE IF NOT EXISTS hop_salary_advances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, staff_id INTEGER NOT NULL,
            given_on TEXT NOT NULL, amount REAL NOT NULL, payment_mode TEXT, expense_txn_id INTEGER, note TEXT,
            by_user TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
    """)


# ── staff ─────────────────────────────────────────────────────────────────────────────────────────────────
_STAFF_COLS = ("name", "phone", "designation", "pay_type", "salary", "joined_on", "left_on", "bank_details", "user_id", "active")


def list_staff(conn: sqlite3.Connection, ws: str, include_left: bool = False) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql = "SELECT * FROM hop_staff WHERE workspace_id=?" + ("" if include_left else " AND active=1")
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY lower(name)", (ws,)).fetchall()]
    adv = advances_due(conn, ws)
    for r in rows:
        r["advance_due"] = adv.get(int(r["id"]), 0.0)
    return rows


def save_staff(conn: sqlite3.Connection, ws: str, p: dict, staff_id: int | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    vals: dict[str, Any] = {}
    for k in _STAFF_COLS:
        if k not in p:
            continue
        v = p.get(k)
        if k == "salary":
            vals[k] = max(0.0, _f(v))
        elif k in ("user_id",):
            vals[k] = int(v) if v not in (None, "", 0, "0") else None
        elif k == "active":
            vals[k] = 1 if v in (True, 1, "1", "true") else 0
        elif k == "pay_type":
            vals[k] = "daily" if _s(v).lower() == "daily" else "monthly"
        else:
            vals[k] = _s(v)[:160] or None
    if staff_id is None:
        if not _s(p.get("name")):
            raise ValueError("Type the staff member's name")
        vals.setdefault("pay_type", "monthly")
        vals.setdefault("salary", 0.0)
        vals.setdefault("active", 1)
        cols = ["workspace_id", *vals, "created_at", "updated_at"]
        cur = conn.execute(f"INSERT INTO hop_staff ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [ws, *vals.values(), _now(), _now()])
        staff_id = int(cur.lastrowid)
    else:
        if not conn.execute("SELECT 1 FROM hop_staff WHERE workspace_id=? AND id=?", (ws, int(staff_id))).fetchone():
            raise LookupError("Staff member not found")
        if "name" in vals and not vals["name"]:
            raise ValueError("Type the staff member's name")
        if vals:
            conn.execute(f"UPDATE hop_staff SET {', '.join(f'{k}=?' for k in vals)}, updated_at=? WHERE workspace_id=? AND id=?",
                         [*vals.values(), _now(), ws, int(staff_id)])
    conn.commit()
    return dict(conn.execute("SELECT * FROM hop_staff WHERE id=?", (int(staff_id),)).fetchone())


# ── money: every salary / advance is an Expense (cash or bank) ───────────────────────────────────────────
def _category_id(conn, ws: str, name: str) -> int:
    from app import hop_money

    for c in hop_money.list_expense_categories(conn, ws):
        if _s(c.get("name")).lower() == name.lower():
            return int(c["id"])
    return int(hop_money.create_expense_category(conn, ws, name)["id"])


def _expense(conn, ws: str, category: str, amount: float, on: str, mode: str, account_id: Any, note: str) -> int:
    from app.hop_doc_preview import create_manual_party_document

    p: dict[str, Any] = {"txn_type": 7, "expense_category_id": _category_id(conn, ws, category), "amount": _r2(amount),
                         "txn_date": on, "payment_mode": mode or "cash", "notes": note[:300]}
    if account_id:
        p["payment_account_id"] = int(account_id)
    out = create_manual_party_document(conn, ws, p)
    return int(out["party_txn_id"])


def _undo_expense(conn, ws: str, txn_id: Any) -> None:
    if txn_id:
        from app.hop_doc_preview import delete_manual_party_document

        try:
            delete_manual_party_document(conn, ws, int(txn_id))
        except LookupError:
            pass


# ── advances ──────────────────────────────────────────────────────────────────────────────────────────────
def advances_due(conn: sqlite3.Connection, ws: str) -> dict[int, float]:
    ensure_schema(conn)
    given = {int(r[0]): _f(r[1]) for r in conn.execute(
        "SELECT staff_id, SUM(amount) FROM hop_salary_advances WHERE workspace_id=? AND deleted_at IS NULL GROUP BY staff_id", (ws,))}
    back = {int(r[0]): _f(r[1]) for r in conn.execute(
        "SELECT staff_id, SUM(advance_recovered) FROM hop_salary_runs WHERE workspace_id=? AND deleted_at IS NULL GROUP BY staff_id", (ws,))}
    return {k: _r2(v - back.get(k, 0.0)) for k, v in given.items() if v - back.get(k, 0.0) > 0.004}


def give_advance(conn: sqlite3.Connection, ws: str, p: dict, *, user: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    sid, amount = int(p.get("staff_id") or 0), _f(p.get("amount"))
    st = conn.execute("SELECT name, salary, pay_type FROM hop_staff WHERE workspace_id=? AND id=?", (ws, sid)).fetchone()
    if not st:
        raise LookupError("Staff member not found")
    if amount <= 0:
        raise ValueError("Type the advance amount")
    on = _s(p.get("date"))[:10] or _today().isoformat()
    txn = _expense(conn, ws, ADVANCE_CAT, amount, on, _s(p.get("payment_mode")) or "cash", p.get("payment_account_id"),
                   "Salary advance")   # no name on the expense: who earns what stays in Salary
    conn.execute("INSERT INTO hop_salary_advances (workspace_id, staff_id, given_on, amount, payment_mode, expense_txn_id, note, "
                 "by_user, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ws, sid, on, _r2(amount), _s(p.get("payment_mode")) or "cash", txn, _s(p.get("note"))[:200] or None,
                  user or None, _now()))
    conn.commit()
    warn = []
    month_pay = _f(st["salary"]) * (26 if st["pay_type"] == "daily" else 1)
    due = advances_due(conn, ws).get(sid, 0.0)
    if month_pay and due > month_pay:
        warn.append(f"{st['name']} now owes ₹{due:,.0f} in advances — more than one month's pay (₹{month_pay:,.0f})")
    return {"advance_due": due, "expense_txn_id": txn, "checks": warn}


def list_advances(conn: sqlite3.Connection, ws: str, staff_id: int | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql = ("SELECT a.*, s.name FROM hop_salary_advances a JOIN hop_staff s ON s.id=a.staff_id "
           "WHERE a.workspace_id=? AND a.deleted_at IS NULL")
    args: list[Any] = [ws]
    if staff_id:
        sql += " AND a.staff_id=?"; args.append(int(staff_id))
    return [dict(r) for r in conn.execute(sql + " ORDER BY a.given_on DESC, a.id DESC LIMIT 300", args).fetchall()]


def delete_advance(conn: sqlite3.Connection, ws: str, adv_id: int) -> None:
    r = conn.execute("SELECT expense_txn_id FROM hop_salary_advances WHERE workspace_id=? AND id=? AND deleted_at IS NULL",
                     (ws, int(adv_id))).fetchone()
    if not r:
        raise LookupError("Advance not found")
    _undo_expense(conn, ws, r[0])
    conn.execute("UPDATE hop_salary_advances SET deleted_at=? WHERE id=?", (_now(), int(adv_id)))
    conn.commit()


# ── attendance (Team duty) → present days ───────────────────────────────────────────────────────────────
def _attendance_days(db_path: str | None, ws_base: str, user_id: int | None, month: str) -> float | None:
    """Present days of a linked NEXORA login (P = 1, H = ½). None when not linked / no attendance."""
    if not user_id or not db_path:
        return None
    try:
        from app.services import team_tracking as tt

        class _Db:  # attendance_month wants an object with db_path
            pass
        db = _Db()
        db.db_path = db_path
        out = tt.attendance_month(db, None, month, self_user={"user_id": int(user_id), "workspace_id": ws_base})
        people = out.get("people") or []
        if not people or people[0].get("not_tracked"):
            return None
        return float(people[0].get("present") or 0) + 0.5 * float(people[0].get("half") or 0)
    except Exception:
        return None


# ── the month's salary sheet ─────────────────────────────────────────────────────────────────────────────
def _month_days(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def _check_month(month: str) -> str:
    try:
        datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError:
        raise ValueError("month must be YYYY-MM") from None
    return month


def month_sheet(conn: sqlite3.Connection, ws: str, month: str = "", *, db_path: str | None = None,
                base_ws: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    month = _check_month(month or _today().strftime("%Y-%m"))
    dim = _month_days(month)
    prev = (datetime.strptime(month + "-01", "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m")
    runs = {int(r["staff_id"]): dict(r) for r in conn.execute(
        "SELECT * FROM hop_salary_runs WHERE workspace_id=? AND month=? AND deleted_at IS NULL", (ws, month))}
    last = {int(r[0]): _f(r[1]) for r in conn.execute(
        "SELECT staff_id, SUM(net + COALESCE(advance_recovered,0)) FROM hop_salary_runs WHERE workspace_id=? AND month=? "
        "AND deleted_at IS NULL GROUP BY staff_id", (ws, prev))}
    adv = advances_due(conn, ws)
    rows, checks = [], []
    for s in list_staff(conn, ws):
        if s.get("joined_on") and s["joined_on"][:7] > month:
            continue
        sid = int(s["id"])
        run = runs.get(sid)
        att = _attendance_days(db_path, base_ws or ws, s.get("user_id"), month)
        present = _f(run["present_days"]) if run else (att if att is not None else (dim if s["pay_type"] == "monthly" else 0.0))
        pay = _f(s["salary"])
        earned = _r2(pay * present / dim) if s["pay_type"] == "monthly" else _r2(pay * present)
        row = {"staff_id": sid, "name": s["name"], "designation": s.get("designation"), "pay_type": s["pay_type"],
               "salary": pay, "days_in_month": dim, "present_days": present, "attendance_days": att,
               "earned": _r2(run["earned"]) if run else earned, "advance_due": adv.get(sid, 0.0),
               "paid": bool(run), "run": run, "last_month": last.get(sid)}
        rows.append(row)
        gross = row["earned"] + (_f(run["bonus"]) if run else 0) - (_f(run["deductions"]) if run else 0)
        if row["last_month"] and gross and abs(gross - row["last_month"]) / row["last_month"] > 0.25:
            checks.append(f"{s['name']}: ₹{gross:,.0f} this month vs ₹{row['last_month']:,.0f} last month — check")
        if row["advance_due"] and pay and row["advance_due"] > (pay if s["pay_type"] == "monthly" else pay * 26):
            checks.append(f"{s['name']} owes ₹{row['advance_due']:,.0f} in advances — more than a month's pay")
    paid = [r for r in rows if r["paid"]]
    return {"month": month, "days_in_month": dim, "staff": rows, "checks": checks,
            "totals": {"staff": len(rows), "paid": len(paid), "to_pay": len(rows) - len(paid),
                       "net_paid": _r2(sum(_f(r["run"]["net"]) for r in paid)),
                       "advance_recovered": _r2(sum(_f(r["run"]["advance_recovered"]) for r in paid)),
                       "salary_cost": _r2(sum(_f(r["run"]["net"]) + _f(r["run"]["advance_recovered"]) for r in paid)),
                       "advances_due": _r2(sum(adv.values()))}}


def pay_salary(conn: sqlite3.Connection, ws: str, p: dict, *, user: str = "") -> dict[str, Any]:
    """Pay one staff member for a month → an Expense "Salary" from cash / bank + the salary slip line."""
    ensure_schema(conn)
    month = _check_month(_s(p.get("month")) or _today().strftime("%Y-%m"))
    sid = int(p.get("staff_id") or 0)
    s = conn.execute("SELECT * FROM hop_staff WHERE workspace_id=? AND id=?", (ws, sid)).fetchone()
    if not s:
        raise LookupError("Staff member not found")
    if conn.execute("SELECT 1 FROM hop_salary_runs WHERE workspace_id=? AND staff_id=? AND month=? AND deleted_at IS NULL",
                    (ws, sid, month)).fetchone():
        raise ValueError(f"{s['name']}'s salary for {month} is already paid — undo it first to pay again")
    dim = _month_days(month)
    present = _f(p.get("present_days")) if p.get("present_days") not in (None, "") else (dim if s["pay_type"] == "monthly" else 0)
    if present < 0 or (present > dim):
        raise ValueError(f"Present days must be between 0 and {dim}")
    earned = _r2(_f(s["salary"]) * present / dim) if s["pay_type"] == "monthly" else _r2(_f(s["salary"]) * present)
    bonus, ded = max(0.0, _f(p.get("bonus"))), max(0.0, _f(p.get("deductions")))
    due = advances_due(conn, ws).get(sid, 0.0)
    recover = min(max(0.0, _f(p.get("advance_recover"))), due)
    net = _r2(earned + bonus - ded - recover)
    if net < 0:
        raise ValueError("Net salary is below zero — lower the deductions or the advance to recover")
    on = _s(p.get("date"))[:10] or _today().isoformat()
    txn = _expense(conn, ws, SALARY_CAT, net, on, _s(p.get("payment_mode")) or "cash", p.get("payment_account_id"),
                   f"Salary {month}") if net > 0 else None
    cur = conn.execute(
        "INSERT INTO hop_salary_runs (workspace_id, staff_id, month, days_in_month, present_days, earned, bonus, deductions, "
        "advance_recovered, net, paid_on, payment_mode, expense_txn_id, note, by_user, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ws, sid, month, dim, present, earned, bonus, ded, recover, net, on, _s(p.get("payment_mode")) or "cash", txn,
         _s(p.get("note"))[:200] or None, user or None, _now()))
    conn.commit()
    return {"run_id": int(cur.lastrowid), "net": net, "earned": earned, "advance_recovered": recover,
            "advance_left": _r2(due - recover), "expense_txn_id": txn}


def undo_salary(conn: sqlite3.Connection, ws: str, run_id: int) -> None:
    r = conn.execute("SELECT expense_txn_id FROM hop_salary_runs WHERE workspace_id=? AND id=? AND deleted_at IS NULL",
                     (ws, int(run_id))).fetchone()
    if not r:
        raise LookupError("Salary payment not found")
    _undo_expense(conn, ws, r[0])
    conn.execute("UPDATE hop_salary_runs SET deleted_at=? WHERE id=?", (_now(), int(run_id)))
    conn.commit()


def year_summary(conn: sqlite3.Connection, ws: str, months: int = 12) -> list[dict[str, Any]]:
    """Salary cost per month (net paid + advance recovered) — for reports, insights and Ask Nexora."""
    ensure_schema(conn)
    return [{"month": r[0], "staff": int(r[1]), "net_paid": _r2(r[2]), "advance_recovered": _r2(r[3]), "cost": _r2(r[2] + r[3])}
            for r in conn.execute(
                "SELECT month, COUNT(*), SUM(net), SUM(COALESCE(advance_recovered,0)) FROM hop_salary_runs "
                "WHERE workspace_id=? AND deleted_at IS NULL GROUP BY month ORDER BY month DESC LIMIT ?", (ws, int(months)))]
