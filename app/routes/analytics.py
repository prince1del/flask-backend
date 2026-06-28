import json

from flask import Blueprint, render_template_string

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth

analytics_blueprint = Blueprint("analytics", __name__)

ANALYTICS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Analytics Dashboard</title>
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


@analytics_blueprint.route("/analytics")
@require_jwt_auth
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
