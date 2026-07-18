import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

os.environ["SECRET_KEY"] = "test-secret"
os.environ["AUTH_ENABLED"] = "false"

from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "master-forms.sqlite3"
    os.environ["DATABASE_PATH"] = str(db_path)
    app = create_app()
    app.config.update(TESTING=True)
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as test_client:
        # Set up a session for the test client to bypass auth checks
        with test_client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user_id"] = 1
            sess["username"] = "test"
            sess["workspace_id"] = "default"
        yield test_client


def test_master_distributor_and_retailer_crud_via_api(client):
    create_resp = client.post(
        "/api/v1/masters/distributors",
        json={
            "name": "Alpha Traders",
            "firm_name": "Alpha Fabrics",
            "firm_nick_name": "Alpha",
            "distributor_code": "D-100",
            "buyer_code": "BC-100",
            "phone_number": "9999999999",
            "email": "alpha@example.com",
            "location": "Delhi",
            "zone": "North",
            "region": "Delhi",
            "address": "Main Road",
            "pincode": "110001",
            "gst_no": "07AAAAA0000A1Z5",
            "payment_terms": "Net 30",
            "credit_limit": 50000,
            "birthday": "1990-01-01",
            "anniversary": "2010-01-01",
        },
    )
    assert create_resp.status_code == 201
    distributor = create_resp.get_json()["data"]
    assert distributor["firm_name"] == "Alpha Fabrics"

    update_resp = client.put(
        f"/api/v1/masters/distributors/{distributor['id']}",
        json={"firm_name": "Alpha International", "phone_number": "8888888888"},
    )
    assert update_resp.status_code == 200
    updated = update_resp.get_json()["data"]
    assert updated["firm_name"] == "Alpha International"
    assert updated["phone_number"] == "8888888888"

    delete_resp = client.delete(f"/api/v1/masters/distributors/{distributor['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()["success"] is True

    retailer_resp = client.post(
        "/api/v1/masters/retailers",
        json={
            "name": "Shop One",
            "contact_person": "Ravi",
            "distributor_id": distributor["id"],
            "phone_number": "7777777777",
            "phone_number_2": "6666666666",
            "email": "shop@example.com",
            "address": "Sector 15",
            "location": "Delhi",
            "state": "Delhi",
            "pincode": "110002",
            "gst_no": "07BBBBB0000B1Z5",
            "category": "General",
            "birthday": "1995-02-02",
            "anniversary": "2012-02-02",
        },
    )
    assert retailer_resp.status_code == 201
    retailer = retailer_resp.get_json()["data"]
    assert retailer["name"] == "Shop One"

    retailer_update = client.put(
        f"/api/v1/masters/retailers/{retailer['id']}",
        json={"category": "Premium", "phone_number_2": "5555555555"},
    )
    assert retailer_update.status_code == 200
    updated_retailer = retailer_update.get_json()["data"]
    assert updated_retailer["category"] == "Premium"
    assert updated_retailer["phone_number_2"] == "5555555555"

    retailer_delete = client.delete(f"/api/v1/masters/retailers/{retailer['id']}")
    assert retailer_delete.status_code == 200


def test_bulk_upload_reinstates_ambiguous_match_detection(tmp_path):
    db_path = tmp_path / "ambiguous.sqlite3"
    db = CentralizedDB(str(db_path))
    # Disable fuzzy matching during creation so we can create similar-named distributors
    d1_id = db.add_master_distributor(
        name="ABC Traders", firm_name="ABC Pvt Ltd", workspace_id="ws-1", allow_fuzzy=False
    )
    d2_id = db.add_master_distributor(
        name="ABC Traders 2", firm_name="ABC Pvt Ltd", workspace_id="ws-1", allow_fuzzy=False
    )

    # Verify distributors were created as separate records
    d1 = db.get_master_distributor(d1_id, workspace_id="ws-1")
    d2 = db.get_master_distributor(d2_id, workspace_id="ws-1")
    assert d1 is not None and d1["name"] == "ABC Traders", f"Got {d1}"
    assert d2 is not None and d2["name"] == "ABC Traders 2", f"Got {d2}"
    assert d1_id != d2_id, "Distributors should be separate records"

    csv_path = tmp_path / "retailers.csv"
    csv_path.write_text(
        "retailer_name,linked_distributor_gst_or_name\n"
        "Alpha Retail,ABC Traders\n",
        encoding="utf-8",
    )

    result = db.bulk_upload_masters("retailers", csv_path, workspace_id="ws-1")

    assert result["ambiguous_distributor_matches"], f"Expected ambiguous matches but got: {result}"
    assert result["unassigned"] >= 1


def test_typo_and_short_code_matching_still_works_alongside_ambiguous_detection(tmp_path):
    """
    REGRESSION GUARD: verifies that adding ambiguous-match detection
    did NOT break the previously-verified (against 2177 rows of real
    production data) typo/short-code distributor matching:
      - "Savitri" -> "Savitri Steel Cement Traders" (short code IS a
        whole word from the full name)
      - "ShriRam" -> "Shri Ram & Co" (short code has no space, while
        the full name does)
    Neither of these should be flagged ambiguous, since there is only
    ONE genuinely close distributor for each reference among a set of
    clearly-distinct distributor names.
    """
    db_path = tmp_path / "typo_regression.sqlite3"
    db = CentralizedDB(str(db_path))

    # A representative slice of genuinely distinct real distributor
    # names (mirrors the real 10-distributor production dataset).
    db.add_master_distributor(name="DCA Marketing", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Shri Ram & Co", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Parnami Textiles", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Kalra Agencies", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Goyal Enterprises", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Balaji Homedecor", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Savitri Steel Cement Traders", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Sain International", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Choice Corner Bombay Dyeing", workspace_id="ws-1", allow_fuzzy=False)
    db.add_master_distributor(name="Bernina International P Ltd", workspace_id="ws-1", allow_fuzzy=False)

    csv_path = tmp_path / "retailers_typo_test.csv"
    csv_path.write_text(
        "retailer_name,linked_distributor_gst_or_name\n"
        "Test Shop A,Savitri\n"
        "Test Shop B,ShriRam\n"
        "Test Shop C,Balaji\n",
        encoding="utf-8",
    )

    result = db.bulk_upload_masters("retailers", csv_path, workspace_id="ws-1")

    assert result["ambiguous_distributor_matches"] == [], (
        f"REGRESSION: genuinely distinct short-code matches should not "
        f"be flagged ambiguous. Got: {result['ambiguous_distributor_matches']}"
    )
    assert result["unassigned"] == 0, (
        f"REGRESSION: all three should have matched their distributor, "
        f"none should be unassigned. Got: {result}"
    )

    retailers = db.list_master_retailers(workspace_id="ws-1")
    by_name = {r["name"]: r for r in retailers}

    savitri_dist = db.get_master_distributor(by_name["Test Shop A"]["distributor_id"], workspace_id="ws-1")
    assert savitri_dist["name"] == "Savitri Steel Cement Traders"

    shriram_dist = db.get_master_distributor(by_name["Test Shop B"]["distributor_id"], workspace_id="ws-1")
    assert shriram_dist["name"] == "Shri Ram & Co"

    balaji_dist = db.get_master_distributor(by_name["Test Shop C"]["distributor_id"], workspace_id="ws-1")
    assert balaji_dist["name"] == "Balaji Homedecor"
