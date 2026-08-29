"""Every uploaded order document must get a durable Drive copy.

Local upload storage is on an ephemeral disk that is wiped on every
redeploy. On 2026-08-28 that cost a full day: Sales Order PDFs vanished
mid-investigation because Google Drive had never been connected, so no
copy existed anywhere. The Filled Order workbook was worse — it was only
ever written to a temp path and discarded after parsing, so the very
start of the order chain had no durable copy at all.

The overriding rule tested here: the backup is best-effort. A Drive
outage, or Drive simply not being connected, must never fail an upload
the user has already completed.
"""

from app.routes import data as data_routes  # noqa: F401  (imported first: app package sets up the blueprint graph)
from app.storage import nexora_docs
from app.storage.providers.google_drive_provider import GoogleDriveProvider

import filled_orders_routes


def test_every_stage_of_the_order_chain_has_a_drive_folder():
    folders = GoogleDriveProvider.NEXORA_SUBFOLDERS
    for stage in ("Filled Orders", "Order Sheets", "Sales Orders", "Commercial Invoices"):
        assert stage in folders, f"{stage} has nowhere durable to live"


def test_push_helper_is_not_pdf_specific(tmp_path, monkeypatch):
    """Filled Orders are .xlsx. The uploader must take any file — Drive
    detects the type itself — and the original PDF-named helper must keep
    working for the existing Sales Order / Commercial Invoice callers."""
    assert nexora_docs.push_pdf_to_nexora_drive is nexora_docs.push_file_to_nexora_drive

    workbook = tmp_path / "balaji bath aw26.xlsx"
    workbook.write_bytes(b"PK\x03\x04 not really a workbook")
    captured = {}

    class FakeProvider:
        def ensure_nexora_workspace(self):
            return {"root_id": "root", "folders": {"Filled Orders": "fo-folder"}}

        def upload(self, path, folder_id, display_name=None):
            captured["path"] = path
            captured["folder_id"] = folder_id
            captured["display_name"] = display_name
            return {"id": "drive-file-1", "name": display_name}

    class FakeManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            return FakeProvider()

        def download_file_bytes(self, *_a, **_k):
            raise AssertionError("not used here")

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: FakeManager())

    result = nexora_docs.push_file_to_nexora_drive(
        user_id=1,
        workspace_id="ws",
        local_path=workbook,
        subfolder="Filled Orders",
        display_name="Balaji Bath AW26.xlsx",
    )
    assert result == {"id": "drive-file-1", "name": "Balaji Bath AW26.xlsx"}
    assert captured["folder_id"] == "fo-folder"
    assert captured["display_name"] == "Balaji Bath AW26.xlsx"


def test_drive_failure_returns_none_instead_of_raising(tmp_path, monkeypatch):
    """The single most important property: the upload already succeeded, so
    a Drive problem must be swallowed, never propagated to the user."""
    doc = tmp_path / "so.pdf"
    doc.write_bytes(b"%PDF-1.4")

    class ExplodingManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            raise RuntimeError("No storage provider connected for user")

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: ExplodingManager())

    assert nexora_docs.push_file_to_nexora_drive(
        user_id=1, workspace_id="ws", local_path=doc, subfolder="Sales Orders"
    ) is None


def test_missing_or_absent_file_is_handled(tmp_path):
    assert nexora_docs.push_file_to_nexora_drive(
        user_id=1, workspace_id="ws",
        local_path=tmp_path / "never_written.xlsx", subfolder="Filled Orders",
    ) is None
    # No connected user means nothing to upload to.
    assert nexora_docs.push_file_to_nexora_drive(
        user_id=None, workspace_id="ws", local_path=None, subfolder="Filled Orders",
    ) is None


def test_filled_order_upload_backs_up_on_the_success_path_only():
    """The backup must sit on the committed-success path — not on the
    confirmation-required branches, which return before anything is saved."""
    import inspect

    source = inspect.getsource(filled_orders_routes.upload_filled_order)
    assert "push_file_to_nexora_drive" in source, (
        "the distributor's Filled Order workbook is still not backed up anywhere"
    )
    assert '"Filled Orders"' in source
    # It must come after the order row is actually created.
    assert source.index("fodb.create_filled_order") < source.index(
        "push_file_to_nexora_drive"
    ), "backup runs before the order is committed"


def test_order_sheet_upload_backs_up_to_drive():
    import inspect

    source = inspect.getsource(data_routes.upload_order_sheet_v2)
    assert "push_file_to_nexora_drive" in source
    assert '"Order Sheets"' in source
