"""Separate data for every UI (owner, 20-11): the Distributor UI never sees one byte of the Business UI's books,
notes, items or settings, and back; Ask Nexora in one UI cannot use another UI's tools or memory."""
from __future__ import annotations

import pytest


@pytest.fixture
def api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    biz = {**h, "X-Nexora-UI": "business"}
    dist = {**h, "X-Nexora-UI": "distributor"}
    return client, h, biz, dist


def _names(client, path, headers, key="company"):
    d = client.get(path, headers=headers).get_json()["data"]
    rows = d if isinstance(d, list) else (d.get("items") or d.get("notes") or d.get("rows") or [])
    return [r.get(key) or r.get("title") or r.get("text") for r in rows]


def test_distributor_has_its_own_books(api):
    client, h, biz, dist = api
    assert client.post("/api/v1/hop/customers", headers=biz, json={"company": "Biz Party"}).status_code in (200, 201)
    assert client.post("/api/v1/hop/customers", headers=dist, json={"company": "Dist Retailer"}).status_code in (200, 201)
    assert _names(client, "/api/v1/hop/customers", biz) == ["Biz Party"]
    assert _names(client, "/api/v1/hop/customers", dist) == ["Dist Retailer"]
    # An old phone / the web (no UI header) is the Business UI.
    assert _names(client, "/api/v1/hop/customers", h) == ["Biz Party"]


def test_distributor_cannot_pick_a_business_firm(api):
    client, h, biz, dist = api
    client.post("/api/v1/hop/customers", headers=biz, json={"company": "Biz Party"})
    main_biz = client.get("/api/v1/hop/firms", headers=biz).get_json()["data"]["active"]
    # Even when the phone sends the Business firm id, the Distributor UI stays in its own firm.
    forced = {**dist, "X-Hop-Firm": main_biz}
    assert "Biz Party" not in _names(client, "/api/v1/hop/customers", forced)
    d = client.get("/api/v1/hop/firms", headers=dist).get_json()["data"]
    assert d["active"] != main_biz and all(f["firm_ws"] != main_biz for f in d["firms"])
    b = client.get("/api/v1/hop/firms", headers=biz).get_json()["data"]
    assert all(f["firm_ws"] != d["active"] for f in b["firms"])
    # …and the Business UI can't open the Distributor firm either.
    assert _names(client, "/api/v1/hop/customers", {**biz, "X-Hop-Firm": d["active"]}) == ["Biz Party"]


def test_notes_and_items_stay_in_their_ui(api):
    client, h, biz, dist = api
    client.post("/api/v1/hop/notes", headers=biz, json={"text": "business only note"})
    client.post("/api/v1/hop/notes", headers=dist, json={"text": "distributor only note"})
    bn = str(client.get("/api/v1/hop/notes", headers=biz).get_json()["data"])
    dn = str(client.get("/api/v1/hop/notes", headers=dist).get_json()["data"])
    assert "business only note" in bn and "distributor only note" not in bn
    assert "distributor only note" in dn and "business only note" not in dn
    from app import hop_dist_extra  # noqa: F401  (Article Master reads the firm's own catalogue)
    client.post("/api/v1/hop/products", headers=biz, json={"name": "QA Split AC", "selling_price": 100})
    arts = str(client.get("/api/v1/hop/dist/articles", headers=dist).get_json())
    assert "QA Split AC" not in arts


def test_firm_helper_rules(tmp_path):
    import sqlite3

    from app import hop_firms
    from app.hop_schema import ensure_hop_schema

    ensure_hop_schema(str(tmp_path / "f.sqlite3"))
    conn = sqlite3.connect(tmp_path / "f.sqlite3")
    conn.row_factory = sqlite3.Row
    assert hop_firms.active_ws(conn, "co", None, "business") == "co"
    dws = hop_firms.active_ws(conn, "co", None, "distributor")
    assert dws == "co__dist" and dws != "co"
    assert hop_firms.active_ws(conn, "co", "co", "distributor") == dws
    f2 = hop_firms.create(conn, "co", "Second Dist", ui="distributor")["firm_ws"]
    assert hop_firms.active_ws(conn, "co", f2, "distributor") == f2
    assert hop_firms.active_ws(conn, "co", f2, "business") == "co"
    assert [f["firm_ws"] for f in hop_firms.list_firms(conn, "co", "business")] == ["co"]
    assert {f["firm_ws"] for f in hop_firms.list_firms(conn, "co", "distributor")} == {dws, f2}


def test_ask_nexora_tools_per_ui():
    from app.services.nexora_ai_agent import tool_allowed

    assert tool_allowed("get_pending_so", ui="distributor")
    assert tool_allowed("get_market_visits", ui="business")
    assert tool_allowed("read_app", {"path": "/api/v1/order-desk/tree"}, ui="distributor")
    assert tool_allowed("read_app", {"path": "/api/v1/hop/customers"}, ui="distributor") is None
    assert tool_allowed("get_hop_books", ui="executive")
    assert tool_allowed("read_app", {"path": "/api/v1/hop/customers"}, ui="executive")
    assert tool_allowed("get_pending_so", ui="executive") is None
    assert tool_allowed("get_hop_books", ui="distributor") is None


def test_contacts_only_from_own_ui(tmp_path):
    import sqlite3

    from app.services import nexora_contacts

    db = str(tmp_path / "c.sqlite3")
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE hop_customers (workspace_id TEXT, company TEXT, contact_person TEXT, mobile TEXT)")
        c.execute("CREATE TABLE master_retailers (workspace_id TEXT, name TEXT, contact_person TEXT, phone_number TEXT)")
        c.execute("INSERT INTO hop_customers VALUES ('co', 'Ramesh Traders', '', '9811111111')")
        c.execute("INSERT INTO master_retailers VALUES ('co', 'Ramesh Retail', '', '9822222222')")
    biz = [h["name"] for h in nexora_contacts.find(db, ["co"], "Ramesh", ui="business")]
    exe = [h["name"] for h in nexora_contacts.find(db, ["co"], "Ramesh", ui="executive")]
    assert biz == ["Ramesh Traders"] and exe == ["Ramesh Retail"]


def test_catalogue_bulk_delete_goes_to_recycle_bin_in_own_ui_only(api):
    client, h, biz, dist = api
    ids = []
    for n in ("Old Towel", "Old Sheet", "Keep Pillow"):
        r = client.post("/api/v1/hop/products", headers=biz, json={"name": n, "selling_price": 10})
        ids.append((r.get_json().get("data") or {}).get("id"))
    d = client.post("/api/v1/hop/products", headers=dist, json={"name": "Dist Item", "selling_price": 10})
    dist_id = (d.get_json().get("data") or {}).get("id")
    r = client.post("/api/v1/hop/products/bulk-delete", headers=biz, json={"ids": ids[:2] + [dist_id]})
    assert r.status_code == 200 and r.get_json()["data"]["deleted"] == 2
    left = str(client.get("/api/v1/hop/products", headers=biz).get_json())
    assert "Keep Pillow" in left and "Old Towel" not in left
    # The Business UI can't touch the Distributor's item, even by id.
    assert "Dist Item" in str(client.get("/api/v1/hop/products", headers=dist).get_json())
    assert client.post("/api/v1/hop/products/bulk-delete", headers=biz, json={}).status_code == 400
