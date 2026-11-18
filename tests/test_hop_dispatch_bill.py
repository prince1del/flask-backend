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


def test_sample_with_party_and_several_items(co):
    with connect(co["path"]) as conn:
        cid = co["cust"]["id"]
        s = hop_ops.create_sample(conn, WS, {"customer_id": cid, "items": [
            {"name": "Blackout fabric", "qty": 2, "unit": "MTR"}, {"name": "Sheer swatch", "qty": 1}]})
        row = next(r for r in hop_ops.list_samples(conn, WS) if r["id"] == s["id"])
        assert row["customer_company"] == "Hotel A" and len(row["items"]) == 2
        assert "Blackout fabric × 2" in row["sample_name"]


def test_commission_due_from_customer_agent_and_delete(co):
    from app.hop_doc_preview import create_manual_party_document as create
    with connect(co["path"]) as conn:
        cid = co["cust"]["id"]
        inv1 = create(conn, WS, {"txn_type": 1, "customer_id": cid, "lines": [{"item_name": "Rod", "qty": 1, "rate": 1000}]})
        e = hop_ops.upsert_commission_entry(conn, WS, {"party_txn_id": inv1["party_txn_id"], "agent_name": "Raju",
                                                       "commission_pct": 3, "tds_pct": 2})
        hop_ops.set_customer_commission_agent(conn, WS, inv1["party_txn_id"], "Raju", 3, 2)
        inv2 = create(conn, WS, {"txn_type": 1, "customer_id": cid, "lines": [{"item_name": "Rod", "qty": 2, "rate": 1000}]})
        rows = {r["party_txn_id"]: r for r in hop_ops.list_tax_invoices_for_commission(conn, WS)}
        assert rows[inv2["party_txn_id"]]["commission_due"] and rows[inv2["party_txn_id"]]["suggested_agent"] == "Raju"
        assert not rows[inv1["party_txn_id"]]["commission_due"]
        eid = rows[inv1["party_txn_id"]]["entry_id"]
        hop_ops.delete_commission_entry(conn, WS, eid)
        assert not {r["party_txn_id"]: r for r in hop_ops.list_tax_invoices_for_commission(conn, WS)}[inv1["party_txn_id"]]["has_entry"]
        _ = e


def test_ask_party_sales_period_and_not_found(co):
    from app.services import hop_ai
    import inspect

    fn = next(f for n, f in inspect.getmembers(hop_ai, inspect.isfunction)
              if "what" in inspect.signature(f).parameters and "party_name" in inspect.signature(f).parameters)
    params = list(inspect.signature(fn).parameters)
    with connect(co["path"]) as conn:
        base = {params[0]: conn, params[1]: WS}
        rest = {k: None for k in params if k not in (params[0], params[1], "what", "party_name")}
        r = fn(**base, what="party_statement", party_name="Bernina", **rest)
        assert r["not_found"] == "Bernina" and "Hotel A" in r["closest_names"]
        if "extra" in params:
            rest["extra"] = {"from": "2026-11-01", "to": "2026-11-30"}
        r2 = fn(**base, what="party_statement", party_name="Hotel A", **rest)
        assert "sales_in_period" in r2 and r2["sales_in_period"] >= 0
