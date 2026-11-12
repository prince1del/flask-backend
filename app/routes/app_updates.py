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

from flask import Blueprint, jsonify, send_file

from app.routes.auth import require_jwt_auth

app_updates_bp = Blueprint("app_updates", __name__, url_prefix="/api/v1/app")


def _android_release_dir() -> Path:
    explicit = (os.getenv("APP_RELEASES_DIR") or "").strip()
    if explicit:
        base = Path(explicit)
    else:
        disk = (os.getenv("PERSISTENT_DISK_PATH") or "").strip() or "/var/data"
        base = Path(disk) / "app-releases"
    return base / "android"


def _load_latest() -> dict | None:
    meta_path = _android_release_dir() / "latest.json"
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
    if not (_android_release_dir() / file_name).is_file():
        return None
    try:
        meta["version_code"] = int(meta.get("version_code"))
    except (TypeError, ValueError):
        return None
    return meta


@app_updates_bp.route("/android/latest", methods=["GET"])
@require_jwt_auth
def android_latest():
    meta = _load_latest()
    if meta is None:
        return jsonify({"success": True, "data": {"available": False}}), 200
    return jsonify(
        {
            "success": True,
            "data": {
                "available": True,
                "version_code": meta["version_code"],
                "version_name": meta.get("version_name") or str(meta["version_code"]),
                "notes": meta.get("notes") or "",
                "sha256": meta.get("sha256") or "",
                "size_bytes": meta.get("size_bytes"),
                "published_at": meta.get("published_at"),
                "mandatory": bool(meta.get("mandatory", False)),
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
