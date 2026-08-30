import csv
import difflib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber

import article_master_parser as amparser

from flask import (
    Blueprint,
    Response,
    current_app,
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
from app.fiscal_year import normalize_fiscal_year
from app.routes.auth import get_workspace_id, require_jwt_auth, get_request_user_id
from app.storage.payment_drive_backup import backup_category_payment_status_to_drive
from app.routes.ask_nexora_troubleshoot import log_unresolved_query, resolve_query as resolve_unresolved_query
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
    _CALC_OP_LABELS,
    _CONTEXT_FOLLOWUP_PHRASES,
    _PARTY_QUERY_STOPWORDS,
    _SEASON_TOKEN_RE,
    detect_upload_file_type,
    expected_upload_format,
    _looks_like_past_tense_pjp_query,
    extract_party_name_candidate,
    find_absolute_date_in_query,
    find_bare_category_physical_size_query,
    find_bare_category_size_in_query,
    find_price_range_in_query,
    identity_name_hint,
    indian_number_format,
    infer_ai_intent,
    infer_distributor_name,
    margin_brand_hint,
    normalize_voice_query,
    stage_label_for_key,
    try_calculator,
    _detect_margin_field,
)
from app.verification import (
    parse_distributor_fields_from_text,
    parse_retailer_fields_from_text,
)


data_blueprint = Blueprint("data", __name__)
logger = logging.getLogger(__name__)


@data_blueprint.route("/api/v1/utils/scan-visiting-card", methods=["POST"])
@require_jwt_auth
def scan_visiting_card_generic() -> tuple[Response, int]:
    """OCR a visiting card image -> structured fields, for any logged-in
    account regardless of role/shell (Executive, Business, Distributor,
    Retailer). Never auto-saves anywhere — caller reviews/edits in the UI
    and saves through that shell's own normal create-party flow."""
    upload = request.files.get("card_image") or request.files.get("file") or request.files.get("image")
    if not upload or not getattr(upload, "filename", None):
        return jsonify(
            {"success": False, "error": {"code": "VALIDATION_ERROR", "message": "card_image file is required"}}
        ), 400

    from app.services.visiting_card_ocr import save_upload_temp, scan_visiting_card

    path = None
    try:
        path = save_upload_temp(upload)
        result = scan_visiting_card(path)
        return jsonify({"success": True, "data": result}), 200
    except MemoryError:
        return jsonify(
            {
                "success": False,
                "error": {
                    "code": "OCR_OOM",
                    "message": "Server ran out of memory reading the card. Add GEMINI_API_KEY on Render.",
                },
            }
        ), 503
    except Exception as exc:
        return jsonify(
            {"success": False, "error": {"code": "OCR_ERROR", "message": f"Card scan failed: {exc}"}}
        ), 500
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


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
    distributors = db.list_master_distributors(
        limit=200, workspace_id=workspace_id, user_id=get_request_user_id()
    )
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
#
# Also handles GLUED labels with no space (Bombay Dyeing CI buyer line):
#   "KALRA AGENCIESGST No.: 09AGSPK… Date of Issue: 22.04.2026"
_NEXT_LABEL_PATTERN = re.compile(
    r"(?:\s+|(?<=[A-Za-z0-9]))(?:"
    r"date(?:\s+of\s+issue)?|invoice\s*date|buyer\s*code|buyer\s*name|buyer\s*id|"
    r"gst\s*no\.?|gstin|address|mobile(?:\s*no\.?)?|state(?:\s*code)?|"
    r"place\s*of\s*supply|consignee|transporter|vehicle\s*no\.?|"
    r"order\s*date|contract\s*no\.?|order\s*ref(?:erence)?\s*no\.?|"
    r"sales\s*order\s*(?:no\.?|number|date)?|so\s*(?:no\.?|number)?|"
    r"customer\s*name|distributor\s*name|party\s*name|name\s*\(of|"
    r"invoice\s*no\.?|cust[\-\s]*po"
    r")\s*:",
    re.I,
)


def _truncate_at_next_label(value: str) -> str:
    match = _NEXT_LABEL_PATTERN.search(value or "")
    if match:
        return value[: match.start()].strip(" ,;-")
    return (value or "").strip()


def _clean_party_display_name(value: str | None) -> str | None:
    """Strip trailing GST/date/address junk from buyer/consignee names."""
    text = _truncate_at_next_label(value or "")
    if not text:
        return None
    # Extra hard cuts if a GSTIN or date token still leaked in
    text = re.split(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]", text, maxsplit=1)[0]
    text = re.split(r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b", text, maxsplit=1)[0]
    text = text.strip(" ,;-")
    return text or None


def _extract_consignee_display_name(text: str) -> str | None:
    """
    Bombay Dyeing CI often truncates invoice-to 'Name (of the customer)'
    mid-firm (e.g. 'Shri Ram &') while CONSIGNEE has the full name
    ('Shri Ram & Co., Meerut').
    """
    block_match = re.search(
        r"CONSIGNEE\s*\([^)]*\)(.*?)(?:Taxes\s+Payable|Description\s+of\s+Product|\bSN\b|\Z)",
        text or "",
        re.I | re.S,
    )
    block = block_match.group(1) if block_match else (text or "")
    name_match = re.search(
        r"\bName\s*:\s*(.+?)(?=\s*GST\s*No\.?|\s*GSTIN|\s*Address|\s*Transportation|\s*Vehicle|\n)",
        block,
        re.I,
    )
    if not name_match:
        return None
    return _clean_party_display_name(name_match.group(1))


def _prefer_fuller_party_name(primary: str | None, alternate: str | None) -> str | None:
    """Pick the more complete firm name when one field is truncated."""
    a = (primary or "").strip()
    b = (alternate or "").strip()
    if not a:
        return b or None
    if not b:
        return a or None
    a_core = a.rstrip(" &-").lower()
    b_low = b.lower()
    # Primary cut off at trailing '&' / short prefix of consignee
    if a.endswith("&") or a.endswith("-"):
        if a_core and a_core in b_low:
            return b
    if a_core and b_low.startswith(a_core) and len(b) > len(a) + 1:
        return b
    if a.lower() in b_low and len(b) > len(a) + 2:
        return b
    return a


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

    # Bombay Dyeing CI: "Name (of the customer): …" often sits on the SAME
    # physical line as the buyer GSTIN with no newline/space
    # ("…1ZQName (of the customer): KALRA AGENCIESGST No.: …").
    # Dedicated search beats the generic key:value loop for this layout.
    ci_name_match = re.search(
        r"Name\s*\(\s*of\s*the\s*customer\s*\)\s*:\s*(.+?)"
        r"(?=(?:GST\s*No\.?|GSTIN|Date\s*of\s*Issue|Invoice\s*Date|Address|"
        r"Mobile|State\s*Code|Place\s*of\s*Supply|CONSIGNEE|\Z))",
        normalized_text,
        re.I | re.S,
    )
    if ci_name_match:
        cleaned_ci_name = _clean_party_display_name(
            re.sub(r"\s+", " ", ci_name_match.group(1)).strip()
        )
        if cleaned_ci_name:
            parsed["buyer_name"] = cleaned_ci_name

    # GST numbers — ALL found in the document. The caller excludes
    # the workspace's own known company GST (via Company Profile) to
    # determine which remaining one is the buyer's. Deliberately does
    # NOT guess/exclude here, since this function has no workspace
    # context of its own.
    parsed_gst_list = _extract_all_gstins(text)
    if parsed_gst_list:
        parsed["all_gst_numbers"] = ",".join(parsed_gst_list)

    if parsed.get("buyer_name"):
        cleaned_buyer = _clean_party_display_name(parsed["buyer_name"])
        if cleaned_buyer:
            parsed["buyer_name"] = cleaned_buyer
        else:
            parsed.pop("buyer_name", None)

    # Prefer fuller consignee firm name when invoice-to name is truncated
    consignee_name = _extract_consignee_display_name(normalized_text)
    if consignee_name:
        parsed["consignee_name"] = consignee_name
        parsed["buyer_name"] = _prefer_fuller_party_name(
            parsed.get("buyer_name"), consignee_name
        )

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


def _extract_ci_buyer_gst(text: str, own_company_gst: str | None = None) -> str | None:
    """
    Prefer GST printed on the buyer / consignee block of a Bombay Dyeing CI,
    then fall back to 'all GSTINs minus company GST'.
    """
    own = (own_company_gst or "").strip().upper()
    gst_token = r"(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9])"
    section_patterns = (
        rf"Name\s*\(\s*of\s*the\s*customer\s*\)\s*:.*?GST\s*No\.?\s*:?\s*{gst_token}",
        rf"CONSIGNEE\s*\([^)]*\).*?GST\s*No\.?\s*:?\s*{gst_token}",
        rf"INVOICE\s*TO\s*\([^)]*\).*?GST\s*No\.?\s*:?\s*{gst_token}",
        # Glued buyer line: "KALRA AGENCIESGST No.: 09AGSPK…"
        rf"Name\s*\(\s*of\s*the\s*customer\s*\)\s*:[^\n]{{0,120}}?GST\s*No\.?\s*:?\s*{gst_token}",
    )
    for pattern in section_patterns:
        match = re.search(pattern, text or "", re.I | re.S)
        if not match:
            continue
        gst = match.group(1).upper()
        if gst and gst != own:
            return gst
    return _identify_buyer_gst(_extract_all_gstins(text), own_company_gst)


def _ci_buyer_name_lookup_variants(buyer_name: str | None) -> list[str]:
    """Variants that help map CI print names onto Customers master rows."""
    raw = (buyer_name or "").strip()
    if not raw:
        return []
    variants: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        text = (value or "").strip(" ,;-")
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        variants.append(text)

    _add(raw)
    # "Shri Ram & Co., Meerut" → "Shri Ram & Co."
    if "," in raw:
        _add(raw.split(",", 1)[0])
    # Drop trailing legal suffix noise for looser exact lookups
    trimmed = re.sub(
        r"\b(pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|llp)\s*$",
        "",
        raw,
        flags=re.I,
    ).strip(" ,;-")
    _add(trimmed)
    if "," in trimmed:
        _add(trimmed.split(",", 1)[0])
    return variants


def _distributor_public_payload(distributor: dict | None) -> dict | None:
    if not distributor:
        return None
    return {
        "id": distributor.get("id"),
        "name": distributor.get("firm_name") or distributor.get("name"),
        "firm_name": distributor.get("firm_name"),
        "firm_nick_name": distributor.get("firm_nick_name"),
        "gst_no": distributor.get("gst_no"),
        "buyer_code": distributor.get("buyer_code") or distributor.get("distributor_id"),
    }


def _match_ci_buyer_to_customers(
    db: CentralizedDB,
    *,
    buyer_name: str | None,
    buyer_gst: str | None,
    workspace_id: str | None,
    allow_fuzzy: bool = True,
) -> dict[str, Any]:
    """
    Map CI buyer (GST + printed name) onto Customers → master_distributors.
    Prefer GST, then exact name/firm/nick, then fuzzy. Never invent a party.
    """
    matched: dict[str, Any] | None = None
    match_method: str | None = None
    candidates: list[dict[str, Any]] = []

    if buyer_gst:
        matched = db.get_master_distributor_by_gst(buyer_gst, workspace_id=workspace_id)
        if matched:
            match_method = "gst"

    if not matched:
        for variant in _ci_buyer_name_lookup_variants(buyer_name):
            hit = (
                db.get_master_distributor_by_name(variant, workspace_id=workspace_id)
                or db._find_master_distributor_by_gst_or_name(
                    variant, workspace_id=workspace_id
                )
            )
            if hit:
                matched = hit
                match_method = "name"
                break
            # firm_name / nick exact (get_master_distributor_by_name only hits `name`)
            with sqlite3.connect(db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                sql = (
                    "SELECT id FROM master_distributors WHERE "
                    "(LOWER(COALESCE(firm_name,'')) = ? OR LOWER(COALESCE(firm_nick_name,'')) = ? "
                    "OR LOWER(COALESCE(name,'')) = ?)"
                )
                params: list[Any] = [variant.lower(), variant.lower(), variant.lower()]
                if workspace_id:
                    sql += " AND workspace_id = ?"
                    params.append(workspace_id)
                sql += " LIMIT 1"
                row = conn.execute(sql, params).fetchone()
            if row:
                matched = db.get_master_distributor(int(row["id"]), workspace_id=workspace_id)
                if matched:
                    match_method = "firm_name"
                    break

    if not matched and buyer_name and allow_fuzzy:
        # One fuzzy pass on the best short variant — avoid scanning the whole
        # master list repeatedly (can time out on Render free tier).
        fuzzy_queries = _ci_buyer_name_lookup_variants(buyer_name) or [buyer_name]
        query = fuzzy_queries[-1] if fuzzy_queries else buyer_name
        try:
            fuzzy = db._fuzzy_match_distributor(query, workspace_id=workspace_id)
        except Exception:
            fuzzy = {"status": "none"}
        status = fuzzy.get("status")
        if status == "matched" and fuzzy.get("distributor"):
            matched = fuzzy["distributor"]
            if matched.get("id") is not None:
                full = db.get_master_distributor(
                    int(matched["id"]), workspace_id=workspace_id
                )
                if full:
                    matched = full
            match_method = "fuzzy"
        elif status == "ambiguous":
            seen_ids: set[Any] = set()
            for cand in fuzzy.get("candidates") or []:
                payload = _distributor_public_payload(cand)
                if not payload or payload.get("id") in seen_ids:
                    continue
                candidates.append(payload)
                seen_ids.add(payload.get("id"))

    status = "matched" if matched else ("ambiguous" if candidates else "none")
    return {
        "status": status,
        "match_method": match_method,
        "buyer_name": buyer_name,
        "buyer_gst": buyer_gst,
        "distributor": _distributor_public_payload(matched),
        "candidates": candidates,
    }


def _build_ci_party_match_summary(
    *,
    ci_match: dict[str, Any],
    so_distributor: dict | None,
) -> dict[str, Any]:
    """
    Compare CI→Customers match with the SO's linked Customers distributor.
    """
    ci_dist = ci_match.get("distributor")
    so_payload = _distributor_public_payload(so_distributor)
    so_id = so_payload.get("id") if so_payload else None
    ci_id = ci_dist.get("id") if ci_dist else None
    buyer_name = ci_match.get("buyer_name")
    so_name = (so_payload or {}).get("name") if so_payload else None

    if so_id is not None and ci_id is not None:
        if int(so_id) == int(ci_id):
            return {
                "status": "matched",
                "message": (
                    f"CI buyer matches Customers distributor "
                    f"\"{(ci_dist or {}).get('name')}\" "
                    f"(via {ci_match.get('match_method') or 'lookup'}) "
                    f"and the SO party."
                ),
                "ci_distributor": ci_dist,
                "so_distributor": so_payload,
            }
        return {
            "status": "mismatch",
            "message": (
                f"CI buyer maps to Customers \"{(ci_dist or {}).get('name')}\", "
                f"but SO is linked to \"{so_name}\". Confirm before linking."
            ),
            "ci_distributor": ci_dist,
            "so_distributor": so_payload,
        }

    if so_id is not None and ci_id is None:
        if buyer_name and so_name and _names_match(buyer_name, so_name):
            return {
                "status": "matched",
                "message": (
                    f"CI buyer name matches SO party \"{so_name}\" "
                    f"(no exact Customers GST/name hit for CI alone)."
                ),
                "ci_distributor": None,
                "so_distributor": so_payload,
            }
        if ci_match.get("status") == "ambiguous":
            return {
                "status": "ambiguous",
                "message": (
                    f"CI buyer could match multiple Customers rows; "
                    f"SO party is \"{so_name}\". Confirm manually."
                ),
                "ci_distributor": None,
                "so_distributor": so_payload,
                "candidates": ci_match.get("candidates") or [],
            }
        return {
            "status": "unmatched",
            "message": (
                f"CI buyer \"{buyer_name or '—'}\" did not match Customers master; "
                f"SO party is \"{so_name}\". Confirm this is the same distributor."
            ),
            "ci_distributor": None,
            "so_distributor": so_payload,
        }

    # No SO — CI-only lane
    if ci_id is not None:
        return {
            "status": "matched",
            "message": (
                f"CI buyer matched Customers \"{(ci_dist or {}).get('name')}\" "
                f"via {ci_match.get('match_method') or 'lookup'}."
            ),
            "ci_distributor": ci_dist,
            "so_distributor": None,
        }
    if ci_match.get("status") == "ambiguous":
        return {
            "status": "ambiguous",
            "message": "CI buyer matches multiple Customers distributors — pick one.",
            "ci_distributor": None,
            "so_distributor": None,
            "candidates": ci_match.get("candidates") or [],
        }
    return {
        "status": "unmatched",
        "message": (
            f"CI buyer \"{buyer_name or '—'}\" not found in Customers. "
            f"Select the correct distributor before saving."
        ),
        "ci_distributor": None,
        "so_distributor": None,
    }


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
                            user_id=get_request_user_id(),
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
                                user_id=get_request_user_id(),
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
        limit=200, workspace_id=get_workspace_id(), user_id=get_request_user_id()
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
                        user_id=get_request_user_id(),
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

    # "100TC", "144TC", or page-split "144T C"
    tc_match = re.search(r"(\d+)\s*T\s*C\s*$", text)
    if not tc_match:
        return None
    tc = tc_match.group(1)
    remainder = text[: tc_match.start()].strip()

    # Glued fitted-sheet codes: KSFST183X198+30 / DBFST… (no word-break after KS)
    fst_match = re.search(
        r"(?<![A-Z0-9])(SB|DB|DBL|KS|KB|KDB|QB)FST(?=\d|X|\b)",
        remainder,
        re.IGNORECASE,
    )
    size = None
    if fst_match:
        size = fst_match.group(1).upper()
    else:
        # Glued packs 1+2DB and glued forms DBSET/SBSET — `\b` right after DB
        # misses DBSET (D-B-S are all word chars).
        size_match = re.search(
            r"(?:(?<=\d\+\d)|(?<![A-Z0-9]))(SB|DB|DBL|KS|KB|KDB|QB)(?:SET|SETS|BS|FS)?\b",
            remainder,
        )
        size = size_match.group(1) if size_match else None
    if size == "DBL":
        size = "DB"
    elif size in {"KDB", "QB", "KB"}:
        size = "KS"
    elif not size:
        if re.search(r"\bDUVET\b", remainder):
            size = "DUVET"
        elif re.search(r"\bCOMF(?:ORT(?:ER|OR)|ERTOR)\b", remainder):
            size = "COMF"
        elif re.search(r"\bTROUSSEAU\b", remainder):
            size = "TRS"

    units_match = re.search(r"\b\d\+\d\b", remainder)
    brand = remainder[: units_match.start()].strip() if units_match else (
        remainder.split()[0] if remainder else None
    )
    # "FLORA SB 2+2 …" — SB is size, not part of the brand.
    if brand:
        brand = re.sub(
            r"(?:\s+(?:SB|DB|DBL|KS|KB|KDB|QB)(?:SET|SETS|BS|FS)?)+$",
            "",
            brand,
            flags=re.I,
        ).strip()

    if not brand or not tc or not size:
        return None
    return f"{brand}|{tc}|{size}"


_CI_BRAND_STOP_TOKENS = frozenset({
    "SB", "DB", "DBL", "KS", "KB", "KDB", "QB",
    "SET", "BS", "FS", "SBSET", "DBSET", "KSSET",
})


def _ci_line_brand_token(item_name: str | None) -> str:
    """First brand words of a CI description — 'COTTON COMFORT DB 1+2…' → COTTON COMFORT."""
    tokens = [t for t in re.split(r"\s+", str(item_name or "").upper()) if t]
    brand: list[str] = []
    for tok in tokens:
        if tok in _CI_BRAND_STOP_TOKENS or re.match(r"\d+\+\d", tok):
            break
        if tok.isdigit() or re.fullmatch(r"\d+CM", tok):
            continue
        brand.append(tok)
        if len(brand) >= 2:
            break
    return " ".join(brand)


def _ci_am_brand_agrees_with_line(line: dict[str, Any] | None) -> bool:
    """True when Article Master brand is the same family as the PDF description."""
    if not isinstance(line, dict):
        return True
    am = line.get("article_match") if isinstance(line.get("article_match"), dict) else {}
    art = am.get("article") if isinstance(am.get("article"), dict) else {}
    brand = str(art.get("brand") or "").strip().upper()
    if not brand:
        return True
    pdf_brand = _ci_line_brand_token(str(line.get("item_name") or line.get("item_key") or ""))
    if not pdf_brand:
        return True
    first = brand.split()[0]
    pdf_first = pdf_brand.split()[0]
    return bool(first) and first == pdf_first


def _ci_line_pack_signature(item_name: str | None) -> str:
    """'BLUMEN 1+2 DBSET …' → '1+2DB'. Distinguishes double vs single of the same brand."""
    text = str(item_name or "").upper()
    text = re.sub(r"(\d\+\d)(SB|DB|DBL|KS|KB)(SET|SETS|BS|FS)?", r"\1 \2 \3", text)
    text = re.sub(r"\b(SB|DB|DBL|KS|KB)(SET|SETS|BS|FS)\b", r"\1 \2", text)
    match = re.search(r"(\d\+\d)\s*(SB|DB|DBL|KS|KB)", text)
    if not match:
        return ""
    size = match.group(2)
    if size == "DBL":
        size = "DB"
    return f"{match.group(1)}{size}"


def _ci_lines_contradict_pdf_text(lines: list | None, text: str | None) -> bool:
    """True when saved line brands/packs are not printed on this invoice PDF."""
    upper = str(text or "").upper()
    if not upper or not isinstance(lines, list):
        return False
    compact = re.sub(r"\s+", "", upper)
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        brand = _ci_line_brand_token(ln.get("item_name"))
        first = brand.split()[0] if brand else ""
        if len(first) >= 4 and first not in upper:
            return True
        pack = _ci_line_pack_signature(ln.get("item_name"))
        if pack and pack not in compact:
            return True
    return False


def _refresh_saved_ci_lines(
    lines: list | None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Re-key every saved CI/SO line from its PDF item_name and drop AM hits
    whose brand is not in that name (Flora must not stay matched to Aster).

    Runs for every invoice on GET/save — not a per-CI special case.
    """
    if not isinstance(lines, list):
        return [], False
    changed = False
    out: list[dict[str, Any]] = []
    for raw in lines:
        if not isinstance(raw, dict):
            continue
        line = dict(raw)
        name = str(line.get("item_name") or "").strip()
        if name:
            fresh_key = size_code_only_item_key(extract_order_sheet_item_key(name))
            old_key = size_code_only_item_key(line.get("item_key"))
            if fresh_key and fresh_key != old_key:
                line["item_key"] = fresh_key
                changed = True
        if not _ci_am_brand_agrees_with_line(line):
            line.pop("article_match", None)
            line.pop("article_id", None)
            changed = True
        out.append(line)
    return out, changed


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
    text = re.sub(r"\d{2,4}\s*TC\b", " ", str(cell), flags=re.I)
    cleaned = re.sub(r"[^\d.]", "", text.replace("\n", ""))
    if not cleaned:
        return None
    # Wrapped "3,767.\n40" → 3767.40; reject "4.00897.0" mash-ups.
    if cleaned.count(".") > 1:
        cleaned = cleaned[: cleaned.find(".", cleaned.find(".") + 1)]
        cleaned = cleaned.rstrip(".")
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
    # Page-end brand left alone (e.g. "FLFIEST") while next page starts mid-line
    # with SN + "1+2 KSFST…" — hold prefix and prepend to the next SN row.
    pending_prefix: str | None = None
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
                                continuation = _clean_pdf_cell_text(row[1] if len(row) > 1 else "")
                                if items and _ci_should_stitch_page_break(
                                    str(items[-1].get("item_name") or ""),
                                    continuation,
                                ):
                                    merged = f"{items[-1]['item_name']} {continuation}".strip()
                                    items[-1]["item_name"] = merged
                                    items[-1]["item_key"] = extract_order_sheet_item_key(merged)
                                continue
                            description_cell, qty_cell, net_value_cell = row[1], row[3], row[7]
                        else:
                            if len(row) < 9:
                                continue
                            serial_no = (row[0] or "").strip()
                            description_cell = row[1]
                            # Any SN can split across a page: last row of page N is
                            # truncated, first leftover row of page N+1 has no SN.
                            # Inverse split also happens: brand ("FLFIEST") left on
                            # page N with empty SN; page N+1 has SN + remainder.
                            if not serial_no.isdigit():
                                continuation = _clean_pdf_cell_text(description_cell)
                                if items and _ci_should_stitch_page_break(
                                    str(items[-1].get("item_name") or ""),
                                    continuation,
                                ):
                                    merged = f"{items[-1]['item_name']} {continuation}".strip()
                                    items[-1]["item_name"] = merged
                                    items[-1]["item_key"] = extract_order_sheet_item_key(merged)
                                    pending_prefix = None
                                elif continuation and _ci_text_is_page_prefix(continuation):
                                    pending_prefix = continuation
                                continue
                            code = None
                            qty_cell, net_value_cell = row[4], row[8]
                            hsn = _clean_pdf_cell_text(row[2]) if len(row) > 2 else None
                            uom = _clean_pdf_cell_text(row[3]) if len(row) > 3 else None
                            rate = _clean_pdf_cell_number(row[5]) if len(row) > 5 else None
                            amount = _clean_pdf_cell_number(row[6]) if len(row) > 6 else None
                            discount = _clean_pdf_cell_number(row[7]) if len(row) > 7 else None
                            # Full BD CI tables: ... Taxable, CGST r/a, SGST r/a, IGST r/a, Total
                            # Only accept tax/total cells when they pass a sanity check —
                            # short/merged rows otherwise put IGST *rate* (e.g. 5.0) into Total.
                            cgst_amt = None
                            sgst_amt = None
                            igst_rate = None
                            igst_amt = None
                            line_total = None
                            if len(row) >= 16:
                                cgst_amt = _clean_pdf_cell_number(row[10])
                                sgst_amt = _clean_pdf_cell_number(row[12])
                                igst_rate = _clean_pdf_cell_number(row[13])
                                cand_igst = _clean_pdf_cell_number(row[14])
                                cand_total = _clean_pdf_cell_number(row[15])
                                taxable_for_check = _clean_pdf_cell_number(row[8])
                                if (
                                    cand_total is not None
                                    and taxable_for_check is not None
                                    and cand_total >= max(taxable_for_check * 0.9, taxable_for_check)
                                ):
                                    line_total = cand_total
                                    # Prefer computed IGST when cell looks like a rate (e.g. 5.0)
                                    if cand_igst is not None and taxable_for_check and cand_igst > 40:
                                        igst_amt = cand_igst
                                    elif taxable_for_check is not None and cand_total is not None:
                                        igst_amt = round(cand_total - taxable_for_check, 2)
                                    if igst_rate is None or (igst_rate is not None and igst_rate > 40):
                                        if taxable_for_check and igst_amt is not None and taxable_for_check > 0:
                                            igst_rate = round((igst_amt / taxable_for_check) * 100, 2)
                                elif (
                                    cand_igst is not None
                                    and taxable_for_check is not None
                                    and 0 < cand_igst <= 40
                                ):
                                    # Cell is IGST % rate, not amount
                                    igst_rate = cand_igst
                                    igst_amt = round(taxable_for_check * (cand_igst / 100.0), 2)
                                    line_total = round(taxable_for_check + igst_amt, 2)

                        full_description = _clean_pdf_cell_text(description_cell)
                        if (
                            doc_type == "CI"
                            and pending_prefix
                            and full_description
                            and _ci_line_missing_leading_brand(full_description)
                        ):
                            full_description = f"{pending_prefix} {full_description}".strip()
                            pending_prefix = None
                        elif doc_type == "CI" and pending_prefix:
                            # Next SN already has a brand — drop stale prefix
                            pending_prefix = None
                        qty = _clean_pdf_cell_number(qty_cell)
                        net_value = _clean_pdf_cell_number(net_value_cell)
                        if not full_description or qty is None or net_value is None:
                            continue

                        item: dict[str, Any] = {
                            "item_name": full_description,
                            "item_key": extract_order_sheet_item_key(full_description),
                            "material_code": code,
                            "qty": qty,
                            "value": net_value,
                        }
                        if doc_type == "CI":
                            item.update({
                                "hsn": hsn,
                                "uom": uom,
                                "rate": rate,
                                "amount": amount,
                                "discount": discount,
                                "taxable": net_value,
                                "cgst_amt": cgst_amt,
                                "sgst_amt": sgst_amt,
                                "igst_rate": igst_rate,
                                "igst_amt": igst_amt,
                                "line_total": line_total,
                            })
                        items.append(item)
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

    # Second repair: recover missing Design+Colour tokens that still
    # exist in extract_text() but were dropped from a truncated table
    # cell (Aster 7990BGE / Blumen 7984BLU page-break cases).
    items = _repair_truncated_ci_design_colours(path, items)

    return items


_CI_DESIGN_COLOUR_EXCLUDE = frozenset({"TC", "CM", "MM", "IN", "KG", "PCS", "SET", "ASST"})
# Glued 7985BLU / 756SBL140TC / 7695BLU144T, or spaced 7684 PUR.
_CI_DESIGN_COLOUR_GLUED_RE = re.compile(
    r"(?<![A-Z0-9])(\d{3,4})([A-Z]{2,4})(?=\d{0,4}T?C?\b|(?![A-Z0-9]))",
    re.IGNORECASE,
)
_CI_DESIGN_COLOUR_SPACED_RE = re.compile(
    r"(?<![A-Z0-9])(\d{3,4})\s+([A-Z]{2,4})(?![A-Z0-9])",
    re.IGNORECASE,
)
def _ci_as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ci_lines_disagree_with_header(header: dict | None, lines: list | None) -> bool:
    """True when saved line qty/amount drifted from the CI footer (Total Pieces / Invoice Total)."""
    if not isinstance(header, dict) or not lines:
        return False
    pieces = _ci_as_float(header.get("total_pieces"))
    invoice = _ci_as_float(header.get("invoice_total"))
    qty = 0.0
    amt = 0.0
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        qty += _ci_as_float(ln.get("qty")) or 0.0
        amt += _ci_as_float(ln.get("line_total") or ln.get("value") or ln.get("taxable")) or 0.0
    if pieces is not None and pieces > 0 and qty > 0 and abs(qty - pieces) > max(0.51, pieces * 0.05):
        return True
    if invoice is not None and invoice > 0 and amt > 0 and abs(amt - invoice) > max(2.0, invoice * 0.08):
        return True
    return False


_CI_FOOTER_MARKERS = (
    "BOMBAY DYEING",
    "AUTHORIZED SIGNATORY",
    "INVOICE TOTAL",
    "PAYMENT DUE",
    "WE HEREBY CERTIFY",
    "TOTAL TAXABLE",
    "TOTAL PIECES",
    "PRO FORMA",
    "ORIGINAL FOR RECIPIENT",
)


def _ci_design_colour_tokens(text: str) -> list[str]:
    upper = (text or "").upper()
    tokens: list[str] = []
    seen: set[str] = set()
    for pattern in (_CI_DESIGN_COLOUR_GLUED_RE, _CI_DESIGN_COLOUR_SPACED_RE):
        for match in pattern.finditer(upper):
            design, colour = match.group(1), match.group(2).upper()
            if colour in _CI_DESIGN_COLOUR_EXCLUDE:
                continue
            token = f"{design}{colour}"
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def _ci_is_footer_text(text: str) -> bool:
    upper = (text or "").upper()
    return any(marker in upper for marker in _CI_FOOTER_MARKERS)


def _ci_line_needs_page_continuation(name: str) -> bool:
    """True when a parsed line is missing the design/colour/TC tail."""
    upper = (name or "").strip().upper()
    if not upper or _ci_is_footer_text(upper):
        return False
    if not _ci_design_colour_tokens(upper):
        return True
    # TC split across the page: "...7695BLU144T" + next page "C"
    if re.search(r"\d{2,4}T$", upper) and not re.search(r"\d{2,4}\s*TC\b", upper):
        return True
    return False


def _ci_text_is_page_continuation(text: str) -> bool:
    """True when an SN-empty leftover row is the rest of the previous line."""
    upper = _clean_pdf_cell_text(text).upper()
    if not upper or _ci_is_footer_text(upper) or len(upper) > 80:
        return False
    if _ci_design_colour_tokens(upper):
        return True
    if re.fullmatch(r"\d{2,4}\s*TC", upper):
        return True
    if upper in {"C", "TC", "T C", "T"}:
        return True
    if re.match(r"^\d{3,4}[A-Z]{2,4}", upper):
        return True
    # Mid-description remainder (size/pack) that belongs on the previous SN
    if re.match(r"^(?:1\+2|1\+1|(?:KS|DB|SB|KB)FST|\d{2,4}\s*[Xx×])", upper):
        return True
    return False


def _ci_text_is_page_prefix(text: str) -> bool:
    """True when an SN-empty row is the brand START of the *next* line.

    Commercial Invoice (1).PDF page 2 ends with description ``FLFIEST`` (UoM SET)
    and page 3 opens SN 14 as ``1+2 KSFST…`` — brand must be held, not dropped
    or wrongly stitched onto the previous complete SN.
    """
    upper = _clean_pdf_cell_text(text).upper()
    if not upper or _ci_is_footer_text(upper) or len(upper) > 48:
        return False
    if _ci_design_colour_tokens(upper):
        return False
    if re.search(r"\d{2,4}\s*[Xx×]\s*\d{2,4}", upper):
        return False
    if re.search(r"(?<![A-Z0-9])(KS|DB|SB|KB)FST", upper):
        return False
    # Remainder of previous line, not a next-line brand
    if re.match(r"^(?:1\+2|1\+1|\d)", upper):
        return False
    tokens = upper.split()
    if not tokens or not re.match(r"^[A-Z]{2,}$", tokens[0]):
        return False
    return True


def _ci_line_missing_leading_brand(name: str) -> bool:
    """True when a SN row description starts mid-line (brand left on prior page)."""
    upper = (name or "").strip().upper()
    if not upper:
        return False
    if re.match(r"^(?:1\+2|1\+1)\b", upper):
        return True
    if re.match(r"^(?:KS|DB|SB|KB)FST", upper):
        return True
    if re.match(r"^\d{2,4}\s*[Xx×]", upper):
        return True
    return False


def _ci_should_stitch_page_break(current_name: str, continuation: str) -> bool:
    return _ci_line_needs_page_continuation(current_name) and _ci_text_is_page_continuation(
        continuation
    )


def _repair_truncated_ci_design_colours(
    path: str | Path,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill truncated CI descriptions using design/colour tokens from full PDF text."""
    if not items:
        return items
    incomplete = [
        item
        for item in items
        if item.get("item_name") and not _ci_design_colour_tokens(str(item.get("item_name") or ""))
    ]
    if not incomplete:
        return items

    try:
        full_text = _extract_pdf_text(path) or ""
    except Exception:
        try:
            import pdfplumber as _pdfplumber

            chunks: list[str] = []
            with _pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    chunks.append(page.extract_text() or "")
            full_text = "\n".join(chunks)
        except Exception:
            return items

    available = _ci_design_colour_tokens(full_text)
    if not available:
        return items

    used: set[str] = set()
    for item in items:
        used.update(_ci_design_colour_tokens(str(item.get("item_name") or "")))
    unused = [tok for tok in available if tok not in used]
    if not unused:
        return items

    # Prefer a TC suffix from a complete sibling line.
    tc_suffix = ""
    for item in items:
        name = str(item.get("item_name") or "").upper()
        match = re.search(r"\b(\d{2,4}\s*TC)\b", name)
        if match:
            tc_suffix = match.group(1).replace(" ", "")
            break

    for item in incomplete:
        if not unused:
            break
        token = unused.pop(0)
        base = str(item.get("item_name") or "").rstrip()
        repaired = f"{base} {token}"
        if tc_suffix and tc_suffix not in repaired.upper():
            repaired = f"{repaired} {tc_suffix}"
        item["item_name"] = repaired
        item["item_key"] = extract_order_sheet_item_key(repaired)
        used.add(token)

    return items


def _normalize_doc_date(raw: str | None) -> str | None:
    """Normalize DD.MM.YYYY / DD/MM/YYYY / YYYY-MM-DD → YYYY-MM-DD."""
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _enrich_ci_header_from_text(text: str, base: dict[str, str] | None = None) -> dict[str, Any]:
    """CI-specific header fields beyond the shared SO/CI label parser."""
    header: dict[str, Any] = dict(base or {})
    patterns = {
        "invoice_date": r"(?:date\s*of\s*issue|invoice\s*date)\s*:?\s*([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4})",
        "sales_order_date": r"sales\s*order\s*date\s*:?\s*([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4})",
        "cust_po": r"cust[\-\s]*po\s*:?\s*([^\n]+)",
        "place_of_supply": r"place\s*of\s*supply\s*:?\s*([^\n]+)",
        "payment_due": r"payment\s*due\s*:?\s*([^\n]+)",
        "delivery_no": r"delivery\s*no\.?\s*:?\s*([A-Za-z0-9\-/]+)",
        "lr_no": r"l\.?r\.?\s*no\.?\s*:?\s*([A-Za-z0-9\-/]+)",
        "transporter": r"transporter\s*:?\s*([^\n]+)",
        "total_pieces": r"total\s*pieces\s*:?\s*([\d,]+)",
    }
    for key, pattern in patterns.items():
        # Always re-read Total Pieces from text — "1,188" used to save as 1.
        if header.get(key) and key != "total_pieces":
            continue
        match = re.search(pattern, text or "", re.I)
        if match:
            raw = match.group(1).strip().rstrip(":")
            if key == "total_pieces":
                try:
                    header[key] = int(float(raw.replace(",", "")))
                except ValueError:
                    header[key] = raw
            else:
                header[key] = raw

    # Amount in words / invoice total from footer block
    total_match = re.search(
        r"invoice\s*total\s*:?\s*([\d,]+\.?\d*)",
        text or "",
        re.I,
    )
    if total_match:
        try:
            header["invoice_total"] = float(total_match.group(1).replace(",", ""))
        except ValueError:
            pass
    taxable_match = re.search(
        r"total\s*taxable\s*amount\s*:?\s*([\d,]+\.?\d*)",
        text or "",
        re.I,
    )
    if taxable_match:
        try:
            header["taxable_amount"] = float(taxable_match.group(1).replace(",", ""))
        except ValueError:
            pass
    igst_match = re.search(
        r"total\s*igst\s*:?\s*([\d,]+\.?\d*)",
        text or "",
        re.I,
    )
    if igst_match:
        try:
            header["total_igst"] = float(igst_match.group(1).replace(",", ""))
        except ValueError:
            pass

    if header.get("invoice_date"):
        header["invoice_date"] = _normalize_doc_date(str(header["invoice_date"])) or header["invoice_date"]
    if header.get("sales_order_date"):
        header["sales_order_date"] = (
            _normalize_doc_date(str(header["sales_order_date"])) or header["sales_order_date"]
        )
    if header.get("buyer_name"):
        cleaned_buyer = _clean_party_display_name(str(header["buyer_name"]))
        if cleaned_buyer:
            header["buyer_name"] = cleaned_buyer
        else:
            header.pop("buyer_name", None)

    consignee_name = _extract_consignee_display_name(text or "")
    if consignee_name:
        header["consignee_name"] = consignee_name
        header["buyer_name"] = _prefer_fuller_party_name(
            header.get("buyer_name"), consignee_name
        )
    return header


def build_commercial_invoice_detail(path: str | Path) -> dict[str, Any]:
    """
    Full CI record — same spirit as SO save: header + line items + totals
    + raw text, not just invoice/SO numbers.
    """
    target = Path(path)
    try:
        text = _extract_pdf_text(target)
    except Exception as exc:
        return {"error": f"Unable to read PDF: {exc}", "source": "commercial_invoice"}

    if not (text or "").strip():
        return {"error": "Unreadable PDF text", "source": "commercial_invoice"}

    base_header = _parse_sales_order_header_fields(text)
    header = _enrich_ci_header_from_text(text, base_header)
    line_items = parse_bombay_dyeing_so_ci_line_items(target, "CI")
    for item in line_items:
        item["item_key"] = size_code_only_item_key(item.get("item_key"))

    # If footer shows IGST invoice but some line tax cells failed, fill ~5% IGST
    # from taxable value (Bombay Dyeing interstate CI pattern).
    header_igst = header.get("total_igst")
    taxable_sum = sum(float(it.get("value") or 0) for it in line_items)
    if header_igst is not None and taxable_sum > 0:
        implied_rate = (float(header_igst) / taxable_sum) * 100.0
        if 4.0 <= implied_rate <= 6.0:
            for item in line_items:
                if item.get("value") is None:
                    continue
                if item.get("igst_amt") is None:
                    item["igst_rate"] = round(implied_rate, 2)
                    item["igst_amt"] = round(float(item["value"]) * (implied_rate / 100.0), 2)
                if item.get("line_total") is None and item.get("igst_amt") is not None:
                    item["line_total"] = round(float(item["value"]) + float(item["igst_amt"]), 2)

    def _sum(key: str) -> float:
        total = 0.0
        for item in line_items:
            try:
                total += float(item.get(key) or 0)
            except (TypeError, ValueError):
                continue
        return total

    totals = {
        "qty": _sum("qty"),
        "taxable": _sum("taxable") or _sum("value"),
        "igst": _sum("igst_amt"),
        "cgst": _sum("cgst_amt"),
        "sgst": _sum("sgst_amt"),
        "line_total": _sum("line_total"),
        "invoice_total": header.get("invoice_total"),
        "taxable_amount": header.get("taxable_amount"),
        "total_igst": header.get("total_igst"),
        "line_count": len(line_items),
    }
    # Prefer footer invoice totals when line tax columns were unreliable
    if header.get("total_igst") is not None:
        totals["igst"] = header.get("total_igst")
    if totals["invoice_total"] is None and totals["line_total"]:
        totals["invoice_total"] = totals["line_total"]
    if totals.get("line_total") and totals.get("taxable") and totals["line_total"] < totals["taxable"] * 0.9:
        totals["line_total"] = totals.get("invoice_total") or totals["taxable"]

    legacy = _parse_pdf_table_like_text(text)
    return {
        "source": "commercial_invoice",
        "detail_level": "full",
        "text": text,
        "header": header,
        "line_items": line_items,
        "totals": totals,
        "parsed": legacy,
    }


def _linux_mem_available_mb() -> float | None:
    """Best-effort free RAM reading for Render/Linux; None if unavailable."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    return float(parts[1]) / 1024.0  # kB → MB
    except Exception:
        return None
    return None


def _ci_table_parse_enabled() -> bool:
    """
    pdfplumber extract_tables OOMs on Render Starter (512MB) and kills the
    worker → HTTP 502 HTML. Only enable table parse when explicitly forced
    or when enough free RAM is available (~Standard 2GB).
    """
    flag = (os.environ.get("CI_ENABLE_TABLE_PARSE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    available = _linux_mem_available_mb()
    if available is not None:
        return available >= 800
    return False


def _prepare_ci_parsed_for_save(
    path: str | Path | None,
    client_parsed: dict | None = None,
) -> dict[str, Any]:
    """
    Build a save-safe CI payload. Text/header only on small instances;
    full table detail only when RAM allows (avoids 502 OOM on confirm).
    """
    client = client_parsed if isinstance(client_parsed, dict) else {}
    text = ""
    if path:
        try:
            text = _extract_pdf_text(path) or ""
        except Exception:
            text = ""
    if not text:
        text = str(client.get("text") or "")

    base_header: dict[str, Any] = {}
    if isinstance(client.get("header"), dict):
        base_header.update(client["header"])
    parsed_header = _parse_sales_order_header_fields(text)
    for key, value in parsed_header.items():
        if value not in (None, ""):
            base_header[key] = value
    header = _enrich_ci_header_from_text(text, base_header)

    if path and _ci_table_parse_enabled():
        try:
            full = build_commercial_invoice_detail(path)
            if isinstance(full, dict) and not full.get("error"):
                return full
        except Exception:
            pass

    totals_in = client.get("totals") if isinstance(client.get("totals"), dict) else {}
    invoice_total = header.get("invoice_total")
    if invoice_total is None:
        invoice_total = totals_in.get("invoice_total")
    saved_lines, _ = _refresh_saved_ci_lines(list(client.get("line_items") or []))
    return {
        "source": "commercial_invoice",
        "detail_level": "text_only_save",
        "text": text,
        "header": header,
        "line_items": saved_lines,
        "totals": {
            "invoice_total": invoice_total,
            "taxable_amount": header.get("taxable_amount") or totals_in.get("taxable_amount"),
            "total_igst": header.get("total_igst") or totals_in.get("total_igst"),
            "line_count": len(client.get("line_items") or []),
        },
        "parse_note": (
            "Line-item table parse skipped to avoid OOM on small instances; "
            "header/amount saved. Set CI_ENABLE_TABLE_PARSE=1 on 2GB+ to enable."
        ),
    }


def _parse_filled_order_items(file_path: Path) -> list[dict[str, Any]]:
    """
    Reads a distributor's Filled/Placed Order spreadsheet (xlsx/xls/
    csv) and extracts item/quantity/value rows at Brand+TC+Size
    granularity, using flexible column-name matching since different
    distributors' sheets don't all use the same headers.

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
        user_id=_current_user_id(),
    )
    # Order Sheets were only ever written to the local upload folder, which is
    # on an ephemeral disk. Push to Drive, record where it landed, then drop
    # the server copy — no-op unless Drive confirmed it.
    try:
        from app.storage.nexora_docs import push_file_to_nexora_drive

        uploaded = push_file_to_nexora_drive(
            user_id=_current_user_id(),
            workspace_id=workspace_id,
            local_path=target_path,
            subfolder="Order Sheets",
            display_name=f"{name} {category}{Path(str(target_path)).suffix}",
            replace_if_exists=True,
        )
        sheet_drive_file_id = (uploaded or {}).get("id")
        if sheet_drive_file_id:
            db.set_order_sheet_drive_file_id(
                sheet_id, str(sheet_drive_file_id), workspace_id=workspace_id
            )
            _drop_local_after_drive_backup(target_path, str(sheet_drive_file_id))
    except Exception:
        logger.exception("Order Sheet Drive backup failed for sheet %s", sheet_id)

    sheet = db.get_order_sheet(
        sheet_id, workspace_id=workspace_id, user_id=_current_user_id()
    )
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
    _backup_so_pack_upload_to_drive(
        user_id=get_request_user_id(),
        workspace_id=get_workspace_id(),
        mode=mode,
        label=label,
        payload=payload,
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

    user_id = get_request_user_id()
    workspace_id = get_workspace_id()

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
                    _backup_so_pack_upload_to_drive(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        mode=mode,
                        label=label,
                        payload=payload,
                    )
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
      so_buyer_label / so_source_filename optional
      confirm_action: optional replace | split | additional
      parent_so_number: required when confirm_action=split

    Revision rules:
      - same SO# + same content → already_in_system
      - same SO# + changed qty/value → replace_confirmation_required
      - new SO# overlapping materials on this FO → split_or_additional_required
    """
    import filled_orders_db as fodb
    from app.services import fo_so_match_db as matchdb
    from app.services import fo_so_revision as sorev
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack
    from app.services.order_stream import (
        build_mixed_zip_retry_hint,
        classify_so_pack_stream,
        filter_so_pack_by_stream,
        streams_compatible,
        stream_display_label,
    )

    body = request.get_json(silent=True, force=True) or {}
    filled_order_id = body.get("filled_order_id")
    so_pack = body.get("so_pack")
    confirm_action = (body.get("confirm_action") or "").strip().lower() or None
    parent_so_number = (body.get("parent_so_number") or "").strip() or None
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
        fo_stream = (fo.get("order_stream") or "regular").strip().lower()
        pack_stream = classify_so_pack_stream(so_pack)
        pack_was_mixed = pack_stream == "mixed"
        if pack_was_mixed:
            so_pack = filter_so_pack_by_stream(so_pack, fo_stream)
            if not (so_pack.get("line_detail") or so_pack.get("consolidated")):
                return _json_response(
                    {
                        "success": False,
                        "error": {
                            "code": "stream_mismatch",
                            "message": (
                                f"This zip contains both Regular and Special SOs. "
                                f"No {stream_display_label(fo_stream)} SO lines found for this Filled Order."
                            ),
                        },
                    },
                    409,
                )
            pack_stream = fo_stream
        elif not streams_compatible(fo_stream, pack_stream):
            return _json_response(
                {
                    "success": False,
                    "error": {
                        "code": "stream_mismatch",
                        "message": (
                            f"Filled Order is {stream_display_label(fo_stream)} but this SO pack is "
                            f"{stream_display_label(pack_stream)}. Upload the matching stream "
                            "(Regular FO ↔ Regular SO, Special FO ↔ Special/SPL SO)."
                        ),
                    },
                },
                409,
            )
        new_lines = [r for r in (so_pack.get("line_detail") or []) if isinstance(r, dict)]
        new_numbers = matchdb.extract_so_numbers_from_pack(so_pack)
        items = fodb.get_filled_order_items(conn, filled_order_id)
        existing = sorev.get_latest_run_for_fo(
            conn, user_id=user_id, filled_order_id=filled_order_id
        )
        conflicts = matchdb.find_so_number_conflicts(conn, new_numbers)
        # SO index is global — only revise when the conflicting run is this same FO.
        fo_conflicts = [
            c
            for c in conflicts
            if int(c.get("filled_order_id") or 0) == int(filled_order_id)
            or (
                existing
                and int(c.get("run_id") or 0) == int(existing.get("id") or 0)
            )
        ]
        other_fo_conflicts = [c for c in conflicts if c not in fo_conflicts]
        if other_fo_conflicts and not confirm_action:
            return _json_response(
                {
                    "success": False,
                    "error": {
                        "code": "duplicate_sales_order",
                        "message": (
                            "Sales Order already matched to a different Filled Order. "
                            "Delete it from Order Match first, then upload again."
                        ),
                        "conflicts": other_fo_conflicts,
                    },
                },
                409,
            )
        conflicts = fo_conflicts

        decision = sorev.analyze_incoming_against_existing(
            existing_run=existing,
            so_pack=so_pack,
            conflicts=conflicts,
        )

        def _already_in_system_error() -> dict[str, Any]:
            err: dict[str, Any] = {
                "code": "so_already_in_system",
                "message": "SO already in system — no change detected.",
                "compares": decision.get("compares") or [],
            }
            hint = build_mixed_zip_retry_hint(
                conn,
                user_id=user_id,
                fo=fo,
                so_source_filename=so_source_filename,
                pack_was_mixed=pack_was_mixed,
            )
            if hint:
                err.update(hint)
                err["message"] = hint.get("message") or err["message"]
            return err

        if confirm_action and decision["action"] == "already_in_system":
            return _json_response(
                {"success": False, "error": _already_in_system_error()},
                409,
            )

        if not confirm_action:
            if decision["action"] == "already_in_system":
                return _json_response(
                    {"success": False, "error": _already_in_system_error()},
                    409,
                )
            if decision["action"] == "replace_confirm":
                return _json_response(
                    {
                        "success": False,
                        "error": {
                            "code": "so_replace_confirmation_required",
                            "message": (
                                "Sales Order revision detected — confirm replace "
                                "old SO with new SO (full details below)."
                            ),
                            "compares": decision.get("compares") or [],
                        },
                    },
                    409,
                )
            if decision["action"] == "split_or_additional":
                return _json_response(
                    {
                        "success": False,
                        "error": {
                            "code": "so_split_or_additional_required",
                            "message": (
                                "This SO overlaps materials already on this FO — "
                                "choose Additional order or SO split."
                            ),
                            "parent_candidates": decision.get("parent_candidates") or [],
                            "new_summary": decision.get("new_summary"),
                            "run_id": decision.get("run_id"),
                            "filled_order_id": filled_order_id,
                            "season": decision.get("season"),
                            "category": decision.get("category"),
                            "fo_leftover_qty": decision.get("fo_leftover_qty"),
                            "recommended_action": decision.get("recommended_action")
                            or "split",
                        },
                    },
                    409,
                )

        # Build merged line_detail when revising an existing FO match.
        working_pack = so_pack
        replaced_note = None
        # New SO that fits FO leftover (after replace) merges as Additional —
        # including when materials still overlap the reduced parent (Balaji 543).
        effective_action = confirm_action
        if (
            not effective_action
            and existing
            and decision["action"] == "save_new"
            and new_lines
        ):
            effective_action = "additional"
            if decision.get("auto_additional"):
                replaced_note = (
                    f"Additional SO linked to FO leftover "
                    f"({decision.get('fo_leftover_qty')} pcs open)"
                )
            else:
                replaced_note = "Additional order SO added"

        if existing and effective_action in ("replace", "split", "additional"):
            existing_lines = list(existing.get("so_line_detail") or [])
            if effective_action == "replace":
                replace_nums = {
                    str(c.get("so_number") or "")
                    for c in conflicts
                    if c.get("so_number")
                }
                if not replace_nums:
                    replace_nums = set(new_numbers)
                merged = sorev.merge_lines_for_replace(
                    existing_lines, new_lines, replace_nums
                )
                replaced_note = f"Replaced SO {', '.join(sorted(replace_nums))}"
            elif effective_action == "split":
                if not parent_so_number:
                    return _json_response(
                        {
                            "success": False,
                            "error": {
                                "message": "parent_so_number is required for split",
                            },
                        },
                        400,
                    )
                merged = sorev.merge_lines_for_split(
                    existing_lines, new_lines, parent_so_number
                )
                replaced_note = (
                    f"Split from SO {parent_so_number} — parent reduced, new SO added"
                )
            else:  # additional
                merged = sorev.merge_lines_for_additional(existing_lines, new_lines)
                if not replaced_note:
                    replaced_note = "Additional order SO added"
            working_pack = sorev.pack_from_lines(
                merged, source_filename=so_source_filename
            )
            # Free SO index + drop previous FO run before saving the merged match.
            from app.services import order_desk_archive as oda

            oda.archive_match_run(conn, user_id, existing, restore_scope="entity")
            matchdb.delete_match_run(conn, user_id, int(existing["id"]))
            for c in conflicts:
                rid = c.get("run_id")
                if rid and int(rid) != int(existing["id"]):
                    matchdb.delete_match_run(conn, user_id, int(rid))

        result = run_match_saved_fo_vs_so_pack(
            fo_meta=fo, fo_items=items, so_pack_payload=working_pack,
        )
        try:
            run = matchdb.save_match_run(
                conn,
                user_id=user_id,
                match_payload=result,
                so_buyer_label=so_buyer_label,
                so_source_filename=so_source_filename,
                so_line_detail=working_pack.get("line_detail")
                if isinstance(working_pack.get("line_detail"), list)
                else None,
                so_pack=working_pack,
            )
        except matchdb.DuplicateSalesOrderError as dup:
            return _json_response(
                {
                    "success": False,
                    "error": {
                        "code": "duplicate_sales_order",
                        "message": str(dup),
                        "conflicts": dup.conflicts,
                    },
                },
                409,
            )
        from app.services import order_desk_archive as oda

        oda.restore_match_archives_after_save(
            conn,
            user_id,
            int(run["id"]),
            filled_order_id,
            new_numbers,
        )
        oda.restore_match_run_archives_after_save(
            conn,
            user_id,
            int(run["id"]),
            filled_order_id,
            new_numbers,
        )
        result["run"] = {k: v for k, v in run.items() if k != "rows"}
        result["run_id"] = run.get("id")
        if replaced_note:
            result["revision_note"] = replaced_note
            result["confirm_action"] = effective_action
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
    Hard-isolated by JWT user_id — each user only sees their own runs.
    The legacy ?mine= query param is ignored (always mine).
    """
    from app.services import fo_so_match_db as matchdb

    user = getattr(request, "user", None)
    try:
        user_id = (
            int(user["user_id"])
            if isinstance(user, dict) and user.get("user_id") is not None
            else None
        )
    except (TypeError, ValueError):
        user_id = None
    if user_id is None:
        return _json_response(
            {"success": False, "error": {"message": "Authentication required"}},
            401,
        )
    conn = sqlite3.connect(_db_path())
    try:
        runs = matchdb.list_match_runs(conn, user_id=user_id)
        try:
            from app.services import order_desk_archive as oda

            oda.maybe_purge(conn)
        except Exception:
            current_app.logger.exception("order_match_list: archive purge skipped")
        return _json_response({"success": True, "data": {"runs": runs, "count": len(runs)}})
    except Exception as exc:
        current_app.logger.exception("order_match_list failed for user_id=%s", user_id)
        return _json_response(
            {"success": False, "error": {"message": f"Could not load Order Match list: {exc}"}},
            500,
        )
    finally:
        conn.close()


def _collect_order_match_so_numbers(run: dict) -> set[str]:
    nums: set[str] = set()
    for r in run.get("rows") or []:
        if not isinstance(r, dict):
            continue
        for n in r.get("so_numbers") or []:
            s = str(n or "").strip()
            if s:
                nums.add(s)
        for cell in r.get("so_breakdown") or []:
            if isinstance(cell, dict):
                s = str(cell.get("so_number") or "").strip()
                if s:
                    nums.add(s)
    return nums


def _enrich_order_match_so_documents(
    run: dict,
    *,
    db: CentralizedDB,
    workspace_id: str,
    user_id: int | None,
) -> None:
    """Per-SO Drive / lifecycle links so mobile can open SO & CI PDFs."""
    docs: dict[str, dict[str, Any]] = {}
    for so in _collect_order_match_so_numbers(run):
        entry: dict[str, Any] = {"so_number": so}
        tracking = db.get_order_lifecycle_by_order_ref_no(so, workspace_id=workspace_id)
        if tracking:
            tid = tracking.get("tracking_id")
            entry["tracking_id"] = tid
            try:
                with sqlite3.connect(db.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        """
                        SELECT sales_order_drive_file_id, commercial_invoice_drive_file_id,
                               sales_order_file_reference, commercial_invoice_file_reference
                        FROM order_lifecycle_tracking WHERE tracking_id = ?
                        """,
                        (int(tid),),
                    ).fetchone()
                if row:
                    so_drive = (row["sales_order_drive_file_id"] or "").strip() or None
                    ci_drive = (row["commercial_invoice_drive_file_id"] or "").strip() or None
                    entry["so_drive_file_id"] = so_drive
                    entry["ci_drive_file_id"] = ci_drive
                    entry["has_so_pdf"] = bool(
                        so_drive or (row["sales_order_file_reference"] or "").strip()
                    )
                    entry["has_ci_pdf"] = bool(
                        ci_drive or (row["commercial_invoice_file_reference"] or "").strip()
                    )
            except Exception:
                pass
        docs[so] = entry
    run["so_documents"] = docs


@data_blueprint.route("/api/v1/order-fulfillment/order-match/<int:run_id>", methods=["GET"])
@require_jwt_auth
def order_match_get(run_id: int) -> Response:
    """Match run detail with line rows — owner-only (JWT user_id)."""
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
        run = matchdb.get_match_run(conn, run_id, user_id=user_id)
        if not run:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        db = CentralizedDB(_db_path())
        _enrich_order_match_so_documents(
            run,
            db=db,
            workspace_id=get_workspace_id() or "default",
            user_id=user_id,
        )
        return _json_response({"success": True, "data": {"run": run}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/<int:run_id>", methods=["DELETE"])
@require_jwt_auth
def order_match_delete(run_id: int) -> Response:
    from app.services import fo_so_match_db as matchdb
    from app.services import order_desk_archive as oda

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
    so_number = (request.args.get("so_number") or "").strip() or None
    confirm_all = request.args.get("confirm_all") == "1"
    conn = sqlite3.connect(_db_path())
    try:
        run = matchdb.get_match_run(conn, run_id, user_id=user_id)
        if not run:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        if so_number:
            oda.archive_match_so(conn, user_id, run, so_number, restore_scope="entity")
            result = matchdb.delete_match_so_from_run(conn, user_id, run_id, so_number)
            if result is None:
                return _json_response(
                    {"success": False, "error": {"message": "Match run not found"}},
                    404,
                )
            conn.commit()
            data: dict[str, Any] = {"deleted": True, "so_number": so_number}
            if result.get("deleted_run"):
                data["deleted_run_id"] = run_id
            return _json_response({"success": True, "data": data})

        so_numbers = matchdb.extract_so_numbers_from_run_row(run)
        if len(so_numbers) > 1 and not confirm_all:
            return _json_response(
                {
                    "success": False,
                    "error": {
                        "code": "match_run_has_multiple_so",
                        "message": (
                            "This match holds multiple Sales Orders. "
                            "Confirm delete all, or delete one SO at a time."
                        ),
                        "so_numbers": so_numbers,
                    },
                },
                409,
            )
        oda.archive_match_run(conn, user_id, run, restore_scope="run")
        ok = matchdb.delete_match_run(conn, user_id, run_id)
        if not ok:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        conn.commit()
        return _json_response({"success": True, "data": {"deleted": True}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/delete-selected", methods=["POST"])
@require_jwt_auth
def order_match_delete_selected() -> Response:
    from app.services import fo_so_match_db as matchdb
    from app.services import order_desk_archive as oda

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
            run = matchdb.get_match_run(conn, run_id, user_id=user_id)
            if not run:
                continue
            oda.archive_match_run(conn, user_id, run, restore_scope="run")
            if matchdb.delete_match_run(conn, user_id, run_id):
                deleted += 1
        conn.commit()
        return _json_response({"success": True, "data": {"deleted": deleted}})
    finally:
        conn.close()


@data_blueprint.route("/api/v1/order-fulfillment/order-match/<int:run_id>/strip-so", methods=["POST"])
@require_jwt_auth
def order_match_strip_so(run_id: int) -> Response:
    """Delete one SO from a match run (alias for DELETE with so_number)."""
    from app.services import fo_so_match_db as matchdb
    from app.services import order_desk_archive as oda

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
    body = request.get_json(silent=True) or {}
    so_number = (body.get("so_number") or request.args.get("so_number") or "").strip()
    if not so_number:
        return _json_response(
            {"success": False, "error": {"message": "so_number is required"}},
            400,
        )
    conn = sqlite3.connect(_db_path())
    try:
        run = matchdb.get_match_run(conn, run_id, user_id=user_id)
        if not run:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        oda.archive_match_so(conn, user_id, run, so_number, restore_scope="entity")
        result = matchdb.delete_match_so_from_run(conn, user_id, run_id, so_number)
        if result is None:
            return _json_response(
                {"success": False, "error": {"message": "Match run not found"}},
                404,
            )
        conn.commit()
        data: dict[str, Any] = {"deleted": True, "so_number": so_number}
        if result.get("deleted_run"):
            data["deleted_run_id"] = run_id
        return _json_response({"success": True, "data": data})
    finally:
        conn.close()


def _archive_order_pdf_to_drive(
    *,
    db: CentralizedDB,
    user_id: int | None,
    workspace_id: str,
    tracking_id: int | None,
    kind: str,
    local_path: str | Path | None,
    display_name: str,
) -> str | None:
    """Best-effort: copy SO/CI PDF into Drive/NEXORA (does not fail the upload).

    Returns the Drive file id when the upload is confirmed, so the caller can
    then drop the server-side copy — see _drop_local_after_drive_backup().
    """
    if not tracking_id or not local_path:
        return None
    from app.storage.nexora_docs import push_pdf_to_nexora_drive

    subfolder = "Sales Orders" if kind == "so" else "Commercial Invoices"
    uploaded = push_pdf_to_nexora_drive(
        user_id=user_id,
        workspace_id=workspace_id,
        local_path=local_path,
        subfolder=subfolder,
        display_name=display_name,
        replace_if_exists=True,
    )
    file_id = (uploaded or {}).get("id")
    if file_id:
        try:
            db.set_order_lifecycle_drive_file_id(
                int(tracking_id), kind, str(file_id), workspace_id=workspace_id
            )
        except Exception:
            # Without the stored id nothing can find the Drive copy later, so
            # the local file must stay as the only way to reach this document.
            return None
    return str(file_id) if file_id else None


def _backup_so_pack_upload_to_drive(
    *,
    user_id: int | None,
    workspace_id: str | None,
    mode: str,
    label: str,
    payload: Any,
) -> None:
    """Best-effort: push each SO PDF into Drive/NEXORA/Sales Orders (never the zip).

    Order Desk analyze parses in memory — without this hook uploads never reach
    Drive. Archives are unpacked so Drive holds the same separate PDFs the user
    would get from loose uploads.
    """
    if not user_id:
        return
    from app.services.so_pack_consolidate import _load_pack_pdfs
    from app.storage.nexora_docs import push_file_to_nexora_drive, remove_file_from_nexora_drive

    archive_name = Path(label).name
    is_archive = archive_name.lower().endswith((".zip", ".rar"))

    def _push_bytes(raw: bytes, display_name: str) -> None:
        suffix = Path(display_name).suffix or ".pdf"
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            push_file_to_nexora_drive(
                user_id=user_id,
                workspace_id=workspace_id,
                local_path=tmp_path,
                subfolder="Sales Orders",
                display_name=display_name,
                replace_if_exists=True,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "SO Pack Drive backup failed for %s", display_name
            )
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    try:
        if mode == "pdfs":
            pdfs = [(Path(name).name, raw) for name, raw in payload]
        elif mode == "single":
            pdfs = _load_pack_pdfs(payload, label)
        else:
            pdfs = []
        for name, raw in pdfs:
            safe_name = Path(name).name
            if not safe_name.lower().endswith(".pdf"):
                continue
            _push_bytes(raw, safe_name)
        if is_archive:
            remove_file_from_nexora_drive(
                user_id=user_id,
                workspace_id=workspace_id,
                subfolder="Sales Orders",
                display_name=archive_name,
            )
    except Exception:
        logging.getLogger(__name__).exception("SO Pack Drive backup failed")


def _drop_local_after_drive_backup(
    local_path: str | Path | None, drive_file_id: str | None
) -> bool:
    """Delete the server-side copy once Drive holds it.

    Drive is the durable store; the server disk is small and is wiped on every
    redeploy anyway, so keeping a second copy there only fills it up.

    Deletes ONLY when Drive returned a file id AND that id was recorded — if
    Drive is not connected, or the upload failed, the local file is the only
    copy in existence and is left alone. Also refuses to touch anything
    outside the upload root, so a bad path can never delete something else.
    """
    if not drive_file_id or not local_path:
        return False
    try:
        target = Path(str(local_path)).resolve()
        root = (
            Path("app/instance/order_fulfillment_files")
            if Path("app/instance").exists()
            else Path("instance/order_fulfillment_files")
        ).resolve()
        if root not in target.parents:
            return False
        if not target.is_file():
            return False
        target.unlink()
        return True
    except OSError:
        return False


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
    merged_from_ci_only = False
    so_ci_rematch = None
    so_drive_file_id: str | None = None
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
                # Detect CI-first stub BEFORE linking SO, so we can rematch after.
                prior_ci_only = db.find_mergeable_ci_only_tracking(
                    order_ref_no, workspace_id=workspace_id
                )
                merged_from_ci_only = False
                if prior_ci_only is not None:
                    so_file_prior = str(prior_ci_only.get("sales_order_file_reference") or "").strip()
                    ci_file_prior = str(
                        prior_ci_only.get("commercial_invoice_file_reference") or ""
                    ).strip()
                    merged_from_ci_only = bool(ci_file_prior and not so_file_prior)

                tracking_id = db.link_sales_order_to_order_lifecycle(
                    order_ref_no=order_ref_no,
                    distributor_id=confirmed_distributor_id,
                    sales_order_file_reference=str(target_path),
                    sales_order_parsed=parsed_sales_order,
                    workspace_id=workspace_id,
                )
                if user_id and tracking_id and order_ref_no:
                    from app.services import order_desk_archive as oda

                    restore_conn = sqlite3.connect(_db_path())
                    try:
                        oda.restore_tracking_after_so_upload(
                            restore_conn,
                            user_id,
                            order_ref_no,
                            int(tracking_id),
                            workspace_id or "default",
                        )
                    finally:
                        restore_conn.close()
                # Backed up now, but the local file is still needed below for
                # line-item parsing — it is dropped at the very end of the
                # request instead (see so_drive_file_id).
                so_drive_file_id = _archive_order_pdf_to_drive(
                    db=db,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    tracking_id=tracking_id,
                    kind="so",
                    local_path=target_path,
                    display_name=f"{order_ref_no or 'SO'} {distributor_name_for_folder}.pdf",
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

                # CI was saved first → re-run SO vs CI discrepancy on every line
                # and refresh reconciliation so Order Desk / Android show Linked.
                if merged_from_ci_only:
                    so_ci_rematch = db.recheck_all_order_lifecycle_discrepancies(
                        tracking_id, workspace_id=workspace_id
                    )
                    if so_ci_rematch.get("has_discrepancy"):
                        has_any_discrepancy = True
                    try:
                        db.generate_distributor_reconciliation_excel(
                            tracking_id, workspace_id=workspace_id
                        )
                    except Exception:
                        pass

                db.mark_document_processed(workspace_id, "SO", order_ref_no, tracking_id)
            except Exception as exc:
                link_error = str(exc)

    # All parsing is done — Drive now holds this PDF, so free the server disk.
    # No-op unless Drive confirmed the upload and stored its file id.
    _drop_local_after_drive_backup(target_path, so_drive_file_id)

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
            "merged_from_ci_only": merged_from_ci_only,
            "so_ci_rematch": so_ci_rematch,
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


def _lifecycle_has_real_sales_order(tracking: dict | None) -> bool:
    """
    True only when a Sales Order PDF (or parsed SO payload) exists.

    A CI-only tracking row reuses order_ref_no from the invoice so a later
    SO can merge — that stub must NOT count as \"SO found / Linked\".
    """
    if not tracking:
        return False
    so_file = str(tracking.get("sales_order_file_reference") or "").strip()
    if so_file:
        return True
    parsed = tracking.get("sales_order_parsed")
    if isinstance(parsed, str) and parsed.strip():
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
    if not isinstance(parsed, dict) or not parsed:
        return False
    if parsed.get("header") or parsed.get("rows") or parsed.get("line_items"):
        return True
    return False


def _lookup_order_match_so(order_ref_no: str, user_id: int | None = None) -> dict | None:
    """SO present in FO↔SO Order Match index (SO Pack match), even without SO PDF."""
    from app.services import fo_so_match_db as matchdb

    ref = (order_ref_no or "").strip()
    if not ref:
        return None
    conn = sqlite3.connect(_db_path())
    try:
        matchdb.ensure_schema(conn)
        om = matchdb.lookup_so_in_order_match(conn, ref, user_id=user_id)
        if om is None and user_id is not None:
            om = matchdb.lookup_so_in_order_match(conn, ref, user_id=None)
        return om
    finally:
        conn.close()


def _bridge_order_match_so_into_lifecycle(
    db,
    *,
    order_ref_no: str,
    workspace_id: str,
    user_id: int | None,
    distributor_id: int | None = None,
) -> dict | None:
    """
    If SO exists only in Order Match (FO↔SO), seed lifecycle sales_order_parsed
    so CI can link without re-uploading the SO PDF.
    """
    from app.services import fo_so_match_db as matchdb

    om = _lookup_order_match_so(order_ref_no, user_id=user_id)
    if not om:
        return None
    parsed = matchdb.sales_order_parsed_from_order_match(om)
    dist_id = distributor_id or om.get("distributor_id")
    existing = db.get_order_lifecycle_by_order_ref_no(
        order_ref_no, workspace_id=workspace_id
    )
    if existing and existing.get("distributor_id"):
        dist_id = dist_id or existing.get("distributor_id")
    if not dist_id:
        return om
    try:
        tracking_id = db.link_sales_order_to_order_lifecycle(
            order_ref_no=order_ref_no,
            distributor_id=int(dist_id),
            sales_order_file_reference=None,
            sales_order_parsed=parsed,
            workspace_id=workspace_id or "default",
        )
        # Populate so_qty on fulfillment items for SO↔CI compare.
        for row in parsed.get("line_items") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("item_name") or row.get("product") or "").strip()
            qty = float(row.get("qty") or row.get("quantity") or 0)
            val = float(row.get("net_amount") or row.get("value") or row.get("amount") or 0)
            if not name or qty <= 0:
                continue
            try:
                db.upsert_order_lifecycle_item(
                    tracking_id,
                    item_name=name,
                    qty=qty,
                    value=val,
                    source="so",
                    workspace_id=workspace_id or "default",
                )
            except Exception:
                pass
        om = dict(om)
        om["lifecycle_tracking_id"] = tracking_id
        return om
    except Exception:
        return om


@data_blueprint.route("/api/v1/order-fulfillment/upload/invoice", methods=["POST"])
@require_jwt_auth
def upload_invoice_v2() -> Response:
    """
    Commercial Invoice (CI) PDF upload. Extracts Sales Order Number /
    Invoice No / amount. NEVER auto-links or auto-saves sale data —
    frontend must confirm via:
      - /confirm-ci-link   when SO already exists in Nexora
      - /confirm-ci-only   when SO is missing (CI-first historical sale)
    """
    try:
        return _upload_invoice_v2_impl()
    except Exception as exc:
        return _json_response(
            {
                "success": False,
                "error": {
                    "message": f"CI upload failed: {exc}",
                    "code": "ci_upload_failed",
                },
            },
            500,
        )


def _upload_invoice_v2_impl(uploaded_file=None) -> Response:
    """
    Fast CI upload preview for Render (avoid 502 from pdfplumber OOM/timeout).

    Upload only does: save file + text extract + header/GST + Customers match.
    Full line-item CI detail is rebuilt on confirm-ci-link / confirm-ci-only.
    """
    uploaded_file = uploaded_file or request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return _json_response({"success": False, "error": {"message": "file is required"}}, 400)

    raw = uploaded_file.read()
    if not raw:
        return _json_response({"success": False, "error": {"message": "Empty file"}}, 400)
    kind = _so_pack_sniff_kind(raw, uploaded_file.filename)
    from werkzeug.datastructures import FileStorage

    if kind in ("zip", "rar"):
        try:
            pdfs = _expand_ci_upload_items([(uploaded_file.filename or f"ci.{kind}", raw)])
        except ValueError as exc:
            return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
        if len(pdfs) != 1:
            return _json_response({"success": True, "data": _ingest_ci_pdfs(pdfs)})
        raw = pdfs[0][1]
        uploaded_file = FileStorage(
            stream=io.BytesIO(raw),
            filename=pdfs[0][0],
            content_type="application/pdf",
        )
    else:
        uploaded_file = FileStorage(
            stream=io.BytesIO(raw),
            filename=_so_pack_safe_filename(uploaded_file.filename, kind or "pdf"),
            content_type="application/pdf",
        )

    target_path = _save_order_fulfillment_upload_organized(uploaded_file, "CI", "CI Received")
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    try:
        extracted_text = _extract_pdf_text(target_path)
    except Exception:
        extracted_text = ""

    header = _enrich_ci_header_from_text(
        extracted_text, _parse_sales_order_header_fields(extracted_text)
    )
    order_ref_no = (header.get("order_ref_no") or "").strip() or None
    invoice_no = (header.get("invoice_no") or "").strip() or None
    buyer_name = (header.get("buyer_name") or "").strip() or None

    extracted_amount = None
    if header.get("invoice_total") is not None:
        try:
            extracted_amount = float(header["invoice_total"])
        except (TypeError, ValueError):
            extracted_amount = None
    if extracted_amount is None and header.get("taxable_amount") is not None:
        try:
            taxable = float(header["taxable_amount"])
            igst = float(header.get("total_igst") or 0)
            extracted_amount = taxable + igst if taxable else None
        except (TypeError, ValueError):
            extracted_amount = None

    if invoice_no and db.is_document_already_processed(workspace_id, "CI", invoice_no):
        return _json_response({
            "success": True,
            "data": {
                "is_duplicate": True,
                "invoice_no": invoice_no,
                "order_ref_no": order_ref_no,
                "message": (
                    f"This Commercial Invoice (Invoice No \"{invoice_no}\") has already "
                    f"been processed — rejecting to avoid double-counting."
                ),
            },
        })

    matching_so = None
    so_distributor = None
    distributor_name = None
    existing_ci_only_tracking = None
    order_match_so = None
    if order_ref_no:
        existing_tracking = db.get_order_lifecycle_by_order_ref_no(
            order_ref_no, workspace_id=workspace_id
        )
        if existing_tracking and _lifecycle_has_real_sales_order(existing_tracking):
            matching_so = existing_tracking
            if matching_so.get("distributor_id"):
                so_distributor = db.get_master_distributor(
                    matching_so["distributor_id"], workspace_id=workspace_id
                )
                if so_distributor:
                    distributor_name = (
                        so_distributor.get("firm_name") or so_distributor.get("name")
                    )
        elif existing_tracking:
            # Same order_ref already has CI-only (no SO PDF) — not a linkable SO.
            existing_ci_only_tracking = existing_tracking
            if existing_tracking.get("distributor_id"):
                so_distributor = db.get_master_distributor(
                    existing_tracking["distributor_id"], workspace_id=workspace_id
                )
                if so_distributor:
                    distributor_name = (
                        so_distributor.get("firm_name") or so_distributor.get("name")
                    )

        # SO Pack → Order Match often has the SO without a lifecycle PDF.
        # Bridge that SO into lifecycle so CI can confirm-link.
        if matching_so is None:
            uid = _current_user_id()
            dist_hint = None
            if existing_tracking and existing_tracking.get("distributor_id"):
                dist_hint = existing_tracking.get("distributor_id")
            order_match_so = _bridge_order_match_so_into_lifecycle(
                db,
                order_ref_no=order_ref_no,
                workspace_id=workspace_id or "default",
                user_id=uid,
                distributor_id=int(dist_hint) if dist_hint else None,
            )
            if order_match_so:
                matching_so = db.get_order_lifecycle_by_order_ref_no(
                    order_ref_no, workspace_id=workspace_id
                )
                if matching_so and _lifecycle_has_real_sales_order(matching_so):
                    existing_ci_only_tracking = None
                    if matching_so.get("distributor_id") and not so_distributor:
                        so_distributor = db.get_master_distributor(
                            matching_so["distributor_id"], workspace_id=workspace_id
                        )
                        if so_distributor:
                            distributor_name = (
                                so_distributor.get("firm_name")
                                or so_distributor.get("name")
                            )
                else:
                    matching_so = None

    own_company_gst = None
    try:
        own_company_profile = db.get_company_profile(workspace_id)
        own_company_gst = (
            own_company_profile.get("gst_number") if own_company_profile else None
        )
    except Exception:
        own_company_gst = None

    buyer_gst = _extract_ci_buyer_gst(extracted_text or "", own_company_gst)
    if not buyer_gst:
        header_gsts = header.get("all_gst_numbers") or ""
        if header_gsts:
            buyer_gst = _identify_buyer_gst(
                [g for g in str(header_gsts).split(",") if g],
                own_company_gst,
            )
    if buyer_gst:
        header["buyer_gst"] = buyer_gst

    # Light stub — confirm endpoints rebuild full CI detail from the PDF.
    parsed_invoice = {
        "source": "commercial_invoice",
        "detail_level": "upload_preview",
        "header": header,
        "line_items": [],
        "totals": {
            "invoice_total": extracted_amount,
            "taxable_amount": header.get("taxable_amount"),
            "total_igst": header.get("total_igst"),
            "line_count": 0,
        },
    }

    try:
        ci_customer_match = _match_ci_buyer_to_customers(
            db,
            buyer_name=buyer_name,
            buyer_gst=buyer_gst,
            workspace_id=workspace_id,
            allow_fuzzy=not bool(buyer_gst),  # GST hit is enough; skip slow fuzzy scan
        )
        party_match = _build_ci_party_match_summary(
            ci_match=ci_customer_match,
            so_distributor=so_distributor,
        )
    except Exception as exc:
        ci_customer_match = {
            "status": "none",
            "match_method": None,
            "buyer_name": buyer_name,
            "buyer_gst": buyer_gst,
            "distributor": None,
            "candidates": [],
            "error": str(exc),
        }
        party_match = {
            "status": "unmatched",
            "message": f"Customers match failed ({exc}). Select distributor manually.",
            "ci_distributor": None,
            "so_distributor": _distributor_public_payload(so_distributor),
        }
    suggested_distributor = ci_customer_match.get("distributor")

    compare = None
    if matching_so:
        so_items = db.list_order_lifecycle_items_for_tracking(
            matching_so["tracking_id"], workspace_id=workspace_id
        )
        so_total_qty = sum(
            float(it.get("so_qty") or it.get("fulfilled_qty") or 0) for it in so_items
        )
        so_total_value = sum(float(it.get("so_value") or 0) for it in so_items)
        compare = {
            "order_ref_no": order_ref_no,
            "invoice_no": invoice_no,
            "so_distributor": distributor_name,
            "so_distributor_id": (so_distributor or {}).get("id") if so_distributor else None,
            "ci_buyer_name": buyer_name,
            "ci_buyer_gst": buyer_gst,
            "customers_match": party_match,
            "so_has_file": _lifecycle_has_real_sales_order(matching_so),
            "so_from_order_match": bool(order_match_so),
            "so_tracking_id": matching_so.get("tracking_id"),
            "so_item_count": len(so_items),
            "ci_line_count": None,
            "so_total_qty": so_total_qty,
            "ci_total_qty": None,
            "so_total_value": so_total_value,
            "ci_total_value": extracted_amount,
            "ci_amount": extracted_amount,
            "qty_mismatch": None,
            "party_mismatch": party_match.get("status") == "mismatch",
            "detail_note": "Full CI line qty/value compare runs when you confirm (keeps upload fast).",
        }

    matching_so_summary = None
    if matching_so:
        matching_so_summary = {
            "tracking_id": matching_so.get("tracking_id"),
            "order_ref_no": matching_so.get("order_ref_no"),
            "distributor_id": matching_so.get("distributor_id"),
            "sales_order_file_reference": matching_so.get("sales_order_file_reference"),
            "has_sales_order": _lifecycle_has_real_sales_order(matching_so),
            "from_order_match": bool(order_match_so),
            "commercial_invoice_file_reference": matching_so.get(
                "commercial_invoice_file_reference"
            ),
        }

    return _json_response({
        "success": True,
        "data": {
            "order_ref_no": order_ref_no,
            "invoice_no": invoice_no,
            "buyer_name": buyer_name,
            "buyer_gst": buyer_gst,
            "commercial_invoice_file_reference": str(target_path),
            "commercial_invoice_parsed": parsed_invoice,
            "matching_sales_order": matching_so_summary,
            "order_match_so": (
                {
                    "so_number": order_match_so.get("so_number"),
                    "run_id": order_match_so.get("run_id"),
                    "so_qty": order_match_so.get("so_qty"),
                    "so_net": order_match_so.get("so_net"),
                    "source": "order_match",
                }
                if order_match_so
                else None
            ),
            "distributor_name": distributor_name,
            "suggested_distributor": suggested_distributor,
            "ci_customer_match": ci_customer_match,
            "party_match": party_match,
            "extracted_amount": extracted_amount,
            "ci_line_count": 0,
            "ci_total_qty": None,
            "ci_header": header,
            "ci_totals": parsed_invoice.get("totals"),
            "detail_level": "upload_preview",
            "compare": compare,
            "requires_confirmation": matching_so is not None,
            "requires_ci_only_confirmation": matching_so is None,
            "no_match_found": matching_so is None,
            "existing_ci_only_tracking_id": (
                (existing_ci_only_tracking or {}).get("tracking_id")
                if matching_so is None else None
            ),
        },
    })


def _apply_ci_line_items_and_achievement(
    db: CentralizedDB,
    *,
    tracking_id: int,
    commercial_invoice_file_reference: str | None,
    commercial_invoice_parsed: dict,
    invoice_no: str | None,
    amount: float | None,
    notes: str | None,
    workspace_id: str,
    user_id: int | None = None,
) -> tuple[list, bool, int | None, str | None, dict]:
    """Shared post-save: full CI lines → AM match → reconciliation + achievement.

    Returns (item_results, has_discrepancy, achievement_id, achievement_error, article_master_match).
    """
    import article_master_db as amdb
    from ci_article_match import annotate_ci_line_items_with_article_master

    item_results = []
    has_any_discrepancy = False
    article_master_match: dict[str, Any] = {
        "total": 0,
        "matched": 0,
        "unmatched": 0,
        "no_key": 0,
        "catalog_size": 0,
        "unmatched_lines": [],
    }

    # Prefer the full structured line_items saved with the CI (SO-parity).
    parsed_line_items, _ = _refresh_saved_ci_lines(
        list((commercial_invoice_parsed or {}).get("line_items") or [])
    )
    if (
        not parsed_line_items
        and commercial_invoice_file_reference
        and _ci_table_parse_enabled()
    ):
        try:
            parsed_line_items = parse_bombay_dyeing_so_ci_line_items(
                commercial_invoice_file_reference, "CI"
            )
        except Exception:
            parsed_line_items = []
    if not parsed_line_items:
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
                "rate": rate,
            })

    # Article Master match (same catalog FO uses)
    if user_id is not None and parsed_line_items:
        try:
            with sqlite3.connect(db.db_path) as am_conn:
                amdb.ensure_schema(am_conn)
                parsed_line_items, article_master_match = annotate_ci_line_items_with_article_master(
                    am_conn, amdb, int(user_id), parsed_line_items,
                )
        except Exception as exc:
            article_master_match = {
                **article_master_match,
                "error": str(exc),
            }

    # Persist annotated lines back onto the tracking CI payload
    try:
        if isinstance(commercial_invoice_parsed, dict):
            commercial_invoice_parsed = dict(commercial_invoice_parsed)
            commercial_invoice_parsed["line_items"] = parsed_line_items
            commercial_invoice_parsed["article_master_match"] = article_master_match
            with sqlite3.connect(db.db_path) as conn:
                conn.execute(
                    "UPDATE order_lifecycle_tracking SET commercial_invoice_parsed = ? WHERE tracking_id = ?",
                    (json.dumps(commercial_invoice_parsed, default=str), tracking_id),
                )
                conn.commit()
    except Exception:
        pass

    for line_item in parsed_line_items:
        norm_key = size_code_only_item_key(line_item.get("item_key"))
        item_result = db.upsert_order_lifecycle_item(
            tracking_id=tracking_id,
            item_name=line_item["item_name"],
            source="ci",
            qty=line_item["qty"],
            value=line_item["value"],
            workspace_id=workspace_id,
            item_key=norm_key,
        )
        # Attach AM match onto reconciliation row payload (not a DB column yet)
        am = line_item.get("article_match")
        if isinstance(am, dict):
            item_result = dict(item_result or {})
            item_result["article_match"] = am
            item_result["article_id"] = am.get("article_id")
        item_results.append(item_result)
        if item_result.get("has_discrepancy"):
            has_any_discrepancy = True
    if item_results:
        db.generate_distributor_reconciliation_excel(tracking_id, workspace_id=workspace_id)
    if invoice_no:
        db.mark_document_processed(workspace_id, "CI", invoice_no, tracking_id)

    # Persist invoice date on tracking (SO also stores generated dates)
    inv_date = None
    header = (commercial_invoice_parsed or {}).get("header") or {}
    if isinstance(header, dict):
        inv_date = _normalize_doc_date(str(header.get("invoice_date") or "")) or None
    if inv_date:
        try:
            with sqlite3.connect(db.db_path) as conn:
                conn.execute(
                    "UPDATE order_lifecycle_tracking SET commercial_invoice_date = ? WHERE tracking_id = ?",
                    (inv_date, tracking_id),
                )
                conn.commit()
        except Exception:
            pass

    achievement_id = None
    achievement_error = None
    if amount is None:
        totals = (commercial_invoice_parsed or {}).get("totals") or {}
        for key in ("invoice_total", "line_total", "taxable_amount"):
            if totals.get(key) is not None:
                try:
                    amount = float(totals[key])
                    break
                except (TypeError, ValueError):
                    continue
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
            achievement_error = str(exc)
    return item_results, has_any_discrepancy, achievement_id, achievement_error, article_master_match


def _current_user_id() -> int | None:
    user = getattr(request, "user", None)
    return (
        int(user["user_id"])
        if isinstance(user, dict) and user.get("user_id") is not None
        else None
    )


def _request_username() -> str | None:
    user = getattr(request, "user", None)
    if not isinstance(user, dict):
        return None
    raw = user.get("username") or user.get("email")
    return str(raw).strip() if raw else None


@data_blueprint.route("/api/v1/statement-of-account/from-ledger", methods=["POST"])
@require_jwt_auth
def statement_of_account_from_ledger() -> Response:
    """BD drawer: upload SAP party GL (.xls/.xlsx) → Statement of Account.

    Multipart field: file
    Query: format=json (preview with full lines) | format=xlsx (Excel download).
    Default: xlsx (backward compatible).
    """
    from app.services.statement_of_account import (
        parse_statement_of_account,
        statement_as_api_data,
        statement_to_xlsx_bytes,
    )

    uploaded = request.files.get("file")
    if uploaded is None or not (uploaded.filename or "").strip():
        return _json_response(
            {"success": False, "error": {"message": "Upload a ledger .xls / .xlsx file"}},
            400,
        )
    filename = uploaded.filename or "ledger.xls"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xls", ".xlsx", ".xlsm"}:
        return _json_response(
            {
                "success": False,
                "error": {"message": "Only Excel ledger files (.xls / .xlsx) are supported"},
            },
            400,
        )
    try:
        raw = uploaded.read()
        if not raw:
            return _json_response(
                {"success": False, "error": {"message": "File is empty"}},
                400,
            )
        statement = parse_statement_of_account(raw, filename)
        data = statement_as_api_data(statement)
    except ValueError as exc:
        return _json_response(
            {"success": False, "error": {"message": str(exc)}},
            400,
        )
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"Could not build statement: {exc}"}},
            500,
        )

    fmt = (request.args.get("format") or "xlsx").strip().lower()
    if fmt in {"json", "preview"}:
        return _json_response({"success": True, "data": data})

    xlsx_bytes = statement_to_xlsx_bytes(statement)
    download_name = data.get("filename") or "Statement_of_Account.xlsx"
    resp = send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=download_name,
    )
    resp.headers["X-SOA-Party"] = str(data.get("party_name") or "")
    resp.headers["X-SOA-Closing"] = str(data.get("closing_balance") or "")
    resp.headers["X-SOA-Lines"] = str(data.get("line_count") or 0)
    return resp


@data_blueprint.route("/api/v1/distributor-payments/status", methods=["GET"])
@require_jwt_auth
def get_distributor_payment_status() -> Response:
    """Drawer-only "Distributors Payment Status" tree: distributor -> season
    -> category, each carrying its SO bill total (incl. GST from matched SO
    Pack runs) and whatever deposits have been recorded against it."""
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    distributors = db.list_distributor_category_payment_status(user_id)
    backup_category_payment_status_to_drive(
        db=db,
        user_id=user_id,
        workspace_id=get_workspace_id(),
        username=_request_username(),
        allow_read_cooldown=True,
    )
    return _json_response({"success": True, "data": {"distributors": distributors}})


@data_blueprint.route("/api/v1/distributor-payments/deposits", methods=["POST"])
@require_jwt_auth
def add_distributor_payment_deposit() -> Response:
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    if not user_id:
        return _json_response({"success": False, "error": {"message": "Not signed in"}}, 401)
    payload = request.get_json(silent=True) or {}
    try:
        distributor_id = int(payload.get("distributor_id"))
        season = str(payload.get("season") or "").strip()
        category = str(payload.get("category") or "").strip()
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        return _json_response(
            {"success": False, "error": {"message": "distributor_id, season, category, amount are required"}},
            400,
        )
    if not season or not category or amount <= 0:
        return _json_response(
            {"success": False, "error": {"message": "season, category are required and amount must be > 0"}},
            400,
        )
    payment_date = str(payload.get("payment_date") or "").strip()
    if not payment_date:
        return _json_response(
            {"success": False, "error": {"message": "payment_date is required"}}, 400
        )
    note = payload.get("note")
    entry = db.add_distributor_category_payment(
        user_id, distributor_id, season, category, amount, payment_date,
        note=str(note).strip() if note else None,
    )
    backup_category_payment_status_to_drive(
        db=db,
        user_id=user_id,
        workspace_id=get_workspace_id(),
        username=_request_username(),
    )
    return _json_response({"success": True, "data": {"entry": entry}})


@data_blueprint.route("/api/v1/distributor-payments/cd", methods=["POST"])
@require_jwt_auth
def set_distributor_cd_rate() -> Response:
    """Set Cash Discount % for a distributor+season."""
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    if not user_id:
        return _json_response({"success": False, "error": {"message": "Not signed in"}}, 401)
    payload = request.get_json(silent=True) or {}
    try:
        distributor_id = int(payload["distributor_id"])
        season = str(payload["season"]).strip()
        cd_percent = float(payload["cd_percent"])
    except (TypeError, ValueError, KeyError):
        return _json_response(
            {"success": False, "error": {"message": "distributor_id, season, cd_percent required"}}, 400
        )
    if not season:
        return _json_response({"success": False, "error": {"message": "season is required"}}, 400)
    entry = db.set_distributor_cd_rate(user_id, distributor_id, season, cd_percent)
    return _json_response({"success": True, "data": entry})


@data_blueprint.route(
    "/api/v1/distributor-payments/deposits/<int:deposit_id>", methods=["DELETE"]
)
@require_jwt_auth
def delete_distributor_payment_deposit(deposit_id: int) -> Response:
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    if not user_id:
        return _json_response({"success": False, "error": {"message": "Not signed in"}}, 401)
    ok = db.delete_distributor_category_payment(user_id, deposit_id)
    if not ok:
        return _json_response(
            {"success": False, "error": {"message": "Deposit not found"}}, 404
        )
    backup_category_payment_status_to_drive(
        db=db,
        user_id=user_id,
        workspace_id=get_workspace_id(),
        username=_request_username(),
    )
    return _json_response({"success": True})


@data_blueprint.route("/api/v1/distributor-secondary-sales/status", methods=["GET"])
@require_jwt_auth
def get_distributor_secondary_sales_status() -> Response:
    """Distributor Zone → Secondary Sale: per-distributor monthly entries
    grouped by Indian FY (Apr–Mar)."""
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    distributors = db.list_distributor_secondary_sales(user_id)
    return _json_response({"success": True, "data": {"distributors": distributors}})


@data_blueprint.route("/api/v1/distributor-secondary-sales/entries", methods=["POST"])
@require_jwt_auth
def upsert_distributor_secondary_sale() -> Response:
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    if not user_id:
        return _json_response({"success": False, "error": {"message": "Not signed in"}}, 401)
    payload = request.get_json(silent=True) or {}
    try:
        distributor_id = int(payload.get("distributor_id"))
        year = int(payload.get("year"))
        month = int(payload.get("month"))
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        return _json_response(
            {
                "success": False,
                "error": {"message": "distributor_id, year, month, amount are required"},
            },
            400,
        )
    note = payload.get("note")
    try:
        entry = db.upsert_distributor_secondary_sale(
            user_id,
            distributor_id,
            year,
            month,
            amount,
            note=str(note).strip() if note else None,
        )
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    return _json_response({"success": True, "data": {"entry": entry}})


@data_blueprint.route(
    "/api/v1/distributor-secondary-sales/entries/<int:entry_id>", methods=["DELETE"]
)
@require_jwt_auth
def delete_distributor_secondary_sale(entry_id: int) -> Response:
    db = CentralizedDB(_db_path())
    user_id = _current_user_id()
    if not user_id:
        return _json_response({"success": False, "error": {"message": "Not signed in"}}, 401)
    ok = db.delete_distributor_secondary_sale(user_id, entry_id)
    if not ok:
        return _json_response(
            {"success": False, "error": {"message": "Entry not found"}}, 404
        )
    return _json_response({"success": True})


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
    user_id = _current_user_id()
    order_sheets = db.list_order_sheets(
        workspace_id=workspace_id, user_id=user_id
    )
    tracking_records = db.list_order_lifecycle_tracking(
        workspace_id=workspace_id, limit=500, user_id=user_id
    )
    # CI list: mark SO matched when order_ref exists in FO↔SO Order Match
    # even if Sales Order PDF was never uploaded to lifecycle.
    for rec in tracking_records:
        if rec.get("has_sales_order"):
            continue
        ref = str(rec.get("order_ref_no") or "").strip()
        if not ref:
            continue
        try:
            om = _lookup_order_match_so(ref, user_id=user_id)
        except Exception:
            om = None
        if om:
            rec["has_order_match_so"] = True
            # List UI treats this as linked (same as detail sheet).
            rec["has_sales_order"] = True
    return _json_response({
        "success": True,
        "data": {
            "order_sheets": order_sheets,
            "tracking_records": tracking_records,
            "ci_count": sum(1 for t in tracking_records if t.get("has_commercial_invoice")),
        },
    })


@data_blueprint.route("/api/v1/order-fulfillment/tracking/<int:tracking_id>", methods=["GET"])
@require_jwt_auth
def get_order_fulfillment_tracking(tracking_id: int) -> Response:
    """Single SO/CI lifecycle record — mobile global-search detail."""
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    tracking = db.get_order_lifecycle_tracking(
        tracking_id, workspace_id=workspace_id, user_id=_current_user_id()
    )
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

    # Invoice number from THIS tracking's PDF payload — never from
    # processed_documents. That table can hold two CI numbers on the
    # same tracking_id after an overwrite (9337 stamp + 9346 lines).
    invoice_no = db._extract_ci_invoice_no(tracking.get("commercial_invoice_parsed"))
    if not invoice_no:
        try:
            with sqlite3.connect(_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                ci_row = conn.execute(
                    "SELECT document_number FROM processed_documents "
                    "WHERE tracking_id = ? AND document_type = 'CI' "
                    "ORDER BY processed_at DESC LIMIT 1",
                    (tracking_id,),
                ).fetchone()
                if ci_row:
                    invoice_no = ci_row["document_number"]
        except Exception:
            pass

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
        "has_sales_order": _lifecycle_has_real_sales_order(tracking) or bool(
            tracking.get("sales_order_drive_file_id")
        ),
        "has_commercial_invoice": bool(
            tracking.get("commercial_invoice_file_reference")
            or tracking.get("commercial_invoice_drive_file_id")
        ),
        "sales_order_drive_file_id": tracking.get("sales_order_drive_file_id"),
        "commercial_invoice_drive_file_id": tracking.get("commercial_invoice_drive_file_id"),
        "order_sheet_name": tracking.get("order_sheet_name"),
        "created_at": tracking.get("created_at"),
        "items": items,
        "item_count": len(items),
    }

    # Bridge: SO exists in Order Match (FO↔SO) but no lifecycle SO PDF yet.
    # Seed sales_order_parsed so CI shows SO MATCHED / PARTIAL instead of UNMATCHED.
    order_ref = str(tracking.get("order_ref_no") or "").strip()
    if (
        order_ref
        and payload.get("has_commercial_invoice")
        and not _lifecycle_has_real_sales_order(tracking)
    ):
        om = _bridge_order_match_so_into_lifecycle(
            db,
            order_ref_no=order_ref,
            workspace_id=workspace_id or "default",
            user_id=_current_user_id(),
            distributor_id=distributor_id,
        )
        if om:
            payload["order_match_so"] = {
                "so_number": om.get("so_number"),
                "run_id": om.get("run_id"),
                "so_qty": om.get("so_qty"),
                "so_net": om.get("so_net"),
                "source": "order_match",
            }
            payload["has_order_match_so"] = True
            # Refresh tracking flags after bridge
            tracking = db.get_order_lifecycle_tracking(
                tracking_id, workspace_id=workspace_id, user_id=_current_user_id()
            ) or tracking
            payload["has_sales_order"] = _lifecycle_has_real_sales_order(tracking)
            # Expose SO lines for mobile qty compare even if item rematch is thin
            so_bridge_lines = []
            for l in om.get("line_detail") or []:
                if not isinstance(l, dict):
                    continue
                try:
                    q = float(l.get("qty") or l.get("quantity") or 0)
                except (TypeError, ValueError):
                    q = 0.0
                if q <= 0:
                    continue
                so_bridge_lines.append(
                    {
                        "item_name": l.get("product_name")
                        or l.get("product_detail")
                        or l.get("material_code"),
                        "material_code": l.get("material_code"),
                        "qty": q,
                        "value": l.get("net_amount") or l.get("net"),
                    }
                )
            if so_bridge_lines:
                payload["so_line_items"] = so_bridge_lines
                payload["so_line_count"] = len(so_bridge_lines)
            # Reload items after SO upsert
            try:
                raw_items = db.list_order_lifecycle_items_for_tracking(
                    tracking_id, workspace_id=workspace_id or "default"
                )
                items = []
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
                payload["items"] = items
                payload["item_count"] = len(items)
            except Exception:
                pass
    elif order_ref and not payload.get("has_sales_order"):
        om = _lookup_order_match_so(order_ref, user_id=_current_user_id())
        if om:
            payload["order_match_so"] = {
                "so_number": om.get("so_number"),
                "run_id": om.get("run_id"),
                "so_qty": om.get("so_qty"),
                "so_net": om.get("so_net"),
                "source": "order_match",
            }
            payload["has_order_match_so"] = True

    # Attach CI header/totals summary when a full CI detail was saved
    ci_parsed = tracking.get("commercial_invoice_parsed")
    if isinstance(ci_parsed, dict):
        if isinstance(ci_parsed.get("header"), dict):
            payload["ci_header"] = {
                k: ci_parsed["header"].get(k)
                for k in (
                    "invoice_no",
                    "invoice_date",
                    "order_ref_no",
                    "sales_order_date",
                    "buyer_name",
                    "buyer_gst",
                    "consignee_name",
                    "cust_po",
                    "place_of_supply",
                    "payment_due",
                    "delivery_no",
                    "invoice_total",
                    "taxable_amount",
                    "total_igst",
                    "total_pieces",
                    "transporter",
                    "lr_no",
                )
                if ci_parsed["header"].get(k) not in (None, "")
            }
        if isinstance(ci_parsed.get("totals"), dict):
            payload["ci_totals"] = ci_parsed.get("totals")
        payload["ci_detail_level"] = ci_parsed.get("detail_level")
        ci_lines = list(ci_parsed.get("line_items") or [])
        ci_lines, keys_changed = _refresh_saved_ci_lines(ci_lines)
        if keys_changed:
            updated = dict(ci_parsed)
            updated["line_items"] = ci_lines
            ci_parsed = updated
            try:
                with sqlite3.connect(db.db_path) as conn:
                    conn.execute(
                        "UPDATE order_lifecycle_tracking "
                        "SET commercial_invoice_parsed = ? WHERE tracking_id = ?",
                        (json.dumps(updated, default=str), tracking_id),
                    )
                    conn.commit()
            except Exception:
                pass
        # Re-parse from CI PDF when any saved line is missing Design+Colour
        # (page-break truncation → 17/18 colourways). Uses real PDF text only.
        ci_file = tracking.get("commercial_invoice_file_reference")
        needs_design_repair = bool(ci_file) and bool(ci_lines) and any(
            isinstance(ln, dict)
            and ln.get("item_name")
            and not _ci_design_colour_tokens(str(ln.get("item_name") or ""))
            for ln in ci_lines
        )
        needs_qty_repair = bool(ci_file) and _ci_lines_disagree_with_header(
            payload.get("ci_header") or ci_parsed.get("header"),
            ci_lines,
        )
        source_text = str(ci_parsed.get("text") or "")
        if not source_text and ci_file:
            try:
                source_text = _extract_pdf_text(ci_file) or ""
            except Exception:
                source_text = ""
        needs_brand_repair = bool(ci_file) and _ci_lines_contradict_pdf_text(
            ci_lines, source_text
        )
        if needs_design_repair or needs_qty_repair or needs_brand_repair:
            try:
                fresh = None
                rebuilt_detail = None
                if needs_qty_repair or needs_brand_repair:
                    rebuilt_detail = build_commercial_invoice_detail(ci_file)
                    if isinstance(rebuilt_detail, dict) and not rebuilt_detail.get("error"):
                        fresh = rebuilt_detail.get("line_items")
                if not fresh:
                    fresh = parse_bombay_dyeing_so_ci_line_items(ci_file, "CI")
                existing_n = len([ln for ln in ci_lines if isinstance(ln, dict)])
                # Brand repair must replace a longer wrong payload (Flora 18)
                # with this invoice's real lines (Cotton Comfort 3).
                if fresh and (needs_brand_repair or len(fresh) >= existing_n):
                    # Keep AM annotations when item_name still matches.
                    by_name = {
                        str(ln.get("item_name") or "").strip().upper(): ln
                        for ln in ci_lines
                        if isinstance(ln, dict)
                    }
                    merged: list[dict[str, Any]] = []
                    for ln in fresh:
                        row = dict(ln)
                        old = by_name.get(str(row.get("item_name") or "").strip().upper())
                        if (
                            not needs_brand_repair
                            and isinstance(old, dict)
                            and isinstance(old.get("article_match"), dict)
                            and _ci_am_brand_agrees_with_line(old)
                        ):
                            row["article_match"] = old["article_match"]
                            if old.get("article_id") is not None:
                                row["article_id"] = old.get("article_id")
                        merged.append(row)
                    ci_lines, _ = _refresh_saved_ci_lines(merged)
                    updated = dict(ci_parsed)
                    updated["line_items"] = ci_lines
                    if isinstance(rebuilt_detail, dict) and not rebuilt_detail.get("error"):
                        if isinstance(rebuilt_detail.get("header"), dict):
                            updated["header"] = rebuilt_detail["header"]
                            payload["ci_header"] = {
                                k: rebuilt_detail["header"].get(k)
                                for k in (
                                    "invoice_no",
                                    "invoice_date",
                                    "order_ref_no",
                                    "sales_order_date",
                                    "buyer_name",
                                    "buyer_gst",
                                    "consignee_name",
                                    "cust_po",
                                    "place_of_supply",
                                    "payment_due",
                                    "delivery_no",
                                    "invoice_total",
                                    "taxable_amount",
                                    "total_igst",
                                    "total_pieces",
                                    "transporter",
                                    "lr_no",
                                )
                                if rebuilt_detail["header"].get(k) not in (None, "")
                            }
                        if isinstance(rebuilt_detail.get("totals"), dict):
                            updated["totals"] = rebuilt_detail["totals"]
                            payload["ci_totals"] = rebuilt_detail["totals"]
                    with sqlite3.connect(db.db_path) as conn:
                        conn.execute(
                            "UPDATE order_lifecycle_tracking "
                            "SET commercial_invoice_parsed = ? WHERE tracking_id = ?",
                            (json.dumps(updated, default=str), tracking_id),
                        )
                        conn.commit()
                    ci_parsed = updated
                else:
                    repaired = _repair_truncated_ci_design_colours(
                        ci_file,
                        [dict(ln) for ln in ci_lines if isinstance(ln, dict)],
                    )
                    if repaired:
                        ci_lines = repaired
                        updated = dict(ci_parsed)
                        updated["line_items"] = ci_lines
                        with sqlite3.connect(db.db_path) as conn:
                            conn.execute(
                                "UPDATE order_lifecycle_tracking "
                                "SET commercial_invoice_parsed = ? WHERE tracking_id = ?",
                                (json.dumps(updated, default=str), tracking_id),
                            )
                            conn.commit()
                        ci_parsed = updated
            except Exception:
                pass
        article_master_match = ci_parsed.get("article_master_match")
        needs_am = bool(ci_lines) and (
            keys_changed
            or needs_brand_repair
            or not isinstance(article_master_match, dict)
            or any(not isinstance(ln.get("article_match"), dict) for ln in ci_lines if isinstance(ln, dict))
        )
        if needs_am:
            user = getattr(request, "user", None)
            user_id = (
                int(user["user_id"])
                if isinstance(user, dict) and user.get("user_id") is not None
                else None
            )
            if user_id is not None:
                try:
                    import article_master_db as amdb
                    from ci_article_match import annotate_ci_line_items_with_article_master

                    with sqlite3.connect(db.db_path) as am_conn:
                        amdb.ensure_schema(am_conn)
                        ci_lines, article_master_match = annotate_ci_line_items_with_article_master(
                            am_conn, amdb, user_id, ci_lines,
                        )
                    # Persist so next open is fast
                    try:
                        updated = dict(ci_parsed)
                        updated["line_items"] = ci_lines
                        updated["article_master_match"] = article_master_match
                        with sqlite3.connect(db.db_path) as conn:
                            conn.execute(
                                "UPDATE order_lifecycle_tracking "
                                "SET commercial_invoice_parsed = ? WHERE tracking_id = ?",
                                (json.dumps(updated, default=str), tracking_id),
                            )
                            conn.commit()
                    except Exception:
                        pass
                except Exception as exc:
                    article_master_match = {
                        "total": len(ci_lines),
                        "matched": 0,
                        "unmatched": 0,
                        "no_key": 0,
                        "error": str(exc),
                        "unmatched_lines": [],
                    }
        payload["ci_line_items"] = ci_lines
        payload["ci_line_count"] = len(ci_lines)
        if isinstance(article_master_match, dict):
            payload["article_master_match"] = article_master_match
        if ci_parsed.get("parse_note"):
            payload["ci_parse_note"] = ci_parsed.get("parse_note")
    # SO PDF lines (design/colour per SKU) — from saved reconciliation items.
    so_lines: list[dict[str, Any]] = []
    for it in items:
        name = (it.get("item_name") or "").strip()
        qty = it.get("so_qty")
        if not name or qty is None or float(qty or 0) <= 0:
            continue
        so_lines.append(
            {
                "item_name": name,
                "item_key": it.get("item_key"),
                "qty": float(qty),
                "value": it.get("so_value"),
            }
        )
    if so_lines:
        payload["so_line_items"] = so_lines
        payload["so_line_count"] = len(so_lines)
    return _json_response({"success": True, "data": payload})


@data_blueprint.route("/api/v1/order-fulfillment/tracking/<int:tracking_id>/file", methods=["GET"])
@require_jwt_auth
def download_order_fulfillment_tracking_file(tracking_id: int) -> Response:
    """SO/CI PDF: Google Drive first (NEXORA folder), then local upload file."""
    kind = (request.args.get("kind") or "so").strip().lower()
    if kind not in ("so", "ci"):
        return _json_response({"success": False, "error": {"message": "kind must be so or ci"}}, 400)

    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    tracking = db.get_order_lifecycle_tracking(
        tracking_id, workspace_id=workspace_id, user_id=_current_user_id()
    )
    if tracking is None:
        return _json_response({"success": False, "error": {"message": "Tracking record not found"}}, 404)

    user = getattr(request, "user", None)
    user_id = int(user["user_id"]) if isinstance(user, dict) and user.get("user_id") is not None else None
    drive_col = "sales_order_drive_file_id" if kind == "so" else "commercial_invoice_drive_file_id"
    local_col = "sales_order_file_reference" if kind == "so" else "commercial_invoice_file_reference"
    drive_id = (tracking.get(drive_col) or "").strip()
    download_name = f"{kind}_{tracking.get('order_ref_no') or tracking_id}.pdf"

    if drive_id and user_id:
        try:
            from app.storage.manager import StorageManager
            from app.storage.providers.google_drive_provider import GoogleDriveProvider

            manager = StorageManager()
            manager.register_provider("google_drive", GoogleDriveProvider)
            payload = manager.download_file_bytes(
                user_id=user_id, file_id=drive_id, workspace_id=workspace_id
            )
            content = payload.get("content") or b""
            filename = payload.get("file_name") or download_name
            mime = payload.get("mime_type") or "application/pdf"
            return Response(
                content,
                mimetype=mime,
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Content-Length": str(len(content)),
                    "Cache-Control": "private, max-age=86400",
                },
            )
        except Exception:
            pass

    local_ref = tracking.get(local_col)
    if local_ref:
        candidate = Path(str(local_ref))
        if candidate.is_file():
            return send_file(candidate, as_attachment=False, download_name=candidate.name or download_name)

    return _json_response(
        {
            "success": False,
            "error": {
                "message": "PDF not on Drive yet. Connect Google Drive and re-upload, or Sync Cloud Hub.",
            },
        },
        404,
    )


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
    from app.services import order_desk_archive as oda

    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    user_id = _current_user_id()
    tracking = db.get_order_lifecycle_tracking(
        tracking_id, workspace_id=workspace_id, user_id=user_id
    )
    if tracking is None:
        return _json_response({"success": False, "error": {"message": "Tracking record not found"}}, 404)
    if user_id is not None:
        conn = sqlite3.connect(_db_path())
        try:
            items, achievements, payments, processed = oda.collect_tracking_bundle(
                conn, tracking_id
            )
            oda.archive_tracking_bundle(
                conn,
                user_id,
                tracking,
                fulfillment_items=items,
                achievements=achievements,
                payment_entries=payments,
                processed_documents=processed,
                restore_scope="run",
            )
            conn.commit()
        finally:
            conn.close()
    file_references = db.delete_order_lifecycle_tracking(
        tracking_id, workspace_id=workspace_id, user_id=user_id
    )
    if file_references is None:
        return _json_response({"success": False, "error": {"message": "Tracking record not found"}}, 404)

    _cleanup_order_fulfillment_files(
        file_references,
        user_id=user_id,
        order_ref_no=str(tracking.get("order_ref_no") or ""),
        tracking_id=tracking_id,
    )
    return _json_response({"success": True, "data": {"deleted_tracking_id": tracking_id}})


@data_blueprint.route("/api/v1/order-fulfillment/tracking/delete-selected", methods=["POST"])
@require_jwt_auth
def delete_selected_order_fulfillment_tracking() -> Response:
    """Bulk-delete SO/CI tracking rows (and linked files when under upload root)."""
    from app.services import order_desk_archive as oda

    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or data.get("tracking_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return _json_response(
            {"success": False, "error": {"message": "ids must be a non-empty list"}},
            400,
        )
    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()
    user_id = _current_user_id()
    deleted = 0
    for raw in raw_ids:
        try:
            tracking_id = int(raw)
        except (TypeError, ValueError):
            continue
        tracking = db.get_order_lifecycle_tracking(
            tracking_id, workspace_id=workspace_id, user_id=user_id
        )
        if tracking is None:
            continue
        if user_id is not None:
            conn = sqlite3.connect(_db_path())
            try:
                items, achievements, payments, processed = oda.collect_tracking_bundle(
                    conn, tracking_id
                )
                oda.archive_tracking_bundle(
                    conn,
                    user_id,
                    tracking,
                    fulfillment_items=items,
                    achievements=achievements,
                    payment_entries=payments,
                    processed_documents=processed,
                    restore_scope="run",
                )
                conn.commit()
            finally:
                conn.close()
        file_references = db.delete_order_lifecycle_tracking(
            tracking_id, workspace_id=workspace_id, user_id=user_id
        )
        if file_references is None:
            continue
        _cleanup_order_fulfillment_files(
            file_references,
            user_id=user_id,
            order_ref_no=str(tracking.get("order_ref_no") or ""),
            tracking_id=tracking_id,
        )
        deleted += 1
    return _json_response({"success": True, "data": {"deleted": deleted}})


def _cleanup_order_fulfillment_files(
    file_references,
    user_id: int | None = None,
    order_ref_no: str | None = None,
    tracking_id: int | None = None,
) -> None:
    if not file_references:
        return
    from app.services import order_desk_archive as oda

    upload_root = oda.upload_root()
    if isinstance(file_references, dict):
        pairs = list(file_references.items())
    else:
        pairs = [(None, ref) for ref in file_references]
    for key, file_ref in pairs:
        if not file_ref:
            continue
        kind_hint = None
        if key and "sales_order" in str(key):
            kind_hint = "so"
        elif key and "commercial_invoice" in str(key):
            kind_hint = "ci"
        if user_id is not None:
            rel = oda.move_file_to_recycle(user_id, str(file_ref))
            if rel:
                conn = sqlite3.connect(_db_path())
                try:
                    oda.archive_file_reference(
                        conn,
                        user_id,
                        rel,
                        tracking_id=tracking_id,
                        kind_hint=kind_hint,
                        order_ref_no=order_ref_no,
                        original_path=str(file_ref),
                    )
                    conn.commit()
                finally:
                    conn.close()
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

    from app.services import order_desk_archive as oda

    user_id = _current_user_id()
    if user_id is not None:
        rel = oda.move_file_to_recycle(user_id, str(candidate))
        if rel:
            conn = sqlite3.connect(_db_path())
            try:
                path_norm = requested_path.replace("\\", "/")
                kind_hint = (
                    "so" if "/SO" in path_norm.upper() else
                    "ci" if "/CI" in path_norm.upper() else None
                )
                oda.archive_file_reference(
                    conn,
                    user_id,
                    rel,
                    original_path=str(candidate),
                    kind_hint=kind_hint,
                )
                conn.commit()
            finally:
                conn.close()
        return _json_response({"success": True, "data": {"deleted": requested_path, "recycled": True}})

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
    try:
        return _confirm_ci_so_link_impl()
    except Exception as exc:
        return _json_response(
            {
                "success": False,
                "error": {"message": f"CI link save failed: {exc}", "code": "ci_link_failed"},
            },
            500,
        )


def _confirm_ci_so_link_impl(payload: dict | None = None) -> Response:
    payload = payload if payload is not None else (request.get_json(silent=True) or {})
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

    # Rebuild CI detail for save — text/header on 512MB; tables only if RAM allows.
    if commercial_invoice_file_reference:
        commercial_invoice_parsed = _prepare_ci_parsed_for_save(
            commercial_invoice_file_reference,
            commercial_invoice_parsed if isinstance(commercial_invoice_parsed, dict) else {},
        )

    # Duplicate-detection: this CI's OWN Invoice No (NOT the Sales
    # Order Number it references) must be genuinely new. Re-uploading
    # the SAME invoice would silently double-count its qty/value —
    # reject rather than re-process. A DIFFERENT invoice for the same
    # order_ref_no (a legitimately separate CI) is always allowed.
    ci_raw_text = (commercial_invoice_parsed or {}).get("text") or ""
    ci_header = (commercial_invoice_parsed or {}).get("header") or _parse_sales_order_header_fields(ci_raw_text)
    invoice_no = (ci_header.get("invoice_no") if isinstance(ci_header, dict) else None) or None
    if not invoice_no:
        invoice_no = _parse_sales_order_header_fields(ci_raw_text).get("invoice_no")
    ci_date = None
    if isinstance(ci_header, dict):
        ci_date = _normalize_doc_date(str(ci_header.get("invoice_date") or "")) or None
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
    if not matching_so_for_move or not _lifecycle_has_real_sales_order(matching_so_for_move):
        # SO may only exist in FO↔SO Order Match — bridge then retry.
        _bridge_order_match_so_into_lifecycle(
            db,
            order_ref_no=order_ref_no,
            workspace_id=workspace_id or "default",
            user_id=_current_user_id(),
            distributor_id=(
                int(matching_so_for_move["distributor_id"])
                if matching_so_for_move and matching_so_for_move.get("distributor_id")
                else None
            ),
        )
        matching_so_for_move = db.get_order_lifecycle_by_order_ref_no(
            order_ref_no, workspace_id=workspace_id
        )
    if not matching_so_for_move or not _lifecycle_has_real_sales_order(matching_so_for_move):
        return Response(
            json.dumps({
                "success": False,
                "error": {
                    "message": (
                        f"No Sales Order found in Nexora for order ref \"{order_ref_no}\" "
                        "(Sales Orders PDF or Order Match). "
                        "Use Confirm — save CI-only (no SO), or upload/match the SO first."
                    ),
                    "code": "so_not_found",
                },
            }),
            mimetype="application/json",
            status=404,
        )
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
            commercial_invoice_date=ci_date,
            workspace_id=workspace_id,
        )
        ci_user = getattr(request, "user", None)
        ci_user_id = (
            int(ci_user["user_id"])
            if isinstance(ci_user, dict) and ci_user.get("user_id") is not None
            else None
        )
        # Dropped after _apply_ci_line_items_and_achievement below, which
        # still reads this file.
        ci_drive_file_id = _archive_order_pdf_to_drive(
            db=db,
            user_id=ci_user_id,
            workspace_id=workspace_id,
            tracking_id=tracking_id,
            kind="ci",
            local_path=commercial_invoice_file_reference,
            display_name=f"{invoice_no or order_ref_no or 'CI'}.pdf",
        )
    except ValueError as exc:
        return Response(
            json.dumps({"success": False, "error": {"message": str(exc)}}),
            mimetype="application/json",
            status=404,
        )

    item_results, has_any_discrepancy, achievement_id, achievement_error, article_master_match = (
        _apply_ci_line_items_and_achievement(
            db,
            tracking_id=tracking_id,
            commercial_invoice_file_reference=commercial_invoice_file_reference,
            commercial_invoice_parsed=commercial_invoice_parsed,
            invoice_no=invoice_no,
            amount=amount,
            notes=notes,
            workspace_id=workspace_id,
            user_id=ci_user_id,
        )
    )

    if ci_user_id and tracking_id:
        from app.services import order_desk_archive as oda

        restore_conn = sqlite3.connect(_db_path())
        try:
            oda.restore_tracking_after_upload(
                restore_conn,
                ci_user_id,
                order_ref_no,
                int(tracking_id),
                workspace_id or "default",
                upload_kind="ci",
            )
        finally:
            restore_conn.close()

    _drop_local_after_drive_backup(commercial_invoice_file_reference, ci_drive_file_id)

    if achievement_error and achievement_id is None and amount is not None:
        return Response(
            json.dumps({
                "success": True,
                "data": {
                    "tracking_id": tracking_id,
                    "achievement_id": None,
                    "achievement_error": achievement_error,
                    "article_master_match": article_master_match,
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
                "article_master_match": article_master_match,
                "mode": "linked",
                "detail_level": (
                    commercial_invoice_parsed.get("detail_level")
                    if isinstance(commercial_invoice_parsed, dict)
                    else None
                ),
            },
        }, default=str),
        mimetype="application/json",
    )


@data_blueprint.route("/api/v1/order-fulfillment/confirm-ci-only", methods=["POST"])
@require_jwt_auth
def confirm_ci_only() -> Response:
    """
    Save a Commercial Invoice when no Sales Order exists in Nexora yet.
    Requires explicit user confirmation + distributor_id. Sale/achievement
    is recorded from the CI amount. order_ref_no prefers the SO number
    printed on the CI so a later SO upload can merge into the same row.
    """
    try:
        return _confirm_ci_only_impl()
    except Exception as exc:
        return _json_response(
            {
                "success": False,
                "error": {"message": f"CI-only save failed: {exc}", "code": "ci_only_failed"},
            },
            500,
        )


def _confirm_ci_only_impl(payload: dict | None = None) -> Response:
    payload = payload if payload is not None else (request.get_json(silent=True) or {})
    commercial_invoice_file_reference = payload.get("commercial_invoice_file_reference")
    commercial_invoice_parsed = payload.get("commercial_invoice_parsed") or {}
    amount = payload.get("amount")
    notes = payload.get("notes")
    distributor_id = payload.get("distributor_id")
    order_ref_no = (payload.get("order_ref_no") or "").strip()
    invoice_no = (payload.get("invoice_no") or "").strip() or None

    try:
        distributor_id = int(distributor_id) if distributor_id not in (None, "") else None
    except (TypeError, ValueError):
        distributor_id = None
    if distributor_id is None:
        return _json_response(
            {"success": False, "error": {"message": "distributor_id is required for CI-only save"}},
            400,
        )
    if not commercial_invoice_file_reference:
        return _json_response(
            {"success": False, "error": {"message": "commercial_invoice_file_reference is required"}},
            400,
        )

    db = CentralizedDB(_db_path())
    workspace_id = get_workspace_id()

    if commercial_invoice_file_reference:
        commercial_invoice_parsed = _prepare_ci_parsed_for_save(
            commercial_invoice_file_reference,
            commercial_invoice_parsed if isinstance(commercial_invoice_parsed, dict) else {},
        )

    ci_raw_text = (commercial_invoice_parsed or {}).get("text") or ""
    ci_header = (commercial_invoice_parsed or {}).get("header") or _parse_sales_order_header_fields(ci_raw_text)
    if not invoice_no and isinstance(ci_header, dict):
        invoice_no = (ci_header.get("invoice_no") or "").strip() or None
    if not invoice_no:
        invoice_no = (_parse_sales_order_header_fields(ci_raw_text).get("invoice_no") or "").strip() or None
    if not order_ref_no and isinstance(ci_header, dict):
        order_ref_no = (ci_header.get("order_ref_no") or "").strip()
    if not order_ref_no:
        order_ref_no = (_parse_sales_order_header_fields(ci_raw_text).get("order_ref_no") or "").strip()
    if not order_ref_no:
        order_ref_no = f"CI-{invoice_no}" if invoice_no else ""
    if not order_ref_no:
        return _json_response(
            {
                "success": False,
                "error": {
                    "message": "Could not determine order_ref_no or invoice_no from the CI"
                },
            },
            400,
        )

    ci_date = None
    if isinstance(ci_header, dict):
        ci_date = _normalize_doc_date(str(ci_header.get("invoice_date") or "")) or None

    if invoice_no and db.is_document_already_processed(workspace_id, "CI", invoice_no):
        return _json_response({
            "success": True,
            "data": {
                "is_duplicate": True,
                "tracking_id": None,
                "achievement_id": None,
                "link_error": (
                    f"This Commercial Invoice (Invoice No \"{invoice_no}\") has ALREADY "
                    f"been processed — rejecting to avoid double-counting."
                ),
                "mode": "ci_only",
            },
        })

    distributor = db.get_master_distributor(distributor_id, workspace_id=workspace_id)
    if not distributor:
        return _json_response(
            {"success": False, "error": {"message": f"Distributor id {distributor_id} not found"}},
            404,
        )

    # Ensure selected Customers row matches CI buyer (GST preferred, else name).
    acknowledge_party_mismatch = bool(payload.get("acknowledge_party_mismatch"))
    ci_text_for_match = ci_raw_text
    if not ci_text_for_match and commercial_invoice_file_reference:
        try:
            ci_text_for_match = _extract_pdf_text(commercial_invoice_file_reference) or ""
        except Exception:
            ci_text_for_match = ""
    own_profile = db.get_company_profile(workspace_id)
    own_gst = own_profile.get("gst_number") if own_profile else None
    ci_buyer_gst = _extract_ci_buyer_gst(ci_text_for_match, own_gst)
    ci_buyer_name = None
    if isinstance(ci_header, dict):
        ci_buyer_name = (ci_header.get("buyer_name") or "").strip() or None
    if not ci_buyer_name:
        ci_buyer_name = (
            _parse_sales_order_header_fields(ci_text_for_match).get("buyer_name") or ""
        ).strip() or None
    ci_match = _match_ci_buyer_to_customers(
        db,
        buyer_name=ci_buyer_name,
        buyer_gst=ci_buyer_gst,
        workspace_id=workspace_id,
        allow_fuzzy=not bool(ci_buyer_gst),
    )
    matched = ci_match.get("distributor") or {}
    selected_gst = (distributor.get("gst_no") or "").strip().upper()
    mismatch_reason = None
    if matched.get("id") is not None and int(matched["id"]) != int(distributor_id):
        mismatch_reason = (
            f"CI buyer maps to Customers \"{matched.get('name')}\" "
            f"(id {matched.get('id')}), but you selected "
            f"\"{distributor.get('firm_name') or distributor.get('name')}\" "
            f"(id {distributor_id})."
        )
    elif ci_buyer_gst and selected_gst and ci_buyer_gst != selected_gst:
        mismatch_reason = (
            f"CI buyer GST {ci_buyer_gst} does not match selected Customers GST {selected_gst}."
        )
    if mismatch_reason and not acknowledge_party_mismatch:
        return _json_response(
            {
                "success": False,
                "error": {
                    "message": mismatch_reason + " Select the matched distributor, or confirm mismatch.",
                    "code": "ci_customers_mismatch",
                    "ci_customer_match": ci_match,
                },
            },
            409,
        )

    try:
        commercial_invoice_file_reference = str(
            _resolve_existing_order_fulfillment_source(commercial_invoice_file_reference)
        )
    except ValueError:
        return _json_response(
            {
                "success": False,
                "error": {
                    "message": (
                        "commercial_invoice_file_reference must point to a file "
                        "previously uploaded under order fulfillment storage"
                    )
                },
            },
            400,
        )

    distributor_name_for_folder = (
        distributor.get("firm_name") or distributor.get("name") or "Unassigned"
    )
    try:
        moved_path = _move_into_distributor_order_cycle_folder(
            Path(commercial_invoice_file_reference),
            distributor_name_for_folder,
            "CI",
            order_sheet_name="CI Only (no SO yet)",
        )
        commercial_invoice_file_reference = str(moved_path)
    except (FileNotFoundError, OSError, ValueError):
        pass

    try:
        tracking_id = db.save_ci_only_order_lifecycle(
            order_ref_no=order_ref_no,
            distributor_id=distributor_id,
            commercial_invoice_file_reference=commercial_invoice_file_reference,
            commercial_invoice_parsed=commercial_invoice_parsed,
            commercial_invoice_date=ci_date,
            workspace_id=workspace_id,
        )
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "UPDATE order_lifecycle_tracking SET order_sheet_name = ? WHERE tracking_id = ?",
                ("CI Only (no SO yet)", tracking_id),
            )
            conn.commit()
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)

    ci_user = getattr(request, "user", None)
    ci_user_id = (
        int(ci_user["user_id"])
        if isinstance(ci_user, dict) and ci_user.get("user_id") is not None
        else None
    )
    # Local copy is still read by _apply_ci_line_items_and_achievement below,
    # so it is dropped after that, not here.
    ci_drive_file_id = _archive_order_pdf_to_drive(
        db=db,
        user_id=ci_user_id,
        workspace_id=workspace_id,
        tracking_id=tracking_id,
        kind="ci",
        local_path=commercial_invoice_file_reference,
        display_name=f"{invoice_no or order_ref_no or 'CI'}.pdf",
    )

    item_results, has_any_discrepancy, achievement_id, achievement_error, article_master_match = (
        _apply_ci_line_items_and_achievement(
            db,
            tracking_id=tracking_id,
            commercial_invoice_file_reference=commercial_invoice_file_reference,
            commercial_invoice_parsed=commercial_invoice_parsed,
            invoice_no=invoice_no,
            amount=amount,
            notes=notes or "CI-only save (SO not in Nexora at upload time)",
            workspace_id=workspace_id,
            user_id=ci_user_id,
        )
    )

    if ci_user_id and tracking_id and order_ref_no:
        from app.services import order_desk_archive as oda

        restore_conn = sqlite3.connect(_db_path())
        try:
            oda.restore_tracking_after_upload(
                restore_conn,
                ci_user_id,
                order_ref_no,
                int(tracking_id),
                workspace_id or "default",
                upload_kind="ci",
            )
        finally:
            restore_conn.close()

    _drop_local_after_drive_backup(commercial_invoice_file_reference, ci_drive_file_id)

    return _json_response({
        "success": True,
        "data": {
            "tracking_id": tracking_id,
            "achievement_id": achievement_id,
            "achievement_error": achievement_error,
            "item_results": item_results,
            "has_discrepancy": has_any_discrepancy,
            "article_master_match": article_master_match,
            "mode": "ci_only",
            "order_ref_no": order_ref_no,
            "invoice_no": invoice_no,
            "distributor_name": distributor_name_for_folder,
            "detail_level": (
                commercial_invoice_parsed.get("detail_level")
                if isinstance(commercial_invoice_parsed, dict)
                else None
            ),
        },
    })


def _flask_response_payload(resp: Response) -> tuple[int, dict]:
    try:
        body = json.loads(resp.get_data(as_text=True) or "{}")
    except Exception:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def _expand_ci_upload_items(items: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """PDF / ZIP / RAR → ordered list of (filename, pdf_bytes). Same unpack as SO Pack."""
    from app.services.so_pack_consolidate import _load_pack_pdfs

    pdfs: list[tuple[str, bytes]] = []
    for name, raw in items:
        kind = _so_pack_sniff_kind(raw, name)
        if kind == "pdf":
            pdfs.append((_so_pack_safe_filename(name, "pdf"), raw))
        elif kind in ("zip", "rar"):
            unpacked = _load_pack_pdfs(raw, _so_pack_safe_filename(name, kind))
            if not unpacked:
                raise ValueError(f"No PDFs inside {name}")
            pdfs.extend(unpacked)
        else:
            raise ValueError(f"Unsupported CI upload: {name}. Use PDF, ZIP, or RAR.")
    if not pdfs:
        raise ValueError("No commercial invoice PDFs found")
    return pdfs


def _collect_ci_pdfs_from_request() -> list[tuple[str, bytes]]:
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    uploads += [f for f in request.files.getlist("files") if f and f.filename]
    seen: set[int] = set()
    unique = []
    for f in uploads:
        key = id(f)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    if not unique:
        raise ValueError("file is required")
    items: list[tuple[str, bytes]] = []
    for f in unique:
        raw = f.read()
        if not raw:
            raise ValueError(f"Empty file: {f.filename}")
        items.append((f.filename or "ci.pdf", raw))
    return _expand_ci_upload_items(items)


def _ci_party_safe_for_auto(preview: dict) -> bool:
    status = ((preview.get("party_match") or {}) if isinstance(preview.get("party_match"), dict) else {}).get(
        "status"
    )
    return status == "matched" or not status


def _auto_confirm_ci_preview(preview: dict) -> dict:
    """Desktop bulk rules: auto-link when real SO + party OK; CI-only when no SO + Customers matched."""
    invoice_no = preview.get("invoice_no") or ""
    order_ref_no = preview.get("order_ref_no") or ""
    if preview.get("is_duplicate"):
        return {
            "state": "dup",
            "status": preview.get("message") or preview.get("link_error") or "Duplicate — already processed",
            "invoice_no": invoice_no,
            "order_ref_no": order_ref_no,
            "tracking_id": None,
        }

    party_ok = _ci_party_safe_for_auto(preview)
    party_match = preview.get("party_match") if isinstance(preview.get("party_match"), dict) else {}
    compare = preview.get("compare") if isinstance(preview.get("compare"), dict) else {}
    matching_so = (
        preview.get("matching_sales_order")
        if isinstance(preview.get("matching_sales_order"), dict)
        else {}
    )
    has_real_so = (
        not preview.get("no_match_found")
        and bool(matching_so)
        and (
            compare.get("so_has_file")
            or matching_so.get("sales_order_file_reference")
            or matching_so.get("has_sales_order")
            or matching_so.get("from_order_match")
            or preview.get("order_match_so")
        )
    )
    amount = preview.get("extracted_amount")
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None

    if has_real_so and party_ok and party_match.get("status") != "mismatch":
        resp = _confirm_ci_so_link_impl(
            {
                "order_ref_no": preview.get("order_ref_no"),
                "commercial_invoice_file_reference": preview.get("commercial_invoice_file_reference"),
                "commercial_invoice_parsed": preview.get("commercial_invoice_parsed"),
                "amount": amount,
            }
        )
        _code, body = _flask_response_payload(resp)
        out = body.get("data") if isinstance(body.get("data"), dict) else {}
        if body.get("success") and not out.get("is_duplicate") and not out.get("link_error"):
            return {
                "state": "ok",
                "status": f"Linked to SO · tracking #{out.get('tracking_id') or '—'}",
                "invoice_no": invoice_no,
                "order_ref_no": order_ref_no,
                "tracking_id": out.get("tracking_id"),
            }
        return {
            "state": "bad",
            "status": out.get("link_error")
            or ((body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None)
            or "Link failed",
            "invoice_no": invoice_no,
            "order_ref_no": order_ref_no,
            "tracking_id": None,
        }

    suggested = preview.get("suggested_distributor") if isinstance(preview.get("suggested_distributor"), dict) else {}
    if (
        preview.get("no_match_found")
        and party_ok
        and party_match.get("status") == "matched"
        and suggested.get("id")
    ):
        resp = _confirm_ci_only_impl(
            {
                "order_ref_no": preview.get("order_ref_no"),
                "invoice_no": preview.get("invoice_no"),
                "distributor_id": suggested.get("id"),
                "commercial_invoice_file_reference": preview.get("commercial_invoice_file_reference"),
                "commercial_invoice_parsed": preview.get("commercial_invoice_parsed"),
                "amount": amount,
                "acknowledge_party_mismatch": False,
            }
        )
        _code, body = _flask_response_payload(resp)
        out = body.get("data") if isinstance(body.get("data"), dict) else {}
        if body.get("success") and not out.get("is_duplicate") and not out.get("link_error"):
            return {
                "state": "ok",
                "status": f"CI-only saved · tracking #{out.get('tracking_id') or '—'}",
                "invoice_no": invoice_no,
                "order_ref_no": order_ref_no,
                "tracking_id": out.get("tracking_id"),
            }
        return {
            "state": "bad",
            "status": out.get("link_error")
            or ((body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None)
            or "CI-only save failed",
            "invoice_no": invoice_no,
            "order_ref_no": order_ref_no,
            "tracking_id": None,
        }

    if preview.get("no_match_found"):
        status = "Needs review — pick distributor / confirm CI-only"
    elif party_match.get("status") == "mismatch":
        status = "Needs review — party mismatch"
    else:
        status = "Needs review"
    return {
        "state": "review",
        "status": status,
        "invoice_no": invoice_no,
        "order_ref_no": order_ref_no,
        "tracking_id": None,
    }


def _ingest_one_ci_pdf(filename: str, data: bytes) -> dict:
    from werkzeug.datastructures import FileStorage

    fs = FileStorage(stream=io.BytesIO(data), filename=filename, content_type="application/pdf")
    preview_resp = _upload_invoice_v2_impl(uploaded_file=fs)
    _code, body = _flask_response_payload(preview_resp)
    if not body.get("success"):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        return {
            "file": filename,
            "state": "bad",
            "status": err.get("message") or f"Upload failed (HTTP {_code})",
            "invoice_no": "",
            "order_ref_no": "",
            "tracking_id": None,
        }
    preview = body.get("data") if isinstance(body.get("data"), dict) else {}
    row = _auto_confirm_ci_preview(preview)
    row["file"] = filename
    return row


def _ci_bulk_summary(results: list[dict]) -> dict:
    return {
        "saved": sum(1 for r in results if r.get("state") == "ok"),
        "duplicates": sum(1 for r in results if r.get("state") == "dup"),
        "review": sum(1 for r in results if r.get("state") == "review"),
        "failed": sum(1 for r in results if r.get("state") == "bad"),
        "total": len(results),
        "results": results,
    }


def _ingest_ci_pdfs(pdfs: list[tuple[str, bytes]], on_progress=None) -> dict:
    results: list[dict] = []
    total = len(pdfs)
    for i, (name, raw) in enumerate(pdfs, 1):
        if on_progress:
            on_progress(f"Reading {i}/{total} — {name}")
        try:
            results.append(_ingest_one_ci_pdf(name, raw))
        except Exception as exc:
            results.append(
                {
                    "file": name,
                    "state": "bad",
                    "status": str(exc),
                    "invoice_no": "",
                    "order_ref_no": "",
                    "tracking_id": None,
                }
            )
    return _ci_bulk_summary(results)


@data_blueprint.route("/api/v1/order-fulfillment/upload/invoices-bulk", methods=["POST"])
@require_jwt_auth
def upload_invoices_bulk() -> Response:
    """Unpack PDF/ZIP/RAR commercial invoices, preview each, auto-confirm like desktop bulk."""
    try:
        pdfs = _collect_ci_pdfs_from_request()
        data = _ingest_ci_pdfs(pdfs)
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)
    except Exception as exc:
        return _json_response(
            {"success": False, "error": {"message": f"CI bulk upload failed: {exc}"}},
            500,
        )
    return _json_response({"success": True, "data": data})


@data_blueprint.route("/api/v1/order-fulfillment/upload/invoices-bulk-stream", methods=["POST"])
@require_jwt_auth
def upload_invoices_bulk_stream() -> Response:
    """Same as invoices-bulk, with NDJSON progress lines (Android SO Pack overlay)."""
    try:
        pdfs = _collect_ci_pdfs_from_request()
    except ValueError as exc:
        return _json_response({"success": False, "error": {"message": str(exc)}}, 400)

    @stream_with_context
    def generate():
        try:
            total = len(pdfs)
            yield json.dumps(
                {"type": "progress", "message": f"Found {total} commercial invoice PDF(s)…"}
            ) + "\n"
            results: list[dict] = []
            for i, (name, raw) in enumerate(pdfs, 1):
                yield json.dumps(
                    {"type": "progress", "message": f"Reading {i}/{total} — {name}"}
                ) + "\n"
                try:
                    results.append(_ingest_one_ci_pdf(name, raw))
                except Exception as exc:
                    results.append(
                        {
                            "file": name,
                            "state": "bad",
                            "status": str(exc),
                            "invoice_no": "",
                            "order_ref_no": "",
                            "tracking_id": None,
                        }
                    )
            yield json.dumps(
                {"type": "done", "data": _ci_bulk_summary(results)}, default=str
            ) + "\n"
        except Exception as exc:
            yield json.dumps({"type": "error", "message": f"CI bulk upload failed: {exc}"}) + "\n"

    resp = Response(generate(), mimetype="application/x-ndjson")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@data_blueprint.route("/articles")
@require_jwt_auth
def articles() -> str:
    db = CentralizedDB(_db_path())
    articles = json.dumps(db.list_articles_by_category(workspace_id=get_workspace_id()), indent=2)
    return render_template_string(
        '<h1>Article Master</h1><pre>{{ articles }}</pre><p><a href="/">Back</a></p>',
        articles=articles,
    )


def _summarize_ask_nexora_search(
    search_payload: dict | None, raw_query: str = ""
) -> str:
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
        normalized_raw = (raw_query or "").lower()
        wants_mrp = "mrp" in normalized_raw
        wants_exmill = any(
            t in normalized_raw
            for t in ("exmill", "ex-mill", "ex mill", "x mill", "xmill", "x-mill")
        )
        wants_ptr = "ptr" in normalized_raw
        wants_specific_field = wants_mrp or wants_exmill or wants_ptr
        wants_size_only = "size" in normalized_raw and not wants_specific_field

        def _as_float(value: Any) -> float:
            try:
                return float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        _PHYSICAL_SIZE_KEYS = (
            "bs size",
            "size",
            "bedset size (cms)",
            "bedset size",
            "pillow size",
        )

        def _physical_size(a: dict) -> str | None:
            """Physical cm dimension (e.g. "274x274") from extra_attributes,
            same field names the Android app's physicalSizeLabel() checks."""
            raw = a.get("extra_attributes")
            if not raw:
                return None
            try:
                attrs = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                return None
            if not isinstance(attrs, dict):
                return None
            lower_map = {str(k).lower(): v for k, v in attrs.items()}
            for key in _PHYSICAL_SIZE_KEYS:
                value = lower_map.get(key)
                if value and str(value).strip():
                    return str(value).strip()
            return None

        def _price_bits(a: dict) -> list[str]:
            # Only show the field(s) actually asked about — "aster exmill"
            # shouldn't also print MRP the user never asked for. With no
            # specific field named, default to MRP only — Ex-mill/PTR are
            # cost-side figures the user has to ask for explicitly.
            mrp_n = _as_float(a.get("mrp"))
            ex_mill_n = _as_float(a.get("ex_mill_price"))
            ptr_n = _as_float(a.get("ptr"))
            bits: list[str] = []
            if wants_size_only:
                return bits
            if wants_specific_field:
                if wants_mrp and mrp_n > 0:
                    bits.append(f"MRP ₹{int(round(mrp_n))}")
                if wants_exmill:
                    bits.append(f"Ex-mill ₹{int(round(ex_mill_n))}")
                if wants_ptr and ptr_n > 0:
                    bits.append(f"PTR ₹{int(round(ptr_n))}")
            else:
                if mrp_n > 0:
                    bits.append(f"MRP ₹{int(round(mrp_n))}")
            return bits

        def _label(a: dict, include_size: bool) -> str:
            brand = (a.get("brand") or "?").strip() or "?"
            size = (a.get("size") or "").strip()
            if include_size and size:
                physical = _physical_size(a)
                size_part = f"{size} ({physical})" if physical else size
                base = f"{brand} {size_part}"
            else:
                base = brand
            bits = _price_bits(a)
            if bits:
                return f"{base} — {', '.join(bits)}"
            return base

        # Show size whenever it was specifically asked about (or nothing
        # specific was asked) — only a price-only ask on a single match
        # skips it, matching "aster exmill" -> "Aster — Ex-mill ₹625"
        # with no size needed to disambiguate a single row.
        include_size_for_single = wants_size_only or not wants_specific_field

        if len(arts) == 1 and isinstance(arts[0], dict):
            # A single match doesn't need row numbering or a "found N"
            # header — just answer with the field(s) that were asked.
            chunks.append(_label(arts[0], include_size=include_size_for_single))
        else:
            # The Ask Nexora sheet's answer bubble scrolls internally now
            # (AskNexoraSheet.kt), so there's no longer a reason to cap the
            # list at 10 and hide the rest behind a static "...and N more"
            # — show every match up to the same 120-row ceiling the
            # underlying DB query already applies.
            lines = [f"Found {len(arts)} match(es):"]
            for idx, a in enumerate(arts, start=1):
                if not isinstance(a, dict):
                    continue
                lines.append(f"{idx}. {_label(a, include_size=True)}")
            chunks.append("\n".join(lines))

    return "\n\n".join(chunks) if chunks else "No matching information found."


_UNRESOLVED_ANSWER_MARKERS = (
    "no matching information found",
    "i could not find a distributor named",
    "i couldn't identify the distributor",
    "i couldn't identify that distributor",
    "no target/achievement records found",
    "no target data found",
    "i couldn't find any orders for that season",
    "i couldn't work out that calculation",
    "no credit policy records found",
)


def _is_unresolved_answer(answer: str) -> bool:
    """True for answers that mean Nexora couldn't understand or find the
    entity asked about — as opposed to a real, definitive business answer
    like "no active alerts" or "no PJP entry planned" (those are correct
    zero-result answers, not comprehension failures)."""
    lowered = (answer or "").lower()
    return any(marker in lowered for marker in _UNRESOLVED_ANSWER_MARKERS)


_PJP_WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _pjp_label_caps(label: str) -> str:
    """str.capitalize() lowercases everything after the first letter —
    fine for word labels ("today" -> "Today", "monday" -> "Monday") but
    it would mangle an absolute-date label ("17 Aug" -> "17 aug"), which
    is already correctly cased coming out of strftime."""
    return label if label[:1].isdigit() else label.capitalize()


def _resolve_pjp_query_date(query: str, today) -> tuple[Any, str]:
    """Parse a relative date reference out of a PJP query (today/tomorrow/
    day-after-tomorrow/a weekday name, English or Hindi) and return the
    target date plus a human label for the answer. Defaults to today when
    nothing more specific is mentioned.
    """
    normalized = (query or "").lower()
    # "parso" (day after tomorrow) is commonly misheard by voice STT as
    # "person" or "parson" — real English words, so this substitution is
    # scoped to PJP date-parsing only, not applied as a global normalization
    # that could misfire on an unrelated "person"/"parson" mention.
    normalized = re.sub(r"\b(person|parson)s?\b", "parso", normalized)
    if "day after tomorrow" in normalized or "parso" in normalized:
        return today + timedelta(days=2), "day after tomorrow"
    if "tomorrow" in normalized or "kal" in normalized:
        return today + timedelta(days=1), "tomorrow"
    for name, weekday in _PJP_WEEKDAY_NAMES.items():
        if name in normalized:
            delta = (weekday - today.weekday()) % 7
            return today + timedelta(days=delta), name.capitalize()
    absolute_date = find_absolute_date_in_query(
        normalized, today, assume_future=not _looks_like_past_tense_pjp_query(normalized)
    )
    if absolute_date:
        return absolute_date, absolute_date.strftime("%d %b")
    return today, "today"


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
    if row:
        return dict(row)
    # Third tier: global_search()'s own phonetic/honorific-fold matching
    # (handles spelling variants like Shri/Shree/Sri, "binina"-style vowel
    # slips) — cheap to try since it's already there.
    try:
        fuzzy_results = db.global_search(entity, workspace_id=workspace_id, user_id=None)
        fuzzy_dists = (fuzzy_results.get("results") or {}).get("distributors") or []
        if fuzzy_dists and isinstance(fuzzy_dists[0], dict) and fuzzy_dists[0].get("name"):
            return fuzzy_dists[0]
    except Exception:
        pass
    # Fourth tier: edit-distance fuzzy match against every distributor
    # name/firm_name/nick in the workspace. Voice transcription reliably
    # produces single-letter substitutions/transpositions the phonetic
    # tier above doesn't cover — "bermina"->"Bernina" (n/m), "pranami"->
    # "Parnami" (letter swap). Compare against whole-word tokens, not the
    # full multi-word firm name, so a short spoken term still scores well
    # against one word of a longer name.
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT id, distributor_id, distributor_code, firm_name, "
            "firm_nick_name, name, phone_number, location, address, "
            "pincode, email, gst_no, buyer_code, zone, region, "
            "payment_terms, credit_limit, status FROM master_distributors "
            "WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    entity_lower = entity.lower()
    entity_fold = db._party_name_fold(entity_lower)
    entity_words = [w for w in entity_lower.split() if len(w) >= 4]
    entity_fold_words = [w for w in entity_fold.split() if len(w) >= 4]
    best_row = None
    best_ratio = 0.0
    best_match_count = 0
    for r in all_rows:
        for candidate in (r["name"], r["firm_name"], r["firm_nick_name"]):
            if not candidate:
                continue
            candidate_lower = candidate.lower()
            candidate_fold = db._party_name_fold(candidate_lower)
            ratio = difflib.SequenceMatcher(None, entity_lower, candidate_lower).ratio()
            candidate_words = [w for w in candidate_lower.split() if len(w) >= 3]
            candidate_fold_words = [w for w in candidate_fold.split() if len(w) >= 3]
            for word in candidate_words:
                ratio = max(
                    ratio, difflib.SequenceMatcher(None, entity_lower, word).ratio()
                )
            # A multi-word spoken query ("savitri sticker" for "Savitri
            # Steel...") rarely matches well as one long string against
            # the candidate — the mangled second word drags the whole-
            # string ratio down even when the distinctive first word is a
            # near-perfect hit. Compare word-for-word too, with a higher
            # bar (0.75) since a single short generic word (e.g. "traders")
            # coincidentally matching isn't enough signal on its own. Also
            # count how many of the entity's own distinct words this
            # candidate explains — a word shared by several distributors
            # ("International", "Traders", ...) ties every one of them on
            # raw ratio, so the tie-break below needs to know that
            # "Sain International" matches both "sain" and "international"
            # while "Bernina International" only matches one.
            matched_entity_words = 0
            for ew in entity_words:
                word_hit = False
                for cw in candidate_words:
                    word_ratio = difflib.SequenceMatcher(None, ew, cw).ratio()
                    if word_ratio >= 0.75:
                        ratio = max(ratio, word_ratio)
                        word_hit = True
                if word_hit:
                    matched_entity_words += 1
            # Known surname/honorific spelling variants (Goyal/Goel/Goil,
            # Shri/Shree/Sri) — compare the folded forms too so these
            # count as an exact word match, not just a fuzzy-ratio one.
            for ew in entity_fold_words:
                for cw in candidate_fold_words:
                    if ew == cw:
                        ratio = max(ratio, 1.0)
            # Prefer whichever candidate explains more of the query's
            # distinct words first, then fall back to the raw ratio —
            # keeps "Kalra" -> "Kalra Agencies" working exactly as before
            # (single matched word, decided by ratio) while fixing the
            # multi-word shared-suffix collision above.
            if (matched_entity_words, ratio) > (best_match_count, best_ratio):
                best_ratio = ratio
                best_match_count = matched_entity_words
                best_row = r
    if best_row is not None and best_ratio >= 0.70:
        return dict(best_row)
    return None


def _resolve_distributor_with_context(
    db: CentralizedDB, query: str, context_query: str, workspace_id: str | None
) -> dict[str, Any] | None:
    """Resolve a distributor for the current query, falling back to the
    previous turn's question when this one names no distributor of its
    own — the simple follow-up case ("mobile number bhi batao" right
    after "bernina ka naam batao")."""
    entity = extract_party_name_candidate(query)
    distributor = _find_distributor_fuzzy(db, entity, workspace_id)
    if not distributor and context_query:
        context_entity = extract_party_name_candidate(context_query)
        if context_entity:
            distributor = _find_distributor_fuzzy(db, context_entity, workspace_id)
    return distributor


def _match_token_from_candidates(
    text: str, candidates: list[str], min_ratio: float = 0.78
) -> str | None:
    """Find which of a user's own distinct category/brand values (from
    their filled-order data — there's no fixed enum, categories and
    brands are whatever each user's order sheets contain) the free-text
    query is naming. Tries an exact multi-word substring first (handles
    "floral fiesta"), then falls back to per-word edit-distance so voice
    slips ("aster"/"astar") still resolve."""
    text = text or ""
    best: str | None = None
    best_ratio = 0.0
    for candidate in candidates:
        candidate_lower = candidate.lower().strip()
        if not candidate_lower:
            continue
        if candidate_lower in text:
            return candidate
        for cw in candidate_lower.split():
            if len(cw) < 3:
                continue
            for qw in text.split():
                if len(qw) < 3:
                    continue
                ratio = difflib.SequenceMatcher(None, qw, cw).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = candidate
    return best if best_ratio >= min_ratio else None


# "towel" and the stored category "Bath" are lexically unrelated — no
# edit-distance ratio in _match_token_from_candidates will ever bring
# them close, so a plain fuzzy match can never bridge this synonym pair.
_CATEGORY_WORD_SYNONYMS = {
    "towel": "Bath", "towels": "Bath", "bath": "Bath",
    "bedsheet": "Bed", "bedsheets": "Bed", "bed": "Bed", "sheet": "Bed", "sheets": "Bed",
}


def _match_category_from_query(entity_query: str, categories: list[str]) -> str | None:
    """Try the known category-word synonym map first (only when that
    literal category value actually exists in this user's own data), then
    fall back to the existing fuzzy matcher for brand-name-style typos."""
    categories_lower = {c.lower(): c for c in categories}
    for word in entity_query.split():
        mapped = _CATEGORY_WORD_SYNONYMS.get(word)
        if mapped and mapped.lower() in categories_lower:
            return categories_lower[mapped.lower()]
    return _match_token_from_candidates(entity_query, categories)


# Natural-language phrases for the size codes filled-order items are
# stored under (e.g. "KS BS" = King Size Bed Sheet) — voice queries say
# "king size bedsheet", not the abbreviation, so map the phrase to the
# fragment expected inside the stored code. Longer/more specific phrases
# are listed first so "double bed large" wins over the bare "double".
_SIZE_QUERY_ALIASES: list[tuple[str, str]] = [
    ("double bed large", "DBL"),
    ("dbl", "DBL"),
    ("king size", "KS"),
    ("king", "KS"),
    ("single bed", "SB"),
    ("single", "SB"),
    ("double bed", "DB"),
    ("double", "DB"),
    ("kids", "KB"),
    ("kid", "KB"),
]


def _match_size_from_query(text: str, sizes: list[str]) -> str | None:
    text = text or ""
    for phrase, code in _SIZE_QUERY_ALIASES:
        if phrase not in text:
            continue
        for size in sizes:
            if code in (size or "").upper().split():
                return size
    return None


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
    # Previous user question in this chat session, for simple follow-ups
    # ("mobile number bhi batao" after "bernina ka naam batao") — used as
    # an entity-resolution fallback when the current query names no
    # distributor of its own.
    context_query = str(
        payload.get("context") or request.args.get("context") or ""
    ).strip()

    # Strip wake phrases: "hey nexora", "ask nexora" (legacy jarvis still ok).
    query = re.sub(
        r"(?i)\b((hey|hi|ok|okay)\s+nexora|(ask|talk to)\s+(nexora|jarvis))\b[,:!.]?",
        "",
        query,
    ).strip()
    # Correct known voice-transcription slips (spelled-out acronyms like
    # "k a g" -> "kag", "bjp" -> "pjp", "cal" -> "kal") once, up front, so
    # intent matching, date parsing, and entity extraction all see the
    # corrected text consistently.
    query = normalize_voice_query(query)
    context_query = normalize_voice_query(context_query)
    db = CentralizedDB(_db_path())
    intent = infer_ai_intent(query)
    ask_prefix = ""
    if not query:
        return Response(
            json.dumps(
                {
                    "intent": "wake",
                    "query": "",
                    "answer": "Listening — ask about last visit, PJP, alerts, credit status, parties, or MRP 1000-2000.",
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

    if intent == "greeting":
        profile = db.get_user_profile(user_id) if user_id is not None else None
        display_name = ((profile or {}).get("full_name") or (profile or {}).get("username") or "").strip()
        first_name = display_name.split()[0] if display_name else ""
        greeting_target = f", {first_name}" if first_name else ""
        answer = f"{ask_prefix} Hello{greeting_target}! How can I help you today?"
    elif intent == "identity":
        # "Ayush Agarwal kon hai" — a person's name, not a firm. Distributor
        # contact names already resolve through _find_distributor_fuzzy
        # (same edit-distance tier used for firm names); retailers are
        # checked as a plain LIKE fallback since there's no equivalent
        # fuzzy tier for them yet.
        name_hint = identity_name_hint(query)
        if not name_hint:
            answer = f"{ask_prefix} Who do you mean?"
        else:
            person_dist = _find_distributor_fuzzy(db, name_hint, workspace_id)
            if person_dist and person_dist.get("name"):
                firm = person_dist.get("firm_name") or "—"
                nick = person_dist.get("firm_nick_name")
                firm_label = f"{firm} ({nick})" if nick else firm
                answer = (
                    f"{ask_prefix} {person_dist['name']} is the contact person "
                    f"for distributor {firm_label}."
                )
            else:
                like_query = f"%{name_hint.lower()}%"
                with sqlite3.connect(_db_path()) as id_conn:
                    id_conn.row_factory = sqlite3.Row
                    ret_row = id_conn.execute(
                        "SELECT name, owner_name, contact_person FROM master_retailers "
                        "WHERE workspace_id = ? AND (LOWER(owner_name) LIKE ? "
                        "OR LOWER(contact_person) LIKE ?) LIMIT 1",
                        (workspace_id, like_query, like_query),
                    ).fetchone()
                if ret_row:
                    person = ret_row["owner_name"] or ret_row["contact_person"]
                    answer = (
                        f"{ask_prefix} {person} is associated with retailer "
                        f"{ret_row['name']}."
                    )
                else:
                    answer = f"{ask_prefix} I couldn't find anyone named '{name_hint}'."
    elif intent == "last_visit":
        entity = (
            query.split("to", 1)[-1].strip().rstrip("?") if "to" in query else query
        )
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if not distributor and context_query:
            context_entity = extract_party_name_candidate(context_query)
            if context_entity:
                distributor = _find_distributor_fuzzy(db, context_entity, workspace_id)
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
        # the user's own planned visit for the requested day, not a
        # generic priority backlog across the whole workspace. Support
        # relative-date queries ("tomorrow pjp", "Saturday", "day after
        # tomorrow") instead of always answering for today.
        today = datetime.now(timezone.utc).date()
        target_date, date_label = _resolve_pjp_query_date(query, today)
        pjp_row = None
        try:
            with sqlite3.connect(_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                pjp_row = conn.execute(
                    "SELECT place_to_visit, business_activity, particulars, "
                    "day_type FROM monthly_pjp_days WHERE workspace_id = ? "
                    "AND user_id = ? AND plan_date = ?",
                    (workspace_id, user_id, target_date.isoformat()),
                ).fetchone()
        except sqlite3.OperationalError:
            pjp_row = None

        place = (pjp_row["place_to_visit"] if pjp_row else None) or ""
        day_type = ((pjp_row["day_type"] if pjp_row else None) or "").lower()
        day_ref = "Today's" if date_label == "today" else f"{_pjp_label_caps(date_label)}'s"
        if place.strip() and place.strip().lower() not in {"holiday", "leave"}:
            activity = pjp_row["business_activity"] if pjp_row else None
            extra = f" — {activity}" if activity else ""
            place_label = place.strip()
            if "market" not in place_label.lower():
                place_label = f"{place_label} Market"
            answer = f"{ask_prefix} {day_ref} planned visit: {place_label}{extra}."
        elif day_type in {"holiday", "leave"}:
            answer = f"{ask_prefix} {_pjp_label_caps(date_label)} is marked as {day_type} in your PJP."
        elif target_date.weekday() >= 5:
            # No entry planned and it's a Saturday/Sunday — a weekly off,
            # not a gap in the plan the user forgot to fill.
            answer = f"{ask_prefix} {_pjp_label_caps(date_label)} is a holiday (weekend)."
        else:
            answer = f"{ask_prefix} No PJP entry planned for {date_label} yet."
    elif intent == "purchase_trends":
        distributor = _resolve_distributor_with_context(
            db, query, context_query, workspace_id
        )
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
    elif intent == "cd_discount":
        entity = extract_party_name_candidate(query)
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            dist_id = distributor.get("id")
            dist_name = distributor.get("firm_name") or distributor.get("name") or "This distributor"
            status_rows = db.list_distributor_category_payment_status(user_id)
            dist_row = next((r for r in status_rows if r.get("distributor_id") == dist_id), None)
            if dist_row:
                season_bits = []
                for season_entry in dist_row.get("seasons", []):
                    cd_pct = float(season_entry.get("cd_percent") or 0)
                    if cd_pct <= 0:
                        continue
                    cd_amt = sum(
                        float(c.get("cd_amount") or 0) for c in season_entry.get("categories", [])
                    )
                    season_bits.append(
                        f"{season_entry.get('season')}: {cd_pct:g}% (Rs {indian_number_format(cd_amt)})"
                    )
                if season_bits:
                    answer = f"{ask_prefix} {dist_name} CD discount — " + "; ".join(season_bits) + "."
                else:
                    answer = f"{ask_prefix} No CD discount set for {dist_name} yet."
            else:
                answer = (
                    f"{ask_prefix} No sales order data found for {dist_name} to calculate CD against."
                )
        else:
            answer = f"{ask_prefix} I couldn't identify the distributor for the CD discount question."
    elif intent == "last_order":
        entity = extract_party_name_candidate(query)
        distributor = _find_distributor_fuzzy(db, entity, workspace_id)
        if distributor:
            dist_id = distributor.get("id")
            dist_name = distributor.get("firm_name") or distributor.get("name") or "This distributor"
            status_rows = db.list_distributor_category_payment_status(user_id)
            dist_row = next((r for r in status_rows if r.get("distributor_id") == dist_id), None)
            latest_season = (dist_row or {}).get("seasons") or []
            latest_season = latest_season[0] if latest_season else None
            cat_bits = (
                [
                    f"{c.get('category')} Rs {indian_number_format(float(c.get('so_total') or 0))}"
                    for c in latest_season.get("categories", [])
                ]
                if latest_season
                else []
            )
            if cat_bits:
                answer = (
                    f"{ask_prefix} {dist_name} last order ({latest_season.get('season')}) — "
                    + ", ".join(cat_bits)
                    + "."
                )
            else:
                answer = f"{ask_prefix} No sales order data found for {dist_name}."
        else:
            answer = f"{ask_prefix} I couldn't identify the distributor for the last order question."
    elif intent == "target":
        # Use the same target_achievement_breakup source as the app's real
        # "Target vs Achievement" card (app/routes/target_achievement.py
        # get_breakup() -> list_target_distributor_breakup()), not the older
        # standalone targets_achievements table.
        #
        # A bare year in the query ("target of 2026") means the Indian
        # fiscal year STARTING that year — "2026" -> FY 2026-2027, "2025"
        # -> FY 2025-2026 — matching how the dashboard labels FYs.
        year_match = re.search(r"\b(20\d{2})\b", query) or re.search(
            r"\b(20\d{2})\b", context_query
        )
        requested_fy = f"{year_match.group(1)}-{int(year_match.group(1)) + 1}" if year_match else None
        entity = re.sub(r"\b20\d{2}\b", "", extract_party_name_candidate(query)).strip().lower()
        if not entity and context_query:
            # Follow-up ("achievement bhi batao" after "bernina ka target
            # 2026 batao") — reuse the distributor named in the prior turn.
            entity = re.sub(
                r"\b20\d{2}\b", "", extract_party_name_candidate(context_query)
            ).strip().lower()

        # Show only the field(s) actually asked about — "target" alone ->
        # target only, "achievement" alone -> achievement only, both (or
        # neither) named -> show both, same as the article MRP/ex-mill fix.
        normalized_target_query = query.lower()
        wants_target_field = "target" in normalized_target_query
        wants_achievement_field = (
            "achiev" in normalized_target_query or "purchase" in normalized_target_query
        )
        if not wants_target_field and not wants_achievement_field:
            wants_target_field = wants_achievement_field = True

        def _target_ach_bits(target_rs: float, achieved_rs: float) -> str:
            bits = []
            if wants_target_field:
                bits.append(f"target Rs {indian_number_format(target_rs)}")
            if wants_achievement_field:
                bits.append(f"achievement Rs {indian_number_format(achieved_rs)}")
            return ", ".join(bits)

        db.ensure_target_achievement_tables()
        with sqlite3.connect(_db_path()) as conn:
            fy_years = conn.execute(
                "SELECT id, financial_year FROM target_achievement_years "
                "WHERE workspace_id = ? ORDER BY financial_year",
                (workspace_id,),
            ).fetchall()
        if requested_fy:
            fy_years = [
                (year_id, fy_label)
                for year_id, fy_label in fy_years
                if normalize_fiscal_year(fy_label) == requested_fy
            ]

        if entity:
            # Distributor named — name + that distributor's target/purchase
            # for the matched fiscal year(s). Resolve through the same
            # typo-tolerant fuzzy matcher every other distributor-scoped
            # intent uses first (handles "sain internattinal" for "Sain
            # International") — a plain substring check on the breakup
            # row's name/nick text has zero typo tolerance and was the
            # only path here before.
            fuzzy_distributor = _find_distributor_fuzzy(db, entity, workspace_id)
            year_parts: list[str] = []
            matched_name = None
            for year_id, fy_label in fy_years:
                breakup = db.list_target_distributor_breakup(workspace_id, year_id)
                for row in breakup:
                    name = str(row.get("distributor_name") or "")
                    nick = str(row.get("nick") or "")
                    row_distributor_id = row.get("distributor_id")
                    id_match = (
                        fuzzy_distributor
                        and row_distributor_id
                        and row_distributor_id == fuzzy_distributor["id"]
                    )
                    if id_match or entity in name.lower() or (nick and entity in nick.lower()):
                        matched_name = matched_name or name
                        target_rs = float(row.get("target_lakhs") or 0) * 100_000
                        achieved_rs = float(row.get("achievement_lakhs") or 0) * 100_000
                        year_parts.append(
                            f"FY{fy_label}: {_target_ach_bits(target_rs, achieved_rs)}"
                        )
                        break
            if year_parts:
                answer = (
                    f"{ask_prefix} {matched_name} — " + "; ".join(year_parts) + "."
                )
            else:
                answer = (
                    f"{ask_prefix} No target/achievement records found for that distributor"
                    + (f" in FY{requested_fy}." if requested_fy else ".")
                )
        else:
            # No distributor named — overall company-wide target for the FY(s),
            # same numbers as the "Target vs Achievement" dashboard card.
            if not fy_years:
                answer = (
                    f"{ask_prefix} No target data found"
                    + (f" for FY{requested_fy}." if requested_fy else ".")
                )
            else:
                fy_parts = []
                for year_id, fy_label in fy_years:
                    meta = db.fy_target_meta(workspace_id, year_id)
                    summary = db.build_fy_achievement_summary(
                        workspace_id, year_id, fy_label, user_id
                    )
                    target_lakhs = float(
                        meta.get("target_lakhs") or summary.get("target_lakhs") or 0
                    )
                    ach_lakhs = float(summary.get("active_achievement") or 0)
                    fy_parts.append(
                        f"FY{fy_label}: "
                        f"{_target_ach_bits(target_lakhs * 100_000, ach_lakhs * 100_000)}"
                    )
                answer = f"{ask_prefix} " + "; ".join(fy_parts) + "."
    elif intent == "category_orders":
        distributor = _resolve_distributor_with_context(
            db, query, context_query, workspace_id
        )
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
        distributor = _resolve_distributor_with_context(
            db, query, context_query, workspace_id
        )
        if distributor:
            firm = distributor.get("firm_name") or distributor["name"]
            # Show only what was asked — "owner ka naam" -> name only,
            # "mobile number" -> phone only, "address"/"pata" -> address
            # only; ask for none specifically and everything comes back,
            # same pattern as the article MRP/ex-mill and target/
            # achievement field filters.
            normalized_owner_query = query.lower()
            # "profile" means the full picture, not just name+phone (the
            # no-field-named default below) — show everything on file.
            wants_profile = "profile" in normalized_owner_query
            wants_name = wants_profile or any(
                t in normalized_owner_query
                for t in ("naam", "name", "owner", "malik", "proprietor")
            )
            wants_phone = wants_profile or any(
                t in normalized_owner_query
                for t in ("mobile", "phone", "contact number", "contact no", "number")
            )
            wants_address = wants_profile or any(
                t in normalized_owner_query for t in ("address", "pata", "location")
            )
            wants_gst = wants_profile or any(
                t in normalized_owner_query for t in ("gst", "gstin")
            )
            if not wants_name and not wants_phone and not wants_address and not wants_gst:
                wants_name = wants_phone = True

            bits = []
            if wants_name:
                bits.append(distributor["name"])
            if wants_phone and distributor.get("phone_number"):
                bits.append(f"phone {distributor['phone_number']}")
            elif wants_phone and not wants_name:
                bits.append("no phone number on file")
            if wants_address:
                addr = (distributor.get("address") or "").strip() or (
                    distributor.get("location") or ""
                ).strip()
                bits.append(f"address {addr}" if addr else "no address on file")
            if wants_gst:
                gst = (distributor.get("gst_no") or "").strip()
                bits.append(f"GST {gst}" if gst else "no GST number on file")

            answer = f"{ask_prefix} {firm} — {', '.join(bits)}."
        else:
            answer = f"{ask_prefix} I couldn't identify that distributor firm."
    elif intent == "season_order_value":
        # "aw26 bed ka total order", "bernina ka aw26 aster order kitna
        # tha", "bernina ka aw26 florentine king bedsheet order" — same
        # underlying data as the "Total value of SO" home-screen card
        # (filled_orders / filled_order_items), just filtered down to a
        # single season/category/distributor/brand/size figure.
        import filled_orders_db as fodb

        normalized_season_query = query.lower()
        season_match = _SEASON_TOKEN_RE.search(normalized_season_query)
        season_prefix = season_match.group(0).upper() if season_match else None
        # A genuine "out of them how many aster order" follow-up names no
        # season/distributor of its own, referring back to whatever the
        # previous question was about — "no season" there means "across
        # every season on file", and the distributor must come from
        # context_query since this query never names one. Any OTHER query
        # with no season code ("bnd ka towel ka order", "aster ka order
        # kitna tha") is a fresh question, not a follow-up — defaults to
        # the most recent season on file instead, same as how PJP defaults
        # to today when no date is given, rather than silently summing
        # the user's entire order history.
        is_context_followup = season_prefix is None and any(
            phrase in normalized_season_query for phrase in _CONTEXT_FOLLOWUP_PHRASES
        )
        season_label = season_prefix

        with sqlite3.connect(_db_path()) as fo_conn:
            fodb.ensure_schema(fo_conn)
            if season_prefix:
                seasons = fodb.list_seasons_matching_prefix(fo_conn, user_id, season_prefix)
            elif is_context_followup:
                seasons = fodb.list_seasons_matching_prefix(fo_conn, user_id, "")
            else:
                last_season = fodb.get_last_season(fo_conn, user_id)
                if last_season:
                    seasons = [last_season]
                    season_label = last_season
                else:
                    seasons = fodb.list_seasons_matching_prefix(fo_conn, user_id, "")

            if not seasons:
                answer = (
                    f"{ask_prefix} I couldn't find any orders for that season."
                )
            else:
                entity_query = (
                    normalized_season_query.replace(season_prefix.lower(), " ")
                    if season_prefix
                    else normalized_season_query
                )
                entity_query = re.sub(r"\b(aw|ss|fw)\d{2}\b", " ", entity_query)

                categories = fodb.list_distinct_categories(fo_conn, user_id, seasons)
                category = _match_category_from_query(entity_query, categories)

                # "towel order" resolves category correctly to "Bath" via
                # the synonym map, but the leftover word "towel" then also
                # fuzzy-matches a real brand named e.g. "Gym Towel" against
                # the UNMODIFIED entity_query below — silently narrowing a
                # plain category question into a (wrong) brand+category
                # one that matches nothing. Strip the category's own words
                # AND whichever synonym word(s) resolved it before matching
                # brand/size, so a word already "spent" on category can't
                # also be claimed by an unrelated field.
                brand_size_query = entity_query
                if category:
                    strip_words = set(category.lower().split()) | {
                        k for k, v in _CATEGORY_WORD_SYNONYMS.items()
                        if v.lower() == category.lower()
                    }
                    strip_pattern = r"\b(" + "|".join(re.escape(w) for w in strip_words) + r")\b"
                    brand_size_query = re.sub(strip_pattern, " ", entity_query, flags=re.IGNORECASE)

                brands = fodb.list_distinct_brands(fo_conn, user_id, seasons)
                brand = _match_token_from_candidates(brand_size_query, brands)

                sizes = fodb.list_distinct_sizes(fo_conn, user_id, seasons)
                size = _match_size_from_query(brand_size_query, sizes)

                # Whatever's left after stripping season/category/brand words is
                # the candidate distributor name, same follow-up-aware fallback
                # as the other distributor-scoped intents. Unlike those,
                # this intent has a valid distributor-less meaning (company-
                # wide total) — so on a season-coded query, no distributor
                # left after stripping filler words means "company-wide",
                # not "look at the previous question's distributor" — no
                # context-query fallback in that case. A context-followup
                # query ("out of them...") is the opposite: it never names
                # its own distributor, so context_query is the only place
                # one can come from.
                residual = entity_query
                for token in (category, brand, size):
                    if token:
                        residual = re.sub(re.escape(token.lower()), " ", residual)
                residual_words = re.findall(r"[\w&.'-]+", residual)
                meaningful_words = [
                    w for w in residual_words if w.lower() not in _PARTY_QUERY_STOPWORDS
                ]
                distributor = (
                    _find_distributor_fuzzy(db, " ".join(meaningful_words), workspace_id)
                    if meaningful_words
                    else None
                )
                if not distributor and is_context_followup and context_query:
                    context_entity = extract_party_name_candidate(context_query)
                    if context_entity:
                        distributor = _find_distributor_fuzzy(
                            db, context_entity, workspace_id
                        )

                totals = fodb.query_order_value(
                    fo_conn,
                    user_id,
                    seasons,
                    category=category,
                    distributor_id=distributor["id"] if distributor else None,
                    brand=brand,
                    size=size,
                )

                desc_bits = [season_label] if season_label else []
                if brand:
                    desc_bits.append(brand)
                if size:
                    desc_bits.append(size)
                elif category:
                    desc_bits.append(category)
                desc = " ".join(desc_bits)
                distributor_label = (
                    (distributor.get("firm_name") or distributor["name"])
                    if distributor
                    else None
                )

                if totals["matched_orders"] == 0:
                    who = distributor_label or "any distributor"
                    answer = f"{ask_prefix} No {desc} order found for {who}."
                else:
                    value_txt = f"Rs {indian_number_format(totals['total_ex_mill_value'])}"
                    qty_txt = f"{indian_number_format(totals['total_piece_qty'])} pcs"
                    if distributor:
                        answer = (
                            f"{ask_prefix} {distributor_label} — {desc} order: "
                            f"{value_txt} ({qty_txt})."
                        )
                    else:
                        answer = (
                            f"{ask_prefix} {desc} total order across all distributors: "
                            f"{value_txt} ({qty_txt})."
                        )
    elif intent == "price_range_articles":
        # "1000 se 2500 ke beech ki bedsheet dikhao", "towel 478 to 2500" —
        # same MRP-band lookup as the home-screen "1000-2000" search bar
        # (_search_articles_by_mrp_range), but reached from a full sentence
        # (not just a bare range) and optionally narrowed to one category.
        price_range = find_price_range_in_query(query)
        if not price_range:
            answer = f"{ask_prefix} I couldn't work out that price range."
        else:
            lo, hi = price_range
            with sqlite3.connect(_db_path()) as am_conn:
                am_conn.row_factory = sqlite3.Row
                category_rows = am_conn.execute(
                    "SELECT DISTINCT category FROM article_master "
                    "WHERE user_id = ? AND is_active = 1 AND category IS NOT NULL",
                    (user_id,),
                ).fetchall()
            categories = [r["category"] for r in category_rows if r["category"]]
            category = _match_token_from_candidates(query.lower(), categories)
            articles = db._search_articles_by_mrp_range(
                lo, hi, user_id, category=category
            )
            search_results = {
                "results": {
                    "distributors": [], "retailers": [], "orders": [], "stock": [],
                    "article_master": articles, "verifications": [],
                    "visit_logs": [], "analytics": [],
                }
            }
            # Force MRP-only formatting (not the MRP+Ex-mill default) —
            # a price-range browse listing shouldn't repeat Ex-mill on
            # every single row; raw_query="mrp" is a formatting signal
            # to _summarize_ask_nexora_search's field-detection, not an
            # actual search term.
            answer = (
                f"{ask_prefix} {_summarize_ask_nexora_search(search_results, raw_query='mrp')}"
            )
    elif intent == "category_size_articles":
        # Bare "single bedsheet" / "double bedsheet" — no brand named — list
        # every matching Article Master SKU across brands instead of the
        # generic party-name search (which finds nothing: both words are
        # search stopwords).
        size_code = find_bare_category_size_in_query(query)
        articles = db._search_articles_by_size(size_code, user_id) if size_code else []
        search_results = {
            "results": {
                "distributors": [], "retailers": [], "orders": [], "stock": [],
                "article_master": articles, "verifications": [],
                "visit_logs": [], "analytics": [],
            }
        }
        answer = (
            f"{ask_prefix} {_summarize_ask_nexora_search(search_results, raw_query=query)}"
        )
    elif intent == "category_size_dimensions":
        # "size of double bedsheet" / "double bedsheet ka size" — the
        # physical cm dimension (Article Master's "BS Size"), which varies
        # per brand, not MRP. Same size_code resolution as the bare
        # category+size MRP listing above.
        size_code = find_bare_category_physical_size_query(query)
        articles = db._search_articles_by_size(size_code, user_id) if size_code else []
        if not articles:
            answer = f"{ask_prefix} No matching article found."
        else:
            _PHYSICAL_SIZE_KEYS = ("bs size", "bedset size (cms)", "bedset size", "size")
            lines = []
            for a in articles[:20]:
                extra = a.get("extra_attributes")
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except (TypeError, ValueError):
                        extra = {}
                extra_lower = {str(k).strip().lower(): v for k, v in (extra or {}).items()}
                dim = next(
                    (extra_lower[k] for k in _PHYSICAL_SIZE_KEYS if extra_lower.get(k) not in (None, "")),
                    None,
                )
                brand = a.get("brand") or "?"
                lines.append(f"{brand} — {dim}" if dim else f"{brand} — size not on file")
            answer = f"{ask_prefix} Size by brand:\n" + "\n".join(lines)
            if len(articles) > 20:
                answer += f"\n…and {len(articles) - 20} more."
    elif intent == "article_margin":
        # "Aster ka retailer margin kitna hai" / "Florentine customer
        # discount" — Article Master's Retailer Margin / Proposed Customer
        # Discount, a booked business figure from extra_attributes, not a
        # value derivable from MRP/PTR.
        field_type = _detect_margin_field(query.lower())
        brand_hint = margin_brand_hint(query)
        if not brand_hint:
            answer = f"{ask_prefix} Which brand's margin? e.g. 'Aster ka retailer margin'."
        else:
            margin_search = db.global_search(brand_hint, workspace_id=workspace_id, user_id=user_id)
            articles = (margin_search.get("results") or {}).get("article_master") or []
            if not articles:
                answer = f"{ask_prefix} No article found for '{brand_hint}'."
            else:
                field_label = "Retailer Margin" if field_type == "retailer" else "Proposed Customer Discount"
                aliases = (
                    ("retailer margin", "retailer md", "retail mark down", "retailer markdown")
                    if field_type == "retailer"
                    else (
                        "proposed customer discount", "perceived", "proposed cust. discount",
                        "proposed cust discount", "perceived margin",
                    )
                )
                lines = []
                for a in articles[:12]:
                    extra = a.get("extra_attributes")
                    if isinstance(extra, str):
                        try:
                            extra = json.loads(extra)
                        except (TypeError, ValueError):
                            extra = {}
                    extra_lower = {str(k).strip().lower(): v for k, v in (extra or {}).items()}
                    raw_val = next(
                        (extra_lower[a2] for a2 in aliases if extra_lower.get(a2) not in (None, "")),
                        None,
                    )
                    pct = amparser.format_percent_display(raw_val) if raw_val is not None else None
                    label = " ".join(x for x in (a.get("brand"), a.get("size")) if x).strip() or "?"
                    lines.append(f"{label} — {field_label}: {pct or 'not set'}")
                extra_count = len(articles) - 12
                answer = f"{ask_prefix} {field_label} for '{brand_hint}':\n" + "\n".join(lines)
                if extra_count > 0:
                    answer += f"\n…and {extra_count} more."
    elif intent == "calculator":
        calc = try_calculator(query.lower())
        if not calc:
            answer = f"{ask_prefix} I couldn't work out that calculation."
        else:
            def _fmt_num(n: float) -> str:
                if n == int(n):
                    return indian_number_format(n)
                sign = "-" if n < 0 else ""
                frac = f"{abs(n):.2f}".split(".")[1].rstrip("0")
                whole_txt = indian_number_format(abs(int(n)))
                return f"{sign}{whole_txt}.{frac}" if frac else f"{sign}{whole_txt}"

            result = calc["result"]
            result_txt = _fmt_num(result)
            if calc["op"] == "percent":
                pct_val, base = calc["operands"]
                answer = (
                    f"{ask_prefix} {_fmt_num(pct_val)}% of {_fmt_num(base)} "
                    f"= {result_txt}"
                )
            else:
                operands, ops = calc["operands"], calc["ops"]
                expr = _fmt_num(operands[0])
                for op, value in zip(ops, operands[1:]):
                    expr += f" {_CALC_OP_LABELS[op]} {_fmt_num(value)}"
                answer = f"{ask_prefix} {expr} = {result_txt}"
    else:
        # Strip filler words so a sentence like "distributor kalra name" or
        # "aster ka mrp aur exmill kya hai" narrows to the actual search
        # term ("kalra" / "aster") — global_search's LIKE-based matching
        # otherwise rarely matches a full natural-language sentence.
        search_query = extract_party_name_candidate(query)
        search_results = db.global_search(
            search_query, workspace_id=workspace_id, user_id=user_id
        )
        results_map = search_results.get("results") or {}
        if results_map.get("article_master"):
            # A product/article hit is a strong, specific signal (the term
            # matched a brand/category directly). Lock the answer to just
            # the article(s) instead of also surfacing unrelated party/order
            # hits that only matched loosely elsewhere (e.g. a retailer's
            # address happening to contain the same substring) — otherwise
            # "aster" pulls in unrelated retailers alongside the real match.
            search_results = {
                **search_results,
                "results": {"article_master": results_map["article_master"]},
            }
        elif results_map.get("distributors"):
            # A distributor match takes priority over retailers/orders that
            # only matched loosely — in most cases a name/number lookup
            # means the distributor, not an unrelated retailer.
            search_results = {
                **search_results,
                "results": {"distributors": results_map["distributors"]},
            }
        elif not any(
            results_map.get(k)
            for k in ("retailers", "orders", "stock", "verifications", "visit_logs", "analytics")
        ):
            # Nothing matched at all — last resort: the same edit-distance
            # distributor fuzzy match the season/last-visit/profile intents
            # already use (handles voice-STT letter swaps like "bermina"/
            # "vernina" for Bernina) that the generic FTS-based
            # global_search above doesn't try for a plain "search" query.
            fuzzy_dist = _find_distributor_fuzzy(db, search_query, workspace_id)
            if fuzzy_dist:
                search_results = {
                    **search_results,
                    "results": {"distributors": [fuzzy_dist]},
                }
        answer = (
            f"{ask_prefix} {_summarize_ask_nexora_search(search_results, raw_query=query)}"
        )

    ai_fallback_used = False
    if _is_unresolved_answer(answer):
        # Rule-based engine admits it couldn't answer — hand off to the
        # Gemini tool-calling Order Desk agent before giving up, so a real
        # CI/SO reconciliation question gets a real answer instead of an
        # apology. Only for the BD workspace — the agent's tools are
        # BD-specific (filled_orders / fo_so_match_runs).
        if user_id is not None and workspace_id != "house_of_prizm":
            try:
                from app.services.nexora_ai_agent import NexoraAiAgentError, ask_order_desk

                ai_answer = ask_order_desk(query, user_id=user_id, workspace_id=workspace_id)
                answer = f"{ask_prefix} {ai_answer}"
                intent = "ai_order_desk"
                ai_fallback_used = True
            except NexoraAiAgentError:
                pass  # AI unavailable (no key / quota / all models down) — fall through to the apology.
            except Exception:  # noqa: BLE001 - never let the AI agent 500 this endpoint
                pass
        if not ai_fallback_used:
            log_unresolved_query(workspace_id, user_id, query)
            answer = (
                f"{ask_prefix} I couldn't find an answer to that — please try "
                f"asking something else, or rephrase your question."
            )
            still_unresolved = True
        else:
            still_unresolved = False
    else:
        still_unresolved = False

    if not still_unresolved:
        # This exact question may have failed before (and been logged for
        # troubleshooting) but now resolves — e.g. after a keyword/entity fix
        # was taught, or the Gemini fallback answered it. Clear the stale
        # entry automatically. Skipped when it's still unresolved — that
        # branch just logged this exact failure a moment ago above.
        resolve_unresolved_query(workspace_id, query)

    return Response(
        json.dumps(
            {"intent": intent, "query": query, "answer": answer.strip()}, ensure_ascii=False
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
