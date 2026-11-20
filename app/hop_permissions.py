"""HoP staff permissions: what each staff login may see or change.

* The owner (the company owner flag, or the first HoP login of the company) always has full
  access and is the only one who can change permissions, download backups or wipe data.
* Every other HoP login is "staff". Per area it has ``none`` / ``view`` / ``edit``.
  Staff with nothing saved yet get FULL_STAFF (everything except owner-only screens) — so
  adding this never cuts anyone off; the owner then tightens it.
* Checked on the server for every /api/v1/hop request (phone and web alike); the screens also
  hide menus the login cannot open.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

AREAS: list[tuple[str, str]] = [
    ("sales", "Sales — invoices, estimates, orders, challans, returns, payment-in"),
    ("purchase", "Purchase — bills, payment-out, expenses, other income, purchase orders"),
    ("parties", "Parties — customers and vendors"),
    ("items", "Items, stock and godowns"),
    ("money", "Cash & bank — accounts, cheques, reconciliation, loans, fixed assets, journals"),
    ("reports", "Reports, GST files and books"),
    ("crm", "Leads, deals, projects, samples, dispatches and other HoP screens"),
    ("settings", "Settings, company profile, audit trail, financial year"),
    ("salary", "Salary — staff pay, salary sheet, advances (private: off for staff unless you allow it)"),
]
AREA_KEYS = [a for a, _ in AREAS]
LEVELS = ("none", "view", "edit")
FULL_STAFF = {a: "edit" for a in AREA_KEYS} | {"settings": "view", "salary": "none"}
PRESETS: dict[str, dict[str, str]] = {
    "Full access": FULL_STAFF,
    "Salesperson": {"sales": "edit", "parties": "edit", "items": "view", "crm": "edit", "purchase": "none",
                    "money": "none", "reports": "none", "settings": "none"},
    "Accountant": {"sales": "edit", "purchase": "edit", "parties": "edit", "items": "view", "money": "edit",
                   "reports": "view", "crm": "view", "settings": "view"},
    "Store keeper": {"items": "edit", "sales": "view", "purchase": "view", "parties": "view", "money": "none",
                     "reports": "none", "crm": "none", "settings": "none"},
    "View only": {a: "view" for a in AREA_KEYS} | {"settings": "none", "salary": "none"},
}
SALES_TYPES = {1, 3, 21, 27, 30, 65, 82, 83}
PURCHASE_TYPES = {2, 4, 7, 16, 29, 66}
MONEY_TYPES = {81}
OWNER_ONLY = re.compile(r"^/(backup|backups|staff-permissions|wipe|wipe-data|reset|settings/wipe-data|firms/restore|gov)(/|$)")
# POSTs that only read / prepare (no change) need "view".
READ_POSTS = re.compile(r"/(preview|entry-warnings|suggest-rate|reconcile/match|import/[a-z]+/preview|ai/read-bill|"
                        r"gst-files/[a-z]+|insights)(/|$)")
PREFIX_AREA = [
    ("salary", "salary"),
    ("customers", "parties"), ("vendors", "parties"), ("parties", "parties"), ("import/parties", "parties"),
    ("products", "items"), ("notes", "crm"), ("manufacture", "items"), ("loyalty", "sales"), ("catalog-link", "sales"), ("targets", "reports"), ("sms", "sales"), ("reminders", "sales"), ("stock", "items"), ("godowns", "items"), ("item-", "items"), ("categories", "items"),
    ("units", "items"), ("import/items", "items"),
    ("bank-accounts", "money"), ("bank-entries", "money"), ("cheques", "money"), ("loans", "money"),
    ("fixed-assets", "money"),
    ("expense-categories", "purchase"), ("ai/read-bill", "_docs"),
    ("books", "reports"), ("reports", "reports"), ("gst-files", "reports"), ("tally", "reports"), ("gst", "reports"),
    ("app-settings", "settings"), ("firm-profile", "settings"), ("company-profile", "settings"),
    ("audit-log", "settings"), ("financial-year", "settings"), ("recycle-bin", "settings"), ("print-theme", "settings"),
    ("bills/bulk", "reports"), ("purchase-copies", "purchase"),
    ("dist/so", "purchase"), ("dist/principals", "parties"), ("dist/pipeline", "purchase"), ("dist/ledger", "purchase"),
    ("dist/articles", "items"), ("dist/price-changes", "items"), ("dist/price-list", "items"), ("dist/check-so", "purchase"), ("dist/ledger-check", "purchase"),
    ("dist/retailer-dues", "sales"), ("dist/retailers", "sales"), ("dist/followups", "sales"), ("dist/staff", "settings"),
    ("dist/profit", "reports"),
    ("dist/retailer-goods", "sales"), ("dist/warehouse", "items"), ("dist/forecast", "items"), ("dist", "reports"),
    ("my-permissions", "_any"), ("services-on", "_any"), ("qa-marks", "_any"), ("firms", "_any"), ("help", "_any"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_staff_permissions (
        workspace_id TEXT NOT NULL, user_id INTEGER NOT NULL, perms TEXT NOT NULL, preset TEXT,
        updated_by INTEGER, updated_at TEXT, PRIMARY KEY (workspace_id, user_id))""")


def owner_id(conn: sqlite3.Connection, ws: str) -> int | None:
    """The company owner flag, else the first HoP login of the company."""
    try:
        r = conn.execute("SELECT id FROM users WHERE workspace_id=? AND IFNULL(is_workspace_owner,0)=1 ORDER BY id LIMIT 1",
                         (ws,)).fetchone() or conn.execute(
            "SELECT id FROM users WHERE workspace_id=? AND role='hop_admin' ORDER BY id LIMIT 1", (ws,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(r[0]) if r else None


def is_owner(conn: sqlite3.Connection, user: dict[str, Any]) -> bool:
    if user.get("is_workspace_owner"):
        return True
    uid = user.get("user_id")
    oid = owner_id(conn, str(user.get("workspace_id") or ""))
    return uid is None or oid is None or int(uid) == oid


def perms_for(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    if is_owner(conn, user):
        return {"owner": True, "areas": {a: "edit" for a in AREA_KEYS}, "preset": "Owner", "can_backup": True}
    r = conn.execute("SELECT perms, preset FROM hop_staff_permissions WHERE workspace_id=? AND user_id=?",
                     (str(user.get("workspace_id") or ""), int(user["user_id"]))).fetchone()
    areas = dict(FULL_STAFF)
    can_backup = False
    if r:
        try:
            saved = json.loads(r[0])
            areas.update({k: v for k, v in saved.items() if k in AREA_KEYS and v in LEVELS})
            can_backup = saved.get("_backup") is True
        except ValueError:
            pass
    return {"owner": False, "areas": areas, "preset": (r[1] if r else None) or ("Full access" if not r else "Custom"),
            "can_backup": can_backup}


def area_for(conn: sqlite3.Connection, ws: str, method: str, path: str, body: Any, args: Any) -> str:
    """Which area a /api/v1/hop request belongs to ('_owner' = owner only, '_any' = everyone)."""
    rest = path.split("/api/v1/hop", 1)[-1]
    if OWNER_ONLY.match(rest):
        return "_owner"
    seg = rest.lstrip("/")
    if seg.startswith("party-transactions") or seg.startswith("documents") or seg.startswith("open-bills"):
        ty = None
        m = re.match(r"party-transactions/(\d+)", seg)
        if isinstance(body, dict) and body.get("txn_type") not in (None, ""):
            ty = body.get("txn_type")
        elif m:
            try:
                r = conn.execute("SELECT txn_type FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                                 (ws, int(m.group(1)))).fetchone()
                ty = r[0] if r else None
            except sqlite3.OperationalError:
                ty = None
        else:
            ty = (args.get("txn_type") or args.get("payment_type") or "").split(",")[0] if args else None
        try:
            ty = int(ty) if ty not in (None, "") else None
        except (TypeError, ValueError):
            ty = None
        if ty in PURCHASE_TYPES:
            return "purchase"
        if ty in MONEY_TYPES:
            return "money"
        if ty in SALES_TYPES:
            return "sales"
        return "_docs"   # lists of mixed documents: sales or purchase view is enough
    for prefix, area in PREFIX_AREA:
        if seg.startswith(prefix):
            return area
    return "crm"


def check(conn: sqlite3.Connection, user: dict[str, Any], method: str, path: str, body: Any = None,
          args: Any = None) -> str | None:
    """None = allowed, else the message to show."""
    ws = str(user.get("hop_active_ws") or user.get("workspace_id") or "")
    p = perms_for(conn, user)
    area = area_for(conn, ws, method, path, body, args)
    if p["owner"] or area == "_any":
        return None
    if area == "_owner":
        # The owner may allow a staff login (e.g. the accountant) to download the locked backup. Restore stays owner-only.
        rest = path.split("/api/v1/hop", 1)[-1].rstrip("/")
        if method == "GET" and p.get("can_backup") and (rest == "/backup" or rest.startswith("/backups")):
            return None
        return "Only the owner can do this."
    need = "view" if method in ("GET", "HEAD", "OPTIONS") or READ_POSTS.search(path) else "edit"
    order = {"none": 0, "view": 1, "edit": 2}
    if area == "_docs":
        have = max(order[p["areas"]["sales"]], order[p["areas"]["purchase"]])
        label = "Sales or Purchase"
    else:
        have = order[p["areas"][area]]
        label = dict(AREAS)[area].split(" — ")[0].split(",")[0]
    if have >= order[need]:
        return None
    return f"You don't have permission for {label}" + (" (view only)." if have == 1 else ".") + " Ask the owner."


def staff_list(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    ensure_schema(conn)
    oid = owner_id(conn, ws)
    rows = conn.execute("SELECT id, username, role FROM users WHERE workspace_id=? AND role='hop_admin' ORDER BY id",
                        (ws,)).fetchall()
    staff = []
    for r in rows:
        if int(r[0]) == oid:
            continue
        p = perms_for(conn, {"user_id": int(r[0]), "workspace_id": ws})
        staff.append({"user_id": int(r[0]), "name": r[1], "areas": p["areas"], "preset": p["preset"],
                      "can_backup": p["can_backup"],
                      "summary": summary(p["areas"]) + (" · Can download backup" if p["can_backup"] else "")})
    return {"areas": [{"key": k, "label": l} for k, l in AREAS], "levels": list(LEVELS),
            "presets": PRESETS, "staff": staff}


def save(conn: sqlite3.Connection, ws: str, owner: dict[str, Any], user_id: int, areas: dict[str, Any],
         preset: str | None = None, can_backup: bool | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    if not is_owner(conn, owner):
        raise PermissionError("Only the owner can change permissions")
    if int(user_id) == owner_id(conn, ws):
        raise ValueError("The owner always has full access")
    r = conn.execute("SELECT 1 FROM users WHERE id=? AND workspace_id=? AND role='hop_admin'", (int(user_id), ws)).fetchone()
    if not r:
        raise ValueError("Staff login not found in this company")
    old = perms_for(conn, {"user_id": int(user_id), "workspace_id": ws})
    only_backup = not areas and not preset
    if only_backup:
        areas = old["areas"]            # only the backup switch changed
    if preset and preset in PRESETS:
        areas = dict(PRESETS[preset])
    clean = {k: str((areas or {}).get(k) or FULL_STAFF[k]) for k in AREA_KEYS}
    backup = old["can_backup"] if can_backup is None else bool(can_backup)
    bad = [k for k, v in clean.items() if v not in LEVELS]
    if bad:
        raise ValueError("Each area must be none, view or edit")
    keep_preset = preset if preset in PRESETS else (old["preset"] if only_backup else "Custom")
    conn.execute("INSERT OR REPLACE INTO hop_staff_permissions (workspace_id, user_id, perms, preset, updated_by, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (ws, int(user_id), json.dumps({**clean, "_backup": backup}), keep_preset,
                                               owner.get("user_id"), _now()))
    conn.commit()
    return {"user_id": int(user_id), "areas": clean, "can_backup": backup, "summary": summary(clean)}


def summary(areas: dict[str, str]) -> str:
    """Plain line, e.g. 'Can change: Sales, Parties · Can see: Items · No access: Cash & bank, Reports'."""
    names = {k: l.split(" — ")[0].split(",")[0] for k, l in AREAS}
    parts = []
    for lvl, word in (("edit", "Can change"), ("view", "Can only see"), ("none", "No access")):
        ks = [names[k] for k in AREA_KEYS if areas.get(k) == lvl]
        if ks:
            parts.append(f"{word}: {', '.join(ks)}")
    return " · ".join(parts)


def risky(areas: dict[str, str]) -> list[str]:
    """Plain warnings before saving (rule based)."""
    out = []
    if areas.get("money") == "edit" and areas.get("reports") == "none":
        out.append("This person can change cash & bank but cannot see the reports to check them.")
    if areas.get("sales") == "none" and areas.get("purchase") == "none" and areas.get("items") == "none":
        out.append("This person cannot open bills or items — the app will look almost empty.")
    if areas.get("settings") == "edit":
        out.append("Settings 'can change' lets this person change bill numbering, GST and print settings.")
    return out
