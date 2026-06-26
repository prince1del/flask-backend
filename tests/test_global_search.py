from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_global_search_matches_masters_visit_logs_and_verifications(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "global_search.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        gst_no="27AAAA0000A1Z5",
        zone="North",
        region="West",
    )
    retailer_id = db.add_master_retailer(name="Beta Retail", distributor_id=distributor_id, location="Andheri")

    db.add_distributor_visit_log(distributor_id, "2026-06-25", "09:00", {"notes": "Alpha discussion"})
    db.add_retailer_visit_log(retailer_id, distributor_id, "2026-06-25", "12:00", {"notes": "Counter sales good"})
    db.save_verification_output(
        report_type="invoice",
        reference_id="INV-9582",
        content="Alpha client Rahul Kumar Yadav rate 100 qty 12",
    )

    results = db.global_search("alpha")

    assert results["query"] == "alpha"
    assert any(item["category"] == "masters" for item in results["results"]["masters"])
    assert any(item["category"] == "verifications" for item in results["results"]["verifications"])
    assert any(item["category"] == "visit_logs" for item in results["results"]["visit_logs"])


def test_global_search_is_case_insensitive(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "global_search_case.sqlite3"))
    db.add_master_distributor(name="Punjab Traders", gst_no="27BBBB0000A1Z5", zone="North")

    results = db.global_search("punjab")

    assert results["results"]["masters"]
