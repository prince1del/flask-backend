"""HoP settings: every setting changes real behaviour (made-up company)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_app_settings as S, hop_settings
from app.hop_db import connect, create_customer
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "settings.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel S"})
        create_product(conn, WS, {"name": "Lamp", "stock_qty": 2, "purchase_price": 100})
        conn.commit()
    return p, cust["id"]


def _t(conn, tid):
    return dict(conn.execute("SELECT * FROM hop_party_transactions WHERE id=?", (tid,)).fetchone())


def test_screen_payload_and_validation(db):
    p, _ = db
    with connect(p) as conn:
        data = S.payload(conn, WS)
        titles = [s["title"] for s in data["sections"]]
        assert titles[:2] == ["Transaction", "Taxes & GST"] and "Print" in titles and "Party" in titles
        with pytest.raises(ValueError):
            S.save(conn, WS, {"short_stock": "maybe"})
        with pytest.raises(ValueError):
            S.save(conn, WS, {"nonexistent": 1})
        with pytest.raises(ValueError):
            S.save(conn, WS, {"prefix_1": "HO P/!"})


def test_prefix_round_off_and_inclusive_rates(db):
    p, cust = db
    with connect(p) as conn:
        S.save(conn, WS, {"prefix_1": "hs", "auto_round_off": True, "tax_inclusive_rates": True})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-16",
                                "lines": [{"item_name": "Lamp", "qty": 1, "rate": 1180.4, "tax_pct": 18}]})
        assert inv["txn_number"] == "HS/2026-27/1"
        t = _t(conn, inv["party_txn_id"])
        line = conn.execute("SELECT rate, tax_amount FROM hop_txn_lines WHERE source_txn_id=?", (t["source_txn_id"],)).fetchone()
        assert round(line[0], 2) == 1000.34 and round(line[1], 2) == 180.06
        assert t["total_amount"] == 1180 and t["round_off"] == -0.4


def test_cash_sale_default_and_due_days(db):
    p, cust = db
    with connect(p) as conn:
        S.save(conn, WS, {"cash_sale_default": True, "default_due_days": 15})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-01",
                                "lines": [{"item_name": "x", "qty": 1, "rate": 500}]})
        t = _t(conn, inv["party_txn_id"])
        assert (t["received_amount"], t["status_text"], t["due_date"]) == (500, "Paid", "2026-11-16")
        # Saying how much was received still wins.
        inv2 = create(conn, WS, {"txn_type": 1, "customer_id": cust, "received_amount": 0,
                                 "lines": [{"item_name": "x", "qty": 1, "rate": 500}]})
        assert _t(conn, inv2["party_txn_id"])["status_text"] == "Unpaid"


def test_short_stock_warn_and_block(db):
    p, cust = db
    with connect(p) as conn:
        w = hop_settings.invoice_entry_warnings(conn, WS, {"txn_type": 1, "party_id": cust,
                                                           "lines": [{"item_name": "Lamp", "qty": 5, "rate": 10}]})
        assert any(x["code"] == "short_stock" and x["level"] == "warn" for x in w)
        S.save(conn, WS, {"short_stock": "block"})
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "Lamp", "qty": 5, "rate": 10}]})
        conn.rollback()
        create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "Lamp", "qty": 2, "rate": 10}]})


def test_credit_limit_block(db):
    p, cust = db
    with connect(p) as conn:
        conn.execute("UPDATE hop_customers SET credit_limit=1000, credit_no_limit=0 WHERE id=?", (cust,))
        S.save(conn, WS, {"credit_limit_action": "block"})
        create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "x", "qty": 1, "rate": 900}]})
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "x", "qty": 1, "rate": 500}]})


def test_print_options_and_composition(db):
    p, cust = db
    with connect(p) as conn:
        S.save(conn, WS, {"print_amount_words": False, "print_terms": False, "print_footer": "Thank you!",
                          "composition_scheme": True})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust, "doc_terms": "Pay in 7 days",
                                "lines": [{"item_name": "x", "qty": 1, "rate": 100, "tax_pct": 18}]})
        pv = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])
        assert pv["totals"]["amount_in_words"] == "" and pv["terms"] == "" and pv["footer"] == "Thank you!"
        assert pv["header"]["doc_title"] == "Bill of Supply"
        assert pv["print"] == {"paper": "a4", "copies": 1, "copy_labels": ["Original for Recipient"],
                               "date_format": "dd-mm-yyyy", "decimals": 2,
                               "currency": "₹", "header": "", "hsn_column": True, "margin_mm": 10}
        S.save(conn, WS, {"print_paper": "a5", "print_copies": "3"})
        pv = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])
        assert pv["print"]["paper"] == "a5" and len(pv["print"]["copy_labels"]) == 3
        assert _t(conn, inv["party_txn_id"])["total_amount"] == 100   # no GST under composition


def test_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.get("/api/v1/hop/app-settings", headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["values"]["short_stock"] == "warn"
    r = client.put("/api/v1/hop/app-settings", headers=headers, json={"auto_round_off": True, "party_groups": "Hotel, Retail"})
    assert r.get_json()["data"]["values"]["auto_round_off"] is True
    assert r.get_json()["data"]["values"]["party_groups"] == ["Hotel", "Retail"]
    assert client.put("/api/v1/hop/app-settings", headers=headers, json={"short_stock": "x"}).status_code == 400
