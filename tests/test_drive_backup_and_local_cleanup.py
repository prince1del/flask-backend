"""Every uploaded document is backed up to Drive, and only then dropped locally.

Drive is the durable store: the server's upload folder sits on an ephemeral
disk that is wiped on every redeploy, and it fills up in the meantime. So each
upload is pushed to Drive/NEXORA and the server-side copy is removed.

The rule that matters most is the safety interlock: the local file is deleted
ONLY when Drive returned a file id AND that id was recorded. If Drive is not
connected, or the upload failed, the local copy is the only copy in existence
and must survive. Getting this backwards loses the document permanently.
"""

from pathlib import Path

from app.routes import data as data_routes
from app.storage import nexora_docs
from app.storage.providers.google_drive_provider import GoogleDriveProvider

import filled_orders_routes


class _FakeProvider:
    def __init__(self, file_id="drive-file-1"):
        self.file_id = file_id
        self.uploaded = []

    def ensure_nexora_workspace(self):
        return {
            "root_id": "root",
            "folders": {name: f"{name}-id" for name in GoogleDriveProvider.NEXORA_SUBFOLDERS},
        }

    def upload(self, path, folder_id, display_name=None):
        self.uploaded.append({"path": path, "folder_id": folder_id, "name": display_name})
        return {"id": self.file_id, "name": display_name}


def _install_fake_drive(monkeypatch, provider):
    class FakeManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            return provider

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: FakeManager())


# --------------------------------------------------------------- the interlock


def test_local_copy_is_dropped_only_after_drive_confirms(tmp_path, monkeypatch):
    upload_root = tmp_path / "instance" / "order_fulfillment_files" / "SO"
    upload_root.mkdir(parents=True)
    doc = upload_root / "so.pdf"
    doc.write_bytes(b"%PDF-1.4")
    monkeypatch.chdir(tmp_path)

    # No Drive id -> the file is the only copy, it must stay.
    assert data_routes._drop_local_after_drive_backup(doc, None) is False
    assert doc.is_file()
    assert data_routes._drop_local_after_drive_backup(doc, "") is False
    assert doc.is_file()

    # Drive confirmed -> safe to free the disk.
    assert data_routes._drop_local_after_drive_backup(doc, "drive-abc") is True
    assert not doc.exists()


def test_never_deletes_outside_the_upload_root(tmp_path, monkeypatch):
    (tmp_path / "instance" / "order_fulfillment_files").mkdir(parents=True)
    outsider = tmp_path / "important.sqlite3"
    outsider.write_bytes(b"not an upload")
    monkeypatch.chdir(tmp_path)

    assert data_routes._drop_local_after_drive_backup(outsider, "drive-abc") is False
    assert outsider.is_file(), "a path outside the upload root must never be deleted"


def test_missing_file_and_missing_path_are_handled(tmp_path, monkeypatch):
    (tmp_path / "instance" / "order_fulfillment_files").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    gone = tmp_path / "instance" / "order_fulfillment_files" / "gone.pdf"
    assert data_routes._drop_local_after_drive_backup(gone, "drive-abc") is False
    assert data_routes._drop_local_after_drive_backup(None, "drive-abc") is False


# ------------------------------------------------- every stage reaches Drive


def test_every_stage_of_the_order_chain_has_a_drive_folder():
    folders = GoogleDriveProvider.NEXORA_SUBFOLDERS
    for stage in ("Filled Orders", "Order Sheets", "Sales Orders", "Commercial Invoices"):
        assert stage in folders, f"{stage} has nowhere durable to live"


def test_filled_order_workbook_is_pushed_to_drive(tmp_path, monkeypatch):
    """The FO is an .xlsx held only in a tempfile that is deleted after
    parsing — without this push, the start of the order chain has no copy."""
    provider = _FakeProvider()
    _install_fake_drive(monkeypatch, provider)

    workbook = tmp_path / "upload.xlsx"
    workbook.write_bytes(b"PK\x03\x04")

    filled_orders_routes._backup_filled_order_to_drive(
        user_id=1,
        workspace_id="ws",
        tmp_path=str(workbook),
        suffix=".xlsx",
        filename="whatever_the_distributor_named_it.xlsx",
        distributor_name_raw="Balaji Homedecor",
        category="Bath",
        season="AW26",
        order_id=7,
    )

    assert len(provider.uploaded) == 1
    sent = provider.uploaded[0]
    assert sent["folder_id"] == "Filled Orders-id"
    # Named so it can be found by eye in Drive later.
    assert sent["name"] == "Balaji Homedecor Bath AW26.xlsx"


def test_filled_order_drive_failure_never_breaks_the_upload(tmp_path, monkeypatch):
    """The order row is already committed by then — a Drive problem must not
    turn a saved upload into an error."""
    class ExplodingManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            raise RuntimeError("No storage provider connected for user")

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: ExplodingManager())
    workbook = tmp_path / "upload.xlsx"
    workbook.write_bytes(b"PK\x03\x04")

    filled_orders_routes._backup_filled_order_to_drive(
        user_id=1, workspace_id="ws", tmp_path=str(workbook), suffix=".xlsx",
        filename="x.xlsx", distributor_name_raw="D", category="Bed",
        season="AW26", order_id=1,
    )  # must not raise


def test_push_helper_takes_any_file_type(tmp_path, monkeypatch):
    """Filled Orders are .xlsx, SO/CI are .pdf — Drive detects the type, so
    the helper must not be PDF-specific. The old PDF-named alias still works
    for the existing SO/CI callers."""
    assert nexora_docs.push_pdf_to_nexora_drive is nexora_docs.push_file_to_nexora_drive

    provider = _FakeProvider()
    _install_fake_drive(monkeypatch, provider)
    book = tmp_path / "sheet.xlsx"
    book.write_bytes(b"PK\x03\x04")

    result = nexora_docs.push_file_to_nexora_drive(
        user_id=1, workspace_id="ws", local_path=book,
        subfolder="Order Sheets", display_name="AW26 Bed.xlsx",
    )
    assert result["id"] == "drive-file-1"
    assert provider.uploaded[0]["folder_id"] == "Order Sheets-id"


def test_drive_outage_returns_none_rather_than_raising(tmp_path, monkeypatch):
    class ExplodingManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            raise RuntimeError("Drive down")

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: ExplodingManager())
    doc = tmp_path / "so.pdf"
    doc.write_bytes(b"%PDF")
    assert nexora_docs.push_file_to_nexora_drive(
        user_id=1, workspace_id="ws", local_path=doc, subfolder="Sales Orders"
    ) is None


# ------------------------------------------- uploads wire the cleanup in


def test_upload_paths_drop_their_local_copy_after_backup():
    """Each upload must both back up AND clean up; a backup that leaves the
    file behind still fills the disk."""
    import inspect

    so = inspect.getsource(data_routes.upload_sales_order_v2)
    assert "_drop_local_after_drive_backup" in so
    # The PDF is parsed for line items after the Drive push, so the delete
    # must come last — deleting at push time would break that parsing.
    assert so.index("_archive_order_pdf_to_drive") < so.index("parse_bombay_dyeing_so_ci_line_items")
    assert so.index("parse_bombay_dyeing_so_ci_line_items") < so.index(
        "_drop_local_after_drive_backup"
    )

    sheet = inspect.getsource(data_routes.upload_order_sheet_v2)
    assert "push_file_to_nexora_drive" in sheet
    assert '"Order Sheets"' in sheet
    assert "_drop_local_after_drive_backup" in sheet

    fo = inspect.getsource(filled_orders_routes.upload_filled_order)
    assert "_backup_filled_order_to_drive" in fo
    assert fo.index("fodb.create_filled_order") < fo.index("_backup_filled_order_to_drive"), (
        "the workbook must only be backed up once the order is actually committed"
    )
