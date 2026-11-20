"""Salary on a made-up firm: staff, advance from the bank, pay with advance recovered, books effects, privacy."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_books, hop_money
from app import hop_salary as S
from app.hop_db import connect
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    path = str(tmp_path / "salary.sqlite3")
    ensure_hop_schema(path)
    return path


def _bal(conn, aid):
    return float(conn.execute("SELECT current_balance FROM hop_bank_accounts WHERE id=?", (aid,)).fetchone()[0])


def _cat_total(conn, name):
    return next(c for c in hop_money.list_expense_categories(conn, WS) if c["name"] == name)["total"]


def test_salary_month_advance_pay_and_books(db):
    with connect(db) as conn:
        bank = hop_books.create_bank_account(conn, WS, {"name": "Axis", "account_type": "bank", "opening_balance": 100000})
        ravi = S.save_staff(conn, WS, {"name": "Ravi Kumar", "designation": "Salesman", "salary": 30000, "joined_on": "2026-01-05"})
        mohan = S.save_staff(conn, WS, {"name": "Mohan", "pay_type": "daily", "salary": 600})
        with pytest.raises(ValueError):
            S.save_staff(conn, WS, {"salary": 10})                    # no name
        # advance from the bank: money out today, an expense "Salary advance"
        a = S.give_advance(conn, WS, {"staff_id": ravi["id"], "amount": 5000, "payment_mode": "bank",
                                      "payment_account_id": bank["id"], "date": "2026-11-05"})
        assert a["advance_due"] == 5000 and _bal(conn, bank["id"]) == 95000
        assert _cat_total(conn, "Salary advance") == 5000
        sheet = S.month_sheet(conn, WS, "2026-11")
        r = next(x for x in sheet["staff"] if x["name"] == "Ravi Kumar")
        assert r["days_in_month"] == 30 and r["present_days"] == 30 and r["earned"] == 30000 and r["advance_due"] == 5000
        m = next(x for x in sheet["staff"] if x["name"] == "Mohan")
        assert m["present_days"] == 0 and m["earned"] == 0
        # pay Ravi for 28 days, recover the advance, bonus 1000
        out = S.pay_salary(conn, WS, {"staff_id": ravi["id"], "month": "2026-11", "present_days": 28, "bonus": 1000,
                                      "advance_recover": 9999, "payment_mode": "bank", "payment_account_id": bank["id"],
                                      "date": "2026-11-30"})
        assert out["earned"] == 28000 and out["advance_recovered"] == 5000 and out["net"] == 24000 and out["advance_left"] == 0
        assert _bal(conn, bank["id"]) == 71000 and _cat_total(conn, "Salary") == 24000
        # the expense has no staff name on it (who earns what stays in Salary)
        note = conn.execute("SELECT COALESCE(notes,'') FROM hop_party_transactions WHERE id=?", (out["expense_txn_id"],)).fetchone()[0]
        assert "Ravi" not in note
        with pytest.raises(ValueError):
            S.pay_salary(conn, WS, {"staff_id": ravi["id"], "month": "2026-11"})       # paid twice
        with pytest.raises(ValueError):
            S.pay_salary(conn, WS, {"staff_id": mohan["id"], "month": "2026-11", "present_days": 40})
        S.pay_salary(conn, WS, {"staff_id": mohan["id"], "month": "2026-11", "present_days": 20})   # cash, ₹12,000
        sheet = S.month_sheet(conn, WS, "2026-11")
        assert sheet["totals"]["paid"] == 2 and sheet["totals"]["net_paid"] == 36000
        assert sheet["totals"]["salary_cost"] == 41000 and sheet["totals"]["advances_due"] == 0
        # P&L sees salary + advance as expenses
        from app import hop_reports
        pl = hop_reports.profit_and_loss(conn, WS, "2026-11-01", "2026-11-30")
        exp = {e["category"]: e["amount"] for e in pl["expenses"]}
        assert exp.get("Salary") == 36000 and exp.get("Salary advance") == 5000 and pl["total_expenses"] == 41000
        # undo Mohan: the expense goes to the Recycle bin, the month shows unpaid again
        run_id = next(x for x in sheet["staff"] if x["name"] == "Mohan")["run"]["id"]
        S.undo_salary(conn, WS, run_id)
        assert S.month_sheet(conn, WS, "2026-11")["totals"]["paid"] == 1 and _cat_total(conn, "Salary") == 24000
        # Ask Nexora
        from app.services import hop_ai
        ans = hop_ai.books_answer(conn, WS, "salary", "ravi", {"from": "2026-11-01"})
        assert ans["month"] == "2026-11" and ans["staff"][0]["net_paid"] == 24000


def test_salary_checks_jump_and_big_advance(db):
    with connect(db) as conn:
        s = S.save_staff(conn, WS, {"name": "Asha", "salary": 10000})
        S.pay_salary(conn, WS, {"staff_id": s["id"], "month": "2026-10", "present_days": 31})
        S.save_staff(conn, WS, {"salary": 20000}, s["id"])
        S.pay_salary(conn, WS, {"staff_id": s["id"], "month": "2026-11", "present_days": 30})
        assert any("last month" in c for c in S.month_sheet(conn, WS, "2026-11")["checks"])
        out = S.give_advance(conn, WS, {"staff_id": s["id"], "amount": 25000})
        assert out["checks"] and "more than one month" in out["checks"][0]


def test_salary_is_private_for_staff(tmp_path, monkeypatch):
    from tests.test_hop_permissions import _staff
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, owner, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    staff, _ = _staff(client, db_path)
    r = client.post("/api/v1/hop/salary/staff", headers=owner, json={"name": "Geeta", "salary": 15000})
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/v1/hop/salary/month?month=2026-11", headers=owner).get_json()["data"]["staff"][0]["name"] == "Geeta"
    # a staff login has full access by default — but not to Salary
    assert client.get("/api/v1/hop/salary/month", headers=staff).status_code == 403
    assert client.get("/api/v1/hop/my-permissions", headers=staff).get_json()["data"]["areas"]["salary"] == "none"
    lst = client.get("/api/v1/hop/staff-permissions", headers=owner).get_json()["data"]
    assert any(a["key"] == "salary" for a in lst["areas"])


def test_pf_esi_pt_tds_working_and_dues(db):
    from app import hop_payroll_tax as T

    with connect(db) as conn:
        bank = hop_books.create_bank_account(conn, WS, {"name": "HDFC", "account_type": "bank", "opening_balance": 500000})
        S.ensure_schema(conn)
        T.save_settings(conn, WS, {"pf_enabled": True, "esi_enabled": True, "pt_default": 200, "pf_er_pct": 99})
        cfg = T.settings(conn, WS)
        assert cfg["pf_er_pct"] == 12.0          # government rates are not saved per firm
        low = S.save_staff(conn, WS, {"name": "Sunil", "salary": 15000, "pf_on": True})
        high = S.save_staff(conn, WS, {"name": "Neha", "salary": 150000, "pf_on": True, "pan": "abcde1234f"})
        assert high["pan"] == "ABCDE1234F"
        pv = S.preview(conn, WS, {"staff_id": low["id"], "month": "2026-11"})
        w = pv["working"]
        assert (w["pf_emp"], w["pf_er"], w["esi_emp"], w["esi_er"], w["pt"], w["tds"]) == (1800, 1950, 113, 488, 200, 0)
        assert pv["net"] == 15000 - 1800 - 113 - 200
        hw = S.preview(conn, WS, {"staff_id": high["id"], "month": "2026-11"})["working"]
        assert hw["esi_emp"] == 0 and hw["pf_emp"] == 1800 and hw["tds"] == 12567    # ₹17.25 lakh taxable
        assert T.yearly_tax(1200000, cfg) == 0 and T.yearly_tax(1300000, cfg) == 78000
        S.pay_salary(conn, WS, {"staff_id": low["id"], "month": "2026-11", "payment_mode": "bank", "payment_account_id": bank["id"]})
        S.pay_salary(conn, WS, {"staff_id": high["id"], "month": "2026-11", "payment_mode": "bank", "payment_account_id": bank["id"]})
        d = {x["kind"]: x for x in S.dues(conn, WS, "2026-11")}
        assert d["pf"]["amount"] == 1800 * 2 + 1950 * 2 and d["esi"]["amount"] == 601
        assert d["tds"]["amount"] == 12567 and d["pt"]["amount"] == 400
        assert d["pf"]["due_on"] == "2026-12-15" and d["tds"]["due_on"] == "2026-12-07"
        out = S.pay_due(conn, WS, {"month": "2026-11", "kind": "pf", "payment_mode": "bank", "payment_account_id": bank["id"],
                                   "challan_ref": "TRRN123"})
        assert next(x for x in out["dues"] if x["kind"] == "pf")["left"] == 0
        assert _cat_total(conn, "PF") == 7500
        assert any(x["label"] == "TDS on salary" for x in S.open_dues(conn, WS))
        with pytest.raises(ValueError):
            S.pay_due(conn, WS, {"month": "2026-11", "kind": "gst"})
