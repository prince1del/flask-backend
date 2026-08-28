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
        print("ARCHIVED PURGE", cdb.delete_archived_duplicate_logins())
    except Exception as e:
        print("LOGIN DEDUPE err:", e)

    try:
        cdb.ensure_login_identity_indexes()
    except Exception as e:
        print("LOGIN INDEX err:", e)

    # One-time, opt-in cleanup: delete mail-sync-imported Sales Orders whose
    # local PDF was lost to the web dyno's ephemeral disk (before Google
    # Drive backup was connected) so they can be genuinely re-fetched from
    # Gmail instead of being permanently stuck behind the duplicate check.
    # OFF by default — set RUN_SO_BACKLOG_CLEANUP=1 in the Render env vars,
    # redeploy once, then UNSET it (this must not run on every boot, or it
    # would delete every future mail-imported SO too).
    if os.getenv("RUN_SO_BACKLOG_CLEANUP") == "1":
        try:
            import sqlite3 as _sqlite3

            _conn = _sqlite3.connect(db_path)
            cdb.ensure_gmail_import_log_table()
            _rows = _conn.execute(
                "SELECT DISTINCT tracking_id, user_id, workspace_id FROM gmail_import_log "
                "WHERE kind = 'SO' AND outcome = 'auto_confirmed' AND tracking_id IS NOT NULL"
            ).fetchall()
            _conn.close()
            _deleted = 0
            _cleared: set[tuple[int, str]] = set()
            for _tid, _uid, _wsid in _rows:
                _wsid = _wsid or "default"
                if cdb.delete_order_lifecycle_tracking(int(_tid), workspace_id=_wsid, user_id=_uid) is not None:
                    _deleted += 1
                _cleared.add((_uid, _wsid))
            for _uid, _wsid in _cleared:
                cdb.clear_processed_gmail_messages(user_id=_uid, workspace_id=_wsid)
            print(
                f"SO BACKLOG CLEANUP: deleted {_deleted}/{len(_rows)} mail-imported SO tracking "
                f"records; cleared Gmail history for {len(_cleared)} user/workspace pair(s) "
                f"so the next 'Rescan all mail' genuinely re-fetches them"
            )
        except Exception as e:
            print("SO BACKLOG CLEANUP err:", e)

    # One-time, opt-in repair: auto_attach_so_to_filled_order() used to call
    # link_filled_order_to_tracking() BEFORE the match/merge computation,
    # not after — so a tracking_id whose match attempt failed partway
    # (exception swallowed by the function's own outer except) still got
    # permanently marked "linked" in filled_order_so_link, since that
    # insert commits immediately and unconditionally. A permanently
    # "linked" tracking_id is invisible to every future self-heal retry
    # (list_candidate_sales_orders_for_filled_order excludes it), even
    # though its FO<->SO match run was never actually created/updated —
    # this is exactly why Sain International's and Shri Ram & Co's freshly
    # re-imported SOs kept showing candidates=0 with stale old totals.
    # Fixed at the source (link now only happens after success) — this
    # just clears the already-bad links so the self-heal can retry them
    # for real. OFF by default — set RUN_FO_SO_LINK_REPAIR=1, redeploy
    # once, then unset it (idempotent either way: an already-correct link
    # just gets harmlessly re-created by the next self-heal pass).
    if os.getenv("RUN_FO_SO_LINK_REPAIR") == "1":
        try:
            import sqlite3 as _sqlite3

            _conn = _sqlite3.connect(db_path)
            cdb.ensure_gmail_import_log_table()
            _deleted_links = _conn.execute(
                "DELETE FROM filled_order_so_link WHERE order_lifecycle_tracking_id IN ("
                "  SELECT DISTINCT tracking_id FROM gmail_import_log "
                "  WHERE kind = 'SO' AND outcome = 'auto_confirmed' AND tracking_id IS NOT NULL"
                ")"
            ).rowcount
            _conn.commit()
            _conn.close()
            print(
                f"FO<->SO LINK REPAIR: cleared {_deleted_links} filled_order_so_link row(s) "
                f"for mail-imported SOs so the next self-heal (Order Desk load) retries them "
                f"for real instead of skipping them as already-linked"
            )
        except Exception as e:
            print("FO<->SO LINK REPAIR err:", e)

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
