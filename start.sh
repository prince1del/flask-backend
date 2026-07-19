#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app

# Create SQLAlchemy + CentralizedDB tables before Alembic ALTER migrations.
python -c "
import os, sys, traceback
print('boot: DATABASE_URL set=', bool((os.getenv('DATABASE_URL') or '').strip()))
print('boot: DATABASE_URL scheme=', ((os.getenv('DATABASE_URL') or '').split('://')[0] if os.getenv('DATABASE_URL') else 'none'))
try:
    import psycopg
    print('boot: psycopg=', psycopg.__version__)
except Exception as e:
    print('boot: psycopg import failed:', e)
try:
    from app.web_app import create_app
    from centralized_db_system.db import CentralizedDB
    app = create_app()
    print('SQLALCHEMY_DATABASE_URI=', (app.config.get('SQLALCHEMY_DATABASE_URI') or '')[:80])
    print('DATABASE_PATH=', app.config.get('DATABASE_PATH'))
    CentralizedDB(app.config['DATABASE_PATH'])
    print('CentralizedDB ready')
except Exception:
    traceback.print_exc()
    sys.exit(1)
"

# On Postgres, models come from create_all(); stamp head if alembic empty.
# On SQLite, run the normal upgrade chain.
python -c "
import sys, traceback
try:
    from app.web_app import create_app
    from sqlalchemy import inspect
    app = create_app()
    uri = (app.config.get('SQLALCHEMY_DATABASE_URI') or '')
    with app.app_context():
        from app.db import db
        from flask_migrate import upgrade, stamp
        tables = set(inspect(db.engine).get_table_names())
        if 'postgresql' in uri:
            if 'alembic_version' not in tables:
                stamp(revision='head')
                print('Postgres: stamped alembic head after create_all')
            else:
                try:
                    upgrade()
                    print('Postgres: flask db upgrade ok')
                except Exception as e:
                    print('Postgres: upgrade soft-fail:', e)
        else:
            upgrade()
            print('SQLite: flask db upgrade ok')
except Exception:
    traceback.print_exc()
    sys.exit(1)
"

# Seed a login user if missing (override via env).
python -c "
from app.web_app import create_app
from centralized_db_system.db import CentralizedDB
import os
app = create_app()
db = CentralizedDB(app.config['DATABASE_PATH'])
username = os.getenv('SEED_USERNAME', 'bd_gt_north_head')
password = os.getenv('SEED_PASSWORD', 'BdGtNorth@123')
role = os.getenv('SEED_ROLE', 'sales_executive')
workspace = os.getenv('SEED_WORKSPACE', 'bombay_dyeing_gt_north')
try:
    print('SEED', db.create_user(username, password, role=role, workspace_id=workspace))
except ValueError as e:
    print('SEED skip:', e)
except Exception as e:
    print('SEED err:', e)
"

exec gunicorn "app.web_app:create_app()"
