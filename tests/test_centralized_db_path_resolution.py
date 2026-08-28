"""Regression tests for CentralizedDB()'s bare-constructor path resolution.

Bug: a bare CentralizedDB() (no db_path argument) resolved its SQLite path
independently of app/db_url.py's resolve_centralized_db_path() — the one
Flask's app.config["DATABASE_PATH"] and every request-scoped CentralizedDB()
via _db_path() actually uses. When DATABASE_URL was a Postgres connection
string (Render production), the old code fell through to
`return Path(value).expanduser()`, turning the Postgres URL itself into a
bogus filesystem path instead of honoring DATABASE_PATH / Render's /var/data
persistent disk. Any caller using the bare constructor (Google Drive OAuth
connect/status in app/routes/storage.py, and 13 other call sites) was
silently reading/writing a throwaway SQLite file that evaporates on every
redeploy — production symptom: Google Drive showed "connected" until the
next deploy, then reverted to "not connected" every time.
"""

from centralized_db_system.db import CentralizedDB


def _resolve(monkeypatch, **env):
    for key in ("DATABASE_PATH", "DATABASE_URL", "CLOUD_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # _resolve_db_path doesn't touch `self` — safe to call unbound.
    return str(CentralizedDB._resolve_db_path(None, None))


def test_postgres_database_url_is_not_mangled_into_a_path(monkeypatch):
    result = _resolve(
        monkeypatch,
        DATABASE_URL="postgresql://user:pass@host.render.com/dbname",
    )
    assert "postgresql" not in result
    assert "@" not in result
    assert result.endswith("centralized_db.sqlite3")


def test_explicit_database_path_env_var_wins(monkeypatch, tmp_path):
    target = tmp_path / "somewhere" / "centralized_db.sqlite3"
    result = _resolve(
        monkeypatch,
        DATABASE_URL="postgresql://user:pass@host.render.com/dbname",
        DATABASE_PATH=str(target),
    )
    assert result == str(target)


def test_matches_app_db_url_resolver_for_postgres(monkeypatch):
    """The bare constructor must resolve to the exact same path Flask's
    app.config["DATABASE_PATH"] does for the same environment — otherwise
    auth reads one file while a bare-constructor caller writes another."""
    from app.db_url import resolve_centralized_db_path

    for key in ("DATABASE_PATH", "DATABASE_URL", "CLOUD_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host.render.com/dbname")

    bare_ctor_result = str(CentralizedDB._resolve_db_path(None, None))
    flask_config_result = resolve_centralized_db_path()
    assert bare_ctor_result == flask_config_result
