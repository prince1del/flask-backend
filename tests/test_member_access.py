"""Owner picks features per invite, can change them or block a login later; never across companies."""
from __future__ import annotations

from app.services import member_access
from tests.test_subscription_and_invites import (  # noqa: F401  (fixtures)
    _allow_team, _auth, _login, _signup, app, client,
)


def _team(app, client, owner_name, member_name, features):
    owner = _signup(client, owner_name).get_json()["data"]
    _allow_team(app, owner)
    h = _auth(owner["access_token"])
    made = client.post("/api/v1/invites", headers=h, json={"role": "sales_executive", "features": features})
    assert made.status_code == 201, made.get_json()
    assert made.get_json()["data"]["features"] == [k for k in member_access.FEATURE_KEYS if k in features]
    member = _signup(client, member_name, company_name="", category="",
                     invite_code=made.get_json()["data"]["code"]).get_json()["data"]
    member_access.invalidate()
    return owner, h, member, _auth(member["access_token"])


def test_member_gets_only_the_chosen_features(app, client):
    _owner, h, member, mh = _team(app, client, "boss1", "rep1", ["order_desk", "todo"])
    st = client.get("/api/v1/subscription/status", headers=mh).get_json()["data"]
    assert st["features"] == ["order_desk", "todo"]
    assert any(f["key"] == "articles" for f in st["feature_catalog"])

    off = client.get("/api/v1/article-master/list", headers=mh)
    assert off.status_code == 403 and off.get_json()["error"]["code"] == "FEATURE_OFF"
    assert client.get("/api/v1/dsr-market/visits", headers=mh).status_code == 403
    assert client.get("/api/v1/personal-todos", headers=mh).status_code != 403
    assert client.get("/api/v1/filled-orders/list", headers=mh).status_code != 403
    # The owner is never limited.
    assert client.get("/api/v1/article-master/list", headers=h).status_code == 200
    assert client.get("/api/v1/subscription/status", headers=h).get_json()["data"]["features"] is None


def test_owner_changes_features_and_blocks_login(app, client):
    _owner, h, member, mh = _team(app, client, "boss2", "rep2", ["todo"])
    team = client.get("/api/v1/invites/team", headers=h).get_json()["data"]
    row = next(m for m in team["members"] if m["username"] == "rep2")
    assert row["features"] == ["todo"] and row["blocked"] is False
    assert next(m for m in team["members"] if m["username"] == "boss2")["is_owner"] is True

    r = client.post(f"/api/v1/invites/members/{row['id']}/access", headers=h,
                    json={"features": ["todo", "articles"]})
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/v1/article-master/list", headers=mh).status_code == 200

    assert client.post(f"/api/v1/invites/members/{row['id']}/access", headers=h,
                       json={"blocked": True}).status_code == 200
    blocked = client.get("/api/v1/personal-todos", headers=mh)
    assert blocked.status_code == 403 and blocked.get_json()["error"]["code"] == "ACCOUNT_BLOCKED"
    login = client.post("/api/v1/auth/login", json={"username": "rep2", "password": "pass12345"})
    assert login.status_code == 403 and login.get_json()["error"]["code"] == "ACCOUNT_BLOCKED"

    assert client.post(f"/api/v1/invites/members/{row['id']}/access", headers=h,
                       json={"blocked": False}).status_code == 200
    assert _login(client, "rep2", "pass12345")


def test_members_and_strangers_cannot_change_access(app, client):
    owner, h, member, mh = _team(app, client, "boss3", "rep3", ["todo"])
    team = client.get("/api/v1/invites/team", headers=h).get_json()["data"]["members"]
    owner_row = next(m for m in team if m["username"] == "boss3")
    rep_row = next(m for m in team if m["username"] == "rep3")
    # a member cannot see the team or change anyone
    assert client.get("/api/v1/invites/team", headers=mh).status_code == 403
    assert client.post(f"/api/v1/invites/members/{owner_row['id']}/access", headers=mh,
                       json={"blocked": True}).status_code == 403
    # the owner's own access cannot be changed
    assert client.post(f"/api/v1/invites/members/{owner_row['id']}/access", headers=h,
                       json={"blocked": True}).status_code == 403
    # unknown feature names are refused
    assert client.post(f"/api/v1/invites/members/{rep_row['id']}/access", headers=h,
                       json={"features": ["admin_everything"]}).status_code == 400
    assert client.post("/api/v1/invites", headers=h,
                       json={"role": "sales_executive", "features": ["root"]}).status_code == 400


def test_other_company_cannot_see_or_touch_my_members(app, client):
    _a, ha, _ma, mha = _team(app, client, "alpha", "alpharep", ["todo"])
    _b, hb, _mb, _mhb = _team(app, client, "beta", "betarep", ["todo"])
    alpha_team = client.get("/api/v1/invites/team", headers=ha).get_json()["data"]["members"]
    beta_team = client.get("/api/v1/invites/team", headers=hb).get_json()["data"]["members"]
    assert {m["username"] for m in alpha_team} == {"alpha", "alpharep"}
    assert {m["username"] for m in beta_team} == {"beta", "betarep"}

    alpharep_id = next(m["id"] for m in alpha_team if m["username"] == "alpharep")
    r = client.post(f"/api/v1/invites/members/{alpharep_id}/access", headers=hb, json={"blocked": True})
    assert r.status_code == 404  # beta's owner cannot block alpha's rep
    assert client.get("/api/v1/personal-todos", headers=mha).status_code != 403


def test_member_sign_in_waits_for_owner_approval_on_one_phone(app, client):
    from tests.test_subscription_and_invites import _allow_team

    owner = _signup(client, "gateboss").get_json()["data"]
    _allow_team(app, owner)
    h = _auth(owner["access_token"])
    code = client.post("/api/v1/invites", headers=h, json={"role": "sales_executive"}).get_json()["data"]["code"]
    joined = _signup(client, "gaterep", approve=False, company_name="", category="", invite_code=code,
                     device_label="Rahul's Galaxy S23 · Samsung SM-S918B").get_json()["data"]
    mh = _auth(joined["access_token"])
    r = client.get("/api/v1/personal-todos", headers=mh)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "LOGIN_PENDING"
    assert client.get("/api/v1/auth/approval", headers=mh).get_json()["data"]["approval"] == "pending"

    reqs = client.get("/api/v1/invites/login-requests", headers=h).get_json()["data"]["requests"]
    assert reqs[0]["username"] == "gaterep" and reqs[0]["device_label"].startswith("Rahul's Galaxy")
    assert client.get("/api/v1/invites/login-requests", headers=mh).status_code == 403
    assert client.post(f"/api/v1/invites/login-requests/{reqs[0]['id']}", headers=h, json={"approve": True}).status_code == 200
    assert client.get("/api/v1/personal-todos", headers=mh).status_code != 403
    team = client.get("/api/v1/invites/team", headers=h).get_json()["data"]["members"]
    assert next(m for m in team if m["username"] == "gaterep")["device"]["device"].startswith("Rahul's Galaxy")

    # The owner never waits for approval.
    assert client.get("/api/v1/invites/team", headers=h).status_code == 200

    # Signing in again (another phone) needs a new approval; the old phone is out.
    login = client.post("/api/v1/auth/login", json={"username": "gaterep", "password": "pass12345",
                                                     "device_label": "Office tablet"})
    assert login.status_code == 200 and login.get_json()["data"]["approval"] == "pending"
    new_h = _auth(login.get_json()["data"]["access_token"])
    assert client.get("/api/v1/personal-todos", headers=new_h).status_code == 403
    assert client.get("/api/v1/personal-todos", headers=mh).status_code in (401, 403)

    # Approve, then sign out: the session ends, refresh stops, next sign-in waits again.
    rid = client.get("/api/v1/invites/login-requests", headers=h).get_json()["data"]["requests"][0]["id"]
    client.post(f"/api/v1/invites/login-requests/{rid}", headers=h, json={"approve": True})
    refresh = login.get_json()["data"]["refresh_token"]
    fresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert fresh.status_code == 200 and fresh.get_json()["data"]["refresh_token"]
    client.post("/api/v1/auth/logout", headers=new_h)
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_other_company_cannot_approve_my_member(app, client):
    from tests.test_subscription_and_invites import _allow_team

    a = _signup(client, "appra").get_json()["data"]
    _allow_team(app, a)
    code = client.post("/api/v1/invites", headers=_auth(a["access_token"]), json={"role": "sales_executive"}).get_json()["data"]["code"]
    _signup(client, "appr_rep", approve=False, company_name="", category="", invite_code=code)
    b = _signup(client, "apprb").get_json()["data"]
    _allow_team(app, b)
    hb = _auth(b["access_token"])
    assert client.get("/api/v1/invites/login-requests", headers=hb).get_json()["data"]["requests"] == []
    rid = client.get("/api/v1/invites/login-requests", headers=_auth(a["access_token"])).get_json()["data"]["requests"][0]["id"]
    assert client.post(f"/api/v1/invites/login-requests/{rid}", headers=hb, json={"approve": True}).status_code == 404
