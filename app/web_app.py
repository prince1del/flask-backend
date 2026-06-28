import json
import os

from flask import Flask, current_app, render_template_string, request

from centralized_db_system.db import CentralizedDB

from app.init_db import init_db
from app.jwt_service import JWTService
from app.routes import (
    analytics_blueprint,
    auth_blueprint,
    data_blueprint,
    party_matching_bp,
    reports_blueprint,
    schemas_blueprint,
    storage_bp,
    target_achievement_bp,
    workspaces_blueprint,
)
from app.routes.auth import register_auth_hooks


def create_app() -> Flask:
    init_db()

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
    app.register_blueprint(storage_bp)
    app.register_blueprint(target_achievement_bp)
    app.register_blueprint(party_matching_bp)

    @app.route("/health", methods=["GET"])
    def health() -> str:
        return "OK", 200

    @app.route("/scheduler", methods=["GET", "POST"])
    def scheduler() -> str:
        current_date = request.args.get("current_date") or "2026-06-26"
        db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
        db = CentralizedDB(db_path)
        suggestions = json.dumps(db.get_morning_suggestion_list(current_date), indent=2)
        html = render_template_string(
            "<h1>Morning Suggestions</h1><pre>{{suggestions}}</pre><h2>Weekly PJP Planner</h2>",
            suggestions=suggestions,
        )
        return html

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
