import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, render_template_string, request
from flask_cors import CORS
from sqlalchemy import text


def load_env_file() -> None:
    root_path = Path(__file__).resolve().parent.parent
    env_file = root_path / '.env'
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or line.startswith('export '):
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


load_env_file()

from centralized_db_system.db import CentralizedDB

from flask_migrate import Migrate
from app.db import db
from app.init_db import init_db
from app.db_url import resolve_centralized_db_path, resolve_sqlalchemy_url
from app.jwt_service import JWTService
from app.version import APP_NAME, APP_VERSION
from app.routes import (
    admin_bp,
    analytics_blueprint,
    auth_blueprint,
    data_blueprint,
    finance_bp,
    gdrive_bp,
    intelligence_bp,
    order_sheets_bp,
    party_matching_bp,
    parties_bp,
    reports_bp,
    sales_bp,
    schemas_blueprint,
    storage_bp,
    target_achievement_bp,
    workspaces_blueprint,
    inventory_bp,
    business_bp,
    fulfillment_bp,
    operations_bp,
    phase2_8_bp,
    mappings_bp,
    company_profile_bp,
    masters_bp,
    executive_bp,
    hop_bp,
    dsr_market_bp,
    distributor_zone_bp,
    distributor_grievances_bp,
    personal_todos_bp,
    pjp_bp,
    ask_nexora_troubleshoot_bp,
    payment_collection_bp,
    call_lists_bp,
    ai_agent_bp,
)
import app.models  # register SQLAlchemy models
from app.routes.auth import register_auth_hooks
from app.routes.data import index as data_index
from app.routes.order_reconciliation_api import order_reconciliation_blueprint
from article_master_routes import article_master_bp
from filled_orders_routes import filled_orders_bp
from nexora_ask_routes import nexora_ask_bp


def _ensure_compatibility_columns(app: Flask) -> None:
    with app.app_context():
        inspector = db.inspect(db.engine)
        table_columns = {
            "users": {
                "email": "email VARCHAR(255)",
                "updated_at": "updated_at DATETIME",
                "full_name": "full_name VARCHAR(255)",
                "phone": "phone VARCHAR(20)",
                "status": "status VARCHAR(20) DEFAULT 'active'",
                "role": "role VARCHAR(50) DEFAULT 'unassigned'",
                "workspace_id": "workspace_id VARCHAR(100) DEFAULT 'default'",
                "gdrive_access_token": "gdrive_access_token TEXT",
                "gdrive_refresh_token": "gdrive_refresh_token TEXT",
                "gdrive_connected": "gdrive_connected INTEGER DEFAULT 0",
                "gdrive_email": "gdrive_email TEXT",
            },
            "distributors": {
                "uuid": "uuid VARCHAR(36)",
                "territory": "territory VARCHAR(100)",
                "pin_code": "pin_code VARCHAR(10)",
                "workspace_id": "workspace_id VARCHAR(100) DEFAULT 'default'",
                "created_by": "created_by INTEGER",
                "updated_at": "updated_at DATETIME",
            },
            "retailers": {
                "uuid": "uuid VARCHAR(36)",
                "distributor_id": "distributor_id INTEGER",
                "territory": "territory VARCHAR(100)",
                "pin_code": "pin_code VARCHAR(10)",
                "store_type": "store_type VARCHAR(50)",
                "workspace_id": "workspace_id VARCHAR(100) DEFAULT 'default'",
                "created_by": "created_by INTEGER",
                "updated_at": "updated_at DATETIME",
            },
            "sales_orders": {
                "created_by": "created_by INTEGER",
                "updated_at": "updated_at DATETIME",
            },
        }
        for table_name, columns in table_columns.items():
            if not inspector.has_table(table_name):
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            with db.engine.begin() as connection:
                for column_name, definition in columns.items():
                    if column_name not in existing_columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {definition}"))


def _ensure_filled_orders_schema(app: Flask) -> None:
    """Run filled-orders DDL + dedupe/unique-slot migration at startup."""
    import sqlite3

    import filled_orders_db as fodb

    db_path = app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    if not db_path:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        fodb.ensure_schema(conn)
    finally:
        conn.close()


def _ensure_article_master_schema(app: Flask) -> None:
    """Ensure article_master + brand_aliases tables exist (needed by global search)."""
    import sqlite3

    import article_master_db as amdb

    db_path = app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    if not db_path:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        amdb.ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


def create_app() -> Flask:
    init_db()

    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static",
    )
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY must be set in environment or .env file."
        )
    app.secret_key = secret_key
    app.config["SECRET_KEY"] = secret_key
    app.config["GEMINI_API_KEY"] = (os.getenv("GEMINI_API_KEY") or "").strip()
    app.config["NEXORA_ASK_LLM"] = (os.getenv("NEXORA_ASK_LLM") or "").strip()

    database_url = resolve_sqlalchemy_url(project_root=Path(__file__).resolve().parent.parent)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["DATABASE_PATH"] = resolve_centralized_db_path(
        project_root=Path(__file__).resolve().parent.parent
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.extensions["jwt_service"] = JWTService(secret_key=app.secret_key)

    db.init_app(app)
    Migrate(app, db)

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
        _ensure_compatibility_columns(app)
        db.create_all()
        _ensure_filled_orders_schema(app)
        _ensure_article_master_schema(app)
        try:
            cdb = CentralizedDB(app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
            result = cdb.migrate_bd_owner_login()
            app.logger.info("BD owner login migrate: %s", result)
        except Exception as exc:
            app.logger.warning("BD owner login migrate skipped: %s", exc)
        try:
            cdb = CentralizedDB(app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
            hop_login = cdb.ensure_hop_admin_login(
                old_username=os.getenv("HOP_ADMIN_OLD_USERNAME", "hop_prizm"),
                new_username=os.getenv("HOP_ADMIN_USERNAME", "prince1del"),
                new_password=os.getenv("HOP_ADMIN_PASSWORD", "@Princeking123"),
            )
            app.logger.info("HoP admin login ensure: %s", hop_login)
        except Exception as exc:
            app.logger.warning("HoP admin login ensure skipped: %s", exc)
        try:
            cdb = CentralizedDB(app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
            owner_user = os.getenv("WORKSPACE_OWNER_USERNAME", "kunwar1del").strip() or "kunwar1del"
            promote = cdb.promote_workspace_owner(owner_user)
            app.logger.info("Workspace supreme owner promote: %s", promote)
        except Exception as exc:
            app.logger.warning("Workspace owner promote skipped: %s", exc)
        try:
            cdb = CentralizedDB(app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
            dedupe = cdb.dedupe_email_login_accounts()
            if dedupe.get("changes"):
                app.logger.info("Login email dedupe: %s", dedupe)
            purge = cdb.delete_archived_duplicate_logins()
            if purge.get("deleted"):
                app.logger.info("Archived duplicate login purge: %s", purge)
        except Exception as exc:
            app.logger.warning("Login email dedupe skipped: %s", exc)
        try:
            cdb = CentralizedDB(app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
            cdb.ensure_login_identity_indexes()
        except Exception as exc:
            app.logger.warning("Login identity indexes skipped: %s", exc)

    @app.route("/", methods=["GET", "POST"])
    @app.route("/dashboard")
    def dashboard():
        if request.method == "POST":
            return data_index()
        return render_template("index.html")

    @app.route("/reports")
    def reports_landing():
        return render_template_string(
            """
            <!doctype html>
            <html>
            <head>
              <meta charset=\"utf-8\">
              <title>NEXORA | Reports</title>
              <style>
                body { font-family: Arial, sans-serif; margin: 2rem; background: #0f0f0f; color: #fff; }
                .card { max-width: 640px; background: #161616; border: 1px solid #2a2a2a; border-radius: 18px; padding: 2rem; }
                .badge { display: inline-block; padding: 0.35rem 0.6rem; background: #1d4ed8; border-radius: 999px; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em; }
                a { color: #93c5fd; text-decoration: none; }
              </style>
            </head>
            <body>
              <div class=\"card\">
                <div class=\"badge\">Reports</div>
                <h1>Coming Soon</h1>
                <p>Reports will appear here as soon as business data is available for this workspace.</p>
                <p><a href=\"/\">← Back to Dashboard</a></p>
              </div>
            </body>
            </html>
            """
        )

    @app.route("/premium")
    def premium_dashboard():
        return render_template("index-premium.html")

    @app.route("/health", methods=["GET", "HEAD"])
    def health():
        # Lightweight keep-alive target for UptimeRobot / Render — no DB work.
        return jsonify({"status": "ok", "service": "nexora"}), 200

    @app.route("/api/v1/app/version", methods=["GET"])
    def app_version():
        return jsonify({
            "success": True,
            "data": {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
            },
        })

    @app.route("/api/v1/app/update-metadata", methods=["GET"])
    def app_update_metadata():
        metadata_path = Path(__file__).resolve().parent / "update_metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as handle:
                metadata = json.load(handle)
        else:
            metadata = {
                "app_name": APP_NAME,
                "version": APP_VERSION,
                "release_notes": "No update metadata found.",
                "download_url": "",
            }
        return jsonify({
            "success": True,
            "data": metadata,
        })

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(schemas_blueprint)
    app.register_blueprint(data_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(business_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(gdrive_bp)
    app.register_blueprint(target_achievement_bp)
    app.register_blueprint(party_matching_bp)
    app.register_blueprint(parties_bp)
    app.register_blueprint(intelligence_bp)
    app.register_blueprint(order_sheets_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(fulfillment_bp)
    app.register_blueprint(operations_bp)
    app.register_blueprint(phase2_8_bp)
    app.register_blueprint(mappings_bp)
    app.register_blueprint(company_profile_bp)
    app.register_blueprint(masters_bp)
    app.register_blueprint(order_reconciliation_blueprint)
    app.register_blueprint(article_master_bp)
    app.register_blueprint(filled_orders_bp)
    app.register_blueprint(nexora_ask_bp)
    app.register_blueprint(executive_bp)
    app.register_blueprint(hop_bp)
    app.register_blueprint(dsr_market_bp)
    app.register_blueprint(distributor_zone_bp)
    app.register_blueprint(distributor_grievances_bp)
    app.register_blueprint(personal_todos_bp)
    app.register_blueprint(pjp_bp)
    app.register_blueprint(ask_nexora_troubleshoot_bp)
    app.register_blueprint(payment_collection_bp)
    app.register_blueprint(call_lists_bp)
    app.register_blueprint(ai_agent_bp)

    @app.route("/scheduler", methods=["GET", "POST"])
    def scheduler() -> str:
        current_date = (
            request.args.get("current_date")
            or datetime.now(timezone.utc).date().isoformat()
        )
        workspace_id = request.args.get("workspace_id") or "default"
        db_path = current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
        db = CentralizedDB(db_path)
        suggestions = json.dumps(
            db.get_morning_suggestion_list(current_date, workspace_id=workspace_id),
            indent=2,
        )
        html = render_template_string(
            "<h1>Morning Suggestions</h1><pre>{{suggestions}}</pre><h2>Weekly PJP Planner</h2>",
            suggestions=suggestions,
        )
        return html

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
