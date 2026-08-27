"""Database URL helpers for local SQLite and Render Postgres."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def normalize_database_url(url: str | None) -> str | None:
    """Normalize Render/Postgres URLs for SQLAlchemy + psycopg v3.

    Render often gives postgres://. SQLAlchemy 2.0 defaults postgresql:// to
    psycopg2 (not installed). We pin postgresql+psycopg:// (psycopg3).
    Also ensure sslmode=require (Render Postgres expects TLS).
    """
    if not url:
        return None
    url = url.strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    if url.startswith("postgresql+psycopg://") or url.startswith("postgresql://"):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "sslmode" not in query:
            query["sslmode"] = "require"
        url = urlunparse(parsed._replace(query=urlencode(query)))

    return url


def resolve_sqlalchemy_url(project_root: Path | None = None) -> str:
    """Pick SQLAlchemy URL: DATABASE_URL → CLOUD_DATABASE_URL → local sqlite."""
    project_root = project_root or Path(__file__).resolve().parent.parent
    root_db = project_root / "centralized_db.sqlite3"
    instance_db = project_root / "instance" / "centralized_db.sqlite3"

    database_url = normalize_database_url(os.getenv("DATABASE_URL"))
    if database_url:
        return database_url

    cloud_url = normalize_database_url(os.getenv("CLOUD_DATABASE_URL"))
    if cloud_url:
        if cloud_url.startswith("sqlite:///"):
            cloud_path = cloud_url[len("sqlite:///") :]
            if cloud_path == "centralized_db.sqlite3":
                if root_db.exists():
                    return f"sqlite:///{root_db.as_posix()}"
                return f"sqlite:///{instance_db.as_posix()}"
            return cloud_url
        return cloud_url

    if root_db.exists():
        return f"sqlite:///{root_db.as_posix()}"
    if instance_db.exists():
        return f"sqlite:///{instance_db.as_posix()}"
    return "sqlite:///centralized_db.sqlite3"


def _sqlite_path_from_env() -> str | None:
    """SQLite file behind CLOUD_DATABASE_URL / DATABASE_URL, if either is SQLite.

    Postgres URLs (Render) are ignored — CentralizedDB is SQLite-only.
    """
    for env_name in ("CLOUD_DATABASE_URL", "DATABASE_URL"):
        value = (os.getenv(env_name) or "").strip().strip('"').strip("'")
        if not value.startswith("sqlite://"):
            continue
        path_value = value.removeprefix("sqlite:///")
        if not path_value or path_value == "centralized_db.sqlite3":
            continue
        # sqlite:////C:/... → C:/...
        if path_value.startswith("/") and len(path_value) >= 3 and path_value[2] == ":":
            path_value = path_value[1:]
        return str(Path(path_value).expanduser())
    return None


def resolve_centralized_db_path(project_root: Path | None = None) -> str:
    """Path for CentralizedDB (SQLite). Prefer Render persistent disk when mounted."""
    project_root = project_root or Path(__file__).resolve().parent.parent

    explicit = (os.getenv("DATABASE_PATH") or "").strip()
    if explicit:
        Path(explicit).parent.mkdir(parents=True, exist_ok=True)
        return explicit

    # Match CentralizedDB._resolve_db_path: a SQLite URL must win here too,
    # otherwise app.config["DATABASE_PATH"] and CentralizedDB() open two
    # different files (auth reads one, writes land in the other).
    sqlite_path = _sqlite_path_from_env()
    if sqlite_path:
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite_path

    # Render persistent disk — attach at /var/data in the dashboard
    for candidate in (
        os.getenv("PERSISTENT_DISK_PATH", "").strip(),
        "/var/data",
        "/opt/render/project/src/data",
    ):
        if not candidate:
            continue
        base = Path(candidate)
        if base.exists() and base.is_dir():
            db_path = base / "centralized_db.sqlite3"
            return str(db_path)

    root_db = project_root / "centralized_db.sqlite3"
    instance_db = project_root / "instance" / "centralized_db.sqlite3"
    if root_db.exists():
        return str(root_db)
    if instance_db.exists():
        return str(instance_db)
    return str(root_db)
