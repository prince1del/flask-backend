"""
Verifies the 4 missing CRUD functions that CP's masters.py routes
called but which did not exist in db.py (confirmed via a real test
failure: `AttributeError: 'CentralizedDB' object has no attribute
'update_master_distributor'`):
  - update_master_distributor()
  - delete_master_distributor()  (hard delete)
  - update_master_retailer()
  - delete_master_retailer()  (hard delete)

Also verifies get_master_retailer() now returns the newer fields
(contact_person, state, pincode, category, birthday, anniversary,
phone_number_2) that it was previously missing.
"""
from centralized_db_system.db import CentralizedDB


def test_update_master_distributor_partial_update_and_workspace_safety(tmp_path):
    db_path = str(tmp_path / "update_dist.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(
        name="Original Name", firm_name="Original Firm", phone_number="1111111111",
        workspace_id="ws-1",
    )

    updated = db.update_master_distributor(
        dist_id, "ws-1", firm_name="Updated Firm", phone_number="2222222222"
    )
    assert updated is not None
    assert updated["firm_name"] == "Updated Firm"
    assert updated["phone_number"] == "2222222222"
    # Untouched fields should remain unchanged
    assert updated["name"] == "Original Name"

    # Cross-tenant safety: ws-2 must not be able to update ws-1's distributor
    blocked = db.update_master_distributor(dist_id, "ws-2", firm_name="Hacked Name")
    assert blocked is None, "BUG: a different workspace was able to update this distributor"

    unchanged = db.get_master_distributor(dist_id, workspace_id="ws-1")
    assert unchanged["firm_name"] == "Updated Firm", "ws-2's blocked update should not have applied"


def test_delete_master_distributor_is_hard_delete_and_workspace_safe(tmp_path):
    db_path = str(tmp_path / "delete_dist.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="To Delete", workspace_id="ws-1")

    # Cross-tenant: ws-2 cannot delete ws-1's distributor
    blocked = db.delete_master_distributor(dist_id, "ws-2")
    assert blocked is False

    deleted = db.delete_master_distributor(dist_id, "ws-1")
    assert deleted is True

    record = db.get_master_distributor(dist_id, workspace_id="ws-1")
    assert record is None, "Delete should hard-remove the distributor row"


def test_update_master_retailer_including_distributor_reassignment(tmp_path):
    db_path = str(tmp_path / "update_retail.sqlite3")
    db = CentralizedDB(db_path)

    dist_a = db.add_master_distributor(name="Distributor A", workspace_id="ws-1")
    dist_b = db.add_master_distributor(name="Distributor B", workspace_id="ws-1")
    retailer_id = db.add_master_retailer(
        name="Shop One", distributor_id=dist_a, contact_person="Original Contact",
        workspace_id="ws-1",
    )

    updated = db.update_master_retailer(
        retailer_id, "ws-1", distributor_id=dist_b, contact_person="New Contact"
    )
    assert updated is not None
    assert updated["distributor_id"] == dist_b
    assert updated["contact_person"] == "New Contact"

    # Reassigning to a distributor that doesn't exist should fail loudly
    try:
        db.update_master_retailer(retailer_id, "ws-1", distributor_id=999999)
        assert False, "Should have raised ValueError for a non-existent distributor"
    except ValueError:
        pass


def test_delete_master_retailer_is_hard_delete(tmp_path):
    db_path = str(tmp_path / "delete_retail.sqlite3")
    db = CentralizedDB(db_path)

    retailer_id = db.add_master_retailer(name="To Delete Shop", distributor_id=None, workspace_id="ws-1")
    deleted = db.delete_master_retailer(retailer_id, "ws-1")
    assert deleted is True

    record = db.get_master_retailer(retailer_id, workspace_id="ws-1")
    assert record is None, "Delete should hard-remove the retailer row"


def test_get_master_retailer_includes_previously_missing_fields(tmp_path):
    """BUG REPRODUCED (before fix): get_master_retailer() didn't
    SELECT contact_person/state/pincode/category/birthday/anniversary/
    phone_number_2 at all — meaning the API response right after
    creating a retailer with these fields would silently omit them."""
    db_path = str(tmp_path / "get_retail_fields.sqlite3")
    db = CentralizedDB(db_path)

    retailer_id = db.add_master_retailer(
        name="Full Fields Shop",
        distributor_id=None,
        contact_person="Suresh",
        state="Maharashtra",
        pincode="400001",
        category="General Store",
        birthday="1995-05-05",
        anniversary="2015-05-05",
        phone_number_2="9999999999",
        workspace_id="ws-1",
    )

    record = db.get_master_retailer(retailer_id, workspace_id="ws-1")
    assert record["contact_person"] == "Suresh"
    assert record["state"] == "Maharashtra"
    assert record["pincode"] == "400001"
    assert record["category"] == "General Store"
    assert record["birthday"] == "1995-05-05"
    assert record["anniversary"] == "2015-05-05"
    assert record["phone_number_2"] == "9999999999"
