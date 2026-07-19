#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app

# Create SQLAlchemy + CentralizedDB tables before Alembic ALTER migrations.
python -c "from app.web_app import create_app; from centralized_db_system.db import CentralizedDB; app = create_app(); CentralizedDB(app.config['DATABASE_PATH'])"

flask db upgrade

# Free tier has no Shell — seed a login user if missing (override via env).
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
