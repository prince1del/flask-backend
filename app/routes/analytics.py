from flask import Blueprint, render_template_string, current_app

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth, get_workspace_id

analytics_blueprint = Blueprint("analytics", __name__)

ANALYTICS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA | Business Intelligence Brain</title>
  <style>
    body {
      margin: 0;
      background: #0f0f0f;
      color: #ffffff;
      font-family: Inter, 'Segoe UI', sans-serif;
    }
    .analytics-shell {
      padding: 28px;
      background:
        radial-gradient(circle at top left, rgba(59, 130, 246, 0.18), transparent 25%),
        radial-gradient(circle at bottom right, rgba(16, 185, 129, 0.16), transparent 20%),
        #0f0f0f;
    }
    .analytics-topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }
    .analytics-topbar h1 {
      margin: 0;
      font-size: 2.2rem;
      letter-spacing: -0.04em;
    }
    .analytics-actions {
      display: flex;
      gap: 12px;
    }
    .btn {
      border: none;
      padding: 10px 16px;
      border-radius: 12px;
      cursor: pointer;
      font-weight: 600;
    }
    .btn-primary {
      background: linear-gradient(135deg, #3b82f6, #60a5fa);
      color: #07111f;
    }
    .btn-secondary {
      background: #1a1a1a;
      border: 1px solid #353535;
      color: #ffffff;
    }
    .panel {
      background: linear-gradient(180deg, rgba(26, 26, 26, 0.96), rgba(18, 18, 18, 0.92));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 24px;
      padding: 24px;
      box-shadow: 0 20px 50px rgba(0,0,0,0.25);
    }
    .ai-greeting {
      margin-bottom: 20px;
    }
    .ai-greeting p {
      margin: 0;
      font-size: 1.1rem;
      line-height: 1.6;
    }
    .insight-bullets {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 18px 0 0;
      padding: 0;
      list-style: none;
    }
    .insight-bullets li {
      padding: 10px 12px;
      background: rgba(255,255,255,0.04);
      border-radius: 14px;
      font-size: 0.96rem;
    }
    .insight-bullets li strong {
      color: #60a5fa;
    }
    .ai-actions {
      display: flex;
      gap: 12px;
      margin-top: 16px;
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 14px;
      margin: 20px 0;
    }
    .kpi-card {
      padding: 18px;
      min-height: 122px;
    }
    .kpi-label {
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      color: #9ca3af;
    }
    .kpi-value {
      font-size: 2rem;
      font-weight: 800;
      margin: 8px 0;
    }
    .kpi-change {
      font-size: 0.78rem;
      color: #10b981;
    }
    .kpi-change.down { color: #ef4444; }
    .layout-grid {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 18px;
      margin-top: 18px;
    }
    .health-card .score {
      font-size: 4rem;
      font-weight: 800;
      line-height: 1;
    }
    .health-card .score small { font-size: 1.6rem; color: #9ca3af; }
    .health-bar {
      height: 12px;
      background: #2a2a2a;
      border-radius: 999px;
      margin: 14px 0;
      overflow: hidden;
    }
    .health-bar span {
      display: block;
      height: 100%;
      width: 92%;
      background: linear-gradient(135deg, #10b981, #34d399);
    }
    .health-list {
      list-style: none;
      padding: 0;
      margin: 16px 0 0;
    }
    .health-list li {
      display: flex;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-size: 0.92rem;
    }
    .growth-card {
      min-height: 320px;
    }
    .chart-grid {
      display: flex;
      align-items: end;
      gap: 10px;
      height: 210px;
      margin-top: 16px;
    }
    .chart-bar {
      flex: 1;
      border-radius: 12px 12px 0 0;
      background: linear-gradient(180deg, #60a5fa, #3b82f6);
      opacity: 0.7;
    }
    .chart-bar:nth-child(1){ height: 55%; }
    .chart-bar:nth-child(2){ height: 68%; }
    .chart-bar:nth-child(3){ height: 82%; }
    .chart-bar:nth-child(4){ height: 76%; }
    .chart-bar:nth-child(5){ height: 88%; }
    .chart-bar:nth-child(6){ height: 92%; }
    .chart-bar:nth-child(7){ height: 74%; }
    .chart-bar:nth-child(8){ height: 65%; }
    .chart-bar:nth-child(9){ height: 82%; }
    .chart-bar:nth-child(10){ height: 94%; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }
    .summary-card {
      padding: 18px;
    }
    .summary-card small {
      display: block;
      color: #9ca3af;
      margin-bottom: 10px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.58rem;
    }
    .summary-card ul {
      padding-left: 18px;
      margin: 0;
    }
    .summary-card li {
      margin: 8px 0;
    }
    @media (max-width: 1024px) {
      .kpi-grid, .summary-grid, .layout-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="analytics-shell">
    <div class="analytics-topbar">
      <h1>Business Intelligence Brain</h1>
      <div class="analytics-actions">
        <button class="btn btn-secondary" onclick="window.location.href='/'" aria-label="Back to app dashboard">← Back to App</button>
        <button class="btn btn-primary" onclick="window.location.href='/analytics'">Refresh</button>
      </div>
    </div>

    <section class="panel ai-greeting">
      <p><strong>Good Morning, Kunwar.</strong></p>
      <p><strong>Today's Business Summary:</strong></p>
      <ul class="insight-bullets">
        <li><strong>Sales:</strong> ₹18.2L (↑8% vs yesterday)</li>
        <li><strong>Collections:</strong> below target by ₹2.1L</li>
        <li><strong>GST:</strong> due in 2 days (₹2.3L to file)</li>
        <li><strong>Punjab:</strong> sales down 8% — needs action</li>
        <li><strong>ABC Distributor:</strong> inactive for 5 days</li>
        <li><strong>Inventory:</strong> Prod X overstocked risk</li>
        <li><strong>Cash:</strong> ₹9.4L available and healthy</li>
        <li><strong>Target coverage:</strong> {{ dashboard_stats.masters.distributors }} distributors active</li>
      </ul>
      <div class="ai-actions">
        <button class="btn btn-primary">Prepare Today's Action Plan</button>
        <button class="btn btn-secondary">Ask NEXORA</button>
      </div>
    </section>

    <section class="kpi-grid">
      <div class="panel kpi-card"><div class="kpi-label">Sales</div><div class="kpi-value">₹18.2L</div><div class="kpi-change">↑ 8.0%</div></div>
      <div class="panel kpi-card"><div class="kpi-label">Profit</div><div class="kpi-value">12.5%</div><div class="kpi-change down">↓ 2.1%</div></div>
      <div class="panel kpi-card"><div class="kpi-label">Gross Margin</div><div class="kpi-value">42.3%</div><div class="kpi-change">↑ 1.2%</div></div>
      <div class="panel kpi-card"><div class="kpi-label">Collections</div><div class="kpi-value">₹3.1L</div><div class="kpi-change">↓ 5.0%</div></div>
      <div class="panel kpi-card"><div class="kpi-label">Cash</div><div class="kpi-value">₹9.4L</div><div class="kpi-change">↑ 3.2%</div></div>
    </section>

    <section class="summary-grid">
      <div class="panel summary-card">
        <h2>Distributor Snapshot</h2>
        <p>Distributor Code</p>
        <p>Firm nick name</p>
      </div>
      <div class="panel summary-card">
        <h2>Retailer Snapshot</h2>
        <p>Latest retailer records with contact and sales executive details.</p>
      </div>
    </section>

    <section class="layout-grid">
      <div class="panel health-card">
        <small class="kpi-label">Business Health Score</small>
        <div class="score">92<small>/100</small></div>
        <div class="health-bar"><span></span></div>
        <strong>Status: Excellent ✓</strong>
        <ul class="health-list">
          <li><span>Profitability</span><strong>9.2/10</strong></li>
          <li><span>Growth</span><strong>8.8/10</strong></li>
          <li><span>Collections</span><strong>7.5/10</strong></li>
          <li><span>Inventory Health</span><strong>9.1/10</strong></li>
          <li><span>Cash Position</span><strong>9.5/10</strong></li>
        </ul>
      </div>

      <div class="panel growth-card">
        <small class="kpi-label">Sales Trend (Last 90 Days)</small>
        <div class="chart-grid" aria-label="sales trend chart">
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
          <span class="chart-bar"></span>
        </div>
      </div>
    </section>

    <section class="summary-grid">
      <div class="panel summary-card">
        <small>AI Recommendations</small>
        <ul>
          <li>Collections focus: recover ₹0.7L from 3 overdue invoices</li>
          <li>Inventory optimization: clear dead stock and reduce overstock</li>
          <li>GST filing: complete by 11:59 PM today</li>
        </ul>
      </div>
      <div class="panel summary-card">
        <small>Forecast Center</small>
        <ul>
          <li>30-day sales projection: ₹19.5L (↑7%)</li>
          <li>Cash position end of month: ₹14.6L</li>
          <li>Confidence: 87%</li>
        </ul>
      </div>
    </section>
  </main>
</body>
</html>
"""


@analytics_blueprint.route("/analytics")
@require_jwt_auth
def analytics() -> str:
    workspace_id = get_workspace_id()
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    dashboard_stats = db.get_dashboard_payload(workspace_id=workspace_id)
    distributors = db.list_master_distributors(limit=50, workspace_id=workspace_id)
    raw_retailers = db.list_master_retailers(limit=50, workspace_id=workspace_id)
    dist_id_to_name = {d["id"]: d["firm_name"] for d in distributors}
    retailers = []
    for r in raw_retailers:
        r = dict(r)
        r["distributor_name"] = dist_id_to_name.get(r.get("distributor_id"), "Unknown")
        retailers.append(r)
    return render_template_string(
        ANALYTICS_TEMPLATE,
        dashboard_stats=dashboard_stats,
        distributors=distributors,
        retailers=retailers,
    )



