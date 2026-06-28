import os

from flask import Flask

from app.routes import (
    analytics_blueprint,
    auth_blueprint,
    data_blueprint,
    reports_blueprint,
    schemas_blueprint,
    workspaces_blueprint,
    target_achievement_bp,
    party_matching_bp,
    storage_bp,
)
from app.routes.auth import register_auth_hooks


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "change-me")

    register_auth_hooks(app)

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(schemas_blueprint)
    app.register_blueprint(data_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(reports_blueprint)
    app.register_blueprint(target_achievement_bp)
    app.register_blueprint(party_matching_bp)
    app.register_blueprint(storage_bp)

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
