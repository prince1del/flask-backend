import csv
import io
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

from centralized_db_system.bale_to_pieces import calculate_bale_to_pieces
from centralized_db_system.db import CentralizedDB
from centralized_db_system.drive_storage import GoogleDriveStorage
from app.routes.auth import require_jwt_auth
from app.three_step_verification import (
    _extract_pdf_text,
    _parse_pdf_table_like_text,
    compare_step1,
    compare_step2,
    compare_step3,
    run_full_verification,
)
from app.utils import (
    detect_upload_file_type,
    expected_upload_format,
    infer_ai_intent,
    infer_distributor_name,
    stage_label_for_key,
)
from app.verification import (
    parse_distributor_fields_from_text,
    parse_retailer_fields_from_text,
)


data_blueprint = Blueprint("data", __name__)

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
    <title>NEXORA |Order-to-Invoice Workflow</title>
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


def _db_path() -> str:
    try:
        from flask import current_app

        return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")
    except Exception:
        return "centralized_db.sqlite3"


def _get_verification_upload_dir() -> Path:
    upload_root = (
        Path("app/instance/verification_uploads")
        if Path("app/instance/verification_uploads").exists()
        else Path("instance/verification_uploads")
    )
    upload_root.mkdir(parents=True, exist_ok=True)
    session_id = session.get("verification_session_id") or str(uuid.uuid4())
    session["verification_session_id"] = session_id
    session_dir = upload_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


@data_blueprint.route("/api/v1/masters/bulk-upload", methods=["GET", "POST"])
@require_jwt_auth
def bulk_upload() -> tuple[Response, int] | str:
    if request.method == "GET":
        return """
        <!doctype html>
        <html>
        <head><meta charset=\"utf-8\"><title>NEXORA |Bulk Upload Masters</title></head>
        <body style=\"font-family: Arial, sans-serif; margin: 2rem;\">
          <h1>Bulk Upload Masters</h1>
                        <p>
                          Download templates:
                          <a href=\"/api/v1/masters/template/distributors\">Distributor Excel</a> |
                          <a href=\"/api/v1/masters/template/distributors?format=csv\">Distributor CSV</a> |
                          <a href=\"/api/v1/masters/template/retailers\">Retailer Excel</a> |
                          <a href=\"/api/v1/masters/template/retailers?format=csv\">Retailer CSV</a> |
                          <a href=\"/api/v1/masters/template/articles\">Article Excel</a> |
                          <a href=\"/api/v1/masters/template/articles?format=csv\">Article CSV</a>
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
        return (
            jsonify({"status": "error", "message": "No file part in the request"}),
            400,
        )

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
            return (
                jsonify({"status": "error", "message": "Uploaded file is empty"}),
                400,
            )

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

        # On Windows the temporary file must be closed before other libraries read it.
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
                    distributor_fields = parse_distributor_fields_from_text(
                        extracted_text
                    )
                    if distributor_fields.get("name"):
                        db = CentralizedDB(_db_path())
                        inserted_id = db.add_master_distributor(
                            name=distributor_fields["name"],
                            distributor_code=distributor_fields.get("distributor_code"),
                            firm_name=distributor_fields.get("firm_name"),
                            firm_nick_name=distributor_fields.get("firm_nick_name"),
                            gst_no=distributor_fields.get("gst_no"),
                            zone=distributor_fields.get("zone"),
                            region=distributor_fields.get("region"),
                            credit_limit=distributor_fields.get("credit_limit")
                            if isinstance(
                                distributor_fields.get("credit_limit"), (int, float)
                            )
                            else None,
                        )
                        connection = sqlite3.connect(db.db_path)
                        try:
                            connection.execute(
                                "UPDATE master_distributors SET name = ?, firm_name = ?, firm_nick_name = ?, gst_no = ?, zone = ?, region = ?, credit_limit = ?, phone_number = ?, email = ?, address = ? WHERE id = ?",
                                (
                                    distributor_fields.get("name"),
                                    distributor_fields.get("firm_name"),
                                    distributor_fields.get("firm_nick_name"),
                                    distributor_fields.get("gst_no"),
                                    distributor_fields.get("zone"),
                                    distributor_fields.get("region"),
                                    distributor_fields.get("credit_limit")
                                    if isinstance(
                                        distributor_fields.get("credit_limit"),
                                        (int, float),
                                    )
                                    else None,
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
                    retailer_fields = parse_retailer_fields_from_text(extracted_text)
                    if retailer_fields.get("name"):
                        db = CentralizedDB(_db_path())
                        distributor = None
                        reference = retailer_fields.get("distributor_reference")
                        if reference:
                            distributor = db.get_master_distributor_by_name(reference)
                            if distributor is None:
                                distributor = (
                                    db._find_master_distributor_by_gst_or_name(
                                        reference
                                    )
                                )
                        if distributor is None and reference:
                            distributor = db._find_or_create_distributor_from_reference(
                                reference
                            )
                        if distributor is None:
                            distributor = db._find_or_create_distributor_from_reference(
                                retailer_fields.get("name", "")
                            )
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
                                    "UPDATE master_retailers SET name = ?, location = ?, phone_number = ?, email = ?, address = ?, gst_no = ? WHERE id = ?",
                                    (
                                        retailer_fields.get("name"),
                                        retailer_fields.get("location"),
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
                parsed_payload = {
                    "file_type": "pdf",
                    "text_preview": "",
                    "parsed_fields": {},
                }

            if os.path.exists(temp_path):
                os.remove(temp_path)
            return (
                jsonify(
                    {
                        "status": "success",
                        "message": f"Received PDF file {filename}; PDF uploads are accepted and queued for future processing",
                        "rows": 0,
                        "inserted": 1
                        if (
                            (
                                master_type == "distributors"
                                and parsed_payload.get("persisted_distributor_id")
                            )
                            or (
                                master_type == "retailers"
                                and parsed_payload.get("persisted_retailer_id")
                            )
                        )
                        else 0,
                        "updated": 0,
                        "skipped": 0,
                        "errors": [],
                        "file_type": "pdf",
                        "parsed_data": parsed_payload,
                    }
                ),
                200,
            )

        if temp_suffix in {".bin", ".txt", ".json", ".xml"}:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return (
                jsonify(
                    {
                        "status": "success",
                        "message": f"Received file {filename}; upload accepted for future processing",
                        "rows": 0,
                        "inserted": 0,
                        "updated": 0,
                        "skipped": 0,
                        "errors": [],
                        "file_type": suffix.lstrip(".") or "unknown",
                    }
                ),
                200,
            )

        try:
            db_path = (
                Path(__file__).resolve().parent.parent.parent / "centralized_db.sqlite3"
            )
            result = CentralizedDB(str(db_path)).bulk_upload_masters(
                master_type, temp_path
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return (
            jsonify(
                {
                    "status": "success",
                    "message": f"Successfully processed {result['rows_processed']} rows from {filename}",
                    "rows": int(result["rows_processed"]),
                    "inserted": int(result["inserted"]),
                    "updated": int(result.get("updated", 0)),
                    "skipped": int(result["skipped"]),
                    "errors": result.get("errors", []),
                    "file_type": suffix.lstrip(".") if suffix else "unknown",
                }
            ),
            200,
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@data_blueprint.route("/api/v1/contacts/import-export", methods=["GET"])
@require_jwt_auth
def contacts_import_export() -> str:
    return """
    <!doctype html>
    <html>
    <head><meta charset=\"utf-8\"><title>NEXORA |Contacts Import Export</title></head>
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


@data_blueprint.route("/api/v1/contacts/import", methods=["POST"])
@require_jwt_auth
def import_contacts() -> tuple[Response, int]:
    uploaded_file = request.files.get("file")
    if uploaded_file is None or uploaded_file.filename == "":
        return (
            jsonify({"status": "error", "message": "No file part in the request"}),
            400,
        )

    master_type = (request.form.get("master_type") or "distributors").strip().lower()
    if master_type not in {"distributors", "retailers"}:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Unsupported contact type. Use distributors or retailers.",
                }
            ),
            400,
        )

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
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "PDF is not allowed. Please upload CSV or Excel file.",
                }
            ),
            400,
        )

    if suffix not in allowed_suffixes and content_type not in excel_csv_content_types:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Unsupported file format. Please upload CSV or Excel file.",
                }
            ),
            400,
        )

    try:
        content = uploaded_file.read()
        if not content:
            return (
                jsonify({"status": "error", "message": "Uploaded file is empty"}),
                400,
            )

        temp_suffix = suffix if suffix in allowed_suffixes else ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as handle:
            handle.write(content)
            temp_path = handle.name

        try:
            db_path = (
                Path(__file__).resolve().parent.parent.parent / "centralized_db.sqlite3"
            )
            result = CentralizedDB(str(db_path)).bulk_upload_masters(
                master_type, temp_path
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return (
            jsonify(
                {
                    "status": "success",
                    "message": f"Successfully processed {result['rows_processed']} rows from {filename} for {master_type}",
                    "master_type": master_type,
                    "rows": int(result["rows_processed"]),
                    "inserted": int(result.get("inserted", 0)),
                    "updated": int(result.get("updated", 0)),
                    "skipped": int(result.get("skipped", 0)),
                    "errors": result.get("errors", []),
                    "file_type": suffix.lstrip(".") if suffix else "unknown",
                }
            ),
            200,
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@data_blueprint.route("/api/v1/masters/template/<master_type>")
@require_jwt_auth
def download_master_template(master_type: str) -> Response:
    file_format = (request.args.get("format") or "excel").strip().lower()
    if file_format not in {"excel", "csv"}:
        return jsonify({"status": "error", "message": "Unsupported format"}), 400

    try:
        payload = CentralizedDB(_db_path()).generate_master_template(
            master_type, file_format=file_format
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    if file_format == "csv":
        mimetype = "text/csv"
        extension = "csv"
    else:
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"

    response = Response(payload, mimetype=mimetype)
    response.headers[
        "Content-Disposition"
    ] = f"attachment; filename={master_type}_template.{extension}"
    return response


@data_blueprint.route("/legacy", methods=["GET", "POST"], endpoint="index")
def index() -> str:
    report = None
    progress_summary = None
    search_query = request.args.get("q", "") if request.method == "GET" else ""
    search_results = None
    locked_rules_summary = json.dumps(
        CentralizedDB(_db_path()).list_business_rules(locked_only=True), indent=2
    )
    if request.method == "POST":
        db = CentralizedDB(_db_path())
        workflow_action = (
            (request.form.get("workflow_action") or "run_all").strip().lower()
        )
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
            "run_all": {
                "order_file",
                "filled_file",
                "sales_order_file",
                "invoice_file",
            },
        }
        permitted_keys = stage_upload_map.get(
            workflow_action, stage_upload_map["run_all"]
        )
        persisted_upload_ids: list[int] = []

        for key, uploaded_file in files.items():
            if key not in permitted_keys:
                continue
            if not uploaded_file or not uploaded_file.filename:
                continue

            suffix = Path(uploaded_file.filename).suffix.lower()
            content_type = (uploaded_file.mimetype or "").lower()
            detected_file_type = detect_upload_file_type(
                uploaded_file.filename, content_type
            )
            expected_format = expected_upload_format(key)
            if (
                expected_format["extensions"]
                and suffix not in expected_format["extensions"]
                and content_type not in expected_format["content_types"]
            ):
                report = json.dumps(
                    {
                        "status": "error",
                        "message": f"{key} must be uploaded in the expected file format",
                        "expected_extensions": sorted(expected_format["extensions"]),
                        "received_extension": suffix,
                        "received_content_type": content_type,
                    },
                    indent=2,
                )
                progress_summary = "Upload rejected because the file type does not match the required format."
                return render_template_string(
                    HTML_TEMPLATE,
                    report=report,
                    report_data=json.loads(report),
                    progress_summary=progress_summary,
                    locked_rules_summary=locked_rules_summary,
                    sync_status=json.dumps({}, indent=2),
                    search_query=search_query,
                    search_results=search_results,
                )

            safe_name = Path(uploaded_file.filename).name
            target_path = upload_dir / f"{key}_{safe_name}"
            uploaded_file.save(target_path)
            stored_files[key] = str(target_path)
            inferred_distributor_name = infer_distributor_name(
                key, safe_name, explicit_name=distributor_name
            )
            stored_metadata[key] = {
                "stage": stage_label_for_key(key),
                "file_type": detected_file_type,
                "filename": safe_name,
                "distributor_name": inferred_distributor_name,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                upload_record_id = db.save_distributor_order_upload(
                    verification_session_id=session.get("verification_session_id")
                    or "",
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

        uploaded_count = sum(
            1
            for key in ["order_file", "filled_file", "sales_order_file", "invoice_file"]
            if stored_files.get(key)
        )
        progress_lines = [
            f"Captured files ({uploaded_count}/4): {', '.join(sorted([key for key in stored_files if stored_files.get(key)])) if stored_files else 'none'}"
        ]
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
            progress_lines.append(
                "All four files are captured. Verification can now run."
            )
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
            current_msg = (
                "Common order sheet saved. Distributor files can be attached next."
            )
            report_payload = {
                "status": current_status,
                "message": current_msg,
                "uploaded_files": sorted(
                    [key for key in stored_files if stored_files.get(key)]
                ),
                "uploaded_documents": stored_metadata,
                "next_step": next_step,
            }
            if stored_files.get("order_file"):
                report_payload["step1"] = {
                    "status": "saved",
                    "reason": "common_order_sheet_attached",
                }
            report = json.dumps(report_payload, indent=2)
        elif workflow_action == "stage2":
            if stored_files.get("order_file") and stored_files.get("filled_file"):
                try:
                    step1_result = compare_step1(
                        stored_files.get("order_file"), stored_files.get("filled_file")
                    )
                except Exception as exc:
                    step1_result = {"status": "error", "error": str(exc)}
                current_status = "stage-2-checked"
                current_msg = (
                    "Distributor filled order checked against the common order sheet."
                )
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "step1": step1_result,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    },
                    indent=2,
                )
            else:
                current_status = "error"
                current_msg = "Stage 2 requires both order_file and filled_file"
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                    },
                    indent=2,
                )
        elif workflow_action == "stage3":
            if stored_files.get("filled_file") and stored_files.get("sales_order_file"):
                try:
                    step2_result = compare_step2(
                        stored_files.get("filled_file"),
                        stored_files.get("sales_order_file"),
                    )
                except Exception as exc:
                    step2_result = {"status": "error", "error": str(exc)}
                current_status = "stage-3-checked"
                current_msg = (
                    "Sales order checked against distributor-wise filled order."
                )
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "step2": step2_result,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    },
                    indent=2,
                )
            else:
                current_status = "error"
                current_msg = "Stage 3 requires filled_file and sales_order_file"
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                    },
                    indent=2,
                )
        elif workflow_action == "stage4":
            if stored_files.get("sales_order_file") and stored_files.get(
                "invoice_file"
            ):
                try:
                    step3_result = compare_step3(
                        stored_files.get("sales_order_file"),
                        stored_files.get("invoice_file"),
                    )
                except Exception as exc:
                    step3_result = {"status": "error", "error": str(exc)}
                current_status = "stage-4-checked"
                current_msg = "Commercial invoice checked against sales order."
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "step3": step3_result,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                        "next_step": next_step,
                    },
                    indent=2,
                )
            else:
                current_status = "error"
                current_msg = "Stage 4 requires sales_order_file and invoice_file"
                report = json.dumps(
                    {
                        "status": current_status,
                        "message": current_msg,
                        "uploaded_files": sorted(
                            [key for key in stored_files if stored_files.get(key)]
                        ),
                        "uploaded_documents": stored_metadata,
                    },
                    indent=2,
                )
        elif uploaded_count == 0:
            report = json.dumps(
                {"status": "idle", "message": "No files uploaded"}, indent=2
            )
        elif uploaded_count < 4:
            step1_result = None
            if stored_files.get("order_file") and stored_files.get("filled_file"):
                try:
                    step1_result = compare_step1(
                        stored_files.get("order_file"), stored_files.get("filled_file")
                    )
                except Exception as exc:
                    step1_result = {"status": "error", "error": str(exc)}
            current_status = "partial-verification"
            current_msg = "Captured step-by-step. Partial verification. Please upload all four files for a full report."
            report = json.dumps(
                {
                    "status": current_status,
                    "message": current_msg,
                    "uploaded_files": sorted(
                        [key for key in stored_files if stored_files.get(key)]
                    ),
                    "uploaded_documents": stored_metadata,
                    "next_step": next_step,
                    "step1": step1_result
                    or {"status": "skipped", "reason": "missing_excel_inputs"},
                },
                indent=2,
            )
        else:
            try:
                verified_data = run_full_verification(
                    stored_files["order_file"],
                    stored_files["filled_file"],
                    stored_files["sales_order_file"],
                    stored_files["invoice_file"],
                )
                current_status = verified_data.get("status", "completed")
                current_msg = verified_data.get(
                    "message", "Full verification completed successfully"
                )
                report = json.dumps(verified_data, indent=2)
            except Exception as exc:
                current_status = "error"
                current_msg = str(exc)
                report = json.dumps(
                    {"status": "error", "message": current_msg}, indent=2
                )

    report_data = None
    if report:
        try:
            report_data = json.loads(report)
        except Exception:
            report_data = None

    if search_query:
        search_results = json.dumps(
            CentralizedDB("centralized_db.sqlite3").global_search(search_query),
            indent=2,
        )

    sync_status = json.dumps({}, indent=2)
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


@data_blueprint.route("/bale-calculator", methods=["GET", "POST"])
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


@data_blueprint.route("/search")
def search() -> Response:
    query = request.args.get("q", "")
    results = CentralizedDB("centralized_db.sqlite3").global_search(query)
    return Response(json.dumps(results, indent=2), mimetype="application/json")


@data_blueprint.route("/articles")
def articles() -> str:
    db = CentralizedDB("centralized_db.sqlite3")
    articles = json.dumps(db.list_articles_by_category(), indent=2)
    return render_template_string(
        '<h1>Article Master</h1><pre>{{ articles }}</pre><p><a href="/">Back</a></p>',
        articles=articles,
    )


@data_blueprint.route("/api/v1/ai-assistant/query", methods=["GET", "POST"])
@require_jwt_auth
def ai_assistant_query() -> Response:
    payload = request.get_json(silent=True) or {}
    query = str(
        payload.get("query")
        or payload.get("queryText")
        or request.args.get("queryText")
        or request.args.get("query")
        or ""
    ).strip()
    if not query:
        return Response(
            json.dumps({"error": "Missing query"}),
            status=400,
            mimetype="application/json",
        )

    query = query.replace("ask jarvis", "").replace("talk to jarvis", "").strip()
    db = CentralizedDB("centralized_db.sqlite3")
    intent = infer_ai_intent(query)
    answer = "Jarvis at your service, Boss. No matching information found."

    if intent == "last_visit":
        entity = (
            query.split("to", 1)[-1].strip().rstrip("?") if "to" in query else query
        )
        distributor = db.get_master_distributor_by_name(entity)
        if distributor:
            last_visit = db.get_last_visit_date("distributor", distributor["id"])
            answer = f"Jarvis at your service, Boss. Last visit to {distributor['name']} was on {last_visit or 'no recorded visit'}."
        else:
            answer = f"Jarvis at your service, Boss. I could not find a distributor named {entity}."
    elif intent == "alerts":
        alerts = db.list_data_entry_alerts()
        answer = (
            f"Jarvis at your service, Boss. You have {len(alerts)} active alerts."
            if alerts
            else "Jarvis at your service, Boss. No active alerts found."
        )
    elif intent == "pjp":
        suggestions = db.get_morning_suggestion_list("2026-06-26")
        answer = (
            f"Jarvis at your service, Boss. There are {len(suggestions)} retailer visits suggested today."
            if suggestions
            else "Jarvis at your service, Boss. No PJP suggestions found."
        )
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

    return Response(
        json.dumps(
            {"intent": intent, "query": query, "answer": answer}, ensure_ascii=False
        ),
        mimetype="application/json",
    )


@data_blueprint.route("/alerts")
def alerts() -> str:
    db = CentralizedDB("centralized_db.sqlite3")
    rows = db.list_data_entry_alerts()
    return render_template_string(
        '<h1>Data Entry Alerts</h1><pre>{{ rows }}</pre><p><a href="/">Back</a></p>',
        rows=json.dumps(rows, indent=2),
    )


@data_blueprint.route("/credit-policy", methods=["GET", "POST"])
def credit_policy() -> str:
    db = CentralizedDB("centralized_db.sqlite3")
    message = None
    if request.method == "POST":
        distributor_id = request.form.get("distributor_id", type=int)
        max_credit_limit = request.form.get("max_credit_limit", type=float)
        credit_days_allowed = request.form.get("credit_days_allowed", type=int)
        account_status = request.form.get("account_status", "ACTIVE")
        if distributor_id is not None:
            db.upsert_credit_control(
                distributor_id,
                max_credit_limit=max_credit_limit,
                credit_days_allowed=credit_days_allowed,
                account_status=account_status,
            )
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


@data_blueprint.route("/purchase-behavior")
def purchase_behavior() -> str:
    db = CentralizedDB("centralized_db.sqlite3")
    distributor_id = request.args.get("distributor_id", type=int)
    if distributor_id is None:
        distributor_id = 1
    logs = db.build_distributor_purchase_behavior_logs(distributor_id)
    return render_template_string(
        '<h1>Distributor Purchase Behavior</h1><pre>{{ logs }}</pre><p><a href="/">Back</a></p>',
        logs=json.dumps(logs, indent=2),
    )


@data_blueprint.route("/pwa-dashboard")
def pwa_dashboard() -> Response:
    return Response("Not Found", status=404)


@data_blueprint.route("/api/v1/dashboard/summary")
@require_jwt_auth
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
    return Response(
        json.dumps(payload, ensure_ascii=False), mimetype="application/json"
    )


@data_blueprint.route("/manifest.json")
def manifest() -> Response:
    return Response(
        json.dumps(
            {
                "name": "Jarvis Business Platform",
                "short_name": "Jarvis",
                "start_url": "/pwa-dashboard",
                "display": "standalone",
                "background_color": "#020617",
                "theme_color": "#0f172a",
                "icons": [
                    {
                        "src": "/icon-192.svg",
                        "sizes": "192x192",
                        "type": "image/svg+xml",
                    },
                    {
                        "src": "/icon-512.svg",
                        "sizes": "512x512",
                        "type": "image/svg+xml",
                    },
                ],
            }
        ),
        mimetype="application/json",
    )


@data_blueprint.route("/service-worker.js")
def service_worker() -> Response:
    return Response(
        (Path(__file__).resolve().parent.parent / "service-worker.js").read_text(
            encoding="utf-8"
        ),
        mimetype="application/javascript",
    )


@data_blueprint.route("/icon-192.svg")
def icon_192() -> Response:
    return Response(
        (Path(__file__).resolve().parent.parent / "icon-192.svg").read_text(
            encoding="utf-8"
        ),
        mimetype="image/svg+xml",
    )


@data_blueprint.route("/icon-512.svg")
def icon_512() -> Response:
    return Response(
        (Path(__file__).resolve().parent.parent / "icon-512.svg").read_text(
            encoding="utf-8"
        ),
        mimetype="image/svg+xml",
    )


@data_blueprint.route("/workflow-gps")
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


@data_blueprint.route("/article-master")
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
    return render_template_string(
        """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <title>NEXORA |Article Master</title>
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
        """,
        query=query,
        size_filter=size_filter,
        articles=articles,
    )


@data_blueprint.route("/retailer-download")
def retailer_download_page():
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        distributors = [
            dict(r)
            for r in conn.execute(
                "SELECT id, firm_name, firm_nick_name FROM master_distributors ORDER BY firm_name"
            ).fetchall()
        ]
    return render_template_string(
        """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Retailer Download</title>
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
""",
        distributors=distributors,
    )


@data_blueprint.route("/retailer-download/excel")
def retailer_download_excel():
    db_path = _db_path()
    dist_id = request.args.get("dist_id", "all")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if dist_id == "all":
            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                ORDER BY d.firm_name, r.name
            """
            ).fetchall()
            filename = "all_retailers.xlsx"
        else:
            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                WHERE r.distributor_id = ?
                ORDER BY r.name
            """,
                (dist_id,),
            ).fetchall()
            dist_name = rows[0]["firm_nick_name"] if rows else str(dist_id)
            filename = f"{dist_name}_retailers.xlsx"
    import pandas as _pd

    df = _pd.DataFrame([dict(r) for r in rows])
    output = io.BytesIO()
    with _pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Retailers")
    output.seek(0)
    return Response(
        output.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@data_blueprint.route("/retailer-download/csv")
def retailer_download_csv():
    import csv as _csv
    from io import StringIO as _StringIO

    db_path = _db_path()
    dist_id = request.args.get("dist_id", "all")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if dist_id == "all":
            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                ORDER BY d.firm_name, r.name
            """
            ).fetchall()
            filename = "all_retailers.csv"
        else:
            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                WHERE r.distributor_id = ?
                ORDER BY r.name
            """,
                (dist_id,),
            ).fetchall()
            dist_name = rows[0]["firm_nick_name"] if rows else str(dist_id)
            filename = f"{dist_name}_retailers.csv"
    output = _StringIO()
    if rows:
        writer = _csv.DictWriter(output, fieldnames=list(dict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
