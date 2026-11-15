"""Razorpay self-serve billing: keys, products, order -> pay page -> verify / webhook -> plan & seats.

Razorpay itself is faked (no network, no money)."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.services import billing
from app.services import subscriptions as subs
from tests.test_subscription_and_invites import (  # noqa: F401  (fixtures)
    _auth, _expire, _founder_token, _signup, app, client,
)

KEY_SECRET = "test_secret_abcdefghijkl"
HOOK_SECRET = "hook_secret_123"


def _fake_rzp(monkeypatch):
    calls = {"orders": 0, "captured": []}
    amounts: dict[str, int] = {}

    def fake(method, path, cfg, body=None):
        if method == "POST" and path == "/orders":
            calls["orders"] += 1
            oid = f"order_T{calls['orders']}"
            amounts[oid] = body["amount"]
            return {"id": oid, "amount": body["amount"]}
        if method == "GET" and path.startswith("/payments/"):
            pid = path.rsplit("/", 1)[-1]
            return {"id": pid, "amount": amounts[pid.replace("pay_", "order_")], "status": "authorized"}
        if method == "POST" and path.endswith("/capture"):
            calls["captured"].append(path)
            return {"status": "captured"}
        raise AssertionError((method, path))

    monkeypatch.setattr(billing, "_rzp", fake)
    return calls


def _setup_admin(client, app):
    admin = _auth(_founder_token(app, client))
    r = client.post("/api/v1/billing/config", headers=admin, json={
        "key_id": "rzp_test_ABCDEF123456", "key_secret": KEY_SECRET, "webhook_secret": HOOK_SECRET})
    assert r.status_code == 200, r.get_json()
    cfg = r.get_json()["data"]
    assert cfg["configured"] and cfg["mode"] == "test"
    assert KEY_SECRET not in json.dumps(cfg)  # secrets never come back
    for p in ({"code": "plan_month", "name": "Monthly plan", "kind": "plan", "days": 30, "price": 999},
              {"code": "seats_5", "name": "5 team members", "kind": "seats", "seats": 5, "price": 1500}):
        assert client.post("/api/v1/billing/admin/products", headers=admin, json=p).status_code == 200
    return admin


def _pay(client, token, order_id, secret=KEY_SECRET):
    pid = order_id.replace("order_", "pay_")
    sig = hmac.new(secret.encode(), f"{order_id}|{pid}".encode(), hashlib.sha256).hexdigest()
    return client.post("/api/v1/billing/verify", json={
        "token": token, "razorpay_order_id": order_id, "razorpay_payment_id": pid, "razorpay_signature": sig})


def _order_id(app, token):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        return conn.execute("SELECT rzp_order_id FROM billing_orders WHERE token = ?", (token,)).fetchone()[0]


def test_only_super_admin_sets_keys_and_prices(app, client):
    owner = _signup(client, "notadmin").get_json()["data"]
    h = _auth(owner["access_token"])
    assert client.post("/api/v1/billing/config", headers=h, json={"key_id": "rzp_test_X1234567"}).status_code == 403
    assert client.post("/api/v1/billing/admin/products", headers=h,
                       json={"code": "x1", "name": "x", "kind": "plan", "days": 1, "price": 1}).status_code == 403
    assert client.get("/api/v1/billing/admin/sales", headers=h).status_code == 403


def test_no_keys_means_no_orders(app, client):
    owner = _signup(client, "early").get_json()["data"]
    r = client.post("/api/v1/billing/orders", headers=_auth(owner["access_token"]), json={"product_code": "plan_month"})
    assert r.status_code == 400 and "not set up" in r.get_json()["error"]["message"]


def test_buy_plan_end_to_end_and_only_once(app, client, monkeypatch):
    calls = _fake_rzp(monkeypatch)
    _setup_admin(client, app)
    owner = _signup(client, "buyer").get_json()["data"]
    h = _auth(owner["access_token"])
    _expire(app, "buyer")  # an expired login can still pay
    assert client.get("/api/v1/article-master/list", headers=h).status_code == 402

    r = client.post("/api/v1/billing/orders", headers=h, json={"product_code": "plan_month"})
    assert r.status_code == 201, r.get_json()
    order = r.get_json()["data"]
    assert order["amount"] == 999 and order["pay_path"].startswith("/pay/")

    page = client.get(order["pay_path"])  # no login on the phone's browser
    assert page.status_code == 200 and b"checkout.razorpay.com" in page.data and b"Test mode" in page.data

    oid = _order_id(app, order["token"])
    assert _pay(client, order["token"], oid, secret="wrong").status_code == 400  # forged signature
    ok = _pay(client, order["token"], oid)
    assert ok.status_code == 200, ok.get_json()
    assert calls["captured"]  # authorized payment was captured
    until = datetime.fromisoformat(ok.get_json()["data"]["paid_until"])
    assert timedelta(days=29) < until - datetime.now(timezone.utc) <= timedelta(days=30)
    assert client.get("/api/v1/article-master/list", headers=h).status_code == 200

    again = _pay(client, order["token"], oid)  # page reload / webhook: no second 30 days
    assert again.status_code == 200
    st = client.get("/api/v1/subscription/status", headers=h).get_json()["data"]
    assert st["status"] == "active" and st["days_left"] <= 30
    mine = client.get(f"/api/v1/billing/orders/{order['token']}", headers=h).get_json()["data"]
    assert mine["status"] == "paid"


def test_webhook_fulfils_when_page_was_closed(app, client, monkeypatch):
    _fake_rzp(monkeypatch)
    _setup_admin(client, app)
    owner = _signup(client, "closer").get_json()["data"]
    h = _auth(owner["access_token"])
    order = client.post("/api/v1/billing/orders", headers=h, json={"product_code": "plan_month"}).get_json()["data"]
    oid = _order_id(app, order["token"])
    event = json.dumps({"event": "order.paid", "payload": {
        "order": {"entity": {"id": oid, "amount_paid": 99900}},
        "payment": {"entity": {"id": "pay_W1", "order_id": oid, "amount": 99900}}}}).encode()
    bad = client.post("/api/v1/billing/webhook", data=event, headers={"X-Razorpay-Signature": "nope"})
    assert bad.status_code == 401
    sig = hmac.new(HOOK_SECRET.encode(), event, hashlib.sha256).hexdigest()
    ok = client.post("/api/v1/billing/webhook", data=event, headers={"X-Razorpay-Signature": sig,
                                                                     "Content-Type": "application/json"})
    assert ok.status_code == 200, ok.get_json()
    assert client.get("/api/v1/subscription/status", headers=h).get_json()["data"]["status"] == "active"


def test_seats_need_a_paid_plan_then_add_places(app, client, monkeypatch):
    _fake_rzp(monkeypatch)
    admin = _setup_admin(client, app)
    owner = _signup(client, "seatco").get_json()["data"]
    h = _auth(owner["access_token"])
    r = client.post("/api/v1/billing/orders", headers=h, json={"product_code": "seats_5"})
    assert r.status_code == 400 and "plan first" in r.get_json()["error"]["message"]

    plan = client.post("/api/v1/billing/orders", headers=h, json={"product_code": "plan_month"}).get_json()["data"]
    assert _pay(client, plan["token"], _order_id(app, plan["token"])).status_code == 200
    seats = client.post("/api/v1/billing/orders", headers=h, json={"product_code": "seats_5", "quantity": 2})
    assert seats.status_code == 201 and seats.get_json()["data"]["amount"] == 3000
    done = _pay(client, seats.get_json()["data"]["token"], _order_id(app, seats.get_json()["data"]["token"]))
    assert done.get_json()["data"]["team_limit"] == 11  # owner + 10 bought
    inv = client.get("/api/v1/invites", headers=h).get_json()["data"]
    assert inv["can_create"] is True

    sales = client.get("/api/v1/billing/admin/sales", headers=admin).get_json()["data"]
    assert sales["paid_orders"] == 2 and sales["revenue"] == 3999


def test_amount_mismatch_is_refused(app, client, monkeypatch):
    _fake_rzp(monkeypatch)
    _setup_admin(client, app)
    owner = _signup(client, "cheat").get_json()["data"]
    order = client.post("/api/v1/billing/orders", headers=_auth(owner["access_token"]),
                        json={"product_code": "plan_month"}).get_json()["data"]
    oid = _order_id(app, order["token"])
    event = json.dumps({"event": "payment.captured", "payload": {
        "payment": {"entity": {"id": "pay_C", "order_id": oid, "amount": 100}}}}).encode()
    sig = hmac.new(HOOK_SECRET.encode(), event, hashlib.sha256).hexdigest()
    r = client.post("/api/v1/billing/webhook", data=event, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 400
    subs.clear_cache()
    assert client.get("/api/v1/subscription/status",
                      headers=_auth(owner["access_token"])).get_json()["data"]["status"] == "trial"


# ── Auto-renewal ─────────────────────────────────────────────────────────────

def _fake_rzp_subs(monkeypatch):
    calls = {"plans": 0, "subs": 0, "cancelled": []}
    pay_amounts: dict[str, int] = {}

    def fake(method, path, cfg, body=None):
        if method == "POST" and path == "/plans":
            calls["plans"] += 1
            calls["last_plan"] = body
            return {"id": f"plan_P{calls['plans']}"}
        if method == "POST" and path == "/subscriptions":
            calls["subs"] += 1
            return {"id": f"sub_S{calls['subs']}", "status": "created"}
        if method == "POST" and path.endswith("/cancel"):
            calls["cancelled"].append(path)
            return {"status": "active"}
        if method == "GET" and path.startswith("/payments/"):
            pid = path.rsplit("/", 1)[-1]
            return {"id": pid, "amount": pay_amounts.get(pid, 99900), "status": "captured"}
        raise AssertionError((method, path))

    monkeypatch.setattr(billing, "_rzp", fake)
    return calls, pay_amounts


def _hook(client, event: dict):
    raw = json.dumps(event).encode()
    sig = hmac.new(HOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/v1/billing/webhook", data=raw, headers={"X-Razorpay-Signature": sig})


def _days_left(client, h):
    subs.clear_cache()
    return client.get("/api/v1/subscription/status", headers=h).get_json()["data"]["days_left"]


def test_auto_renew_first_charge_and_each_renewal_once(app, client, monkeypatch):
    calls, _amounts = _fake_rzp_subs(monkeypatch)
    admin = _setup_admin(client, app)
    assert client.post("/api/v1/billing/admin/products", headers=admin, json={
        "code": "plan_month", "name": "Monthly plan", "kind": "plan", "days": 30, "price": 999,
        "renewable": True}).status_code == 200
    owner = _signup(client, "renewer").get_json()["data"]
    h = _auth(owner["access_token"])
    _expire(app, "renewer")

    # seats can never auto-renew
    assert client.post("/api/v1/billing/subscriptions", headers=h, json={"product_code": "seats_5"}).status_code == 400
    r = client.post("/api/v1/billing/subscriptions", headers=h, json={"product_code": "plan_month"})
    assert r.status_code == 201, r.get_json()
    sub = r.get_json()["data"]
    assert sub["pay_path"].startswith("/pay/s/") and sub["period"] == "month"
    assert calls["last_plan"]["period"] == "monthly" and calls["last_plan"]["item"]["amount"] == 99900
    page = client.get(sub["pay_path"])
    assert page.status_code == 200 and b"subscription_id" in page.data and b"Auto-renews" in page.data

    sid = "sub_S1"
    bad = client.post("/api/v1/billing/verify-subscription", json={
        "token": sub["token"], "razorpay_subscription_id": sid, "razorpay_payment_id": "pay_F1",
        "razorpay_signature": "forged"})
    assert bad.status_code == 400
    sig = hmac.new(KEY_SECRET.encode(), f"pay_F1|{sid}".encode(), hashlib.sha256).hexdigest()
    ok = client.post("/api/v1/billing/verify-subscription", json={
        "token": sub["token"], "razorpay_subscription_id": sid, "razorpay_payment_id": "pay_F1",
        "razorpay_signature": sig})
    assert ok.status_code == 200, ok.get_json()
    assert 29 <= _days_left(client, h) <= 30

    charged = {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": sid, "status": "active"}},
        "payment": {"entity": {"id": "pay_F1", "amount": 99900}}}}
    assert _hook(client, charged).status_code == 200  # same first payment again: nothing added
    assert 29 <= _days_left(client, h) <= 30

    charged["payload"]["payment"]["entity"]["id"] = "pay_R2"  # next month's renewal
    assert _hook(client, charged).status_code == 200
    assert 59 <= _days_left(client, h) <= 60

    charged["payload"]["payment"]["entity"].update({"id": "pay_R3", "amount": 100})
    assert _hook(client, charged).status_code == 400  # wrong amount is refused
    assert 59 <= _days_left(client, h) <= 60

    mine = client.get("/api/v1/billing/subscriptions", headers=h).get_json()["data"]["subscriptions"]
    assert mine[0]["status"] == "active" and mine[0]["charges"] == 2
    again = client.post("/api/v1/billing/subscriptions", headers=h, json={"product_code": "plan_month"})
    assert again.status_code == 400 and "already on" in again.get_json()["error"]["message"]

    sales = client.get("/api/v1/billing/admin/sales", headers=admin).get_json()["data"]
    assert sales["paid_orders"] == 2 and sales["revenue"] == 1998
    assert sales["subscriptions"][0]["charges"] == 2


def test_stop_auto_renew_keeps_paid_time(app, client, monkeypatch):
    calls, _amounts = _fake_rzp_subs(monkeypatch)
    admin = _setup_admin(client, app)
    client.post("/api/v1/billing/admin/products", headers=admin, json={
        "code": "plan_year", "name": "Yearly plan", "kind": "plan", "days": 365, "price": 9999, "renewable": True})
    owner = _signup(client, "stopper").get_json()["data"]
    h = _auth(owner["access_token"])
    sub = client.post("/api/v1/billing/subscriptions", headers=h, json={"product_code": "plan_year"}).get_json()["data"]
    assert calls["last_plan"]["period"] == "yearly"
    _hook(client, {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_S1"}}, "payment": {"entity": {"id": "pay_Y1", "amount": 999900}}}})
    days = _days_left(client, h)
    assert days >= 365

    other = _signup(client, "stranger").get_json()["data"]
    assert client.post(f"/api/v1/billing/subscriptions/{sub['token']}/cancel",
                       headers=_auth(other["access_token"])).status_code == 403
    stop = client.post(f"/api/v1/billing/subscriptions/{sub['token']}/cancel", headers=h)
    assert stop.status_code == 200 and stop.get_json()["data"]["cancel_at_end"] is True
    assert calls["cancelled"]
    _hook(client, {"event": "subscription.cancelled", "payload": {"subscription": {"entity": {"id": "sub_S1"}}}})
    assert _days_left(client, h) == days  # the year already paid stays
    mine = client.get("/api/v1/billing/subscriptions", headers=h).get_json()["data"]["subscriptions"]
    assert mine[0]["status"] == "cancelled"
    # once stopped, a new auto-renew may start
    assert client.post("/api/v1/billing/subscriptions", headers=h,
                       json={"product_code": "plan_year"}).status_code == 201
