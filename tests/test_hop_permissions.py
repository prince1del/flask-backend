"""HoP staff permissions: owner sets none / view / edit per area; the server enforces it."""
from __future__ import annotations

from centralized_db_system.db import CentralizedDB


def _staff(client, db_path):
    CentralizedDB(db_path).create_user("hopstaff", "staff-pass-123", role="hop_admin", workspace_id="house_of_prizm")
    r = client.post("/api/v1/auth/login", json={"username": "hopstaff", "password": "staff-pass-123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()["data"]
    return {"Authorization": f"Bearer {d['access_token']}"}, int(d.get("user", {}).get("id") or d.get("user_id") or 0)


def test_staff_permissions_enforced(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, owner, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    staff, _ = _staff(client, db_path)
    # Nothing saved yet: staff has full access except owner-only screens.
    assert client.get("/api/v1/hop/my-permissions", headers=staff).get_json()["data"]["areas"]["sales"] == "edit"
    assert client.get("/api/v1/hop/backup", headers=staff).status_code == 403
    assert client.get("/api/v1/hop/staff-permissions", headers=staff).status_code == 403
    lst = client.get("/api/v1/hop/staff-permissions", headers=owner).get_json()["data"]
    sid = lst["staff"][0]["user_id"]
    assert lst["staff"][0]["name"] == "hopstaff" and "Salesperson" in lst["presets"]
    r = client.put("/api/v1/hop/staff-permissions", headers=owner, json={"user_id": sid, "preset": "Salesperson"})
    assert r.status_code == 200, r.get_json()
    me = client.get("/api/v1/hop/my-permissions", headers=staff).get_json()["data"]
    assert me["owner"] is False and me["areas"]["money"] == "none" and me["areas"]["items"] == "view"
    cust = client.post("/api/v1/hop/customers", headers=staff, json={"company": "Staff Hotel"})
    assert cust.status_code in (200, 201)
    sale = client.post("/api/v1/hop/party-transactions", headers=staff, json={
        "txn_type": 1, "customer_id": cust.get_json()["data"]["id"], "lines": [{"item_name": "x", "qty": 1, "rate": 10}]})
    assert sale.status_code in (200, 201), sale.get_json()
    bill = client.post("/api/v1/hop/party-transactions", headers=staff, json={"txn_type": 2, "lines": []})
    assert bill.status_code == 403 and "permission" in bill.get_json()["error"]["message"]
    assert client.get("/api/v1/hop/bank-accounts", headers=staff).status_code == 403
    assert client.get("/api/v1/hop/products", headers=staff).status_code == 200      # view
    assert client.post("/api/v1/hop/products", headers=staff, json={"name": "Z"}).status_code == 403
    # The owner is never limited and cannot be limited.
    assert client.get("/api/v1/hop/bank-accounts", headers=owner).status_code == 200
    me_owner = client.get("/api/v1/hop/my-permissions", headers=owner).get_json()["data"]
    assert me_owner["owner"] is True
    chk = client.post("/api/v1/hop/staff-permissions/check", headers=owner,
                      json={"areas": {"money": "edit", "reports": "none"}}).get_json()["data"]
    assert chk["warnings"] and "Can change: Cash & bank" in chk["summary"]


def test_ask_nexora_respects_permissions(tmp_path):
    import sqlite3

    from app import hop_permissions as HP
    from app.hop_schema import ensure_hop_schema

    p = str(tmp_path / "p.sqlite3")
    ensure_hop_schema(p)
    db = CentralizedDB(p)
    db.create_user("own", "pw-own-1234", role="hop_admin", workspace_id="house_of_prizm")
    db.create_user("stf", "pw-stf-1234", role="hop_admin", workspace_id="house_of_prizm")
    with sqlite3.connect(p) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE workspace_id='house_of_prizm' ORDER BY id")]
        HP.save(conn, "house_of_prizm", {"user_id": ids[0], "workspace_id": "house_of_prizm"}, ids[1], {}, "Salesperson")
        assert HP.perms_for(conn, {"user_id": ids[1], "workspace_id": "house_of_prizm"})["areas"]["money"] == "none"
        try:
            HP.save(conn, "house_of_prizm", {"user_id": ids[1], "workspace_id": "house_of_prizm"}, ids[1], {}, "Full access")
            raise AssertionError("staff changed own permissions")
        except PermissionError:
            pass
