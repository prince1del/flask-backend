"""Owner notes #32 / #35 (21-11): bill numbers never repeat; delete frees the number; Cancel keeps it."""
from __future__ import annotations


def _setup(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    cid = client.post("/api/v1/hop/customers", headers=h, json={"company": "Number Hotel"}).get_json()["data"]["id"]
    return client, h, cid


def _bill(client, h, cid, **kw):
    body = {"txn_type": 1, "customer_id": cid, "txn_date": "2026-11-21", "lines": [{"item_name": "x", "qty": 1, "rate": 100}]}
    body.update(kw)
    return client.post("/api/v1/hop/party-transactions", headers=h, json=body)


def _next(client, h):
    return client.get("/api/v1/hop/party-transactions/next-number?txn_type=1&txn_date=2026-11-21",
                      headers=h).get_json()["data"]["txn_number"]


def test_delete_frees_the_number_cancel_keeps_it(tmp_path, monkeypatch):
    client, h, cid = _setup(tmp_path, monkeypatch)
    b1 = _bill(client, h, cid).get_json()["data"]
    b2 = _bill(client, h, cid).get_json()["data"]
    n2 = b2["txn_number"]
    assert n2.endswith("/2")
    assert client.delete(f"/api/v1/hop/party-transactions/{b2['party_txn_id']}", headers=h).status_code == 200
    assert _next(client, h) == n2                      # #35: bill 2 deleted → next is 2 again
    b2b = _bill(client, h, cid).get_json()["data"]
    assert b2b["txn_number"] == n2
    # the old deleted bill 2 cannot come back with the same number
    r = client.post(f"/api/v1/hop/recycle-bin/docs/{b2['party_txn_id']}/restore", headers=h)
    assert r.status_code == 400 and "belongs to another bill" in r.get_json()["error"]["message"]
    # Cancel bill 1: it keeps number 1, shows as Cancelled, and stops counting in the party balance
    r = client.post(f"/api/v1/hop/party-transactions/{b1['party_txn_id']}/cancel", headers=h, json={"reason": "wrong party"})
    assert r.status_code == 200 and r.get_json()["data"]["cancelled"]
    rows = client.get("/api/v1/hop/party-transactions?txn_types=1", headers=h).get_json()["data"]
    c = next(x for x in rows if x["id"] == b1["party_txn_id"])
    assert c["status_text"].startswith("Cancelled") and "wrong party" in c["status_text"] and c["balance_amount"] == 0
    assert _next(client, h).endswith("/3")
    party_rows = client.get(f"/api/v1/hop/party-transactions?party_type=customer&party_id={cid}", headers=h).get_json()["data"]
    assert all(x["id"] != b1["party_txn_id"] for x in party_rows)


def test_used_number_is_refused(tmp_path, monkeypatch):
    client, h, cid = _setup(tmp_path, monkeypatch)
    n1 = _bill(client, h, cid).get_json()["data"]["txn_number"]
    r = _bill(client, h, cid, txn_number=n1, client_ref="phone-dup-1")   # #32: offline bill with number 1 again
    assert r.status_code == 400 and "already used" in r.get_json()["error"]["message"]
    ok = _bill(client, h, cid, txn_number=n1.rsplit("/", 1)[0] + "/PO1-1", client_ref="phone-ok-1")
    assert ok.status_code == 201
    # the same offline bill sent twice is still saved once (client_ref), not refused as a duplicate number
    again = _bill(client, h, cid, txn_number=n1.rsplit("/", 1)[0] + "/PO1-1", client_ref="phone-ok-1")
    assert again.status_code == 200 and again.get_json()["data"]["already_saved"] is True


def test_offline_series_skips_deleted(tmp_path, monkeypatch):
    client, h, cid = _setup(tmp_path, monkeypatch)
    s = client.get("/api/v1/hop/party-transactions/offline-series", headers=h).get_json()["data"]["series"]["1"]
    a = _bill(client, h, cid, txn_number=s["base"] + "1", client_ref="o1").get_json()["data"]
    _bill(client, h, cid, txn_number=s["base"] + "2", client_ref="o2")
    client.delete(f"/api/v1/hop/party-transactions/{a['party_txn_id']}", headers=h)
    s2 = client.get("/api/v1/hop/party-transactions/offline-series", headers=h).get_json()["data"]["series"]["1"]
    assert s2["next"] == 3            # the highest kept number + 1 (the phone also never goes below its own counter)


def test_price_sheet_reads_ean_column():
    """Owner #37: an "EAN code" column goes to the article's barcode, not to the article code."""
    from app import hop_article_sheet as S

    rules = dict((k, v) for k, v in S._RULES)
    assert "ean" in rules["barcode"] and S.ROLE_LABELS["barcode"] == "EAN / barcode"
    first = [k for k, words in S._RULES if any(w in "ean code" for w in words)][0]
    assert first == "barcode"


def test_bulk_delete_bills(tmp_path, monkeypatch):
    client, h, cid = _setup(tmp_path, monkeypatch)
    a = _bill(client, h, cid).get_json()["data"]["party_txn_id"]
    b = _bill(client, h, cid).get_json()["data"]["party_txn_id"]
    r = client.post("/api/v1/hop/party-transactions/bulk-delete", headers=h, json={"ids": [a, b, 99999]})
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["deleted"] == 2 and d["skipped"][0]["id"] == 99999
    assert _next(client, h).endswith("/1")
