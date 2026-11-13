"""Ask Nexora reads the whole app through the app's own GET routes.

Instead of one hand-written tool per module, the AI gets `read_app(path, query)`:
it may call any route on the allow-list below, exactly as the phone or web
would, AS THE USER WHO ASKED (their own login is reused), so it sees only what
that user sees in the app. Replies are trimmed so a big list cannot flood the
model. Adding a module = adding one line here.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

# Allow-list: route template -> what it holds (shown to the AI).
APP_READ_ROUTES: dict[str, str] = {
    # My Day / home
    "/api/v1/executive/home": "My Day home: today's summary, target vs achievement, alerts",
    "/api/v1/dashboard/summary": "Dashboard summary numbers",
    "/api/v1/operations/alerts": "Data-entry / operations alerts",
    "/api/v1/me/profile": "The asking user's own profile",
    # Customers
    "/api/v1/masters/distributors": "All distributors: firm, contact, phone, GST, location, credit",
    "/api/v1/masters/distributors/{id}": "One distributor by id",
    "/api/v1/masters/retailers": "All retailers: name, owner, phone, location, GST",
    "/api/v1/masters/retailers/{id}": "One retailer by id",
    "/api/v1/executive/party/{party_type}/{id}": "Party profile with visits (party_type = distributor or retailer)",
    "/api/v1/search": "Search everything (query q)",
    # Article Master
    "/api/v1/article-master/list": "Article Master: brand, size, product, MRP, PTR, ex-mill",
    "/api/v1/article-master/{id}": "One article",
    "/api/v1/article-master/{id}/price-history": "Price changes of one article",
    "/api/v1/article-master/{id}/price-seasons": "Prices of one article by season",
    # Order Desk
    "/api/v1/filled-orders/list": "Filled Orders (distributor order sheets) list",
    "/api/v1/filled-orders/season-overview": "Filled Orders by season and category",
    "/api/v1/filled-orders/{id}": "One Filled Order with lines",
    "/api/v1/order-fulfillment/order-match/list": "FO-SO match runs (one per distributor/season/category, with so_count)",
    "/api/v1/order-fulfillment/order-match/{id}": "One match run: article rows, SO numbers, qty, value, status",
    "/api/v1/order-fulfillment/pending-so": "Sales Orders not yet billed by a CI",
    "/api/v1/order-fulfillment/order-cycle": "SO/CI records (order lifecycle) list",
    "/api/v1/order-fulfillment/tracking/{id}": "One SO/CI record: CI header, totals, lines, SO match",
    "/api/v1/order-fulfillment/completeness/{id}": "Order completeness for one distributor id",
    "/api/v1/order-sheets": "Order sheets",
    # Distributor Zone / money
    "/api/v1/distributor-payments/status": "Payment status per distributor/season/category: order, deposits, CD, TDS, due",
    "/api/v1/distributor-ledger": "Distributor Hisaab: received vs billed vs to collect, CIs paid/unpaid (query distributor_id)",
    "/api/v1/order-fulfillment/payment-collection": "SO-wise payment entries per distributor",
    "/api/v1/distributor-secondary-sales/status": "Distributor secondary sales",
    "/api/v1/executive/distributor-zone": "Distributor Zone overview",
    "/api/v1/distributor-grievances": "Distributor grievances / complaints",
    "/api/v1/distributor-grievances/{id}": "One grievance",
    # Mail
    "/api/v1/so-mail/imports": "Bombay Dyeing SO payment mails and what NEXORA did with them",
    "/api/v1/so-mail/summary": "SO mails waiting for review",
    "/api/v1/mail-sync/status": "Gmail CI import connection and last run",
    "/api/v1/mail-sync/pending": "CIs from mail waiting for review",
    "/api/v1/mail-sync/log": "CI mail import log",
    # Target vs Achievement
    "/api/v1/target-achievement/summary": "Target vs achievement summary",
    "/api/v1/target-achievement/fy-overview": "Financial-year overview",
    "/api/v1/target-achievement/monthly": "Month-wise target vs achievement",
    "/api/v1/target-achievement/years": "Financial years on file (ids)",
    "/api/v1/target-achievement/years/{id}/breakup": "Distributor-wise target/achievement for a year id",
    "/api/v1/target-achievement/years/{id}/category-breakup": "Category-wise target/achievement for a year id",
    "/api/v1/target-achievement/years/{id}/distributor-targets": "Distributor targets for a year id",
    # Market Visit / PJP / calls / to-do
    "/api/v1/dsr-market/visits": "Market visit (DSR) reports",
    "/api/v1/dsr-market/approach-distributors": "New distributors being approached",
    "/api/v1/dsr-market/drafts": "Unsent DSR drafts",
    "/api/v1/executive/visits": "Visit log",
    "/api/v1/pjp/today": "Today's journey plan",
    "/api/v1/pjp/week": "This week's journey plan",
    "/api/v1/pjp/months": "Months with a journey plan",
    "/api/v1/pjp/months/{year_month}": "Journey plan for a month (YYYY-MM)",
    "/api/v1/call-lists": "Call list batches",
    "/api/v1/call-lists/{id}/rows": "Rows of one call list batch",
    "/api/v1/call-lists/daily-retailers": "Today's retailers to call",
    "/api/v1/call-lists/deals": "Deals from calls",
    "/api/v1/personal-todos": "To-Do tasks",
    "/api/v1/personal-todos/summary": "To-Do counts",
    "/api/v1/personal-todos/due-reminders": "To-Dos due now",
    # Files / reports / AI
    "/api/v1/storage/files": "Catalogues and files in the NEXORA Drive folder",
    "/api/v1/storage/search": "Search Drive files by name",
    "/api/v1/reports/sales": "Sales report",
    "/api/v1/reports/territory-performance": "Territory performance report",
    "/api/v1/reports/trends": "Sales trends",
    "/api/v1/reports/distributor-sales/{id}": "Sales of one distributor id",
    "/api/v1/workspace/users": "Users in this workspace (owner only)",
    "/api/v1/ai/usage-summary": "AI usage and cost",
}

_MAX_LIST = 40
_MAX_STR = 400
_MAX_CHARS = 40000


_PLACEHOLDER_RX = {
    "{id}": r"\d+",
    "{year_month}": r"\d{4}-\d{2}",
    "{party_type}": r"(?:distributor|retailer)",
}


def _template_regex(template: str) -> re.Pattern:
    # Each placeholder accepts only its own shape: ids are numbers, so a word
    # like "export" can never stand in for {id} and reach another route.
    parts = re.split(r"(\{[a-z_]+\})", template)
    return re.compile("^" + "".join(
        _PLACEHOLDER_RX.get(p, r"\d+") if p.startswith("{") else re.escape(p) for p in parts
    ) + "$")


_COMPILED = [(t, _template_regex(t)) for t in APP_READ_ROUTES]


def allowed(path: str) -> str | None:
    """The allow-list template this concrete path matches, or None."""
    clean = (path or "").split("?", 1)[0].strip()
    if not clean.startswith("/api/") or ".." in clean or "//" in clean:
        return None
    for template, rx in _COMPILED:
        if rx.match(clean):
            return template
    return None


_PARAM_CACHE: dict[str, list[str]] = {}


def _query_params(app, template: str) -> list[str]:
    """Query parameter names the route's code reads (request.args.get(...))."""
    if template in _PARAM_CACHE:
        return _PARAM_CACHE[template]
    names: list[str] = []
    probe = re.sub(r"\{year_month\}", "2026-01", re.sub(r"\{party_type\}", "distributor", template))
    probe = re.sub(r"\{[a-z_]+\}", "1", probe)
    try:
        adapter = app.url_map.bind("localhost")
        endpoint, _ = adapter.match(probe, method="GET")
        src = inspect.getsource(app.view_functions[endpoint])
        names = sorted(set(re.findall(r"request\.args\.get\(\s*[\"']([a-z_]+)[\"']", src)))
    except Exception:
        names = []
    _PARAM_CACHE[template] = names
    return names


def catalog_text(app=None) -> str:
    """One line per route for the tool description."""
    lines = []
    for template, desc in APP_READ_ROUTES.items():
        params = _query_params(app, template) if app is not None else []
        hint = f" [query: {', '.join(params)}]" if params else ""
        lines.append(f"{template} - {desc}{hint}")
    return "\n".join(lines)


def trim(value: Any, depth: int = 0) -> Any:
    """Keep replies small: first 40 items of a list, 400 chars of a string."""
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        return {k: trim(v, depth + 1) for k, v in value.items()
                if not (isinstance(v, str) and len(v) > 4000)}  # raw PDF text etc.
    if isinstance(value, list):
        out = [trim(v, depth + 1) for v in value[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            out.append(f"… {len(value) - _MAX_LIST} more not shown (narrow with query/filters)")
        return out
    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[:_MAX_STR] + "…"
    return value


def read_app(path: str, query: dict | None, *, user_id: int, workspace_id: str) -> dict[str, Any]:
    """GET an allow-listed app route as the asking user; JSON back, trimmed."""
    import json

    from flask import current_app, has_request_context, request

    if not allowed(path):
        return {"error": f"'{path}' is not a readable NEXORA screen. Use one of the listed paths."}
    query = {str(k): str(v) for k, v in (query or {}).items() if v not in (None, "")}
    headers: dict[str, str] = {}
    who: dict[str, Any] = {"user_id": int(user_id), "workspace_id": workspace_id}
    if has_request_context():
        # Reuse the asker's own login so the route applies its usual isolation:
        # the phone's token as-is; for a browser session, the same identity the
        # outer request was authenticated as (forwarding the raw cookie to the
        # inner client did not carry the session).
        if request.headers.get("Authorization"):
            headers["Authorization"] = request.headers["Authorization"]
        user = getattr(request, "user", None)
        if isinstance(user, dict) and user.get("user_id") is not None:
            who = {
                "user_id": int(user["user_id"]),
                "workspace_id": user.get("workspace_id") or workspace_id,
                "role": user.get("role") or "unassigned",
                "username": user.get("username"),
                "is_workspace_owner": bool(user.get("is_workspace_owner")),
            }
    with current_app.test_client() as client:
        if not headers:
            with client.session_transaction() as sess:
                sess.update(authenticated=True, **{k: v for k, v in who.items() if v is not None})
        resp = client.get(path.split("?", 1)[0], query_string=query, headers=headers)
    data = resp.get_json(silent=True)
    if data is None:
        return {"error": f"{path} returned no JSON (status {resp.status_code})"}
    if resp.status_code >= 400:
        return {"error": f"{path} answered {resp.status_code}", "detail": trim(data)}
    out = trim(data)
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) > _MAX_CHARS:
        return {"note": "Reply cut to fit; ask for a narrower query.", "partial_json": text[:_MAX_CHARS]}
    return out if isinstance(out, dict) else {"data": out}
