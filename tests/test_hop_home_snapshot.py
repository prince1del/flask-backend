"""Home overview counts come from the real books (made-up company)."""
from __future__ import annotations

from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS


def test_home_from_books(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client
    from app.hop_time import today_iso

    client, owner, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    snap0 = client.get("/api/v1/hop/executive/snapshot", headers=owner)
    assert snap0.status_code == 200, snap0.get_data(as_text=True)[:200]
    with connect(str(tmp_path / "wipe_test.sqlite3")) as conn:
        c = create_customer(conn, WS, {"company": "Hotel Z"})
        d = today_iso()
        create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": d, "received_amount": 400,
                          "lines": [{"item_name": "Rod", "qty": 1, "rate": 1000}]})
        create(conn, WS, {"txn_type": 27, "customer_id": c["id"], "txn_date": d,
                          "lines": [{"item_name": "Rod", "qty": 1, "rate": 500}]})
    data = client.get("/api/v1/hop/executive/snapshot", headers=owner).get_json()["data"]
    assert isinstance(data["cash_available"], float)
    t = data["today"]
    assert t["outstanding_receivables"] == 600
    assert t["payments_received_today"] == 400
    assert t["quotations_pending"] == 1
