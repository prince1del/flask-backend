"""Full API flow tests for Distributor Filled-Order Matching."""

import importlib
import io
import sqlite3
from pathlib import Path

import openpyxl
import pytest

import article_master_db as amdb


def _make_workbook(header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "fo_api.sqlite3"
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"

    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "filled-order-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB
    db = CentralizedDB(str(db_path))
    user = db.create_user("fo_test_user", "pass123", role="sales_executive", workspace_id="ws-1")

    # Article Master is per-user, so seed it for the login that uploads.
    user_id = int(user["id"])
    conn = sqlite3.connect(db_path)
    amdb.create_category(conn, user_id, "Bed", ["brand", "size"], is_confirmed=True, workspace_id="ws-1")
    amdb.upsert_article(conn, user_id, {
        "category": "Bed", "product_type": "Bedsheet", "brand": "ASTER", "size": "DB BS",
        "mrp": 999, "ptr": 450, "ex_mill_price": 400, "bale_pack_size": 12,
        "item_key": "ASTER|DB BS", "extra_attributes": {},
    }, workspace_id="ws-1")
    conn.close()

    distributor_id = db.add_master_distributor(
        "Bernina Textiles",
        firm_name="Bernina International P Ltd",
        firm_nick_name="BND",
        workspace_id="ws-1",
    )

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "fo_test_user", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    return client, token, distributor_id


def _bedsheet_workbook_bytes():
    header = ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty"]
    rows = [
        ["ASTER", "DB BS", "Bedsheet SS-26", 999, 450, 400, 12, 60],   # matched, clean multiple
        ["NEWCO", "KG BS", "Bedsheet SS-26", 500, 250, 200, 10, 25],   # unmatched, NOT clean multiple (25 % 10 != 0)
    ]
    return _make_workbook(header, rows)


def test_upload_requires_season_then_commits(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: no season yet -> season_confirmation_required (no prior orders => last_season None)
    resp = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    assert payload["status"] == "season_confirmation_required"
    assert payload["last_season"] is None

    # Step 2: supply season -> confirmation_required preview (matched/unmatched/flagged counts)
    resp = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    assert payload["status"] == "confirmation_required"
    assert payload["total_lines"] == 2
    assert payload["matched_lines"] == 1
    assert payload["unmatched_lines"] == 1
    assert payload["flagged_lines"] == 1
    assert payload["quantity_column_used"] == "Qnty"
    assert len(payload.get("issue_items", [])) >= 1
    unmatched = [it for it in payload.get("issue_items", []) if not it.get("matched")]
    assert len(unmatched) >= 1
    assert unmatched[0].get("issue_summary")

    # Step 3: confirm commit -> saved
    resp = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
            "confirm_commit": "true",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    assert payload["status"] == "success"
    order = payload["filled_order"]
    assert order["total_lines"] == 2
    assert order["matched_lines"] == 1
    assert order["unmatched_lines"] == 1
    assert order["flagged_lines"] == 1
    assert order["season"] == "AW26"
    order_id = order["id"]

    # list + detail
    listed = client.get("/api/v1/filled-orders/list", headers=headers).get_json()
    assert listed["count"] == 1

    detail = client.get(f"/api/v1/filled-orders/{order_id}", headers=headers).get_json()
    items = detail["items"]
    assert len(items) == 2
    matched_item = next(i for i in items if i["matched"])
    unmatched_item = next(i for i in items if not i["matched"])
    assert matched_item["mrp"] == 999  # snapshotted from Article Master, not the file's own 999 (same here) 
    assert matched_item["is_clean_bale_multiple"] is True
    assert unmatched_item["is_clean_bale_multiple"] is False
    assert unmatched_item["article_id"] is None

    # manual correction via PATCH
    patch = client.patch(
        f"/api/v1/filled-orders/{order_id}/items/{unmatched_item['id']}",
        json={"raw_qty_value": 3, "brand": "NEWCO FIXED"},
        headers=headers,
    )
    patch_payload = patch.get_json()
    assert patch.status_code == 200, patch_payload
    assert patch_payload["item"]["brand"] == "NEWCO FIXED"
    assert patch_payload["item"]["final_piece_qty"] == 30
    assert patch_payload["item"]["is_clean_bale_multiple"] is True
    assert patch_payload["filled_order"]["flagged_lines"] == 0

    # next upload should suggest AW26 as last_season
    resp2 = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed2.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload2 = resp2.get_json()
    assert payload2["status"] == "season_confirmation_required"
    assert payload2["last_season"] == "AW26"

    # download regenerates an Excel file
    download = client.get(f"/api/v1/filled-orders/{order_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content_type.startswith("application/vnd.openxmlformats")
    assert "Bernina" in download.headers.get("Content-Disposition", "")
    assert "AW26" in download.headers.get("Content-Disposition", "")

    # resolve the unmatched item by auto-adding to Article Master
    resolve = client.post(
        f"/api/v1/filled-orders/{order_id}/resolve-unmatched",
        json={"item_id": unmatched_item["id"], "action": "add_to_article_master"},
        headers=headers,
    )
    resolve_payload = resolve.get_json()
    assert resolve.status_code == 200, resolve_payload
    assert resolve_payload["status"] == "added_to_article_master"

    detail_after = client.get(f"/api/v1/filled-orders/{order_id}", headers=headers).get_json()
    assert detail_after["filled_order"]["matched_lines"] == 2
    assert detail_after["filled_order"]["unmatched_lines"] == 0

    # delete one item -> counts recompute
    remaining_item_id = detail_after["items"][0]["id"]
    del_item = client.delete(
        f"/api/v1/filled-orders/{order_id}/items/{remaining_item_id}", headers=headers,
    )
    assert del_item.status_code == 200
    after_item_delete = client.get(f"/api/v1/filled-orders/{order_id}", headers=headers).get_json()
    assert after_item_delete["filled_order"]["total_lines"] == 1

    # delete whole order
    del_order = client.delete(f"/api/v1/filled-orders/{order_id}", headers=headers)
    assert del_order.status_code == 200
    empty_list = client.get("/api/v1/filled-orders/list", headers=headers).get_json()
    assert empty_list["count"] == 0


def _multi_candidate_workbook_bytes():
    header = [
        "Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size",
        "No of Bales", "Qty", "Add", "Additional Order Qty",
    ]
    rows = [
        ["Florentine", "King", "Bedsheet SS-26", 1000, 500, 400, 12, 5, 60, 10, 70],
        ["Florentine", "Queen", "Bedsheet SS-26", 900, 450, 360, 12, 8, 96, 0, 96],
        ["Marigold", "King", "Bedsheet SS-26", 1100, 550, 440, 10, 3, 30, 5, 35],
    ]
    return _make_workbook(header, rows)


def test_distributor_confirmation_from_filename(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    preview = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "BND.xlsx"),
            "category": "Bed",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = preview.get_json()
    assert preview.status_code == 200, payload
    assert payload["status"] == "distributor_confirmation_required"
    assert payload["filename_hint"] == "bnd"
    assert payload["suggested_distributor"]["id"] == distributor_id


def test_bnd_auto_additional_order_qty_and_pref_reuse(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_multi_candidate_workbook_bytes().read()), "bnd.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    # Additional Order Qty auto-selected — no qty-column prompt
    assert payload["status"] == "confirmation_required"
    assert payload["quantity_column_used"] == "Additional Order Qty"

    resp2 = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_multi_candidate_workbook_bytes().read()), "bnd.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
            "confirm_commit": "true",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload2 = resp2.get_json()
    assert resp2.status_code == 200, payload2
    assert payload2["status"] == "success"
    order = payload2["filled_order"]
    assert order["quantity_column_used"] == "Additional Order Qty"
    assert order["total_lines"] == 3


def test_duplicate_filled_order_requires_replace(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    def upload_payload(**extra):
        data = {
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26 Bedsheet",
        }
        data.update(extra)
        return data

    preview = client.post(
        "/api/v1/filled-orders/upload",
        data=upload_payload(),
        content_type="multipart/form-data",
        headers=headers,
    )
    assert preview.status_code == 200
    assert preview.get_json()["status"] == "confirmation_required"

    first_save = client.post(
        "/api/v1/filled-orders/upload",
        data=upload_payload(confirm_commit="true"),
        content_type="multipart/form-data",
        headers=headers,
    )
    first_payload = first_save.get_json()
    assert first_save.status_code == 200, first_payload
    assert first_payload["status"] == "success"
    first_id = first_payload["filled_order"]["id"]

    preview2 = client.post(
        "/api/v1/filled-orders/upload",
        data=upload_payload(),
        content_type="multipart/form-data",
        headers=headers,
    )
    preview2_payload = preview2.get_json()
    assert preview2.status_code == 200, preview2_payload
    assert preview2_payload["status"] == "confirmation_required"
    assert preview2_payload["existing_order"]["id"] == first_id

    blocked = client.post(
        "/api/v1/filled-orders/upload",
        data=upload_payload(confirm_commit="true"),
        content_type="multipart/form-data",
        headers=headers,
    )
    blocked_payload = blocked.get_json()
    assert blocked.status_code == 200, blocked_payload
    assert blocked_payload["status"] == "duplicate_order_confirmation_required"
    assert blocked_payload["existing_order"]["id"] == first_id

    listed = client.get("/api/v1/filled-orders/list", headers=headers).get_json()
    assert listed["count"] == 1

    replaced = client.post(
        "/api/v1/filled-orders/upload",
        data=upload_payload(confirm_commit="true", confirm_replace="true"),
        content_type="multipart/form-data",
        headers=headers,
    )
    replaced_payload = replaced.get_json()
    assert replaced.status_code == 200, replaced_payload
    assert replaced_payload["status"] == "success"
    assert replaced_payload["replaced_existing"] is True
    assert replaced_payload["filled_order"]["id"] != first_id

    listed_after = client.get("/api/v1/filled-orders/list", headers=headers).get_json()
    assert listed_after["count"] == 1
    assert listed_after["filled_orders"][0]["id"] == replaced_payload["filled_order"]["id"]


def test_unique_slot_dedupes_legacy_duplicates(tmp_path):
    db_path = tmp_path / "dedupe.sqlite3"
    schema_path = Path(__file__).resolve().parent.parent / "filled_orders_schema.sql"
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute(
        """INSERT INTO filled_orders
           (user_id, distributor_id, distributor_name_raw, category, season, total_lines, matched_lines)
           VALUES (1, 5, 'Bernina', 'Bed', 'AW26 Bedsheet', 10, 10)"""
    )
    conn.execute(
        """INSERT INTO filled_orders
           (user_id, distributor_id, distributor_name_raw, category, season, total_lines, matched_lines)
           VALUES (1, 5, 'Bernina', 'Bed', 'AW26 Bedsheet', 20, 20)"""
    )
    conn.commit()
    conn.close()

    import filled_orders_db as fodb

    conn = sqlite3.connect(db_path)
    fodb._ensure_filled_orders_unique_slot(conn)
    remaining = fodb.list_filled_orders(conn, 1)
    conn.close()
    assert len(remaining) == 1
    assert remaining[0]["total_lines"] == 20


def test_season_overview_groups_by_season(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
            "confirm_commit": "true",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()

    overview = client.get("/api/v1/filled-orders/season-overview", headers=headers).get_json()
    assert overview["count"] == 1
    season = overview["seasons"][0]
    assert season["season"] == "AW26"
    assert len(season["rows"]) >= 1
    row = season["rows"][0]
    assert row["distributor_name"]
    assert row["total_piece_qty"] > 0
    assert row["total_ex_mill_value"] > 0
    assert season["total_piece_qty"] == row["total_piece_qty"]


def test_special_order_merges_into_existing_instead_of_replace(tmp_path, monkeypatch):
    client, token, distributor_id = setup_app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    def first_payload(**extra):
        data = {
            "file": (io.BytesIO(_bedsheet_workbook_bytes().read()), "bernina_bed.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
        }
        data.update(extra)
        return data

    assert client.post(
        "/api/v1/filled-orders/upload",
        data=first_payload(confirm_commit="true"),
        content_type="multipart/form-data",
        headers=headers,
    ).get_json()["status"] == "success"

    extra_wb = _make_workbook(
        ["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "Qnty"],
        [["ASTER", "DB BS", "Bedsheet SS-26", 999, 450, 400, 12, 12]],
    )
    merged = client.post(
        "/api/v1/filled-orders/upload",
        data={
            "file": (extra_wb, "BND Bath linen special order.xlsx"),
            "distributor_id": str(distributor_id),
            "category": "Bed",
            "season": "AW26",
            "confirm_commit": "true",
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    payload = merged.get_json()
    assert merged.status_code == 200, payload
    assert payload["status"] == "success"
    assert payload["merged_into_existing"] is True
    assert payload["replaced_existing"] is False

    listed = client.get("/api/v1/filled-orders/list", headers=headers).get_json()
    assert listed["count"] == 1
    order = listed["filled_orders"][0]
    detail = client.get(f"/api/v1/filled-orders/{order['id']}", headers=headers).get_json()
    items = detail["items"]
    assert items, detail
    aster = next(
        it for it in items
        if str(it.get("brand") or "").strip().upper() == "ASTER"
        or "ASTER" in str(it.get("item_key") or "").upper()
    )
    assert float(aster["final_piece_qty"]) == 72  # 60 original + 12 special
    # ASTER 72×400 + unmatched NEWCO 25×200
    assert float(order["total_ex_mill_value"]) == 72 * 400 + 25 * 200
