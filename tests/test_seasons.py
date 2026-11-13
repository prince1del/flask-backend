"""Seasons: one spelling per season, nearest-SO suggestion, manual assignment."""

from __future__ import annotations

import importlib
import sqlite3

from app.services import seasons as s


def test_canonical_season():
    assert s.canonical_season("aw 26") == "AW26"
    assert s.canonical_season("AW-26") == "AW26"
    assert s.canonical_season("Ss'27 orders") == "SS27"
    assert s.canonical_season("festive  2026") == "FESTIVE 2026"
    assert s.canonical_season("  ") is None


def test_suggest_uses_nearest_so_of_same_distributor_first():
    known = {
        "102876100": (1, "AW26", "Bed"),
        "102890000": (1, "SS27", "Bath"),
        "102876120": (2, "AW26", "Bath"),
    }
    got = s.suggest("102876118", 1, known)
    assert got["season"] == "AW26" and got["category"] == "Bed" and got["near_so"] == "102876100"
    assert s.suggest("102889990", 1, known)["season"] == "SS27"
    assert s.suggest("102876118", 99, known)["near_so"] == "102876120"  # other distributors as fallback
    assert s.suggest("", 1, known) is None


def test_assign_and_overrides():
    conn = sqlite3.connect(":memory:")
    out = s.assign(conn, workspace_id="ws", user_id=1, so_number="SO 102876013", distributor_id=9,
                   season="aw 26", category="Bed")
    assert out == {"so_number": "102876013", "season": "AW26", "category": "Bed"}
    assert s.overrides(conn, "ws") == {"102876013": (9, "AW26", "Bed")}
    s.assign(conn, workspace_id="ws", user_id=1, so_number="102876013", distributor_id=9, season="SS27", category="Bath")
    assert s.overrides(conn, "ws")["102876013"] == (9, "SS27", "Bath")
    assert s.overrides(conn, "other") == {}
    import pytest

    with pytest.raises(ValueError):
        s.assign(conn, workspace_id="ws", user_id=1, so_number="", distributor_id=None, season="AW26", category="Bed")


def test_season_routes(tmp_path, monkeypatch):
    db_path = tmp_path / "seasons.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "seasons-test-key-0123456789abcdef")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    from centralized_db_system.db import CentralizedDB

    CentralizedDB(str(db_path)).create_user("season_user", "pass123", role="sales_executive", workspace_id="ws-1")
    client = app.test_client()
    tok = client.post("/api/v1/auth/login", json={"username": "season_user", "password": "pass123"}).get_json()["data"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    r = client.get("/api/v1/seasons", headers=h)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["seasons"] == [] and data["unassigned"] == [] and data["active"] is None

    ok = client.post("/api/v1/seasons/assign", json={"so_number": "102876013", "distributor_id": 9,
                                                      "season": "aw26", "category": "Bed"}, headers=h)
    assert ok.status_code == 200 and ok.get_json()["data"]["season"] == "AW26"
    bad = client.post("/api/v1/seasons/assign", json={"so_number": "102876013"}, headers=h)
    assert bad.status_code == 400
    assert client.get("/api/v1/seasons").status_code == 401
