"""
Verifies fixes to CentralizedDB.global_search():

1. SECURITY: previously searched/returned results across EVERY
   workspace mixed together — no workspace_id filtering existed at
   all. Fixed to filter by workspace_id, matching the pattern already
   established for every other workspace-scoped feature.
2. Coverage expanded: distributors and retailers are now indexed
   separately (was one merged "masters" bucket), with phone/address/
   pincode now searchable too (previously only name/gst/zone/region).
   Sales Orders/CI (order_lifecycle_tracking) and Stock
   (article_master_v2) are now indexed too.
"""
from centralized_db_system.db import CentralizedDB


def test_search_is_workspace_isolated(tmp_path):
    db_path = str(tmp_path / "search_isolation.sqlite3")
    db = CentralizedDB(db_path)

    db.add_master_distributor(name="Bernina International", location="Delhi", workspace_id="ws-1")
    db.add_master_distributor(name="Some Other Company", location="Delhi", workspace_id="ws-2")

    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    ws1_results = db.global_search("Delhi", workspace_id="ws-1")
    ws2_results = db.global_search("Delhi", workspace_id="ws-2")

    ws1_names = [r.get("contact_person") or r.get("firm_name") for r in ws1_results["results"]["distributors"]]
    ws2_names = [r.get("contact_person") or r.get("firm_name") for r in ws2_results["results"]["distributors"]]

    assert any("Bernina" in (n or "") for n in ws1_names)
    assert not any("Bernina" in (n or "") for n in ws2_names), (
        "BUG REPRODUCED: ws-2's search leaked ws-1's distributor"
    )
    assert any("Some Other Company" in (n or "") for n in ws2_names)
    assert not any("Some Other Company" in (n or "") for n in ws1_names)


def test_search_finds_distributor_by_phone_number(tmp_path):
    """Previously only name/gst/zone/region were indexed — phone
    number searches never matched anything."""
    db_path = str(tmp_path / "search_phone.sqlite3")
    db = CentralizedDB(db_path)

    db.add_master_distributor(
        name="Test Distributor", phone_number="9891788026", workspace_id="ws-1"
    )
    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    results = db.global_search("9891788026", workspace_id="ws-1")
    matches = results["results"]["distributors"]
    assert any(
        (m.get("contact_person") == "Test Distributor") for m in matches
    ), f"Searching by phone number should find the distributor. Got: {matches}"


def test_search_returns_structured_columns_not_jumbled_text(tmp_path):
    """The person explicitly asked for name/distributor/phone/etc. in
    SEPARATE fields (a proper spreadsheet-like result), not one
    jumbled concatenated string."""
    db_path = str(tmp_path / "search_structured.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(
        name="Bombay Dyeing Chennai", phone_number="9840440815", location="Chennai", workspace_id="ws-1"
    )
    db.add_master_retailer(
        name="Chennai Bombay Dyeing Showroom", distributor_id=dist_id,
        phone_number="9840440815", location="Chennai", workspace_id="ws-1",
    )
    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    results = db.global_search("Chennai", workspace_id="ws-1")

    dist_match = results["results"]["distributors"][0]
    assert dist_match["firm_name"] == "Bombay Dyeing Chennai"
    assert dist_match["phone_number"] == "9840440815"
    assert dist_match["city"] == "Chennai"
    assert "content" not in dist_match, "Should return separate fields, not one jumbled content string"

    retail_match = results["results"]["retailers"][0]
    assert retail_match["name"] == "Chennai Bombay Dyeing Showroom"
    assert retail_match["distributor_name"] == "Bombay Dyeing Chennai"
    assert "content" not in retail_match


def test_search_by_distributor_nickname(tmp_path):
    """Nicknames like BND / SUP / PTJ must resolve to the distributor
    and its linked retailers."""
    db_path = str(tmp_path / "search_nick.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(
        name="Mukherjee",
        firm_name="Bernina International P Ltd",
        firm_nick_name="Bnd",
        workspace_id="ws-1",
    )
    db.add_master_retailer(
        name="A.N.Textiles",
        distributor_id=dist_id,
        workspace_id="ws-1",
    )

    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    for query in ("bnd", "BND", "Bnd"):
        results = db.global_search(query, workspace_id="ws-1")
        dists = results["results"]["distributors"]
        rets = results["results"]["retailers"]
        assert any(
            "Bernina" in (d.get("firm_name") or "") for d in dists
        ), f"{query} should find Bernina distributor: {dists}"
        assert any(
            (d.get("firm_nick_name") or "").lower() == "bnd" for d in dists
        )
        assert any(r.get("name") == "A.N.Textiles" for r in rets), (
            f"{query} should find linked retailers: {rets}"
        )


def test_search_bernina_finds_linked_retailers_without_bernina_in_shop_name(tmp_path):
    """Searching a distributor name must also return its retailers,
    even when the shop name itself does not contain that word."""
    db_path = str(tmp_path / "search_bernina_linked.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(
        name="Bernina International P Ltd",
        location="Delhi",
        workspace_id="ws-1",
    )
    db.add_master_retailer(
        name="A.N.Textiles",
        distributor_id=dist_id,
        location="Delhi",
        workspace_id="ws-1",
    )
    db.add_master_retailer(
        name="Aakarshan",
        distributor_id=dist_id,
        location="Delhi",
        workspace_id="ws-1",
    )
    other_id = db.add_master_distributor(
        name="Other Dist Co",
        workspace_id="ws-1",
    )
    db.add_master_retailer(
        name="Unrelated Shop",
        distributor_id=other_id,
        workspace_id="ws-1",
    )

    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    results = db.global_search("bernina", workspace_id="ws-1")
    retailers = results["results"]["retailers"]
    names = [r.get("name") for r in retailers]

    assert len(results["results"]["distributors"]) >= 1
    assert "A.N.Textiles" in names
    assert "Aakarshan" in names
    assert "Unrelated Shop" not in names
    assert all(
        "Bernina" in (r.get("distributor_name") or "") for r in retailers
    )



def test_search_rahul_does_not_match_raghu_or_mahaprabhu(tmp_path):
    """Fuzzy partial_ratio used to match 'rahul' to 'RAGHU' at score 80."""
    db_path = str(tmp_path / "search_rahul.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Goyal Enterprises", workspace_id="ws-1")
    db.add_master_retailer(
        name="RAGHU DARSHAN HOME FURNISHERS", distributor_id=dist_id, workspace_id="ws-1"
    )
    db.add_master_retailer(
        name="Mahaprabhu Bedding Stores", distributor_id=dist_id, workspace_id="ws-1"
    )
    db.add_master_retailer(
        name="Rahul Home Decor", distributor_id=dist_id, workspace_id="ws-1"
    )

    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    results = db.global_search("rahul", workspace_id="ws-1")
    names = [r.get("name") for r in results["results"]["retailers"]]

    assert any("Rahul" in (n or "") for n in names)
    assert not any("RAGHU" in (n or "").upper() for n in names)
    assert not any("Mahaprabhu" in (n or "") for n in names)
