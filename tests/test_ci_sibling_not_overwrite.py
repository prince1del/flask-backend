"""One SO can have several CIs — later invoice must not erase the earlier one."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from centralized_db_system.db import CentralizedDB

SO = "102875630"
CI_337 = "1400009337"
CI_346 = "1400009346"


def _ci_payload(invoice_no: str, sku: str) -> dict:
    return {
        "header": {
            "order_ref_no": SO,
            "invoice_no": invoice_no,
            "buyer_name": "Shri Ram & Co., Meerut",
        },
        "line_items": [
            {"item_name": sku, "item_key": "BLUMEN|104|DB" if "DBSET" in sku else "BLUMEN|104|SB", "qty": 6, "value": 3800},
        ],
    }


def test_second_ci_same_so_does_not_overwrite_first(tmp_path):
    db = CentralizedDB(str(tmp_path / "ci_sibling.sqlite3"))
    dist_id = db.add_master_distributor(name="Shri Ram", workspace_id="ws-1")

    first = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9337.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    second = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9346.pdf",
        commercial_invoice_parsed=_ci_payload(CI_346, "BLUMEN 1+1 SBSET 152X224 7979BGE 104TC"),
        workspace_id="ws-1",
    )

    assert first != second
    rows = db.list_order_lifecycle_by_order_ref_no(SO, workspace_id="ws-1")
    assert len(rows) == 2

    kept = db.get_order_lifecycle_tracking(first, workspace_id="ws-1")
    newer = db.get_order_lifecycle_tracking(second, workspace_id="ws-1")
    assert kept["commercial_invoice_file_reference"] == "/uploads/ci_9337.pdf"
    assert newer["commercial_invoice_file_reference"] == "/uploads/ci_9346.pdf"
    assert db._extract_ci_invoice_no(kept["commercial_invoice_parsed"]) == CI_337
    assert db._extract_ci_invoice_no(newer["commercial_invoice_parsed"]) == CI_346


def test_same_invoice_reupload_stays_on_same_tracking(tmp_path):
    db = CentralizedDB(str(tmp_path / "ci_same.sqlite3"))
    dist_id = db.add_master_distributor(name="Shri Ram", workspace_id="ws-1")
    first = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9337.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    again = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9337_again.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    assert again == first
    assert len(db.list_order_lifecycle_by_order_ref_no(SO, workspace_id="ws-1")) == 1


def test_second_ci_linked_to_so_does_not_replace_first_invoice(tmp_path):
    db = CentralizedDB(str(tmp_path / "ci_link_sibling.sqlite3"))
    dist_id = db.add_master_distributor(name="Shri Ram", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no=SO, distributor_id=dist_id, workspace_id="ws-1"
    )
    db.link_sales_order_to_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        sales_order_file_reference="/uploads/so.pdf",
        sales_order_parsed={"header": {"order_ref_no": SO}},
        workspace_id="ws-1",
    )
    first = db.link_commercial_invoice_to_order_lifecycle(
        order_ref_no=SO,
        commercial_invoice_file_reference="/uploads/ci_9337.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    second = db.link_commercial_invoice_to_order_lifecycle(
        order_ref_no=SO,
        commercial_invoice_file_reference="/uploads/ci_9346.pdf",
        commercial_invoice_parsed=_ci_payload(CI_346, "BLUMEN 1+1 SBSET 152X224 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    assert first == tracking_id
    assert second != first
    kept = db.get_order_lifecycle_tracking(first, workspace_id="ws-1")
    assert db._extract_ci_invoice_no(kept["commercial_invoice_parsed"]) == CI_337
    assert kept["commercial_invoice_file_reference"] == "/uploads/ci_9337.pdf"
    assert kept["sales_order_file_reference"] == "/uploads/so.pdf"
    sibling = db.get_order_lifecycle_tracking(second, workspace_id="ws-1")
    assert db._extract_ci_invoice_no(sibling["commercial_invoice_parsed"]) == CI_346
    # Extra CI is listed as its own invoice; SO file stays on the first row
    # so Sales Orders tab does not show the same SO twice.
    assert not (sibling.get("sales_order_file_reference") or "").strip()


def test_overwritten_ci_stamp_does_not_block_missing_invoice(tmp_path):
    """9346 overwrote 9337 — 9337 stamp must not block 9337 from coming back as a sibling."""
    db = CentralizedDB(str(tmp_path / "ci_ghost.sqlite3"))
    dist_id = db.add_master_distributor(name="Shri Ram", workspace_id="ws-1")
    tracking = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9337.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    db.mark_document_processed("ws-1", "CI", CI_337, tracking_id=tracking)
    # Legacy overwrite: same row now holds 9346, but 9337 stamp remains.
    db._write_ci_onto_tracking(
        tracking,
        "/uploads/ci_9346.pdf",
        json.dumps(_ci_payload(CI_346, "BLUMEN 1+1 SBSET 152X224 7979BGE 104TC")),
        None,
    )
    db.mark_document_processed("ws-1", "CI", CI_346, tracking_id=tracking)

    assert db.is_document_already_processed("ws-1", "CI", CI_346) is True
    assert db.is_document_already_processed("ws-1", "CI", CI_337) is False

    restored = db.save_ci_only_order_lifecycle(
        order_ref_no=SO,
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9337.pdf",
        commercial_invoice_parsed=_ci_payload(CI_337, "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"),
        workspace_id="ws-1",
    )
    assert restored != tracking
    assert db._extract_ci_invoice_no(
        db.get_order_lifecycle_tracking(tracking, workspace_id="ws-1")["commercial_invoice_parsed"]
    ) == CI_346
    assert db._extract_ci_invoice_no(
        db.get_order_lifecycle_tracking(restored, workspace_id="ws-1")["commercial_invoice_parsed"]
    ) == CI_337
    assert len(db.list_order_lifecycle_by_order_ref_no(SO, workspace_id="ws-1")) == 2
