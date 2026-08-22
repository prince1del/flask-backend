# House of Prizm — foundation (isolated from NEXORA executive)

## Isolation rules
- **Do not** change `app/routes/executive.py`, NEXORA My Day UI, or existing executive / `sales_executive` user accounts.
- Prizm uses role `hop_admin` and workspace `house_of_prizm` only.
- Login as `hop_admin` opens `#hop-executive-workspace` — never `#executive-home-workspace`.

## Create user
```bat
.venv\Scripts\python.exe scripts\create_hop_user.py
```
Default username: `prince1del` (override with `--username` / `--password` or env `HOP_ADMIN_USERNAME` / `HOP_ADMIN_PASSWORD`).  
If `hop_prizm` still exists, the script renames it to `prince1del` and sets the password.

## Architecture (locked)
**Project-centric ERP** — Project is the hub. Funnel:

Lead → Meeting → Requirement → Quotation → Negotiation → PO → Production → Dispatch → Invoice → Payment

Every project sits in exactly one `stage` from `PROJECT_STAGES`.

## Working modules (live data, no dummy seed)
| Area | UI | API |
|------|----|-----|
| Executive dashboard | KPI cards → drill-down | `GET /executive/snapshot` |
| Customers / Leads / Meetings / Projects | Lists + create forms | CRUD under `/customers` `/leads` `/meetings` `/projects` |
| Project Hub | Tabs: overview, meetings, samples, vendors, quotes, PO, dispatch, invoices, payments, complaints, timeline | `GET /projects/<id>/hub` + `PATCH /projects/<id>` |
| Quotations | List, status, revise | `/quotations`, `/quotations/<id>/revise` |
| Vendors + comparison | Lists + forms | `/vendors`, `/vendor-comparisons` |
| Samples / Catalogue | Lists + margin calc | `/samples`, `/products` |
| Orders / Dispatch / Invoices / Payments / Complaints | Lists + forms | matching `/api/v1/hop/*` |
| Reports | Pipeline, funnel, receivables, customer dash, daily, profit, targets | `GET /reports/<key>` |

## Files
- Schema: `app/hop_schema.py`
- Core CRUD: `app/hop_db.py`
- Funnel + reports: `app/hop_ops.py`
- Routes: `app/routes/hop.py`
- UI: `app/static/hop_app.js` + `#hop-executive-workspace` in `index.html`

## Still thinner / later polish
- BOQ AI read, PDF/WhatsApp quote send, call logging, banking cash ledger, certificates expiry board, AI assistant
- Quote line-items editor, inventory reservations
