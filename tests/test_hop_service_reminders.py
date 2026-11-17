"""Service reminders: item 'service every N days' → due after each sale, next cycle after 'done',
WhatsApp text, item tip, Ask Nexora summary, and the routes."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app import hop_items, hop_ops, hop_service_reminders as SR
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

TODAY = date(2026, 11, 17)


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "svc.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        ac = hop_ops.create_product(conn, WS, {"name": "Split AC 1.5 Ton", "selling_price": 30000,
                                                "service_period_days": 180})
        hop_ops.create_product(conn, WS, {"name": "RO Water Purifier", "selling_price": 9000})
        cust = create_customer(conn, WS, {"company": "Mehta House", "mobile": "9876543210"})
        other = create_customer(conn, WS, {"company": "Far Future"})
        # sold 182 days ago → overdue by 2; sold 178 days ago → due in 2 (soon); sold today → not listed
        for c, d in ((cust, "2026-05-19"), (other, "2026-05-23"), (cust, "2026-11-17")):
            create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": d,
                              "lines": [{"item_name": "Split AC 1.5 Ton", "product_id": ac["id"], "qty": 1, "rate": 30000}]})
        # a line typed by name only (no product id) still counts — due exactly today
        create(conn, WS, {"txn_type": 1, "customer_id": other["id"], "txn_date": "2026-05-21",
                          "lines": [{"item_name": "split ac 1.5 ton", "qty": 1, "rate": 30000}]})
        # purchase of the same item is not a sale → never a service reminder
        ven = hop_ops.create_vendor(conn, WS, {"company": "AC Supplier"})
        create(conn, WS, {"txn_type": 2, "vendor_id": ven["id"], "txn_date": "2026-05-19",
                          "lines": [{"item_name": "Split AC 1.5 Ton", "product_id": ac["id"], "qty": 1, "rate": 20000}]})
        conn.commit()
    return p


def test_groups_and_whatsapp(db):
    with connect(db) as conn:
        dl = SR.due_list(conn, WS, TODAY)
    assert [(x["party"], x["days"]) for x in dl["overdue"]] == [("Mehta House", -2)]
    assert [x["party"] for x in dl["today"]] == ["Far Future"]   # typed by name only, sold 21 May
    assert [x["days"] for x in dl["soon"]] == [2]
    o = dl["overdue"][0]
    assert o["item"] == "Split AC 1.5 Ton" and o["due_date"] == "2026-11-15" and o["phone"] == "919876543210"
    assert "Mehta House" in o["text"] and "Split AC" in o["text"]
    assert o["whatsapp_url"].startswith("https://wa.me/919876543210?text=")


def test_done_starts_next_cycle_and_whatsapp_is_noted(db):
    with connect(db) as conn:
        line = SR.due_list(conn, WS, TODAY)["overdue"][0]["line_id"]
        SR.mark(conn, WS, line, "whatsapp")
        assert SR.due_list(conn, WS, TODAY)["overdue"][0]["last_reminded"]
        SR.mark(conn, WS, line, "done", "2026-11-16")
        dl = SR.due_list(conn, WS, TODAY)
        assert dl["overdue"] == []   # next service is 180 days after 16 Nov
        with pytest.raises(ValueError):
            SR.mark(conn, WS, line, "done", "2099-01-01")
        with pytest.raises(ValueError):
            SR.mark(conn, WS, 999999, "done")


def test_item_field_and_tip(db):
    with connect(db) as conn:
        rows = {r["name"]: r for r in hop_ops.list_products(conn, WS)}
        assert rows["Split AC 1.5 Ton"]["service_period_days"] == 180
        ro = rows["RO Water Purifier"]
        assert ro["service_suggest_days"] == 90 and any("service every 90 days" in c for c in ro["checks"])
        with pytest.raises(ValueError):
            hop_items.save_extras(conn, WS, ro["id"], {"service_period_days": -5})
        hop_ops.update_product(conn, WS, ro["id"], {"service_period_days": 90})
        assert next(r for r in hop_ops.list_products(conn, WS) if r["id"] == ro["id"])["service_period_days"] == 90
    assert SR.suggest_days("Daikin Inverter AC") == 180 and SR.suggest_days("Cotton bedsheet") is None


def test_ai_summary(db, monkeypatch):
    monkeypatch.setattr(SR, "_today", lambda: TODAY)
    with connect(db) as conn:
        s = SR.summary_for_ai(conn, WS, "mehta")
    assert [x["party"] for x in s["service_overdue"]] == ["Mehta House"] and s["service_soon"] == []
    assert s["items_with_service_period"] == [{"item": "Split AC 1.5 Ton", "every_days": 180}]


def test_routes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.get("/api/v1/hop/reminders/service", headers=headers)
    assert r.status_code == 200 and set(r.get_json()["data"]["counts"]) == {"overdue", "today", "soon"}
    assert client.post("/api/v1/hop/reminders/service/999999/done", headers=headers, json={}).status_code == 400
    assert client.post("/api/v1/hop/reminders/service/1/explode", headers=headers, json={}).status_code == 400
    r = client.get("/api/v1/hop/items/service-suggest?name=Voltas%20AC", headers=headers)
    assert r.get_json()["data"]["days"] == 180
