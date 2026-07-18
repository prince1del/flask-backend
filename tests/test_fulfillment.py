import io
import pytest
from centralized_db_system.db import CentralizedDB


def test_link_sales_order_updates_existing_fulfillment(tmp_path):
    db_path = str(tmp_path / "ful.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="Dist A", buyer_code="BC1")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-500", distributor_id=distributor_id)

    # create an existing fulfillment item with ordered target
    fid = db.create_order_fulfillment_item(order_lifecycle_id=tracking_id, product_code="Widget", ordered_qty=20, fulfilled_qty=0)

    sales_order_parsed = {"rows": [{"product": "Widget", "quantity": "10", "rate": "100"}]}

    db.link_sales_order_to_order_lifecycle(
        order_ref_no="SO-500",
        distributor_id=distributor_id,
        sales_order_file_reference=None,
        sales_order_parsed=sales_order_parsed,
        workspace_id="default",
    )

    items = db.list_fulfillment_items(tracking_id)
    assert any(item["product_code"] == "Widget" and item["fulfilled_qty"] == 10 for item in items)


def test_link_sales_order_creates_fulfillment_when_missing(tmp_path):
    db_path = str(tmp_path / "ful2.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="Dist B", buyer_code="BC2")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-501", distributor_id=distributor_id)

    sales_order_parsed = {"rows": [{"product": "Gadget", "quantity": "5", "rate": "50"}]}

    db.link_sales_order_to_order_lifecycle(
        order_ref_no="SO-501",
        distributor_id=distributor_id,
        sales_order_file_reference=None,
        sales_order_parsed=sales_order_parsed,
        workspace_id="default",
    )

    items = db.list_fulfillment_items(tracking_id)
    assert any(item["product_code"] == "Gadget" and item["fulfilled_qty"] == 5 for item in items)
