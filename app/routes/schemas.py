from flask import Blueprint, redirect, render_template_string, request

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth

schemas_blueprint = Blueprint("schemas", __name__)

SCHEMA_MANAGER_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>NEXORA |Schema Manager</title>
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


@schemas_blueprint.route("/api/v1/schemas", methods=["GET", "POST"])
@require_jwt_auth
def schemas() -> tuple[dict[str, object], int]:
    return {"status": "ok", "items": []}, 200


@schemas_blueprint.route("/settings/schema")
@schemas_blueprint.route("/settings/schema/")
@require_jwt_auth
def schema_manager():
    entity = request.args.get("entity", "distributor")
    message = request.args.get("message", "")
    db = CentralizedDB("centralized_db.sqlite3")
    fields = db.get_all_schema_fields(entity)
    return render_template_string(
        SCHEMA_MANAGER_TEMPLATE, entity=entity, fields=fields, message=message
    )


@schemas_blueprint.route("/settings/schema/add", methods=["POST"])
@require_jwt_auth
def schema_add_field():
    entity = request.form.get("entity", "distributor")
    field_name = request.form.get("field_name", "").strip()
    field_label = request.form.get("field_label", "").strip()
    field_type = request.form.get("field_type", "text")
    if field_name and field_label:
        db = CentralizedDB("centralized_db.sqlite3")
        existing = db.get_all_schema_fields(entity)
        next_order = max([f["field_order"] for f in existing], default=-1) + 1
        db.add_schema_field(entity, field_name, field_label, field_type, next_order)
        message = f"Field '{field_label}' added!"
    else:
        message = "Field name and label required."
    return redirect(f"/settings/schema?entity={entity}&message={message}")


@schemas_blueprint.route("/settings/schema/delete", methods=["POST"])
@require_jwt_auth
def schema_delete_field():
    entity = request.form.get("entity", "distributor")
    field_id = request.form.get("field_id", type=int)
    if field_id:
        CentralizedDB("centralized_db.sqlite3").delete_schema_field(field_id)
    return redirect(f"/settings/schema?entity={entity}&message=Field deleted")


@schemas_blueprint.route("/settings/schema/toggle", methods=["POST"])
@require_jwt_auth
def schema_toggle_field():
    entity = request.form.get("entity", "distributor")
    field_id = request.form.get("field_id", type=int)
    is_visible = request.form.get("is_visible", type=int)
    if field_id is not None:
        CentralizedDB("centralized_db.sqlite3").toggle_schema_field_visibility(
            field_id, is_visible
        )
    return redirect(f"/settings/schema?entity={entity}&message=Visibility updated")


@schemas_blueprint.route("/settings/schema/move", methods=["POST"])
@require_jwt_auth
def schema_move_field():
    entity = request.form.get("entity", "distributor")
    field_id = request.form.get("field_id", type=int)
    direction = request.form.get("direction", "up")
    db = CentralizedDB("centralized_db.sqlite3")
    fields = db.get_all_schema_fields(entity)
    ids = [f["id"] for f in fields]
    if field_id in ids:
        idx = ids.index(field_id)
        if direction == "up" and idx > 0:
            ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
        elif direction == "down" and idx < len(ids) - 1:
            ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]
        db.reorder_schema_fields([{"id": fid, "order": i} for i, fid in enumerate(ids)])
    return redirect(f"/settings/schema?entity={entity}&message=Reordered")


@schemas_blueprint.route("/settings/schema/seed", methods=["POST"])
@require_jwt_auth
def schema_seed():
    CentralizedDB("centralized_db.sqlite3").seed_default_schema()
    return redirect(
        "/settings/schema?entity=distributor&message=Default schema loaded!"
    )
