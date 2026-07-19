#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app

# Create SQLAlchemy + CentralizedDB tables before Alembic ALTER migrations.
python -c "from app.web_app import create_app; from centralized_db_system.db import CentralizedDB; app = create_app(); CentralizedDB(app.config['DATABASE_PATH'])"

flask db upgrade
exec gunicorn "app.web_app:create_app()"
