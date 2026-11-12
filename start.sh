#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app
# Limit glibc arenas — Python on Linux otherwise fragments toward OOM on 512MB.
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export PYTHONUNBUFFERED=1

# Lightweight migrate/seed (soft-fail). Skip entirely with SKIP_STARTUP_BOOT=1.
if [ "${SKIP_STARTUP_BOOT:-0}" != "1" ]; then
python - <<'PY'
import gc
import os
import sys
import traceback

print("boot: DATABASE_URL set=", bool((os.getenv("DATABASE_URL") or "").strip()))
try:
    from sqlalchemy import inspect
    from flask_migrate import upgrade, stamp
    from app.web_app import create_app
    from app.db import db
    from app.hop_schema import ensure_hop_schema
    from centralized_db_system.db import CentralizedDB

    app = create_app()
    db_path = app.config["DATABASE_PATH"]
    print("DATABASE_PATH=", db_path)
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""

    # WAL lets several gunicorn worker processes read/write the business
    # SQLite file concurrently without "database is locked". Persistent; idempotent.
    try:
        import sqlite3 as _sq
        with _sq.connect(db_path) as _c:
            print("SQLite journal_mode:", _c.execute("PRAGMA journal_mode=WAL").fetchone()[0])
    except Exception as e:
        print("SQLite WAL skip:", e)

    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        if "postgresql" in uri:
            if "alembic_version" not in tables:
                stamp(revision="head")
                print("Postgres: stamped alembic head")
            else:
                try:
                    upgrade()
                    print("Postgres: upgrade ok")
                except Exception as e:
                    print("Postgres: upgrade soft-fail:", e)
        else:
            try:
                upgrade()
                print("SQLite: upgrade ok")
            except Exception as e:
                print("SQLite: upgrade soft-fail:", e)

    cdb = CentralizedDB(db_path)
    try:
        seed_pw = (os.getenv("SEED_PASSWORD") or "").strip()
        if not seed_pw:
            print("SEED skip: SEED_PASSWORD not set")
        else:
            print(
                "SEED",
                cdb.create_user(
                    os.getenv("SEED_USERNAME", "kps.julka@gmail.com"),
                    seed_pw,
                    role=os.getenv("SEED_ROLE", "sales_executive"),
                    workspace_id=os.getenv("SEED_WORKSPACE", "bombay_dyeing_gt_north"),
                ),
            )
    except ValueError as e:
        print("SEED skip:", e)
    except Exception as e:
        print("SEED err:", e)

    try:
        print("BD login migrate:", cdb.migrate_bd_owner_login())
    except Exception as e:
        print("BD login migrate skip:", e)

    try:
        ensure_hop_schema(db_path)
        print(
            "HOP SEED",
            cdb.ensure_hop_admin_login(
                old_username=os.getenv("HOP_ADMIN_OLD_USERNAME", "hop_prizm"),
                new_username=os.getenv("HOP_ADMIN_USERNAME", "prince1del"),
                new_password=os.getenv("HOP_ADMIN_PASSWORD", ""),
            ),
        )
    except Exception as e:
        print("HOP SEED err:", e)

    try:
        print("LOGIN DEDUPE", cdb.dedupe_email_login_accounts())
        print("ARCHIVED PURGE", cdb.delete_archived_duplicate_logins())
    except Exception as e:
        print("LOGIN DEDUPE err:", e)

    try:
        cdb.ensure_login_identity_indexes()
    except Exception as e:
        print("LOGIN INDEX err:", e)

    # Free peak RAM before gunicorn starts (same shell, sequential).
    del cdb, app, db
    gc.collect()
    print("boot: released create_app memory")
except Exception:
    traceback.print_exc()
    # Prefer serving over failing deploy if migrate/seed OOMs or errors.
    print("boot: continuing to gunicorn despite boot error", file=sys.stderr)
PY
fi

# Sizing: 2 vCPU / 2GB. The old "1 sync worker, 1 thread" setup (sized for
# 512MB Starter) served ONE request at a time — a slow OCR / mail-sync / PDF call
# froze every other client. gthread lets I/O-bound requests overlap; a second
# worker process uses the second CPU core so CPU-heavy PDF parsing in one
# process does not stall everyone else (Python threads share one core per
# process). Safe with SQLite because the DB is switched to WAL mode above.
# Tune on Render via env: GUNICORN_WORKERS / GUNICORN_THREADS / GUNICORN_MAX_REQUESTS.
# No --preload (master+worker ≈ 2× RAM).
exec gunicorn wsgi:app \
  --bind 0.0.0.0:${PORT:-10000} \
  --workers "${GUNICORN_WORKERS:-2}" \
  --threads "${GUNICORN_THREADS:-6}" \
  --worker-class gthread \
  --timeout 120 \
  --graceful-timeout 20 \
  --keep-alive 5 \
  --max-requests "${GUNICORN_MAX_REQUESTS:-2000}" \
  --max-requests-jitter 200
