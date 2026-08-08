"""Honorific spelling variants for party global search (Shri / Shree / Sri Ram)."""
from centralized_db_system.db import CentralizedDB


def test_party_name_fold_shri_variants():
    db = CentralizedDB(":memory:")
    assert db._party_name_fold("Shree ram") == "shri ram"
    assert db._party_name_fold("Sri Ram") == "shri ram"
    assert db._party_name_fold("Sriram") == "shriram"
    assert db._party_name_compact("Shree ram") == "shriram"
    assert db._party_name_compact("Shri Ram Distributor") == "shriramdistributor"


def test_search_finds_shri_ram_distributor_via_shree_ram(tmp_path):
    db_path = str(tmp_path / "search_shri.sqlite3")
    db = CentralizedDB(db_path)

    db.add_master_distributor(
        name="Contact",
        firm_name="Shri Ram Distributor",
        firm_nick_name="SRD",
        workspace_id="ws-1",
    )
    # Retailers that already match literal "Shree" — previously this skipped distributor fallback
    db.add_master_retailer(
        name="Shree Ram Furnishing",
        distributor_id=None,
        phone_number="1123922257",
        workspace_id="ws-1",
    )

    with __import__("sqlite3").connect(db_path) as conn:
        db._refresh_global_search_index(conn)

    for query in ("Shree ram", "shriram", "Sriram", "sri ram", "Shri ram"):
        results = db.global_search(query, workspace_id="ws-1")
        dist_names = [
            (r.get("firm_name") or r.get("contact_person") or "")
            for r in results["results"]["distributors"]
        ]
        assert any("Shri Ram Distributor" in n for n in dist_names), (
            f"Query {query!r} should find Shri Ram Distributor. Got distributors={dist_names!r}"
        )
