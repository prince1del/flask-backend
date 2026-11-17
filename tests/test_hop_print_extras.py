"""Printed bill: CGST + SGST vs IGST, HSN summary, currency, header, margins (made-up company)."""
from __future__ import annotations

from app import hop_app_settings as S
from app.hop_db import connect
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_same_state_bill_splits_cgst_sgst(co):
    with connect(co["path"]) as conn:
        data = build_txn_preview(conn, WS, party_txn_id=co["inv"]["party_txn_id"])
        assert data["interstate"] is False
        assert data["gst_split"] == [{"label": "CGST @ 9%", "amount": 450.0}, {"label": "SGST @ 9%", "amount": 450.0}]
        assert data["hsn_summary"] == []  # off by default


def test_place_of_supply_other_state_is_igst_and_hsn_summary(co):
    with connect(co["path"]) as conn:
        S.save(conn, WS, {"print_hsn_summary": True})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": co["cust"]["id"], "place_of_supply": "27",
                                "lines": [{"item_name": "Curtain", "hsn": "6303", "qty": 2, "rate": 500, "tax_pct": 12},
                                          {"item_name": "Rod", "hsn": "7306", "qty": 1, "rate": 1000, "tax_pct": 18,
                                           "cess_pct": 1}]})
        data = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])
        assert data["interstate"] is True
        labels = {r["label"]: r["amount"] for r in data["gst_split"]}
        assert labels == {"IGST @ 12%": 120.0, "IGST @ 18%": 180.0, "Cess": 10.0}
        rows = {r["hsn"]: r for r in data["hsn_summary"]}
        assert rows["6303"]["taxable"] == 1000 and rows["6303"]["igst"] == 120 and rows["6303"]["cgst"] == 0
        assert rows["7306"]["cess"] == 10 and rows["7306"]["total_tax"] == 190


def test_unknown_states_say_gst(co):
    with connect(co["path"]) as conn:
        conn.execute("UPDATE hop_firm_profile SET gstin='', state='' WHERE workspace_id=?", (WS,))
        data = build_txn_preview(conn, WS, party_txn_id=co["inv"]["party_txn_id"])
        assert data["interstate"] is None and data["gst_split"][0]["label"] == "GST @ 18%"


def test_print_options_reach_the_bill(co):
    with connect(co["path"]) as conn:
        S.save(conn, WS, {"currency_symbol": "none", "print_header": "|| Shree Ganeshaya Namah ||",
                          "print_hsn_column": False, "print_margin": "wide"})
        pr = build_txn_preview(conn, WS, party_txn_id=co["inv"]["party_txn_id"])["print"]
        assert (pr["currency"], pr["header"], pr["hsn_column"], pr["margin_mm"]) == ("", "|| Shree Ganeshaya Namah ||", False, 15)
        S.save(conn, WS, {"currency_symbol": "$"})
        assert build_txn_preview(conn, WS, party_txn_id=co["inv"]["party_txn_id"])["print"]["currency"] == "$"
