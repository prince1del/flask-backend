#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app

# One boot process only — calling create_app() 4× used to spike RAM and OOM on 512MB plans.
python - <<'PY'
import os
import sys
import traceback

print("boot: DATABASE_URL set=", bool((os.getenv("DATABASE_URL") or "").strip()))
print(
    "boot: DATABASE_URL scheme=",
    ((os.getenv("DATABASE_URL") or "").split("://")[0] if os.getenv("DATABASE_URL") else "none"),
)
try:
    import psycopg

    print("boot: psycopg=", psycopg.__version__)
except Exception as e:
    print("boot: psycopg import failed:", e)

try:
    from sqlalchemy import inspect
    from flask_migrate import upgrade, stamp
    from app.web_app import create_app
    from app.db import db
    from app.hop_schema import HOP_ROLE, HOP_WORKSPACE_ID, ensure_hop_schema
    from centralized_db_system.db import CentralizedDB

    app = create_app()
    print("SQLALCHEMY_DATABASE_URI=", (app.config.get("SQLALCHEMY_DATABASE_URI") or "")[:80])
    print("DATABASE_PATH=", app.config.get("DATABASE_PATH"))
    cdb = CentralizedDB(app.config["DATABASE_PATH"])
    print("CentralizedDB ready")

    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        if "postgresql" in uri:
            if "alembic_version" not in tables:
                stamp(revision="head")
                print("Postgres: stamped alembic head after create_all")
            else:
                try:
                    upgrade()
                    print("Postgres: flask db upgrade ok")
                except Exception as e:
                    print("Postgres: upgrade soft-fail:", e)
        else:
            upgrade()
            print("SQLite: flask db upgrade ok")

    # BD owner seed (idempotent)
    username = os.getenv("SEED_USERNAME", "kps.julka@gmail.com")
    password = os.getenv("SEED_PASSWORD", "@Princeking123")
    role = os.getenv("SEED_ROLE", "sales_executive")
    workspace = os.getenv("SEED_WORKSPACE", "bombay_dyeing_gt_north")
    try:
        print("SEED", cdb.create_user(username, password, role=role, workspace_id=workspace))
    except ValueError as e:
        print("SEED skip:", e)
    except Exception as e:
        print("SEED err:", e)

    try:
        print("BD login migrate:", cdb.migrate_bd_owner_login())
    except Exception as e:
        print("BD login migrate skip:", e)

    # HoP admin seed
    db_path = app.config["DATABASE_PATH"]
    ensure_hop_schema(db_path)
    hop_user = os.getenv("HOP_ADMIN_USERNAME", "hop_prizm")
    hop_pass = os.getenv("HOP_ADMIN_PASSWORD", "Prizm@2026!")
    try:
        print(
            "HOP SEED",
            CentralizedDB(db_path).create_user(
                hop_user, hop_pass, role=HOP_ROLE, workspace_id=HOP_WORKSPACE_ID
            ),
        )
    except ValueError as e:
        print("HOP SEED skip:", e)
    except Exception as e:
        print("HOP SEED err:", e)

except Exception:
    traceback.print_exc()
    sys.exit(1)
PY

# Starter = 512MB: single worker + preload (one create_app, less OOM than factory recycle).
# max-requests recycles memory; keep jitter so recycle is not synchronized with health pings.
exec gunicorn wsgi:app \
  --bind 0.0.0.0:${PORT:-10000} \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads "${WEB_THREADS:-2}" \
  --preload \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --max-requests 200 \
  --max-requests-jitter 50
