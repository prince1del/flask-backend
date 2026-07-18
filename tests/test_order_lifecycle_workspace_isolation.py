"""
Verifies the fix for a cross-tenant financial-write vulnerability found
while auditing CP's Phase 2 (Order Fulfillment) work.

ROOT CAUSE: update_order_lifecycle_stage() had NO workspace_id
parameter at all. reconcile_invoice() (backing
POST /api/v1/operations/invoices/reconcile) called it with
payment_status="PAID" whenever reconciled=true — meaning ANY
authenticated user, from ANY workspace, could mark ANY OTHER
workspace's order as "PAID" simply by guessing/incrementing a
tracking_id. This is the same class of bug already found and fixed
for credit_control earlier in this project.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "lifecycle_workspace_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "lifecycle-workspace-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("lifecycle_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("lifecycle_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_reconcile_invoice_cannot_mark_other_workspace_order_as_paid(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "lifecycle_user_a", "pass123")

    # ws-2 creates an order lifecycle record, correctly tagged to ws-2,
    # with payment_status starting as PENDING.
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-WS2-001",
        distributor_id=1,
        payment_status="PENDING",
        workspace_id="ws-2",
    )

    # ws-1's user tries to reconcile/mark THIS tracking_id (belonging to
    # ws-2) as PAID, just by guessing the id.
    resp = client.post(
        "/api/v1/operations/invoices/reconcile",
        json={
            "tracking_id": tracking_id,
            "invoice_number": "FAKE-INV-001",
            "invoice_amount": 99999,
            "reconciled": True,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # The endpoint itself doesn't need to fail loudly (it creates its
    # own reconciliation record regardless), but the actual PROTECTED
    # resource — ws-2's order_lifecycle_tracking payment_status — must
    # NOT have changed.
    assert resp.status_code == 200

    ws2_record = db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-2")
    assert ws2_record["payment_status"] == "PENDING", (
        "BUG REPRODUCED: ws-1 was able to mark ws-2's order as PAID "
        "just by guessing its tracking_id"
    )


def test_reconcile_invoice_can_mark_own_workspace_order_as_paid(tmp_path, monkeypatch):
    """Sanity check: the fix must not break the legitimate same-workspace case."""
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "lifecycle_user_a", "pass123")

    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-WS1-001",
        distributor_id=1,
        payment_status="PENDING",
        workspace_id="ws-1",
    )

    resp = client.post(
        "/api/v1/operations/invoices/reconcile",
        json={
            "tracking_id": tracking_id,
            "invoice_number": "REAL-INV-001",
            "invoice_amount": 15000,
            "reconciled": True,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200

    ws1_record = db.get_order_lifecycle_tracking(tracking_id, workspace_id="ws-1")
    assert ws1_record["payment_status"] == "PAID", (
        "Legitimate same-workspace reconciliation should still work"
    )
