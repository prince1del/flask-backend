"""Per-login 7-day trial, super-admin activation codes, and company invite codes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.services import company_invites as ci
from app.services import subscriptions as subs
from centralized_db_system.db import CentralizedDB

QUESTION = CentralizedDB.SECRET_QUESTIONS[0]


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "subs.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-subscriptions-32bytes")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")
    subs.clear_cache()
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application
    subs.clear_cache()


@pytest.fixture
def client(app):
    return app.test_client()


def _db(app) -> CentralizedDB:
    return CentralizedDB(app.config["DATABASE_PATH"])


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
    return client.post("/api/v1/signup", json=payload)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, username, password):
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return res.get_json()["data"]["access_token"]


def _expire(app, username):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        subs.ensure_schema(conn)
        conn.execute(
            "UPDATE users SET trial_ends_at = ?, paid_until = NULL WHERE username = ?",
            (past, username),
        )
        conn.commit()
    subs.clear_cache()


def _founder_token(app, client):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'kunwar1del'").fetchone()
    if row is None:
        created = _db(app).create_user(
            "kunwar1del", "founder123", role="admin", workspace_id="house_of_prizm"
        )
        user_id = int(created["id"])
    else:
        user_id = int(row[0])
    access, _ = app.extensions["jwt_service"].create_tokens(
        user_id=user_id,
        username="kunwar1del",
        role="admin",
        workspace_id="house_of_prizm",
        is_workspace_owner=True,
    )
    return access


def _activate(app, username, days=30):
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        subs.ensure_schema(conn)
        conn.execute("UPDATE users SET paid_until = ? WHERE username = ?", (until, username))
        conn.commit()
    subs.clear_cache()


def _allow_team(app, owner, max_members=0):
    """Activated owner whose company NEXORA allowed ``max_members`` logins."""
    _activate(app, owner["username"])
    db = _db(app)
    ci.set_team_size(db, db.get_company_id_for_workspace(owner["workspace_id"]), max_members)


def test_signup_starts_7_day_trial(client):
    res = _signup(client, "trialco")
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    ends = datetime.fromisoformat(data["trial_ends_at"])
    assert timedelta(days=6, hours=23) < ends - datetime.now(timezone.utc) <= timedelta(days=7)

    status = client.get("/api/v1/subscription/status", headers=_auth(data["access_token"]))
    body = status.get_json()["data"]
    assert body["status"] == "trial"
    assert body["days_left"] == 7


def test_existing_logins_are_unlimited(app, client):
    _db(app).create_user("oldhand", "pass12345", role="hop_admin", workspace_id="house_of_prizm")
    token = _login(client, "oldhand", "pass12345")
    body = client.get("/api/v1/subscription/status", headers=_auth(token)).get_json()["data"]
    assert body["status"] == "unlimited"


def test_expired_login_is_blocked_until_code_redeemed(app, client):
    token = _signup(client, "lapsed").get_json()["data"]["access_token"]
    _expire(app, "lapsed")

    blocked = client.get("/api/v1/me/profile", headers=_auth(token))
    assert blocked.status_code == 402
    assert blocked.get_json()["error"]["code"] == "SUBSCRIPTION_EXPIRED"
    # Plan status stays reachable so the app can show the code screen.
    status = client.get("/api/v1/subscription/status", headers=_auth(token))
    assert status.get_json()["data"]["status"] == "expired"

    founder = _founder_token(app, client)
    made = client.post(
        "/api/v1/subscription/codes", json={"duration_days": 30}, headers=_auth(founder)
    )
    assert made.status_code == 201, made.get_json()
    code = made.get_json()["data"]["codes"][0]["code"]

    redeemed = client.post(
        "/api/v1/subscription/redeem", json={"code": code.lower()}, headers=_auth(token)
    )
    assert redeemed.status_code == 200, redeemed.get_json()
    assert redeemed.get_json()["data"]["status"] == "active"
    assert redeemed.get_json()["data"]["days_left"] == 30

    assert client.get("/api/v1/me/profile", headers=_auth(token)).status_code == 200

    again = client.post("/api/v1/subscription/redeem", json={"code": code}, headers=_auth(token))
    assert again.status_code == 400


def test_code_during_trial_adds_on_top_of_trial(app, client):
    token = _signup(client, "earlybird").get_json()["data"]["access_token"]
    founder = _founder_token(app, client)
    code = client.post(
        "/api/v1/subscription/codes", json={"duration_days": 30}, headers=_auth(founder)
    ).get_json()["data"]["codes"][0]["code"]
    res = client.post("/api/v1/subscription/redeem", json={"code": code}, headers=_auth(token))
    assert res.get_json()["data"]["days_left"] == 37


def test_code_for_named_user_rejects_others(app, client):
    _signup(client, "rightone")
    other = _signup(client, "wrongone").get_json()["data"]["access_token"]
    founder = _founder_token(app, client)
    code = client.post(
        "/api/v1/subscription/codes",
        json={"duration_days": 10, "for_username": "rightone"},
        headers=_auth(founder),
    ).get_json()["data"]["codes"][0]["code"]
    res = client.post("/api/v1/subscription/redeem", json={"code": code}, headers=_auth(other))
    assert res.status_code == 400


def test_only_super_admin_makes_codes(client):
    token = _signup(client, "notboss").get_json()["data"]["access_token"]
    res = client.post(
        "/api/v1/subscription/codes", json={"duration_days": 30}, headers=_auth(token)
    )
    assert res.status_code == 403


def test_super_admin_never_blocked(app, client):
    founder = _founder_token(app, client)
    _expire(app, "kunwar1del")
    assert client.get("/api/v1/me/profile", headers=_auth(founder)).status_code == 200


def test_invite_code_joins_existing_company(app, client):
    owner = _signup(client, "bossman").get_json()["data"]
    _allow_team(app, owner)
    made = client.post(
        "/api/v1/invites",
        json={"role": "sales_executive", "max_uses": 1},
        headers=_auth(owner["access_token"]),
    )
    assert made.status_code == 201, made.get_json()
    code = made.get_json()["data"]["code"]

    check = client.get(f"/api/v1/invites/check?code={code}").get_json()["data"]
    assert check["valid"] is True
    assert check["company_name"] == "bossman Traders"
    assert check["role"] == "sales_executive"

    joined = _signup(client, "newrep", company_name="", category="", invite_code=code)
    assert joined.status_code == 201, joined.get_json()
    data = joined.get_json()["data"]
    assert data["workspace_id"] == owner["workspace_id"]
    assert data["role"] == "sales_executive"
    assert data["joined_existing_company"] is True
    assert data["trial_ends_at"]

    # Single-use code is now spent.
    second = _signup(client, "late", company_name="", category="", invite_code=code)
    assert second.status_code == 400

    members = client.get(
        "/api/v1/invites/members", headers=_auth(owner["access_token"])
    ).get_json()["data"]["members"]
    assert {m["username"] for m in members} == {"bossman", "newrep"}


def test_failed_join_gives_the_invite_back(app, client):
    owner = _signup(client, "giver").get_json()["data"]
    _allow_team(app, owner)
    code = client.post(
        "/api/v1/invites", json={"role": "sales_executive"}, headers=_auth(owner["access_token"])
    ).get_json()["data"]["code"]
    clash = _signup(client, "giver", company_name="", category="", invite_code=code)
    assert clash.status_code == 409
    ok = _signup(client, "fresh", company_name="", category="", invite_code=code)
    assert ok.status_code == 201, ok.get_json()


def test_invited_rep_cannot_invite_or_hand_out_admin(app, client):
    owner = _signup(client, "chief").get_json()["data"]
    _allow_team(app, owner)
    code = client.post(
        "/api/v1/invites", json={"role": "sales_executive"}, headers=_auth(owner["access_token"])
    ).get_json()["data"]["code"]
    rep = _signup(client, "minion", company_name="", category="", invite_code=code)
    rep_token = rep.get_json()["data"]["access_token"]
    res = client.post("/api/v1/invites", json={"role": "hop_admin"}, headers=_auth(rep_token))
    assert res.status_code == 403

    def can_invite(token):
        return client.get("/api/v1/subscription/status", headers=_auth(token)).get_json()["data"]["can_invite"]

    assert can_invite(owner["access_token"]) is True
    assert can_invite(rep_token) is False


def test_new_company_needs_nexora_approval_for_a_team(app, client):
    owner = _signup(client, "soloco").get_json()["data"]
    token = owner["access_token"]

    # On the free trial there is no team at all.
    desk = client.get("/api/v1/invites", headers=_auth(token)).get_json()["data"]
    assert desk["can_manage"] is True
    assert desk["can_invite"] is False
    assert desk["plan_active"] is False
    status = client.get("/api/v1/subscription/status", headers=_auth(token)).get_json()["data"]
    assert status["can_invite"] is False and status["plan_active"] is False
    assert desk["can_create"] is False
    assert client.post(
        "/api/v1/invites/team-request", json={"members": 2}, headers=_auth(token)
    ).status_code == 403

    _activate(app, "soloco")
    desk = client.get("/api/v1/invites", headers=_auth(token)).get_json()["data"]
    assert desk["plan_active"] is True and desk["can_invite"] is True
    assert desk["can_create"] is False
    assert desk["team_limit"] == 1 and desk["seats_left"] == 0

    blocked = client.post("/api/v1/invites", json={"role": "sales_executive"}, headers=_auth(token))
    assert blocked.status_code == 403

    asked = client.post(
        "/api/v1/invites/team-request", json={"members": 2, "note": "2 salesmen"}, headers=_auth(token)
    )
    assert asked.status_code == 200, asked.get_json()
    assert client.get("/api/v1/invites", headers=_auth(token)).get_json()["data"]["team_request"]["members"] == 2

    founder = _founder_token(app, client)
    requests = client.get(
        "/api/v1/subscription/team-requests", headers=_auth(founder)
    ).get_json()["data"]["requests"]
    mine = next(r for r in requests if r["username"] == "soloco")
    assert mine["status"] == "pending" and mine["members"] == 2
    assert client.get(
        "/api/v1/subscription/status", headers=_auth(founder)
    ).get_json()["data"]["team_requests_pending"] >= 1

    granted = client.post(
        f"/api/v1/subscription/companies/{mine['company_id']}/team",
        json={"max_members": 3},
        headers=_auth(founder),
    )
    assert granted.status_code == 200, granted.get_json()

    made = client.post(
        "/api/v1/invites", json={"role": "sales_executive", "max_uses": 10}, headers=_auth(token)
    )
    assert made.status_code == 201, made.get_json()
    invite = made.get_json()["data"]
    assert invite["max_uses"] == 2
    for name in ("teamone", "teamtwo"):
        joined = _signup(client, name, company_name="", category="", invite_code=invite["code"])
        assert joined.status_code == 201, joined.get_json()
    assert client.post(
        "/api/v1/invites", json={"role": "sales_executive"}, headers=_auth(token)
    ).status_code == 403


def test_long_lived_invite_still_gives_member_only_a_7_day_trial(app, client):
    owner = _signup(client, "longcode").get_json()["data"]
    _allow_team(app, owner)
    code = client.post(
        "/api/v1/invites",
        json={"role": "sales_executive", "expires_in_days": 365},
        headers=_auth(owner["access_token"]),
    ).get_json()["data"]
    assert datetime.fromisoformat(code["expires_at"]) - datetime.now(timezone.utc) <= timedelta(days=30)
    member = _signup(client, "longmember", company_name="", category="", invite_code=code["code"])
    ends = datetime.fromisoformat(member.get_json()["data"]["trial_ends_at"])
    assert ends - datetime.now(timezone.utc) <= timedelta(days=7)


def test_only_super_admin_grants_team_places(app, client):
    owner = _signup(client, "sneaky").get_json()["data"]
    company_id = _db(app).get_company_id_for_workspace(owner["workspace_id"])
    res = client.post(
        f"/api/v1/subscription/companies/{company_id}/team",
        json={"max_members": 50},
        headers=_auth(owner["access_token"]),
    )
    assert res.status_code == 403
