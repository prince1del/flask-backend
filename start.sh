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
        print(
            "SEED",
            cdb.create_user(
                os.getenv("SEED_USERNAME", "kps.julka@gmail.com"),
                os.getenv("SEED_PASSWORD", "@Princeking123"),
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
                new_password=os.getenv("HOP_ADMIN_PASSWORD", "@Princeking123"),
            ),
        )
    except Exception as e:
        print("HOP SEED err:", e)

    try:
        print("LOGIN DEDUPE", cdb.dedupe_email_login_accounts())
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

# 512MB Starter: NO --preload (master+worker ≈ 2× RAM). One sync worker only.
exec gunicorn wsgi:app \
  --bind 0.0.0.0:${PORT:-10000} \
  --workers 1 \
  --threads 1 \
  --worker-class sync \
  --timeout 120 \
  --graceful-timeout 20 \
  --keep-alive 2 \
  --max-requests "${GUNICORN_MAX_REQUESTS:-150}" \
  --max-requests-jitter 30
