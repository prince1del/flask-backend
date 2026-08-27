"""Mail sync must pull Sales Orders â€” and when it cannot, say why.

The reported failure was "email se SO pull nahi kar raha": the poller answered
"0 CI, 0 SO" for every possible reason, and a genuine Sales Order attachment was
silently discarded because the pre-upload gate demanded a *uniquely identified*
buyer GSTIN â€” something a real SO (seller GST + buyer GST, or no Company Profile
GST on file) almost never yields.

These tests cover that regression plus every "nothing happened" case the user
must now be told about in plain language.
"""

from __future__ import annotations

import base64
import importlib
import io
import sqlite3
import zipfile

import pytest
from PIL import Image

WS = "ws-1"
OWN_GST = "27AAACB1234C1ZD"
BUYER_GST = "09AGSPK5678L1Z2"

SO_TEXT = """
BOMBAY DYEING AND MANUFACTURING CO LTD
GST No.: 27AAACB1234C1ZD
SALES ORDER
Contract No : 102875606
Buyer Code : BND001
Name (of the customer) : BERNINA INTERNATIONAL P LTD
GST No.: 09AGSPK5678L1Z2
Order Ref No : 102875606
"""


def _blank_pdf_bytes(pages: int = 1) -> bytes:
    """A structurally valid PDF with no text layer (a scan or phone photo)."""
    imgs = [Image.new("RGB", (600, 850), (250, 250, 250)) for _ in range(max(1, pages))]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in entries.items():
            zf.writestr(name, raw)
    return buf.getvalue()


class FakeGmail:
    """Minimal stand-in for the Gmail v1 client surface the poller touches."""

    def __init__(self, messages: list[dict]):
        # messages: [{"id", "subject", "attachments": {filename: bytes}}]
        self._messages = messages

    # -- chain plumbing -------------------------------------------------
    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return _Attachments(self._messages)

    def list(self, userId=None, q=None, maxResults=None):  # noqa: N803
        self.last_query = q
        return _Exec({"messages": [{"id": m["id"]} for m in self._messages]})

    def get(self, userId=None, id=None, format=None):  # noqa: A002, N803
        msg = next(m for m in self._messages if m["id"] == id)
        parts = [
            {
                "filename": name,
                "mimeType": "application/octet-stream",
                "body": {"attachmentId": f"{id}:{name}"},
            }
            for name in msg.get("attachments", {})
        ]
        return _Exec(
            {
                "internalDate": "1750000000000",
                "payload": {
                    "headers": [{"name": "Subject", "value": msg.get("subject", "")}],
                    "parts": parts,
                },
            }
        )


class _Attachments:
    def __init__(self, messages):
        self._messages = messages

    def get(self, userId=None, messageId=None, id=None):  # noqa: A002, N803
        msg = next(m for m in self._messages if m["id"] == messageId)
        name = str(id).split(":", 1)[1]
        raw = msg["attachments"][name]
        return _Exec({"data": base64.urlsafe_b64encode(raw).decode()})


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "mail_sync.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "mail-sync-test-key")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    flask_app = web_app_module.create_app()
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    for name in ("bd_one", "bd_two"):
        db.create_user(name, "pass123", role="sales_executive", workspace_id=WS)

    def login(username: str) -> dict:
        resp = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "pass123"}
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return {"Authorization": f"Bearer {resp.get_json()['data']['access_token']}"}

    def user_id(username: str) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return int(
                conn.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def connect_gmail(username: str) -> None:
        db.save_storage_account(
            user_id=user_id(username),
            workspace_id=WS,
            provider_type="gmail",
            oauth_token={"token": "t", "refresh_token": "r", "client_id": "c"},
            sync_status="connected",
        )

    def use_inbox(messages: list[dict], pdf_text: str | None = SO_TEXT) -> FakeGmail:
        fake = FakeGmail(messages)
        import app.services.gmail_ci_so_sync as sync

        monkeypatch.setattr(sync, "build_gmail_service", lambda _token: fake)
        if pdf_text is not None:
            # Text extraction is the one thing a synthetic PDF cannot provide.
            # Blank/image PDFs are left alone so the unreadable case stays real.
            import app.routes.data as data_module
            import app.three_step_verification as tsv

            def fake_extract(path):
                raw = open(path, "rb").read()
                return "" if b"/Image" in raw else pdf_text

            monkeypatch.setattr(tsv, "_extract_pdf_text", fake_extract)
            monkeypatch.setattr(data_module, "_extract_pdf_text", fake_extract)
        return fake

    return {
        "client": client,
        "login": login,
        "db": db,
        "db_path": str(db_path),
        "user_id": user_id,
        "connect_gmail": connect_gmail,
        "use_inbox": use_inbox,
    }


def _poll(client, headers, reset: bool = True):
    return client.post(f"/api/v1/mail-sync/poll?reset={str(reset).lower()}", headers=headers)


def _diag_rows(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT user_id, outcome, messages_matched, attachments_seen, "
            "attachments_unreadable FROM mail_sync_poll_diagnostics ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 1. Not connected must be stated, not swallowed.
# --------------------------------------------------------------------------


def test_not_connected_says_so_instead_of_reporting_zero(env):
    headers = env["login"]("bd_one")
    resp = _poll(env["client"], headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert data["connected"] is False
    assert data["outcome"] == "mail_sync_not_connected"
    assert "not connected" in data["message"].lower()
    assert _diag_rows(env["db_path"])[0][1] == "mail_sync_not_connected"


# --------------------------------------------------------------------------
# 2. Connected but nothing found must be stated too.
# --------------------------------------------------------------------------


def test_connected_but_no_matching_mail_reports_the_window(env):
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["use_inbox"]([])

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["connected"] is True
    assert data["outcome"] == "mail_sync_no_matching_mail"
    assert data["messages_matched"] == 0
    assert str(data["window_days"]) in data["message"]
    assert _diag_rows(env["db_path"])[0][1] == "mail_sync_no_matching_mail"


def test_mail_found_but_nothing_looks_like_a_sales_order(env):
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["use_inbox"](
        [{"id": "m1", "subject": "Your invoice", "attachments": {"receipt.pdf": b"%PDF-1.4 x"}}],
        pdf_text="Stripe receipt\nInvoice no INV-99\nThank you for your subscription",
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["outcome"] == "mail_sync_not_recognised"
    assert data["attachments_seen"] == 1
    reasons = {r["reason"] for r in data["skipped_reasons"]}
    assert "no_gstin_on_document" in reasons
    assert "none of them looked like" in data["message"].lower()


# --------------------------------------------------------------------------
# 3. THE REGRESSION: a real SO email must actually be ingested.
# --------------------------------------------------------------------------


def test_sales_order_email_is_ingested_and_matched_to_the_distributor(env):
    """Two GSTINs on the page (seller + buyer) and no Company Profile GST â€” the
    exact shape that used to be dropped without a word."""
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    # Created the way the shared SO upload route looks parties up (by workspace,
    # legacy-unowned) so this test exercises the mail sync path, not the
    # separate ownership question covered by the next test.
    env["db"].add_master_distributor(
        "Bernina International P Ltd",
        firm_name="Bernina International P Ltd",
        buyer_code="BND001",
        workspace_id=WS,
    )
    env["use_inbox"](
        [
            {
                "id": "m1",
                "subject": "Sales Order 102875606",
                "attachments": {"SO 102875606.pdf": b"%PDF-1.4 so"},
            }
        ]
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["outcome"] in ("mail_sync_imported", "mail_sync_needs_review"), data
    assert data["attachments_seen"] == 1
    assert data["attachments_unreadable"] == 0
    assert data["skipped_reasons"] == [], "a real SO must not be skipped"
    assert data["debug"][0]["classified_kind"] == "SO"
    assert data["debug"][0]["accepted"] is True

    # It reached the shared Order Desk ingest path, not a mail-only store.
    assert data["so_staged"] == 1, data
    assert data["imported_items"][0]["doc_no"] == "102875606"
    assert data["outcome"] == "mail_sync_imported"

    log = env["client"].get("/api/v1/mail-sync/log", headers=headers).get_json()
    outcomes = {row["outcome"] for row in log["data"]["items"]}
    assert "auto_confirmed" in outcomes
    # A healthy run leaves no diagnostic noise, exactly like SO pack uploads.
    assert _diag_rows(env["db_path"]) == []


def test_unmatched_sales_order_is_queued_for_review_not_dropped(env):
    """No distributor match is a review item with a reason — never a silent skip."""
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["use_inbox"](
        [
            {
                "id": "m1",
                "subject": "Sales Order 102875606",
                "attachments": {"SO 102875606.pdf": b"%PDF-1.4 so"},
            }
        ]
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["outcome"] == "mail_sync_needs_review"
    assert data["pending_review"] == 1
    assert data["skipped_reasons"] == []

    pending = env["client"].get("/api/v1/mail-sync/pending", headers=headers).get_json()
    item = pending["data"]["items"][0]
    assert item["kind"] == "SO"
    assert "distributor" in (item["reason"] or "").lower()
    assert "pick the distributor" in data["message"].lower()


def test_zipped_sales_order_pack_is_no_longer_invisible(env):
    """SO packs arrive zipped; the old query and walk only accepted .pdf."""
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["db"].add_master_distributor(
        "Bernina International P Ltd",
        firm_name="Bernina International P Ltd",
        buyer_code="BND001",
        workspace_id=WS,
    )
    env["use_inbox"](
        [
            {
                "id": "m1",
                "subject": "AW26 orders",
                "attachments": {"so_pack.zip": _zip_of({"SO 102875606.pdf": b"%PDF-1.4 so"})},
            }
        ]
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["attachments_seen"] == 1, "the zip must be unpacked, not skipped"
    assert data["debug"] and data["debug"][0]["classified_kind"] == "SO"
    assert data["so_staged"] == 1, data


# --------------------------------------------------------------------------
# 4. Unreadable attachments must name the file and the reason.
# --------------------------------------------------------------------------


def test_unreadable_attachment_names_the_file_and_why(env):
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["use_inbox"](
        [
            {
                "id": "m1",
                "subject": "Sales order scan",
                "attachments": {"SO scan.pdf": _blank_pdf_bytes(2)},
            }
        ]
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["outcome"] == "mail_sync_attachments_unreadable"
    assert data["attachments_unreadable"] == 1
    bad = data["unreadable_files"][0]
    assert bad["filename"] == "SO scan.pdf"
    assert bad["reason"] == "no_text_layer"
    assert "SO scan.pdf" in data["message"]

    rows = _diag_rows(env["db_path"])
    assert rows[0][1] == "mail_sync_attachments_unreadable"
    assert rows[0][4] == 1


def test_corrupt_container_is_reported_by_name(env):
    headers = env["login"]("bd_one")
    env["connect_gmail"]("bd_one")
    env["use_inbox"](
        [{"id": "m1", "subject": "orders", "attachments": {"orders.zip": b"PKnot-a-zip"}}]
    )

    data = _poll(env["client"], headers).get_json()["data"]
    assert data["attachments_unreadable"] == 1
    assert data["unreadable_files"][0]["filename"] == "orders.zip"
    assert data["outcome"] == "mail_sync_attachments_unreadable"


# --------------------------------------------------------------------------
# 5. Isolation.
# --------------------------------------------------------------------------


def test_diagnostics_are_per_user_and_workspace_wide_only_for_the_owner(env):
    one = env["login"]("bd_one")
    two = env["login"]("bd_two")
    _poll(env["client"], one)
    _poll(env["client"], two)

    def listed(headers):
        resp = env["client"].get("/api/v1/mail-sync/diagnostics", headers=headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()["data"]

    mine = listed(one)
    assert mine["scope"] == "mine"
    assert {r["user_id"] for r in mine["records"]} == {env["user_id"]("bd_one")}
    assert {r["user_id"] for r in listed(two)["records"]} == {env["user_id"]("bd_two")}

    env["db"].create_user(
        "kunwar1del", "pass123", role="sales_executive", workspace_id=WS
    )
    conn = sqlite3.connect(env["db_path"])
    try:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE username = 'kunwar1del'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pytest.skip("workspace owner flag not present in this schema")
    finally:
        conn.close()

    owner = listed(env["login"]("kunwar1del"))
    assert owner["scope"] == "workspace"
    assert {r["user_id"] for r in owner["records"]} == {
        env["user_id"]("bd_one"),
        env["user_id"]("bd_two"),
    }