"""
Verifies that the previously-unauthenticated data.py routes now:
1. Reject requests with no Authorization token (401)
2. For the retailer-download endpoints specifically: enforce workspace
   isolation (a workspace_a token cannot pull workspace_b's retailer data
   by guessing a distributor id or requesting "all").
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "data_routes_auth_test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "data-routes-auth-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    # web_app's load_env_file() re-reads the real .env on reload and can
    # clobber these monkeypatched values — re-apply them after reload.
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "data-routes-auth-test-key")

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("data_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("data_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


PREVIOUSLY_UNAUTHENTICATED_ROUTES = [
    ("GET", "/legacy"),
    ("GET", "/bale-calculator"),
    ("GET", "/search?q=test"),
    ("GET", "/articles"),
    ("GET", "/alerts"),
    ("GET", "/credit-policy"),
    ("GET", "/purchase-behavior"),
    ("GET", "/workflow-gps"),
    ("GET", "/article-master"),
    ("GET", "/retailer-download"),
    ("GET", "/retailer-download/excel?dist_id=all"),
    ("GET", "/retailer-download/csv?dist_id=all"),
]


def test_previously_open_routes_now_require_auth(tmp_path, monkeypatch):
    """Every route that used to be reachable with zero authentication
    must now reject an unauthenticated request."""
    client, _db = setup_auth_app(tmp_path, monkeypatch)

    failures = []
    for method, path in PREVIOUSLY_UNAUTHENTICATED_ROUTES:
        resp = client.open(path, method=method)
        if resp.status_code not in (401, 302):
            failures.append(f"{method} {path} -> {resp.status_code} (expected 401/302)")

    assert not failures, "These routes are still reachable without auth:\n" + "\n".join(failures)


def test_retailer_download_excel_does_not_leak_other_workspace_data(tmp_path, monkeypatch):
    """
    The single highest-risk finding: /retailer-download/excel and .../csv
    used to return EVERY retailer across ALL workspaces (name, phone,
    email, address, GST number) with zero authentication. This test
    proves that with a valid token from workspace ws-1:
      - requesting dist_id=all only returns ws-1's retailers
      - requesting a distributor id that belongs to ws-2 is rejected,
        not silently served
    """
    client, db = setup_auth_app(tmp_path, monkeypatch)

    token_a = login(client, "data_user_a", "pass123")
    token_b = login(client, "data_user_b", "pass123")

    dist_a_id = db.add_master_distributor(
        name="WS1 Distributor", firm_name="WS1 Firm", gst_no="GST-WS1", workspace_id="ws-1"
    )
    dist_b_id = db.add_master_distributor(
        name="WS2 Distributor", firm_name="WS2 Firm", gst_no="GST-WS2", workspace_id="ws-2"
    )
    db.add_master_retailer(
        name="WS1 Retailer", distributor_id=dist_a_id, location="Mumbai",
        gst_no="GST-RT-WS1", workspace_id="ws-1",
    )
    db.add_master_retailer(
        name="WS2 Retailer", distributor_id=dist_b_id, location="Pune",
        gst_no="GST-RT-WS2", workspace_id="ws-2",
    )

    # ws-1's "download all" must not contain ws-2's retailer
    resp_all_a = client.get(
        "/retailer-download/excel?dist_id=all",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_all_a.status_code == 200
    # The response is a binary .xlsx; we can at minimum confirm ws-2's
    # distributor id was never queried into it by checking the route
    # logic via the CSV variant instead (human-readable, easy to assert on).
    resp_all_a_csv = client.get(
        "/retailer-download/csv?dist_id=all",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_all_a_csv.status_code == 200
    csv_text_a = resp_all_a_csv.get_data(as_text=True)
    assert "WS1 Retailer" in csv_text_a
    assert "WS2 Retailer" not in csv_text_a
    assert "GST-RT-WS2" not in csv_text_a

    # ws-1's token trying to pull ws-2's distributor by id directly must be rejected
    cross_workspace_attempt = client.get(
        f"/retailer-download/csv?dist_id={dist_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert cross_workspace_attempt.status_code == 404, (
        f"Expected 404 when ws-1 requests ws-2's distributor id, "
        f"got {cross_workspace_attempt.status_code}: "
        f"{cross_workspace_attempt.get_data(as_text=True)}"
    )

    # Sanity check: ws-2's own token CAN reach its own distributor's retailers
    own_workspace_attempt = client.get(
        f"/retailer-download/csv?dist_id={dist_b_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert own_workspace_attempt.status_code == 200
    assert "WS2 Retailer" in own_workspace_attempt.get_data(as_text=True)
