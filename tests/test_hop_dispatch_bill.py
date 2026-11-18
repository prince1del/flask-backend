"""Dispatch carries our bill / challan number and shows its customer (made-up company)."""
from __future__ import annotations

from app import hop_ops
from app.hop_db import connect
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_dispatch_with_bill_number_only(co):
    with connect(co["path"]) as conn:
        no = conn.execute("SELECT txn_number FROM hop_party_transactions WHERE workspace_id=? AND txn_type=1 LIMIT 1",
                          (WS,)).fetchone()[0]
        hop_ops.create_dispatch(conn, WS, {"invoice_no_link": no, "eway_bill": "EWB1", "courier": "Blue Dart"})
        d = hop_ops.list_dispatches(conn, WS)[0]
        assert d["invoice_no_link"] == no and d["bill_party"] == "Hotel A" and d["bill_kind"] == "Bill"
        assert hop_ops.list_dispatches(conn, WS, q="EWB1")


def test_mark_delivered(co):
    with connect(co["path"]) as conn:
        d = hop_ops.create_dispatch(conn, WS, {"tracking_number": "T1"})
        d = hop_ops.update_dispatch(conn, WS, d["id"], {"delivery_status": "in_transit"})
        assert d["status"] == "dispatched"
        d = hop_ops.update_dispatch(conn, WS, d["id"], {"delivery_status": "delivered"})
        assert d["status"] == "delivered" and d["delivered_at"]


def test_sample_and_complaint_status(co):
    with connect(co["path"]) as conn:
        s = hop_ops.create_sample(conn, WS, {"sample_name": "Swatch"})
        assert hop_ops.update_sample(conn, WS, s["id"], {"approval_status": "approved"})["approval_status"] == "approved"
        c = hop_ops.create_complaint(conn, WS, {"issue": "Rod loose"})
        r = hop_ops.update_complaint(conn, WS, c["id"], {"status": "resolved"})
        assert r["status"] == "resolved"


def test_ask_nexora_dispatch_samples_complaints(co):
    from app.services import hop_ai
    import inspect

    fn = next(f for n, f in inspect.getmembers(hop_ai, inspect.isfunction)
              if "what" in inspect.signature(f).parameters and "party_name" in inspect.signature(f).parameters)
    with connect(co["path"]) as conn:
        no = conn.execute("SELECT txn_number FROM hop_party_transactions WHERE workspace_id=? AND txn_type=1 LIMIT 1",
                          (WS,)).fetchone()[0]
        hop_ops.create_dispatch(conn, WS, {"invoice_no_link": no, "courier": "DTDC"})
        hop_ops.create_complaint(conn, WS, {"issue": "Rod loose"})
        kw = {p: None for p in inspect.signature(fn).parameters if p not in ("conn", "ws", "workspace_id", "what")}
        first = list(inspect.signature(fn).parameters)
        args = {first[0]: conn, first[1]: WS}
        d = fn(**args, what="dispatch", **{k: v for k, v in kw.items() if k not in args})
        assert d["dispatches"][0]["courier"] == "DTDC" and d["not_delivered"] == 1
        c = fn(**args, what="complaints", **{k: v for k, v in kw.items() if k not in args})
        assert c["open"] == 1


def test_converted_estimate_says_where_it_went(co):
    import pytest
    from app import hop_money
    from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
    with connect(co["path"]) as conn:
        cid = conn.execute("SELECT id FROM hop_customers WHERE workspace_id=? LIMIT 1", (WS,)).fetchone()[0]
        e = create(conn, WS, {"txn_type": 27, "customer_id": cid, "lines": [{"item_name": "Rod", "qty": 1, "rate": 100}]})
        inv = create(conn, WS, hop_money.convert_draft(conn, WS, e["party_txn_id"], 1))
        pv = build_txn_preview(conn, WS, party_txn_id=e["party_txn_id"])
        assert pv["converted_to"]["id"] == inv["party_txn_id"] and inv["txn_number"] in pv["converted_to"]["label"]
        with pytest.raises(ValueError, match="already converted to Sale Invoice"):
            hop_money.convert_draft(conn, WS, e["party_txn_id"], 65)
