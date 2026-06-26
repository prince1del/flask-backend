from pathlib import Path

from app.document_analysis import match_person_names
from centralized_db_system.db import CentralizedDB


def test_fuzzy_matching_handles_close_names():
    assert match_person_names("Rahul Kumar Yadav", "Rahul K Yadav") is True
    assert match_person_names("Rahul Kumar Yadav", "Rahul Yadav") is True


def test_master_distributor_fuzzy_matching_reuses_similar_name(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "fuzzy.sqlite3"))

    first_id = db.add_master_distributor(
        name="Alpha Traders Pvt Ltd",
        gst_no="27AAAAA0000A1Z5",
        zone="North",
        region="Mumbai",
    )
    second_id = db.add_master_distributor(
        name="Alpha Trader Pvt Ltd",
        gst_no="27AAAAA0000A1Z6",
        zone="North",
        region="Mumbai",
    )

    assert second_id == first_id
    assert db.get_master_distributor(first_id)["name"] == "Alpha Traders Pvt Ltd"
