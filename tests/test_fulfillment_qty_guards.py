"""Fulfillment qty updates must be workspace-scoped and non-negative."""

import pytest

from centralized_db_system.db import CentralizedDB


def test_update_fulfilled_quantity_workspace_and_caps(tmp_path):
    db = CentralizedDB(str(tmp_path / "fulfill.sqlite3"))
    tracking = db.create_order_lifecycle_tracking(
        order_ref_no="SO-F1", distributor_id=1, workspace_id="ws-a"
    )
    fid = db.create_order_fulfillment_item(
        order_lifecycle_id=tracking,
        product_code="SKU-1",
        brand="B",
        color="Red",
        ordered_qty=10,
        fulfilled_qty=0,
        workspace_id="ws-a",
    )

    ok = db.update_fulfilled_quantity(fid, fulfilled_increment=4, workspace_id="ws-a")
    assert ok["fulfilled_qty"] == 4

    with pytest.raises(ValueError, match="not found"):
        db.update_fulfilled_quantity(fid, fulfilled_increment=1, workspace_id="ws-other")

    with pytest.raises(ValueError, match="greater than zero"):
        db.update_fulfilled_quantity(fid, fulfilled_increment=-2, workspace_id="ws-a")

    with pytest.raises(ValueError, match="exceed ordered"):
        db.update_fulfilled_quantity(fid, fulfilled_increment=7, workspace_id="ws-a")

    # Still at 4 after rejected attempts
    again = db.update_fulfilled_quantity(fid, fulfilled_increment=1, workspace_id="ws-a")
    assert again["fulfilled_qty"] == 5
