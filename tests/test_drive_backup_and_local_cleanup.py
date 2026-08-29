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
        self._files: dict[tuple[str, str], str] = {}

    def ensure_nexora_workspace(self):
        return {
            "root_id": "root",
            "folders": {name: f"{name}-id" for name in GoogleDriveProvider.NEXORA_SUBFOLDERS},
        }

    def upload(self, path, folder_id, display_name=None):
        self.uploaded.append({"path": path, "folder_id": folder_id, "name": display_name})
        return {"id": self.file_id, "name": display_name}

    def upload_or_replace(self, path, folder_id, display_name=None):
        key = (folder_id, display_name or "")
        replaced = key in self._files
        file_id = self._files.get(key, self.file_id)
        if not replaced:
            self._files[key] = file_id
        self.uploaded.append(
            {
                "path": path,
                "folder_id": folder_id,
                "name": display_name,
                "replaced": replaced,
            }
        )
        return {"id": file_id, "name": display_name}


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
    # Named so it can be found by eye in Drive later, with a date so that
    # re-uploads and merged-in files stay distinguishable.
    assert sent["name"].startswith("Balaji Homedecor Bath AW26 (")
    assert sent["name"].endswith(".xlsx")


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


# ------------------------------------ the right account must be selected


class _FakeAccountsDb:
    """storage_accounts is UNIQUE(user_id, workspace_id, provider_type), so a
    user can legitimately hold several — this returns them the way the real
    query does: first match wins unless a provider_type is asked for."""

    def __init__(self, rows):
        self.rows = rows

    def get_storage_account(self, user_id, provider_type=None, workspace_id=None):
        for row in self.rows:
            if row["user_id"] != user_id:
                continue
            if workspace_id and row["workspace_id"] != workspace_id:
                continue
            if provider_type and row["provider_type"] != provider_type:
                continue
            return row
        return None


def _accounts(*provider_types):
    return [
        {
            "id": i + 1,
            "user_id": 1,
            "workspace_id": "ws",
            "provider_type": p,
            "oauth_token": {"token": f"{p}-token"},
        }
        for i, p in enumerate(provider_types)
    ]


def test_a_leftover_gmail_account_does_not_shadow_google_drive(monkeypatch):
    """The bug behind "Drive says Connected but nothing is ever backed up".

    The Gmail import stored its own storage_accounts row. Resolving the
    provider without naming one returned that gmail row first; 'gmail' is not
    a registered provider, so this raised KeyError — which the Drive backup
    swallowed silently. No file, no log, and the UI still said Connected.
    """
    from app.storage.manager import StorageManager
    from app.storage.providers.google_drive_provider import GoogleDriveProvider

    manager = StorageManager()
    manager.register_provider("google_drive", GoogleDriveProvider)
    # gmail row first, exactly as the failing production account was ordered.
    manager.db = _FakeAccountsDb(_accounts("gmail", "google_drive"))
    monkeypatch.setattr(GoogleDriveProvider, "authenticate", lambda self, token: object())

    provider = manager._get_user_provider(
        1, workspace_id="ws", provider_type="google_drive"
    )
    assert isinstance(provider, GoogleDriveProvider)


def test_callers_that_name_no_provider_still_get_a_usable_one(monkeypatch):
    """upload_file, download_file, download_file_bytes and list_files all ask
    without naming a provider — so viewing an SO/CI PDF hit this too, not just
    the backup. Resolving without a name must prefer an account this manager
    can actually drive, rather than whichever row happens to come first."""
    from app.storage.manager import StorageManager
    from app.storage.providers.google_drive_provider import GoogleDriveProvider

    manager = StorageManager()
    manager.register_provider("google_drive", GoogleDriveProvider)
    manager.db = _FakeAccountsDb(_accounts("gmail", "google_drive"))
    monkeypatch.setattr(GoogleDriveProvider, "authenticate", lambda self, token: object())

    provider = manager._get_user_provider(1, workspace_id="ws")
    assert isinstance(provider, GoogleDriveProvider)


def test_cached_connection_is_not_reused_across_providers(monkeypatch):
    """user_connections is keyed by user id alone, so a cached gmail
    connection would otherwise be handed to a Drive caller."""
    from app.storage.manager import StorageManager
    from app.storage.providers.google_drive_provider import GoogleDriveProvider

    manager = StorageManager()
    manager.register_provider("google_drive", GoogleDriveProvider)
    manager.db = _FakeAccountsDb(_accounts("google_drive"))
    monkeypatch.setattr(GoogleDriveProvider, "authenticate", lambda self, token: object())

    manager.user_connections[1] = {
        "provider_type": "gmail",
        "workspace_id": "ws",
        "provider": object(),
    }
    provider = manager._get_user_provider(
        1, workspace_id="ws", provider_type="google_drive"
    )
    assert isinstance(provider, GoogleDriveProvider)


def test_drive_backup_says_why_it_skipped(monkeypatch, tmp_path, caplog):
    """A skipped backup used to be a bare `return None`. Not being connected
    is normal; being unable to tell that apart from a silent malfunction is
    what cost a day."""
    class NoAccountManager:
        def register_provider(self, *_a, **_k):
            pass

        def _get_user_provider(self, *_a, **_k):
            raise KeyError("No storage provider connected for user")

    monkeypatch.setattr("app.storage.manager.StorageManager", lambda: NoAccountManager())
    doc = tmp_path / "so.pdf"
    doc.write_bytes(b"%PDF")

    with caplog.at_level("WARNING"):
        assert nexora_docs.push_file_to_nexora_drive(
            user_id=1, workspace_id="ws", local_path=doc, subfolder="Sales Orders"
        ) is None
    assert "NEXORA Drive backup skipped" in caplog.text


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

    # The Filled Order has two success paths — a brand new order, and a file
    # merged into an existing one. Both are the distributor's document and
    # both must reach Drive; the merge path returns early, so it needs its
    # own call rather than relying on the one further down.
    fo = inspect.getsource(filled_orders_routes.upload_filled_order)
    assert fo.count("_backup_filled_order_to_drive(") == 2, (
        "both the new-order and merged-into-existing paths must back up"
    )
    # Neither backup may run before something is actually committed — the
    # confirmation-required branches above return without saving anything.
    first_backup = fo.index("_backup_filled_order_to_drive(")
    first_commit = min(
        fo.index("fodb.create_filled_order"),
        fo.index("fodb.merge_items_into_filled_order"),
    )
    assert first_commit < first_backup, (
        "the workbook must only be backed up once the order is actually committed"
    )


def test_payment_receiving_backup_pushes_json_snapshot(monkeypatch, tmp_path):
    from app.storage import payment_drive_backup
    from app.storage.payment_drive_backup import PAYMENT_RECEIVING_SUBFOLDER

    fake = _FakeProvider()
    _install_fake_drive(monkeypatch, fake)

    class FakeDB:
        def list_distributor_payment_collection(self, workspace_id, user_id=None):
            return [{"distributor_name": "Bernina", "orders": [], "paid_total": 1000}]

    payment_drive_backup.backup_so_payment_collection_to_drive(
        db=FakeDB(),
        user_id=1,
        workspace_id="default",
        username="kunwar1del",
    )
    assert len(fake.uploaded) == 1
    up = fake.uploaded[0]
    assert up["folder_id"] == f"{PAYMENT_RECEIVING_SUBFOLDER}-id"
    assert up["name"] == "kunwar1del SO Payment Receiving.json"
    assert up["name"].endswith(".json")
    assert Path(up["path"]).suffix == ".json"
    assert up.get("replaced") is False

    payment_drive_backup.backup_so_payment_collection_to_drive(
        db=FakeDB(),
        user_id=1,
        workspace_id="default",
        username="kunwar1del",
    )
    assert len(fake.uploaded) == 2
    assert fake.uploaded[1]["name"] == "kunwar1del SO Payment Receiving.json"
    assert fake.uploaded[1].get("replaced") is True


def test_payment_collection_routes_trigger_backup():
    import inspect
    from app.routes import payment_collection as pc

    create_src = inspect.getsource(pc.create_payment_entry)
    delete_src = inspect.getsource(pc.delete_payment_entry)
    assert "backup_so_payment_collection_to_drive" in create_src
    assert "backup_so_payment_collection_to_drive" in delete_src
