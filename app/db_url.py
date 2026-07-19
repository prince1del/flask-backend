"""Database URL helpers for local SQLite and Render Postgres."""

from __future__ import annotations

import os
from pathlib import Path


def normalize_database_url(url: str | None) -> str | None:
    """Render often provides postgres:// — SQLAlchemy needs postgresql://."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
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


def resolve_centralized_db_path(project_root: Path | None = None) -> str:
    """Path for CentralizedDB (SQLite). Prefer Render persistent disk when mounted."""
    project_root = project_root or Path(__file__).resolve().parent.parent

    explicit = (os.getenv("DATABASE_PATH") or "").strip()
    if explicit:
        Path(explicit).parent.mkdir(parents=True, exist_ok=True)
        return explicit

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
