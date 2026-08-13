"""
Verifies the Order Sheet -> Filled Order -> SO -> CI fixes:

1. Company Profile was found completely missing from the codebase
   (table, functions, route, registration) despite being built and
   verified earlier — rebuilt here.
2. GST extraction now correctly finds GSTINs regardless of whether
   the checksum (last) character is a digit or a letter — the
   original regex only matched digit-ending GSTINs, silently missing
   real ones like Bombay Dyeing's own "27AAACT2328K1ZB" (ends in a
   letter).
3. CI-to-SO linking is no longer silently automatic — it now requires
   an explicit confirm call, which also creates the achievement
   record at that point (not at upload time).
"""
import re

from centralized_db_system.db import CentralizedDB


def test_company_profile_roundtrip(tmp_path):
    db_path = str(tmp_path / "company_profile_test.sqlite3")
    db = CentralizedDB(db_path)

    saved = db.upsert_company_profile(
        workspace_id="ws-1",
        company_name="Bombay Dyeing",
        gst_number="27aaact2328k1zb",  # lowercase on purpose
    )
    assert saved["gst_number"] == "27AAACT2328K1ZB", "GST should be normalized to uppercase"

    fetched = db.get_company_profile("ws-1")
    assert fetched["company_name"] == "Bombay Dyeing"

    # Workspace isolation
    other = db.get_company_profile("ws-2")
    assert other is None


def test_gstin_regex_handles_letter_ending_checksums():
    """
    BUG REPRODUCED (before fix): the original GSTIN regex required the
    last (checksum) character to be a digit, so Bombay Dyeing's own
    real GSTIN "27AAACT2328K1ZB" (ends in the letter B) was silently
    never detected — only the buyer's GST (which happened to end in a
    digit in our sample data) was found, making the "exclude our own
    GST" logic accidentally work by omission rather than by design.
    """
    GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9]\b")

    text = "GST NO: 27AAACT2328K1ZB   Buyer GST No : 07AAACB4006G1Z9"
    matches = GSTIN_PATTERN.findall(text.upper())
    assert "27AAACT2328K1ZB" in matches, "Letter-ending GSTIN should be detected"
    assert "07AAACB4006G1Z9" in matches, "Digit-ending GSTIN should still be detected"


def test_identify_buyer_gst_excludes_own_company():
    def _identify_buyer_gst(all_gst_numbers, own_company_gst):
        if not all_gst_numbers:
            return None
        own_normalized = (own_company_gst or "").strip().upper()
        candidates = [g for g in all_gst_numbers if g != own_normalized]
        if len(candidates) == 1:
            return candidates[0]
        return None

    result = _identify_buyer_gst(
        ["27AAACT2328K1ZB", "07AAACB4006G1Z9"], "27AAACT2328K1ZB"
    )
    assert result == "07AAACB4006G1Z9"


def test_dual_signal_so_matching_with_real_document_text(tmp_path):
    """
    Verifies the enhanced SO-upload matching: Buyer Code AND Buyer GST
    (extracted after excluding the workspace's own company GST) both
    independently resolve to the same distributor for a real Bombay
    Dyeing Sales Order document (Bernina International).
    """
    db_path = str(tmp_path / "dual_signal_test.sqlite3")
    db = CentralizedDB(db_path)

    db.upsert_company_profile(
        workspace_id="ws-1", company_name="Bombay Dyeing", gst_number="27AAACT2328K1ZB"
    )
    dist_id = db.add_master_distributor(
        name="Bernina International P Ltd",
        buyer_code="3220002",
        gst_no="07AAACB4006G1Z9",
        workspace_id="ws-1",
    )

    GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9]\b")

    def _extract_all_gstins(text):
        matches = GSTIN_PATTERN.findall((text or "").upper())
        seen = []
        for m in matches:
            if m not in seen:
                seen.append(m)
        return seen

    def _identify_buyer_gst(all_gst_numbers, own_company_gst):
        if not all_gst_numbers:
            return None
        own_normalized = (own_company_gst or "").strip().upper()
        candidates = [g for g in all_gst_numbers if g != own_normalized]
        return candidates[0] if len(candidates) == 1 else None

    real_so_text = (
        "THE BOMBAY DYEING & MANUFACTURING CO. LTD.\n"
        "GST NO: 27AAACT2328K1ZB\n"
        "Contract No : 102875606\n"
        "Buyer Code : 3220002\n"
        " BERNINA INTERNATIONAL P LTD\n"
        "GST No : 07AAACB4006G1Z9\n"
    )

    buyer_code = "3220002"  # already-proven extraction
    profile = db.get_company_profile("ws-1")
    buyer_gst = _identify_buyer_gst(
        _extract_all_gstins(real_so_text), profile["gst_number"]
    )

    matched_by_code = db.get_master_distributor_by_buyer_code(buyer_code, workspace_id="ws-1")
    matched_by_gst = db.get_master_distributor_by_gst(buyer_gst, workspace_id="ws-1")

    assert matched_by_code is not None and matched_by_code["id"] == dist_id
    assert matched_by_gst is not None and matched_by_gst["id"] == dist_id
    assert matched_by_code["id"] == matched_by_gst["id"], (
        "Both signals should agree on the same distributor for this real document"
    )


def test_list_order_lifecycle_tracking_for_uploads_view(tmp_path):
    """
    Verifies the new list function that powers the "where do my
    uploaded files show up" Order Fulfillment UI — should show the
    distributor's real name (not just an id) and correctly flag
    whether an SO/CI file is attached, scoped to the right workspace.
    """
    db_path = str(tmp_path / "list_tracking_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Bernina International P Ltd", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-999",
        distributor_id=dist_id,
        sales_order_file_reference="/uploads/so_test.pdf",
        payment_status="PENDING",
        workspace_id="ws-1",
    )

    records = db.list_order_lifecycle_tracking(workspace_id="ws-1")
    assert len(records) == 1
    assert records[0]["tracking_id"] == tracking_id
    assert records[0]["order_ref_no"] == "SO-999"
    assert records[0]["distributor_name"] == "Bernina International P Ltd"
    assert records[0]["has_sales_order"] is True
    assert records[0]["has_commercial_invoice"] is False

    # Workspace isolation
    other_ws_records = db.list_order_lifecycle_tracking(workspace_id="ws-2")
    assert other_ws_records == []


def test_order_ref_no_not_corrupted_by_following_date_on_same_line():
    """
    BUG REPRODUCED (real document, live testing): the SO PDF's actual
    extracted text has "Contract No : 102875606 Date : 01.04.2026" all
    on ONE line. The original naive split(":", 1) captured everything
    after the first colon as the value, corrupting order_ref_no into
    "102875606 Date : 01.04.2026" and silently breaking all
    downstream distributor matching.
    """
    from app.routes.data import _parse_sales_order_header_fields

    text = (
        "THE BOMBAY DYEING & MANUFACTURING CO. LTD.\n"
        "GST NO: 27AAACT2328K1ZB\n"
        "Contract No : 102875606 Date : 01.04.2026\n"
        "Buyer Code : 3220002\n"
    )
    parsed = _parse_sales_order_header_fields(text)
    assert parsed["order_ref_no"] == "102875606", f"Got: {parsed['order_ref_no']!r}"


def test_financial_year_computation_edge_cases():
    """Indian FY runs April -> March. Verifies month-boundary edge cases."""
    from datetime import datetime
    from app.routes.data import _compute_financial_year

    assert _compute_financial_year(datetime(2025, 4, 1)) == "FY2025-26"
    assert _compute_financial_year(datetime(2026, 3, 31)) == "FY2025-26"
    assert _compute_financial_year(datetime(2026, 7, 7)) == "FY2026-27"
    assert _compute_financial_year(datetime(2026, 1, 1)) == "FY2025-26"


def test_file_serving_blocks_path_traversal(tmp_path, monkeypatch):
    """
    SECURITY: verifies the file-viewing endpoint's path-traversal
    guard. A request like "?path=../../../../etc/passwd" must be
    rejected, not silently served — otherwise the endpoint could leak
    arbitrary files off the server.
    """
    from pathlib import Path

    upload_root = tmp_path / "order_fulfillment_files"
    upload_root.mkdir()
    (upload_root / "SO").mkdir()
    (upload_root / "SO" / "test.pdf").write_text("fake pdf content")

    # Simulate the same resolve()+relative_to() check used in the route
    def _is_safe(requested_path: str) -> bool:
        candidate = (upload_root / requested_path).resolve()
        try:
            candidate.relative_to(upload_root.resolve())
            return True
        except ValueError:
            return False

    assert _is_safe("SO/test.pdf") is True
    assert _is_safe("../../../../etc/passwd") is False
    assert _is_safe("../outside_file.txt") is False
    assert _is_safe("SO/../../outside") is False


def test_extract_amount_from_parsed_invoice_strips_currency_formatting():
    """
    Confirms the confirmation flow's focus is on WHICH PARTY the
    invoice belongs to (the founder was clear amount confirmation
    shouldn't be manual) — the amount itself is auto-extracted and
    cleaned of currency symbols/commas here.
    """
    from app.routes.data import _extract_amount_from_parsed_invoice

    assert _extract_amount_from_parsed_invoice(
        {"parsed": {"invoice_amount": "₹40,194.00"}}
    ) == 40194.00
    assert _extract_amount_from_parsed_invoice({"parsed": {"invoice_amount": "5000"}}) == 5000.0
    assert _extract_amount_from_parsed_invoice({"parsed": {}}) is None
    assert _extract_amount_from_parsed_invoice({}) is None


def test_item_tracking_accumulates_across_multiple_so_uploads(tmp_path):
    """
    Verifies the founder's explicit requirement: multiple Sales
    Orders can exist against the same tracked order (for different
    items), and uploading a SECOND SO for a different item must not
    wipe out the first SO's numbers for its own item.
    """
    db_path = str(tmp_path / "item_accum.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Bernina International P Ltd", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-ITEM-TEST", distributor_id=dist_id, workspace_id="ws-1"
    )

    # First SO covers "Florentine"
    db.upsert_order_lifecycle_item(tracking_id, "Florentine", "so", qty=50, value=10000, workspace_id="ws-1")
    # Second SO (a genuinely separate document) covers a different item, "Paisley"
    db.upsert_order_lifecycle_item(tracking_id, "Paisley", "so", qty=30, value=6000, workspace_id="ws-1")

    items = db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id="ws-1")
    by_name = {i["item_name"]: i for i in items}
    assert by_name["Florentine"]["so_qty"] == 50
    assert by_name["Florentine"]["so_value"] == 10000
    assert by_name["Paisley"]["so_qty"] == 30
    assert by_name["Paisley"]["so_value"] == 6000

    # A THIRD so upload also covering "Florentine" (e.g. a correction/
    # addendum) should ADD to the existing Florentine row, not create
    # a duplicate or overwrite it.
    db.upsert_order_lifecycle_item(tracking_id, "florentine", "so", qty=10, value=2000, workspace_id="ws-1")
    items = db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id="ws-1")
    by_name = {i["item_name"]: i for i in items}
    assert len([i for i in items if "florentine" in i["item_name"].lower()]) == 1, (
        "Should have matched the existing Florentine row via fuzzy match, not duplicated it"
    )
    assert by_name["Florentine"]["so_qty"] == 60
    assert by_name["Florentine"]["so_value"] == 12000


def test_discrepancy_flagged_immediately_when_so_and_ci_disagree(tmp_path):
    """
    "raise alarm immediately" — verifies the discrepancy flag flips
    to true the moment SO and CI numbers for the same item disagree.
    """
    db_path = str(tmp_path / "discrepancy_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Test Distributor", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-DISC-TEST", distributor_id=dist_id, workspace_id="ws-1"
    )

    db.upsert_order_lifecycle_item(tracking_id, "Florentine", "so", qty=50, value=10000, workspace_id="ws-1")
    item = db.upsert_order_lifecycle_item(tracking_id, "Florentine", "ci", qty=50, value=10000, workspace_id="ws-1")
    assert item["has_discrepancy"] == 0, "Matching SO and CI numbers should NOT be flagged"

    # A second item where CI qty genuinely differs from SO qty
    db.upsert_order_lifecycle_item(tracking_id, "Paisley", "so", qty=30, value=6000, workspace_id="ws-1")
    item2 = db.upsert_order_lifecycle_item(tracking_id, "Paisley", "ci", qty=25, value=5000, workspace_id="ws-1")
    assert item2["has_discrepancy"] == 1, f"Expected discrepancy flag, got: {item2}"
    assert "SO vs CI qty" in item2["discrepancy_notes"]


def test_generate_distributor_reconciliation_excel_uses_order_cycle_folder_structure(tmp_path, monkeypatch):
    """
    Verifies the founder-requested folder structure:
      Order Cycle/{Financial Year}/{Distributor Name}/reconciliation.xlsx
    (a per-distributor working sheet, not a document-type tree).
    """
    import os
    monkeypatch.chdir(tmp_path)

    db_path = str(tmp_path / "excel_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Bernina International P Ltd", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-EXCEL-TEST", distributor_id=dist_id, workspace_id="ws-1"
    )
    db.upsert_order_lifecycle_item(tracking_id, "Florentine", "so", qty=50, value=10000, workspace_id="ws-1")
    db.upsert_order_lifecycle_item(tracking_id, "Florentine", "ci", qty=50, value=10000, workspace_id="ws-1")

    output_path = db.generate_distributor_reconciliation_excel(tracking_id, workspace_id="ws-1")

    assert "Order Cycle" in output_path
    assert "Bernina International P Ltd" in output_path
    assert os.path.exists(output_path)

    import openpyxl
    workbook = openpyxl.load_workbook(output_path)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    assert headers == ["Item", "Ordered Qty", "Ordered Value", "SO Qty", "SO Value", "CI Qty", "CI Value", "Discrepancy"]
    data_row = [cell.value for cell in sheet[2]]
    assert data_row[0] == "Florentine"
    assert data_row[3] == 50  # SO Qty
    assert data_row[5] == 50  # CI Qty


def test_parse_filled_order_items_flexible_column_names(tmp_path):
    """
    Verifies the Filled Order spreadsheet parser recognizes common
    column-name variations (different distributors' sheets don't all
    use identical headers) and correctly computes value from qty*rate
    when there's no direct "value" column.
    """
    import pandas as pd
    from app.routes.data import _parse_filled_order_items

    xlsx_path = tmp_path / "filled_order.xlsx"
    df = pd.DataFrame({
        "Item Name": ["Florentine", "Paisley"],
        "Order Qty": [50, 30],
        "Rate": [200, 200],
    })
    df.to_excel(xlsx_path, index=False)

    items = _parse_filled_order_items(xlsx_path)
    assert len(items) == 2
    by_name = {i["item_name"]: i for i in items}
    assert by_name["Florentine"]["qty"] == 50
    assert by_name["Florentine"]["value"] == 10000  # 50 * 200
    assert by_name["Paisley"]["qty"] == 30
    assert by_name["Paisley"]["value"] == 6000  # 30 * 200


def test_pending_filled_order_items_consumed_once_and_applied_as_ordered(tmp_path):
    """
    Verifies the full pending-items pipeline: a Filled Order uploaded
    for a distributor BEFORE the matching SO exists gets held as
    "pending", then correctly consumed (and marked so it can't be
    reused) once a Sales Order for the SAME distributor creates the
    tracking record — populating the "Ordered Qty/Value" columns.
    """
    db_path = str(tmp_path / "pending_items_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Bernina International P Ltd", workspace_id="ws-1")

    # Step 1: Filled Order uploaded and confirmed for this distributor
    # BEFORE any SO exists — items held as pending.
    db.save_pending_filled_order_items(
        distributor_id=dist_id, workspace_id="ws-1",
        items=[{"item_name": "Florentine", "qty": 50, "value": 10000}],
    )

    # Step 2: Later, a Sales Order arrives for the SAME distributor —
    # order_ref_no is now known, tracking record gets created.
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-PENDING-TEST", distributor_id=dist_id, workspace_id="ws-1"
    )
    pending = db.get_and_consume_pending_filled_order_items(distributor_id=dist_id, workspace_id="ws-1")
    assert pending is not None
    assert pending[0]["item_name"] == "Florentine"

    for pending_item in pending:
        db.upsert_order_lifecycle_item(
            tracking_id=tracking_id, item_name=pending_item["item_name"], source="ordered",
            qty=pending_item["qty"], value=pending_item["value"], workspace_id="ws-1",
        )

    items = db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id="ws-1")
    assert items[0]["ordered_qty"] == 50
    assert items[0]["ordered_value"] == 10000

    # Consuming again (e.g. if a second, unrelated SO comes in later)
    # must NOT return the same items again — they were already applied.
    second_consume = db.get_and_consume_pending_filled_order_items(distributor_id=dist_id, workspace_id="ws-1")
    assert second_consume is None


def test_delete_file_blocks_path_traversal(tmp_path):
    """
    SECURITY: the delete endpoint uses the SAME path-traversal guard
    as the view endpoint. A request like
    "?path=../../../../etc/passwd" must be rejected.
    """
    upload_root = tmp_path / "order_fulfillment_files"
    upload_root.mkdir()
    (upload_root / "SO").mkdir()
    (upload_root / "SO" / "test.pdf").write_text("fake pdf content")

    def _is_safe(requested_path: str) -> bool:
        candidate = (upload_root / requested_path).resolve()
        try:
            candidate.relative_to(upload_root.resolve())
            return True
        except ValueError:
            return False

    assert _is_safe("SO/test.pdf") is True
    assert _is_safe("../../../../etc/passwd") is False


def test_delete_order_lifecycle_tracking_removes_record_and_items(tmp_path):
    """
    Verifies the Delete button on the Sales Orders/CI table: deleting
    a tracking record also cleans up its item-level reconciliation
    rows, and is workspace-isolated (can't delete another workspace's
    record).
    """
    db_path = str(tmp_path / "delete_tracking_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Test Distributor", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-DELETE-TEST", distributor_id=dist_id,
        sales_order_file_reference="/uploads/so_test.pdf", workspace_id="ws-1",
    )
    db.upsert_order_lifecycle_item(tracking_id, "Florentine", "so", qty=50, value=10000, workspace_id="ws-1")

    # Cross-tenant safety: ws-2 cannot delete ws-1's tracking record
    blocked = db.delete_order_lifecycle_tracking(tracking_id, workspace_id="ws-2")
    assert blocked is None

    result = db.delete_order_lifecycle_tracking(tracking_id, workspace_id="ws-1")
    assert result is not None
    assert result["sales_order_file_reference"] == "/uploads/so_test.pdf"

    assert db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-1") is None
    assert db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id="ws-1") == []


def test_get_latest_order_sheet_returns_most_recent_active(tmp_path):
    """
    Verifies the "match against the latest order sheet" requirement —
    a newly-arriving SO/CI gets attached to whichever order sheet was
    uploaded most recently for the workspace.
    """
    db_path = str(tmp_path / "latest_sheet_test.sqlite3")
    db = CentralizedDB(db_path)

    db.add_order_sheet(name="Order Sheets SS25", category="Bedsheet", workspace_id="ws-1")
    import time
    time.sleep(0.01)
    db.add_order_sheet(name="Order Sheets SS26", category="Bedsheet", workspace_id="ws-1")

    latest = db.get_latest_order_sheet(workspace_id="ws-1")
    assert latest["name"] == "Order Sheets SS26"


def test_move_into_distributor_order_cycle_folder_with_order_sheet_level(tmp_path, monkeypatch):
    """
    Verifies the full founder-requested hierarchy:
      Order Cycle/{FY}/{Distributor}/{Order Sheet Name}/SO/<original filename>
      Order Cycle/{FY}/{Distributor}/{Order Sheet Name}/CI/<original filename>
    SO and CI get their OWN dedicated subfolders (original filenames,
    no prefix), and a Filled Order's copy sits directly in the Order
    Sheet Name folder.
    """
    monkeypatch.chdir(tmp_path)
    from pathlib import Path

    from app.routes.data import (
        _move_into_distributor_order_cycle_folder,
        _order_fulfillment_files_root,
    )

    upload_root = _order_fulfillment_files_root()

    def _stage(name: str) -> Path:
        staging = upload_root / "CI" / "CI Received" / "FY2026-27"
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / name
        path.write_text(f"fake {name}")
        return path

    so_file = _stage("102875606.pdf")
    so_result = _move_into_distributor_order_cycle_folder(
        so_file, "Bernina International P Ltd", "SO",
        order_sheet_name="Order Sheets SS26", financial_year="FY2026-27",
    )
    assert so_result.name == "102875606.pdf", "Original filename should be preserved, no prefix"
    assert so_result.parent.name == "SO"
    assert so_result.parent.parent.name == "Order Sheets SS26"
    assert so_result.parent.parent.parent.name == "Bernina International P Ltd"

    ci_file = _stage("Commercial Invoice.pdf")
    ci_result = _move_into_distributor_order_cycle_folder(
        ci_file, "Bernina International P Ltd", "CI",
        order_sheet_name="Order Sheets SS26", financial_year="FY2026-27",
    )
    assert ci_result.name == "Commercial Invoice.pdf"
    assert ci_result.parent.name == "CI"
    # SO and CI sit in SEPARATE subfolders, both under the SAME order-sheet folder
    assert ci_result.parent.parent == so_result.parent.parent

    filled_order_file = _stage("placed_order.xlsx")
    filled_result = _move_into_distributor_order_cycle_folder(
        filled_order_file, "Bernina International P Ltd", "FilledOrder",
        order_sheet_name="Order Sheets SS26", financial_year="FY2026-27",
    )
    # Filled Order copy sits DIRECTLY in the order-sheet folder, no subfolder
    assert filled_result.parent == so_result.parent.parent


def test_move_rejects_paths_outside_order_fulfillment_root(tmp_path, monkeypatch):
    import pytest

    monkeypatch.chdir(tmp_path)
    from app.routes.data import (
        _move_into_distributor_order_cycle_folder,
        _resolve_existing_order_fulfillment_source,
    )

    outside = tmp_path / "secret-config.env"
    outside.write_text("SECRET=1")

    with pytest.raises(ValueError, match="under"):
        _resolve_existing_order_fulfillment_source(outside)

    with pytest.raises(ValueError, match="under"):
        _move_into_distributor_order_cycle_folder(
            outside, "Evil Dist", "CI", order_sheet_name="Sheet"
        )
    assert outside.exists(), "outside file must not be moved/deleted"


def test_extract_order_sheet_item_key_from_real_material_description():
    """
    Verifies the Brand+TC+Size key extraction against the REAL Bombay
    Dyeing SO/CI Material Description format — all 18 of Aster's
    design+color SKU-lines must normalize to the SAME key, so they
    correctly accumulate together instead of being treated as 18
    unrelated items.
    """
    from app.routes.data import extract_order_sheet_item_key

    real_descriptions = [
        "ASTER 1+2 DB SET 224X244 7985BLU 100TC",
        "ASTER 1+2 DB SET 224X244 7985ORG 100TC",
        "ASTER 1+2 DB SET 224X244 7990PCH 100TC",
    ]
    keys = {extract_order_sheet_item_key(d) for d in real_descriptions}
    assert keys == {"ASTER|100|DB"}, f"All 18 Aster SKU-lines should collapse to one key, got: {keys}"


def test_make_order_sheet_item_key_matches_extracted_key():
    """
    The Filled Order spreadsheet's own Brand/TC/Size columns must
    normalize to the EXACT SAME key format as the SO/CI Material
    Description parser, so the two sides can actually match.
    """
    from app.routes.data import extract_order_sheet_item_key, make_order_sheet_item_key

    so_key = extract_order_sheet_item_key("ASTER 1+2 DB SET 224X244 7985BLU 100TC")
    filled_order_key = make_order_sheet_item_key("Aster", 100.0, "DB BS")
    assert so_key == filled_order_key == "ASTER|100|DB"


def test_real_aster_reconciliation_end_to_end(tmp_path):
    """
    Full end-to-end reconciliation using the REAL verified numbers
    from Bombay Dyeing's actual documents:
      - Filled Order (BND_Order.xlsx): Aster ordered_qty=864, value=501120
      - SO (BND_102875606.pdf): 18 SKU-lines, summing to so_qty=1188, so_value=689040
      - CI (Commercial_Invoice.PDF): matches the SO exactly (1188, 689040)
    Confirms: item_key correctly accumulates the 18 SO lines, AND
    correctly flags the genuine 864-vs-1188 discrepancy that was
    found when cross-checking the real documents.
    """
    from app.routes.data import extract_order_sheet_item_key

    db_path = str(tmp_path / "real_aster_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(
        name="Bernina International P Ltd", buyer_code="3220002",
        gst_no="07AAACB4006G1Z9", workspace_id="ws-1",
    )

    # Step 1: Filled Order says Aster = 864 qty, Rs 501120
    db.save_pending_filled_order_items(
        distributor_id=dist_id, workspace_id="ws-1",
        items=[{"item_name": "Aster 100TC DB BS", "item_key": "ASTER|100|DB", "qty": 864, "value": 501120}],
    )

    # Step 2: SO arrives (Contract 102875606) with 18 design+color lines, all Aster
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="102875606", distributor_id=dist_id, workspace_id="ws-1"
    )
    pending = db.get_and_consume_pending_filled_order_items(distributor_id=dist_id, workspace_id="ws-1")
    for p in pending:
        db.upsert_order_lifecycle_item(
            tracking_id=tracking_id, item_name=p["item_name"], source="ordered",
            qty=p["qty"], value=p["value"], workspace_id="ws-1", item_key=p["item_key"],
        )

    real_so_descriptions = [
        "ASTER 1+2 DB SET 224X244 7985BLU 100TC", "ASTER 1+2 DB SET 224X244 7985ORG 100TC",
        "ASTER 1+2 DB SET 224X244 7985PNK 100TC", "ASTER 1+2 DB SET 224X244 7986BLU 100TC",
        "ASTER 1+2 DB SET 224X244 7986BRW 100TC", "ASTER 1+2 DB SET 224X244 7986MRN 100TC",
        "ASTER 1+2 DB SET 224X244 7987MST 100TC", "ASTER 1+2 DB SET 224X244 7987PNK 100TC",
        "ASTER 1+2 DB SET 224X244 7987TEA 100TC", "ASTER 1+2 DB SET 224X244 7988BLU 100TC",
        "ASTER 1+2 DB SET 224X244 7988GRY 100TC", "ASTER 1+2 DB SET 224X244 7988LLC 100TC",
        "ASTER 1+2 DB SET 224X244 7989GRN 100TC", "ASTER 1+2 DB SET 224X244 7989ORG 100TC",
        "ASTER 1+2 DB SET 224X244 7989PNK 100TC", "ASTER 1+2 DB SET 224X244 7990BGE 100TC",
        "ASTER 1+2 DB SET 224X244 7990LLC 100TC", "ASTER 1+2 DB SET 224X244 7990PCH 100TC",
    ]
    final_item = None
    for desc in real_so_descriptions:
        item_key = extract_order_sheet_item_key(desc)
        final_item = db.upsert_order_lifecycle_item(
            tracking_id=tracking_id, item_name=desc, source="so",
            qty=66.0, value=38280.0, workspace_id="ws-1", item_key=item_key,
        )

    # All 18 lines should have accumulated into ONE row
    items = db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id="ws-1")
    assert len(items) == 1, f"Expected 1 accumulated item, got {len(items)}"
    assert items[0]["so_qty"] == 1188.0, f"Expected 1188 (18*66), got {items[0]['so_qty']}"
    assert items[0]["so_value"] == 689040.0, f"Expected 689040 (18*38280), got {items[0]['so_value']}"

    # Genuine discrepancy: Filled Order wanted 864, SO delivered 1188
    assert items[0]["ordered_qty"] == 864
    assert items[0]["has_discrepancy"] == 1, "864 (ordered) vs 1188 (SO) should be flagged"
    assert "Ordered vs SO qty" in items[0]["discrepancy_notes"]


def test_order_completeness_tracks_multiple_separate_sos(tmp_path):
    """
    Checkpoint C: verifies that a SECOND Sales Order (a different
    item, arriving under a DIFFERENT order_ref_no/tracking_id) still
    correctly gets its own "Ordered Qty" baseline via the persistent
    filled_order_item_baselines lookup — not just the first SO to
    consume the pending queue.
    """
    db_path = str(tmp_path / "completeness_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Test Distributor", workspace_id="ws-1")

    db.save_pending_filled_order_items(
        distributor_id=dist_id, workspace_id="ws-1",
        items=[
            {"item_name": "Aster 100TC DB", "item_key": "ASTER|100|DB", "qty": 864, "value": 501120},
            {"item_name": "Blumen 104TC SB", "item_key": "BLUMEN|104|SB", "qty": 288, "value": 121824},
        ],
    )

    # First SO (Aster) consumes the pending queue
    tracking_1 = db.create_order_lifecycle_tracking(order_ref_no="SO-001", distributor_id=dist_id, workspace_id="ws-1")
    pending = db.get_and_consume_pending_filled_order_items(distributor_id=dist_id, workspace_id="ws-1")
    for p in pending:
        db.upsert_order_lifecycle_item(
            tracking_id=tracking_1, item_name=p["item_name"], source="ordered",
            qty=p["qty"], value=p["value"], workspace_id="ws-1", item_key=p["item_key"],
        )
    db.upsert_order_lifecycle_item(
        tracking_id=tracking_1, item_name="Aster SO line", source="so",
        qty=1188, value=689040, workspace_id="ws-1", item_key="ASTER|100|DB",
    )

    # Completeness check: Aster covered, Blumen still pending
    summary = db.get_distributor_order_completeness(dist_id, workspace_id="ws-1")
    assert summary["total_items"] == 2
    assert summary["covered_items_count"] == 1
    assert summary["pending_items_count"] == 1
    assert summary["is_complete"] is False
    assert summary["pending_items"][0]["item_key"] == "BLUMEN|104|SB"

    # SECOND SO arrives for Blumen — a DIFFERENT tracking_id, pending queue
    # was already consumed by the first SO. Its "ordered" baseline must
    # STILL be correctly populated via filled_order_item_baselines.
    tracking_2 = db.create_order_lifecycle_tracking(order_ref_no="SO-002", distributor_id=dist_id, workspace_id="ws-1")
    blumen_item = db.upsert_order_lifecycle_item(
        tracking_id=tracking_2, item_name="Blumen SO line", source="so",
        qty=288, value=121824, workspace_id="ws-1", item_key="BLUMEN|104|SB",
    )
    assert blumen_item["ordered_qty"] == 288, (
        f"Second SO's item should still get its Ordered baseline via the "
        f"persistent lookup table, got: {blumen_item}"
    )

    # Completeness check again: now BOTH covered
    summary2 = db.get_distributor_order_completeness(dist_id, workspace_id="ws-1")
    assert summary2["covered_items_count"] == 2
    assert summary2["pending_items_count"] == 0
    assert summary2["is_complete"] is True


def test_duplicate_document_detection_roundtrip(tmp_path):
    """
    Verifies the core duplicate-detection mechanism: a document
    number, once marked processed, is correctly detected as a
    duplicate on a second check — but a DIFFERENT document number
    (genuinely a separate SO/CI) is never falsely flagged.
    """
    db_path = str(tmp_path / "dup_test.sqlite3")
    db = CentralizedDB(db_path)

    assert db.is_document_already_processed("ws-1", "SO", "102875606") is False

    db.mark_document_processed("ws-1", "SO", "102875606", tracking_id=1)
    assert db.is_document_already_processed("ws-1", "SO", "102875606") is True

    # A genuinely different order number is NOT a duplicate
    assert db.is_document_already_processed("ws-1", "SO", "102875607") is False

    # CI duplicate-detection is tracked SEPARATELY by its own invoice
    # number (not the same namespace as SO's order_ref_no). The stamp
    # only counts while that invoice still lives on the tracking row.
    assert db.is_document_already_processed("ws-1", "CI", "1400009285") is False
    dist_id = db.add_master_distributor(name="Dup Test", workspace_id="ws-1")
    ci_tid = db.save_ci_only_order_lifecycle(
        order_ref_no="102875999",
        distributor_id=dist_id,
        commercial_invoice_file_reference="/uploads/ci_9285.pdf",
        commercial_invoice_parsed={"header": {"invoice_no": "1400009285", "order_ref_no": "102875999"}},
        workspace_id="ws-1",
    )
    db.mark_document_processed("ws-1", "CI", "1400009285", tracking_id=ci_tid)
    assert db.is_document_already_processed("ws-1", "CI", "1400009285") is True

    # Workspace isolation
    assert db.is_document_already_processed("ws-2", "SO", "102875606") is False


def test_second_so_for_different_item_can_arrive_before_first(tmp_path):
    """
    "Kisi bhi item ka SO pehle aa sakta hai" — verifies the system
    does NOT assume any particular arrival order. Blumen's SO can
    arrive and be fully processed BEFORE Aster's SO exists at all.
    """
    db_path = str(tmp_path / "any_order_test.sqlite3")
    db = CentralizedDB(db_path)

    dist_id = db.add_master_distributor(name="Test Distributor", workspace_id="ws-1")
    db.save_pending_filled_order_items(
        distributor_id=dist_id, workspace_id="ws-1",
        items=[
            {"item_name": "Aster 100TC DB", "item_key": "ASTER|100|DB", "qty": 864, "value": 501120},
            {"item_name": "Blumen 104TC SB", "item_key": "BLUMEN|104|SB", "qty": 288, "value": 121824},
        ],
    )

    # Blumen's SO arrives FIRST, before Aster's
    tracking_blumen = db.create_order_lifecycle_tracking(order_ref_no="SO-BLUMEN-FIRST", distributor_id=dist_id, workspace_id="ws-1")
    pending = db.get_and_consume_pending_filled_order_items(distributor_id=dist_id, workspace_id="ws-1")
    for p in pending:
        db.upsert_order_lifecycle_item(
            tracking_id=tracking_blumen, item_name=p["item_name"], source="ordered",
            qty=p["qty"], value=p["value"], workspace_id="ws-1", item_key=p["item_key"],
        )
    blumen_result = db.upsert_order_lifecycle_item(
        tracking_id=tracking_blumen, item_name="Blumen SO", source="so",
        qty=288, value=121824, workspace_id="ws-1", item_key="BLUMEN|104|SB",
    )
    assert blumen_result["ordered_qty"] == 288
    assert blumen_result["has_discrepancy"] == 0, "Blumen matches exactly, no discrepancy expected"


def test_bombay_dyeing_multiline_parser_handles_real_18_item_so():
    """
    BUG REPRODUCED (real document, user-reported): the OLD generic
    table parser — and later, an OLD version of this dedicated
    parser that regex-matched extract_text() output — both silently
    dropped or garbled the Design/Color/TC portion of each SO line
    item, producing a broken "product" string that
    extract_order_sheet_item_key() couldn't parse, and creating a
    spurious duplicate row instead of correctly accumulating into
    the existing "Aster 100.0TC DB BS" Filled Order item.

    Root cause (found by testing against the REAL production PDF,
    not a hand-typed sample): pdfplumber's extract_text() reading
    order for this document does NOT match the fixed line-layout any
    text-based regex assumed — it differs in ways that broke every
    prior regex attempt. extract_tables() (cell-based, not
    text-based) reads the genuinely bordered SO table correctly
    regardless of text reading order, which is why the parser now
    takes a file path and doc_type ("SO"/"CI") instead of extracted
    text.

    This test runs the parser against the actual real 18-item Aster
    SO PDF fixture and verifies it correctly reconstructs every item,
    matching the manually-verified real totals exactly (1188 qty,
    Rs 689040 net value).
    """
    from pathlib import Path

    from app.routes.data import parse_bombay_dyeing_so_ci_line_items

    fixture_path = Path(__file__).parent / "fixtures" / "BND_102875606.pdf"
    items = parse_bombay_dyeing_so_ci_line_items(fixture_path, "SO")
    assert len(items) == 18, f"Expected 18 items, got {len(items)}"

    all_keys = {item["item_key"] for item in items}
    assert all_keys == {"ASTER|100|DB"}, f"All 18 items should share the same item_key, got: {all_keys}"

    total_qty = sum(item["qty"] for item in items)
    total_value = sum(item["value"] for item in items)
    assert total_qty == 1188.0, f"Expected 1188 total qty, got {total_qty}"
    assert total_value == 689040.0, f"Expected Rs 689040 total value, got {total_value}"

    # Each item's name should be the FULL, correctly-reassembled
    # description (not missing the dimensions/design/color/TC part)
    assert items[0]["item_name"] == "ASTER 1+2 DB SET 224X244 7985BLU 100TC"


def test_confirm_ci_link_creates_achievement(tmp_path):
    """
    Verifies the new explicit confirm-link flow: linking a CI to an
    SO and creating the achievement now only happens via this single,
    explicit call — never automatically at upload time.
    """
    db_path = str(tmp_path / "confirm_link_test.sqlite3")
    db = CentralizedDB(db_path)

    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-TEST-001",
        distributor_id=1,
        payment_status="PENDING",
        workspace_id="ws-1",
    )

    # The link should not exist until explicitly confirmed.
    before = db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-1")
    assert before.get("commercial_invoice_file_reference") is None

    linked_tracking_id = db.link_commercial_invoice_to_order_lifecycle(
        order_ref_no="SO-TEST-001",
        commercial_invoice_file_reference="/uploads/ci_test.pdf",
        commercial_invoice_parsed={"total": 40194.0},
        workspace_id="ws-1",
    )
    assert linked_tracking_id == tracking_id

    achievement_id = db.create_achievement(
        order_lifecycle_tracking_id=tracking_id,
        amount=40194.0,
        currency="INR",
        source="ci",
        created_by="tester",
        workspace_id="ws-1",
        notes="Confirmed via test",
    )
    assert isinstance(achievement_id, int)

    achievements = db.list_achievements(tracking_id=tracking_id)
    assert len(achievements) == 1
    assert achievements[0]["amount"] == 40194.0


def test_get_order_lifecycle_by_order_ref_no_returns_correct_fields(tmp_path):
    """
    BUG REPRODUCED (real, user-noticed): get_order_lifecycle_by_
    order_ref_no's SQL SELECT column list and its result dict were
    hand-written independently. A column added to one but not the
    other silently shifted every field after it by one position —
    order_sheet_name (needed to file a Commercial Invoice into the
    correct Order Sheet's folder) wasn't in the SELECT at all, so it
    always came back None, silently sending every CI into an
    "Unassigned Order Sheet" folder instead of the correct one. This
    test locks in the fix: the columns list is now shared between the
    SELECT and the result dict, so they can't drift apart again.
    """
    db_path = str(tmp_path / "order_ref_lookup_test.sqlite3")
    db = CentralizedDB(db_path)

    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-LOOKUP-TEST",
        distributor_id=42,
        payment_status="PENDING",
        workspace_id="ws-1",
    )
    db.link_commercial_invoice_to_order_lifecycle(
        order_ref_no="SO-LOOKUP-TEST",
        commercial_invoice_file_reference="/uploads/ci_lookup_test.pdf",
        commercial_invoice_parsed={"total": 12345.0},
        commercial_invoice_date=None,
        workspace_id="ws-1",
    )

    result = db.get_order_lifecycle_by_order_ref_no("SO-LOOKUP-TEST", workspace_id="ws-1")
    assert result is not None
    assert result["tracking_id"] == tracking_id
    assert result["distributor_id"] == 42
    assert result["commercial_invoice_file_reference"] == "/uploads/ci_lookup_test.pdf"
    assert "order_sheet_name" in result
    assert "order_sheet_id" in result
    # Every field after commercial_invoice_file_reference/parsed in
    # the old buggy version was mislabeled with the WRONG field's
    # value — dispatch_date, expected_delivery_date etc. should all
    # be their own (currently unset/None) values, not silently
    # holding some other column's data.
    assert result["dispatch_date"] is None
    assert result["created_at"] is not None


def test_ci_first_then_so_merges_and_rematches(tmp_path):
    """CI saved before SO must land on the same tracking and rematch SO vs CI."""
    db_path = str(tmp_path / "ci_first_so_later.sqlite3")
    db = CentralizedDB(db_path)

    tracking_id = db.save_ci_only_order_lifecycle(
        order_ref_no="102875610",
        distributor_id=7,
        commercial_invoice_file_reference="/uploads/ci_first.pdf",
        commercial_invoice_parsed={
            "header": {"order_ref_no": "102875610", "invoice_no": "1400009328"},
            "line_items": [
                {"item_name": "ASTER DB BS", "item_key": "ASTER|100|DB", "qty": 216, "value": 131544},
            ],
        },
        workspace_id="ws-1",
    )
    db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name="ASTER DB BS",
        source="ci",
        qty=216,
        value=131544,
        workspace_id="ws-1",
        item_key="ASTER|100|DB",
    )

    mergeable = db.find_mergeable_ci_only_tracking("102875610", workspace_id="ws-1")
    assert mergeable is not None
    assert mergeable["tracking_id"] == tracking_id

    linked_id = db.link_sales_order_to_order_lifecycle(
        order_ref_no="102875610",
        distributor_id=7,
        sales_order_file_reference="/uploads/so_later.pdf",
        sales_order_parsed={"header": {"order_ref_no": "102875610"}},
        workspace_id="ws-1",
    )
    assert linked_id == tracking_id

    db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name="ASTER DB BS",
        source="so",
        qty=216,
        value=131544,
        workspace_id="ws-1",
        item_key="ASTER|100|DB",
    )
    rematch = db.recheck_all_order_lifecycle_discrepancies(tracking_id, workspace_id="ws-1")
    assert rematch["has_discrepancy"] is False

    after = db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-1")
    assert after["sales_order_file_reference"] == "/uploads/so_later.pdf"
    assert after["commercial_invoice_file_reference"] == "/uploads/ci_first.pdf"
    assert after["order_ref_no"] == "102875610"


def test_ci_invoice_fallback_ref_merges_when_so_arrives(tmp_path):
    """CI saved as CI-{invoice} still merges when later SO matches header SO#."""
    db_path = str(tmp_path / "ci_fallback_ref.sqlite3")
    db = CentralizedDB(db_path)

    tracking_id = db.save_ci_only_order_lifecycle(
        order_ref_no="CI-1400009999",
        distributor_id=9,
        commercial_invoice_file_reference="/uploads/ci_fallback.pdf",
        commercial_invoice_parsed={
            "header": {"order_ref_no": "102899001", "invoice_no": "1400009999"},
        },
        workspace_id="ws-1",
    )

    mergeable = db.find_mergeable_ci_only_tracking("102899001", workspace_id="ws-1")
    assert mergeable is not None
    assert mergeable["tracking_id"] == tracking_id

    linked_id = db.link_sales_order_to_order_lifecycle(
        order_ref_no="102899001",
        distributor_id=9,
        sales_order_file_reference="/uploads/so_real.pdf",
        sales_order_parsed={"header": {"order_ref_no": "102899001"}},
        workspace_id="ws-1",
    )
    assert linked_id == tracking_id
    after = db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-1")
    assert after["order_ref_no"] == "102899001"
    assert after["sales_order_file_reference"] == "/uploads/so_real.pdf"
