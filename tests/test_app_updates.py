"""OTA release endpoints for the Android app."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.web_app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "ota.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("APP_RELEASES_DIR", str(tmp_path / "releases"))
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), tmp_path / "releases" / "android"


def _publish(rel_dir, *, code=907900, file_name="nexora-907900.apk", body=b"APKDATA"):
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / file_name).write_bytes(body)
    meta = {
        "version_code": code,
        "version_name": "1.0.907900",
        "notes": "Pending SO fix",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "file": file_name,
    }
    (rel_dir / "latest.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def test_latest_reports_nothing_when_unpublished(client):
    c, _ = client
    r = c.get("/api/v1/app/android/latest")
    assert r.status_code == 200
    assert r.get_json()["data"] == {"available": False}
    assert c.get("/api/v1/app/android/download").status_code == 404


def test_latest_and_download_serve_published_apk(client):
    c, rel = client
    meta = _publish(rel)
    data = c.get("/api/v1/app/android/latest").get_json()["data"]
    assert data["available"] is True
    assert data["version_code"] == 907900
    assert data["sha256"] == meta["sha256"]
    r = c.get("/api/v1/app/android/download")
    assert r.status_code == 200
    assert r.data == b"APKDATA"
    assert r.mimetype == "application/vnd.android.package-archive"


def test_path_like_file_name_is_refused(client):
    c, rel = client
    _publish(rel)
    meta = json.loads((rel / "latest.json").read_text(encoding="utf-8"))
    meta["file"] = "../ota.sqlite3"
    (rel / "latest.json").write_text(json.dumps(meta), encoding="utf-8")
    assert c.get("/api/v1/app/android/latest").get_json()["data"] == {"available": False}
    assert c.get("/api/v1/app/android/download").status_code == 404


def test_updates_are_shown_per_ui(client):
    """Each UI sees only its own points plus the ones for every UI (app lock, Ask Nexora…)."""
    c, rel = client
    _publish(rel)
    meta = json.loads((rel / "latest.json").read_text(encoding="utf-8"))
    meta["notes"] = "[business] Loyalty points; [executive] Faster Hisaab; Fingerprint lock"
    (rel / "latest.json").write_text(json.dumps(meta), encoding="utf-8")
    hist = [{"version_code": 907800, "version_name": "1.0.907800", "published_at": "2026-11-16",
             "items": [{"ui": "business", "text": "Godowns"}]},
            {"version_code": 907700, "version_name": "1.0.907700", "notes": "[executive] Ledger check"}]
    (rel / "history.json").write_text(json.dumps(hist), encoding="utf-8")

    biz = c.get("/api/v1/app/android/latest?ui=business").get_json()["data"]
    assert biz["relevant"] is True and biz["notes"] == "Loyalty points; Fingerprint lock"
    exe = c.get("/api/v1/app/android/releases?ui=executive").get_json()["data"]["releases"]
    assert [r["version_code"] for r in exe] == [907900, 907700]  # Godowns build is not shown
    assert exe[0]["notes"] == "Faster Hisaab; Fingerprint lock"
    everything = c.get("/api/v1/app/android/releases").get_json()["data"]["releases"]
    assert len(everything) == 3  # older phones (no ui) still see all

    meta["notes"] = "[business] Only for business"
    (rel / "latest.json").write_text(json.dumps(meta), encoding="utf-8")
    exe_latest = c.get("/api/v1/app/android/latest?ui=executive").get_json()["data"]
    assert exe_latest["relevant"] is False and exe_latest["notes"] == "Small fixes and improvements"
