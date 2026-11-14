"""Article Master is one catalog per company: teammates read, owner/admin edit."""

from __future__ import annotations

import pytest

from app import create_app
from app.services import company_invites as ci
from app.services import subscriptions as subs
from centralized_db_system.db import CentralizedDB

QUESTION = CentralizedDB.SECRET_QUESTIONS[0]


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "am_shared.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-am-shared-32bytes-xx")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    subs.clear_cache()
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application
    subs.clear_cache()


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(client, username, **extra):
    payload = {
        "company_name": f"{username} Traders",
        "username": username,
        "password": "pass12345",
        "email": f"{username}@example.com",
        "secret_question": QUESTION,
        "secret_answer": "Delhi",
        "category": "business",
        **extra,
    }
    res = client.post("/api/v1/signup", json=payload)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _join(app, client, owner, username, role="sales_executive"):
    db = CentralizedDB(app.config["DATABASE_PATH"])
    subs.set_unlimited(app.config["DATABASE_PATH"], owner["user_id"])
    subs.clear_cache()
    ci.set_team_size(db, db.get_company_id_for_workspace(owner["workspace_id"]), 0)
    code = client.post(
        "/api/v1/invites", json={"role": role}, headers=_auth(owner["access_token"])
    ).get_json()["data"]["code"]
    return _signup(client, username, company_name="", category="", invite_code=code)


ARTICLE = {"category": "Bed", "product_type": "Bedsheet", "brand": "ASTER", "size": "DB BS", "mrp": 999}


def _add_article(client, token):
    return client.post("/api/v1/article-master/manual", json=ARTICLE, headers=_auth(token))


def _brands(client, token):
    body = client.get("/api/v1/article-master/list", headers=_auth(token)).get_json()
    return [a["brand"] for a in body["articles"]], body


def test_teammate_reads_company_catalog_but_cannot_change_it(app, client):
    owner_data = _signup(client, "amboss")
    owner = owner_data["access_token"]
    added = _add_article(client, owner)
    assert added.status_code in (200, 201), added.get_json()
    article_id = added.get_json()["article"]["id"]

    rep = _join(app, client, owner_data, "amrep")["access_token"]
    brands, body = _brands(client, rep)
    assert brands == ["Aster"]
    assert body["shared"] is True and body["can_edit"] is False
    assert client.get(f"/api/v1/article-master/{article_id}", headers=_auth(rep)).status_code == 200

    assert _add_article(client, rep).status_code == 403
    edit = client.patch(
        f"/api/v1/article-master/{article_id}/edit",
        json={"field": "mrp", "value": 1},
        headers=_auth(rep),
    )
    assert edit.status_code == 403
    assert client.delete(f"/api/v1/article-master/{article_id}", headers=_auth(rep)).status_code == 403
    assert client.post(
        "/api/v1/article-master/delete-all", json={}, headers=_auth(rep)
    ).status_code == 403
    assert client.post(
        "/api/v1/article-master/merge-duplicates", json={"auto": True}, headers=_auth(rep)
    ).status_code == 403

    assert _brands(client, owner)[0] == ["Aster"]


def test_company_admin_teammate_edits_the_shared_catalog(app, client):
    owner_data = _signup(client, "amchief")
    owner = owner_data["access_token"]
    article_id = _add_article(client, owner).get_json()["article"]["id"]
    admin = _join(app, client, owner_data, "amadmin", role="hop_admin")["access_token"]

    assert _brands(client, admin)[1]["can_edit"] is True
    edit = client.patch(
        f"/api/v1/article-master/{article_id}/edit",
        json={"field": "mrp", "value": 1200},
        headers=_auth(admin),
    )
    assert edit.status_code == 200, edit.get_json()
    owner_view = _brands(client, owner)[1]["articles"][0]
    assert owner_view["mrp"] == 1200


def test_other_companies_do_not_see_the_catalog(client):
    first = _signup(client, "amfirst")["access_token"]
    _add_article(client, first)
    other = _signup(client, "amother")["access_token"]
    brands, body = _brands(client, other)
    assert brands == []
    assert body["can_edit"] is True
