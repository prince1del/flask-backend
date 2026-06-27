import csv
import io
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.utils import secure_filename

from app.three_step_verification import _extract_pdf_text, _parse_pdf_table_like_text, compare_step1, compare_step2, compare_step3, run_full_verification


def _parse_distributor_fields_from_text(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        cleaned_value = value.strip()
        if not cleaned_value:
            continue
        if normalized_key in {"distributor_name", "name", "distributor"}:
            parsed["name"] = cleaned_value
        elif normalized_key in {"distributor_code", "dist_code", "code"}:
            parsed["distributor_code"] = cleaned_value
        elif normalized_key in {"firm_name", "firm"}:
            parsed["firm_name"] = cleaned_value
        elif normalized_key in {"firm_nick_name", "firm_nickname", "firm_nick_name_", "nickname", "nick_name"}:
            parsed["firm_nick_name"] = cleaned_value
        elif normalized_key in {"gstin", "gst_no", "gst_number"}:
            parsed["gst_no"] = cleaned_value
        elif normalized_key in {"zone", "region_name"}:
            parsed["zone"] = cleaned_value
        elif normalized_key in {"region", "area"}:
            parsed["region"] = cleaned_value
        elif normalized_key in {"credit_limit", "credit", "limit"}:
            try:
                parsed["credit_limit"] = float(cleaned_value)
            except (TypeError, ValueError):
                parsed["credit_limit"] = cleaned_value
        elif normalized_key in {"address", "street", "address_line"}:
            parsed["address"] = cleaned_value
        elif normalized_key in {"phone", "phone_number", "mobile", "contact_no", "contact_number"}:
            parsed["phone_number"] = cleaned_value
        elif normalized_key in {"email", "email_address"}:
            parsed["email"] = cleaned_value
    return parsed


def _parse_retailer_fields_from_text(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        cleaned_value = value.strip()
        if not cleaned_value:
            continue
        if normalized_key in {"retailer_name", "name", "retailer"} or normalized_key.endswith("retailer"):
            parsed["name"] = cleaned_value
        elif normalized_key in {"distributor", "linked_distributor", "linked_distributor_name", "distributor_name"} or normalized_key.endswith("distributor"):
            parsed["distributor_reference"] = cleaned_value
        elif normalized_key in {"location", "city", "place"}:
            parsed["location"] = cleaned_value
        elif normalized_key in {"address", "street", "address_line"}:
            parsed["address"] = cleaned_value
        elif normalized_key in {"phone", "phone_number", "mobile", "contact_no", "contact_number"}:
            parsed["phone_number"] = cleaned_value
        elif normalized_key in {"email", "email_address"}:
            parsed["email"] = cleaned_value
        elif normalized_key in {"gstin", "gst_no", "gst_number"}:
            parsed["gst_no"] = cleaned_value
    if not parsed.get("name") and lines:
        first_line = lines[0]
        if ":" in first_line:
            _, first_value = first_line.split(":", 1)
            parsed["name"] = first_value.strip()
    return parsed


from centralized_db_system.bale_to_pieces import calculate_bale_to_pieces
from centralized_db_system.db import CentralizedDB
from centralized_db_system.drive_storage import GoogleDriveStorage
from centralized_db_system.firebase_sync import FirebaseSync

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
    <title>Order-to-Invoice Workflow</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    form { margin-bottom: 2rem; }
    input[type=file] { margin-bottom: 1rem; display: block; }
    pre { background: #f5f5f5; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    .step-list { padding-left: 1.2rem; }
  </style>
</head>
<body>
    <h1>Order-to-Invoice Workflow</h1>
    <p>Common order sheet first aayegi, phir distributor-wise filled Excel, phir sales order PDF, aur last me commercial invoice PDF match hogi.</p>
    <p><strong>File rules:</strong> Order sheet aur distributor order hamesha Excel honge. Sales order aur commercial invoice hamesha PDF honge.</p>
        {% if locked_rules_summary %}
        <div class="card">
            <h2>Locked Business Rules</h2>
            <pre>{{ locked_rules_summary|safe }}</pre>
        </div>
        {% endif %}
    <div class="card">
        <h2>Workflow Stages</h2>
        <ol class="step-list">
            <li>Stage 1: Common order sheet upload and attach to all distributors (Excel)</li>
            <li>Stage 2: Distributor-wise filled order capture (Excel)</li>
            <li>Stage 3: Sales order match check (PDF)</li>
            <li>Stage 4: Commercial invoice match check (PDF)</li>
        </ol>
    </div>
  {% if progress_summary %}
    <div class="card">
      <h2>Verification Progress</h2>
      <pre>{{ progress_summary|safe }}</pre>
    </div>
  {% endif %}
  <form method="post" enctype="multipart/form-data" id="verification-form">
      <label>Distributor name (optional, for distributor-wise tracking)</label>
      <input type="text" name="distributor_name" placeholder="e.g. Alpha Traders" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;" />
        <label>Stage 1 - Common order sheet (Excel)</label>
        <input type="file" name="order_file" accept=".xlsx,.xls,.xlsm,.xlsb,.csv" onchange="updateFileLabel(this, 'order-label')">
    <div id="order-label">No file chosen</div>
        <label>Stage 2 - Distributor filled order (Excel)</label>
        <input type="file" name="filled_file" accept=".xlsx,.xls,.xlsm,.xlsb,.csv" onchange="updateFileLabel(this, 'filled-label')">
    <div id="filled-label">No file chosen</div>
        <label>Stage 3 - Sales order (PDF)</label>
        <input type="file" name="sales_order_file" accept=".pdf" onchange="updateFileLabel(this, 'sales-label')">
    <div id="sales-label">No file chosen</div>
        <label>Stage 4 - Commercial invoice (PDF)</label>
        <input type="file" name="invoice_file" accept=".pdf" onchange="updateFileLabel(this, 'invoice-label')">
    <div id="invoice-label">No file chosen</div>
        <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
            <button type="submit" name="workflow_action" value="stage1">Save Stage 1</button>
            <button type="submit" name="workflow_action" value="stage2">Check Stage 2</button>
            <button type="submit" name="workflow_action" value="stage3">Check Stage 3</button>
            <button type="submit" name="workflow_action" value="stage4">Check Stage 4</button>
            <button type="submit" name="workflow_action" value="run_all">Run Full Verification</button>
        </div>
  </form>
  <script>
    function updateFileLabel(input, targetId) {
      const target = document.getElementById(targetId);
      target.textContent = input.files && input.files.length ? input.files[0].name : 'No file chosen';
    }
  </script>

  {% if report %}
    <h2>Verification Report</h2>
    <pre>{{ report|safe }}</pre>
  {% endif %}
    {% if report_data and report_data.get('step1') and report_data.get('step1').get('input_summary') %}
        <div class="card">
            <h2>Step 1 Inferred Mapping</h2>
            {% for name, summary in report_data.get('step1').get('input_summary').items() %}
                <h3>{{ name|capitalize }} Excel</h3>
                <p><strong>Columns:</strong> {{ summary.get('columns') }}</p>
                <p><strong>Inferred:</strong></p>
                <pre>{{ summary.get('inferred_columns') }}</pre>
            {% endfor %}
        </div>
    {% endif %}

    {% if report_data and report_data.get('uploaded_documents') %}
        <div class="card">
            <h2>Recognized Uploads</h2>
            <pre>{{ report_data.get('uploaded_documents') }}</pre>
        </div>
    {% endif %}

  <div class="card">
    <h2>Global Search</h2>
    <form method="get" action="/">
      <input name="q" value="{{ search_query }}" placeholder="Search masters, visits, verification outputs, analytics" style="width: 100%; padding: 0.5rem;" />
      <button type="submit">Search</button>
    </form>
    {% if search_results %}
      <pre>{{ search_results|safe }}</pre>
    {% endif %}
  </div>
  <div class="card">
    <h2>Performance Analytics</h2>
    <p><a href="/analytics">Open analytics dashboard</a></p>
    {% if report %}
      <p><a href="/download/report">Download verification report</a></p>
    {% endif %}
  </div>
  <div class="card">
    <h2>Sync Status</h2>
    <pre>{{ sync_status|safe }}</pre>
  </div>
</body>
</html>
"""

ADMIN_DATABASE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Database Admin</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    form { margin-bottom: 1rem; }
    pre { background: #f5f5f5; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
    button { padding: 0.5rem 1rem; }
  </style>
</head>
<body>
  <h1>Database Admin</h1>
  <p>Manage backup, restore, and audit logs for the centralized database.</p>
  <div class="card">
    <h2>Backup</h2>
    <form method="post">
      <input type="hidden" name="action" value="backup" />
      <button type="submit">Create Backup</button>
    </form>
    {% if backup_message %}<p>{{ backup_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Restore</h2>
    <form method="post">
      <input type="hidden" name="action" value="restore" />
      <label>Backup file path</label>
      <input name="restore_path" value="instance/backups/centralized_db_backup.sqlite3" style="width: 100%; margin: 0.5rem 0;" />
      <button type="submit">Restore Database</button>
    </form>
    {% if restore_message %}<p>{{ restore_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Cleanup Temp Files</h2>
    <form method="post">
      <input type="hidden" name="action" value="cleanup" />
      <label>Directory</label>
      <input name="cleanup_dir" value="instance/verification_uploads" style="width: 100%; margin: 0.5rem 0;" />
      <button type="submit">Clean Stale Files</button>
    </form>
    {% if cleanup_message %}<p>{{ cleanup_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>Audit Logs</h2>
    <pre>{{ audit_logs }}</pre>
  </div>
  <p><a href="/">Back to dashboard</a></p>
</body>
</html>
"""

ANALYTICS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Analytics Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    pre { background: #f5f5f5; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>Analytics Dashboard</h1>
  <p>Performance tracking for masters, targets, and sales flow.</p>
  <div class="card">
    <h2>Overview</h2>
    <pre>{{ payload }}</pre>
  </div>
    <div class="card">
        <h2>Distributor Snapshot</h2>
        <p>Latest {{ distributors|length }} distributor records.</p>
        <div style="overflow-x:auto;">
            <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; min-width: 980px;">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Distributor Code</th>
                        <th>Firm Name</th>
                        <th>Firm nick name</th>
                        <th>Distributor Name</th>
                        <th>Mobile Number</th>
                        <th>Email id</th>
                        <th>Location</th>
                        <th>Distribution State</th>
                        <th>Distribution Area</th>
                        <th>GST Number</th>
                        <th>Payment Terms</th>
                        <th>Secondary Distributor</th>
                        <th>Secondary Distributor Phone</th>
                        <th>Secondary Distributor Birthday</th>
                        <th>Secondary Distributor Anniversary</th>
                        <th>Sales Executive</th>
                        <th>Sales Executive Phone</th>
                        <th>Sales Executive Email</th>
                        <th>Sales Executive Birthday</th>
                        <th>Sales Executive Anniversary</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in distributors %}
                    <tr>
                        <td>{{ item.id }}</td>
                        <td>{{ item.distributor_name or '' }}</td>
                        <td>{{ item.firm_name or '' }}</td>
                        <td>{{ item.firm_nick_name or '' }}</td>
                        <td>{{ item.name or '' }}</td>
                        <td>{{ item.phone_number or '' }}</td>
                        <td>{{ item.email or '' }}</td>
                        <td>{{ item.location or '' }}</td>
                        <td>{{ item.zone or '' }}</td>
                        <td>{{ item.region or '' }}</td>
                        <td>{{ item.gst_no or '' }}</td>
                        <td>{{ item.payment_terms or '' }}</td>
                        <td>{{ item.secondary_distributor_name or '' }}</td>
                        <td>{{ item.secondary_distributor_phone_number or '' }}</td>
                        <td>{{ item.secondary_distributor_birthday or '' }}</td>
                        <td>{{ item.secondary_distributor_anniversary or '' }}</td>
                        <td>{{ item.sales_executive_name or '' }}</td>
                        <td>{{ item.sales_executive_phone_number or '' }}</td>
                        <td>{{ item.sales_executive_email or '' }}</td>
                        <td>{{ item.sales_executive_birthday or '' }}</td>
                        <td>{{ item.sales_executive_anniversary or '' }}</td>
<td>{{ item.owner_name or '' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    <div class="card">
        <h2>Retailer Snapshot</h2>
        <p>Latest retailer records with contact and sales executive details.</p>
        <div style="overflow-x:auto;">
            <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; min-width: 1200px;">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Retailer Code</th>
                        <th>Retailer Name</th>
                        <th>Owner Name</th>
                        <th>Distributor</th>
                        <th>Location</th>
                        <th>Phone</th>
                        <th>Email</th>
                        <th>Address</th>
                        <th>GST</th>
                        <th>Secondary Retailer</th>
                        <th>Secondary Retailer Phone</th>
                        <th>Secondary Retailer Birthday</th>
                        <th>Secondary Retailer Anniversary</th>
                        <th>Sales Executive</th>
                        <th>Sales Executive Phone</th>
                        <th>Sales Executive Email</th>
                        <th>Sales Executive Birthday</th>
                        <th>Sales Executive Anniversary</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in retailers %}
                    <tr>
                        <td>{{ item.id }}</td>
                        <td>{{ item.retailer_code or '' }}</td>
                        <td>{{ item.name or '' }}</td>
                        <td>{{ item.owner_name or '' }}</td>
                        <td>{{ item.distributor_name or '' }}</td>
                        <td>{{ item.location or '' }}</td>
                        <td>{{ item.phone_number or '' }}</td>
                        <td>{{ item.email or '' }}</td>
                        <td>{{ item.address or '' }}</td>
                        <td>{{ item.gst_no or '' }}</td>
                        <td>{{ item.secondary_retailer_name or '' }}</td>
                        <td>{{ item.secondary_retailer_phone_number or '' }}</td>
                        <td>{{ item.secondary_retailer_birthday or '' }}</td>
                        <td>{{ item.secondary_retailer_anniversary or '' }}</td>
                        <td>{{ item.sales_executive_name or '' }}</td>
                        <td>{{ item.sales_executive_phone_number or '' }}</td>
                        <td>{{ item.sales_executive_email or '' }}</td>
                        <td>{{ item.sales_executive_birthday or '' }}</td>
                        <td>{{ item.sales_executive_anniversary or '' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
  <div class="card">
    <h2>Download Data</h2>
    <p><a href="/download/analytics">Download analytics JSON</a></p>
    <p><a href="/download/distributors">Download distributors CSV</a></p>
    <p><a href="/download/distributors/excel">Download distributors Excel</a></p>
    <p><a href="/download/distributors/pdf">Download distributors PDF</a></p>
    <p><a href="/download/retailers">Download retailers CSV</a></p>
    <p><a href="/download/targets">Download targets/achievements CSV</a></p>
    <p><a href="/download/primary-sales">Download primary sales CSV</a></p>
    <p><a href="/download/secondary-sales">Download secondary sales CSV</a></p>
  </div>
    <div class="card">
        <h2>Contact Master Tools</h2>
        <p><a href="/api/v1/contacts/import-export">Open contacts import/export</a> (CSV/Excel only)</p>
    </div>
  <p><a href="/">Back to verification</a></p>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sign In</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    form { max-width: 320px; display: grid; gap: 0.75rem; }
    input { padding: 0.6rem; }
    button { padding: 0.6rem 1rem; }
    .error { color: #b91c1c; }
  </style>
</head>
<body>
  <h1>Sign in</h1>
  <form method="post">
    <input name="username" placeholder="Username" required />
    <input name="password" type="password" placeholder="Password" required />
    <button type="submit">Sign In</button>
  </form>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
</body>
</html>
"""

SCHEDULER_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PJP & DSR Scheduler</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    pre { background: #f5f5f5; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>PJP & DSR Scheduler</h1>
  <div class="card">
    <h2>Morning Suggestions</h2>
    <pre>{{ suggestions }}</pre>
  </div>
  <div class="card">
    <h2>Weekly PJP Planner</h2>
    <form method="post" action="/scheduler">
      <label>Week Start Date</label>
      <input name="week_start_date" value="{{ current_date }}" />
      <label>Day of Week</label>
      <input name="day_of_week" value="Monday" />
      <label>Planned Distributors</label>
      <input name="planned_distributor_ids" placeholder="1,2" />
      <label>Planned Retailer IDs</label>
      <input name="planned_retailer_ids" placeholder="1,2" />
      <button type="submit">Save Weekly Plan</button>
    </form>
    {% if plan_message %}<p>{{ plan_message }}</p>{% endif %}
  </div>
  <div class="card">
    <h2>DSR Reports</h2>
    <form method="get" action="/scheduler">
      <label>From Date</label>
      <input name="from_date" value="{{ from_date }}" />
      <label>To Date</label>
      <input name="to_date" value="{{ to_date }}" />
      <button type="submit">Filter Reports</button>
    </form>
    <pre>{{ reports }}</pre>
    <p><a href="/download/dsr?report_id={{ report_id }}">Download latest DSR Excel</a></p>
  </div>
  <div class="card">
    <h2>Bale to Pieces Calculator</h2>
    <form method="post" action="/bale-calculator">
      <label>Total Bales</label>
      <input name="total_bales" type="number" value="1" />
      <label>Packs per Bale</label>
      <input name="packs_per_bale" type="number" value="1" />
      <label>Pieces per Pack</label>
      <input name="pcs_per_pack" type="number" value="1" />
      <label>Number of Designs</label>
      <input name="number_of_designs" type="number" value="1" />
      <label>Number of Colors</label>
      <input name="number_of_colors" type="number" value="1" />
      <button type="submit">Calculate</button>
    </form>
    {% if calculator_result %}<pre>{{ calculator_result }}</pre>{% endif %}
  </div>
  <p><a href="/">Back to verification</a></p>
</body>
</html>
"""


def _infer_ai_intent(query: str) -> str:
    normalized = (query or "").strip().lower()
    if any(term in normalized for term in ["retailers should i visit", "visit today", "which retailers", "pjp", "schedule", "today's visits", "visit list"]):
        return "pjp"
    if any(term in normalized for term in ["last visit", "visited", "visit to"]):
        return "last_visit"
    if any(term in normalized for term in ["mismatch", "alert", "price mismatch", "invoice"]):
        return "alerts"
    if any(term in normalized for term in ["top-selling", "top selling", "purchase", "trend", "this month"]):
        return "purchase_trends"
    return "search"


def _auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _expected_upload_format(key: str) -> dict[str, set[str]]:
    if key in {"order_file", "filled_file"}:
        return {
            "extensions": {".csv", ".xlsx", ".xls", ".xlsm", ".xlsb"},
            "content_types": {
                "text/csv",
                "application/csv",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
                "application/x-download",
                "application/x-unknown",
                "binary/octet-stream",
                "multipart/form-data",
                "text/plain",
                "application/xml",
            }
        }

    if key in {"sales_order_file", "invoice_file"}:
        return {
            "extensions": {".pdf"},
            "content_types": {"application/pdf", "application/octet-stream", "application/x-download", "binary/octet-stream"},
        }

    return {"extensions": set(), "content_types": set()}


def _stage_label_for_key(key: str) -> str:
    return {
        "order_file": "Stage 1 - Common order sheet",
        "filled_file": "Stage 2 - Distributor filled order",
        "sales_order_file": "Stage 3 - Sales order",
        "invoice_file": "Stage 4 - Commercial invoice",
    }.get(key, key)


def _detect_upload_file_type(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" or "pdf" in content_type:
        return "pdf"
    if suffix in {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"} or "excel" in content_type or "spreadsheet" in content_type:
        return "excel"
    return "unknown"


def _infer_distributor_name(upload_key: str, filename: str, explicit_name: str | None = None) -> str | None:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()
    if upload_key == "order_file":
        return "Common Order Sheet"

    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    if not stem:
        return None

    ignored_tokens = {
        "filled",
        "order",
        "sheet",
        "sales",
        "so",
        "invoice",
        "commercial",
        "stage1",
        "stage2",
        "stage3",
        "stage4",
    }
    tokens = [token for token in stem.split() if token.lower() not in ignored_tokens]
    candidate = " ".join(tokens).strip()
    return candidate or None


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "change-me")

    # ---- FIREBASE CLOUD CONNECTION CODE START ----
    fb_sync = None
    try:
        from centralized_db_system.firebase_sync import FirebaseSync
        fb_sync = FirebaseSync() 
        if fb_sync._client is not None:
            print("--- Firebase Cloud Server Connected Successfully! ---")
        else:
            print("--- Firebase Client loaded but inactive (Check Config) ---")
    except Exception as e:
        print(f"--- Firebase Connection Failed: {e} ---")
    # ---- FIREBASE CLOUD CONNECTION CODE END ----

    def _db_path() -> str:
        return str(app.config.get("DATABASE_PATH") or "centralized_db.sqlite3")

    def _get_verification_upload_dir() -> Path:
        upload_root = Path(app.instance_path) / "verification_uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        session_id = session.get("verification_session_id") or str(uuid.uuid4())
        session["verification_session_id"] = session_id
        session_dir = upload_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    @app.before_request
    def enforce_auth() -> Response | None:
        if not _auth_enabled():
            return None

        if request.path in {"/login", "/logout"}:
            return None
        if request.path.startswith("/static/"):
            return None
        if request.path in {"/manifest.json", "/service-worker.js", "/icon-192.svg", "/icon-512.svg"}:
            return None

        if session.get("authenticated"):
            return None

        return redirect(url_for("login"))

    @app.before_request
    def ensure_default_admin() -> None:
        if not _auth_enabled():
            return
        CentralizedDB().ensure_default_admin_user()

    @app.route("/api/v1/masters/bulk-upload", methods=["GET", "POST"])
    def bulk_upload() -> tuple[Response, int] | str:
        if request.method == "GET":
            return """
            <!doctype html>
            <html>
            <head><meta charset=\"utf-8\"><title>Bulk Upload Masters</title></head>
            <body style=\"font-family: Arial, sans-serif; margin: 2rem;\">
              <h1>Bulk Upload Masters</h1>
                            <p>
                              Download templates:
                              <a href="/api/v1/masters/template/distributors">Distributor Excel</a> |
                              <a href="/api/v1/masters/template/distributors?format=csv">Distributor CSV</a> |
                              <a href="/api/v1/masters/template/retailers">Retailer Excel</a> |
                              <a href="/api/v1/masters/template/retailers?format=csv">Retailer CSV</a> |
                              <a href="/api/v1/masters/template/articles">Article Excel</a> |
                              <a href="/api/v1/masters/template/articles?format=csv">Article CSV</a>
                            </p>
              <form method=\"post\" enctype=\"multipart/form-data\">
                <p><label>File <input type=\"file\" name=\"file\" required /></label></p>
                <p><label>Master Type
                  <select name=\"master_type\">
                    <option value=\"distributors\">Distributors</option>
                    <option value=\"retailers\">Retailers</option>
                  </select>
                </label></p>
                <p><button type=\"submit\">Upload</button></p>
              </form>
            </body>
            </html>
            """

        uploaded_file = request.files.get("file")
        if uploaded_file is None or uploaded_file.filename == "":
            return jsonify({"status": "error", "message": "No file part in the request"}), 400

        filename = uploaded_file.filename or ""
        master_type = (request.form.get("master_type") or "distributors").strip().lower()
        if master_type not in {"distributors", "retailers"}:
            return jsonify({"status": "error", "message": "Unsupported master type"}), 400

        suffix = Path(filename).suffix.lower()
        content_type = (uploaded_file.mimetype or "").lower()
        supported_suffixes = {".csv", ".xlsx", ".xls", ".xlsm", ".xlsb", ".pdf"}
        supported_content_types = {
            "text/csv",
            "application/csv",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/pdf",
            "application/octet-stream",
            "application/x-download",
            "application/x-unknown",
            "binary/octet-stream",
            "multipart/form-data",
            "text/plain",
            "application/xml",
        }

        try:
            content = uploaded_file.read()
            if not content:
                return jsonify({"status": "error", "message": "Uploaded file is empty"}), 400

            if suffix in supported_suffixes:
                temp_suffix = suffix or ".xlsx"
            elif "excel" in content_type or "spreadsheet" in content_type:
                temp_suffix = ".xlsx"
            elif "pdf" in content_type:
                temp_suffix = ".pdf"
            elif content_type in supported_content_types or suffix in supported_suffixes:
                temp_suffix = ".csv"
            else:
                temp_suffix = ".bin"

            with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as handle:
                handle.write(content)
                temp_path = handle.name

            if temp_suffix == ".pdf":
                try:
                    extracted_text = _extract_pdf_text(temp_path)
                    parsed_data = _parse_pdf_table_like_text(extracted_text)
                    parsed_payload = {
                        "file_type": "pdf",
                        "text_preview": extracted_text[:1000],
                        "parsed_fields": parsed_data,
                    }
                    if master_type == "distributors":
                        distributor_fields = _parse_distributor_fields_from_text(extracted_text)
                        if distributor_fields.get("name"):
                            db_path = Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
                            db = CentralizedDB(str(db_path))
                            inserted_id = db.add_master_distributor(
                                name=distributor_fields["name"],
                                distributor_code=distributor_fields.get("distributor_code"),
                                firm_name=distributor_fields.get("firm_name"),
                                firm_nick_name=distributor_fields.get("firm_nick_name"),
                                gst_no=distributor_fields.get("gst_no"),
                                zone=distributor_fields.get("zone"),
                                region=distributor_fields.get("region"),
                                credit_limit=distributor_fields.get("credit_limit") if isinstance(distributor_fields.get("credit_limit"), (int, float)) else None,
                            )
                            connection = sqlite3.connect(db.db_path)
                            try:
                                connection.execute(
                                    "UPDATE master_distributors SET phone_number = ?, email = ?, address = ? WHERE id = ?",
                                    (
                                        distributor_fields.get("phone_number"),
                                        distributor_fields.get("email"),
                                        distributor_fields.get("address"),
                                        inserted_id,
                                    ),
                                )
                                connection.commit()
                            finally:
                                connection.close()
                            parsed_payload["persisted_distributor_id"] = inserted_id
                    elif master_type == "retailers":
                        retailer_fields = _parse_retailer_fields_from_text(extracted_text)
                        if retailer_fields.get("name"):
                            db_path = Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
                            db = CentralizedDB(str(db_path))
                            distributor = None
                            reference = retailer_fields.get("distributor_reference")
                            if reference:
                                distributor = db.get_master_distributor_by_name(reference)
                                if distributor is None:
                                    distributor = db._find_master_distributor_by_gst_or_name(reference)
                            if distributor is None and reference:
                                distributor = db._find_or_create_distributor_from_reference(reference)
                            if distributor is None:
                                distributor = db._find_or_create_distributor_from_reference(retailer_fields.get("name", ""))
                            if distributor is not None:
                                inserted_id = db.add_master_retailer(
                                    name=retailer_fields["name"],
                                    distributor_id=distributor["id"],
                                    location=retailer_fields.get("location"),
                                    conn=None,
                                )
                                connection = sqlite3.connect(db.db_path)
                                try:
                                    connection.execute(
                                        "UPDATE master_retailers SET phone_number = ?, email = ?, address = ?, gst_no = ? WHERE id = ?",
                                        (
                                            retailer_fields.get("phone_number"),
                                            retailer_fields.get("email"),
                                            retailer_fields.get("address"),
                                            retailer_fields.get("gst_no"),
                                            inserted_id,
                                        ),
                                    )
                                    connection.commit()
                                finally:
                                    connection.close()
                                parsed_payload["persisted_retailer_id"] = inserted_id
                except Exception:
                    parsed_payload = {"file_type": "pdf", "text_preview": "", "parsed_fields": {}}

                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return jsonify({
                    "status": "success",
                    "message": f"Received PDF file {filename}; PDF uploads are accepted and queued for future processing",
                    "rows": 0,
                    "inserted": 1 if ((master_type == "distributors" and parsed_payload.get("persisted_distributor_id")) or (master_type == "retailers" and parsed_payload.get("persisted_retailer_id"))) else 0,
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                    "file_type": "pdf",
                    "parsed_data": parsed_payload,
                }), 200

            if temp_suffix in {".bin", ".txt", ".json", ".xml"}:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return jsonify({
                    "status": "success",
                    "message": f"Received file {filename}; upload accepted for future processing",
                    "rows": 0,
                    "inserted": 0,
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                    "file_type": suffix.lstrip(".") or "unknown",
                }), 200

            try:
                db_path = Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
                result = CentralizedDB(str(db_path)).bulk_upload_masters(master_type, temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            return jsonify({
                "status": "success",
                "message": f"Successfully processed {result['rows_processed']} rows from {filename}",
                "rows": int(result["rows_processed"]),
                "inserted": int(result["inserted"]),
                "updated": int(result.get("updated", 0)),
                "skipped": int(result["skipped"]),
                "errors": result.get("errors", []),
                "file_type": suffix.lstrip(".") if suffix else "unknown",
            }), 200
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/v1/contacts/import-export", methods=["GET"])
    def contacts_import_export() -> str:
        return """
        <!doctype html>
        <html>
        <head><meta charset=\"utf-8\"><title>Contacts Import Export</title></head>
        <body style=\"font-family: Arial, sans-serif; margin: 2rem;\">
                    <h1>Contacts Import/Export</h1>
                    <p>Use blank template download, fill contact details, and upload for insert/update for both distributors and retailers.</p>
          <p><strong>Allowed formats:</strong> CSV and Excel only (no PDF).</p>
                    <h2>Blank Template Download</h2>
          <p>
                        Distributor:
                        <a href=\"/api/v1/masters/template/distributors\">Excel</a> |
                        <a href=\"/api/v1/masters/template/distributors?format=csv\">CSV</a>
                    </p>
                    <p>
                        Retailer:
                        <a href=\"/api/v1/masters/template/retailers\">Excel</a> |
                        <a href=\"/api/v1/masters/template/retailers?format=csv\">CSV</a>
          </p>
          <h2>Current Data Export</h2>
          <p>
                        Distributor:
                        <a href=\"/download/distributors/excel\">Excel</a> |
                        <a href=\"/download/distributors\">CSV</a>
                    </p>
                    <p>
                        Retailer:
                        <a href=\"/download/retailers/excel\">Excel</a> |
                        <a href=\"/download/retailers\">CSV</a>
          </p>
          <h2>Upload Filled Sheet</h2>
          <form method=\"post\" action=\"/api/v1/contacts/import\" enctype=\"multipart/form-data\">
                        <p>
                            <label>Contact Type
                                <select name=\"master_type\">
                                    <option value=\"distributors\">Distributors</option>
                                    <option value=\"retailers\">Retailers</option>
                                </select>
                            </label>
                        </p>
            <p><label>File <input type=\"file\" name=\"file\" accept=\".csv,.xlsx,.xls,.xlsm,.xlsb\" required /></label></p>
            <p><button type=\"submit\">Upload and Update Contacts</button></p>
          </form>
          <p><a href=\"/analytics\">Back to analytics</a></p>
        </body>
        </html>
        """

    @app.route("/api/v1/contacts/import", methods=["POST"])
    def import_contacts() -> tuple[Response, int]:
        uploaded_file = request.files.get("file")
        if uploaded_file is None or uploaded_file.filename == "":
            return jsonify({"status": "error", "message": "No file part in the request"}), 400

        master_type = (request.form.get("master_type") or "distributors").strip().lower()
        if master_type not in {"distributors", "retailers"}:
            return jsonify({"status": "error", "message": "Unsupported contact type. Use distributors or retailers."}), 400

        filename = uploaded_file.filename or ""
        suffix = Path(filename).suffix.lower()
        content_type = (uploaded_file.mimetype or "").lower()
        allowed_suffixes = {".csv", ".xlsx", ".xls", ".xlsm", ".xlsb"}
        excel_csv_content_types = {
            "text/csv",
            "application/csv",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
            "application/x-download",
            "application/x-unknown",
            "binary/octet-stream",
            "multipart/form-data",
            "text/plain",
            "application/xml",
        }

        if suffix == ".pdf" or "pdf" in content_type:
            return jsonify({"status": "error", "message": "PDF is not allowed. Please upload CSV or Excel file."}), 400

        if suffix not in allowed_suffixes and content_type not in excel_csv_content_types:
            return jsonify({"status": "error", "message": "Unsupported file format. Please upload CSV or Excel file."}), 400

        try:
            content = uploaded_file.read()
            if not content:
                return jsonify({"status": "error", "message": "Uploaded file is empty"}), 400

            temp_suffix = suffix if suffix in allowed_suffixes else ".xlsx"
            with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as handle:
                handle.write(content)
                temp_path = handle.name

            try:
                db_path = Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
                result = CentralizedDB(str(db_path)).bulk_upload_masters(master_type, temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            return jsonify({
                "status": "success",
                "message": f"Successfully processed {result['rows_processed']} rows from {filename} for {master_type}",
                "master_type": master_type,
                "rows": int(result["rows_processed"]),
                "inserted": int(result.get("inserted", 0)),
                "updated": int(result.get("updated", 0)),
                "skipped": int(result.get("skipped", 0)),
                "errors": result.get("errors", []),
                "file_type": suffix.lstrip(".") if suffix else "unknown",
            }), 200
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Response:
        if not _auth_enabled():
            return redirect("/")

        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if CentralizedDB().authenticate_user(username, password):
                session["authenticated"] = True
                session["username"] = username
                return redirect(request.args.get("next") or "/")
            error = "Invalid username or password"

        return render_template_string(LOGIN_TEMPLATE, error=error)

    @app.route("/api/v1/masters/template/<master_type>")
    def download_master_template(master_type: str) -> Response:
        file_format = (request.args.get("format") or "excel").strip().lower()
        if file_format not in {"excel", "csv"}:
            return jsonify({"status": "error", "message": "Unsupported format"}), 400

        try:
            payload = CentralizedDB(_db_path()).generate_master_template(master_type, file_format=file_format)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        if file_format == "csv":
            mimetype = "text/csv"
            extension = "csv"
        else:
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            extension = "xlsx"

        response = Response(payload, mimetype=mimetype)
        response.headers["Content-Disposition"] = f"attachment; filename={master_type}_template.{extension}"
        return response

    @app.route("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.route("/", methods=["GET", "POST"])
    def index() -> str:
        report = None
        progress_summary = None
        search_query = request.args.get("q", "") if request.method == "GET" else ""
        search_results = None
        locked_rules_summary = json.dumps(CentralizedDB(_db_path()).list_business_rules(locked_only=True), indent=2)
        if request.method == "POST":
            db = CentralizedDB(_db_path())
            workflow_action = (request.form.get("workflow_action") or "run_all").strip().lower()
            distributor_name = (request.form.get("distributor_name") or "").strip()
            files = {
                "order_file": request.files.get("order_file"),
                "filled_file": request.files.get("filled_file"),
                "sales_order_file": request.files.get("sales_order_file"),
                "invoice_file": request.files.get("invoice_file"),
            }
            upload_dir = _get_verification_upload_dir()
            stored_files = {}
            stored_metadata = dict(session.get("verification_file_metadata", {}))
            for key, stored_path in session.get("verification_files", {}).items():
                if stored_path and os.path.exists(stored_path):
                    stored_files[key] = stored_path

            if files.get("order_file") and files["order_file"].filename:
                stored_files = {}
                stored_metadata = {}

            stage_upload_map = {
                "stage1": {"order_file"},
                "stage2": {"order_file", "filled_file"},
                "stage3": {"filled_file", "sales_order_file"},
                "stage4": {"sales_order_file", "invoice_file"},
                "run_all": {"order_file", "filled_file", "sales_order_file", "invoice_file"},
            }
            permitted_keys = stage_upload_map.get(workflow_action, stage_upload_map["run_all"])
            persisted_upload_ids: list[int] = []

            for key, uploaded_file in files.items():
                if key not in permitted_keys:
                    continue
                if not uploaded_file or not uploaded_file.filename:
                    continue

                suffix = Path(uploaded_file.filename).suffix.lower()
                content_type = (uploaded_file.mimetype or "").lower()
                detected_file_type = _detect_upload_file_type(uploaded_file.filename, content_type)
                expected_format = _expected_upload_format(key)
                if expected_format["extensions"] and suffix not in expected_format["extensions"] and content_type not in expected_format["content_types"]:
                    report = json.dumps({
                        "status": "error",
                        "message": f"{key} must be uploaded in the expected file format",
                        "expected_extensions": sorted(expected_format["extensions"]),
                        "received_extension": suffix,
                        "received_content_type": content_type,
                    }, indent=2)
                    progress_summary = "Upload rejected because the file type does not match the required format."
                    
                    if fb_sync and fb_sync._client is not None:
                        fb_sync.sync_verification_status({"status": "error", "message": f"{key} upload failed format rule", "next_step": "Retry"})
                        
                    return render_template_string(
                        HTML_TEMPLATE,
                        report=report,
                        report_data=json.loads(report),
                        progress_summary=progress_summary,
                        locked_rules_summary=locked_rules_summary,
                        sync_status=json.dumps(fb_sync.get_sync_status() if fb_sync else {}, indent=2),
                        search_query=search_query,
                        search_results=search_results,
                    )

                safe_name = secure_filename(Path(uploaded_file.filename).name)
                target_path = upload_dir / f"{key}_{safe_name}"
                uploaded_file.save(target_path)
                stored_files[key] = str(target_path)
                inferred_distributor_name = _infer_distributor_name(key, safe_name, explicit_name=distributor_name)
                stored_metadata[key] = {
                    "stage": _stage_label_for_key(key),
                    "file_type": detected_file_type,
                    "filename": safe_name,
                    "distributor_name": inferred_distributor_name,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    upload_record_id = db.save_distributor_order_upload(
                        verification_session_id=session.get("verification_session_id") or "",
                        distributor_name=inferred_distributor_name,
                        stage_key=key,
                        file_type=detected_file_type,
                        filename=safe_name,
                        file_path=str(target_path),
                        metadata=stored_metadata[key],
                    )
                    stored_metadata[key]["upload_record_id"] = upload_record_id
                    persisted_upload_ids.append(upload_record_id)
                except Exception as exc:
                    stored_metadata[key]["persistence_error"] = str(exc)

            session["verification_files"] = stored_files
            session["verification_file_metadata"] = stored_metadata
            session.modified = True

            uploaded_count = sum(1 for key in ["order_file", "filled_file", "sales_order_file", "invoice_file"] if stored_files.get(key))
            progress_lines = [f"Captured files ({uploaded_count}/4): {', '.join(sorted([key for key in stored_files if stored_files.get(key)])) if stored_files else 'none'}"]
            step_labels = {
                "order_file": "Order sheet",
                "filled_file": "Order filled",
                "sales_order_file": "Sales order",
                "invoice_file": "Commercial invoice",
            }
            next_step = None
            for key in ["order_file", "filled_file", "sales_order_file", "invoice_file"]:
                if not stored_files.get(key):
                    next_step = step_labels.get(key)
                    break
            if next_step:
                progress_lines.append(f"Next step: upload {next_step}")
            else:
                progress_lines.append("All four files are captured. Verification can now run.")
            if stored_metadata:
                readable_metadata = "; ".join(
                    f"{key} -> {meta.get('stage')} [{meta.get('file_type')}] {meta.get('filename')} ({meta.get('distributor_name') or 'unassigned'})"
                    for key, meta in sorted(stored_metadata.items())
                )
                progress_lines.append(f"Recognized uploads: {readable_metadata}")
            if persisted_upload_ids:
                progress_lines.append(f"Persisted upload records: {persisted_upload_ids}")
            progress_summary = "\n".join(progress_lines)

            current_status = "idle"
            current_msg = "No files uploaded"
            
            if workflow_action == "stage1":
                current_status = "stage-1-saved"
                current_msg = "Common order sheet saved. Distributor files can be attached next."
                report_payload = {
                    "status": current_status,
                    "message": current_msg,
                    "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]),
                    "uploaded_documents": stored_metadata,
                    "next_step": next_step,
                }
                if stored_files.get("order_file"):
                    report_payload["step1"] = {"status": "saved", "reason": "common_order_sheet_attached"}
                report = json.dumps(report_payload, indent=2)
            elif workflow_action == "stage2":
                if stored_files.get("order_file") and stored_files.get("filled_file"):
                    try:
                        step1_result = compare_step1(stored_files.get("order_file"), stored_files.get("filled_file"))
                    except Exception as exc:
                        step1_result = {"status": "error", "error": str(exc)}
                    current_status = "stage-2-checked"
                    current_msg = "Distributor filled order checked against the common order sheet."
                    report = json.dumps({
                        "status": current_status,
                        "message": current_msg,
                        "step1": step1_result,
                        "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    }, indent=2)
                else:
                    current_status = "error"
                    current_msg = "Stage 2 requires both order_file and filled_file"
                    report = json.dumps({"status": current_status, "message": current_msg, "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]), "uploaded_documents": stored_metadata}, indent=2)
            elif workflow_action == "stage3":
                if stored_files.get("filled_file") and stored_files.get("sales_order_file"):
                    try:
                        step2_result = compare_step2(stored_files.get("filled_file"), stored_files.get("sales_order_file"))
                    except Exception as exc:
                        step2_result = {"status": "error", "error": str(exc)}
                    current_status = "stage-3-checked"
                    current_msg = "Sales order checked against distributor-wise filled order."
                    report = json.dumps({
                        "status": current_status,
                        "message": current_msg,
                        "step2": step2_result,
                        "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    }, indent=2)
                else:
                    current_status = "error"
                    current_msg = "Stage 3 requires filled_file and sales_order_file"
                    report = json.dumps({"status": current_status, "message": current_msg, "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]), "uploaded_documents": stored_metadata}, indent=2)
            elif workflow_action == "stage4":
                if stored_files.get("sales_order_file") and stored_files.get("invoice_file"):
                    try:
                        step3_result = compare_step3(stored_files.get("sales_order_file"), stored_files.get("invoice_file"))
                    except Exception as exc:
                        step3_result = {"status": "error", "error": str(exc)}
                    current_status = "stage-4-checked"
                    current_msg = "Commercial invoice checked against sales order."
                    report = json.dumps({
                        "status": current_status,
                        "message": current_msg,
                        "step3": step3_result,
                        "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    }, indent=2)
                else:
                    current_status = "error"
                    current_msg = "Stage 4 requires sales_order_file and invoice_file"
                    report = json.dumps({"status": current_status, "message": current_msg, "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]), "uploaded_documents": stored_metadata}, indent=2)
            elif uploaded_count == 0:
                report = json.dumps({"status": "idle", "message": "No files uploaded"}, indent=2)
            elif uploaded_count < 4:
                step1_result = None
                if stored_files.get("order_file") and stored_files.get("filled_file"):
                    try:
                        step1_result = compare_step1(stored_files.get("order_file"), stored_files.get("filled_file"))
                    except Exception as exc:
                        step1_result = {"status": "error", "error": str(exc)}
                current_status = "partial-verification"
                current_msg = "Captured step-by-step. Partial verification. Please upload all four files for a full report."
                report = json.dumps({
                    "status": current_status,
                    "message": current_msg,
                    "uploaded_files": sorted([key for key in stored_files if stored_files.get(key)]),
                    "uploaded_documents": stored_metadata,
                    "next_step": next_step,
                    "step1": step1_result or {"status": "skipped", "reason": "missing_excel_inputs"},
                }, indent=2)
            else:
                try:
                    verified_data = run_full_verification(stored_files["order_file"], stored_files["filled_file"], stored_files["sales_order_file"], stored_files["invoice_file"])
                    current_status = verified_data.get("status", "completed")
                    current_msg = verified_data.get("message", "Full verification completed successfully")
                    report = json.dumps(verified_data, indent=2)
                except Exception as exc:
                    current_status = "error"
                    current_msg = str(exc)
                    report = json.dumps({"status": "error", "message": current_msg}, indent=2)

            if fb_sync and fb_sync._client is not None:
                fb_sync.sync_verification_status({
                    "status": current_status,
                    "message": current_msg,
                    "next_step": next_step or "All Completed",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })

        report_data = None
        if report:
            try:
                report_data = json.loads(report)
            except Exception:
                report_data = None

        if search_query:
            search_results = json.dumps(CentralizedDB("centralized_db.sqlite3").global_search(search_query), indent=2)

        sync_status = json.dumps(fb_sync.get_sync_status() if fb_sync else {"active": False}, indent=2)
        return render_template_string(
            HTML_TEMPLATE,
            report=report,
            report_data=report_data,
            progress_summary=progress_summary,
            locked_rules_summary=locked_rules_summary,
            sync_status=sync_status,
            search_query=search_query,
            search_results=search_results,
        )

    @app.route("/admin/database", methods=["GET", "POST"])
    def database_admin() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        backup_message = None
        restore_message = None
        cleanup_message = None
        audit_logs = "No audit logs found"

        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            if action == "backup":
                backup_path = db.backup_database(Path("instance") / "backups" / "centralized_db_backup.sqlite3")
                backup_message = f"Backup created at {backup_path}"
            elif action == "restore":
                restore_path = (request.form.get("restore_path") or "").strip()
                if restore_path:
                    try:
                        restored_path = db.restore_database(restore_path, overwrite=True)
                        restore_message = f"Restored database to {restored_path}"
                    except Exception as exc:
                        restore_message = f"Restore failed: {exc}"
                else:
                    restore_message = "Please provide a backup file path"
            elif action == "cleanup":
                cleanup_dir = (request.form.get("cleanup_dir") or "").strip() or "instance/verification_uploads"
                removed = db.cleanup_temp_uploads(cleanup_dir)
                cleanup_message = f"Removed {removed} stale files from {cleanup_dir}"

        logs = db.list_audit_logs(limit=20)
        audit_logs = json.dumps(logs, indent=2) if logs else "No audit logs found"
        return render_template_string(
            ADMIN_DATABASE_TEMPLATE,
            backup_message=backup_message,
            restore_message=restore_message,
            cleanup_message=cleanup_message,
            audit_logs=audit_logs,
        )

    @app.route("/analytics")
    def analytics() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        payload = json.dumps(db.get_dashboard_payload(), indent=2)
        distributors = db.list_master_distributors(limit=50)
        raw_retailers = db.list_master_retailers(limit=50)
        dist_id_to_name = {d['id']: d['firm_name'] for d in distributors}
        retailers = []
        for r in raw_retailers:
            r = dict(r)
            r['distributor_name'] = dist_id_to_name.get(r.get('distributor_id'), 'Unknown')
            retailers.append(r)
        return render_template_string(ANALYTICS_TEMPLATE, payload=payload, distributors=distributors, retailers=retailers)

    @app.route("/scheduler", methods=["GET", "POST"])
    def scheduler() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        current_date = request.args.get("current_date") or "2026-06-26"
        from_date = request.args.get("from_date") or current_date
        to_date = request.args.get("to_date") or current_date
        report_id = None
        plan_message = None

        if request.method == "POST":
            week_start_date = request.form.get("week_start_date") or current_date
            day_of_week = request.form.get("day_of_week") or "Monday"
            planned_distributor_ids = [int(item.strip()) for item in request.form.get("planned_distributor_ids", "").split(",") if item.strip()]
            planned_retailer_ids = [int(item.strip()) for item in request.form.get("planned_retailer_ids", "").split(",") if item.strip()]
            plan_id = db.create_weekly_pjp_plan(week_start_date, day_of_week, planned_distributor_ids, planned_retailer_ids)
            plan_message = f"Saved weekly plan {plan_id}"

        suggestions = json.dumps(db.get_morning_suggestion_list(current_date), indent=2)
        reports = json.dumps(db.list_dsr_reports_by_date_range(from_date, to_date), indent=2)
        latest_report = db.list_dsr_reports_by_date_range(from_date, to_date)
        if latest_report:
            report_id = latest_report[-1]["report_id"]

        return render_template_string(
            SCHEDULER_TEMPLATE,
            suggestions=suggestions,
            current_date=current_date,
            from_date=from_date,
            to_date=to_date,
            reports=reports,
            report_id=report_id,
            plan_message=plan_message,
        )

    @app.route("/download/analytics")
    def download_analytics() -> Response:
        db = CentralizedDB("centralized_db.sqlite3")
        payload = db.get_dashboard_payload()
        return Response(
            json.dumps(payload, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=analytics.json"},
        )

    @app.route("/download/report")
    def download_report() -> Response:
        report_text = request.args.get("report", "")
        return Response(
            report_text,
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=verification_report.txt"},
        )

    @app.route("/catalog/media")
    def catalog_media() -> str:
        storage = GoogleDriveStorage()
        metadata = storage.upload_file(Path("README.md"), "catalog-sample.jpg")
        return render_template_string(
            "<h1>Catalog media metadata</h1><pre>{{ metadata }}</pre>",
            metadata=json.dumps(metadata, indent=2),
        )

    @app.route("/download/distributors")
    def download_distributors() -> Response:
        csv_data = CentralizedDB("centralized_db.sqlite3").export_master_distributors()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=distributors.csv"},
        )

    @app.route("/download/distributors/excel")
    def download_distributors_excel() -> Response:
        excel_bytes = CentralizedDB("centralized_db.sqlite3").export_master_distributors_excel()
        return Response(
            excel_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=distributors.xlsx"},
        )

    @app.route("/download/distributors/pdf")
    def download_distributors_pdf() -> Response:
        pdf_bytes = CentralizedDB("centralized_db.sqlite3").export_master_distributors_pdf()
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": "attachment; filename=distributors.pdf"},
        )

    @app.route("/download/retailers")
    def download_retailers() -> Response:
        csv_data = CentralizedDB("centralized_db.sqlite3").export_master_retailers()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=retailers.csv"},
        )

    @app.route("/download/retailers/excel")
    def download_retailers_excel() -> Response:
        excel_bytes = CentralizedDB("centralized_db.sqlite3").export_master_retailers_excel()
        return Response(
            excel_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=retailers.xlsx"},
        )

    @app.route("/download/targets")
    def download_targets() -> Response:
        csv_data = CentralizedDB("centralized_db.sqlite3").export_targets_achievements()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=targets_achievements.csv"},
        )

    @app.route("/download/primary-sales")
    def download_primary_sales() -> Response:
        csv_data = CentralizedDB("centralized_db.sqlite3").export_primary_sales()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=primary_sales.csv"},
        )

    @app.route("/download/secondary-sales")
    def download_secondary_sales() -> Response:
        csv_data = CentralizedDB("centralized_db.sqlite3").export_secondary_sales()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=secondary_sales.csv"},
        )

    @app.route("/download/dsr")
    def download_dsr() -> Response:
        report_id = request.args.get("report_id", type=int)
        if report_id is None:
            return Response("Missing report_id", status=400)
        excel_bytes = CentralizedDB("centralized_db.sqlite3").export_dsr_report(report_id, export_format="excel")
        return Response(
            excel_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=dsr_report_{report_id}.xlsx"},
        )

    @app.route("/bale-calculator", methods=["GET", "POST"])
    def bale_calculator() -> str:
        calculator_result = None
        if request.method == "POST":
            payload = {
                "total_bales": request.form.get("total_bales", type=float),
                "packs_per_bale": request.form.get("packs_per_bale", type=float),
                "pcs_per_pack": request.form.get("pcs_per_pack", type=float),
                "number_of_designs": request.form.get("number_of_designs", type=float),
                "number_of_colors": request.form.get("number_of_colors", type=float),
            }
            calculator_result = json.dumps(calculate_bale_to_pieces(**payload), indent=2)
        return Response(calculator_result or "", mimetype="application/json")

    @app.route("/search")
    def search() -> Response:
        query = request.args.get("q", "")
        results = CentralizedDB("centralized_db.sqlite3").global_search(query)
        return Response(json.dumps(results, indent=2), mimetype="application/json")

    @app.route("/articles")
    def articles() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        articles = json.dumps(db.list_articles_by_category(), indent=2)
        return render_template_string(
            "<h1>Article Master</h1><pre>{{ articles }}</pre><p><a href=\"/\">Back</a></p>",
            articles=articles,
        )

    @app.route("/api/v1/ai-assistant/query", methods=["GET", "POST"])
    def ai_assistant_query() -> Response:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query") or payload.get("queryText") or request.args.get("queryText") or request.args.get("query") or "").strip()
        if not query:
            return Response(json.dumps({"error": "Missing query"}), status=400, mimetype="application/json")

        query = query.replace("ask jarvis", "").replace("talk to jarvis", "").strip()
        db = CentralizedDB("centralized_db.sqlite3")
        intent = _infer_ai_intent(query)
        answer = "Jarvis at your service, Boss. No matching information found."

        if intent == "last_visit":
            entity = query.split("to", 1)[-1].strip().rstrip("?") if "to" in query else query
            distributor = db.get_master_distributor_by_name(entity)
            if distributor:
                last_visit = db.get_last_visit_date("distributor", distributor["id"])
                answer = f"Jarvis at your service, Boss. Last visit to {distributor['name']} was on {last_visit or 'no recorded visit'}."
            else:
                answer = f"Jarvis at your service, Boss. I could not find a distributor named {entity}."
        elif intent == "alerts":
            alerts = db.list_data_entry_alerts()
            answer = f"Jarvis at your service, Boss. You have {len(alerts)} active alerts." if alerts else "Jarvis at your service, Boss. No active alerts found."
        elif intent == "pjp":
            suggestions = db.get_morning_suggestion_list("2026-06-26")
            answer = f"Jarvis at your service, Boss. There are {len(suggestions)} retailer visits suggested today." if suggestions else "Jarvis at your service, Boss. No PJP suggestions found."
        elif intent == "purchase_trends":
            distributor = db.get_master_distributor_by_name(query)
            if distributor:
                logs = db.build_distributor_purchase_behavior_logs(distributor["id"])
                answer = f"Jarvis at your service, Boss. Top behavior log for {distributor['name']}: {logs[0]['category_name'] if logs else 'no data'}."
            else:
                answer = "Jarvis at your service, Boss. I couldn't identify the distributor for purchase trend analysis."
        else:
            search_results = db.global_search(query)
            answer = f"Jarvis at your service, Boss. {json.dumps(search_results, ensure_ascii=False)}"

        return Response(json.dumps({"intent": intent, "query": query, "answer": answer}, ensure_ascii=False), mimetype="application/json")

    @app.route("/alerts")
    def alerts() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        rows = db.list_data_entry_alerts()
        return render_template_string(
            "<h1>Data Entry Alerts</h1><pre>{{ rows }}</pre><p><a href=\"/\">Back</a></p>",
            rows=json.dumps(rows, indent=2),
        )

    @app.route("/credit-policy", methods=["GET", "POST"])
    def credit_policy() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        message = None
        if request.method == "POST":
            distributor_id = request.form.get("distributor_id", type=int)
            max_credit_limit = request.form.get("max_credit_limit", type=float)
            credit_days_allowed = request.form.get("credit_days_allowed", type=int)
            account_status = request.form.get("account_status", "ACTIVE")
            if distributor_id is not None:
                db.upsert_credit_control(distributor_id, max_credit_limit=max_credit_limit, credit_days_allowed=credit_days_allowed, account_status=account_status)
                message = "Credit policy saved"
        policy_rows = db.list_credit_control()
        return render_template_string(
            """
            <h1>Credit Policy</h1>
            <form method=\"post\">
              <label>Distributor</label><input name=\"distributor_id\" type=\"number\" />
              <label>Max Credit Limit</label><input name=\"max_credit_limit\" type=\"number\" step=\"0.01\" />
              <label>Credit Days Allowed</label><input name=\"credit_days_allowed\" type=\"number\" />
              <label>Account Status</label><input name=\"account_status\" value=\"ACTIVE\" />
              <button type=\"submit\">Save</button>
            </form>
            {% if message %}<p>{{ message }}</p>{% endif %}
            <pre>{{ policy_rows }}</pre><p><a href=\"/\">Back</a></p>
            """,
            message=message,
            policy_rows=json.dumps(policy_rows, indent=2),
        )

    @app.route("/purchase-behavior")
    def purchase_behavior() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        distributor_id = request.args.get("distributor_id", type=int)
        if distributor_id is None:
            distributor_id = 1
        logs = db.build_distributor_purchase_behavior_logs(distributor_id)
        return render_template_string(
            "<h1>Distributor Purchase Behavior</h1><pre>{{ logs }}</pre><p><a href=\"/\">Back</a></p>",
            logs=json.dumps(logs, indent=2),
        )

    @app.route("/pwa-dashboard")
    def pwa_dashboard() -> Response:
        return Response((Path(__file__).parent / "pwa_dashboard.html").read_text(encoding="utf-8"), mimetype="text/html")

    @app.route("/api/v1/dashboard/summary")
    def dashboard_summary() -> Response:
        db = CentralizedDB("centralized_db.sqlite3")
        alerts = db.list_data_entry_alerts()
        tasks = db.list_workflow_todos_for_party(party_id=1, party_type="distributor")
        payload = {
            "overview": {
                "distributors": db.get_dashboard_payload()["masters"]["distributors"],
                "retailers": db.get_dashboard_payload()["masters"]["retailers"],
                "alerts": len(alerts),
                "tasks": len(tasks),
            },
            "suggestions": db.get_morning_suggestion_list("2026-06-26")[:3],
        }
        return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/json")

    @app.route("/manifest.json")
    def manifest() -> Response:
        return Response(json.dumps({
            "name": "Jarvis Business Platform",
            "short_name": "Jarvis",
            "start_url": "/pwa-dashboard",
            "display": "standalone",
            "background_color": "#020617",
            "theme_color": "#0f172a",
            "icons": [
                {"src": "/icon-192.svg", "sizes": "192x192", "type": "image/svg+xml"},
                {"src": "/icon-512.svg", "sizes": "512x512", "type": "image/svg+xml"},
            ],
        }), mimetype="application/json")

    @app.route("/service-worker.js")
    def service_worker() -> Response:
        return Response((Path(__file__).parent / "service-worker.js").read_text(encoding="utf-8"), mimetype="application/javascript")

    @app.route("/icon-192.svg")
    def icon_192() -> Response:
        return Response((Path(__file__).parent / "icon-192.svg").read_text(encoding="utf-8"), mimetype="image/svg+xml")

    @app.route("/icon-512.svg")
    def icon_512() -> Response:
        return Response((Path(__file__).parent / "icon-512.svg").read_text(encoding="utf-8"), mimetype="image/svg+xml")

    @app.route("/workflow-gps")
    def workflow_gps() -> str:
        db = CentralizedDB("centralized_db.sqlite3")
        party_id = request.args.get("party_id", type=int) or 1
        party_type = request.args.get("party_type", "distributor") or "distributor"
        tasks = db.list_workflow_todos_for_party(party_id=party_id, party_type=party_type)
        gps_logs = []
        with sqlite3.connect(db.db_path) as conn:
            rows = conn.execute(
                "SELECT log_id, visit_log_id, captured_latitude, captured_longitude, geofenced_status, device_timestamp, created_at FROM gps_visit_verification_logs ORDER BY log_id DESC LIMIT 20"
            ).fetchall()
            gps_logs = [
                {
                    "log_id": row[0],
                    "visit_log_id": row[1],
                    "captured_latitude": row[2],
                    "captured_longitude": row[3],
                    "geofenced_status": row[4],
                    "device_timestamp": row[5],
                    "created_at": row[6],
                }
                for row in rows
            ]
        return render_template_string(
            """
            <h1>Workflow & GPS Monitor</h1>
            <form method="get">
              <label>Party ID</label><input name="party_id" type="number" value="{{ party_id }}" />
              <label>Party Type</label><input name="party_type" value="{{ party_type }}" />
              <button type="submit">Load</button>
            </form>
            <h2>Checklist</h2>
            <pre>{{ tasks }}</pre>
            <h2>GPS Verification History</h2>
            <pre>{{ gps_logs }}</pre>
            <p><a href="/">Back</a></p>
            """,
            party_id=party_id,
            party_type=party_type,
            tasks=json.dumps(tasks, indent=2),
            gps_logs=json.dumps(gps_logs, indent=2),
        )

    @app.route("/reports")
    def monthly_reports() -> str:
        selected_month = request.args.get("month") or datetime.now().strftime("%Y-%m")
        data = _get_monthly_report_data(_db_path(), selected_month)
        return render_template_string(REPORTS_TEMPLATE, selected_month=selected_month, **data)

    @app.route("/reports/download/excel")
    def download_monthly_report_excel() -> Response:
        selected_month = request.args.get("month") or datetime.now().strftime("%Y-%m")
        data = _get_monthly_report_data(_db_path(), selected_month)
        df = pd.DataFrame(data["distributor_activity"])
        if df.empty:
            df = pd.DataFrame(columns=["distributor_name", "total_uploads", "stage1", "stage2", "stage3", "stage4", "last_upload"])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Monthly Report")
        output.seek(0)
        return Response(
            output.read(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=monthly_report_{selected_month}.xlsx"},
        )

    @app.route("/reports/download/csv")
    def download_monthly_report_csv() -> Response:
        selected_month = request.args.get("month") or datetime.now().strftime("%Y-%m")
        data = _get_monthly_report_data(_db_path(), selected_month)
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=["distributor_name", "total_uploads", "stage1", "stage2", "stage3", "stage4", "last_upload"])
        writer.writeheader()
        writer.writerows(data["distributor_activity"])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=monthly_report_{selected_month}.csv"},
        )
    @app.route("/article-master")
    def article_master_search() -> str:
        db_path = _db_path()
        query = request.args.get("q", "").strip()
        size_filter = request.args.get("size", "").strip()
        articles = []
        if query or size_filter:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                sql = "SELECT * FROM article_master_v2 WHERE 1=1"
                params = []
                if query:
                    sql += " AND (brand LIKE ? OR product LIKE ? OR print_style LIKE ?)"
                    params += [f"%{query}%", f"%{query}%", f"%{query}%"]
                if size_filter:
                    sql += " AND size = ?"
                    params.append(size_filter)
                sql += " ORDER BY brand, size"
                articles = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return render_template_string(ARTICLE_MASTER_TEMPLATE,
            query=query, size_filter=size_filter, articles=articles)


    @app.route('/retailer-download')
    def retailer_download_page():
        db_path = _db_path()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            distributors = [dict(r) for r in conn.execute('SELECT id, firm_name, firm_nick_name FROM master_distributors ORDER BY firm_name').fetchall()]
        return render_template_string(RETAILER_DOWNLOAD_TEMPLATE, distributors=distributors)

    @app.route('/retailer-download/excel')
    def retailer_download_excel():
        db_path = _db_path()
        dist_id = request.args.get('dist_id', 'all')
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if dist_id == 'all':
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    ORDER BY d.firm_name, r.name
                """).fetchall()
                filename = 'all_retailers.xlsx'
            else:
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    WHERE r.distributor_id = ?
                    ORDER BY r.name
                """, (dist_id,)).fetchall()
                dist_name = rows[0]['firm_nick_name'] if rows else str(dist_id)
                filename = f'{dist_name}_retailers.xlsx'
        import io as _io
        import pandas as _pd
        df = _pd.DataFrame([dict(r) for r in rows])
        output = _io.BytesIO()
        with _pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Retailers')
        output.seek(0)
        return Response(
            output.read(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/retailer-download/csv')
    def retailer_download_csv():
        import csv as _csv
        from io import StringIO as _StringIO
        db_path = _db_path()
        dist_id = request.args.get('dist_id', 'all')
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if dist_id == 'all':
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    ORDER BY d.firm_name, r.name
                """).fetchall()
                filename = 'all_retailers.csv'
            else:
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    WHERE r.distributor_id = ?
                    ORDER BY r.name
                """, (dist_id,)).fetchall()
                dist_name = rows[0]['firm_nick_name'] if rows else str(dist_id)
                filename = f'{dist_name}_retailers.csv'
        output = _StringIO()
        if rows:
            writer = _csv.DictWriter(output, fieldnames=list(dict(rows[0]).keys()))
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    # ============ SCHEMA MANAGER ROUTES ============

    SCHEMA_MANAGER_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Schema Manager</title>
<style>
body { font-family: Arial, sans-serif; margin: 2rem; }
.card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
.tabs { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.tab { padding: 0.5rem 1.2rem; border: 1px solid #ddd; border-radius: 4px; text-decoration: none; color: #333; }
.tab.active { background: #0d6efd; color: white; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f0f0f0; }
.btn { padding: 0.3rem 0.8rem; border: none; border-radius: 4px; cursor: pointer; }
.btn-danger { background: #dc3545; color: white; }
.btn-sm { padding: 0.2rem 0.5rem; font-size: 0.8rem; }
</style></head><body>
<h1>⚙️ Schema Manager</h1>
<div class="tabs">
<a href="/settings/schema?entity=distributor" class="tab {% if entity == 'distributor' %}active{% endif %}">Distributor</a>
<a href="/settings/schema?entity=retailer" class="tab {% if entity == 'retailer' %}active{% endif %}">Retailer</a>
<a href="/settings/schema?entity=article" class="tab {% if entity == 'article' %}active{% endif %}">Article</a>
</div>
{% if message %}<div style="background:#d4edda;padding:0.7rem;border-radius:4px;margin-bottom:1rem;">{{ message }}</div>{% endif %}
<div class="card">
<h2>{{ entity|capitalize }} Fields</h2>
<table><thead><tr><th>#</th><th>Field Name</th><th>Label</th><th>Type</th><th>Visible</th><th>Order</th><th>Actions</th></tr></thead>
<tbody>
{% for f in fields %}
<tr>
<td>{{ f.id }}</td><td>{{ f.field_name }}</td><td>{{ f.field_label }}</td>
<td>{{ f.field_type }}</td><td>{{ '👁' if f.is_visible else '🙈' }}</td>
<td>
<form method="post" action="/settings/schema/move" style="display:inline">
<input type="hidden" name="entity" value="{{ entity }}">
<input type="hidden" name="field_id" value="{{ f.id }}">
<input type="hidden" name="direction" value="up">
<button class="btn btn-sm" type="submit">▲</button>
</form>
<form method="post" action="/settings/schema/move" style="display:inline">
<input type="hidden" name="entity" value="{{ entity }}">
<input type="hidden" name="field_id" value="{{ f.id }}">
<input type="hidden" name="direction" value="down">
<button class="btn btn-sm" type="submit">▼</button>
</form>
</td>
<td>
<form method="post" action="/settings/schema/toggle" style="display:inline">
<input type="hidden" name="entity" value="{{ entity }}">
<input type="hidden" name="field_id" value="{{ f.id }}">
<input type="hidden" name="is_visible" value="{{ 0 if f.is_visible else 1 }}">
<button class="btn btn-sm" type="submit">{{ '🙈' if f.is_visible else '👁' }}</button>
</form>
<form method="post" action="/settings/schema/delete" style="display:inline" onsubmit="return confirm('Delete?')">
<input type="hidden" name="entity" value="{{ entity }}">
<input type="hidden" name="field_id" value="{{ f.id }}">
<button class="btn btn-danger btn-sm" type="submit">🗑</button>
</form>
</td>
</tr>
{% else %}
<tr><td colspan="7" style="text-align:center;">No fields. Click Load Default Schema.</td></tr>
{% endfor %}
</tbody></table>
</div>
<div class="card">
<h2>➕ Add New Field</h2>
<form method="post" action="/settings/schema/add">
<input type="hidden" name="entity" value="{{ entity }}">
<input name="field_name" placeholder="field_name" required />
<input name="field_label" placeholder="Label" required />
<select name="field_type"><option value="text">text</option><option value="number">number</option><option value="date">date</option></select>
<button class="btn" style="background:#0d6efd;color:white;" type="submit">Add</button>
</form>
</div>
<div class="card">
<form method="post" action="/settings/schema/seed">
<button class="btn" style="background:#6c757d;color:white;" type="submit">🔄 Load Default Schema</button>
</form>
</div>
<p><a href="/">← Back</a> | <a href="/analytics">Analytics</a></p>
</body></html>"""

    @app.route("/settings/schema")
    @app.route("/settings/schema/")
    def schema_manager():
        entity = request.args.get("entity", "distributor")
        message = request.args.get("message", "")
        db = CentralizedDB(_db_path())
        fields = db.get_all_schema_fields(entity)
        return render_template_string(SCHEMA_MANAGER_TEMPLATE, entity=entity, fields=fields, message=message)

    @app.route("/settings/schema/add", methods=["POST"])
    def schema_add_field():
        entity = request.form.get("entity", "distributor")
        field_name = request.form.get("field_name", "").strip()
        field_label = request.form.get("field_label", "").strip()
        field_type = request.form.get("field_type", "text")
        if field_name and field_label:
            db = CentralizedDB(_db_path())
            existing = db.get_all_schema_fields(entity)
            next_order = max([f["field_order"] for f in existing], default=-1) + 1
            db.add_schema_field(entity, field_name, field_label, field_type, next_order)
            message = f"Field '{field_label}' added!"
        else:
            message = "Field name and label required."
        return redirect(f"/settings/schema?entity={entity}&message={message}")

    @app.route("/settings/schema/delete", methods=["POST"])
    def schema_delete_field():
        entity = request.form.get("entity", "distributor")
        field_id = request.form.get("field_id", type=int)
        if field_id:
            CentralizedDB(_db_path()).delete_schema_field(field_id)
        return redirect(f"/settings/schema?entity={entity}&message=Field deleted")

    @app.route("/settings/schema/toggle", methods=["POST"])
    def schema_toggle_field():
        entity = request.form.get("entity", "distributor")
        field_id = request.form.get("field_id", type=int)
        is_visible = request.form.get("is_visible", type=int)
        if field_id is not None:
            CentralizedDB(_db_path()).toggle_schema_field_visibility(field_id, is_visible)
        return redirect(f"/settings/schema?entity={entity}&message=Visibility updated")

    @app.route("/settings/schema/move", methods=["POST"])
    def schema_move_field():
        entity = request.form.get("entity", "distributor")
        field_id = request.form.get("field_id", type=int)
        direction = request.form.get("direction", "up")
        db = CentralizedDB(_db_path())
        fields = db.get_all_schema_fields(entity)
        ids = [f["id"] for f in fields]
        if field_id in ids:
            idx = ids.index(field_id)
            if direction == "up" and idx > 0:
                ids[idx], ids[idx-1] = ids[idx-1], ids[idx]
            elif direction == "down" and idx < len(ids)-1:
                ids[idx], ids[idx+1] = ids[idx+1], ids[idx]
            db.reorder_schema_fields([{"id": fid, "order": i} for i, fid in enumerate(ids)])
        return redirect(f"/settings/schema?entity={entity}&message=Reordered")

    @app.route("/settings/schema/seed", methods=["POST"])
    def schema_seed():
        CentralizedDB(_db_path()).seed_default_schema()
        return redirect("/settings/schema?entity=distributor&message=Default schema loaded!")

    # ============ END SCHEMA MANAGER ROUTES ============
    return app

ARTICLE_MASTER_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Article Master</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 0.9rem; }
    th { background: #f0f0f0; }
    .search-box { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    .search-box input, .search-box select { padding: 0.5rem; font-size: 1rem; }
    .search-box button { padding: 0.5rem 1.5rem; background: #0d6efd; color: white; border: none; border-radius: 4px; cursor: pointer; }
    .badge { background: #e9ecef; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; }
  </style>
</head>
<body>
  <h1>📦 Article Master</h1>
  <div class="card">
    <form method="get" action="/article-master">
      <div class="search-box">
        <input type="text" name="q" value="{{ query }}" placeholder="Brand search karo... (e.g. Cardinal, Epigram)" style="width: 300px;" />
        <select name="size">
          <option value="">-- All Sizes --</option>
          {% for s in ['SB BS','DB BS','KS BS','KB FS','DB FS','DBL BS','DB Comf'] %}
          <option value="{{ s }}" {% if size_filter == s %}selected{% endif %}>{{ s }}</option>
          {% endfor %}
        </select>
        <button type="submit">🔍 Search</button>
        <a href="/article-master" style="padding: 0.5rem;">Clear</a>
      </div>
    </form>
  </div>
  {% if articles %}
  <div class="card">
    <p><strong>{{ articles|length }} articles मिले</strong></p>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr>
            <th>Brand</th><th>TC</th><th>Size</th><th>BS Size</th>
            <th>Product</th><th>Print Style</th><th>Bale</th><th>Colors</th>
            <th>MRP (₹)</th><th>Selling Price (₹)</th><th>PTR (₹)</th>
            <th>Retailer Margin</th><th>Ex-Mill (₹)</th>
          </tr>
        </thead>
        <tbody>
          {% for a in articles %}
          <tr>
            <td><strong>{{ a.brand }}</strong></td>
            <td><span class="badge">{{ a.tc }}</span></td>
            <td><span class="badge">{{ a.size }}</span></td>
            <td>{{ a.bs_size }}</td>
            <td>{{ a.product }}</td>
            <td>{{ a.print_style }}</td>
            <td>{{ a.bale_size }}</td>
            <td>{{ a.colors }}</td>
            <td><strong>₹{{ "%.0f"|format(a.mrp) }}</strong></td>
            <td>₹{{ "%.0f"|format(a.selling_price) }}</td>
            <td>₹{{ "%.0f"|format(a.ptr) }}</td>
            <td>{{ "%.0f"|format(a.retailer_margin * 100) }}%</td>
            <td>₹{{ "%.0f"|format(a.exmill_price) }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% elif query or size_filter %}
  <div class="card"><p>कोई article नहीं मिला।</p></div>
  {% else %}
  <div class="card"><p>Brand name या size search करो। उदाहरण: "Cardinal", "KS BS"</p></div>
  {% endif %}
  <p><a href="/">← Back</a> | <a href="/analytics">Analytics</a> | <a href="/reports">Reports</a></p>
</body>
</html>
"""

RETAILER_DOWNLOAD_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Retailer Download</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    .btn { display: inline-block; padding: 0.5rem 1.2rem; border-radius: 4px; text-decoration: none; margin: 0.3rem; font-size: 0.9rem; }
    .btn-excel { background: #1d6f42; color: white; }
    .btn-csv { background: #0d6efd; color: white; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f0f0f0; }
  </style>
</head>
<body>
  <h1>📥 Retailer Download</h1>

  <div class="card">
    <h2>All Retailers Download</h2>
    <p>Total 750 retailers — sabhi distributors ke saath</p>
    <a href="/retailer-download/excel?dist_id=all" class="btn btn-excel">📊 Excel Download (All)</a>
    <a href="/retailer-download/csv?dist_id=all" class="btn btn-csv">📄 CSV Download (All)</a>
  </div>

  <div class="card">
    <h2>Distributor Wise Download</h2>
    <table>
      <thead>
        <tr><th>Distributor</th><th>Nick Name</th><th>Excel</th><th>CSV</th></tr>
      </thead>
      <tbody>
        {% for d in distributors %}
        <tr>
          <td>{{ d.firm_name }}</td>
          <td>{{ d.firm_nick_name }}</td>
          <td><a href="/retailer-download/excel?dist_id={{ d.id }}" class="btn btn-excel">📊 Excel</a></td>
          <td><a href="/retailer-download/csv?dist_id={{ d.id }}" class="btn btn-csv">📄 CSV</a></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <p><a href="/">← Back</a> | <a href="/analytics">Analytics</a></p>
</body>
</html>
"""

REPORTS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Monthly Reports</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f0f0f0; }
    .summary-box { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
    .stat { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 1rem; min-width: 150px; text-align: center; }
    .stat h3 { margin: 0; font-size: 2rem; color: #0d6efd; }
    .stat p { margin: 0.25rem 0 0; color: #666; font-size: 0.9rem; }
  </style>
</head>
<body>
  <h1>📊 Monthly Reports</h1>
  <div class="card">
    <h2>Select Month</h2>
    <form method="get" action="/reports">
      <input type="month" name="month" value="{{ selected_month }}" style="padding: 0.4rem; margin: 0 1rem;" />
      <button type="submit">Load Report</button>
    </form>
  </div>
  <div class="card">
    <h2>Summary — {{ selected_month }}</h2>
    <div class="summary-box">
      <div class="stat"><h3>{{ total_uploads }}</h3><p>Total Uploads</p></div>
      <div class="stat"><h3>{{ total_distributors }}</h3><p>Active Distributors</p></div>
      <div class="stat"><h3>{{ verified_count }}</h3><p>Verified Orders</p></div>
      <div class="stat"><h3>{{ pending_count }}</h3><p>Pending Orders</p></div>
    </div>
  </div>
  <div class="card">
    <h2>Distributor Order Activity</h2>
    <div style="overflow-x:auto;">
      <table>
        <thead>
          <tr>
            <th>Distributor</th><th>Total Uploads</th><th>Stage 1</th>
            <th>Stage 2</th><th>Stage 3</th><th>Stage 4</th><th>Last Upload</th>
          </tr>
        </thead>
        <tbody>
          {% for row in distributor_activity %}
          <tr>
            <td>{{ row.distributor_name or 'Unknown' }}</td>
            <td>{{ row.total_uploads }}</td>
            <td>{{ row.stage1 }}</td>
            <td>{{ row.stage2 }}</td>
            <td>{{ row.stage3 }}</td>
            <td>{{ row.stage4 }}</td>
            <td>{{ row.last_upload }}</td>
          </tr>
          {% else %}
          <tr><td colspan="7" style="text-align:center;">No data for this month</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <h2>Download Report</h2>
    <p>
      <a href="/reports/download/excel?month={{ selected_month }}">📥 Download Excel</a> &nbsp;|&nbsp;
      <a href="/reports/download/csv?month={{ selected_month }}">📥 Download CSV</a>
    </p>
  </div>
  <p><a href="/">← Back to Dashboard</a> | <a href="/analytics">Analytics</a></p>
</body>
</html>
"""


def _get_monthly_report_data(db_path: str, month: str) -> dict:
    try:
        year, mon = month.split("-")
        start_date = f"{year}-{mon}-01"
        import calendar
        last_day = calendar.monthrange(int(year), int(mon))[1]
        end_date = f"{year}-{mon}-{last_day:02d}"
    except Exception:
        return {"distributor_activity": [], "total_uploads": 0, "total_distributors": 0, "verified_count": 0, "pending_count": 0}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            total_uploads = conn.execute(
                "SELECT COUNT(*) FROM distributor_order_uploads WHERE DATE(uploaded_at) BETWEEN ? AND ?",
                (start_date, end_date)
            ).fetchone()[0]
            rows = conn.execute("""
                SELECT distributor_name,
                    COUNT(*) as total_uploads,
                    SUM(CASE WHEN stage_key = 'order_file' THEN 1 ELSE 0 END) as stage1,
                    SUM(CASE WHEN stage_key = 'filled_file' THEN 1 ELSE 0 END) as stage2,
                    SUM(CASE WHEN stage_key = 'sales_order_file' THEN 1 ELSE 0 END) as stage3,
                    SUM(CASE WHEN stage_key = 'invoice_file' THEN 1 ELSE 0 END) as stage4,
                    MAX(DATE(uploaded_at)) as last_upload
                FROM distributor_order_uploads
                WHERE DATE(uploaded_at) BETWEEN ? AND ?
                AND distributor_name IS NOT NULL
                AND distributor_name != ''
                GROUP BY distributor_name
                ORDER BY total_uploads DESC
            """, (start_date, end_date)).fetchall()
            distributor_activity = [dict(row) for row in rows]
            total_distributors = len(distributor_activity)
            verified_count = sum(1 for r in distributor_activity if r["stage1"] and r["stage2"] and r["stage3"] and r["stage4"])
            pending_count = total_distributors - verified_count
    except Exception:
        distributor_activity = []
        total_uploads = total_distributors = verified_count = pending_count = 0
    return {
        "distributor_activity": distributor_activity,
        "total_uploads": total_uploads,
        "total_distributors": total_distributors,
        "verified_count": verified_count,
        "pending_count": pending_count,
    }
if __name__ == "__main__":
    app = create_app()
    app.run(debug=os.getenv('DEBUG','False')=='True', port=int(os.getenv('PORT',5000)))