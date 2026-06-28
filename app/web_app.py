import os

from flask import Flask

from app.jwt_service import JWTService
from app.routes import (
    analytics_blueprint,
    auth_blueprint,
    data_blueprint,
    reports_blueprint,
    schemas_blueprint,
    workspaces_blueprint,
)
from app.routes.auth import register_auth_hooks


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "change-me")
    app.config["SECRET_KEY"] = app.secret_key
    app.extensions["jwt_service"] = JWTService(secret_key=app.secret_key)

    register_auth_hooks(app)

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(schemas_blueprint)
    app.register_blueprint(data_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(reports_blueprint)

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
