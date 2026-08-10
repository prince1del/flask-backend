import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)

from centralized_db_system.bale_to_pieces import calculate_bale_to_pieces
from centralized_db_system.db import CentralizedDB
from centralized_db_system.drive_storage import GoogleDriveStorage
from app.routes.auth import get_workspace_id, require_jwt_auth
from app.three_step_verification import (
    _extract_pdf_text,
    _parse_pdf_table_like_text,
    compare_step1,
    compare_step2,
    compare_step3,
    parse_step2_sales_order_pdf,
    parse_step3_invoice_pdf,
    run_full_verification,
)
from app.utils import (
    detect_upload_file_type,
    expected_upload_format,
    extract_party_name_candidate,
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
        <label>Order Sheet Name</label>
        <input type="text" name="order_sheet_name" placeholder="e.g. AW26 Bedsheet" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;" />
        <label>Order Sheet Category</label>
        <input type="text" name="order_sheet_category" placeholder="e.g. Bedsheet" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;" />
        <label>Order Sheet Active</label>
        <select name="order_sheet_is_active" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;">
            <option value="1" selected>Active</option>
            <option value="0">Inactive</option>
        </select>
        <label>Stage 1 - Common order sheet (Excel)</label>
        <input type="file" name="order_file" accept=".xlsx,.xls,.xlsm,.xlsb,.csv" onchange="updateFileLabel(this, 'order-label')">
    <div id="order-label">No file chosen</div>
        <label>Stage 2 - Distributor filled order (Excel)</label>
        <input type="file" name="filled_file" accept=".xlsx,.xls,.xlsm,.xlsb,.csv" onchange="updateFileLabel(this, 'filled-label')">
    <div id="filled-label">No file chosen</div>
        <label>Distributor for filled order</label>
        <select name="filled_file_distributor_id" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;">
            <option value="">Select distributor (recommended for Stage 2)</option>
            {% for distributor in distributor_options %}
                <option value="{{ distributor.id }}" {% if selected_filled_distributor_id == distributor.id|string %}selected{% endif %}>{{ distributor.firm_nick_name or distributor.name }} ({{ distributor.name }})</option>
            {% endfor %}
        </select>
        {% if suggested_filled_distributor_name %}
            <div class="card" style="background: #f8fafc; border-color: #93c5fd;">
                <strong>Suggested distributor:</strong> {{ suggested_filled_distributor_name }}
                {% if suggested_filled_distributor_name != selected_filled_distributor_name %}
                <div>Please confirm the distributor selection for this filled order.</div>
                {% endif %}
            </div>
        {% endif %}
        <label>Stage 3 - Sales order (PDF)</label>
        <input type="file" name="sales_order_file" accept=".pdf" onchange="updateFileLabel(this, 'sales-label')">
    <div id="sales-label">No file chosen</div>
        <label>Distributor for sales order</label>
        <select name="sales_order_distributor_id" style="margin-bottom: 1rem; display: block; width: 100%; max-width: 420px; padding: 0.4rem;">
            <option value="">Select distributor (recommended for Stage 3)</option>
            {% for distributor in distributor_options %}
                <option value="{{ distributor.id }}" {% if selected_sales_order_distributor_id == distributor.id|string %}selected{% endif %}>{{ distributor.firm_nick_name or distributor.name }} ({{ distributor.name }})</option>
            {% endfor %}
        </select>
        {% if sales_order_linking_summary %}
            <div class="card" style="background: #f8fafc; border-color: #93c5fd;">
                <strong>Sales Order Linking:</strong> {{ sales_order_linking_summary }}
            </div>
        {% endif %}
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

        configured_path = current_app.config.get("DATABASE_PATH")
        if configured_path:
            return str(configured_path)
    except Exception:
        configured_path = None

    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        return str(env_path)

    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("sqlite://"):
        sqlite_path = database_url.removeprefix("sqlite://")
        if sqlite_path.startswith("/") and len(sqlite_path) >= 3 and sqlite_path[2] == ":":
            sqlite_path = sqlite_path[1:]
        return sqlite_path

    return "centralized_db.sqlite3"


def _fingerprint_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    target_path = Path(path)
    if not target_path.exists() or not target_path.is_file():
        return None

    digest = hashlib.sha256()
    try:
        with target_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _names_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    a_norm = _normalize_text(a)
    b_norm = _normalize_text(b)
    return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm


def _build_sales_order_match_summary(
    match_distributor_name: str | None,
    suggested_distributor_name: str | None,
    buyer_name: str | None,
    selected_distributor_name: str | None,
) -> str | None:
    if match_distributor_name and selected_distributor_name:
        if _names_match(match_distributor_name, selected_distributor_name):
            return (
                f"Buyer Code and selected distributor both match {selected_distributor_name}."
            )
        return (
            f"Buyer Code suggests {match_distributor_name}, but selected distributor is {selected_distributor_name}."
        )
    if match_distributor_name and not selected_distributor_name:
        if buyer_name:
            return (
                f"Buyer Code suggests {match_distributor_name}, buyer name text is {buyer_name}. Please confirm manually."
            )
        return (
            f"Buyer Code suggests {match_distributor_name}. Please confirm the distributor manually."
        )
    if not match_distributor_name and selected_distributor_name:
        return (
            f"No distributor found from Buyer Code. Using selected distributor {selected_distributor_name}."
        )
    return None


def _normalize_upload_filename(filename: str) -> str:
    return " ".join(
        word.strip()
        for word in Path(filename).stem.replace("_", " ").replace("-", " ").split()
        if word.strip()
    ).lower()


def _suggest_filled_order_distributor(
    filename: str, workspace_id: str
) -> dict[str, Any] | None:
    if not filename or not workspace_id:
        return None
    stem = _normalize_upload_filename(filename)
    if not stem:
        return None

    db = CentralizedDB(_db_path())
    distributors = db.list_master_distributors(limit=200, workspace_id=workspace_id)
    for distributor in distributors:
        nick = (distributor.get("firm_nick_name") or "").strip().lower()
        if nick and nick in stem:
            return distributor

    # Fallback: suggest on exact distributor name token match
    for distributor in distributors:
        name = (distributor.get("name") or "").strip().lower()
        if name and name in stem:
            return distributor

    return None


GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9]\b")


def _extract_all_gstins(text: str) -> list[str]:
    """
    Finds every GSTIN-format token in the document text (there are
    typically two: the SELLER's own GSTIN, which appears on every
    document regardless of buyer, and the BUYER's GSTIN, which is what
    we actually want). Returns unique values in the order first seen.
    The caller is responsible for excluding the workspace's own known
    company GST (via Company Profile) to isolate the buyer's GST —
    this function stays workspace-agnostic and purely textual.
    """
    matches = GSTIN_PATTERN.findall((text or "").upper())
    seen: list[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
    return seen


# Real PDF text extraction frequently runs multiple "Label: value" pairs
# together on a single line (e.g. "Contract No : 102875606 Date :
# 01.04.2026"). A naive split(":", 1) on that line would capture
# "102875606 Date : 01.04.2026" as the value — silently corrupting the
# order reference number and breaking distributor matching entirely.
# This pattern finds where the NEXT label starts, so the value can be
# truncated there.
_NEXT_LABEL_PATTERN = re.compile(
    r"\s+(?:date|buyer\s*code|buyer\s*name|buyer\s*id|gst\s*no\.?|gstin|"
    r"order\s*date|contract\s*no\.?|order\s*ref(?:erence)?\s*no\.?|"
    r"sales\s*order\s*(?:no\.?|number)?|so\s*(?:no\.?|number)?|"
    r"customer\s*name|distributor\s*name|party\s*name|name\s*\(of)\s*:",
    re.I,
)


def _truncate_at_next_label(value: str) -> str:
    match = _NEXT_LABEL_PATTERN.search(value)
    if match:
        return value[: match.start()].strip()
    return value.strip()


def _parse_sales_order_header_fields(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    normalized_text = re.sub(r"\(cid:\d+\)", "\n", text or "")
    normalized_text = re.sub(r"[\r\f\v]+", "\n", normalized_text)
    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = _normalize_text(key).replace(" ", "_")
        cleaned_value = _truncate_at_next_label(value.strip())
        if not cleaned_value:
            continue
        if normalized_key in {"buyer_code", "buyer_id", "buyer"}:
            parsed["buyer_code"] = cleaned_value
        elif normalized_key in {
            "invoice_no", "invoice_no.", "invoice_number", "ci_no", "ci_number",
        }:
            # Overwritten below by a more precise dedicated search —
            # this loop-based match alone can't reliably distinguish
            # the real header value from page-footer noise (see the
            # comment on the dedicated search for why).
            parsed.setdefault("invoice_no", cleaned_value)
        elif normalized_key in {
            "order_ref",
            "order_ref_no",
            "order_reference",
            "so_number",
            "so_no",
            "sales_order_number",
            "contract_no",
            "contract_number",
        }:
            parsed["order_ref_no"] = cleaned_value
        elif normalized_key in {
            "buyer_name",
            "client_name",
            "customer_name",
            "distributor_name",
            "party_name",
            "bill_to",
            "ship_to",
            "buyer",
            "distributor",
            # "Name (of the customer):" — the label used in the
            # founder's real Commercial Invoice documents. The
            # trailing "(of the customer)" gets folded into
            # underscores by the normalize+replace step above, so the
            # normalized key looks like "name_(of_the_customer)" — we
            # match it as a substring check below instead of an exact
            # set-membership, since parentheses don't normalize
            # predictably across all PDF-extraction libraries.
        }:
            parsed["buyer_name"] = cleaned_value
        elif "name" in normalized_key and "customer" in normalized_key:
            parsed["buyer_name"] = cleaned_value

    # Dedicated invoice_no extraction — overrides the loop-based match
    # above. Real Bombay Dyeing CIs repeat "Invoice No.: X" up to
    # once per page as a footer/pagination marker ("Invoice No.: X /
    # <page number>"), plus once in the genuine header — but the
    # header line often has extra prefix text ("INVOICE TO (DETAILS
    # OF RECEIVER) Invoice No.: X") that the generic key:value loop
    # above can't match at all, since the "key" isn't a clean line-
    # start. Meanwhile the footer DOES match that loop, corrupting
    # the true value with a trailing page index. Searching the raw
    # text directly, anywhere on a line, and preferring a match with
    # no trailing "/ <number>" fixes both problems at once.
    invoice_no_matches = re.findall(
        r"invoice\s*no\.?\s*:?\s*([A-Za-z0-9\-]+)(?:\s*/\s*(\d+))?",
        normalized_text,
        re.I,
    )
    if invoice_no_matches:
        plain_matches = [num for num, page_suffix in invoice_no_matches if not page_suffix]
        parsed["invoice_no"] = plain_matches[0] if plain_matches else invoice_no_matches[0][0]

    if "buyer_code" not in parsed:
        match = re.search(
            r"\b(?:buyer\s*code|buyer\s*id|party\s*code|retailer\s*code|distributor\s*code|customer\s*code)\b[:\s]*([A-Za-z0-9\-/]+)",
            text,
            re.I,
        )
        if match:
            parsed["buyer_code"] = match.group(1).strip()

    if "order_ref_no" not in parsed:
        match = re.search(
            r"\b(?:order\s*ref(?:erence)?|so(?:\s*no|\s*number)?|sales\s*order\s*(?:no|number)?|contract\s*(?:no|number)?)\b[:\s]*([A-Za-z0-9\-/]+)",
            text,
            re.I,
        )
        if match:
            parsed["order_ref_no"] = match.group(1).strip()

    if "buyer_name" not in parsed:
        match = re.search(
            r"\b(?:buyer\s*name|distributor\s*name|party\s*name|client\s*name|customer\s*name|bill\s*to|ship\s*to)\b[:\s]*(.+?)(?:\r?$|\n)",
            text,
            re.I | re.M,
        )
        if match:
            parsed["buyer_name"] = match.group(1).strip()

    # GST numbers — ALL found in the document. The caller excludes
    # the workspace's own known company GST (via Company Profile) to
    # determine which remaining one is the buyer's. Deliberately does
    # NOT guess/exclude here, since this function has no workspace
    # context of its own.
    parsed_gst_list = _extract_all_gstins(text)
    if parsed_gst_list:
        parsed["all_gst_numbers"] = ",".join(parsed_gst_list)

    return parsed


def _identify_buyer_gst(all_gst_numbers: list[str], own_company_gst: str | None) -> str | None:
    """
    Given every GSTIN found in a document, returns the one that is
    genuinely the BUYER's — i.e. everything EXCEPT the workspace's own
    known company GST (from Company Profile). If there isn't exactly
    one remaining candidate (none left, or more than one — e.g. the
    document also lists a transporter's GST), returns None rather than
    guessing, so the caller can fall back to Buyer Code / fuzzy-name
    matching instead.
    """
    if not all_gst_numbers:
        return None
    own_normalized = (own_company_gst or "").strip().upper()
    candidates = [g for g in all_gst_numbers if g != own_normalized]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _build_sales_order_link_summary(
    selected_name: str | None,
    matched_name: str | None,
    buyer_name: str | None,
) -> str:
    if matched_name and selected_name:
        if selected_name.lower().strip() == matched_name.lower().strip():
            return (
                f"Is this SO for {selected_name}? Buyer Code and selected distributor both matched."
            )
        return (
            f"Buyer Code suggests {matched_name}, but selected distributor is {selected_name}. Please confirm manually."
        )
    if matched_name and not selected_name:
        if buyer_name:
            return (
                f"Buyer Code suggests {matched_name}, buyer name text is {buyer_name}. Please confirm manually."
            )
        return f"Buyer Code suggests {matched_name}. Please confirm manually."
    if not matched_name and selected_name:
        return f"Selected distributor is {selected_name}. Buyer Code could not be matched automatically."
    return "Sales order distributor could not be linked automatically. Please confirm manually."


DASHBOARD_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "dashboard_config.json"
DEFAULT_DASHBOARD_CONFIG = {
    "brand_name": "NEXORA",
    "app_name": "NEXORA ENTERPRISE",
    "dashboard_title": "Ask Nexora",
    "short_name": "Ask Nexora",
    "theme_color": "#020617",
    "background_color": "#020617",
    "enabled_modules": [
        "dashboard",
        "verification",
        "analytics",
        "masters",
        "sales",
        "inventory",
        "reports",
        "file_library",
        "party_match",
    ],
    "api_endpoints": {
        "dashboard_summary": "/api/v1/dashboard/summary",
        "manifest": "/manifest.json",
    },
}


def _dashboard_config_path() -> Path:
    try:
        from flask import current_app

        config_path = current_app.config.get("DASHBOARD_CONFIG_PATH")
        if config_path:
            return Path(config_path)
    except Exception:
        pass
    return DASHBOARD_CONFIG_PATH


def _ensure_dashboard_config_exists() -> None:
    config_path = _dashboard_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(
            json.dumps(DEFAULT_DASHBOARD_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_dashboard_config() -> dict[str, Any]:
    config_path = _dashboard_config_path()
    _ensure_dashboard_config_exists()
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        config_path.write_text(
            json.dumps(DEFAULT_DASHBOARD_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dict(DEFAULT_DASHBOARD_CONFIG)


def save_dashboard_config(update_data: dict[str, Any]) -> dict[str, Any]:
    config = load_dashboard_config()
    config.update(update_data)
    config_path = _dashboard_config_path()
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config


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
                        workspace_id = get_workspace_id()
                        db = CentralizedDB(_db_path())
                        inserted_id = db.add_master_distributor(
                            name=distributor_fields["name"],
                            distributor_code=distributor_fields.get("distributor_code"),
                            buyer_code=distributor_fields.get("buyer_code"),
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
                            workspace_id=workspace_id,
                        )
                        connection = sqlite3.connect(db.db_path)
                        try:
                            connection.execute(
                                "UPDATE master_distributors SET name = ?, firm_name = ?, firm_nick_name = ?, gst_no = ?, buyer_code = ?, zone = ?, region = ?, credit_limit = ?, phone_number = ?, email = ?, address = ? WHERE id = ?",
                                (
                                    distributor_fields.get("name"),
                                    distributor_fields.get("firm_name"),
                                    distributor_fields.get("firm_nick_name"),
                                    distributor_fields.get("gst_no"),
                                    distributor_fields.get("buyer_code"),
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
                        workspace_id = get_workspace_id()
                        db = CentralizedDB(_db_path())
                        distributor = None
                        reference = retailer_fields.get("distributor_reference")
                        if reference:
                            distributor = db.get_master_distributor_by_name(
                                reference, workspace_id=workspace_id
                            )
                            if distributor is None:
                                distributor = (
                                    db._find_master_distributor_by_gst_or_name(
                                        reference,
                                        workspace_id=workspace_id,
                                    )
                                )
                        if distributor is None and reference:
                            distributor = db._find_or_create_distributor_from_reference(
                                reference,
                                workspace_id=workspace_id,
                            )
                        if distributor is None:
                            distributor = db._find_or_create_distributor_from_reference(
                                retailer_fields.get("name", ""),
                                workspace_id=workspace_id,
                            )
                        if distributor is not None:
                            inserted_id = db.add_master_retailer(
                                name=retailer_fields["name"],
                                distributor_id=distributor["id"],
                                location=retailer_fields.get("location"),
                                workspace_id=workspace_id,
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
            result = CentralizedDB(_db_path()).bulk_upload_masters(
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
            result = CentralizedDB(_db_path()).bulk_upload_masters(
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
@require_jwt_auth
def index() -> str:
    report = None
    progress_summary = None
    search_query = request.args.get("q", "") if request.method == "GET" else ""
    search_results = None
    db = CentralizedDB(_db_path())
    locked_rules_summary = json.dumps(
        db.list_business_rules(locked_only=True), indent=2
    )
    distributor_options = db.list_master_distributors(
        limit=200, workspace_id=get_workspace_id()
    )
    suggested_filled_distributor_name = None
    selected_filled_distributor_name = None
    selected_filled_distributor_id = ""
    selected_sales_order_distributor_id = ""
    selected_sales_order_distributor_name = None
    sales_order_linking_summary = None
    filled_file_distributor = None
    if request.method == "POST":
        workflow_action = (
            (request.form.get("workflow_action") or "run_all").strip().lower()
        )
        distributor_name = (request.form.get("distributor_name") or "").strip()
        order_sheet_name = (request.form.get("order_sheet_name") or "").strip()
        order_sheet_category = (request.form.get("order_sheet_category") or "").strip()
        order_sheet_is_active = str(
            request.form.get("order_sheet_is_active", "1") or "1"
        ).strip().lower()
        order_sheet_is_active = 0 if order_sheet_is_active in {"0", "false", "no", "off", ""} else 1
        selected_filled_distributor_id = (
            request.form.get("filled_file_distributor_id") or ""
        ).strip()
        filled_file_distributor = None
        if selected_filled_distributor_id.isdigit():
            filled_file_distributor = db.get_master_distributor(
                int(selected_filled_distributor_id),
                workspace_id=get_workspace_id(),
            )
            if filled_file_distributor is not None:
                selected_filled_distributor_name = filled_file_distributor["name"]

        selected_sales_order_distributor_id = (
            request.form.get("sales_order_distributor_id") or ""
        ).strip()
        sales_order_distributor = None
        if selected_sales_order_distributor_id.isdigit():
            sales_order_distributor = db.get_master_distributor(
                int(selected_sales_order_distributor_id),
                workspace_id=get_workspace_id(),
            )
            if sales_order_distributor is not None:
                selected_sales_order_distributor_name = sales_order_distributor["name"]

        order_sheet_distributor = distributor_name
        order_sheet_distributor_id = None
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
        persisted_order_sheet_ids: list[int] = []

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
                    distributor_options=distributor_options,
                    suggested_filled_distributor_name=suggested_filled_distributor_name,
                    selected_filled_distributor_name=selected_filled_distributor_name,
                    selected_filled_distributor_id=selected_filled_distributor_id,
                    selected_sales_order_distributor_id=selected_sales_order_distributor_id,
                    selected_sales_order_distributor_name=selected_sales_order_distributor_name,
                    sales_order_linking_summary=sales_order_linking_summary,
                )

            safe_name = Path(uploaded_file.filename).name
            target_path = upload_dir / f"{key}_{safe_name}"
            uploaded_file.save(target_path)
            stored_files[key] = str(target_path)
            inferred_distributor_name = infer_distributor_name(
                key, safe_name, explicit_name=distributor_name
            )
            if key == "filled_file" and filled_file_distributor is not None:
                inferred_distributor_name = filled_file_distributor["name"]
            stored_metadata[key] = {
                "stage": stage_label_for_key(key),
                "file_type": detected_file_type,
                "filename": safe_name,
                "distributor_name": inferred_distributor_name,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            if key == "filled_file":
                if filled_file_distributor is not None:
                    stored_metadata[key]["distributor_id"] = (
                        filled_file_distributor["id"]
                    )
                else:
                    suggested = _suggest_filled_order_distributor(
                        safe_name, get_workspace_id()
                    )
                    if suggested is not None:
                        suggested_filled_distributor_name = suggested["name"]
                        stored_metadata[key]["suggested_distributor_name"] = (
                            suggested["name"]
                        )
                        stored_metadata[key]["suggested_firm_nick_name"] = (
                            suggested["firm_nick_name"]
                        )
                        stored_metadata[key]["suggested_distributor_id"] = (
                            suggested["id"]
                        )
            if key == "sales_order_file":
                extracted_text = ""
                try:
                    extracted_text = _extract_pdf_text(target_path)
                except Exception:
                    extracted_text = ""
                sales_header = _parse_sales_order_header_fields(extracted_text)
                stored_metadata[key]["sales_order_text"] = (
                    extracted_text[:500] if extracted_text else ""
                )
                stored_metadata[key]["sales_order_header"] = sales_header
                sales_order_buyer_code = sales_header.get("buyer_code")
                sales_order_ref = sales_header.get("order_ref_no")
                sales_order_buyer_name = sales_header.get("buyer_name")
                stored_metadata[key]["order_ref_no"] = sales_order_ref
                stored_metadata[key]["buyer_code"] = sales_order_buyer_code
                stored_metadata[key]["buyer_name"] = sales_order_buyer_name

                # Second signal: the BUYER's GST (excluding our own
                # company's GST, from Company Profile) — cross-checked
                # against the Buyer Code match below. GST is a more
                # reliable signal than free-text buyer name, since it's
                # a legally standardized, unique-per-firm identifier.
                all_gst_numbers = (sales_header.get("all_gst_numbers") or "").split(",")
                all_gst_numbers = [g for g in all_gst_numbers if g]
                own_company_profile = db.get_company_profile(get_workspace_id())
                own_company_gst = (
                    own_company_profile.get("gst_number") if own_company_profile else None
                )
                buyer_gst = _identify_buyer_gst(all_gst_numbers, own_company_gst)
                stored_metadata[key]["buyer_gst"] = buyer_gst

                parsed_sales_order = parse_step2_sales_order_pdf(target_path)
                stored_metadata[key]["parsed_sales_order"] = parsed_sales_order

                matched_by_buyer_code = None
                if sales_order_buyer_code:
                    matched_by_buyer_code = db.get_master_distributor_by_buyer_code(
                        sales_order_buyer_code, workspace_id=get_workspace_id()
                    )
                matched_by_gst = None
                if buyer_gst:
                    matched_by_gst = db.get_master_distributor_by_gst(
                        buyer_gst, workspace_id=get_workspace_id()
                    )

                # Combine both signals: if they AGREE, this is a
                # confident match. If they DISAGREE, flag it clearly
                # rather than silently trusting one over the other —
                # a human still confirms either way (below), but the
                # summary text must be honest about the disagreement.
                signal_agreement = None
                if matched_by_buyer_code and matched_by_gst:
                    signal_agreement = (
                        matched_by_buyer_code["id"] == matched_by_gst["id"]
                    )
                matched_distributor = matched_by_buyer_code or matched_by_gst
                stored_metadata[key]["matched_by_buyer_code"] = (
                    matched_by_buyer_code["name"] if matched_by_buyer_code else None
                )
                stored_metadata[key]["matched_by_gst"] = (
                    matched_by_gst["name"] if matched_by_gst else None
                )
                stored_metadata[key]["signals_agree"] = signal_agreement

                if matched_distributor is not None:
                    stored_metadata[key]["matched_distributor_id"] = (
                        matched_distributor["id"]
                    )
                    stored_metadata[key]["matched_distributor_name"] = (
                        matched_distributor["name"]
                    )
                if sales_order_distributor is not None:
                    stored_metadata[key]["distributor_id"] = (
                        sales_order_distributor["id"]
                    )
                    stored_metadata[key]["distributor_name"] = (
                        sales_order_distributor["name"]
                    )
                elif matched_distributor is not None:
                    stored_metadata[key]["suggested_distributor_id"] = (
                        matched_distributor["id"]
                    )
                    stored_metadata[key]["suggested_distributor_name"] = (
                        matched_distributor["name"]
                    )

                if sales_order_ref and sales_order_distributor is not None:
                    try:
                        tracking_id = db.link_sales_order_to_order_lifecycle(
                            order_ref_no=sales_order_ref,
                            distributor_id=sales_order_distributor["id"],
                            sales_order_file_reference=str(target_path),
                            sales_order_parsed=parsed_sales_order,
                            workspace_id=get_workspace_id(),
                        )
                        stored_metadata[key][
                            "order_lifecycle_tracking_id"
                        ] = tracking_id
                        stored_metadata[key][
                            "linked_sales_order_distributor_id"
                        ] = sales_order_distributor["id"]
                    except Exception as exc:
                        stored_metadata[key][
                            "order_lifecycle_link_error"
                        ] = str(exc)

                if signal_agreement is False:
                    sales_order_linking_summary = (
                        f"Warning: Buyer Code suggests "
                        f"'{matched_by_buyer_code['name']}', but the buyer's GST "
                        f"number suggests '{matched_by_gst['name']}' — these "
                        f"disagree. Please confirm the correct distributor "
                        f"manually before proceeding."
                    )
                elif signal_agreement is True:
                    sales_order_linking_summary = (
                        f"Is this SO for {matched_by_buyer_code['name']}? "
                        f"Both Buyer Code and GST number matched — please confirm."
                    )
                else:
                    sales_order_linking_summary = _build_sales_order_link_summary(
                        selected_sales_order_distributor_name,
                        matched_distributor["name"] if matched_distributor else None,
                        sales_order_buyer_name,
                    )
                stored_metadata[key]["sales_order_linking_summary"] = (
                    sales_order_linking_summary
                )
            if key == "invoice_file":
                extracted_text = ""
                try:
                    extracted_text = _extract_pdf_text(target_path)
                except Exception:
                    extracted_text = ""
                invoice_header = _parse_sales_order_header_fields(extracted_text)
                stored_metadata[key]["commercial_invoice_text"] = (
                    extracted_text[:500] if extracted_text else ""
                )
                stored_metadata[key]["invoice_order_ref_no"] = invoice_header.get(
                    "order_ref_no"
                )
                stored_metadata[key]["invoice_buyer_code"] = invoice_header.get(
                    "buyer_code"
                )
                stored_metadata[key]["invoice_buyer_name"] = invoice_header.get(
                    "buyer_name"
                )
                parsed_invoice = parse_step3_invoice_pdf(target_path)
                stored_metadata[key]["parsed_commercial_invoice"] = parsed_invoice
            if key == "order_file":
                if not order_sheet_name or not order_sheet_category:
                    report = json.dumps(
                        {
                            "status": "error",
                            "message": "Order sheet name and category are required for stage 1 uploads.",
                        },
                        indent=2,
                    )
                    progress_summary = (
                        "Order sheet name and category are required for stage 1 uploads."
                    )
                    return render_template_string(
                        HTML_TEMPLATE,
                        report=report,
                        report_data=json.loads(report),
                        progress_summary=progress_summary,
                        locked_rules_summary=locked_rules_summary,
                        sync_status=json.dumps({}, indent=2),
                        search_query=search_query,
                        search_results=search_results,
                        distributor_options=distributor_options,
                        suggested_filled_distributor_name=suggested_filled_distributor_name,
                        selected_filled_distributor_name=selected_filled_distributor_name,
                        selected_filled_distributor_id=selected_filled_distributor_id,
                        selected_sales_order_distributor_id=selected_sales_order_distributor_id,
                        selected_sales_order_distributor_name=selected_sales_order_distributor_name,
                        sales_order_linking_summary=sales_order_linking_summary,
                    )
                try:
                    order_sheet_fingerprint = _fingerprint_file(target_path)
                    order_sheet_id = db.add_order_sheet(
                        name=order_sheet_name,
                        category=order_sheet_category,
                        file_reference=str(target_path),
                        workspace_id=get_workspace_id(),
                        is_active=order_sheet_is_active,
                        content_fingerprint=order_sheet_fingerprint,
                    )
                    stored_metadata[key]["order_sheet_id"] = order_sheet_id
                    session["verification_order_sheet_id"] = order_sheet_id
                    persisted_order_sheet_ids.append(order_sheet_id)
                except Exception as exc:
                    stored_metadata[key]["order_sheet_error"] = str(exc)
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

                # Check whether a matching SO exists and prepare a
                # confirmation summary — this NEVER auto-links. The
                # founder was explicit that CI-to-SO linking (which
                # drives achievement/revenue figures) must always be a
                # human-confirmed action, never silent, even when the
                # verification comparison and reference number both
                # look clean.
                invoice_ref = stored_metadata.get("invoice_file", {}).get(
                    "invoice_order_ref_no"
                )
                linked_invoice = False
                invoice_link_error = None
                requires_confirmation = False
                confirmation_summary = None

                if step3_result.get("status") == "ok" and invoice_ref:
                    matching_so = db.get_order_lifecycle_by_order_ref_no(
                        invoice_ref, workspace_id=get_workspace_id()
                    )
                    if matching_so:
                        requires_confirmation = True
                        confirmation_summary = (
                            f"This Commercial Invoice's Sales Order Number "
                            f"('{invoice_ref}') matches an existing Sales Order "
                            f"on file (tracking #{matching_so['tracking_id']}). "
                            f"Confirm to link this CI and record the achievement — "
                            f"nothing is saved automatically."
                        )
                        stored_metadata["invoice_file"]["pending_link"] = {
                            "order_ref_no": invoice_ref,
                            "tracking_id": matching_so["tracking_id"],
                            "commercial_invoice_file_reference": str(
                                stored_files.get("invoice_file")
                            ),
                            "commercial_invoice_parsed": stored_metadata.get(
                                "invoice_file", {}
                            ).get("parsed_commercial_invoice", {}),
                        }
                    else:
                        invoice_link_error = (
                            f"No existing Sales Order found on file matching "
                            f"reference '{invoice_ref}'. Please verify the Sales "
                            f"Order was uploaded first, or link manually."
                        )
                        stored_metadata["invoice_file"][
                            "commercial_invoice_link_error"
                        ] = invoice_link_error
                elif step3_result.get("status") == "ok" and not invoice_ref:
                    invoice_link_error = "Commercial invoice order reference number could not be extracted from the PDF"
                    stored_metadata["invoice_file"][
                        "commercial_invoice_link_error"
                    ] = invoice_link_error
                else:
                    if invoice_ref:
                        stored_metadata["invoice_file"][
                            "commercial_invoice_link_error"
                        ] = (
                            "Commercial invoice comparison did not verify cleanly. "
                            "Manual review is required before linking."
                        )

                current_status = "stage-4-checked"
                current_msg = "Commercial invoice checked against sales order."
                report_payload = {
                    "status": current_status,
                    "message": current_msg,
                    "step3": step3_result,
                    "uploaded_files": sorted(
                        [key for key in stored_files if stored_files.get(key)]
                    ),
                    "uploaded_documents": stored_metadata,
                    "next_step": next_step,
                }
                if requires_confirmation:
                    report_payload["requires_confirmation"] = True
                    report_payload["confirmation_summary"] = confirmation_summary
                    report_payload["pending_link"] = stored_metadata["invoice_file"].get(
                        "pending_link"
                    )
                if invoice_link_error:
                    report_payload["commercial_invoice_link_error"] = (
                        invoice_link_error
                    )
                report = json.dumps(report_payload, indent=2)
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
            CentralizedDB(_db_path()).global_search(search_query),
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
        distributor_options=distributor_options,
        suggested_filled_distributor_name=suggested_filled_distributor_name,
        selected_filled_distributor_name=selected_filled_distributor_name,
        selected_filled_distributor_id=selected_filled_distributor_id,
        selected_sales_order_distributor_id=selected_sales_order_distributor_id,
        selected_sales_order_distributor_name=selected_sales_order_distributor_name,
        sales_order_linking_summary=sales_order_linking_summary,
    )


@data_blueprint.route("/bale-calculator", methods=["GET", "POST"])
@require_jwt_auth
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
@data_blueprint.route("/api/v1/search")
@require_jwt_auth
def search() -> Response:
    query = request.args.get("q", "")
    user = getattr(request, "user", None)
    user_id = int(user["user_id"]) if isinstance(user, dict) and user.get("user_id") is not None else None
    # SECURITY: workspace_id must be passed through — without it this
    # searches and returns results mixed across EVERY workspace, the
    # same class of cross-tenant leak already found and fixed for
    # bulk_upload_masters/export functions earlier in this project.
    try:
        payload = CentralizedDB(_db_path()).global_search(
            query, workspace_id=get_workspace_id(), user_id=user_id,
        )
        return jsonify(payload)
    except Exception as exc:
        return jsonify(
            {
                "query": query,
                "results": {},
                "success": False,
                "error": {"code": "SEARCH_FAILED", "message": str(exc)},
            }
        ), 500


def _compute_financial_year(reference_date: datetime | None = None) -> str:
    """
    Indian financial year runs April -> March. E.g. any date from
    1 Apr 2025 through 31 Mar 2026 falls in "FY2025-26".
    """
    dt = reference_date or datetime.now(timezone.utc)
    if dt.month >= 4:
        start_year = dt.year
    else:
        start_year = dt.year - 1
    end_year_short = str(start_year + 1)[-2:]
    return f"FY{start_year}-{end_year_short}"


def _get_organized_upload_path(folder: str, subfolder: str, filename: str) -> Path:
    """
    Builds the founder-requested folder structure so uploaded files
    can be visually confirmed on disk, e.g.:
      Order Sheets/Bedsheet/FY2025-26/<file>
      Distributor/Order Given/FY2025-26/<file>
      SO/SO Received/FY2025-26/<file>
      CI/CI Received/FY2025-26/<file>
    """
    upload_root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    )
    fy = _compute_financial_year()
    target_dir = upload_root / folder / subfolder / fy
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    # Avoid silently overwriting a same-named file uploaded earlier —
    # append a short timestamp instead.
    target_path = target_dir / safe_name
    if target_path.exists():
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        target_path = target_dir / f"{stem}_{timestamp}{suffix}"
    return target_path


def _save_order_fulfillment_upload(uploaded_file, prefix: str) -> Path:
    upload_dir = _get_verification_upload_dir()
    safe_name = Path(uploaded_file.filename).name
    target_path = upload_dir / f"{prefix}_{safe_name}"
    uploaded_file.save(target_path)
    return target_path


def extract_order_sheet_item_key(material_description: str) -> str | None:
    """
    Parses an SO/CI line item's Material Description (e.g.
    "ASTER 1+2 DB SET 224X244 7985BLU 100TC") into a normalized
    Brand+TC+Size key ("ASTER|100|DB") -- the SAME granularity as one
    row in the Order Sheet/Filled Order. This is what lets many SO/CI
    SKU-lines (each a distinct design+color variant) correctly
    accumulate together and match against ONE Filled Order row,
    rather than being treated as unrelated, unmatched items.
    Verified against real Bombay Dyeing SO/CI documents (Aster, 18
    design+color lines, all correctly collapsing to "ASTER|100|DB").
    """
    text = (material_description or "").strip().upper()
    if not text:
        return None

    tc_match = re.search(r"(\d+)\s*TC\s*$", text)
    if not tc_match:
        return None
    tc = tc_match.group(1)
    remainder = text[: tc_match.start()].strip()

    size_match = re.search(r"\b(DB|SB|KS|KB)\b", remainder)
    size = size_match.group(1) if size_match else None

    units_match = re.search(r"\b\d\+\d\b", remainder)
    brand = remainder[: units_match.start()].strip() if units_match else (
        remainder.split()[0] if remainder else None
    )

    if not brand or not tc or not size:
        return None
    return f"{brand}|{tc}|{size}"


def make_order_sheet_item_key(brand: str, tc: Any, size: str) -> str | None:
    """
    Builds the SAME normalized Brand+TC+Size key from an Order
    Sheet/Filled Order row's own Brand/TC/Size columns, so both sides
    (SO/CI Material Descriptions and Filled Order spreadsheet rows)
    can be matched on an identical key format.
    """
    brand_clean = (str(brand or "")).strip().upper()
    size_clean = (str(size or "")).strip().upper()
    size_match = re.search(r"\b(DB|SB|KS|KB)\b", size_clean)
    size_code = size_match.group(1) if size_match else size_clean.split()[0] if size_clean else None
    try:
        tc_clean = str(int(float(tc)))
    except (ValueError, TypeError):
        tc_clean = str(tc).strip()
    if not brand_clean or not tc_clean or not size_code:
        return None
    return f"{brand_clean}|{tc_clean}|{size_code}"


def size_code_only_item_key(item_key: str | None) -> str | None:
    """Re-export for callers that already import from data.py."""
    from order_item_keys import size_code_only_item_key as _normalize

    return _normalize(item_key)


_MATERIAL_CODE_ONLY_RE = re.compile(r"^[A-Z0-9]+$", re.I)


def _clean_pdf_cell_text(cell: str | None) -> str:
    """
    A single logical value (e.g. a Material Description) inside a
    pdfplumber table cell often comes back with embedded newlines —
    either because the source cell genuinely wraps onto multiple
    visual lines ("ASTER 1+2 DB SET\\n224X244 7985BLU 100TC"), or
    because a narrow column wraps mid-number ("66.0\\n00"). Collapsing
    all whitespace (including embedded newlines) to single spaces
    reassembles the original text correctly in both cases.
    """
    return re.sub(r"\s+", " ", (cell or "").replace("\n", " ")).strip()


def _clean_pdf_cell_number(cell: str | None) -> float | None:
    """
    Same embedded-newline problem as _clean_pdf_cell_text, but for
    numeric cells — e.g. a narrow "Taxable" column wraps as
    "38,280\\n.00". Stripping ALL whitespace (not collapsing to a
    space) before removing non-numeric characters reassembles
    "38,280.00" correctly, rather than "38,280 .00" (which would
    still fail float() without the extra cleanup this does anyway).
    """
    if cell is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(cell).replace("\n", ""))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_bombay_dyeing_so_ci_line_items(path: str | Path, doc_type: str) -> list[dict[str, Any]]:
    """
    Dedicated, verified parser for the real Bombay Dyeing SO/CI PDFs —
    reads pdfplumber's extract_tables() (cell-based) instead of
    extract_text() (line-based).

    This replaces an earlier version of this function that regex-
    matched against extract_text() output, assuming a fixed 3-line-
    per-item text layout. That assumption held for a hand-copied
    sample of the real text, but NOT for what _extract_pdf_text()
    (three_step_verification.py) actually produces from the live
    files, which was found to differ per document:
      - SO: items land as 2 physical text lines, not 3, so the old
        regex found 0 matches and silently fell through to the
        generic fallback parser (_parse_pdf_table_like_text), which
        dropped the Design/Color/TC line entirely — the exact bug
        this whole parser was originally built to fix.
      - CI: extract_text() runs the CGST/SGST/IGST numeric columns
        together with no separating whitespace at all (e.g.
        "66.0580.0 38,280.0.00 38,2800.00"), which is not reliably
        regex-parseable at all — this is why CI items never showed
        up in the reconciliation sheet.

    Both SO and CI are genuinely bordered tables in the source PDFs,
    so extract_tables() sidesteps both problems by reading actual
    table cells rather than guessing at text reading order. Verified
    against the real BND_102875606.pdf (18/18 items, sum 1188 qty /
    Rs.689,040) and Commercial_Invoice.PDF (18/18 items, same sums).

    doc_type: "SO" or "CI" — selects the column layout to read:
      SO columns: Material Code, Material Description, HSN Code,
                  Qty, Rate, Unit, Schedule Delivery, Net Value,
                  GST Value, Total Value
      CI columns: SN, Description of Product, HSN Code, UoM, Qty,
                  Rate, Amount, Discount, Taxable, CGST rate/amt,
                  SGST rate/amt, IGST rate/amt, Total
    "Net Value" (SO) and "Taxable" (CI) are the same concept — the
    pre-GST line value — used as this function's "value" for both.

    Returns a clean list of {item_key, item_name, material_code,
    qty, value} dicts. item_name is the full, correctly-reassembled
    Material Description (e.g. "ASTER 1+2 DB SET 224X244 7985BLU
    100TC"), which extract_order_sheet_item_key() can then parse.
    """
    if doc_type not in ("SO", "CI"):
        raise ValueError("doc_type must be 'SO' or 'CI'")

    items: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    for row in table:
                        if not row:
                            continue

                        if doc_type == "SO":
                            if len(row) < 10:
                                continue
                            code = (row[0] or "").strip()
                            if not code or not _MATERIAL_CODE_ONLY_RE.match(code) or code.upper() == "TOTAL":
                                continue
                            description_cell, qty_cell, net_value_cell = row[1], row[3], row[7]
                        else:
                            if len(row) < 9:
                                continue
                            serial_no = (row[0] or "").strip()
                            if not serial_no.isdigit():
                                continue
                            code = None
                            description_cell, qty_cell, net_value_cell = row[1], row[4], row[8]

                        full_description = _clean_pdf_cell_text(description_cell)
                        qty = _clean_pdf_cell_number(qty_cell)
                        net_value = _clean_pdf_cell_number(net_value_cell)
                        if not full_description or qty is None or net_value is None:
                            continue

                        items.append({
                            "item_name": full_description,
                            "item_key": extract_order_sheet_item_key(full_description),
                            "material_code": code,
                            "qty": qty,
                            "value": net_value,
                        })
    except Exception:
        return []

    # Repair items whose multi-line description cell got truncated at
    # a PDF page boundary — pdfplumber's per-page table extraction
    # can't see text that visually continues onto the next page
    # within the same cell, so the trailing Design/Color/TC portion
    # is lost (e.g. "ASTER 1+2 DB SET 224X244" instead of the full
    # "...224X244 7990BGE 100TC"). qty/value still parse correctly
    # (they weren't split), but extract_order_sheet_item_key() fails
    # without the TC suffix, leaving this one item as an orphan row
    # instead of merging into the rest of its group — which then
    # shows up as a false SO-vs-CI (or Ordered-vs-SO) quantity
    # mismatch that isn't a real business discrepancy at all.
    #
    # Real Bombay Dyeing SO/CI documents are always single-brand per
    # upload, so if every OTHER successfully-keyed item in this same
    # document shares exactly ONE item_key, and the truncated item's
    # name is a literal prefix of another item's full name (proving
    # it's genuinely the same truncated description, not a
    # coincidence), it's safe to carry that key forward.
    keyed_items = [item for item in items if item["item_key"]]
    distinct_keys = {item["item_key"] for item in keyed_items}
    if len(distinct_keys) == 1:
        fallback_key = next(iter(distinct_keys))
        for item in items:
            if item["item_key"] is None and any(
                other["item_name"].startswith(item["item_name"])
                for other in keyed_items
            ):
                item["item_key"] = fallback_key

    return items


def _parse_filled_order_items(file_path: Path) -> list[dict[str, Any]]:
    """
    Reads a distributor's Filled/Placed Order spreadsheet (xlsx/xls/
    csv) and extracts item/quantity/value rows at Brand+TC+Size
    granularity, using flexible column-name matching since different
    distributors' sheets don't all use the exact same headers.

    Supports TWO real-world formats, verified against actual Bombay
    Dyeing documents:
      1. The real Order Sheet/Filled Order format — separate Brand,
         TC, Size columns plus "AWDs Qty"/"AWD Order Value" columns.
      2. A simpler generic format — a single combined item-name
         column plus qty/value (or qty/rate) columns.
    """
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
    except Exception:
        return []

    df.columns = [str(c).strip().lower() for c in df.columns]

    # --- Format 1: real Bombay Dyeing structure (Brand/TC/Size + AWDs columns) ---
    if {"brand", "tc", "size"}.issubset(df.columns):
        qty_col = next((c for c in df.columns if c in {"awds qty", "ordered qty", "qty"}), None)
        value_col = next(
            (c for c in df.columns if c in {"awd order value", "ordered value", "order value", "value"}), None
        )
        if qty_col:
            items = []
            for _, row in df.iterrows():
                brand = row.get("brand")
                if pd.isna(brand) or not str(brand).strip():
                    continue
                try:
                    qty = float(row.get(qty_col, 0) or 0)
                except (ValueError, TypeError):
                    continue
                try:
                    value = float(row.get(value_col, 0) or 0) if value_col else 0.0
                except (ValueError, TypeError):
                    value = 0.0
                item_key = make_order_sheet_item_key(brand, row.get("tc"), row.get("size"))
                display_name = f"{str(brand).strip()} {row.get('tc')}TC {row.get('size')}"
                items.append({"item_name": display_name, "item_key": item_key, "qty": qty, "value": value})
            return items

    # --- Format 2: generic single item-name column ---
    item_col = next((c for c in df.columns if c in {
        "item", "item name", "product", "product name", "article", "article name", "design", "sku",
    }), None)
    qty_col = next((c for c in df.columns if c in {
        "qty", "quantity", "order qty", "ordered qty", "pieces", "no. of pieces", "nos",
    }), None)
    value_col = next((c for c in df.columns if c in {
        "value", "amount", "order value", "ordered value", "total", "total value", "total amount",
    }), None)
    rate_col = next((c for c in df.columns if c in {"rate", "price", "unit price", "unit rate"}), None)

    if not item_col or not qty_col:
        return []

    items = []
    for _, row in df.iterrows():
        item_name = str(row.get(item_col, "")).strip()
        if not item_name or item_name.lower() == "nan":
            continue
        try:
            qty = float(row.get(qty_col, 0) or 0)
        except (ValueError, TypeError):
            continue
        if value_col and pd.notna(row.get(value_col)):
            try:
                value = float(row.get(value_col))
            except (ValueError, TypeError):
                value = 0.0
        elif rate_col and pd.notna(row.get(rate_col)):
            try:
                value = qty * float(row.get(rate_col))
            except (ValueError, TypeError):
                value = 0.0
        else:
            value = 0.0
        items.append({"item_name": item_name, "item_key": None, "qty": qty, "value": value})
    return items


def _order_fulfillment_files_root() -> Path:
    root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_existing_order_fulfillment_source(path: Path | str) -> Path:
    """
    Allow shutil.move only for files that already live under the
    order-fulfillment uploads root. Rejects absolute paths outside that
    tree (config files, other tenants' uploads, etc.).
    """
    upload_root = _order_fulfillment_files_root()
    raw = Path(path).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw.resolve())
    else:
        candidates.append((Path.cwd() / raw).resolve())
        candidates.append((upload_root / raw).resolve())

    for candidate in candidates:
        try:
            candidate.relative_to(upload_root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise ValueError(
        f"Source file must be an existing upload under {upload_root}"
    )


def _cleanup_empty_parent_folders(path: Path, stop_at: Path) -> None:
    """
    After moving a file OUT of the old document-type tree
    (SO/SO Received/FY, CI/CI Received/FY, Distributor/Order Given/FY),
    walks back up removing now-empty parent folders — so the founder
    doesn't end up with a pile of leftover empty folders. Stops at
    stop_at (the upload root, exclusive) and never removes anything
    at or above it.
    """
    stop_at = stop_at.resolve()
    current = path.parent.resolve()
    while stop_at in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _move_into_distributor_order_cycle_folder(
    temp_path: Path,
    distributor_name: str,
    doc_type: str,
    order_sheet_name: str | None = None,
    financial_year: str | None = None,
) -> Path:
    """
    Moves an already-saved file into the founder-requested navigable
    structure:
      Order Cycle/{FY}/{Distributor}/{Order Sheet Name}/SO/<original filename>
      Order Cycle/{FY}/{Distributor}/{Order Sheet Name}/CI/<original filename>
      Order Cycle/{FY}/{Distributor}/{Order Sheet Name}/<original filled-order filename>
    doc_type is one of "SO", "CI", or "FilledOrder". SO and CI get
    their OWN dedicated subfolder (original filenames preserved, no
    prefix); a Filled Order's copy sits directly in the Order Sheet
    Name folder per the founder's spec.
    """
    fy = financial_year or _compute_financial_year()
    safe_distributor_name = re.sub(r'[<>:"/\\|?*]', "_", distributor_name).strip() or "Unassigned"
    safe_order_sheet_name = re.sub(r'[<>:"/\\|?*]', "_", order_sheet_name or "Unassigned Order Sheet").strip()

    # Refuse to move arbitrary filesystem paths supplied by clients.
    temp_path = _resolve_existing_order_fulfillment_source(temp_path)
    upload_root = _order_fulfillment_files_root()
    order_sheet_dir = upload_root / "Order Cycle" / fy / safe_distributor_name / safe_order_sheet_name

    if doc_type in ("SO", "CI"):
        target_dir = order_sheet_dir / doc_type
    else:
        target_dir = order_sheet_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / temp_path.name
    if target_path.exists():
        stem, suffix = target_path.stem, target_path.suffix
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        target_path = target_dir / f"{stem}_{timestamp}{suffix}"

    shutil.move(str(temp_path), str(target_path))
    _cleanup_empty_parent_folders(temp_path, upload_root)
    return target_path


def _save_order_fulfillment_upload_organized(uploaded_file, folder: str, subfolder: str) -> Path:
    target_path = _get_organized_upload_path(folder, subfolder, uploaded_file.filename)
    uploaded_file.save(target_path)
    return target_path


@data_blueprint.route("/api/v1/order-fulfillment/upload/order-sheet", methods=["POST"])
@require_jwt_auth
def upload_order_sheet_v2() -> Response:
    """Order Sheet Master upload — the base product/pricing sheet a
    distributor's order gets checked against."""
    uploaded_file = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip()
    if not uploaded_file or not uploaded_file.filename:
        return _json_response({"success": False, "error": {"message": "file is required"}}, 400)
    if not name or not category:
        return _json_response(
            {"success": False, "error": {"message": "name and category are required"}}, 400
        )

    target_path = _save_order_fulfillment_upload_organized(uploaded_file, "Order Sheets", category)
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    fingerprint = _fingerprint_file(target_path)
    sheet_id = db.add_order_sheet(
        name=name,
        category=category,
        file_reference=str(target_path),
        workspace_id=workspace_id,
        content_fingerprint=fingerprint,
    )
    sheet = db.get_order_sheet(sheet_id, workspace_id=workspace_id)
    return _json_response({"success": True, "data": sheet})


@data_blueprint.route("/api/v1/order-fulfillment/upload/filled-order", methods=["POST"])
@require_jwt_auth
def upload_filled_order_v2() -> Response:
    """
    A distributor's placed/filled order. If distributor_id isn't
    passed, suggests one from the filename (fuzzy match) but does NOT
    save the link — the frontend must show the suggestion and let the
    person confirm or pick a different distributor before re-submitting
    with distributor_id set.
    """
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return _json_response({"success": False, "error": {"message": "file is required"}}, 400)

    distributor_id = request.form.get("distributor_id", type=int)
    target_path = _save_order_fulfillment_upload_organized(uploaded_file, "Distributor", "Order Given")
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    confirmed_distributor = None
    if distributor_id:
        confirmed_distributor = db.get_master_distributor(distributor_id, workspace_id=workspace_id)
        if confirmed_distributor is None:
            return _json_response(
                {"success": False, "error": {"message": "distributor_id not found"}}, 404
            )
        # Move out of the generic temp location into the founder's
        # requested navigable structure — matched against the latest
        # active Order Sheet (same one the later SO/CI for this
        # distributor will also land under).
        distributor_name = confirmed_distributor.get("firm_name") or confirmed_distributor.get("name")
        latest_sheet = db.get_latest_order_sheet(workspace_id=workspace_id)
        order_sheet_name = latest_sheet["name"] if latest_sheet else None
        target_path = _move_into_distributor_order_cycle_folder(
            target_path, distributor_name, "FilledOrder", order_sheet_name=order_sheet_name
        )

    suggested_distributor = None
    if confirmed_distributor is None:
        suggested_distributor = _suggest_filled_order_distributor(
            Path(uploaded_file.filename).name, workspace_id
        )

    upload_id = db.save_distributor_order_upload(
        verification_session_id=str(uuid.uuid4()),
        stage_key="filled_file",
        file_type="filled_order",
        filename=Path(uploaded_file.filename).name,
        file_path=str(target_path),
        distributor_name=(confirmed_distributor or suggested_distributor or {}).get("name"),
    )

    parsed_items_count = 0
    if confirmed_distributor is not None:
        # Parse item/qty/value rows now — held as "pending" until the
        # matching Sales Order PDF is uploaded for this same
        # distributor (order_ref_no isn't known yet at this stage).
        parsed_items = _parse_filled_order_items(target_path)
        if parsed_items:
            db.save_pending_filled_order_items(
                distributor_id=confirmed_distributor["id"],
                workspace_id=workspace_id,
                items=parsed_items,
            )
            parsed_items_count = len(parsed_items)

    return _json_response({
        "success": True,
        "data": {
            "upload_id": upload_id,
            "filename": Path(uploaded_file.filename).name,
            "confirmed_distributor": confirmed_distributor,
            "suggested_distributor": suggested_distributor,
            "requires_confirmation": confirmed_distributor is None,
            "parsed_items_count": parsed_items_count,
        },
    })


def _so_pack_upload_kind(filename: str) -> str | None:
    lower = (filename or "").lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".rar"):
        return "rar"
    if lower.endswith(".pdf"):
        return "pdf"
    return None


def _so_pack_safe_filename(filename: str | None, kind: str | None = None) -> str:
    """Basename only — strip path-like Content-Disposition names from mobile clients."""
    raw = Path(filename or "so_pack").name
    raw = raw.replace("\\", "/").split("/")[-1]
    if ":" in raw:
        raw = raw.split(":")[-1]
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in raw).strip()
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "so_pack"
    if kind and not _so_pack_upload_kind(cleaned):
        cleaned = f"{cleaned}.{kind}"
    return cleaned


def _so_pack_sniff_kind(raw: bytes, filename: str | None = None) -> str | None:
    kind = _so_pack_upload_kind(filename or "")
    if kind:
        return kind
    if not raw:
        return None
    if raw[:4] == b"%PDF":
        return "pdf"
    if raw[:2] == b"PK":
        return "zip"
    if raw[:4] == b"Rar!" or raw[:7] == b"Rar!\x1a\x07\x00":
        return "rar"
    return None


def _so_pack_collect_uploads():
    """Return ('single', filename, bytes) | ('pdfs', label, list[(name,bytes)]) | raise ValueError."""
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    if not uploads:
        one = request.files.get("file")
        if one and one.filename:
            uploads = [one]
    if not uploads:
        raise ValueError("file is required")

    prepared: list[tuple[str, bytes, str]] = []
    for f in uploads:
        raw = f.read()
        if not raw:
            raise ValueError(f"Empty file: {f.filename}")
        kind = _so_pack_sniff_kind(raw, f.filename)
        if not kind:
            raise ValueError("Upload ZIP, RAR, or PDF files only")
        safe_name = _so_pack_safe_filename(f.filename, kind)
        prepared.append((safe_name, raw, kind))

    if len(prepared) == 1:
        name, raw, _kind = prepared[0]
        return "single", name, raw

    if not all(k == "pdf" for _, _, k in prepared):
        raise ValueError(
            "Multiple files must all be PDFs (use one ZIP/RAR per distributor pack)"
        )
    pdfs: list[tuple[str, bytes]] = [(name, raw) for name, raw, _ in prepared]
    label = pdfs[0][0] if len(pdfs) == 1 else f"{len(pdfs)}_PDFs"
    return "pdfs", label, pdfs


@data_blueprint.route("/api/v1/order-fulfillment/so-pack/analyze", methods=["POST"])
@require_jwt_auth
def so_pack_analyze() -> Response:
    """Unpack ZIP/RAR or accept PDF(s) → consolidated product qty/amount JSON."""
    from app.services.so_pack_consolidate import analyze_so_pack, analyze_so_pack_pdfs

    try:
        mode, label, payload = _so_pack_collect_uploads()
        if mode == "pdfs":
            data = analyze_so_pack_pdfs(payload, label)
        else:
            data = analyze_so_pack(payload, label)
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"SO pack analyze failed: {exc}"}},
            500,
        )
    return _json_response({"success": True, "data": data})


@data_blueprint.route("/api/v1/order-fulfillment/so-pack/analyze-stream", methods=["POST"])
@require_jwt_auth
def so_pack_analyze_stream() -> Response:
    """Same as analyze, but streams NDJSON progress lines then a final done event."""
    from app.services.so_pack_consolidate import (
        iter_analyze_so_pack,
        iter_analyze_so_pack_pdfs,
    )

    try:
        mode, label, payload = _so_pack_collect_uploads()
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)

    @stream_with_context
    def generate():
        try:
            events = (
                iter_analyze_so_pack_pdfs(payload, label)
                if mode == "pdfs"
                else iter_analyze_so_pack(payload, label)
            )
            for kind, item in events:
                if kind == "progress":
                    yield json.dumps({"type": "progress", "message": str(item)}) + "\n"
                elif kind == "done":
                    yield json.dumps({"type": "done", "data": item}, default=str) + "\n"
        except ValueError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
        except Exception as exc:
            yield json.dumps(
                {"type": "error", "message": f"SO pack analyze failed: {exc}"}
            ) + "\n"

    resp = Response(generate(), mimetype="application/x-ndjson")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@data_blueprint.route("/api/v1/order-fulfillment/so-pack/excel", methods=["POST"])
@require_jwt_auth
def so_pack_excel() -> Response:
    """Build Consolidated_SO_Product_Qty_Amount.xlsx from analyzed JSON or ZIP/RAR upload.

    Prefer JSON body with the analyze payload — skips re-unpacking the archive.
    """
    from app.services.so_pack_consolidate import (
        analyze_so_pack,
        build_consolidated_xlsx,
        so_pack_excel_download_name,
    )

    payload: dict[str, Any] | None = None
    # Accept JSON even when Flask request.is_json is false (charset / proxies).
    json_payload = request.get_json(silent=True, force=False)
    if json_payload is None and request.data:
        ctype = (request.content_type or "").lower()
        if "json" in ctype or (not request.files and not request.form):
            try:
                raw_body = request.get_data(cache=True, as_text=True)
                json_payload = json.loads(raw_body) if raw_body else None
            except Exception:
                json_payload = None

    try:
        if isinstance(json_payload, dict) and (
            json_payload.get("meta")
            or json_payload.get("consolidated")
            or json_payload.get("so_summary")
            or json_payload.get("line_detail")
        ):
            payload = json_payload
            xlsx_bytes = build_consolidated_xlsx(payload)
        else:
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                return _json_response(
                    {
                        "success": False,
                        "error": {
                            "message": "file is required (or send analyzed SO pack JSON)",
                        },
                    },
                    400,
                )
            fname = uploaded.filename
            lower = fname.lower()
            if not (lower.endswith(".zip") or lower.endswith(".rar") or lower.endswith(".pdf")):
                return _json_response(
                    {"success": False, "error": {"message": "Upload a .zip, .rar, or .pdf"}},
                    400,
                )
            raw = uploaded.read()
            if not raw:
                return _json_response({"success": False, "error": {"message": "Empty file"}}, 400)
            payload = analyze_so_pack(raw, fname)
            xlsx_bytes = build_consolidated_xlsx(payload)
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"SO pack excel failed: {exc}"}},
            500,
        )
    resp = send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=so_pack_excel_download_name(payload),
    )
    resp.headers["X-Nexora-SoPack-Excel"] = "assort-1design-Ncolour-v4"
    return resp


@data_blueprint.route("/api/v1/order-fulfillment/so-pack/excel-batch", methods=["POST"])
@require_jwt_auth
def so_pack_excel_batch() -> Response:
    """Build a ZIP of separate Excels — one workbook per distributor pack payload."""
    from app.services.so_pack_consolidate import build_batch_excel_zip

    json_payload = request.get_json(silent=True, force=False)
    if json_payload is None and request.data:
        ctype = (request.content_type or "").lower()
        if "json" in ctype or (not request.files and not request.form):
            try:
                raw_body = request.get_data(cache=True, as_text=True)
                json_payload = json.loads(raw_body) if raw_body else None
            except Exception:
                json_payload = None

    packs: list[Any] = []
    if isinstance(json_payload, dict):
        raw_packs = json_payload.get("packs")
        if isinstance(raw_packs, list):
            packs = raw_packs
        elif (
            json_payload.get("meta")
            or json_payload.get("consolidated")
            or json_payload.get("so_summary")
            or json_payload.get("line_detail")
        ):
            packs = [json_payload]
    elif isinstance(json_payload, list):
        packs = json_payload

    if not packs:
        return _json_response(
            {"success": False, "error": {"message": "packs[] (analyzed SO pack JSON) is required"}},
            400,
        )

    try:
        zip_bytes, download_name = build_batch_excel_zip(packs)
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"SO pack batch excel failed: {exc}"}},
            500,
        )

    resp = send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )
    resp.headers["X-Nexora-SoPack-Excel"] = "multi-pack-zip-v1"
    return resp


@data_blueprint.route("/api/v1/order-fulfillment/match-lab", methods=["POST"])
@require_jwt_auth
def fo_so_match_lab() -> Response:
    """DUMMY: compare Filled Order Excel vs SO Pack (ZIP/RAR/PDF or Pack xlsx).

    Temporary teaching endpoint — does not save or lock into Order Desk flow.
    Form fields:
      - filled_order: distributor FO .xlsx/.xls
      - so_pack: ZIP/RAR/PDF(s) and/or SO Pack .xlsx (Brand Wise Size Wise)
      - category (optional), qty_column (optional)
    """
    import os
    from app.services.fo_so_match_lab import (
        run_match_lab_files,
        write_upload_to_temp,
    )

    fo_file = request.files.get("filled_order")
    if not fo_file or not fo_file.filename:
        return _json_response(
            {"success": False, "error": {"message": "filled_order Excel is required"}},
            400,
        )

    so_uploads = [f for f in request.files.getlist("so_pack") if f and f.filename]
    if not so_uploads:
        one = request.files.get("so_pack")
        if one and one.filename:
            so_uploads = [one]
    if not so_uploads:
        return _json_response(
            {"success": False, "error": {"message": "so_pack ZIP/RAR/PDF or Pack xlsx is required"}},
            400,
        )

    category = (request.form.get("category") or "").strip() or None
    qty_column = (request.form.get("qty_column") or "").strip() or None
    tmp_paths: list[str] = []

    try:
        fo_tmp = write_upload_to_temp(fo_file)
        tmp_paths.append(str(fo_tmp))

        # Prefer SO Pack Excel if present (fast path); else ZIP/RAR/PDF analyze.
        xlsx = next(
            (f for f in so_uploads if (f.filename or "").lower().endswith((".xlsx", ".xlsm"))),
            None,
        )
        if xlsx:
            so_tmp = write_upload_to_temp(xlsx)
            tmp_paths.append(str(so_tmp))
            result = run_match_lab_files(
                fo_path=fo_tmp,
                so_path=so_tmp,
                category=category,
                pref_column_name=qty_column,
            )
        else:
            # Reuse SO Pack collector rules for archives / PDFs
            kinds = []
            for f in so_uploads:
                kind = _so_pack_upload_kind(f.filename or "")
                if not kind:
                    return _json_response(
                        {
                            "success": False,
                            "error": {
                                "message": "so_pack must be ZIP, RAR, PDF, or SO Pack .xlsx",
                            },
                        },
                        400,
                    )
                kinds.append(kind)

            if len(so_uploads) == 1 and kinds[0] in ("zip", "rar"):
                raw = so_uploads[0].read()
                if not raw:
                    return _json_response(
                        {"success": False, "error": {"message": "Empty SO pack file"}},
                        400,
                    )
                result = run_match_lab_files(
                    fo_path=fo_tmp,
                    so_mode="single",
                    so_label=so_uploads[0].filename,
                    so_payload=raw,
                    category=category,
                    pref_column_name=qty_column,
                )
            else:
                if not all(k == "pdf" for k in kinds):
                    return _json_response(
                        {
                            "success": False,
                            "error": {
                                "message": "Multiple so_pack files must all be PDFs, or one ZIP/RAR, or one Pack xlsx",
                            },
                        },
                        400,
                    )
                pdfs = []
                for f in so_uploads:
                    raw = f.read()
                    if not raw:
                        return _json_response(
                            {"success": False, "error": {"message": f"Empty file: {f.filename}"}},
                            400,
                        )
                    pdfs.append((Path(f.filename or "SO.pdf").name, raw))
                label = pdfs[0][0] if len(pdfs) == 1 else f"{len(pdfs)}_PDFs"
                result = run_match_lab_files(
                    fo_path=fo_tmp,
                    so_mode="pdfs",
                    so_label=label,
                    so_payload=pdfs,
                    category=category,
                    pref_column_name=qty_column,
                )

        if not result.get("success"):
            return _json_response({"success": False, "data": result}, 400)
        return _json_response({"success": True, "data": result})
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"Match Lab failed: {exc}"}},
            500,
        )
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


@data_blueprint.route("/api/v1/order-fulfillment/so-pack/match-filled-order", methods=["POST"])
@require_jwt_auth
def so_pack_match_filled_order() -> Response:
    """Match analyzed SO Pack JSON against a *saved* Filled Order (Order Desk flow).

    Body JSON:
      filled_order_id: int
      so_pack: analyze payload (needs line_detail)
      so_buyer_label / so_source_filename optional (for saved Order Match page)
    Persists a match run and returns it under data.run for the Order Match workspace.
    """
    import filled_orders_db as fodb
    from app.services import fo_so_match_db as matchdb
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    body = request.get_json(silent=True, force=True) or {}
    filled_order_id = body.get("filled_order_id")
    so_pack = body.get("so_pack")
    try:
        filled_order_id = int(filled_order_id)
    except (TypeError, ValueError):
        return _json_response(
            {"success": False, "error": {"message": "filled_order_id is required"}},
            400,
        )
    if not isinstance(so_pack, dict) or not (
        so_pack.get("line_detail") or so_pack.get("consolidated")
    ):
        return _json_response(
            {
                "success": False,
                "error": {"message": "so_pack analyze payload with line_detail is required"},
            },
            400,
        )

    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    if user_id is None:
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )

    so_buyer_label = (body.get("so_buyer_label") or "").strip() or None
    so_source_filename = (body.get("so_source_filename") or "").strip() or None
    if not so_source_filename:
        meta = so_pack.get("meta") or {}
        so_source_filename = meta.get("source_filename")

    conn = sqlite3.connect(_db_path())
    try:
        fodb.ensure_schema(conn)
        fo = fodb.get_filled_order(conn, user_id, filled_order_id)
        if not fo:
            return _json_response(
                {"success": False, "error": {"message": "Filled order not found"}},
                404,
            )
        items = fodb.get_filled_order_items(conn, filled_order_id)
        result = run_match_saved_fo_vs_so_pack(
            fo_meta=fo, fo_items=items, so_pack_payload=so_pack,
        )
        run = matchdb.save_match_run(
            conn,
            user_id=user_id,
            match_payload=result,
            so_buyer_label=so_buyer_label,
            so_source_filename=so_source_filename,
        )
        result["run"] = {k: v for k, v in run.items() if k != "rows"}
        result["run_id"] = run.get("id")
        return _json_response({"success": True, "data": result})
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"SO Pack FO match failed: {exc}"}},
            500,
        )
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/list", methods=["GET"])
@require_jwt_auth
def order_match_list() -> Response:
    """
    FO ↔ SO Pack match runs (Order Match page + BD app SO tab).
    Shared across team by default so a match saved on desktop is
    visible in the mobile app. Pass ?mine=1 to restrict to current user.
    """
    from app.services import fo_so_match_db as matchdb

    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    if user_id is None:
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )
    mine_only = str(request.args.get("mine") or "").strip().lower() in ("1", "true", "yes")
    conn = sqlite3.connect(_db_path())
    try:
        runs = matchdb.list_match_runs(
            conn, user_id=user_id if mine_only else None
        )
        return _json_response({"success": True, "data": {"runs": runs, "count": len(runs)}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/<int:run_id>", methods=["GET"])
@require_jwt_auth
def order_match_get(run_id: int) -> Response:
    """Match run detail with line rows — shared read for desktop + BD app."""
    from app.services import fo_so_match_db as matchdb

    user = getattr(request, "user", None)
    if not (isinstance(user, dict) and user.get("user_id") is not None):
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )
    conn = sqlite3.connect(_db_path())
    try:
        run = matchdb.get_match_run(conn, run_id, user_id=None)
        if not run:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        return _json_response({"success": True, "data": {"run": run}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/<int:run_id>", methods=["DELETE"])
@require_jwt_auth
def order_match_delete(run_id: int) -> Response:
    from app.services import fo_so_match_db as matchdb

    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    if user_id is None:
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )
    conn = sqlite3.connect(_db_path())
    try:
        ok = matchdb.delete_match_run(conn, user_id, run_id)
        if not ok:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        return _json_response({"success": True, "data": {"deleted": True}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/delete-selected", methods=["POST"])
@require_jwt_auth
def order_match_delete_selected() -> Response:
    from app.services import fo_so_match_db as matchdb

    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    if user_id is None:
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or data.get("run_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return _json_response(
            {"success": False, "error": {"message": "ids must be a non-empty list"}},
            400,
        )
    conn = sqlite3.connect(_db_path())
    deleted = 0
    try:
        for raw in raw_ids:
            try:
                run_id = int(raw)
            except (TypeError, ValueError):
                continue
            if matchdb.delete_match_run(conn, user_id, run_id):
                deleted += 1
        return _json_response({"success": True, "data": {"deleted": deleted}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/upload/sales-order", methods=["POST"])
@require_jwt_auth
def upload_sales_order_v2() -> Response:
    """
    Sales Order (SO) PDF upload. Extracts Buyer Code + Buyer GST
    (excluding the workspace's own company GST) and cross-checks both
    signals against master_distributors. If distributor_id is passed
    (the person has confirmed), the SO is linked to order lifecycle
    tracking right away; otherwise only the match summary is returned
    for the frontend to show a confirmation prompt.
    """
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return _json_response({"success": False, "error": {"message": "file is required"}}, 400)

    confirmed_distributor_id = request.form.get("distributor_id", type=int)
    confirmed_filled_order_id = request.form.get("filled_order_id", type=int)
    season_hint = (request.form.get("season") or "").strip() or None
    target_path = _save_order_fulfillment_upload_organized(uploaded_file, "SO", "SO Received")
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    try:
        extracted_text = _extract_pdf_text(target_path)
    except Exception:
        extracted_text = ""

    header = _parse_sales_order_header_fields(extracted_text)
    buyer_code = header.get("buyer_code")
    order_ref_no = header.get("order_ref_no")
    buyer_name = header.get("buyer_name")

    all_gst_numbers = [g for g in (header.get("all_gst_numbers") or "").split(",") if g]
    own_profile = db.get_company_profile(workspace_id)
    own_gst = own_profile.get("gst_number") if own_profile else None
    buyer_gst = _identify_buyer_gst(all_gst_numbers, own_gst)

    matched_by_buyer_code = (
        db.get_master_distributor_by_buyer_code(buyer_code, workspace_id=workspace_id)
        if buyer_code else None
    )
    matched_by_gst = (
        db.get_master_distributor_by_gst(buyer_gst, workspace_id=workspace_id)
        if buyer_gst else None
    )
    signals_agree = None
    if matched_by_buyer_code and matched_by_gst:
        signals_agree = matched_by_buyer_code["id"] == matched_by_gst["id"]

    parsed_sales_order = parse_step2_sales_order_pdf(target_path)

    tracking_id = None
    link_error = None
    item_results = []
    has_any_discrepancy = False
    is_duplicate = False
    suggested_filled_order = None
    filled_order_linked = False
    user = getattr(request, "user", None)
    user_id = int(user["user_id"]) if isinstance(user, dict) and user.get("user_id") is not None else None
    if confirmed_distributor_id and order_ref_no:
        if db.is_document_already_processed(workspace_id, "SO", order_ref_no):
            is_duplicate = True
            link_error = (
                f"This Sales Order (Order Ref \"{order_ref_no}\") has ALREADY been "
                f"processed — rejecting this upload to avoid double-counting quantities/"
                f"values. If this is genuinely a correction, please delete the existing "
                f"tracking record first."
            )
        else:
            try:
                confirmed_distributor_obj = db.get_master_distributor(confirmed_distributor_id, workspace_id=workspace_id)
                distributor_name_for_folder = (
                    (confirmed_distributor_obj or {}).get("firm_name")
                    or (confirmed_distributor_obj or {}).get("name")
                    or "Unassigned"
                )
                # Move out of the generic temp location into the
                # founder's requested navigable structure — matched
                # against the latest active Order Sheet.
                latest_sheet = db.get_latest_order_sheet(workspace_id=workspace_id)
                order_sheet_name = latest_sheet["name"] if latest_sheet else None
                target_path = _move_into_distributor_order_cycle_folder(
                    target_path, distributor_name_for_folder, "SO", order_sheet_name=order_sheet_name
                )
                tracking_id = db.link_sales_order_to_order_lifecycle(
                    order_ref_no=order_ref_no,
                    distributor_id=confirmed_distributor_id,
                    sales_order_file_reference=str(target_path),
                    sales_order_parsed=parsed_sales_order,
                    workspace_id=workspace_id,
                )

                # Apply Filled Order (Article Master module) as ordered_qty source.
                # Falls back to the legacy pending-queue upload when no linked
                # filled order is available for this distributor/season.
                import filled_orders_db as fodb
                import filled_orders_reconciliation as forecon
                from order_item_keys import size_code_only_item_key

                fo_conn = sqlite3.connect(_db_path())
                fodb.ensure_schema(fo_conn)
                filled_order_id_to_apply = confirmed_filled_order_id
                try:
                    if not filled_order_id_to_apply:
                        filled_order_id_to_apply = fodb.get_filled_order_id_for_tracking(
                            fo_conn, tracking_id,
                        )
                    if not filled_order_id_to_apply and user_id:
                        latest = fodb.get_latest_filled_order(
                            fo_conn,
                            user_id,
                            confirmed_distributor_id,
                            season=season_hint,
                        )
                        if latest:
                            suggested_filled_order = latest
                        if not season_hint and not latest:
                            latest_any = fodb.get_latest_filled_order(
                                fo_conn, user_id, confirmed_distributor_id,
                            )
                            if latest_any:
                                suggested_filled_order = latest_any

                    if filled_order_id_to_apply and user_id:
                        fo_row = fodb.get_filled_order(
                            fo_conn, user_id, filled_order_id_to_apply,
                        )
                        if fo_row is None:
                            raise ValueError("Filled order not found for this user")
                        ordered_results = forecon.apply_filled_order_ordered_items(
                            db,
                            tracking_id=tracking_id,
                            filled_order_id=filled_order_id_to_apply,
                            workspace_id=workspace_id,
                            conn=fo_conn,
                        )
                        fodb.link_filled_order_to_tracking(
                            fo_conn, filled_order_id_to_apply, tracking_id,
                        )
                        filled_order_linked = True
                        for ordered_result in ordered_results:
                            item_results.append(ordered_result)
                            if ordered_result.get("has_discrepancy"):
                                has_any_discrepancy = True
                finally:
                    fo_conn.close()

                if not filled_order_linked:
                    pending_ordered_items = db.get_and_consume_pending_filled_order_items(
                        distributor_id=confirmed_distributor_id, workspace_id=workspace_id
                    )
                    if pending_ordered_items:
                        for pending_item in pending_ordered_items:
                            ordered_result = db.upsert_order_lifecycle_item(
                                tracking_id=tracking_id,
                                item_name=pending_item["item_name"],
                                source="ordered",
                                qty=pending_item["qty"],
                                value=pending_item["value"],
                                workspace_id=workspace_id,
                                item_key=size_code_only_item_key(pending_item.get("item_key")),
                            )
                            item_results.append(ordered_result)
                            if ordered_result.get("has_discrepancy"):
                                has_any_discrepancy = True

                # Item-level reconciliation: parse each line item from
                # the SO PDF's actual table cells (not the flattened
                # text — see parse_bombay_dyeing_so_ci_line_items for
                # why: extract_text()'s reading order doesn't match
                # what a text-based parser expects for this format).
                # Falls back to the generic text parser if the table
                # parser finds nothing (e.g. a differently-formatted
                # SO from another source with no real bordered table).
                parsed_line_items = parse_bombay_dyeing_so_ci_line_items(target_path, "SO")
                if not parsed_line_items:
                    table_data = _parse_pdf_table_like_text(extracted_text)
                    for row in table_data.get("rows", []):
                        item_name = (row.get("product") or "").strip()
                        if not item_name:
                            continue
                        try:
                            qty = float(str(row.get("quantity") or "0").replace(",", ""))
                            rate = float(str(row.get("rate") or "0").replace(",", ""))
                        except ValueError:
                            continue
                        parsed_line_items.append({
                            "item_name": item_name,
                            "item_key": size_code_only_item_key(
                                extract_order_sheet_item_key(item_name),
                            ),
                            "qty": qty,
                            "value": qty * rate,
                        })

                for line_item in parsed_line_items:
                    norm_key = size_code_only_item_key(line_item.get("item_key"))
                    item_result = db.upsert_order_lifecycle_item(
                        tracking_id=tracking_id,
                        item_name=line_item["item_name"],
                        source="so",
                        qty=line_item["qty"],
                        value=line_item["value"],
                        workspace_id=workspace_id,
                        item_key=norm_key,
                    )
                    item_results.append(item_result)
                    if item_result.get("has_discrepancy"):
                        has_any_discrepancy = True

                if filled_order_linked and filled_order_id_to_apply:
                    fo_conn2 = sqlite3.connect(_db_path())
                    fodb.ensure_schema(fo_conn2)
                    try:
                        forecon.flag_so_items_without_filled_order_match(
                            db,
                            tracking_id=tracking_id,
                            so_line_items=parsed_line_items,
                            filled_order_id=filled_order_id_to_apply,
                            workspace_id=workspace_id,
                            conn=fo_conn2,
                        )
                    finally:
                        fo_conn2.close()

                if item_results:
                    db.generate_distributor_reconciliation_excel(tracking_id, workspace_id=workspace_id)
                db.mark_document_processed(workspace_id, "SO", order_ref_no, tracking_id)
            except Exception as exc:
                link_error = str(exc)

    return _json_response({
        "success": True,
        "data": {
            "order_ref_no": order_ref_no,
            "buyer_code": buyer_code,
            "buyer_name": buyer_name,
            "buyer_gst": buyer_gst,
            "matched_by_buyer_code": matched_by_buyer_code,
            "matched_by_gst": matched_by_gst,
            "signals_agree": signals_agree,
            "item_results": item_results,
            "has_discrepancy": has_any_discrepancy,
            "is_duplicate": is_duplicate,
            "requires_confirmation": tracking_id is None and not is_duplicate,
            "requires_filled_order_confirmation": (
                tracking_id is not None
                and not filled_order_linked
                and suggested_filled_order is not None
            ),
            "suggested_filled_order": suggested_filled_order,
            "filled_order_linked": filled_order_linked,
            "tracking_id": tracking_id,
            "link_error": link_error,
        },
    })


def _extract_amount_from_parsed_invoice(parsed_invoice: dict) -> float | None:
    """
    Pulls a usable numeric amount out of parse_step3_invoice_pdf()'s
    output, if one was captured (e.g. an "Invoice Amount:" line).
    Strips currency symbols/commas before converting to float.
    """
    parsed_fields = (parsed_invoice or {}).get("parsed") or {}
    raw_value = parsed_fields.get("invoice_amount")
    if not raw_value:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw_value))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


@data_blueprint.route("/api/v1/order-fulfillment/upload/invoice", methods=["POST"])
@require_jwt_auth
def upload_invoice_v2() -> Response:
    """
    Commercial Invoice (CI) PDF upload. Extracts the Sales Order
    Number and looks for a matching tracked Sales Order — but NEVER
    auto-links. The frontend must call the separate
    /confirm-ci-link endpoint after the person explicitly confirms.
    Confirmation is about WHICH PARTY this invoice is for (the amount
    is auto-extracted from the PDF, not something the person needs to
    type in manually).
    """
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return _json_response({"success": False, "error": {"message": "file is required"}}, 400)

    target_path = _save_order_fulfillment_upload_organized(uploaded_file, "CI", "CI Received")
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    try:
        extracted_text = _extract_pdf_text(target_path)
    except Exception:
        extracted_text = ""

    header = _parse_sales_order_header_fields(extracted_text)
    order_ref_no = header.get("order_ref_no")
    parsed_invoice = parse_step3_invoice_pdf(target_path)
    extracted_amount = _extract_amount_from_parsed_invoice(parsed_invoice)

    matching_so = None
    distributor_name = None
    if order_ref_no:
        matching_so = db.get_order_lifecycle_by_order_ref_no(order_ref_no, workspace_id=workspace_id)
        if matching_so and matching_so.get("distributor_id"):
            distributor = db.get_master_distributor(matching_so["distributor_id"], workspace_id=workspace_id)
            if distributor:
                distributor_name = distributor.get("firm_name") or distributor.get("name")

    return _json_response({
        "success": True,
        "data": {
            "order_ref_no": order_ref_no,
            "commercial_invoice_file_reference": str(target_path),
            "commercial_invoice_parsed": parsed_invoice,
            "matching_sales_order": matching_so,
            "distributor_name": distributor_name,
            "extracted_amount": extracted_amount,
            "requires_confirmation": matching_so is not None,
            "no_match_found": matching_so is None,
        },
    })


@data_blueprint.route("/api/v1/order-fulfillment/uploads", methods=["GET"])
@require_jwt_auth
def list_order_fulfillment_uploads() -> Response:
    """
    Powers the "where do my uploaded files show up" view — lists
    Order Sheets and tracked Sales Orders/Commercial Invoices
    together.
    """
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    order_sheets = db.list_order_sheets(workspace_id=workspace_id)
    tracking_records = db.list_order_lifecycle_tracking(workspace_id=workspace_id)
    return _json_response({
        "success": True,
        "data": {"order_sheets": order_sheets, "tracking_records": tracking_records},
    })


@data_blueprint.route("/api/v1/order-fulfillment/tracking/<int:tracking_id>", methods=["GET"])
@require_jwt_auth
def get_order_fulfillment_tracking(tracking_id: int) -> Response:
    """Single SO/CI lifecycle record — mobile global-search detail."""
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    tracking = db.get_order_lifecycle_tracking(tracking_id, workspace_id=workspace_id)
    if tracking is None:
        return _json_response(
            {"success": False, "error": {"message": "Tracking record not found"}},
            404,
        )

    distributor_name = "Unknown"
    distributor_id = tracking.get("distributor_id")
    if distributor_id is not None:
        try:
            with sqlite3.connect(_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT COALESCE(firm_name, name, 'Unknown') AS distributor_name "
                    "FROM master_distributors WHERE id = ?",
                    (distributor_id,),
                ).fetchone()
                if row:
                    distributor_name = row["distributor_name"]
        except Exception:
            pass

    invoice_no = None
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            ci_row = conn.execute(
                "SELECT document_number FROM processed_documents "
                "WHERE tracking_id = ? AND document_type = 'CI' LIMIT 1",
                (tracking_id,),
            ).fetchone()
            if ci_row:
                invoice_no = ci_row["document_number"]
    except Exception:
        pass
    if not invoice_no:
        invoice_no = db._extract_ci_invoice_no(tracking.get("commercial_invoice_parsed"))

    # Line-level FO/SO/CI reconciliation (match results).
    items = []
    try:
        raw_items = db.list_order_lifecycle_items_for_tracking(
            tracking_id, workspace_id=workspace_id or "default"
        )
        for it in raw_items:
            items.append(
                {
                    "id": it.get("id"),
                    "product_code": it.get("product_code"),
                    "item_key": it.get("item_key"),
                    "item_name": it.get("item_name"),
                    "brand": it.get("brand"),
                    "color": it.get("color"),
                    "ordered_qty": it.get("ordered_qty"),
                    "fulfilled_qty": it.get("fulfilled_qty"),
                    "so_qty": it.get("so_qty"),
                    "ci_qty": it.get("ci_qty"),
                    "ordered_value": it.get("ordered_value"),
                    "so_value": it.get("so_value"),
                    "ci_value": it.get("ci_value"),
                    "has_discrepancy": bool(it.get("has_discrepancy")),
                    "discrepancy_notes": it.get("discrepancy_notes"),
                }
            )
    except Exception:
        items = []

    # Don't dump huge parsed blobs to mobile — keep summary fields + items.
    payload = {
        "tracking_id": tracking.get("tracking_id"),
        "order_ref_no": tracking.get("order_ref_no"),
        "invoice_no": invoice_no,
        "distributor_id": distributor_id,
        "distributor_name": distributor_name,
        "transit_status": tracking.get("transit_status"),
        "payment_status": tracking.get("payment_status"),
        "receiving_status": tracking.get("receiving_status"),
        "order_received_date": tracking.get("order_received_date"),
        "order_filled_date": tracking.get("order_filled_date"),
        "sales_order_generated_date": tracking.get("sales_order_generated_date"),
        "commercial_invoice_date": tracking.get("commercial_invoice_date"),
        "dispatch_date": tracking.get("dispatch_date"),
        "expected_delivery_date": tracking.get("expected_delivery_date"),
        "actual_delivery_date": tracking.get("actual_delivery_date"),
        "pod_number": tracking.get("pod_number"),
        "has_sales_order": bool(tracking.get("sales_order_file_reference")),
        "has_commercial_invoice": bool(tracking.get("commercial_invoice_file_reference")),
        "order_sheet_name": tracking.get("order_sheet_name"),
        "created_at": tracking.get("created_at"),
        "items": items,
        "item_count": len(items),
    }
    return _json_response({"success": True, "data": payload})


@data_blueprint.route("/api/v1/order-fulfillment/tracking/<int:tracking_id>", methods=["DELETE"])
@require_jwt_auth
def delete_order_fulfillment_tracking(tracking_id: int) -> Response:
    """
    Deletes a tracked Sales Order/Commercial Invoice record (and its
    item-level reconciliation rows) — the Delete button shown on each
    row of the "Sales Orders / Commercial Invoices" table. Also
    removes the physical SO/CI files from disk if they're inside the
    upload root (same path-traversal safeguard as the file endpoints).
    """
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    file_references = db.delete_order_lifecycle_tracking(tracking_id, workspace_id=workspace_id)
    if file_references is None:
        return _json_response({"success": False, "error": {"message": "Tracking record not found"}}, 404)

    _cleanup_order_fulfillment_files(file_references)
    return _json_response({"success": True, "data": {"deleted_tracking_id": tracking_id}})


@data_blueprint.route("/api/v1/order-fulfillment/tracking/delete-selected", methods=["POST"])
@require_jwt_auth
def delete_selected_order_fulfillment_tracking() -> Response:
    """Bulk-delete SO/CI tracking rows (and linked files when under upload root)."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or data.get("tracking_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return _json_response(
            {"success": False, "error": {"message": "ids must be a non-empty list"}},
            400,
        )
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    deleted = 0
    for raw in raw_ids:
        try:
            tracking_id = int(raw)
        except (TypeError, ValueError):
            continue
        file_references = db.delete_order_lifecycle_tracking(
            tracking_id, workspace_id=workspace_id
        )
        if file_references is None:
            continue
        _cleanup_order_fulfillment_files(file_references)
        deleted += 1
    return _json_response({"success": True, "data": {"deleted": deleted}})


def _cleanup_order_fulfillment_files(file_references) -> None:
    if not file_references:
        return
    upload_root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    ).resolve()
    values = (
        file_references.values()
        if isinstance(file_references, dict)
        else list(file_references)
    )
    for file_ref in values:
        if not file_ref:
            continue
        try:
            file_path = Path(file_ref).resolve()
            file_path.relative_to(upload_root)
            if file_path.exists():
                file_path.unlink()
        except (ValueError, OSError):
            continue


def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(json.dumps(payload, indent=2, default=str), mimetype="application/json", status=status)


@data_blueprint.route("/api/v1/order-fulfillment/completeness/<int:distributor_id>", methods=["GET"])
@require_jwt_auth
def order_fulfillment_completeness(distributor_id: int) -> Response:
    """
    Checkpoint C: is this distributor's order fully covered by SOs
    yet, or are some items still pending? Powers a completeness
    indicator in the Order Cycle UI.
    """
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    summary = db.get_distributor_order_completeness(distributor_id, workspace_id=workspace_id)
    return _json_response({"success": True, "data": summary})


@data_blueprint.route("/api/v1/order-fulfillment/order-cycle", methods=["GET"])
@require_jwt_auth
def order_cycle_hierarchy() -> Response:
    """
    Powers the navigable "Order Cycle" tab: Financial Year -> 
    Distributor -> Order Sheet Name -> Filled Order copy / SO folder /
    CI folder. Walks the "Order Cycle" folder specifically (not the
    whole upload root) and returns a properly-typed hierarchy rather
    than generic folder/file nodes.
    """
    upload_root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    )
    order_cycle_root = upload_root / "Order Cycle"

    def _file_entry(entry: Path, relative_to: Path) -> dict:
        stat = entry.stat()
        return {
            "name": entry.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "relative_path": str(entry.relative_to(relative_to)).replace("\\", "/"),
        }

    financial_years = []
    if order_cycle_root.exists():
        for fy_dir in sorted(order_cycle_root.iterdir(), reverse=True):
            if not fy_dir.is_dir():
                continue
            distributors = []
            for dist_dir in sorted(fy_dir.iterdir(), key=lambda p: p.name.lower()):
                if not dist_dir.is_dir():
                    continue
                order_sheets = []
                for sheet_dir in sorted(dist_dir.iterdir(), key=lambda p: p.name.lower()):
                    if not sheet_dir.is_dir():
                        continue
                    filled_order_files = []
                    so_files = []
                    ci_files = []
                    for entry in sheet_dir.iterdir():
                        if entry.is_dir() and entry.name == "SO":
                            so_files = [_file_entry(f, upload_root) for f in entry.iterdir() if f.is_file()]
                        elif entry.is_dir() and entry.name == "CI":
                            ci_files = [_file_entry(f, upload_root) for f in entry.iterdir() if f.is_file()]
                        elif entry.is_file() and entry.name != "reconciliation.xlsx":
                            filled_order_files.append(_file_entry(entry, upload_root))
                    reconciliation_file = None
                    reconciliation_path = sheet_dir / "reconciliation.xlsx"
                    if reconciliation_path.exists():
                        reconciliation_file = _file_entry(reconciliation_path, upload_root)
                    order_sheets.append({
                        "name": sheet_dir.name,
                        "filled_order_files": filled_order_files,
                        "so_files": so_files,
                        "ci_files": ci_files,
                        "reconciliation_file": reconciliation_file,
                    })
                distributors.append({"name": dist_dir.name, "order_sheets": order_sheets})
            financial_years.append({"fy": fy_dir.name, "distributors": distributors})

    return _json_response({"success": True, "data": {"financial_years": financial_years}})


@data_blueprint.route("/api/v1/order-fulfillment/file-browser", methods=["GET"])
@require_jwt_auth
def order_fulfillment_file_browser() -> Response:
    """
    Walks the organized upload folder tree (Order Sheets/<category>/
    <FY>/..., Distributor/Order Given/<FY>/..., SO/SO Received/<FY>/...,
    CI/CI Received/<FY>/...) so the founder can visually confirm files
    landed exactly where expected.
    """
    upload_root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    )

    def _build_tree(directory: Path) -> dict:
        node = {"name": directory.name, "type": "folder", "children": []}
        if not directory.exists():
            return node
        for entry in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.is_dir():
                node["children"].append(_build_tree(entry))
            else:
                stat = entry.stat()
                node["children"].append({
                    "name": entry.name,
                    "type": "file",
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "relative_path": str(entry.relative_to(upload_root)).replace("\\", "/"),
                })
        return node

    if not upload_root.exists():
        return _json_response({"success": True, "data": {"name": "order_fulfillment_files", "type": "folder", "children": []}})

    tree = _build_tree(upload_root)
    return _json_response({"success": True, "data": tree})


def _resolve_order_fulfillment_file_path(requested_path: str) -> Path | None:
    """
    Shared path-traversal-safe resolver used by both the view and
    delete file endpoints. Returns None if the path escapes the
    upload root or doesn't exist.
    """
    upload_root = (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    ).resolve()
    candidate = (upload_root / requested_path).resolve()
    try:
        candidate.relative_to(upload_root)
    except ValueError:
        return None
    return candidate


@data_blueprint.route("/api/v1/order-fulfillment/file", methods=["GET"])
@require_jwt_auth
def order_fulfillment_view_file() -> Response:
    """
    Serves a single uploaded file for viewing/downloading, given its
    relative_path (as returned by the file-browser endpoint above).

    SECURITY: resolves the requested path and verifies it is genuinely
    INSIDE the upload root before serving anything — without this
    check, a request like "?path=../../../../etc/passwd" could read
    arbitrary files off the server.
    """
    requested_path = request.args.get("path", "")
    if not requested_path:
        return _json_response({"success": False, "error": {"message": "path is required"}}, 400)

    candidate = _resolve_order_fulfillment_file_path(requested_path)
    if candidate is None:
        return _json_response({"success": False, "error": {"message": "Invalid path"}}, 400)
    if not candidate.exists() or not candidate.is_file():
        return _json_response({"success": False, "error": {"message": "File not found"}}, 404)

    # download_name preserves the real filename in the browser tab/
    # save-dialog — without this the file opens under a meaningless
    # random blob-URL identifier instead of e.g. "CI_Invoice.pdf".
    return send_file(candidate, as_attachment=False, download_name=candidate.name)


@data_blueprint.route("/api/v1/order-fulfillment/file", methods=["DELETE"])
@require_jwt_auth
def order_fulfillment_delete_file() -> Response:
    """
    Deletes a single uploaded file, given its relative_path — lets the
    founder remove unwanted/duplicate/test uploads directly from the
    File Browser. Same path-traversal protection as the view route.
    """
    requested_path = request.args.get("path", "")
    if not requested_path:
        return _json_response({"success": False, "error": {"message": "path is required"}}, 400)

    candidate = _resolve_order_fulfillment_file_path(requested_path)
    if candidate is None:
        return _json_response({"success": False, "error": {"message": "Invalid path"}}, 400)
    if not candidate.exists() or not candidate.is_file():
        return _json_response({"success": False, "error": {"message": "File not found"}}, 404)

    candidate.unlink()
    return _json_response({"success": True, "data": {"deleted": requested_path}})


@data_blueprint.route("/api/v1/order-fulfillment/confirm-ci-link", methods=["POST"])
@require_jwt_auth
def confirm_ci_so_link() -> Response:
    """
    The ONLY place a Commercial Invoice actually gets linked to a
    Sales Order — deliberately requires an explicit person-initiated
    call (never triggered automatically from the stage4 upload
    handler). On a confirmed link, this ALSO creates the achievement
    record right away — "auto-achievement generation" is satisfied by
    doing it immediately after the one point where a human has
    genuinely confirmed the link is correct, not by silently guessing
    at upload time.
    """
    payload = request.get_json(silent=True) or {}
    order_ref_no = (payload.get("order_ref_no") or "").strip()
    commercial_invoice_file_reference = payload.get("commercial_invoice_file_reference")
    commercial_invoice_parsed = payload.get("commercial_invoice_parsed") or {}
    amount = payload.get("amount")
    notes = payload.get("notes")

    if not order_ref_no:
        return Response(
            json.dumps({"success": False, "error": {"message": "order_ref_no is required"}}),
            mimetype="application/json",
            status=400,
        )

    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    # Duplicate-detection: this CI's OWN Invoice No (NOT the Sales
    # Order Number it references) must be genuinely new. Re-uploading
    # the SAME invoice would silently double-count its qty/value —
    # reject rather than re-process. A DIFFERENT invoice for the same
    # order_ref_no (a legitimately separate CI) is always allowed.
    ci_raw_text = (commercial_invoice_parsed or {}).get("text") or ""
    ci_header = _parse_sales_order_header_fields(ci_raw_text)
    invoice_no = ci_header.get("invoice_no")
    if invoice_no and db.is_document_already_processed(workspace_id, "CI", invoice_no):
        return Response(
            json.dumps({
                "success": True,
                "data": {
                    "is_duplicate": True,
                    "tracking_id": None,
                    "achievement_id": None,
                    "link_error": (
                        f"This Commercial Invoice (Invoice No \"{invoice_no}\") has ALREADY "
                        f"been processed — rejecting this upload to avoid double-counting "
                        f"quantities/values."
                    ),
                },
            }),
            mimetype="application/json",
        )

    # Move the CI file out of the generic temp location into the
    # founder's requested navigable structure — uses the SAME Order
    # Sheet the matched Sales Order was already filed under.
    matching_so_for_move = db.get_order_lifecycle_by_order_ref_no(order_ref_no, workspace_id=workspace_id)
    if matching_so_for_move and matching_so_for_move.get("distributor_id") and commercial_invoice_file_reference:
        try:
            commercial_invoice_file_reference = str(
                _resolve_existing_order_fulfillment_source(commercial_invoice_file_reference)
            )
        except ValueError:
            return Response(
                json.dumps(
                    {
                        "success": False,
                        "error": {
                            "message": (
                                "commercial_invoice_file_reference must point to a file "
                                "previously uploaded under order fulfillment storage"
                            )
                        },
                    }
                ),
                mimetype="application/json",
                status=400,
            )
        distributor_for_move = db.get_master_distributor(
            matching_so_for_move["distributor_id"], workspace_id=workspace_id
        )
        if distributor_for_move:
            distributor_name_for_folder = (
                distributor_for_move.get("firm_name") or distributor_for_move.get("name") or "Unassigned"
            )
            try:
                moved_path = _move_into_distributor_order_cycle_folder(
                    Path(commercial_invoice_file_reference), distributor_name_for_folder, "CI",
                    order_sheet_name=matching_so_for_move.get("order_sheet_name"),
                )
                commercial_invoice_file_reference = str(moved_path)
            except (FileNotFoundError, OSError, ValueError):
                pass  # File already moved or missing — proceed with the validated reference

    try:
        tracking_id = db.link_commercial_invoice_to_order_lifecycle(
            order_ref_no=order_ref_no,
            commercial_invoice_file_reference=commercial_invoice_file_reference,
            commercial_invoice_parsed=commercial_invoice_parsed,
            commercial_invoice_date=None,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        return Response(
            json.dumps({"success": False, "error": {"message": str(exc)}}),
            mimetype="application/json",
            status=404,
        )

    # Item-level reconciliation: parse each line item from the CI's
    # actual table cells (see parse_bombay_dyeing_so_ci_line_items —
    # the CI's flattened text runs the CGST/SGST/IGST numeric columns
    # together with no separating whitespace at all, which is why CI
    # items previously never showed up in the reconciliation sheet).
    # Falls back to the generic text parser if nothing matches.
    item_results = []
    has_any_discrepancy = False
    parsed_line_items = (
        parse_bombay_dyeing_so_ci_line_items(commercial_invoice_file_reference, "CI")
        if commercial_invoice_file_reference else []
    )
    if not parsed_line_items:
        ci_full_text = (commercial_invoice_parsed or {}).get("text") or ""
        table_data = (commercial_invoice_parsed or {}).get("parsed") or {}
        for row in table_data.get("rows", []):
            item_name = (row.get("product") or "").strip()
            if not item_name:
                continue
            try:
                qty = float(str(row.get("quantity") or "0").replace(",", ""))
                rate = float(str(row.get("rate") or "0").replace(",", ""))
            except ValueError:
                continue
            parsed_line_items.append({
                "item_name": item_name,
                "item_key": extract_order_sheet_item_key(item_name),
                "qty": qty,
                "value": qty * rate,
            })

    for line_item in parsed_line_items:
        item_result = db.upsert_order_lifecycle_item(
            tracking_id=tracking_id,
            item_name=line_item["item_name"],
            source="ci",
            qty=line_item["qty"],
            value=line_item["value"],
            workspace_id=workspace_id,
            item_key=line_item.get("item_key"),
        )
        item_results.append(item_result)
        if item_result.get("has_discrepancy"):
            has_any_discrepancy = True
    if item_results:
        db.generate_distributor_reconciliation_excel(tracking_id, workspace_id=workspace_id)
    if invoice_no:
        db.mark_document_processed(workspace_id, "CI", invoice_no, tracking_id)

    achievement_id = None
    if amount is not None:
        try:
            current_user = getattr(request, "user", None)
            created_by = current_user.get("user_id") if isinstance(current_user, dict) else None
            achievement_id = db.create_achievement(
                order_lifecycle_tracking_id=tracking_id,
                amount=float(amount),
                currency="INR",
                source="ci",
                created_by=created_by,
                workspace_id=workspace_id,
                notes=notes,
            )
        except Exception as exc:
            # The link itself succeeded — achievement-creation failing
            # (e.g. a bad amount value) should not roll that back, but
            # must be visible to the caller.
            return Response(
                json.dumps({
                    "success": True,
                    "data": {
                        "tracking_id": tracking_id,
                        "achievement_id": None,
                        "achievement_error": str(exc),
                    },
                }),
                mimetype="application/json",
            )

    return Response(
        json.dumps({
            "success": True,
            "data": {
                "tracking_id": tracking_id,
                "achievement_id": achievement_id,
                "item_results": item_results,
                "has_discrepancy": has_any_discrepancy,
            },
        }, default=str),
        mimetype="application/json",
    )


@data_blueprint.route("/articles")
@require_jwt_auth
def articles() -> str:
    db = CentralizedDB(_db_path())
    articles = json.dumps(db.list_articles_by_category(workspace_id=get_workspace_id()), indent=2)
    return render_template_string(
        '<h1>Article Master</h1><pre>{{ articles }}</pre><p><a href="/">Back</a></p>',
        articles=articles,
    )


def _summarize_ask_nexora_search(search_payload: dict | None) -> str:
    """Human-readable Ask Nexora summary (not raw JSON dump)."""
    if not isinstance(search_payload, dict):
        return "No matching information found."
    results = search_payload.get("results")
    if not isinstance(results, dict):
        results = search_payload
    chunks: list[str] = []

    dists = results.get("distributors") or []
    if dists:
        names = [
            (d.get("firm_name") or d.get("name") or "?").strip() or "?"
            for d in dists[:5]
            if isinstance(d, dict)
        ]
        extra = f" (+{len(dists) - 5} more)" if len(dists) > 5 else ""
        chunks.append(f"{len(dists)} distributor(s): {', '.join(names)}{extra}")

    rets = results.get("retailers") or []
    if rets:
        names = [
            (r.get("name") or "?").strip() or "?"
            for r in rets[:5]
            if isinstance(r, dict)
        ]
        extra = f" (+{len(rets) - 5} more)" if len(rets) > 5 else ""
        chunks.append(f"{len(rets)} retailer(s): {', '.join(names)}{extra}")

    orders = results.get("orders") or []
    if orders:
        refs = [
            (o.get("order_ref_no") or o.get("invoice_no") or "?").strip() or "?"
            for o in orders[:5]
            if isinstance(o, dict)
        ]
        extra = f" (+{len(orders) - 5} more)" if len(orders) > 5 else ""
        chunks.append(f"{len(orders)} SO/CI: {', '.join(refs)}{extra}")

    arts = results.get("article_master") or []
    if arts:
        labels: list[str] = []
        for a in arts[:6]:
            if not isinstance(a, dict):
                continue
            brand = (a.get("brand") or "?").strip() or "?"
            size = (a.get("size") or "").strip()
            label = f"{brand} {size}".strip()

            def _as_float(value: Any) -> float:
                try:
                    return float(value) if value is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            mrp_n = _as_float(a.get("mrp"))
            ex_mill_n = _as_float(a.get("ex_mill_price"))
            price_bits = []
            if mrp_n > 0:
                price_bits.append(f"MRP ₹{int(round(mrp_n))}")
            if ex_mill_n > 0:
                price_bits.append(f"Ex-mill ₹{int(round(ex_mill_n))}")
            if price_bits:
                label = f"{label} ({', '.join(price_bits)})"
            labels.append(label)
        extra = f" (+{len(arts) - 6} more)" if len(arts) > 6 else ""
        chunks.append(f"{len(arts)} article(s): {', '.join(labels)}{extra}")

    return "; ".join(chunks) if chunks else "No matching information found."


def _find_distributor_fuzzy(
    db: CentralizedDB, entity: str, workspace_id: str | None
) -> dict[str, Any] | None:
    """Resolve a distributor from a user-typed partial name for Ask Nexora.

    get_master_distributor_by_name() requires an exact match against the
    `name` column, which in this schema is usually the owner/contact's
    personal name (e.g. "Prateek Kalra"), not the trade name — so a query
    like "kalra", which only matches firm_name "Kalra Agencies", would
    never resolve. Try the exact/aliased path first (cheap, canonicalized),
    then fall back to a substring search across name/firm_name/
    firm_nick_name.
    """
    entity = (entity or "").strip()
    if not entity:
        return None
    exact = db.get_master_distributor_by_name(entity, workspace_id=workspace_id)
    if exact:
        return exact
    like_query = f"%{entity.lower()}%"
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, distributor_id, distributor_code, firm_name, "
            "firm_nick_name, name, phone_number, location, address, "
            "pincode, email, gst_no, buyer_code, zone, region, "
            "payment_terms, credit_limit, status FROM master_distributors "
            "WHERE workspace_id = ? AND (LOWER(name) LIKE ? OR "
            "LOWER(firm_name) LIKE ? OR LOWER(firm_nick_name) LIKE ?) "
            "ORDER BY id LIMIT 1",
            (workspace_id, like_query, like_query, like_query),
        ).fetchone()
    return dict(row) if row else None


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

    # Strip wake phrases: "hey nexora", "ask nexora" (legacy jarvis still ok).
    query = re.sub(
        r"(?i)\b((hey|hi|ok|okay)\s+nexora|(ask|talk to)\s+(nexora|jarvis))\b[,:!.]?",
        "",
        query,
    ).strip()
    db = CentralizedDB(_db_path())
    intent = infer_ai_intent(query)
    ask_prefix = "Ask Nexora:"
    if not query:
        return Response(
            json.dumps(
                {
                    "intent": "wake",
                    "query": "",
                    "answer": f"{ask_prefix} Listening — ask about last visit, PJP, alerts, credit status, parties, or MRP 1000-2000.",
                },
                ensure_ascii=False,
            ),
            mimetype="application/json",
        )
    answer = f"{ask_prefix} No matching information found."

    user = getattr(request, "user", None)
    user_id = (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )
    workspace_id = get_workspace_id()

    if intent == "last_visit":
        entity = (
            query.split("to", 1)[-1].strip().rstrip("?") if "to" in query else query
        )
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            last_visit = db.get_last_visit_date("distributor", distributor["id"])
            answer = (
                f"{ask_prefix} Last visit to {distributor['name']} was on "
                f"{last_visit or 'no recorded visit'}."
            )
        else:
            answer = f"{ask_prefix} I could not find a distributor named {entity}."
    elif intent == "alerts":
        alerts = db.list_data_entry_alerts(workspace_id=workspace_id)
        answer = (
            f"{ask_prefix} You have {len(alerts)} active alerts."
            if alerts
            else f"{ask_prefix} No active alerts found."
        )
    elif intent == "pjp":
        # Use the same monthly_pjp_days table that powers the app's real
        # "This week's PJP" card (app/routes/pjp.py week_plan()) — this is
        # the user's own planned visit for today, not a generic priority
        # backlog across the whole workspace.
        today = datetime.now(timezone.utc).date().isoformat()
        pjp_row = None
        try:
            with sqlite3.connect(_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                pjp_row = conn.execute(
                    "SELECT place_to_visit, business_activity, particulars, "
                    "day_type FROM monthly_pjp_days WHERE workspace_id = ? "
                    "AND user_id = ? AND plan_date = ?",
                    (workspace_id, user_id, today),
                ).fetchone()
        except sqlite3.OperationalError:
            pjp_row = None

        place = (pjp_row["place_to_visit"] if pjp_row else None) or ""
        day_type = ((pjp_row["day_type"] if pjp_row else None) or "").lower()
        if place.strip() and place.strip().lower() not in {"holiday", "leave"}:
            activity = pjp_row["business_activity"] if pjp_row else None
            extra = f" — {activity}" if activity else ""
            answer = f"{ask_prefix} Today's planned visit: {place.strip()}{extra}."
        elif day_type in {"holiday", "leave"}:
            answer = f"{ask_prefix} Today is marked as {day_type} in your PJP."
        else:
            answer = f"{ask_prefix} No PJP entry planned for today yet."
    elif intent == "purchase_trends":
        entity = extract_party_name_candidate(query)
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            logs = db.build_distributor_purchase_behavior_logs(distributor["id"])
            answer = (
                f"{ask_prefix} Top behavior log for {distributor['name']}: "
                f"{logs[0]['category_name'] if logs else 'no data'}."
            )
        else:
            answer = (
                f"{ask_prefix} I couldn't identify the distributor for purchase trend analysis."
            )
    elif intent == "credit":
        policy_rows = db.list_credit_control(workspace_id=workspace_id)
        if policy_rows:
            non_active = [
                row
                for row in policy_rows
                if str(row.get("account_status") or "ACTIVE").upper() != "ACTIVE"
            ]
            if non_active:
                answer = (
                    f"{ask_prefix} {len(non_active)} of {len(policy_rows)} "
                    f"distributor credit account(s) are not active (on hold/blocked)."
                )
            else:
                answer = (
                    f"{ask_prefix} All {len(policy_rows)} distributor credit "
                    f"account(s) are active."
                )
        else:
            answer = f"{ask_prefix} No credit policy records found."
    elif intent == "target":
        # Use the same target_achievement_breakup source as the app's real
        # "Target vs Achievement" card (app/routes/target_achievement.py
        # get_breakup() -> list_target_distributor_breakup()), not the older
        # standalone targets_achievements table.
        entity = extract_party_name_candidate(query).strip().lower()
        year_parts: list[str] = []
        matched_name = None
        if entity:
            db.ensure_target_achievement_tables()
            with sqlite3.connect(_db_path()) as conn:
                fy_years = conn.execute(
                    "SELECT id, financial_year FROM target_achievement_years "
                    "WHERE workspace_id = ? ORDER BY financial_year",
                    (workspace_id,),
                ).fetchall()
            for year_id, fy_label in fy_years:
                breakup = db.list_target_distributor_breakup(workspace_id, year_id)
                for row in breakup:
                    name = str(row.get("distributor_name") or "")
                    nick = str(row.get("nick") or "")
                    if entity in name.lower() or (nick and entity in nick.lower()):
                        matched_name = matched_name or name
                        target_rs = float(row.get("target_lakhs") or 0) * 100_000
                        achieved_rs = float(row.get("achievement_lakhs") or 0) * 100_000
                        year_parts.append(
                            f"FY{fy_label}: purchase Rs {achieved_rs:,.0f} "
                            f"(target Rs {target_rs:,.0f})"
                        )
                        break
        if year_parts:
            answer = (
                f"{ask_prefix} {matched_name} year-wise — "
                + "; ".join(year_parts)
                + "."
            )
        else:
            answer = (
                f"{ask_prefix} No target/achievement records found for that distributor."
            )
    elif intent == "category_orders":
        entity = extract_party_name_candidate(query)
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            logs = db.build_distributor_purchase_behavior_logs(distributor["id"])
            if logs:
                by_category: dict[str, float] = {}
                for log in logs:
                    cat = log.get("category_name") or "Uncategorized"
                    by_category[cat] = by_category.get(cat, 0.0) + float(
                        log.get("total_volume") or 0.0
                    )
                top_categories = sorted(
                    by_category.items(), key=lambda item: item[1], reverse=True
                )[:5]
                breakdown = ", ".join(
                    f"{name}: {volume:,.0f} units" for name, volume in top_categories
                )
                answer = (
                    f"{ask_prefix} {distributor['name']} order volume by "
                    f"category — {breakdown}."
                )
            else:
                answer = (
                    f"{ask_prefix} No order/category data found for "
                    f"{distributor['name']}."
                )
        else:
            answer = (
                f"{ask_prefix} I couldn't identify the distributor for category breakdown."
            )
    elif intent == "owner":
        entity = extract_party_name_candidate(query)
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            firm = distributor.get("firm_name") or distributor["name"]
            answer = (
                f"{ask_prefix} {firm} — owner/contact: {distributor['name']}"
                + (
                    f", phone {distributor['phone_number']}"
                    if distributor.get("phone_number")
                    else ""
                )
                + "."
            )
        else:
            answer = f"{ask_prefix} I couldn't identify that distributor firm."
    else:
        # Strip filler words so a sentence like "distributor kalra name" or
        # "aster ka mrp aur exmill kya hai" narrows to the actual search
        # term ("kalra" / "aster") — global_search's LIKE-based matching
        # otherwise rarely matches a full natural-language sentence.
        search_query = extract_party_name_candidate(query)
        search_results = db.global_search(
            search_query, workspace_id=workspace_id, user_id=user_id
        )
        answer = f"{ask_prefix} {_summarize_ask_nexora_search(search_results)}"

    return Response(
        json.dumps(
            {"intent": intent, "query": query, "answer": answer}, ensure_ascii=False
        ),
        mimetype="application/json",
    )


@data_blueprint.route("/alerts")
@require_jwt_auth
def alerts() -> str:
    db = CentralizedDB(_db_path())
    rows = db.list_data_entry_alerts(workspace_id=get_workspace_id())
    return render_template_string(
        '<h1>Data Entry Alerts</h1><pre>{{ rows }}</pre><p><a href="/">Back</a></p>',
        rows=json.dumps(rows, indent=2),
    )


@data_blueprint.route("/credit-policy", methods=["GET", "POST"])
@require_jwt_auth
def credit_policy() -> str:
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    message = None
    if request.method == "POST":
        distributor_id = request.form.get("distributor_id", type=int)
        max_credit_limit = request.form.get("max_credit_limit", type=float)
        credit_days_allowed = request.form.get("credit_days_allowed", type=int)
        account_status = request.form.get("account_status", "ACTIVE")
        if distributor_id is not None:
            try:
                db.upsert_credit_control(
                    distributor_id,
                    max_credit_limit=max_credit_limit,
                    credit_days_allowed=credit_days_allowed,
                    account_status=account_status,
                    workspace_id=workspace_id,
                )
                message = "Credit policy saved"
            except ValueError:
                message = "Distributor not found in your workspace"
    policy_rows = db.list_credit_control(workspace_id=workspace_id)
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
@require_jwt_auth
def purchase_behavior() -> str:
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    distributor_id = request.args.get("distributor_id", type=int)
    if distributor_id is None:
        return jsonify({
            "success": False,
            "error": "distributor_id is required",
        }), 400

    # This table is keyed by distributor_id, not workspace_id directly —
    # verify the requested distributor actually belongs to this workspace
    # before building/returning its behavior logs. Without this check,
    # any authenticated user could view any workspace's distributor
    # purchase behavior just by guessing a distributor_id.
    with sqlite3.connect(_db_path()) as conn:
        owner = conn.execute(
            "SELECT id FROM master_distributors WHERE id = ? AND workspace_id = ?",
            (distributor_id, workspace_id),
        ).fetchone()
    if not owner:
        return jsonify({"success": False, "error": "Distributor not found"}), 404

    logs = db.build_distributor_purchase_behavior_logs(distributor_id)
    return render_template_string(
        '<h1>Distributor Purchase Behavior</h1><pre>{{ logs }}</pre><p><a href="/">Back</a></p>',
        logs=json.dumps(logs, indent=2),
    )


@data_blueprint.route("/pwa-dashboard")
def pwa_dashboard() -> Response:
    return Response(
        """
        <!doctype html>
        <html>
        <head>
          <meta charset=\"utf-8\"> 
          <title>Ask Nexora</title>
          <link rel=\"manifest\" href=\"/manifest.json\">
          <meta name=\"theme-color\" content=\"#0f172a\">
        </head>
        <body style=\"font-family: system-ui, sans-serif; background: #020617; color: #eef2ff; margin: 2rem;\">
          <h1>Ask Nexora</h1>
          <p>Progressive web app shell is available.</p>
          <p><a href=\"/manifest.json\" style=\"color: #facc15\">View manifest</a></p>
        </body>
        </html>
        """,
        mimetype="text/html",
    )


@data_blueprint.route("/api/v1/dashboard/summary")
@require_jwt_auth
def dashboard_summary() -> Response:
    workspace_id = get_workspace_id()
    db = CentralizedDB(_db_path())
    alerts = db.list_data_entry_alerts(workspace_id=workspace_id)
    tasks = db.list_workflow_todos_for_party(
        party_id=1, party_type="distributor", workspace_id=workspace_id
    )
    dashboard_payload = db.get_dashboard_payload(workspace_id=workspace_id)
    payload = {
        "overview": {
            "distributors": dashboard_payload["masters"]["distributors"],
            "retailers": dashboard_payload["masters"]["retailers"],
            "alerts": len(alerts),
            "tasks": len(tasks),
        },
        "suggestions": db.get_morning_suggestion_list(
            datetime.now(timezone.utc).date().isoformat(),
            workspace_id=workspace_id,
        )[:3],
    }
    return Response(
        json.dumps(payload, ensure_ascii=False), mimetype="application/json"
    )


@data_blueprint.route("/api/ui/dashboard-config", methods=["GET", "PUT"])
@require_jwt_auth
def dashboard_config() -> Response:
    if request.method == "GET":
        return Response(
            json.dumps(load_dashboard_config(), ensure_ascii=False),
            mimetype="application/json",
        )

    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Request body must be a valid JSON object",
                }
            ),
            400,
        )

    allowed_keys = {
        "brand_name",
        "app_name",
        "dashboard_title",
        "short_name",
        "theme_color",
        "background_color",
        "enabled_modules",
    }
    update_data: dict[str, Any] = {
        key: value
        for key, value in request_data.items()
        if key in allowed_keys
    }

    if "enabled_modules" in update_data and not isinstance(
        update_data["enabled_modules"], list
    ):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "enabled_modules must be a list",
                }
            ),
            400,
        )

    if not update_data:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "No valid config fields provided",
                }
            ),
            400,
        )

    new_config = save_dashboard_config(update_data)
    return Response(
        json.dumps(new_config, ensure_ascii=False), mimetype="application/json"
    )


@data_blueprint.route("/manifest.json")
def manifest() -> Response:
    config = load_dashboard_config()
    return Response(
        json.dumps(
            {
                "name": config.get("app_name", "NEXORA ENTERPRISE"),
                "short_name": config.get("short_name", "Ask Nexora"),
                "start_url": "/pwa-dashboard",
                "display": "standalone",
                "background_color": config.get("background_color", "#020617"),
                "theme_color": config.get("theme_color", "#020617"),
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
@require_jwt_auth
def workflow_gps() -> str:
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    party_id = request.args.get("party_id", type=int) or 1
    party_type = request.args.get("party_type", "distributor") or "distributor"
    tasks = db.list_workflow_todos_for_party(
        party_id=party_id, party_type=party_type, workspace_id=workspace_id
    )
    gps_logs = []
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT log_id, visit_log_id, captured_latitude, captured_longitude, geofenced_status, device_timestamp, created_at FROM gps_visit_verification_logs WHERE workspace_id = ? ORDER BY log_id DESC LIMIT 20",
            (workspace_id,),
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
@require_jwt_auth
def article_master_search() -> str:
    db_path = _db_path()
    workspace_id = get_workspace_id()
    query = request.args.get("q", "").strip()
    size_filter = request.args.get("size", "").strip()
    articles = []
    if query or size_filter:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM article_master_v2 WHERE workspace_id = ?"
            params = [workspace_id]
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
@require_jwt_auth
def retailer_download_page():
    db_path = _db_path()
    workspace_id = get_workspace_id()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        distributors = [
            dict(r)
            for r in conn.execute(
                "SELECT id, firm_name, firm_nick_name FROM master_distributors WHERE workspace_id = ? ORDER BY firm_name",
                (workspace_id,),
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
@require_jwt_auth
def retailer_download_excel():
    db_path = _db_path()
    workspace_id = get_workspace_id()
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
                WHERE r.workspace_id = ? AND (d.workspace_id = ? OR d.workspace_id IS NULL)
                ORDER BY d.firm_name, r.name
            """,
                (workspace_id, workspace_id),
            ).fetchall()
            filename = "all_retailers.xlsx"
        else:
            # Verify the requested distributor actually belongs to this workspace
            # before returning ANY retailer rows for it — prevents a caller from
            # pulling another workspace's retailers by guessing a dist_id.
            owner_check = conn.execute(
                "SELECT id FROM master_distributors WHERE id = ? AND workspace_id = ?",
                (dist_id, workspace_id),
            ).fetchone()
            if not owner_check:
                return jsonify({"success": False, "error": "Distributor not found"}), 404

            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                WHERE r.distributor_id = ? AND r.workspace_id = ?
                ORDER BY r.name
            """,
                (dist_id, workspace_id),
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
@require_jwt_auth
def retailer_download_csv():
    import csv as _csv
    from io import StringIO as _StringIO

    db_path = _db_path()
    workspace_id = get_workspace_id()
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
                WHERE r.workspace_id = ? AND (d.workspace_id = ? OR d.workspace_id IS NULL)
                ORDER BY d.firm_name, r.name
            """,
                (workspace_id, workspace_id),
            ).fetchall()
            filename = "all_retailers.csv"
        else:
            # Verify the requested distributor actually belongs to this workspace
            # before returning ANY retailer rows for it.
            owner_check = conn.execute(
                "SELECT id FROM master_distributors WHERE id = ? AND workspace_id = ?",
                (dist_id, workspace_id),
            ).fetchone()
            if not owner_check:
                return jsonify({"success": False, "error": "Distributor not found"}), 404

            rows = conn.execute(
                """
                SELECT r.id, r.retailer_code, r.name as retailer_name, r.owner_name,
                d.firm_name as distributor_name, d.firm_nick_name,
                r.location, r.phone_number, r.email, r.address, r.gst_no
                FROM master_retailers r
                LEFT JOIN master_distributors d ON r.distributor_id = d.id
                WHERE r.distributor_id = ? AND r.workspace_id = ?
                ORDER BY r.name
            """,
                (dist_id, workspace_id),
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
