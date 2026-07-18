web: sh -c "FLASK_APP=wsgi:app flask db upgrade && gunicorn \"app.web_app:create_app()\""
