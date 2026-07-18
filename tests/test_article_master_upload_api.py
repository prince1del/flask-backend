"""Upload API should return JSON, not HTML error pages."""

import importlib
import io
import json
import sqlite3

import openpyxl
import pytest

import article_master_db as amdb


def _make_workbook_bytes(brand="ASTER", size="DB BS", mrp=999, ptr=450, ex_mill=400, tc=100):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "TC"])
    ws.append([brand, size, "Bedsheet SS-26", mrp, ptr, ex_mill, 10, tc])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _upload_confirmed(client, token, buf, category="AUTO", conflict_resolutions=None):
    data = {
        "file": (io.BytesIO(buf.read()), "bedsheet_test.xlsx"),
        "confirmed_category": category,
    }
    if conflict_resolutions is not None:
        data["conflict_resolutions"] = json.dumps(conflict_resolutions)
    buf.seek(0)
    return client.post(
        "/api/v1/article-master/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )


def setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "am_upload.sqlite3"
    schema_path = tmp_path.parent.parent / "article_master_schema.sql"
    if not schema_path.exists():
        schema_path = tmp_path.parent / "article_master_schema.sql"
    # resolve from repo root
    from pathlib import Path
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"

    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    amdb.create_category(conn, 1, "Bed", ["brand", "TC", "size"], is_confirmed=True, workspace_id="ws-1")
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "article-upload-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB
    db = CentralizedDB(str(db_path))
    db.create_user("am_upload_user", "pass123", role="sales_executive", workspace_id="ws-1")

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "am_upload_user", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    return client, token


def test_article_master_upload_returns_json(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    data = {
        "file": (io.BytesIO(_make_workbook_bytes().read()), "bedsheet_test.xlsx"),
    }
    resp = client.post(
        "/api/v1/article-master/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.get_data(as_text=True)
    assert resp.content_type.startswith("application/json"), body[:500]
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    assert payload["status"] == "confirmation_required"
    assert payload["detected_category"] in {"Bed", "UNCATEGORIZED - REVIEW"}
    assert "category_breakdown" in payload

    confirmed = client.post(
        "/api/v1/article-master/upload",
        data={
            "file": (io.BytesIO(_make_workbook_bytes().read()), "bedsheet_test.xlsx"),
            "confirmed_category": "AUTO",
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    confirmed_payload = confirmed.get_json()
    assert confirmed.status_code == 200, confirmed_payload
    assert confirmed_payload["status"] == "success"
    assert confirmed_payload["created"] >= 1


def test_delete_one_and_delete_all(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    # seed one article via confirmed upload
    client.post(
        "/api/v1/article-master/upload",
        data={
            "file": (io.BytesIO(_make_workbook_bytes().read()), "bedsheet_test.xlsx"),
            "confirmed_category": "Bed",
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    listed = client.get(
        "/api/v1/article-master/list",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    assert listed["count"] >= 1
    article_id = listed["articles"][0]["id"]

    deleted = client.delete(
        f"/api/v1/article-master/{article_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deleted.status_code == 200

    # re-upload then delete all
    client.post(
        "/api/v1/article-master/upload",
        data={
            "file": (io.BytesIO(_make_workbook_bytes().read()), "bedsheet_test.xlsx"),
            "confirmed_category": "Bed",
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    wipe = client.post(
        "/api/v1/article-master/delete-all",
        json={"category": "All"},
        headers={"Authorization": f"Bearer {token}"},
    )
    wipe_payload = wipe.get_json()
    assert wipe.status_code == 200
    assert wipe_payload["deleted"] >= 1
    empty = client.get(
        "/api/v1/article-master/list",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    assert empty["count"] == 0


def test_upload_skips_duplicate_with_same_prices(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    buf = _make_workbook_bytes()
    first = _upload_confirmed(client, token, buf, category="Bed")
    assert first.status_code == 200
    assert first.get_json()["created"] == 1

    buf2 = _make_workbook_bytes()
    second = _upload_confirmed(client, token, buf2, category="Bed")
    payload = second.get_json()
    assert second.status_code == 200
    assert payload["status"] == "success"
    assert payload["created"] == 0
    assert payload["skipped"] == 1
    assert "already available" in payload["message"].lower()


def test_upload_price_mismatch_requires_confirmation(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    buf = _make_workbook_bytes(mrp=1049, ptr=719.31, ex_mill=625.49)
    first = _upload_confirmed(client, token, buf, category="Bed")
    assert first.get_json()["created"] == 1

    buf2 = _make_workbook_bytes(mrp=799, ptr=532.67, ex_mill=451.41)
    second = _upload_confirmed(client, token, buf2, category="Bed")
    payload = second.get_json()
    assert second.status_code == 200
    assert payload["status"] == "price_mismatch_confirmation_required"
    assert len(payload["conflicts"]) == 1
    assert payload["conflicts"][0]["price_diffs"]
    assert payload["created"] == 0

    listed = client.get(
        "/api/v1/article-master/list",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    assert listed["count"] == 1


def test_upload_price_mismatch_replace_updates_existing(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    buf = _make_workbook_bytes(mrp=1049, ptr=719.31, ex_mill=625.49)
    _upload_confirmed(client, token, buf, category="Bed")

    buf2 = _make_workbook_bytes(mrp=799, ptr=532.67, ex_mill=451.41)
    conflict = _upload_confirmed(client, token, buf2, category="Bed").get_json()
    idx = str(conflict["conflicts"][0]["upload_index"])

    buf3 = _make_workbook_bytes(mrp=799, ptr=532.67, ex_mill=451.41)
    resolved = _upload_confirmed(
        client, token, buf3, category="Bed", conflict_resolutions={idx: "replace"}
    )
    payload = resolved.get_json()
    assert payload["status"] == "success"
    assert payload["updated"] == 1
    assert payload["created"] == 0

    listed = client.get(
        "/api/v1/article-master/list",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    assert listed["count"] == 1
    article = listed["articles"][0]
    assert float(article["mrp"]) == 799


def test_upload_item_key_drift_can_create_new(tmp_path, monkeypatch):
    client, token = setup_app(tmp_path, monkeypatch)
    db_path = tmp_path / "am_upload.sqlite3"
    conn = sqlite3.connect(db_path)
    amdb.insert_article(
        conn,
        1,
        {
            "category": "Bed",
            "brand": "ASTER",
            "size": "DB BS",
            "product_type": "Bedsheet SS-26",
            "mrp": 1049,
            "ptr": 719.31,
            "ex_mill_price": 625.49,
            "bale_pack_size": 10,
            "item_key": "ASTER|100 (ONE IN A DENT)|DB BS",
            "extra_attributes": {"TC": "100 (ONE IN A DENT)"},
        },
        workspace_id="ws-1",
    )
    conn.close()

    buf = _make_workbook_bytes(mrp=799, ptr=532.67, ex_mill=451.41, tc=100)
    conflict = _upload_confirmed(client, token, buf, category="Bed").get_json()
    assert conflict["status"] == "price_mismatch_confirmation_required"
    assert conflict["conflicts"][0]["can_create_new"] is True
    idx = str(conflict["conflicts"][0]["upload_index"])

    buf2 = _make_workbook_bytes(mrp=799, ptr=532.67, ex_mill=451.41, tc=100)
    resolved = _upload_confirmed(
        client, token, buf2, category="Bed", conflict_resolutions={idx: "create_new"}
    )
    payload = resolved.get_json()
    assert payload["status"] == "success"
    assert payload["created"] == 1

    listed = client.get(
        "/api/v1/article-master/list",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    assert listed["count"] == 2
