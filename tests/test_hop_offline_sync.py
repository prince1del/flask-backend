"""Offline bills: the same client_ref sent twice saves one bill only."""
from __future__ import annotations


def test_offline_bill_not_saved_twice(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    cid = client.post("/api/v1/hop/customers", headers=h, json={"company": "Offline Hotel"}).get_json()["data"]["id"]
    body = {"txn_type": 1, "customer_id": cid, "client_ref": "phone-123e4567", "txn_date": "2026-11-16",
            "lines": [{"item_name": "x", "qty": 1, "rate": 100}]}
    a = client.post("/api/v1/hop/party-transactions", headers=h, json=body)
    b = client.post("/api/v1/hop/party-transactions", headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 200
    assert b.get_json()["data"]["already_saved"] is True
    assert a.get_json()["data"]["party_txn_id"] == b.get_json()["data"]["party_txn_id"]
    rows = client.get("/api/v1/hop/party-transactions?txn_type=1", headers=h).get_json()["data"]
    assert len([r for r in (rows if isinstance(rows, list) else rows.get("rows", [])) if r.get("party_id") == cid]) == 1
