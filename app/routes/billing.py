"""Billing API (Razorpay): buy a plan or team seats; super admin keys, prices, sales.

/api/v1/billing/*  — JSON API (JWT, except verify + webhook which Razorpay signs)
/pay/<token>       — hosted Razorpay Checkout page the web and phone app open
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template_string, request

from app.routes.auth import _get_auth_db, require_jwt_auth
from app.services import billing
from app.services import subscriptions as subs

billing_bp = Blueprint("billing", __name__)


def _err(message: str, status: int = 400, code: str = "BAD_REQUEST"):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _user() -> dict:
    return getattr(request, "user", None) or {}


def _admin_only():
    if not subs.is_super_admin(_user()):
        return _err("Only the NEXORA super admin can do this", 403, "FORBIDDEN")
    return None


# ── Buyer ────────────────────────────────────────────────────────────────────

@billing_bp.route("/api/v1/billing/products", methods=["GET"])
@require_jwt_auth
def products():
    db = _get_auth_db()
    st = billing.config_status(db.db_path)
    return jsonify({"success": True, "data": {
        "enabled": st["configured"], "mode": st["mode"],
        "products": billing.list_products(db.db_path),
    }}), 200


@billing_bp.route("/api/v1/billing/orders", methods=["POST"])
@require_jwt_auth
def create_order():
    body = request.get_json(silent=True) or {}
    try:
        out = billing.create_order(_get_auth_db(), _user(), body.get("product_code"), body.get("quantity") or 1)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 201


@billing_bp.route("/api/v1/billing/orders", methods=["GET"])
@require_jwt_auth
def my_orders():
    uid = _user().get("user_id")
    if uid is None:
        return _err("Sign in first", 401, "UNAUTHORIZED")
    return jsonify({"success": True, "data": {
        "orders": billing.list_my_orders(_get_auth_db().db_path, int(uid))}}), 200


@billing_bp.route("/api/v1/billing/orders/<token>", methods=["GET"])
@require_jwt_auth
def my_order(token: str):
    uid = _user().get("user_id")
    order = billing.get_order(_get_auth_db().db_path, token, int(uid)) if uid is not None else None
    if order is None:
        return _err("Order not found", 404, "NOT_FOUND")
    return jsonify({"success": True, "data": order}), 200


@billing_bp.route("/api/v1/billing/verify", methods=["POST"])
def verify():
    """Called by the pay page after Checkout. The Razorpay signature is the proof."""
    b = request.get_json(silent=True) or {}
    try:
        out = billing.verify_checkout(
            _get_auth_db(), str(b.get("token") or ""), str(b.get("razorpay_order_id") or ""),
            str(b.get("razorpay_payment_id") or ""), str(b.get("razorpay_signature") or ""),
        )
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


@billing_bp.route("/api/v1/billing/webhook", methods=["POST"])
def webhook():
    try:
        out = billing.handle_webhook(_get_auth_db(), request.get_data() or b"",
                                     request.headers.get("X-Razorpay-Signature", ""))
    except PermissionError as exc:
        return _err(str(exc), 401, "UNAUTHORIZED")
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


# ── Auto-renewal ─────────────────────────────────────────────────────────────

@billing_bp.route("/api/v1/billing/subscriptions", methods=["POST"])
@require_jwt_auth
def create_subscription():
    body = request.get_json(silent=True) or {}
    try:
        out = billing.create_subscription(_get_auth_db(), _user(), body.get("product_code"))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 201


@billing_bp.route("/api/v1/billing/subscriptions", methods=["GET"])
@require_jwt_auth
def my_subscriptions():
    if _user().get("user_id") is None:
        return _err("Sign in first", 401, "UNAUTHORIZED")
    return jsonify({"success": True, "data": {
        "subscriptions": billing.list_subscriptions(_get_auth_db(), _user())}}), 200


@billing_bp.route("/api/v1/billing/subscriptions/<token>/cancel", methods=["POST"])
@require_jwt_auth
def cancel_subscription(token: str):
    try:
        out = billing.cancel_subscription(_get_auth_db(), _user(), token)
    except PermissionError as exc:
        return _err(str(exc), 403, "FORBIDDEN")
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


@billing_bp.route("/api/v1/billing/verify-subscription", methods=["POST"])
def verify_subscription():
    b = request.get_json(silent=True) or {}
    try:
        out = billing.verify_subscription(
            _get_auth_db(), str(b.get("token") or ""), str(b.get("razorpay_subscription_id") or ""),
            str(b.get("razorpay_payment_id") or ""), str(b.get("razorpay_signature") or ""),
        )
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


# ── Super admin ──────────────────────────────────────────────────────────────

@billing_bp.route("/api/v1/billing/config", methods=["GET"])
@require_jwt_auth
def get_config():
    denied = _admin_only()
    if denied:
        return denied
    return jsonify({"success": True, "data": billing.config_status(_get_auth_db().db_path)}), 200


@billing_bp.route("/api/v1/billing/config", methods=["POST"])
@require_jwt_auth
def set_config():
    denied = _admin_only()
    if denied:
        return denied
    b = request.get_json(silent=True) or {}
    try:
        out = billing.save_config(_get_auth_db().db_path, b.get("key_id"), b.get("key_secret"), b.get("webhook_secret"))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


@billing_bp.route("/api/v1/billing/admin/products", methods=["GET"])
@require_jwt_auth
def admin_products():
    denied = _admin_only()
    if denied:
        return denied
    return jsonify({"success": True, "data": {
        "products": billing.list_products(_get_auth_db().db_path, active_only=False)}}), 200


@billing_bp.route("/api/v1/billing/admin/products", methods=["POST"])
@require_jwt_auth
def admin_save_product():
    denied = _admin_only()
    if denied:
        return denied
    try:
        out = billing.save_product(_get_auth_db().db_path, request.get_json(silent=True) or {})
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": out}), 200


@billing_bp.route("/api/v1/billing/admin/sales", methods=["GET"])
@require_jwt_auth
def admin_sales():
    denied = _admin_only()
    if denied:
        return denied
    db_path = _get_auth_db().db_path
    return jsonify({"success": True, "data": {
        **billing.sales_summary(db_path), "subscriptions": billing.admin_subscriptions(db_path)}}), 200


# ── Hosted Checkout page ─────────────────────────────────────────────────────

_PAY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXORA · Payment</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:#0f172a; color:#e2e8f0; display:flex; min-height:100vh; align-items:center; justify-content:center; }
  .card { width:min(420px, calc(100% - 32px)); background:#111c33; border:1px solid #1e2b47; border-radius:20px; padding:28px; }
  h1 { font-size:20px; margin:0 0 4px; } .muted { color:#94a3b8; font-size:14px; }
  .amt { font-size:32px; font-weight:700; margin:18px 0 6px; }
  button { width:100%; padding:14px; border:0; border-radius:12px; background:#6d28d9; color:#fff; font-size:16px; font-weight:600; cursor:pointer; margin-top:18px; }
  .ok { color:#4ade80; } .bad { color:#f87171; } .msg { margin-top:16px; font-size:15px; min-height:22px; }
  .test { display:inline-block; margin-top:10px; padding:3px 10px; border-radius:999px; background:#78350f; color:#fde68a; font-size:12px; }
</style></head><body>
<div class="card">
  <h1>NEXORA</h1>
  <div class="muted">{{ o.product_name }}{% if o.quantity and o.quantity > 1 %} × {{ o.quantity }}{% endif %}</div>
  <div class="amt">₹{{ '{:,.2f}'.format(o.amount) }}{% if sub %}<span class="muted" style="font-size:16px;"> / {{ o.period }}</span>{% endif %}</div>
  {% if sub %}<div class="muted">Auto-renews every {{ o.period }} until you stop it. You can stop it any time in NEXORA → My plan.</div>{% endif %}
  {% if test %}<span class="test">Test mode — no real money</span>{% endif %}
  {% if o.status == 'paid' or (sub and o.charges > 0) %}
    <div class="msg ok">✓ Already paid. You can go back to NEXORA.</div>
  {% else %}
    <button id="pay">Pay now</button>
    <div class="msg" id="msg"></div>
  {% endif %}
</div>
{% if o.status != 'paid' %}
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
  const msg = document.getElementById('msg');
  const btn = document.getElementById('pay');
  const options = {
    key: {{ o.key_id|tojson }},
    {% if sub %}subscription_id: {{ o.subscription_id|tojson }},{% else %}order_id: {{ o.rzp_order_id|tojson }}, amount: {{ o.amount_paise|tojson }}, currency: 'INR',{% endif %}
    name: 'NEXORA', description: {{ o.product_name|tojson }},
    prefill: {{ o.prefill|tojson }}, theme: { color: '#6d28d9' },
    handler: async function (r) {
      btn.disabled = true; msg.className = 'msg'; msg.textContent = 'Confirming your payment…';
      try {
        const res = await fetch({{ verify_url|tojson }}, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: {{ token|tojson }}, ...r }) });
        const d = await res.json();
        if (!res.ok || !d.success) throw new Error((d.error && d.error.message) || 'Could not confirm');
        btn.remove(); msg.className = 'msg ok';
        msg.textContent = '✓ Payment received. Your NEXORA plan is updated — go back to the app.';
      } catch (e) {
        btn.disabled = false; msg.className = 'msg bad';
        msg.textContent = e.message + ' If money was taken, it will be added in a few minutes.';
      }
    },
    modal: { ondismiss: function () { if (!msg.textContent) msg.textContent = 'Payment not completed. Tap Pay now to try again.'; } }
  };
  btn.addEventListener('click', function () { new Razorpay(options).open(); });
  window.addEventListener('load', function () { btn.click(); });
</script>
{% endif %}
</body></html>"""


@billing_bp.route("/pay/<token>", methods=["GET"])
def pay_page(token: str):
    order = billing.order_for_page(_get_auth_db().db_path, token)
    if order is None:
        return "Payment link not found", 404
    return render_template_string(_PAY_PAGE, o=order, token=token, sub=False,
                                  verify_url="/api/v1/billing/verify",
                                  test=str(order.get("key_id") or "").startswith("rzp_test_"))


@billing_bp.route("/pay/s/<token>", methods=["GET"])
def pay_subscription_page(token: str):
    sub = billing.subscription_for_page(_get_auth_db().db_path, token)
    if sub is None:
        return "Payment link not found", 404
    return render_template_string(_PAY_PAGE, o=sub, token=token, sub=True,
                                  verify_url="/api/v1/billing/verify-subscription",
                                  test=str(sub.get("key_id") or "").startswith("rzp_test_"))
