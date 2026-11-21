"""Over-the-air (OTA) updates for the NEXORA Android app.

The latest release lives on the server's persistent disk:

    <APP_RELEASES_DIR>/android/latest.json   (metadata)
    <APP_RELEASES_DIR>/android/<file>.apk    (the APK it points to)

`deploy/publish_android_apk.sh` writes both. The app asks /latest on start,
and if `version_code` is newer than its own BuildConfig.VERSION_CODE it
downloads /download, checks the SHA-256 and hands it to Android's installer.
Both endpoints need a logged-in user, so the APK is not public.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from app.routes.auth import require_jwt_auth

app_updates_bp = Blueprint("app_updates", __name__, url_prefix="/api/v1/app")


def _release_dir(platform: str = "android") -> Path:
    """android = phone app, wear = Galaxy Watch app (deploy/publish_watch_apk.sh)."""
    explicit = (os.getenv("APP_RELEASES_DIR") or "").strip()
    if explicit:
        base = Path(explicit)
    else:
        disk = (os.getenv("PERSISTENT_DISK_PATH") or "").strip() or "/var/data"
        base = Path(disk) / "app-releases"
    return base / platform


def _android_release_dir() -> Path:
    return _release_dir("android")


def _load_latest(platform: str = "android") -> dict | None:
    meta_path = _release_dir(platform) / "latest.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    file_name = str(meta.get("file") or "")
    # Only a bare file name inside the release dir — never a path.
    if not file_name or Path(file_name).name != file_name:
        return None
    if not (_release_dir(platform) / file_name).is_file():
        return None
    try:
        meta["version_code"] = int(meta.get("version_code"))
    except (TypeError, ValueError):
        return None
    return meta


# ---- Which UI a change belongs to: each point is "business", "executive" or "all" (app lock, Ask Nexora…).
_TAGS = {"[business]": "business", "[b]": "business", "[executive]": "executive", "[e]": "executive",
         "[all]": "all", "[a]": "all", "[global]": "all"}
UIS = ("business", "executive", "all")


def release_items(r: dict) -> list[dict]:
    """Points of one build. Uses `items` when published with them; else splits `notes` on ';' and reads a
    leading [business] / [executive] / [all] tag (no tag = all UIs)."""
    raw = r.get("items")
    if isinstance(raw, list) and raw:
        out = []
        for i in raw:
            if isinstance(i, dict) and str(i.get("text") or "").strip():
                ui = str(i.get("ui") or "all").strip().lower()
                out.append({"ui": ui if ui in UIS else "all", "text": str(i["text"]).strip()})
        if out:
            return out
    out = []
    for part in str(r.get("notes") or "").split(";"):
        text, ui = part.strip(), "all"
        low = text.lower()
        for tag, val in _TAGS.items():
            if low.startswith(tag):
                text, ui = text[len(tag):].strip(), val
                break
        if text:
            out.append({"ui": ui, "text": text})
    return out


def items_for(items: list[dict], ui: str | None) -> list[dict]:
    """Only this UI's points plus the ones for every UI. No ui = everything (older phones)."""
    ui = (ui or "").strip().lower()
    if not ui:
        return items
    return [i for i in items if i["ui"] in ("all", ui)]


def _min_version(meta: dict) -> int:
    vals = []
    for v in (meta.get("min_version_code"), os.getenv("NEXORA_MIN_APP_VERSION_CODE")):
        try:
            if v not in (None, ""):
                vals.append(int(v))
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else 0


@app_updates_bp.route("/android/latest", methods=["GET"])
@require_jwt_auth
def android_latest():
    meta = _load_latest()
    if meta is None:
        return jsonify({"success": True, "data": {"available": False}}), 200
    ui = request.args.get("ui")
    mine = items_for(release_items(meta), ui)
    return jsonify(
        {
            "success": True,
            "data": {
                "available": True,
                "version_code": meta["version_code"],
                "version_name": meta.get("version_name") or str(meta["version_code"]),
                # Nothing for this UI → the phone does not pop the update window (Settings still offers it).
                "relevant": bool(mine) or not ui,
                "items": mine,
                "notes": "; ".join(i["text"] for i in mine) if mine else "Small fixes and improvements",
                "sha256": meta.get("sha256") or "",
                "size_bytes": meta.get("size_bytes"),
                "published_at": meta.get("published_at"),
                "mandatory": bool(meta.get("mandatory", False)),
                # owner 21-11 "version check": any phone older than this must update (even when the newest
                # release itself is optional) — latest.json "min_version_code" or env NEXORA_MIN_APP_VERSION_CODE
                "min_version_code": _min_version(meta),
                "download_path": "/api/v1/app/android/download",
            },
        }
    ), 200


@app_updates_bp.route("/android/download", methods=["GET"])
@require_jwt_auth
def android_download():
    meta = _load_latest()
    if meta is None:
        return jsonify({"success": False, "error": {"message": "No release published"}}), 404
    path = _android_release_dir() / meta["file"]
    return send_file(
        path,
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name=f"NEXORA-{meta.get('version_name') or meta['version_code']}.apk",
        max_age=0,
    )


def _load_history(ui: str | None = None) -> list[dict]:
    """Every published build (newest first) from history.json; latest.json as fallback."""
    path = _android_release_dir() / "history.json"
    items: list[dict] = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        except (OSError, ValueError):
            items = []
    latest = _load_latest()
    if latest and not any(str(r.get("version_code")) == str(latest["version_code"]) for r in items):
        items.append(latest)
    out = []
    for r in items:
        try:
            code = int(r.get("version_code"))
        except (TypeError, ValueError):
            continue
        mine = items_for(release_items(r), ui)
        if ui and not mine:
            continue  # nothing in this build for this UI
        out.append({
            "version_code": code,
            "version_name": r.get("version_name") or str(code),
            "notes": "; ".join(i["text"] for i in mine),
            "items": mine,
            "published_at": r.get("published_at"),
        })
    out.sort(key=lambda r: r["version_code"], reverse=True)
    return out[:60]


@app_updates_bp.route("/android/releases", methods=["GET"])
@require_jwt_auth
def android_releases():
    """What changed in each build - Settings → Updates on the phone. ?ui=business|executive → only that
    UI's changes plus the ones for every UI (app lock, Ask Nexora, updates…)."""
    return jsonify({"success": True, "data": {"releases": _load_history(request.args.get("ui"))}}), 200
