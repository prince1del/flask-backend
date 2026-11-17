"""Targets: yearly (Apr–Mar) + monthly; achieved from sale invoices − sale returns; month without its own
target uses yearly ÷ 12; split; needed per remaining month; routes + snapshot."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app import hop_targets as T
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

TODAY = date(2026, 11, 17)


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "t.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Buyer"})
        for d, ty, amt in (("2026-04-10", 1, 1000), ("2026-11-05", 1, 3000), ("2026-11-06", 21, 500), ("2026-03-30", 1, 99)):
            create(conn, WS, {"txn_type": ty, "customer_id": c["id"], "txn_date": d,
                              "lines": [{"item_name": "x", "qty": 1, "rate": amt}]})
        conn.commit()
    return p


def test_yearly_monthly_and_achieved(db):
    with connect(db) as conn:
        T.set_target(conn, WS, "FY2026-27", 120000)
        T.set_target(conn, WS, "2026-11", 20000)
        s = T.summary(conn, WS, today=TODAY)
    assert s["fy"] == "FY2026-27" and s["yearly_target"] == 120000
    apr = s["months"][0]
    assert apr["label"] == "Apr 2026" and apr["achieved"] == 1000 and apr["from_yearly"] and apr["target"] == 10000
    nov = s["current_month"]
    assert nov["period"] == "2026-11" and nov["target"] == 20000 and not nov["from_yearly"]
    assert nov["achieved"] == 2500                     # invoice 3000 − return 500
    assert s["achieved_ytd"] == 3500                   # March 2026 bill belongs to the earlier year
    assert s["months"][-1]["future"] and s["months"][-1]["achieved"] == 0
    # 5 months left (Nov..Mar), 1000 achieved before Nov → (120000 − 1000) / 5
    assert s["per_month_needed"] == pytest.approx(23800)


def test_split_and_validation(db):
    with connect(db) as conn:
        T.split_yearly(conn, WS, 2026, 60000)
        s = T.summary(conn, WS, today=TODAY)
        assert all(m["target"] == 5000 and not m["from_yearly"] for m in s["months"])
        with pytest.raises(ValueError):
            T.set_target(conn, WS, "2026-13", 10)
        with pytest.raises(ValueError):
            T.set_target(conn, WS, "FY2026-28", 10)
        T.set_target(conn, WS, "2026-05", 0)          # 0 removes the month's own target
        assert T.summary(conn, WS, today=TODAY)["months"][1]["from_yearly"]


def test_routes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, owner, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    assert client.post("/api/v1/hop/targets", headers=owner, json={"period_label": "FY2026-27", "target_amount": 1200,
                                                                   "split_months": True}).status_code == 200
    s = client.get("/api/v1/hop/targets/summary?fy=2026", headers=owner).get_json()["data"]
    assert s["yearly_target"] == 1200 and s["months"][0]["target"] == 100
    assert client.post("/api/v1/hop/targets", headers=owner, json={"period_label": "bad", "target_amount": 1}).status_code == 400
    snap = client.get("/api/v1/hop/executive/snapshot", headers=owner)
    if snap.status_code == 200:
        assert "yearly" in snap.get_json()["data"]


def test_profitability_from_bills(tmp_path):
    from app import hop_ops

    p = str(tmp_path / "pf.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "B"})
        prod = hop_ops.create_product(conn, WS, {"name": "Rod", "purchase_price": 60, "selling_price": 100})
        d = date.today().isoformat()
        create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": d,
                          "lines": [{"item_name": "Rod", "product_id": prod["id"], "qty": 10, "rate": 100, "tax_pct": 18}]})
        conn.commit()
        r = hop_ops.report_profitability(conn, WS)
    assert r["revenue"] == pytest.approx(1000) and r["cogs"] == pytest.approx(600) and r["gross_profit"] == pytest.approx(400)
    assert r["gross_margin_pct"] == pytest.approx(40)
