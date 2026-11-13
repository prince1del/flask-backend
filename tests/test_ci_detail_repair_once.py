"""CI detail: the slow PDF re-read ('repair') runs once per PDF, not on every view."""

from __future__ import annotations

import importlib
import json


def test_ci_repair_runs_once_per_pdf(tmp_path, monkeypatch):
    db_path = tmp_path / "ci_detail.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "ci-detail-test-key-0123456789abcd")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    db.create_user("ci_user", "pass123", role="sales_executive", workspace_id="ws-1")
    login = client.post("/api/v1/auth/login", json={"username": "ci_user", "password": "pass123"})
    h = {"Authorization": f"Bearer {login.get_json()['data']['access_token']}"}

    pdf = tmp_path / "CI_1.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    parsed = {
        "header": {"invoice_no": "1400000001", "total_pieces": 100, "invoice_total": 1000.0},
        "totals": {"qty": 100},
        "text": "Invoice No.: 1400000001",
        # Lines add up to 40 pcs against a 100 pcs header -> "needs qty repair".
        "line_items": [{"item_name": "ASTER DB BS 3 Designs 3 Colours", "qty": 40.0, "amount": 400.0,
                        "article_match": {"status": "no_key"}}],
        "article_master_match": {},
    }
    import sqlite3

    user_id = login.get_json()["data"]["user"]["id"] if "id" in login.get_json()["data"].get("user", {}) \
        else login.get_json()["data"]["user"]["user_id"]
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO master_distributors (name, firm_name, workspace_id, user_id, created_at) "
            "VALUES ('Test Dist', 'Test Dist', 'ws-1', ?, datetime('now'))", (user_id,))
        dist_id = cur.lastrowid
    tid = db.create_order_lifecycle_tracking(order_ref_no="SO-1", distributor_id=dist_id, workspace_id="ws-1")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE order_lifecycle_tracking SET commercial_invoice_file_reference = ?, "
            "commercial_invoice_parsed = ? WHERE tracking_id = ?",
            (str(pdf), json.dumps(parsed), tid))

    import app.routes.data as data_routes

    calls = []
    monkeypatch.setattr(data_routes, "build_commercial_invoice_detail",
                        lambda path: calls.append(path) or {"error": "cannot read"})
    monkeypatch.setattr(data_routes, "parse_bombay_dyeing_so_ci_line_items", lambda path, kind: calls.append(path) or [])

    assert client.get(f"/api/v1/order-fulfillment/tracking/{tid}", headers=h).status_code == 200
    first = len(calls)
    assert first >= 1  # first view checks the PDF
    assert client.get(f"/api/v1/order-fulfillment/tracking/{tid}", headers=h).status_code == 200
    assert len(calls) == first  # second view does not re-read it

    pdf.write_bytes(b"%PDF-1.4 re-uploaded, different size")
    client.get(f"/api/v1/order-fulfillment/tracking/{tid}", headers=h)
    assert len(calls) > first  # a new PDF is checked again
