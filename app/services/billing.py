"""Self-serve payments with Razorpay: buy a plan or team seats, no manual approval.

Flow
----
1. The app asks ``create_order`` for a product (plan = N days, seats = N extra
   logins). We create a Razorpay order and keep it in ``billing_orders`` with a
   random public ``token``.
2. The buyer pays on our hosted page ``/pay/<token>`` (Razorpay Checkout).
3. Checkout hands back payment id + signature -> ``verify_checkout`` checks the
   HMAC with the key secret, makes sure the payment is captured, and fulfils.
   Razorpay's webhook (``handle_webhook``) does the same if the page was closed.
   Fulfilment runs once per order (idempotent), whoever arrives first.

What a purchase does
--------------------
* plan  -> the company owner's ``paid_until`` grows by days x quantity (members
  follow the owner's plan, see subscriptions._effective_plan). A login without
  a company owner extends its own.
* seats -> ``companies.max_members`` grows by seats x quantity (needs a paid plan).

Auto-renewal
------------
A plan product marked ``renewable`` can also be bought as a Razorpay
Subscription (UPI Autopay / card mandate): ``create_subscription`` makes the
Razorpay plan once and a subscription; the buyer authorises it on
``/pay/s/<token>``. Every charge (the first one and each renewal) arrives as
``subscription.charged`` and adds the plan's days once per payment id
(``billing_payments``). The buyer can stop it (``cancel_subscription``, at the
end of the paid cycle).

Keys live in ``billing_config`` (entered by the super admin on the web; never
sent back in full) or in env RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET. Prices are in paise in ``billing_products``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

RZP_API = "https://api.razorpay.com/v1"
MAX_QTY = 100
_CODE_RE = re.compile(r"^[a-z0-9_-]{2,40}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS billing_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS billing_products (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('plan', 'seats')),
            days INTEGER NOT NULL DEFAULT 0,
            seats INTEGER NOT NULL DEFAULT 0,
            price_paise INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            sort INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS billing_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            rzp_order_id TEXT UNIQUE,
            user_id INTEGER NOT NULL,
            username TEXT,
            workspace_id TEXT,
            company_id INTEGER,
            product_code TEXT NOT NULL,
            product_name TEXT,
            kind TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            days INTEGER NOT NULL DEFAULT 0,
            seats INTEGER NOT NULL DEFAULT 0,
            amount_paise INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            payment_id TEXT,
            result TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            fulfilled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_billing_orders_user ON billing_orders(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS billing_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            rzp_subscription_id TEXT UNIQUE,
            user_id INTEGER NOT NULL,
            username TEXT,
            workspace_id TEXT,
            company_id INTEGER,
            product_code TEXT NOT NULL,
            product_name TEXT,
            days INTEGER NOT NULL,
            amount_paise INTEGER NOT NULL,
            period TEXT,
            status TEXT NOT NULL DEFAULT 'created',
            cancel_at_end INTEGER NOT NULL DEFAULT 0,
            charges INTEGER NOT NULL DEFAULT 0,
            last_charged_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS billing_payments (
            payment_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            ref TEXT,
            amount_paise INTEGER,
            created_at TEXT NOT NULL
        );
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(billing_products)").fetchall()}
    if "renewable" not in cols:
        conn.execute("ALTER TABLE billing_products ADD COLUMN renewable INTEGER NOT NULL DEFAULT 0")
    if "rzp_plan_id" not in cols:
        conn.execute("ALTER TABLE billing_products ADD COLUMN rzp_plan_id TEXT")
    conn.commit()


# ── Keys ─────────────────────────────────────────────────────────────────────

def _cfg(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM billing_config").fetchall()
    out = {r["key"]: r["value"] or "" for r in rows}
    for env, key in (("RAZORPAY_KEY_ID", "key_id"), ("RAZORPAY_KEY_SECRET", "key_secret"),
                     ("RAZORPAY_WEBHOOK_SECRET", "webhook_secret")):
        if (os.getenv(env) or "").strip():
            out[key] = os.getenv(env).strip()
    return out


def keys(db_path) -> dict[str, str]:
    with _connect(db_path) as conn:
        return _cfg(conn)


def _mask(value: str) -> str:
    v = value or ""
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 4 else "•" * len(v))


def config_status(db_path) -> dict[str, Any]:
    """What the super admin sees: whether keys are set, never the secrets."""
    c = keys(db_path)
    key_id = c.get("key_id", "")
    return {
        "configured": bool(key_id and c.get("key_secret")),
        "mode": "live" if key_id.startswith("rzp_live_") else ("test" if key_id else None),
        "key_id": key_id,
        "key_secret": _mask(c.get("key_secret", "")),
        "webhook_secret": _mask(c.get("webhook_secret", "")),
    }


def save_config(db_path, key_id: str | None, key_secret: str | None, webhook_secret: str | None) -> dict[str, Any]:
    """Blank values keep what is saved (so secrets are never re-typed to change one field)."""
    key_id = (key_id or "").strip()
    if key_id and not re.fullmatch(r"rzp_(test|live)_[A-Za-z0-9]{6,40}", key_id):
        raise ValueError("Key ID looks wrong — it starts with rzp_test_ or rzp_live_")
    now = _now().isoformat()
    with _connect(db_path) as conn:
        for k, v in (("key_id", key_id), ("key_secret", (key_secret or "").strip()),
                     ("webhook_secret", (webhook_secret or "").strip())):
            if v:
                conn.execute(
                    "INSERT INTO billing_config (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (k, v, now),
                )
        conn.commit()
    return config_status(db_path)


# ── Products ─────────────────────────────────────────────────────────────────

def _product(row) -> dict[str, Any]:
    return {
        "code": row["code"], "name": row["name"], "kind": row["kind"],
        "days": row["days"], "seats": row["seats"],
        "price_paise": row["price_paise"], "price": round(row["price_paise"] / 100, 2),
        "active": bool(row["active"]), "sort": row["sort"],
        "renewable": bool(row["renewable"]) if "renewable" in row.keys() else False,
        "period_label": renewal_period(row["days"])[2] if row["kind"] == "plan" else None,
    }


def renewal_period(days: int) -> tuple[str, int, str]:
    """Razorpay plan period/interval for a plan of ``days`` and a label for screens."""
    days = int(days or 0)
    if days in (30, 31):
        return "monthly", 1, "month"
    if days in (90, 91, 92):
        return "monthly", 3, "3 months"
    if days in (180, 182, 183):
        return "monthly", 6, "6 months"
    if days in (365, 366):
        return "yearly", 1, "year"
    if days % 7 == 0:
        return "weekly", days // 7, f"{days // 7} weeks"
    return "daily", days, f"{days} days"


def list_products(db_path, active_only: bool = True) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        sql = "SELECT * FROM billing_products"
        if active_only:
            sql += " WHERE active = 1"
        rows = conn.execute(sql + " ORDER BY sort, price_paise").fetchall()
    return [_product(r) for r in rows]


def save_product(db_path, data: dict[str, Any]) -> dict[str, Any]:
    code = str(data.get("code") or "").strip().lower()
    if not _CODE_RE.match(code):
        raise ValueError("Code: 2–40 small letters, digits, - or _ (e.g. plan_monthly)")
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Name is required")
    kind = str(data.get("kind") or "")
    if kind not in ("plan", "seats"):
        raise ValueError("Kind must be plan or seats")
    try:
        price_paise = int(round(float(data.get("price")) * 100))
        days = int(data.get("days") or 0)
        seats = int(data.get("seats") or 0)
        sort = int(data.get("sort") or 0)
    except (TypeError, ValueError):
        raise ValueError("Price, days and seats must be numbers") from None
    if price_paise < 100:
        raise ValueError("Price must be at least ₹1")
    if kind == "plan" and not (1 <= days <= 3660):
        raise ValueError("A plan needs 1–3660 days")
    if kind == "seats" and not (1 <= seats <= 1000):
        raise ValueError("Seats must be 1–1000")
    renewable = bool(data.get("renewable")) and kind == "plan"
    if renewable and days < 7:
        raise ValueError("Auto-renew needs a plan of 7 days or more")
    with _connect(db_path) as conn:
        old = conn.execute("SELECT price_paise, days FROM billing_products WHERE code = ?", (code,)).fetchone()
        conn.execute(
            """INSERT INTO billing_products (code, name, kind, days, seats, price_paise, active, sort, updated_at, renewable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET name = excluded.name, kind = excluded.kind,
                 days = excluded.days, seats = excluded.seats, price_paise = excluded.price_paise,
                 active = excluded.active, sort = excluded.sort, updated_at = excluded.updated_at,
                 renewable = excluded.renewable""",
            (code, name, kind, days if kind == "plan" else 0, seats if kind == "seats" else 0,
             price_paise, 1 if data.get("active", True) else 0, sort, _now().isoformat(), 1 if renewable else 0),
        )
        if old and (old["price_paise"] != price_paise or old["days"] != days):
            # New price or length -> a new Razorpay plan for new subscriptions;
            # running subscriptions keep their old price.
            conn.execute("UPDATE billing_products SET rzp_plan_id = NULL WHERE code = ?", (code,))
        conn.commit()
        row = conn.execute("SELECT * FROM billing_products WHERE code = ?", (code,)).fetchone()
    return _product(row)


# ── Razorpay HTTP ────────────────────────────────────────────────────────────

def _rzp(method: str, path: str, cfg: dict[str, str], body: dict | None = None) -> dict[str, Any]:
    """Call the Razorpay REST API with basic auth. Raises ValueError for the user."""
    auth = base64.b64encode(f"{cfg['key_id']}:{cfg['key_secret']}".encode()).decode()
    req = urllib.request.Request(
        RZP_API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error", {}).get("description")
        except Exception:
            msg = None
        raise ValueError(f"Razorpay: {msg or exc.code}") from exc
    except Exception as exc:
        raise ValueError("Could not reach Razorpay. Try again.") from exc


# ── Orders ───────────────────────────────────────────────────────────────────

def _company(conn: sqlite3.Connection, workspace_id: str) -> tuple[int | None, int | None]:
    """(company_id, owner_user_id) for a workspace, or (None, None)."""
    try:
        row = conn.execute(
            """SELECT c.id, c.owner_user_id FROM workspace_registry w
               JOIN companies c ON c.id = w.company_id WHERE w.workspace_id = ?""",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    return (int(row[0]), row[1]) if row else (None, None)


def _order(row) -> dict[str, Any]:
    return {
        "token": row["token"], "product_code": row["product_code"], "product_name": row["product_name"],
        "kind": row["kind"], "quantity": row["quantity"], "amount": round(row["amount_paise"] / 100, 2),
        "status": row["status"], "created_at": row["created_at"], "paid_at": row["paid_at"],
        "result": json.loads(row["result"]) if row["result"] else None,
    }


def create_order(db, user: dict[str, Any], product_code: str, quantity: int = 1) -> dict[str, Any]:
    from app.services import company_invites as ci
    from app.services import subscriptions as subs

    db_path = db.db_path
    try:
        quantity = int(quantity or 1)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a number") from None
    if not 1 <= quantity <= MAX_QTY:
        raise ValueError(f"Quantity must be 1–{MAX_QTY}")
    with _connect(db_path) as conn:
        cfg = _cfg(conn)
        if not (cfg.get("key_id") and cfg.get("key_secret")):
            raise ValueError("Online payment is not set up yet. Contact NEXORA.")
        prod = conn.execute(
            "SELECT * FROM billing_products WHERE code = ? AND active = 1", (str(product_code or "").lower(),)
        ).fetchone()
        if prod is None:
            raise ValueError("This plan is not available")
        workspace_id = str(user.get("workspace_id") or "default")
        company_id, _owner = _company(conn, workspace_id)
    if prod["kind"] == "seats":
        if not ci.can_manage_invites(db, user):
            raise ValueError("Only your company's owner or admin can buy team places")
        if not subs.plan_active(db_path, user):
            raise ValueError("Buy a plan first, then add team members")
        if ci.team_limit(db, workspace_id) is None:
            raise ValueError("Your company already has no limit on team members")
    amount = int(prod["price_paise"]) * quantity
    token = secrets.token_urlsafe(18)
    rzp = _rzp("POST", "/orders", cfg, {
        "amount": amount, "currency": "INR", "receipt": token[:40],
        "notes": {"nexora_token": token, "product": prod["code"], "user_id": str(user["user_id"])},
    })
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO billing_orders (token, rzp_order_id, user_id, username, workspace_id, company_id,
                 product_code, product_name, kind, quantity, days, seats, amount_paise, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)""",
            (token, rzp["id"], int(user["user_id"]), user.get("username"), workspace_id, company_id,
             prod["code"], prod["name"], prod["kind"], quantity, prod["days"], prod["seats"],
             amount, _now().isoformat()),
        )
        conn.commit()
    return {"token": token, "pay_path": f"/pay/{token}", "amount": round(amount / 100, 2),
            "product_name": prod["name"], "quantity": quantity}


def order_for_page(db_path, token: str) -> dict[str, Any] | None:
    """What /pay/<token> needs: public key, Razorpay order id, amount, labels."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_orders WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        cfg = _cfg(conn)
        user = conn.execute("SELECT username, email, full_name, phone FROM users WHERE id = ?",
                            (row["user_id"],)).fetchone()
    cols = user.keys() if user else []
    return {
        **_order(row), "rzp_order_id": row["rzp_order_id"], "key_id": cfg.get("key_id", ""),
        "amount_paise": row["amount_paise"],
        "prefill": {
            "name": (user["full_name"] if user and "full_name" in cols and user["full_name"] else
                     (user["username"] if user else "")),
            "email": user["email"] if user and "email" in cols and user["email"] else "",
            "contact": user["phone"] if user and "phone" in cols and user["phone"] else "",
        },
    }


def get_order(db_path, token: str, user_id: int) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_orders WHERE token = ? AND user_id = ?",
                           (token, int(user_id))).fetchone()
    return _order(row) if row else None


def list_my_orders(db_path, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM billing_orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                            (int(user_id), int(limit))).fetchall()
    return [_order(r) for r in rows]


def _sig(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extend_plan(conn: sqlite3.Connection, workspace_id: str | None, user_id: int, days: int):
    """Add ``days`` to the company owner's plan (or this login's when no owner)."""
    from app.services import subscriptions as subs

    now = _now()
    _cid, owner = _company(conn, workspace_id or "default")
    target = int(owner) if owner else int(user_id)
    u = conn.execute("SELECT trial_ends_at, paid_until FROM users WHERE id = ?", (target,)).fetchone()
    current = subs.access_ends_at(u["trial_ends_at"], u["paid_until"]) if u else None
    start = current if current is not None and current > now else now
    new_end = start + timedelta(days=int(days))
    conn.execute("UPDATE users SET paid_until = ? WHERE id = ?", (new_end.isoformat(), target))
    return target, new_end


def _fulfil(db, conn: sqlite3.Connection, row, payment_id: str) -> dict[str, Any]:
    """Apply the purchase once. Caller holds the transaction."""
    from app.services import subscriptions as subs

    now = _now()
    claimed = conn.execute(
        """UPDATE billing_orders SET status = 'paid', payment_id = ?, paid_at = ?, fulfilled_at = ?
           WHERE id = ? AND fulfilled_at IS NULL""",
        (payment_id, now.isoformat(), now.isoformat(), row["id"]),
    ).rowcount
    if claimed != 1:
        fresh = conn.execute("SELECT result FROM billing_orders WHERE id = ?", (row["id"],)).fetchone()
        return json.loads(fresh["result"]) if fresh and fresh["result"] else {"already": True}

    if row["kind"] == "plan":
        target, new_end = _extend_plan(conn, row["workspace_id"], row["user_id"],
                                       int(row["days"]) * int(row["quantity"]))
        result = {"kind": "plan", "paid_until": new_end.isoformat(), "for_user_id": target}
    else:
        add = int(row["seats"]) * int(row["quantity"])
        current = conn.execute("SELECT max_members FROM companies WHERE id = ?", (row["company_id"],)).fetchone()
        base = int(current["max_members"]) if current and current["max_members"] else 1
        new_limit = base + add
        conn.execute("UPDATE companies SET max_members = ? WHERE id = ?", (new_limit, row["company_id"]))
        result = {"kind": "seats", "team_limit": new_limit, "added": add}
    conn.execute("UPDATE billing_orders SET result = ? WHERE id = ?", (json.dumps(result), row["id"]))
    subs.clear_cache()  # the owner and every member see the new plan at once
    return result


def _ensure_captured(cfg: dict[str, str], payment_id: str, amount_paise: int) -> None:
    """Checkout may leave a payment 'authorized'; capture it so money really moves."""
    pay = _rzp("GET", f"/payments/{payment_id}", cfg)
    if int(pay.get("amount") or 0) != int(amount_paise):
        raise ValueError("Paid amount does not match the order")
    status = pay.get("status")
    if status == "authorized":
        _rzp("POST", f"/payments/{payment_id}/capture", cfg, {"amount": int(amount_paise), "currency": "INR"})
    elif status != "captured":
        raise ValueError(f"Payment is {status or 'not complete'}")


def verify_checkout(db, token: str, order_id: str, payment_id: str, signature: str) -> dict[str, Any]:
    with _connect(db.db_path) as conn:
        cfg = _cfg(conn)
        row = conn.execute("SELECT * FROM billing_orders WHERE token = ?", (token,)).fetchone()
    if row is None or row["rzp_order_id"] != order_id:
        raise ValueError("Order not found")
    expected = _sig(cfg.get("key_secret", ""), f"{order_id}|{payment_id}")
    if not signature or not hmac.compare_digest(expected, signature):
        raise ValueError("Payment could not be verified")
    _ensure_captured(cfg, payment_id, row["amount_paise"])
    with _connect(db.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = _fulfil(db, conn, row, payment_id)
        conn.commit()
    return {**result, "status": "paid"}


def handle_webhook(db, raw_body: bytes, signature: str) -> dict[str, Any]:
    """Razorpay webhook: order.paid / payment.captured fulfil the order (if the page was closed)."""
    with _connect(db.db_path) as conn:
        cfg = _cfg(conn)
    secret = cfg.get("webhook_secret", "")
    if not secret:
        raise PermissionError("Webhook secret not set")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PermissionError("Bad signature")
    event = json.loads(raw_body.decode() or "{}")
    name = event.get("event")
    payload = event.get("payload") or {}
    if str(name or "").startswith("subscription."):
        return _subscription_event(db, name, payload)
    payment = (payload.get("payment") or {}).get("entity") or {}
    order = (payload.get("order") or {}).get("entity") or {}
    order_id = order.get("id") or payment.get("order_id")
    if name not in ("order.paid", "payment.captured") or not order_id:
        return {"ignored": name}
    amount = int(payment.get("amount") or order.get("amount_paid") or 0)
    with _connect(db.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM billing_orders WHERE rzp_order_id = ?", (order_id,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ignored": "unknown order"}
        if amount and amount != int(row["amount_paise"]):
            conn.rollback()
            raise ValueError("Amount does not match the order")
        result = _fulfil(db, conn, row, payment.get("id") or "")
        conn.commit()
    return result


# ── Super admin overview ─────────────────────────────────────────────────────

def sales_summary(db_path, limit: int = 50) -> dict[str, Any]:
    month = _now().strftime("%Y-%m")
    with _connect(db_path) as conn:
        tot = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_paise), 0) FROM billing_orders WHERE status = 'paid'"
        ).fetchone()
        mon = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_paise), 0) FROM billing_orders "
            "WHERE status = 'paid' AND substr(paid_at, 1, 7) = ?", (month,)
        ).fetchone()
        rows = conn.execute("SELECT * FROM billing_orders ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return {
        "paid_orders": tot[0], "revenue": round(tot[1] / 100, 2),
        "month": month, "month_orders": mon[0], "month_revenue": round(mon[1] / 100, 2),
        "orders": [{**_order(r), "username": r["username"]} for r in rows],
    }


# ── Auto-renewal (Razorpay Subscriptions) ────────────────────────────────────

_SUB_STATUS = {
    "subscription.authenticated": "authenticated", "subscription.activated": "active",
    "subscription.charged": "active", "subscription.pending": "pending",
    "subscription.halted": "halted", "subscription.paused": "paused",
    "subscription.resumed": "active", "subscription.cancelled": "cancelled",
    "subscription.completed": "completed",
}
_LIVE = ("created", "authenticated", "active", "pending")


def _sub(row) -> dict[str, Any]:
    return {
        "token": row["token"], "product_code": row["product_code"], "product_name": row["product_name"],
        "days": row["days"], "amount": round(row["amount_paise"] / 100, 2), "period": row["period"],
        "status": row["status"], "cancel_at_end": bool(row["cancel_at_end"]), "charges": row["charges"],
        "last_charged_at": row["last_charged_at"], "created_at": row["created_at"],
        "username": row["username"],
    }


def _rzp_plan_id(conn: sqlite3.Connection, cfg: dict[str, str], prod) -> str:
    if prod["rzp_plan_id"]:
        return prod["rzp_plan_id"]
    period, interval, _label = renewal_period(prod["days"])
    plan = _rzp("POST", "/plans", cfg, {
        "period": period, "interval": interval,
        "item": {"name": f"NEXORA {prod['name']}"[:80], "amount": int(prod["price_paise"]), "currency": "INR"},
        "notes": {"product": prod["code"]},
    })
    conn.execute("UPDATE billing_products SET rzp_plan_id = ? WHERE code = ?", (plan["id"], prod["code"]))
    conn.commit()
    return plan["id"]


def create_subscription(db, user: dict[str, Any], product_code: str) -> dict[str, Any]:
    db_path = db.db_path
    workspace_id = str(user.get("workspace_id") or "default")
    with _connect(db_path) as conn:
        cfg = _cfg(conn)
        if not (cfg.get("key_id") and cfg.get("key_secret")):
            raise ValueError("Online payment is not set up yet. Contact NEXORA.")
        prod = conn.execute(
            "SELECT * FROM billing_products WHERE code = ? AND active = 1 AND kind = 'plan' AND renewable = 1",
            (str(product_code or "").lower(),),
        ).fetchone()
        if prod is None:
            raise ValueError("Auto-renew is not available for this plan")
        company_id, _owner = _company(conn, workspace_id)
        clash = conn.execute(
            f"""SELECT 1 FROM billing_subscriptions
                WHERE status IN ({','.join('?' * len(_LIVE))}) AND cancel_at_end = 0
                  AND (user_id = ? OR (company_id IS NOT NULL AND company_id = ?))
                  AND rzp_subscription_id IS NOT NULL AND charges > 0""",
            (*_LIVE, int(user["user_id"]), company_id),
        ).fetchone()
        if clash:
            raise ValueError("Auto-renew is already on. Stop it first to change the plan.")
        plan_id = _rzp_plan_id(conn, cfg, prod)
    period, interval, label = renewal_period(prod["days"])
    # About ten years of renewals; the buyer can stop at any time.
    total = max(1, {"monthly": 120 // interval, "yearly": 10, "weekly": 520 // interval}.get(period, 3650 // max(1, interval)))
    token = secrets.token_urlsafe(18)
    rzp = _rzp("POST", "/subscriptions", cfg, {
        "plan_id": plan_id, "total_count": min(total, 1000), "customer_notify": 1,
        "notes": {"nexora_token": token, "product": prod["code"], "user_id": str(user["user_id"])},
    })
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO billing_subscriptions (token, rzp_subscription_id, user_id, username, workspace_id,
                 company_id, product_code, product_name, days, amount_paise, period, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)""",
            (token, rzp["id"], int(user["user_id"]), user.get("username"), workspace_id, company_id,
             prod["code"], prod["name"], prod["days"], prod["price_paise"], label,
             _now().isoformat(), _now().isoformat()),
        )
        conn.commit()
    return {"token": token, "pay_path": f"/pay/s/{token}", "amount": round(prod["price_paise"] / 100, 2),
            "period": label, "product_name": prod["name"]}


def subscription_for_page(db_path, token: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_subscriptions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        cfg = _cfg(conn)
        user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    cols = user.keys() if user else []

    def col(name):
        return user[name] if user and name in cols and user[name] else ""

    return {
        **_sub(row), "subscription_id": row["rzp_subscription_id"], "key_id": cfg.get("key_id", ""),
        "prefill": {"name": col("full_name") or col("username"), "email": col("email"), "contact": col("phone")},
    }


def _record_charge(conn: sqlite3.Connection, srow, payment_id: str, amount_paise: int | None) -> dict[str, Any]:
    """One renewal charge -> add the plan's days once (per payment id) and log it as a sale."""
    from app.services import subscriptions as subs

    if amount_paise and int(amount_paise) != int(srow["amount_paise"]):
        raise ValueError("Charged amount does not match the plan")
    now = _now().isoformat()
    new = conn.execute(
        "INSERT OR IGNORE INTO billing_payments (payment_id, source, ref, amount_paise, created_at) VALUES (?, 'subscription', ?, ?, ?)",
        (payment_id, srow["token"], srow["amount_paise"], now),
    ).rowcount
    if new != 1:
        return {"already": True}
    target, new_end = _extend_plan(conn, srow["workspace_id"], srow["user_id"], int(srow["days"]))
    result = {"kind": "plan", "paid_until": new_end.isoformat(), "for_user_id": target, "renewal": True}
    conn.execute(
        """INSERT INTO billing_orders (token, rzp_order_id, user_id, username, workspace_id, company_id,
             product_code, product_name, kind, quantity, days, seats, amount_paise, status, payment_id,
             result, created_at, paid_at, fulfilled_at)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'plan', 1, ?, 0, ?, 'paid', ?, ?, ?, ?, ?)""",
        (secrets.token_urlsafe(18), srow["user_id"], srow["username"], srow["workspace_id"], srow["company_id"],
         srow["product_code"], f"{srow['product_name']} (auto-renew)", srow["days"], srow["amount_paise"],
         payment_id, json.dumps(result), now, now, now),
    )
    conn.execute(
        """UPDATE billing_subscriptions SET status = 'active', charges = charges + 1,
             last_charged_at = ?, updated_at = ? WHERE id = ?""",
        (now, now, srow["id"]),
    )
    subs.clear_cache()
    return result


def verify_subscription(db, token: str, subscription_id: str, payment_id: str, signature: str) -> dict[str, Any]:
    with _connect(db.db_path) as conn:
        cfg = _cfg(conn)
        row = conn.execute("SELECT * FROM billing_subscriptions WHERE token = ?", (token,)).fetchone()
    if row is None or row["rzp_subscription_id"] != subscription_id:
        raise ValueError("Subscription not found")
    expected = _sig(cfg.get("key_secret", ""), f"{payment_id}|{subscription_id}")
    if not signature or not hmac.compare_digest(expected, signature):
        raise ValueError("Payment could not be verified")
    pay = _rzp("GET", f"/payments/{payment_id}", cfg)
    if pay.get("status") not in ("captured", "authorized"):
        raise ValueError(f"Payment is {pay.get('status') or 'not complete'}")
    with _connect(db.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = _record_charge(conn, row, payment_id, int(pay.get("amount") or 0) or None)
        conn.commit()
    return {**result, "status": "active"}


def _subscription_event(db, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    ent = (payload.get("subscription") or {}).get("entity") or {}
    sid = ent.get("id")
    if not sid:
        return {"ignored": name}
    with _connect(db.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM billing_subscriptions WHERE rzp_subscription_id = ?", (sid,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ignored": "unknown subscription"}
        result: dict[str, Any] = {"subscription": row["token"]}
        if name == "subscription.charged":
            pay = (payload.get("payment") or {}).get("entity") or {}
            if pay.get("id"):
                try:
                    result.update(_record_charge(conn, row, pay["id"], int(pay.get("amount") or 0) or None))
                except ValueError:
                    conn.rollback()
                    raise
        status = _SUB_STATUS.get(name)
        if status and name != "subscription.charged":
            conn.execute("UPDATE billing_subscriptions SET status = ?, updated_at = ? WHERE id = ?",
                         (status, _now().isoformat(), row["id"]))
        conn.commit()
    return {**result, "event": name}


def list_subscriptions(db, user: dict[str, Any]) -> list[dict[str, Any]]:
    """This login's auto-renewals, and the company's when the login manages the company."""
    from app.services import company_invites as ci

    workspace_id = str(user.get("workspace_id") or "default")
    with _connect(db.db_path) as conn:
        company_id, _owner = _company(conn, workspace_id)
        if company_id is not None and ci.can_manage_invites(db, user):
            rows = conn.execute(
                "SELECT * FROM billing_subscriptions WHERE (company_id = ? OR user_id = ?) AND charges > 0 ORDER BY id DESC",
                (company_id, int(user["user_id"])),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM billing_subscriptions WHERE user_id = ? AND charges > 0 ORDER BY id DESC",
                (int(user["user_id"]),),
            ).fetchall()
    return [_sub(r) for r in rows]


def cancel_subscription(db, user: dict[str, Any], token: str) -> dict[str, Any]:
    """Stop auto-renew at the end of the paid cycle (the time already paid stays)."""
    from app.services import company_invites as ci

    with _connect(db.db_path) as conn:
        cfg = _cfg(conn)
        row = conn.execute("SELECT * FROM billing_subscriptions WHERE token = ?", (token,)).fetchone()
    if row is None:
        raise ValueError("Auto-renew not found")
    own = int(row["user_id"]) == int(user["user_id"])
    same_company_admin = (row["workspace_id"] == user.get("workspace_id") and ci.can_manage_invites(db, user))
    if not (own or same_company_admin):
        raise PermissionError("You cannot stop this auto-renew")
    if row["status"] in ("cancelled", "completed"):
        return _sub(row)
    _rzp("POST", f"/subscriptions/{row['rzp_subscription_id']}/cancel", cfg, {"cancel_at_cycle_end": 1})
    with _connect(db.db_path) as conn:
        conn.execute("UPDATE billing_subscriptions SET cancel_at_end = 1, updated_at = ? WHERE id = ?",
                     (_now().isoformat(), row["id"]))
        conn.commit()
        fresh = conn.execute("SELECT * FROM billing_subscriptions WHERE id = ?", (row["id"],)).fetchone()
    return _sub(fresh)


def admin_subscriptions(db_path, limit: int = 100) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM billing_subscriptions WHERE charges > 0 ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [_sub(r) for r in rows]
