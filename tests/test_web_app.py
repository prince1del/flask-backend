import io
import os
import uuid

import pandas as pd

import app.routes.auth as app_auth
from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


def _build_test_pdf(content: str) -> bytes:
    escaped_content = content.replace("(", "\\(").replace(")", "\\)")
    stream_bytes = f"BT /F1 12 Tf 72 720 Td ({escaped_content}) Tj ET".encode("utf-8")
    pdf = (
        b"%PDF-1.4\n"
        + b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        + b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        + b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        + b"4 0 obj<</Length "
        + str(len(stream_bytes)).encode("utf-8")
        + b">>stream\n"
        + stream_bytes
        + b"\nendstream\nendobj\n"
        + b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        + b"xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n0000000111 00000 n \n0000000219 00000 n \n0000000298 00000 n \n"
        + b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n348\n%%EOF"
    )
    return pdf


def test_upload_page_runs_three_step_verification():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    response = client.post(
        "/",
        data={
            "order_file": (order_buffer, "order.xlsx"),
            "filled_file": (filled_buffer, "filled.xlsx"),
            "sales_order_file": (
                io.BytesIO(
                    b"Product: Milk Powder\nQuantity: 12\nRate: 100\nGST: 5\nClient Name: Rahul Kumar Yadav\nInvoice Amount: 1000\nTotal GST: 5"
                ),
                "sales_order.pdf",
            ),
            "invoice_file": (
                io.BytesIO(
                    b"Product: Milk Powder\nQuantity: 12\nRate: 100\nGST: 5\nClient Name: Rahul K Yadav\nInvoice Amount: 1000\nTotal GST: 5"
                ),
                "invoice.pdf",
            ),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Order-to-Invoice Workflow" in html
    assert "Stage 1 - Common order sheet" in html
    assert (
        "distributor-wise filled Excel" in html
        or "Distributor-wise filled order" in html
    )
    assert "Stage 4 - Commercial invoice" in html
    assert "Save Stage 1" in html
    assert "Check Stage 2" in html
    assert "Check Stage 3" in html
    assert "Check Stage 4" in html
    assert "Run Full Verification" in html
    assert "step1" in html
    assert "step2" in html
    assert "step3" in html


def test_partial_uploads_show_clear_verification_message():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    response = client.post(
        "/",
        data={
            "order_file": (order_buffer, "order.xlsx"),
            "filled_file": (filled_buffer, "filled.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Partial verification" in html


def test_upload_page_shows_error_for_unreadable_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/",
        data={
            "order_file": (io.BytesIO(b"not an excel file"), "order.xlsx"),
            "filled_file": (io.BytesIO(b"not an excel file"), "filled.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "error" in html.lower()


def test_parse_sales_order_header_fields_extracts_client_name_and_buyer_code():
    from app.routes.data import _parse_sales_order_header_fields

    text = (
        "Product: Milk Powder\n"
        "Quantity: 10\n"
        "Rate: 100\n"
        "GST: 5\n"
        "Client Name: Rahul Kumar Yadav\n"
        "Buyer Code: BC123\n"
        "Invoice Amount: 1000"
    )

    parsed = _parse_sales_order_header_fields(text)

    assert parsed["buyer_name"] == "Rahul Kumar Yadav"
    assert parsed["buyer_code"] == "BC123"


def test_order_upload_rejects_pdf_file_type():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/",
        data={
            "order_file": (io.BytesIO(b"%PDF-1.4\n%test pdf"), "order.pdf"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "must be uploaded in the expected file format" in html


def test_stage2_requires_filled_and_order_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    response = client.post(
        "/",
        data={
            "order_file": (order_buffer, "order.xlsx"),
            "workflow_action": "stage2",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Stage 2 requires both order_file and filled_file" in html


def test_uploads_show_recognized_file_types_and_stages():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    sales_order_pdf = io.BytesIO(
        b"Product: Milk Powder\nQuantity: 10\nRate: 100\nGST: 5"
    )

    response = client.post(
        "/",
        data={
            "filled_file": (filled_buffer, "filled.xlsx"),
            "sales_order_file": (sales_order_pdf, "sales_order.pdf"),
            "workflow_action": "run_all",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Recognized Uploads" in html
    assert "Stage 1 - Common order sheet" in html
    assert "excel" in html.lower()
    assert "pdf" in html.lower()


def test_run_all_persists_distributor_wise_upload_records(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = str(tmp_path / "web.sqlite3")
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    sales_order_pdf = io.BytesIO(
        b"Product: Milk Powder\nQuantity: 10\nRate: 100\nGST: 5"
    )
    invoice_pdf = io.BytesIO(b"Product: Milk Powder\nQuantity: 10\nRate: 100\nGST: 5")

    response = client.post(
        "/",
        data={
            "distributor_name": "Alpha Traders",
            "order_file": (order_buffer, "order.xlsx"),
            "filled_file": (filled_buffer, "alpha_traders_filled.xlsx"),
            "sales_order_file": (sales_order_pdf, "sales_order.pdf"),
            "invoice_file": (invoice_pdf, "invoice.pdf"),
            "workflow_action": "run_all",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200

    db = CentralizedDB(app.config["DATABASE_PATH"])
    persisted = db.list_distributor_order_uploads(distributor_name="Alpha Traders")
    assert len(persisted) >= 4
    stages = {item["stage_key"] for item in persisted[:4]}
    assert {"order_file", "filled_file", "sales_order_file", "invoice_file"}.issubset(
        stages
    )


def test_sales_order_upload_with_selected_distributor_creates_order_lifecycle_link(tmp_path):
    app_auth.auth_enabled = lambda: False
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = str(tmp_path / "web_so_link.sqlite3")
    client = app.test_client()

    db = CentralizedDB(app.config["DATABASE_PATH"])
    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        buyer_code="BC123",
    )

    sales_order_pdf = io.BytesIO(
        _build_test_pdf(
            "Buyer Code: BC123\nOrder Ref No: SO-12345\nClient Name: Rahul Kumar\nInvoice Amount: 1000"
        )
    )

    response = client.post(
        "/",
        data={
            "sales_order_distributor_id": str(distributor_id),
            "sales_order_file": (sales_order_pdf, "sales_order.pdf"),
            "workflow_action": "stage3",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    linked = db.get_order_lifecycle_by_order_ref_no("SO-12345", workspace_id="default")
    assert linked is not None
    assert linked["distributor_id"] == distributor_id
    assert linked["sales_order_file_reference"] is not None
    assert linked["sales_order_parsed"] is not None


def test_sales_order_upload_with_buyer_code_match_does_not_auto_link_without_selection(tmp_path):
    app_auth.auth_enabled = lambda: False
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = str(tmp_path / "web_so_auto_no_link.sqlite3")
    client = app.test_client()

    db = CentralizedDB(app.config["DATABASE_PATH"])
    _ = db.add_master_distributor(
        name="Beta Traders",
        buyer_code="BC999",
    )

    sales_order_pdf = io.BytesIO(
        _build_test_pdf(
            "Buyer Code: BC999\nOrder Ref No: SO-99999\nClient Name: Rahul Kumar\nInvoice Amount: 1000"
        )
    )

    response = client.post(
        "/",
        data={
            "sales_order_file": (sales_order_pdf, "sales_order.pdf"),
            "workflow_action": "stage3",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    linked = db.get_order_lifecycle_by_order_ref_no("SO-99999", workspace_id="default")
    assert linked is None


def test_single_uploads_are_accumulated_across_requests():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    first_response = client.post(
        "/",
        data={"order_file": (order_buffer, "order.xlsx")},
        content_type="multipart/form-data",
    )
    assert first_response.status_code == 200

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    second_response = client.post(
        "/",
        data={"filled_file": (filled_buffer, "filled.xlsx")},
        content_type="multipart/form-data",
    )
    assert second_response.status_code == 200
    second_html = second_response.get_data(as_text=True)
    assert "order_file" in second_html
    assert "filled_file" in second_html
    assert '"status": "partial-verification"' in second_html
    assert '"status": "error"' not in second_html


def test_reuploading_order_sheet_clears_stale_session_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_buffer, index=False)
    filled_buffer.seek(0)

    first_response = client.post(
        "/",
        data={
            "order_file": (order_buffer, "order.xlsx"),
            "filled_file": (filled_buffer, "filled.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert first_response.status_code == 200

    fresh_order = io.BytesIO()
    pd.DataFrame(
        [
            {"product": "Aster", "quantity": 18, "rate": 12, "gst": 6, "discount": 750},
        ]
    ).to_excel(fresh_order, index=False)
    fresh_order.seek(0)

    second_response = client.post(
        "/",
        data={"order_file": (fresh_order, "order.xlsx")},
        content_type="multipart/form-data",
    )

    assert second_response.status_code == 200
    with client.session_transaction() as sess:
        stored_files = sess.get("verification_files", {})

    assert set(stored_files.keys()) == {"order_file"}


def test_scheduler_page_renders_morning_suggestions():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/scheduler")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Morning Suggestions" in html
    assert "Weekly PJP Planner" in html


def test_verification_progress_shows_step_by_step_capture():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame([{"product": "Milk Powder", "quantity": 10, "rate": 100}]).to_excel(
        order_buffer, index=False
    )
    order_buffer.seek(0)

    response = client.post(
        "/",
        data={"order_file": (order_buffer, "order.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Captured files" in html
    assert "order_file" in html
    assert "Next step" in html


def test_step1_inferred_mapping_is_shown_on_verification_page():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            ["Aster", "100 (One in a dent)", 1728, 1049, 5, 2],
            ["Bluemen", "104 (One in a dent)", 288, 799, 5, None],
        ],
        dtype=object,
    ).to_excel(order_buffer, header=False, index=False)
    order_buffer.seek(0)

    filled_buffer = io.BytesIO()
    pd.DataFrame(
        [
            ["Aster", "100 (One in a dent)", 1728, 1049, 5, 2],
            ["Bluemen", "104 (One in a dent)", 288, 799, 5, None],
        ],
        dtype=object,
    ).to_excel(filled_buffer, header=False, index=False)
    filled_buffer.seek(0)

    response = client.post(
        "/",
        data={
            "order_file": (order_buffer, "order.xlsx"),
            "filled_file": (filled_buffer, "filled.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Step 1 Inferred Mapping" in html
    assert "original excel" in html.lower()
    assert "quantity" in html.lower()
    assert "rate" in html.lower()


def test_database_admin_page_supports_backup_and_audit_log_view():
    # Covered with auth + CSRF in tests/test_admin_database_path_safety.py
    from pathlib import Path

    html = Path("app/routes/workspaces.py").read_text(encoding="utf-8")
    assert "csrf_token" in html
    assert "resolve_under_allowlist" in html
    assert "instance/backups" in html


def test_pwa_dashboard_and_manifest_assets_are_served():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/pwa-dashboard")
    assert response.status_code == 200

    manifest_response = client.get("/manifest.json")
    assert manifest_response.status_code == 200
    manifest = manifest_response.get_json()
    assert manifest["icons"][0]["src"] == "/icon-192.svg"
    assert manifest["icons"][1]["src"] == "/icon-512.svg"

    worker_response = client.get("/service-worker.js")
    assert worker_response.status_code == 200


def test_dashboard_summary_api_returns_live_backend_metrics():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["overview"]["distributors"] >= 0
    assert payload["overview"]["retailers"] >= 0
    assert payload["overview"]["alerts"] >= 0
    assert payload["overview"]["tasks"] >= 0


def test_dashboard_config_api_returns_branding_details(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["DASHBOARD_CONFIG_PATH"] = str(tmp_path / "dashboard_config.json")
    client = app.test_client()

    response = client.get("/api/ui/dashboard-config")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["brand_name"] == "NEXORA"
    assert payload["app_name"] == "NEXORA ENTERPRISE"
    assert payload["dashboard_title"] == "Ask Nexora"
    assert payload["short_name"] == "Ask Nexora"
    assert "dashboard_summary" in payload["api_endpoints"]
    assert "/manifest.json" == payload["api_endpoints"]["manifest"]


def test_dashboard_config_api_put_updates_runtime_config(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["DASHBOARD_CONFIG_PATH"] = str(tmp_path / "dashboard_config.json")
    client = app.test_client()

    before = client.get("/api/ui/dashboard-config")
    assert before.status_code == 200
    config_before = before.get_json()
    assert "file_library" in config_before["enabled_modules"]
    assert "party_match" in config_before["enabled_modules"]
    assert config_before["app_name"] == "NEXORA ENTERPRISE"

    response = client.put(
        "/api/ui/dashboard-config",
        json={
            "enabled_modules": ["dashboard", "verification", "analytics"],
            "app_name": "Ask Nexora Updated",
        },
    )
    assert response.status_code == 200
    config_after = response.get_json()
    assert "file_library" not in config_after["enabled_modules"]
    assert "party_match" not in config_after["enabled_modules"]
    assert config_after["app_name"] == "Ask Nexora Updated"

    manifest_response = client.get("/manifest.json")
    assert manifest_response.status_code == 200
    manifest = manifest_response.get_json()
    assert manifest["name"] == "Ask Nexora Updated"


def test_bulk_upload_endpoint_serves_upload_form_on_get():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/v1/masters/bulk-upload")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "multipart/form-data" in html
    assert 'name="file"' in html
    assert "master_type" in html
    assert "/api/v1/masters/template/distributors" in html
    assert "/api/v1/masters/template/distributors?format=csv" in html
    assert "/api/v1/masters/template/retailers?format=csv" in html
    assert "/api/v1/masters/template/articles?format=csv" in html


def test_master_template_download_returns_distributor_excel_template():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/v1/masters/template/distributors")

    assert response.status_code == 200
    assert "attachment; filename=distributors_template.xlsx" in response.headers.get(
        "Content-Disposition", ""
    )
    template_df = pd.read_excel(io.BytesIO(response.data))
    required_columns = {
        "Distributor Code",
        "Firm Name",
        "Firm nick name",
        "Distributor Name",
        "Location",
        "Address",
        "Pincode",
        "Distribution State",
        "Distribution Area",
        "Payment Terms",
        "Birthday",
        "Anniversary",
        "Zone",
        "Region",
        "Credit Limit",
        "Opening Balance",
    }
    actual_columns = set(template_df.columns)
    assert required_columns.issubset(actual_columns)
    assert "GST Number" in actual_columns or "GSTIN" in actual_columns
    assert "Mobile Number" in actual_columns or "Phone" in actual_columns
    assert "Email id" in actual_columns or "Email" in actual_columns


def test_master_template_download_rejects_invalid_format():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/v1/masters/template/distributors?format=pdf")

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"


def test_bulk_upload_endpoint_accepts_pdf_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/v1/masters/bulk-upload",
        data={
            "file": (io.BytesIO(b"%PDF-1.4\n%test pdf"), "sample.pdf"),
            "master_type": "distributors",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["file_type"] == "pdf"
    assert payload["parsed_data"]["file_type"] == "pdf"


def test_bulk_upload_endpoint_persists_distributor_info_from_pdf():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    distributor_name = f"PDF Distributor {uuid.uuid4().hex[:8]}"
    pdf_content = (
        "Firm Name: Alpha Group\n"
        "Firm nick name: AG\n"
        f"Distributor Name: {distributor_name}\n"
        "GSTIN: 27ABCDE1234F1Z5\n"
        "Zone: West\n"
        "Region: Mumbai\n"
        "Credit Limit: 1000\n"
        "Phone: 9988776655\n"
        "Email: distributor@example.com\n"
        "Address: Corner Market\n"
    ).encode("utf-8")

    response = client.post(
        "/api/v1/masters/bulk-upload",
        data={
            "file": (io.BytesIO(pdf_content), "sample.pdf"),
            "master_type": "distributors",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["inserted"] >= 1

    stored = CentralizedDB("centralized_db.sqlite3").get_master_distributor_by_name(
        distributor_name
    )
    assert stored is not None
    assert stored["firm_name"] == "Alpha Group"
    assert stored["firm_nick_name"] == "AG"
    assert stored["gst_no"] == "27ABCDE1234F1Z5"
    assert stored["zone"] == "West"
    assert stored["region"] == "Mumbai"
    assert stored["credit_limit"] == 1000.0
    assert stored["phone_number"] == "9988776655"
    assert stored["email"] == "distributor@example.com"
    assert stored["address"] == "Corner Market"


def test_analytics_page_renders_distributor_snapshot_headers():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/analytics")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Distributor Snapshot" in html
    assert "Distributor Code" in html
    assert "Firm nick name" in html


def test_bulk_upload_endpoint_persists_retailer_info_from_pdf():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    retailer_name = f"PDF Retailer {uuid.uuid4().hex[:8]}"
    linked_distributor = "Linked Distributor"
    pdf_content = (
        f"Retailer Name: {retailer_name}\n"
        f"Distributor: {linked_distributor}\n"
        "Location: Mumbai\n"
        "Phone: 9876543210\n"
        "Email: retailer@example.com\n"
        "Address: Market Road\n"
        "GSTIN: 27ABCDE1234F1Z5\n"
    ).encode("utf-8")

    response = client.post(
        "/api/v1/masters/bulk-upload",
        data={
            "file": (io.BytesIO(pdf_content), "sample.pdf"),
            "master_type": "retailers",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["inserted"] >= 1

    stored = CentralizedDB("centralized_db.sqlite3").get_master_retailer_by_name(
        retailer_name
    )
    assert stored is not None
    assert stored["name"] == retailer_name
    assert stored["location"] == "Mumbai"
    assert stored["phone_number"] == "9876543210"
    assert stored["email"] == "retailer@example.com"
    assert stored["address"] == "Market Road"
    assert stored["gst_no"] == "27ABCDE1234F1Z5"


def test_bulk_upload_endpoint_accepts_excel_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    order_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {"distributor": "Alpha", "retailer": "Beta", "city": "Delhi"},
        ]
    ).to_excel(order_buffer, index=False)
    order_buffer.seek(0)

    response = client.post(
        "/api/v1/masters/bulk-upload",
        data={"file": (order_buffer, "masters.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["rows"] == 1


def test_bulk_upload_endpoint_persists_distributors_to_database():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    distributor_name = f"Bulk Distributor {uuid.uuid4().hex[:8]}"
    distributor_gst = f"27{uuid.uuid4().hex[:13].upper()}"
    buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Distributor Name": distributor_name,
                "GSTIN": distributor_gst,
                "Zone": "West",
                "Region": "Mumbai",
                "Credit Limit": 1000,
            },
        ]
    ).to_excel(buffer, index=False)
    buffer.seek(0)

    response = client.post(
        "/api/v1/masters/bulk-upload",
        data={"file": (buffer, "distributors.xlsx"), "master_type": "distributors"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["inserted"] >= 1

    stored = CentralizedDB("centralized_db.sqlite3").get_master_distributor_by_name(
        distributor_name
    )
    assert stored is not None
    assert stored["name"] == distributor_name


def test_contacts_import_export_page_shows_blank_download_and_upload_options():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/v1/contacts/import-export")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/api/v1/masters/template/distributors" in html
    assert "/api/v1/masters/template/distributors?format=csv" in html
    assert "/api/v1/masters/template/retailers" in html
    assert "/api/v1/masters/template/retailers?format=csv" in html
    assert "/download/distributors/excel" in html
    assert "/download/distributors" in html
    assert "/download/retailers/excel" in html
    assert "/download/retailers" in html
    assert 'name="master_type"' in html
    assert '<option value="retailers">Retailers</option>' in html
    assert 'accept=".csv,.xlsx,.xls,.xlsm,.xlsb"' in html


def test_contacts_import_rejects_pdf_files():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/v1/contacts/import",
        data={"file": (io.BytesIO(b"%PDF-1.4\n%test pdf"), "contacts.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "CSV or Excel" in payload["message"]


def test_contacts_import_updates_existing_distributor_from_excel():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    distributor_name = f"Contact Update {uuid.uuid4().hex[:8]}"
    distributor_gst = f"27{uuid.uuid4().hex[:13].upper()}"

    seed_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Distributor Name": distributor_name,
                "GSTIN": distributor_gst,
                "Mobile Number": "9000000000",
                "Address": "Old Address",
            }
        ]
    ).to_excel(seed_buffer, index=False)
    seed_buffer.seek(0)

    first_response = client.post(
        "/api/v1/contacts/import",
        data={
            "master_type": "distributors",
            "file": (seed_buffer, "contacts_seed.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert first_response.status_code == 200

    update_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Distributor Name": distributor_name,
                "GSTIN": distributor_gst,
                "Mobile Number": "9111111111",
                "Address": "New Address",
                "Firm Name": "Updated Firm",
                "Firm nick name": "UF",
            }
        ]
    ).to_excel(update_buffer, index=False)
    update_buffer.seek(0)

    update_response = client.post(
        "/api/v1/contacts/import",
        data={
            "master_type": "distributors",
            "file": (update_buffer, "contacts_update.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert update_response.status_code == 200
    payload = update_response.get_json()
    assert payload["status"] == "success"
    assert payload["updated"] >= 1

    stored = CentralizedDB("centralized_db.sqlite3").get_master_distributor_by_name(
        distributor_name
    )
    assert stored is not None
    assert stored["phone_number"] == "9111111111"
    assert stored["address"] == "New Address"
    assert stored["firm_name"] == "Updated Firm"
    assert stored["firm_nick_name"] == "UF"


def test_contacts_import_updates_existing_retailer_from_excel():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    distributor_name = f"Retailer Dist {uuid.uuid4().hex[:8]}"
    distributor_gst = f"27{uuid.uuid4().hex[:13].upper()}"
    retailer_name = f"Retailer Contact {uuid.uuid4().hex[:8]}"

    distributor_seed = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Distributor Name": distributor_name,
                "GSTIN": distributor_gst,
            }
        ]
    ).to_excel(distributor_seed, index=False)
    distributor_seed.seek(0)
    dist_response = client.post(
        "/api/v1/contacts/import",
        data={
            "master_type": "distributors",
            "file": (distributor_seed, "dist_seed.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert dist_response.status_code == 200

    retailer_seed = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Retailer Name": retailer_name,
                "Distributor": distributor_name,
                "Location": "Old Location",
                "Phone": "9000000001",
                "Email": "old@example.com",
                "Address": "Old Retailer Address",
                "GSTIN": "27ABCDE1234F1Z5",
            }
        ]
    ).to_excel(retailer_seed, index=False)
    retailer_seed.seek(0)
    retailer_first = client.post(
        "/api/v1/contacts/import",
        data={
            "master_type": "retailers",
            "file": (retailer_seed, "retailer_seed.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert retailer_first.status_code == 200

    retailer_update = io.BytesIO()
    pd.DataFrame(
        [
            {
                "Retailer Name": retailer_name,
                "Distributor": distributor_name,
                "Location": "New Location",
                "Phone": "9111111112",
                "Email": "new@example.com",
                "Address": "New Retailer Address",
                "GSTIN": "27ZZZZZ1234Z1Z5",
            }
        ]
    ).to_excel(retailer_update, index=False)
    retailer_update.seek(0)
    retailer_second = client.post(
        "/api/v1/contacts/import",
        data={
            "master_type": "retailers",
            "file": (retailer_update, "retailer_update.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert retailer_second.status_code == 200
    payload = retailer_second.get_json()
    assert payload["status"] == "success"
    assert payload["master_type"] == "retailers"
    assert payload["updated"] >= 1

    stored = CentralizedDB("centralized_db.sqlite3").get_master_retailer_by_name(
        retailer_name
    )
    assert stored is not None
    assert stored["location"] == "New Location"
    assert stored["phone_number"] == "9111111112"
    assert stored["email"] == "new@example.com"
    assert stored["address"] == "New Retailer Address"
    assert stored["gst_no"] == "27ZZZZZ1234Z1Z5"


def test_bale_calculator_endpoint_returns_json():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/bale-calculator",
        data={
            "total_bales": 2,
            "packs_per_bale": 3,
            "pcs_per_pack": 10,
            "number_of_designs": 1,
            "number_of_colors": 1,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_packs"] == 6
    assert payload["total_pieces"] == 60
