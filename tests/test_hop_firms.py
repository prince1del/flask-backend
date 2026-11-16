"""HoP multiple firms: each firm's data is apart; only own firms can be chosen."""
from __future__ import annotations


def test_firms_keep_data_apart(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    d = client.get("/api/v1/hop/firms", headers=h).get_json()["data"]
    assert len(d["firms"]) == 1 and d["firms"][0]["is_main"] == 1 and d["active"] == "house_of_prizm"
    assert client.post("/api/v1/hop/firms", headers=h, json={"name": "Prizm Exports", "gstin": "bad"}).status_code == 400
    r = client.post("/api/v1/hop/firms", headers=h, json={"name": "Prizm Exports", "gstin": "27ABCDE1234F1Z5"})
    assert r.status_code == 200, r.get_json()
    f2 = [f for f in r.get_json()["data"]["firms"] if not f["is_main"]][0]["firm_ws"]
    h2 = {**h, "X-Hop-Firm": f2}
    client.post("/api/v1/hop/customers", headers=h, json={"company": "Main Party"})
    client.post("/api/v1/hop/customers", headers=h2, json={"company": "Export Party"})
    main = [c["company"] for c in client.get("/api/v1/hop/customers", headers=h).get_json()["data"]]
    exp = [c["company"] for c in client.get("/api/v1/hop/customers", headers=h2).get_json()["data"]]
    assert "Main Party" in main and "Export Party" not in main and exp == ["Export Party"]
    prof = client.get("/api/v1/hop/firm-profile", headers=h2).get_json()["data"]
    assert prof.get("firm_name") == "Prizm Exports"
    assert client.get("/api/v1/hop/firms", headers=h2).get_json()["data"]["active"] == f2
    # Someone else's firm id is ignored → main firm.
    bad = {**h, "X-Hop-Firm": "other_company__f2"}
    assert "Main Party" in [c["company"] for c in client.get("/api/v1/hop/customers", headers=bad).get_json()["data"]]
