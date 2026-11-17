"""Online catalogue link: owner makes / switches the link; a customer (no login) sees items and orders →
Sale Order in the owner's firm; prices come from NEXORA; wrong / old links and floods are refused."""
from __future__ import annotations


def test_catalog_link_flow(tmp_path, monkeypatch):
    from app import hop_catalog_link
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    hop_catalog_link._recent.clear()
    client, owner, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    p = client.post("/api/v1/hop/products", headers=owner, json={"name": "Linen curtain", "selling_price": 900,
                                                                  "category": "Curtains", "stock_qty": 5})
    pid = p.get_json()["data"]["id"]
    client.post("/api/v1/hop/products", headers=owner, json={"name": "Old stock", "selling_price": 10, "stock_qty": 0})
    assert client.get("/api/v1/hop/catalog-link", headers=owner).get_json()["data"] is None
    link = client.put("/api/v1/hop/catalog-link", headers=owner, json={"hide_out_of_stock": True}).get_json()["data"]
    token = link["token"]
    assert link["url"].endswith("/c/" + token)
    # the page and the list need no login
    assert client.get(f"/c/{token}").status_code == 200
    cat = client.get(f"/api/v1/public/catalog/{token}").get_json()["data"]
    names = [i["name"] for i in cat["items"]]
    assert names == ["Linen curtain"] and cat["items"][0]["price"] == 900      # out-of-stock hidden
    # order: price from NEXORA even if the page sends another
    r = client.post(f"/api/v1/public/catalog/{token}/order",
                    json={"name": "Asha", "phone": "98765 43210", "lines": [{"id": pid, "qty": 2, "rate": 1}]})
    assert r.status_code == 200, r.get_json()
    docs = client.get("/api/v1/hop/party-transactions?txn_types=65", headers=owner).get_json()["data"]
    assert len(docs) == 1 and abs(float(docs[0]["total_amount"]) - 1800) < 1 and docs[0]["party_name"] == "Asha"
    # bad input
    bad = client.post(f"/api/v1/public/catalog/{token}/order", json={"name": "A", "phone": "1", "lines": []})
    assert bad.status_code == 400
    # an id from another firm / unknown id never becomes a line
    none = client.post(f"/api/v1/public/catalog/{token}/order", json={"name": "Ravi", "phone": "9000000001",
                                                                       "lines": [{"id": 999999, "qty": 1}]})
    assert none.status_code == 400
    # prices hidden
    client.put("/api/v1/hop/catalog-link", headers=owner, json={"show_prices": False})
    assert "price" not in client.get(f"/api/v1/public/catalog/{token}").get_json()["data"]["items"][0]
    # new code: the old link stops working; switched off: nothing shown
    new = client.put("/api/v1/hop/catalog-link", headers=owner, json={"new_code": True}).get_json()["data"]["token"]
    assert new != token and client.get(f"/api/v1/public/catalog/{token}").status_code == 404
    client.put("/api/v1/hop/catalog-link", headers=owner, json={"active": False})
    assert client.get(f"/api/v1/public/catalog/{new}").status_code == 404
    client.put("/api/v1/hop/catalog-link", headers=owner, json={"active": True})
    # flood: the same phone is limited
    codes = [client.post(f"/api/v1/public/catalog/{new}/order", json={"name": "Spam", "phone": "9111111111",
                                                                       "lines": [{"id": pid, "qty": 1}]}).status_code
             for _ in range(hop_catalog_link.ORDERS_PER_HOUR + 1)]
    assert codes[-1] == 400 and codes[0] == 200
