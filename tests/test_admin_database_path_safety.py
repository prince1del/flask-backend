"""Path sandbox + CSRF for /admin/database."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.routes.workspaces import resolve_under_allowlist, _project_root


def test_resolve_under_allowlist_accepts_nested_file(tmp_path):
    root = tmp_path / "instance" / "backups"
    root.mkdir(parents=True)
    target = root / "centralized_db_backup.sqlite3"
    target.write_bytes(b"sqlite")

    # Monkeypatch project root resolution by using absolute path under root
    resolved = resolve_under_allowlist(str(target), root)
    assert resolved == target.resolve()


def test_resolve_under_allowlist_blocks_traversal(tmp_path):
    root = tmp_path / "instance" / "backups"
    root.mkdir(parents=True)
    outside = tmp_path / "evil.sqlite3"
    outside.write_bytes(b"nope")

    with pytest.raises(ValueError, match="must be under"):
        resolve_under_allowlist(str(outside), root)

    with pytest.raises(ValueError, match="must be under"):
        resolve_under_allowlist(str(root / ".." / "evil.sqlite3"), root)


def test_resolve_under_allowlist_blocks_root_and_devnull(tmp_path):
    root = tmp_path / "instance" / "backups"
    root.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_under_allowlist("/", root)
    # Windows-null / Unix-null style absolute paths must not pass containment
    for candidate in (r"C:\Windows\System32", "/dev/null", "/etc/passwd"):
        try:
            resolve_under_allowlist(candidate, root)
            pytest.fail(f"expected rejection for {candidate}")
        except ValueError:
            pass


def _admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "admin_db_page.sqlite3"

    def _apply():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "admin-database-csrf-secret-key-32b")
        monkeypatch.setenv("JWT_SECRET_KEY", "admin-database-csrf-secret-key-32b")
        monkeypatch.setenv("ADMIN_USERNAME", "admin")

    _apply()
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply()

    from app.db import db
    from app.models import User
    from app.jwt_service import JWTService

    app = web_app_module.create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))
    with app.app_context():
        user = User(
            username="admin",
            email="admin@example.com",
            role="admin",
            status="active",
            workspace_id="default",
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    client = app.test_client()
    token, _ = JWTService(secret_key=app.config["SECRET_KEY"]).create_tokens(
        user_id=user_id, username="admin", role="admin", workspace_id="default"
    )
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, app


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token missing from admin database page"
    return match.group(1)


def test_admin_database_requires_csrf_and_blocks_unsafe_restore(tmp_path, monkeypatch):
    client, headers, _ = _admin_client(tmp_path, monkeypatch)

    page = client.get("/admin/database", headers=headers)
    assert page.status_code == 200, page.get_data(as_text=True)
    csrf = _extract_csrf(page.get_data(as_text=True))

    no_csrf = client.post(
        "/admin/database",
        headers=headers,
        data={"action": "backup"},
        content_type="application/x-www-form-urlencoded",
    )
    assert no_csrf.status_code == 200
    assert "CSRF validation failed" in no_csrf.get_data(as_text=True)

    evil = client.post(
        "/admin/database",
        headers=headers,
        data={
            "csrf_token": csrf,
            "action": "restore",
            "restore_path": "/dev/null",
        },
        content_type="application/x-www-form-urlencoded",
    )
    assert evil.status_code == 200
    body = evil.get_data(as_text=True)
    assert "Restore failed" in body
    assert "must be under" in body

    cleanup_evil = client.post(
        "/admin/database",
        headers=headers,
        data={
            "csrf_token": csrf,
            "action": "cleanup",
            "cleanup_dir": str(Path("/")),
        },
        content_type="application/x-www-form-urlencoded",
    )
    assert "Cleanup failed" in cleanup_evil.get_data(as_text=True)

    ok_backup = client.post(
        "/admin/database",
        headers=headers,
        data={"csrf_token": csrf, "action": "backup"},
        content_type="application/x-www-form-urlencoded",
    )
    assert "Backup created" in ok_backup.get_data(as_text=True)
