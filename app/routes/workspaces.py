import json
from pathlib import Path

from flask import Blueprint, Response, current_app, render_template_string, request

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth, require_role

workspaces_blueprint = Blueprint("workspaces", __name__)

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
  <div class="card">
    <h2>Backup</h2>
    <form method="post">
      <input type="hidden" name="action" value="backup" />
      <button type="submit">Create Backup</button>
    </form>
    {% if backup_message %}<p>{{ backup_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Restore</h2>
    <form method="post">
      <input type="hidden" name="action" value="restore" />
      <label>Backup file path</label>
      <input name="restore_path" value="instance/backups/centralized_db_backup.sqlite3" style="width: 100%; margin: 0.5rem 0;" />
      <button type="submit">Restore Database</button>
    </form>
    {% if restore_message %}<p>{{ restore_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Cleanup Temp Files</h2>
    <form method="post">
      <input type="hidden" name="action" value="cleanup" />
      <label>Directory</label>
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


@workspaces_blueprint.route("/api/v1/workspaces", methods=["GET", "POST"])
@require_jwt_auth
def workspaces() -> tuple[dict[str, object], int]:
    return {"status": "ok", "items": []}, 200


@workspaces_blueprint.route("/admin/database", methods=["GET", "POST"])
@require_jwt_auth
@require_role('admin')
def database_admin() -> str:
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    backup_message = None
    restore_message = None
    cleanup_message = None
    audit_logs = "No audit logs found"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "backup":
            backup_path = db.backup_database(
                Path("instance") / "backups" / "centralized_db_backup.sqlite3"
            )
            backup_message = f"Backup created at {backup_path}"
        elif action == "restore":
            restore_path = (request.form.get("restore_path") or "").strip()
            if restore_path:
                try:
                    restored_path = db.restore_database(restore_path, overwrite=True)
                    restore_message = f"Restored database to {restored_path}"
                except Exception as exc:
                    restore_message = f"Restore failed: {exc}"
            else:
                restore_message = "Please provide a backup file path"
        elif action == "cleanup":
            cleanup_dir = (
                request.form.get("cleanup_dir") or ""
            ).strip() or "instance/verification_uploads"
            removed = db.cleanup_temp_uploads(cleanup_dir)
            cleanup_message = f"Removed {removed} stale files from {cleanup_dir}"

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
        backup_message=backup_message,
        restore_message=restore_message,
        cleanup_message=cleanup_message,
        audit_logs=audit_logs,
    )
