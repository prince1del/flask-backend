"""Bill fields: customer PO, e-way + transport, supplier's bill no., UTR, credit note against invoice (made-up company)."""
from __future__ import annotations

from app.hop_db import connect
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create, get_party_transaction_edit_data as get_manual_party_document
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_fields_saved_and_shown(co):
    with connect(co["path"]) as conn:
        cid = co["cust"]["id"]
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cid, "po_number": "PO-77", "po_date": "2026-11-10",
                                "eway_bill_no": "331000123456", "vehicle_no": "DL1LA1234", "transporter_name": "VRL",
                                "lines": [{"item_name": "Rod", "qty": 1, "rate": 1000, "tax_pct": 18}]})
        h = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])["header"]
        assert (h["po_number"], h["eway_bill_no"], h["vehicle_no"], h["transporter_name"]) == ("PO-77", "331000123456", "DL1LA1234", "VRL")
        cn = create(conn, WS, {"txn_type": 21, "customer_id": cid, "ref_txn_id": inv["party_txn_id"],
                               "lines": [{"item_name": "Rod", "qty": 1, "rate": 100, "tax_pct": 18}]})
        h2 = build_txn_preview(conn, WS, party_txn_id=cn["party_txn_id"])["header"]
        assert h2["against"]["id"] == inv["party_txn_id"]
        acct = conn.execute("SELECT id FROM hop_bank_accounts WHERE workspace_id=? AND account_type!='cash' LIMIT 1", (WS,)).fetchone()[0]
        pay = create(conn, WS, {"txn_type": 3, "customer_id": cid, "amount": 500, "received_amount": 500,
                                "payment_mode": "bank", "payment_account_id": acct, "payment_ref": "UTR998877"})
        assert build_txn_preview(conn, WS, party_txn_id=pay["party_txn_id"])["header"]["payment_ref"] == "UTR998877"
        ven = co["ven"]["id"] if "ven" in co else conn.execute("SELECT id FROM hop_vendors WHERE workspace_id=? LIMIT 1", (WS,)).fetchone()[0]
        pb = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ven, "party_bill_no": "MILL/552",
                               "party_bill_date": "2026-11-12", "lines": [{"item_name": "Rod", "qty": 1, "rate": 600}]})
        d = get_manual_party_document(conn, WS, pb["party_txn_id"])
        assert d["party_bill_no"] == "MILL/552" and d["party_bill_date"] == "2026-11-12"


def test_amount_in_words_indian():
    from app.hop_doc_pdf import amount_in_words
    assert amount_in_words(81420) == "Rupees Eighty One Thousand Four Hundred Twenty Only"
    assert amount_in_words(12500000.5) == "Rupees One Crore Twenty Five Lakh and Fifty Paise Only"
    assert amount_in_words(0) == "Rupees Zero Only"
