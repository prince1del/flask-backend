"""E-Invoice / E-Way Bill JSON from sale invoices (made-up company)."""
from __future__ import annotations

from app import hop_einvoice as E
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_einvoice_json_and_problems(co):
    with connect(co["path"]) as conn:
        out = E.einvoice_json(conn, WS, [co["inv"]["party_txn_id"]])
        doc = out["json"][0]
        assert doc["Version"] == "1.1" and doc["DocDtls"]["Typ"] == "INV"
        assert doc["SellerDtls"]["Stcd"] == "07" and doc["BuyerDtls"]["Gstin"] == "07AAAAA0000A1Z1"
        item = doc["ItemList"][0]
        assert (item["HsnCd"], item["Qty"], item["AssAmt"], item["CgstAmt"], item["IgstAmt"]) == ("6303", 5.0, 5000.0, 450.0, 0.0)
        assert doc["ValDtls"]["TotInvVal"] == 5900
        # PIN codes are missing in the made-up company → listed as problems, not a crash.
        assert any("PIN" in p for p in out["problems"])


def test_eway_json_and_saving_refs(co):
    with connect(co["path"]) as conn:
        out = E.eway_json(conn, WS, [co["inv"]["party_txn_id"]], {"mode": "road", "vehicle_no": "dl 1c ab 1234", "distance_km": 12})
        b = out["json"]["billLists"][0]
        assert b["vehicleNo"] == "DL1CAB1234" and b["cgstValue"] == 450 and b["itemList"][0]["cgstRate"] == 9
        assert any("₹50,000" in p for p in out["problems"])
        saved = E.save_refs(conn, WS, co["inv"]["party_txn_id"], {"irn": "abc123", "eway_bill_no": "331000000001"})
        assert saved["irn"] == "abc123"


def test_only_sale_invoices(co):
    import pytest

    with connect(co["path"]) as conn:
        est = create(conn, WS, {"txn_type": 27, "customer_id": co["cust"]["id"], "lines": [{"item_name": "x", "qty": 1, "rate": 10}]})
        with pytest.raises(ValueError):
            E.einvoice_json(conn, WS, [est["party_txn_id"]])
