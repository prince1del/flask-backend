"""Owner 21-11: a business that signs up for the Distributor UI must reach its own books (/api/v1/hop) —
it got "Insufficient permissions". A distributor login inside someone else's workspace must still be refused."""
from __future__ import annotations

import pytest

from app import create_app
from app.services import subscriptions as subs
from centralized_db_system.db import CentralizedDB

QUESTION = CentralizedDB.SECRET_QUESTIONS[0]


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "ss.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-self-serve-32-bytes-long")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")
    subs.clear_cache()
    app = create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))
    with app.app_context():
        yield app.test_client(), str(db_path)
    subs.clear_cache()


def _signup(c, username, category):
    r = c.post("/api/v1/signup", json={"company_name": f"{username} Traders", "username": username,
                                       "password": "pass12345", "email": f"{username}@example.com",
                                       "secret_question": QUESTION, "secret_answer": "Delhi", "category": category})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def test_signed_up_distributor_reaches_own_books(client):
    c, db_path = client
    d = _signup(c, "distone", "distributor")
    assert d["role"] == "distributor"
    h = {"Authorization": f"Bearer {d['access_token']}", "X-Nexora-UI": "distributor"}
    r = c.post("/api/v1/hop/customers", headers=h, json={"company": "Retailer One"})
    assert r.status_code in (200, 201), r.get_json()
    r = c.get("/api/v1/hop/customers", headers=h)
    assert r.status_code == 200, r.get_json()


def test_distributor_login_in_another_workspace_is_refused(client):
    c, db_path = client
    owner = _signup(c, "bizowner", "business")
    db = CentralizedDB(db_path)
    db.create_user("fielddist", "pass12345", role="distributor", workspace_id=owner["workspace_id"])
    r = c.post("/api/v1/auth/login", json={"username": "fielddist", "password": "pass12345"})
    tok = r.get_json()["data"]["access_token"]
    r = c.get("/api/v1/hop/customers", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


@pytest.mark.parametrize("category,ui", [("business", "business"), ("distributor", "distributor"), ("retailer", "retailer")])
def test_every_ui_new_signup_can_save_and_read_books(client, category, ui):
    """Every UI's brand-new sign-up: save a party and a bill, read them back (no "Insufficient permissions")."""
    c, _ = client
    d = _signup(c, f"new{category}", category)
    h = {"Authorization": f"Bearer {d['access_token']}", "X-Nexora-UI": ui}
    cid = c.post("/api/v1/hop/customers", headers=h, json={"company": "First Party"}).get_json()["data"]["id"]
    r = c.post("/api/v1/hop/party-transactions", headers=h, json={
        "txn_type": 1, "customer_id": cid, "txn_date": "2026-11-21", "lines": [{"item_name": "x", "qty": 1, "rate": 10}]})
    assert r.status_code in (200, 201), (category, r.get_json())
    for path in ("/api/v1/hop/customers", "/api/v1/hop/party-transactions?txn_types=1", "/api/v1/hop/products",
                 "/api/v1/hop/feedback/counts"):
        assert c.get(path, headers=h).status_code == 200, (category, path)
