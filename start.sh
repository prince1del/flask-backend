#!/usr/bin/env sh
set -e
export FLASK_APP=wsgi:app
flask db upgrade
exec gunicorn "app.web_app:create_app()"
