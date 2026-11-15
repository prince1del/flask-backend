"""NEXORA Guard: rules find problems, the inbox resolves them when fixed, dismiss works,
and every login only sees its own inbox."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app import hop_books
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_product, create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
from app.services import nexora_guard as guard

HOP_USER = {"user_id": 7, "role": "hop_admin", "workspace_id": WS}


@pytest.fixture
def db(tmp_path: Path):
    path = str(tmp_path / "guard.sqlite3")
    ensure_hop_schema(path)
    return path


def _keys(path):
    return {i["key"]: i for i in guard.issues(path, HOP_USER, "open", refresh_minutes=0)["issues"]}


def test_hop_checks_find_real_problems(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Late"})
        ven = create_vendor(conn, WS, {"company": "Mill Two"})
        create_product(conn, WS, {"name": "Cushion", "stock_qty": 1})
        # Overdue customer bill (old date, nothing paid) — also sells 3 cushions → stock −2.
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-08-01",
                          "lines": [{"item_name": "Cushion", "qty": 3, "rate": 1000}]})
        # Same purchase bill number twice for one vendor.
        for _ in range(2):
            create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ven["id"], "txn_date": "2026-11-10",
                              "txn_number": "MT-77", "lines": [{"item_name": "Fabric", "qty": 1, "rate": 500}]})
        # Cash pushed below zero by an expense.
        from app import hop_money

        cat = next(c for c in hop_money.list_expense_categories(conn, WS) if c["name"] == "Rent")
        create(conn, WS, {"txn_type": 7, "expense_category_id": cat["id"], "amount": 900, "payment_mode": "cash"})
        conn.commit()
    keys = _keys(db)
    assert any(k.startswith("hop:overdue:1:hotel late") for k in keys)
    assert "hop:negstock" in keys
    assert any(k.startswith("hop:dup:2:mt-77") for k in keys)
    assert any(k.startswith("hop:negcash:") for k in keys)
    overdue = next(v for k, v in keys.items() if k.startswith("hop:overdue:1"))
    assert overdue["severity"] == "alert" and "₹3,000" in overdue["title"]


def test_fixed_problem_resolves_itself_and_dismiss_sticks(db):
    with connect(db) as conn:
        bank = hop_books.create_bank_account(conn, WS, {"name": "Tiny Bank", "opening_balance": -500})
        conn.commit()
    issue = next(v for k, v in _keys(db).items() if k.startswith("hop:negcash:tiny bank"))
    # Fix it: money in → issue moves to resolved on the next run.
    with connect(db) as conn:
        hop_books.add_bank_entry(conn, WS, {"account_id": bank["id"], "entry_type": "in", "amount": 1000})
        conn.commit()
    assert not any(k.startswith("hop:negcash:") for k in _keys(db))
    resolved = guard.issues(db, HOP_USER, "resolved", refresh_minutes=0)["issues"]
    assert any(r["id"] == issue["id"] for r in resolved)

    # Dismissed issues stay dismissed while the problem is still there.
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Old"})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-07-01",
                          "lines": [{"item_name": "x", "qty": 1, "rate": 100}]})
        conn.commit()
    od = next(v for k, v in _keys(db).items() if "hotel old" in k)
    guard.set_status(db, HOP_USER, od["id"], "dismissed")
    assert not any("hotel old" in k for k in _keys(db))
    assert any(r["id"] == od["id"] for r in guard.issues(db, HOP_USER, "dismissed", refresh_minutes=0)["issues"])


def test_inbox_is_per_login(db):
    with connect(db) as conn:
        hop_books.create_bank_account(conn, WS, {"name": "Neg", "opening_balance": -10})
        conn.commit()
    guard.run(db, HOP_USER)
    other = {"user_id": 99, "role": "sales_executive", "workspace_id": "someone_else"}
    with pytest.raises(LookupError):
        hop_issue = guard.issues(db, HOP_USER, "open", refresh_minutes=0)["issues"][0]
        guard.set_status(db, other, hop_issue["id"], "dismissed")


def test_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.get("/api/v1/guard/issues", headers=headers)
    assert r.status_code == 200 and "issues" in r.get_json()["data"]
    assert client.post("/api/v1/guard/run", headers=headers).status_code == 200
    assert client.post("/api/v1/guard/issues/999/dismiss", headers=headers).status_code == 404
    assert client.get("/api/v1/guard/issues").status_code == 401
    d = client.get("/api/v1/guard/digest", headers=headers).get_json()["data"]
    assert d["day"] == date.today().isoformat() and d["text"]  # no issues → "All clear" without AI


def test_hop_and_bd_inboxes_never_mix(db, monkeypatch):
    """One login with both businesses (owner): HoP screen shows only HoP problems, Executive only BD."""
    owner_ws = "ws_owner_1"
    with connect(db) as conn:
        hop_books.create_bank_account(conn, owner_ws, {"name": "HoP Bank", "opening_balance": -100})
        conn.commit()
    monkeypatch.setattr(guard, "bd_findings", lambda conn, uid, ws: [
        guard._issue("bd:42:aw26 bed:alert", "Distributor payments", "alert", "Ramesh Traders — ₹50,000 overdue")])
    owner = {"user_id": 5, "role": "sales_executive", "is_workspace_owner": True, "workspace_id": owner_ws}

    hop = guard.issues(db, {**owner, "guard_scope": "hop"}, "open", refresh_minutes=0)["issues"]
    bd = guard.issues(db, {**owner, "guard_scope": "bd"}, "open", refresh_minutes=0)["issues"]
    assert hop and all(i["key"].startswith("hop:") for i in hop)
    assert bd and all(i["key"].startswith("bd:") for i in bd)
    assert not any("Ramesh" in i["title"] for i in hop) and not any("HoP Bank" in i["title"] for i in bd)

    # A plain BD login cannot open the HoP inbox.
    rep = {"user_id": 9, "role": "sales_executive", "workspace_id": "ws_rep"}
    with pytest.raises(PermissionError):
        guard.issues(db, {**rep, "guard_scope": "hop"}, "open", refresh_minutes=0)
    # And without a business given, a BD login gets BD only.
    assert all(i["key"].startswith("bd:") for i in guard.issues(db, rep, "open", refresh_minutes=0)["issues"])
