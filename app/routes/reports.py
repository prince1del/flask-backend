import csv
import io
import json
from io import StringIO

from flask import Blueprint, Response, request, render_template_string

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth
from app.utils import get_monthly_report_data

reports_blueprint = Blueprint("reports", __name__)

REPORTS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Monthly Reports</title>
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


@reports_blueprint.route("/reports")
@require_jwt_auth
def monthly_reports() -> str:
    selected_month = request.args.get("month") or "2026-06"
    data = get_monthly_report_data("centralized_db.sqlite3", selected_month)
    return render_template_string(
        REPORTS_TEMPLATE, selected_month=selected_month, **data
    )


@reports_blueprint.route("/reports/download/excel")
@require_jwt_auth
def download_monthly_report_excel() -> Response:
    selected_month = request.args.get("month") or "2026-06"
    data = get_monthly_report_data("centralized_db.sqlite3", selected_month)
    import pandas as pd

    df = pd.DataFrame(data["distributor_activity"])
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "distributor_name",
                "total_uploads",
                "stage1",
                "stage2",
                "stage3",
                "stage4",
                "last_upload",
            ]
        )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Monthly Report")
    output.seek(0)
    return Response(
        output.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=monthly_report_{selected_month}.xlsx"
        },
    )


@reports_blueprint.route("/reports/download/csv")
@require_jwt_auth
def download_monthly_report_csv() -> Response:
    selected_month = request.args.get("month") or "2026-06"
    data = get_monthly_report_data("centralized_db.sqlite3", selected_month)
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "distributor_name",
            "total_uploads",
            "stage1",
            "stage2",
            "stage3",
            "stage4",
            "last_upload",
        ],
    )
    writer.writeheader()
    writer.writerows(data["distributor_activity"])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=monthly_report_{selected_month}.csv"
        },
    )


@reports_blueprint.route("/download/analytics")
@require_jwt_auth
def download_analytics() -> Response:
    db = CentralizedDB("centralized_db.sqlite3")
    payload = db.get_dashboard_payload()
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=analytics.json"},
    )


@reports_blueprint.route("/download/report")
@require_jwt_auth
def download_report() -> Response:
    report_text = request.args.get("report", "")
    return Response(
        report_text,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=verification_report.txt"},
    )


@reports_blueprint.route("/download/distributors")
@require_jwt_auth
def download_distributors() -> Response:
    csv_data = CentralizedDB("centralized_db.sqlite3").export_master_distributors()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=distributors.csv"},
    )


@reports_blueprint.route("/download/distributors/excel")
@require_jwt_auth
def download_distributors_excel() -> Response:
    excel_bytes = CentralizedDB(
        "centralized_db.sqlite3"
    ).export_master_distributors_excel()
    return Response(
        excel_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=distributors.xlsx"},
    )


@reports_blueprint.route("/download/distributors/pdf")
@require_jwt_auth
def download_distributors_pdf() -> Response:
    pdf_bytes = CentralizedDB("centralized_db.sqlite3").export_master_distributors_pdf()
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=distributors.pdf"},
    )


@reports_blueprint.route("/download/retailers")
@require_jwt_auth
def download_retailers() -> Response:
    csv_data = CentralizedDB("centralized_db.sqlite3").export_master_retailers()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=retailers.csv"},
    )


@reports_blueprint.route("/download/retailers/excel")
@require_jwt_auth
def download_retailers_excel() -> Response:
    excel_bytes = CentralizedDB(
        "centralized_db.sqlite3"
    ).export_master_retailers_excel()
    return Response(
        excel_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=retailers.xlsx"},
    )


@reports_blueprint.route("/download/targets")
@require_jwt_auth
def download_targets() -> Response:
    csv_data = CentralizedDB("centralized_db.sqlite3").export_targets_achievements()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=targets_achievements.csv"
        },
    )


@reports_blueprint.route("/download/primary-sales")
@require_jwt_auth
def download_primary_sales() -> Response:
    csv_data = CentralizedDB("centralized_db.sqlite3").export_primary_sales()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=primary_sales.csv"},
    )


@reports_blueprint.route("/download/secondary-sales")
def download_secondary_sales() -> Response:
    csv_data = CentralizedDB("centralized_db.sqlite3").export_secondary_sales()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=secondary_sales.csv"},
    )


@reports_blueprint.route("/download/dsr")
def download_dsr() -> Response:
    report_id = request.args.get("report_id", type=int)
    if report_id is None:
        return Response("Missing report_id", status=400)
    excel_bytes = CentralizedDB("centralized_db.sqlite3").export_dsr_report(
        report_id, export_format="excel"
    )
    return Response(
        excel_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=dsr_report_{report_id}.xlsx"
        },
    )
