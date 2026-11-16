"""HoP audit trail: changes are recorded with who / what / old amount (made-up data)."""
from __future__ import annotations


def test_audit_trail_records_changes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    cust = client.post("/api/v1/hop/customers", headers=headers, json={"company": "Audit Hotel"}).get_json()["data"]
    doc = client.post("/api/v1/hop/party-transactions", headers=headers, json={
        "txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-16",
        "lines": [{"item_name": "x", "qty": 1, "rate": 1000}]}).get_json()["data"]
    pid = doc["party_txn_id"]
    r = client.patch(f"/api/v1/hop/party-transactions/{pid}", headers=headers, json={
        "txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-16",
        "lines": [{"item_name": "x", "qty": 1, "rate": 1500}]})
    assert r.status_code == 200, r.get_json()
    client.put("/api/v1/hop/app-settings", headers=headers, json={"auto_round_off": True})
    client.get("/api/v1/hop/products", headers=headers)  # reads are not logged
    log = client.get("/api/v1/hop/audit-log", headers=headers).get_json()["data"]["rows"]
    texts = [x["text"] for x in log]
    assert any("changed Document" in t and "₹1,000.00 → ₹1,500.00" in t for t in texts), texts
    assert any(x["entity"] == "Settings" and x["summary"] == {"changed": ["auto_round_off"]} for x in log)
    assert any(x["action"] == "created" and x["entity"] == "Party" for x in log)
    assert not any("products" in (x["path"] or "") and x["action"] == "created" for x in log)
    d = client.delete(f"/api/v1/hop/party-transactions/{pid}", headers=headers)
    assert d.status_code == 200
    log = client.get("/api/v1/hop/audit-log?entity=Document", headers=headers).get_json()["data"]["rows"]
    assert log[0]["action"] == "deleted" and "was ₹1,500.00" in log[0]["text"]


def test_ask_nexora_audit(tmp_path):
    from app import hop_audit
    from app.hop_db import connect
    from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
    from app.services import hop_ai

    p = str(tmp_path / "a.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        hop_audit.record(conn, WS, {"user_id": 5, "username": "ravi"}, "DELETE", "/api/v1/hop/party-transactions/9",
                         None, None, {"txn_number": "HOP/1", "total_amount": 500})
        out = hop_ai.books_answer(conn, WS, "audit", "HOP/1")
        assert out["changes"][0]["what"].startswith("ravi deleted Document HOP/1") and out["people"] == ["ravi"]
