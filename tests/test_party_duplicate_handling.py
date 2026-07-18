"""
Verifies two fixes found via real-world testing (Bombay Dyeing GT
North, 5 July 2026):

1. GST reuse after "delete": deleting a distributor is a soft-delete
   (status='inactive'). Before this fix, gst_number had a blanket
   UNIQUE constraint with no regard for status, so re-creating a
   distributor with the SAME GST after "deleting" the old one always
   failed with a confusing "GST number already exists" error.

2. Duplicate name/phone confirmation: when GST was removed (the only
   thing blocking the earlier save), the system silently created a
   second distributor with identical name/phone/email/address — no
   duplicate-detection existed at all for those fields. Per the
   founder's explicit business rule: name/phone duplicates should
   only trigger a CONFIRMATION prompt (one person can legitimately
   run more than one firm), while GST duplicates among ACTIVE records
   must remain a hard block (GST is legally unique per firm).
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "party_duplicate_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "party-duplicate-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("party_test_user", "pass123", role="sales_executive", workspace_id="ws-1")

    return app.test_client()


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_gst_can_be_reused_after_soft_delete(tmp_path, monkeypatch):
    client = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "party_test_user", "pass123")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Create distributor A with a GST number
    create_resp = client.post(
        "/api/v1/parties/distributors",
        json={"name": "Neelam Julka", "gst_number": "07AAACB4006G1Z9", "phone": "7011206346"},
        headers=headers,
    )
    assert create_resp.status_code == 201
    dist_id = create_resp.get_json()["data"]["id"]

    # Soft-delete it
    delete_resp = client.delete(f"/api/v1/parties/distributors/{dist_id}", headers=headers)
    assert delete_resp.status_code == 200

    # BUG REPRODUCED (before fix): re-creating with the SAME GST used
    # to fail here, even though the old record is no longer visible.
    recreate_resp = client.post(
        "/api/v1/parties/distributors",
        json={"name": "A Totally Different Name", "gst_number": "07AAACB4006G1Z9", "phone": "9999999999"},
        headers=headers,
    )
    assert recreate_resp.status_code == 201, (
        f"GST reuse after soft-delete should be allowed. Got: "
        f"{recreate_resp.get_data(as_text=True)}"
    )


def test_gst_still_blocked_among_active_distributors(tmp_path, monkeypatch):
    """Sanity check: the fix must not weaken the GST-uniqueness rule
    for genuinely active, concurrent distributors."""
    client = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "party_test_user", "pass123")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    client.post(
        "/api/v1/parties/distributors",
        json={"name": "Firm One", "gst_number": "07AAACB4006G1Z9", "phone": "1111111111"},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/parties/distributors",
        json={"name": "Firm Two", "gst_number": "07AAACB4006G1Z9", "phone": "2222222222"},
        headers=headers,
    )
    assert resp.status_code == 400, "Two ACTIVE distributors must never share a GST number"


def test_duplicate_name_or_phone_requires_confirmation_not_hard_block(tmp_path, monkeypatch):
    client = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "party_test_user", "pass123")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    client.post(
        "/api/v1/parties/distributors",
        json={"name": "Ramesh Traders", "phone": "8888888888"},
        headers=headers,
    )

    # Same phone, no GST at all — should be a soft warning (409 +
    # requires_confirmation), NOT a hard 400 block.
    resp = client.post(
        "/api/v1/parties/distributors",
        json={"name": "A Second Firm Ramesh Also Runs", "phone": "8888888888"},
        headers=headers,
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["requires_confirmation"] is True

    # Confirming (force_save=true) must let it through — one person
    # can legitimately run more than one firm.
    forced_resp = client.post(
        "/api/v1/parties/distributors",
        json={"name": "A Second Firm Ramesh Also Runs", "phone": "8888888888", "force_save": True},
        headers=headers,
    )
    assert forced_resp.status_code == 201, (
        f"force_save should let a confirmed duplicate through: "
        f"{forced_resp.get_data(as_text=True)}"
    )
