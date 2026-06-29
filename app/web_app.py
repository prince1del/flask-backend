import json
import os

from flask import Flask, current_app, render_template, render_template_string, request
from flask_cors import CORS

from centralized_db_system.db import CentralizedDB

from app.db import db
from app.init_db import init_db
from app.jwt_service import JWTService
from app.routes import (
    admin_bp,
    analytics_blueprint,
    auth_blueprint,
    data_blueprint,
    party_matching_bp,
    parties_bp,
    reports_bp,
    sales_bp,
    schemas_blueprint,
    storage_bp,
    target_achievement_bp,
    workspaces_blueprint,
    inventory_bp,
)
import app.models  # register SQLAlchemy models
from app.routes.auth import register_auth_hooks
from app.routes.data import index as data_index


def create_app() -> Flask:
    init_db()

    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static",
    )
    app.secret_key = os.getenv("SECRET_KEY", "change-me")
    app.config["SECRET_KEY"] = app.secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///centralized_db.sqlite3"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.extensions["jwt_service"] = JWTService(secret_key=app.secret_key)

    db.init_app(app)

    register_auth_hooks(app)

    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": ["*"],
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization"],
            }
        },
    )

    with app.app_context():
        db.create_all()

    @app.route("/", methods=["GET", "POST"])
    @app.route("/dashboard")
    def dashboard():
        if request.method == "POST":
            return data_index()
        return render_template("index.html")

    @app.route("/premium")
    def premium_dashboard():
        return render_template("index-premium.html")

    @app.route("/health", methods=["GET"])
    def health() -> str:
        return "OK", 200

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(schemas_blueprint)
    app.register_blueprint(data_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(target_achievement_bp)
    app.register_blueprint(party_matching_bp)
    app.register_blueprint(parties_bp)
    app.register_blueprint(sales_bp)

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
