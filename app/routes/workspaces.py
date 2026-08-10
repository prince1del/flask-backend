"""Workspace /admin/database — path sandbox + CSRF for backup/restore/cleanup."""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Blueprint, current_app, render_template_string, request, session

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth, require_role

workspaces_blueprint = Blueprint("workspaces", __name__)

_BACKUP_REL = Path("instance") / "backups"
_CLEANUP_REL = Path("instance") / "verification_uploads"

ADMIN_DATABASE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Database Admin</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    form { margin-bottom: 1rem; }
    pre { background: #f5f5f5; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
    button { padding: 0.5rem 1rem; }
  </style>
</head>
<body>
  <h1>Database Admin</h1>
  <p>Manage backup, restore, and audit logs for the centralized database.</p>
  <p>Restore paths must stay under <code>instance/backups/</code>. Cleanup is limited to
     <code>instance/verification_uploads/</code>.</p>
  <div class="card">
    <h2>Backup</h2>
    <form method="post">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
      <input type="hidden" name="action" value="backup" />
      <button type="submit">Create Backup</button>
    </form>
    {% if backup_message %}<p>{{ backup_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Restore</h2>
    <form method="post">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
      <input type="hidden" name="action" value="restore" />
      <label>Backup file path (under instance/backups only)</label>
      <input name="restore_path" value="instance/backups/centralized_db_backup.sqlite3" style="width: 100%; margin: 0.5rem 0;" />
      <button type="submit">Restore Database</button>
    </form>
    {% if restore_message %}<p>{{ restore_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Cleanup Temp Files</h2>
    <form method="post">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
      <input type="hidden" name="action" value="cleanup" />
      <label>Directory (under instance/verification_uploads only)</label>
      <input name="cleanup_dir" value="instance/verification_uploads" style="width: 100%; margin: 0.5rem 0;" />
      <button type="submit">Clean Stale Files</button>
    </form>
    {% if cleanup_message %}<p>{{ cleanup_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Audit Logs</h2>
    <pre>{{ audit_logs }}</pre>
  </div>
  <p><a href="/">Back to dashboard</a></p>
</body>
</html>
"""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _backups_root() -> Path:
    root = _project_root() / _BACKUP_REL
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _cleanup_root() -> Path:
    root = _project_root() / _CLEANUP_REL
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_under_allowlist(user_path: str, allow_root: Path) -> Path:
    """Resolve user_path and require it to be inside allow_root (no traversal)."""
    raw = (user_path or "").strip()
    if not raw:
        raise ValueError("Path is required")
    allow_root = allow_root.resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (_project_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(allow_root)
    except ValueError as exc:
        raise ValueError(f"Path must be under {allow_root}") from exc
    return candidate


def _csrf_token() -> str:
    token = session.get("admin_database_csrf")
    if not token or not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        session["admin_database_csrf"] = token
    return token


def _csrf_ok() -> bool:
    expected = session.get("admin_database_csrf")
    provided = request.form.get("csrf_token") or ""
    if not expected or not provided:
        return False
    return secrets.compare_digest(str(expected), str(provided))


@workspaces_blueprint.route("/api/v1/workspaces", methods=["GET", "POST"])
@require_jwt_auth
def workspaces() -> tuple[dict[str, object], int]:
    return {"status": "ok", "items": []}, 200


@workspaces_blueprint.route("/admin/database", methods=["GET", "POST"])
@require_jwt_auth
@require_role("admin")
def database_admin() -> str:
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    backup_message = None
    restore_message = None
    cleanup_message = None
    audit_logs = "No audit logs found"
    csrf = _csrf_token()

    if request.method == "POST":
        if not _csrf_ok():
            restore_message = "CSRF validation failed. Reload the page and try again."
            cleanup_message = restore_message
            backup_message = restore_message
        else:
            action = (request.form.get("action") or "").strip()
            if action == "backup":
                backup_path = db.backup_database(
                    _backups_root() / "centralized_db_backup.sqlite3"
                )
                backup_message = f"Backup created at {backup_path}"
            elif action == "restore":
                restore_path = (request.form.get("restore_path") or "").strip()
                if not restore_path:
                    restore_message = "Please provide a backup file path"
                else:
                    try:
                        safe_source = resolve_under_allowlist(restore_path, _backups_root())
                        if not safe_source.is_file():
                            raise FileNotFoundError(safe_source)
                        restored_path = db.restore_database(safe_source, overwrite=True)
                        restore_message = f"Restored database to {restored_path}"
                    except Exception as exc:
                        restore_message = f"Restore failed: {exc}"
            elif action == "cleanup":
                cleanup_dir = (
                    request.form.get("cleanup_dir") or ""
                ).strip() or str(_CLEANUP_REL).replace("\\", "/")
                try:
                    safe_dir = resolve_under_allowlist(cleanup_dir, _cleanup_root())
                    if not safe_dir.is_dir():
                        raise NotADirectoryError(safe_dir)
                    removed = db.cleanup_temp_uploads(safe_dir)
                    cleanup_message = f"Removed {removed} stale files from {safe_dir}"
                except Exception as exc:
                    cleanup_message = f"Cleanup failed: {exc}"

    logs = db.list_audit_logs(limit=20)
    if logs:
        lines = []
        for log in logs:
            details = log.get("details") or {}
            if isinstance(details, dict):
                detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
            else:
                detail_text = str(details) if details else "No details"
            lines.append(
                f"{log.get('created_at', '')} | {log.get('action', 'unknown')} | {log.get('table_name', 'unknown')} | {detail_text}"
            )
        audit_logs = "\n".join(lines)
    else:
        audit_logs = "Coming Soon"

    return render_template_string(
        ADMIN_DATABASE_TEMPLATE,
        csrf_token=csrf,
        backup_message=backup_message,
        restore_message=restore_message,
        cleanup_message=cleanup_message,
        audit_logs=audit_logs,
    )
