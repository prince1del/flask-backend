"""Company B must never see Company A's data on any GET endpoint.

Company A gets one row in every table that carries a workspace_id / user_id,
with a marker string in its text columns. Then every GET route is called as
Company B: plainly, with A's row ids in the URL, and with A's workspace/user
in the query string. Any response containing the marker is a leak.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from app import create_app
from app.services import subscriptions as subs
from centralized_db_system.db import CentralizedDB

MARKER = "ZQXLEAKA"
QUESTION = CentralizedDB.SECRET_QUESTIONS[0]

# Routes that only redirect to Google/OAuth, stream forever, or log out.
SKIP_PREFIXES = (
    "/static", "/logout", "/api/v1/auth/logout", "/api/v1/storage/connect",
    "/api/v1/storage/oauth", "/oauth", "/api/v1/gmail/connect", "/api/v1/gmail/oauth",
)
SKIP_PATTERNS = (re.compile(r"stream"), re.compile(r"/callback"))
SKIP_TABLES = {"sqlite_sequence", "alembic_version"}
OWNER_COLUMNS = {"user_id", "created_by", "owner_user_id", "created_by_user_id", "uploaded_by"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "isolation.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-isolation-32-bytes-long")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    subs.clear_cache()
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application
    subs.clear_cache()


def _signup(client, username):
    res = client.post("/api/v1/signup", json={
        "company_name": f"{username} Traders",
        "username": username,
        "password": "pass12345",
        "email": f"{username}@example.com",
        "secret_question": QUESTION,
        "secret_answer": "Delhi",
        "category": "business",
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _value(col: str, ctype: str, ws: str, uid: int):
    name = col.lower()
    ctype = (ctype or "").upper()
    if name == "workspace_id":
        return ws
    if name in OWNER_COLUMNS:
        return uid
    if name == "status":
        return "active"
    if name == "role":
        return "sales_executive"
    if name.startswith("is_") or name.startswith("has_"):
        return 1
    if name.endswith("_at") or "date" in name or name in ("created", "updated", "timestamp"):
        return "2026-11-10T10:00:00+00:00"
    if name == "season":
        return "AW26"
    if name.endswith("json") or name in ("metadata", "payload", "data", "extra_attributes"):
        return json.dumps({"note": MARKER})
    if "INT" in ctype or "REAL" in ctype or "NUM" in ctype or "FLOAT" in ctype or "DOUBLE" in ctype:
        return 1
    return f"{MARKER} {col}"


def _seed_company_a(db_path: str, ws: str, uid: int) -> dict[str, int]:
    """One marker row per tenant table; returns {table: rowid}."""
    ids: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )]
        for table in tables:
            if table in SKIP_TABLES or table.startswith("sqlite_"):
                continue
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            names = {c[1].lower() for c in cols}
            if "workspace_id" not in names and not (names & OWNER_COLUMNS):
                continue
            values = {}
            for _cid, name, ctype, _notnull, _default, pk in cols:
                if pk == 1 and "INT" in (ctype or "").upper():
                    continue
                values[name] = _value(name, ctype, ws, uid)
            placeholders = ", ".join("?" * len(values))
            col_sql = ", ".join(f'"{c}"' for c in values)
            try:
                cur = conn.execute(
                    f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders})',
                    tuple(values.values()),
                )
                ids[table] = int(cur.lastrowid)
            except sqlite3.Error:
                continue
        conn.commit()
    return ids


def _urls(app, a_ids: list[int], a_ws: str, a_user: str):
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        path = rule.rule
        if path.startswith(SKIP_PREFIXES) or any(p.search(path) for p in SKIP_PATTERNS):
            continue
        if not rule.arguments:
            yield path
            continue
        choices: list[dict] = []
        for value in a_ids:
            args = {}
            for arg in rule.arguments:
                conv = type(rule._converters.get(arg)).__name__ if hasattr(rule, "_converters") else ""
                args[arg] = value if "Integer" in conv else (a_ws if "workspace" in arg else str(value))
            choices.append(args)
        for arg in rule.arguments:
            if "Integer" not in type(rule._converters.get(arg)).__name__:
                choices.append({a: (a_user if a == arg else 1) for a in rule.arguments})
                choices.append({a: (a_ws if a == arg else 1) for a in rule.arguments})
        for args in choices:
            try:
                yield rule.build(args, append_unknown=False)[1]
            except Exception:
                continue


def _sweep(app, client, token, a, a_ids):
    headers = {"Authorization": f"Bearer {token}"}
    query = f"workspace_id={a['workspace_id']}&user_id={a['user_id']}"
    hits: list[str] = []
    statuses: dict[int, int] = {}
    seen: set[str] = set()
    for url in _urls(app, a_ids, a["workspace_id"], a["username"]):
        for probe in (url, f"{url}{'&' if '?' in url else '?'}{query}"):
            if probe in seen:
                continue
            seen.add(probe)
            try:
                res = client.get(probe, headers=headers)
            except Exception:
                continue
            statuses[res.status_code] = statuses.get(res.status_code, 0) + 1
            if MARKER.encode() in res.get_data():
                hits.append(f"{res.status_code} {probe}")
    return hits, statuses


def test_company_b_sees_nothing_of_company_a(app):
    client = app.test_client()
    a = _signup(client, "leakowner")
    b = _signup(client, "leakprobe")
    assert a["workspace_id"] != b["workspace_id"]

    ids = _seed_company_a(app.config["DATABASE_PATH"], a["workspace_id"], int(a["user_id"]))
    assert len(ids) > 20, ids
    a_ids = sorted(set(ids.values()))[:4]

    # The sweep must be able to see a leak: Company A itself finds its marker.
    own, own_statuses = _sweep(app, client, a["access_token"], a, a_ids)
    assert len(own) >= 10, (own, own_statuses)

    leaks, statuses = _sweep(app, client, b["access_token"], a, a_ids)
    assert statuses.get(200, 0) >= 100, statuses
    assert not leaks, "Company B can see Company A's data:\n" + "\n".join(leaks)
