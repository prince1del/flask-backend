"""Brand alias normalization and duplicate merge."""

import importlib
import io
import sqlite3
from pathlib import Path

import openpyxl
import pytest

import article_master_db as amdb
import article_master_parser as amparser


def _schema_conn(db_path):
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    amdb.create_category(conn, 1, "Bed", ["brand", "TC", "size"], is_confirmed=True)
    return conn


def test_brand_alias_maps_bluemen_family_to_blumen(tmp_path):
    conn = _schema_conn(tmp_path / "alias.sqlite3")
    amdb.ensure_default_brand_aliases(conn, 1)
    alias_map = amdb.get_brand_alias_map(conn, 1)
    assert alias_map["bluemen"] == "Blumen"
    assert alias_map["bluman"] == "Blumen"
    assert amdb.canonicalize_brand_name("Bluemen", alias_map) == "Blumen"
    assert amdb.canonicalize_brand_name("Bluman", alias_map) == "Blumen"
    assert amdb.canonicalize_brand_name("Blumen", alias_map) == "Blumen"
    conn.close()


def test_apply_brand_aliases_rebuilds_item_key(tmp_path):
    conn = _schema_conn(tmp_path / "key.sqlite3")
    lookup = {"Bed": ["brand", "TC", "size"]}
    articles = [{
        "category": "Bed",
        "brand": "Bluemen",
        "size": "DB BS",
        "product_type": "Sheet Sets",
        "mrp": 1129,
        "ptr": 790.3,
        "ex_mill_price": 621,
        "item_key": "OLD",
        "extra_attributes": {"TC": "104"},
    }]
    amdb.apply_brand_aliases_to_articles(conn, 1, articles, lookup, ["brand", "size"])
    assert articles[0]["brand"] == "Blumen"
    assert articles[0]["item_key"] == "BLUMEN|104|DB BS"
    conn.close()


def test_merge_blumen_bluemen_duplicates(tmp_path):
    conn = _schema_conn(tmp_path / "merge.sqlite3")
    lookup = {"Bed": ["brand", "TC", "size"]}
    amdb.insert_article(conn, 1, {
        "category": "Bed", "brand": "Blumen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1129, "ptr": 790.3, "ex_mill_price": 621, "bale_pack_size": 18,
        "item_key": "BLUMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    }, workspace_id="ws-1")
    amdb.insert_article(conn, 1, {
        "category": "Bed", "brand": "Bluemen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1299, "ptr": 866, "ex_mill_price": 733.9, "bale_pack_size": 18,
        "item_key": "BLUEMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    }, workspace_id="ws-1")

    groups = amdb.find_duplicate_groups(conn, 1, lookup)
    assert len(groups) == 1
    assert len(groups[0]["articles"]) == 2

    keep_id = groups[0]["suggested_keep_id"]
    remove_ids = [a["id"] for a in groups[0]["articles"] if a["id"] != keep_id]
    price_from = groups[0]["suggested_price_from_id"]
    updated, removed = amdb.merge_articles(
        conn, 1, keep_id, remove_ids, price_from_id=price_from, changed_by="test",
    )
    assert removed == 1
    assert float(updated["mrp"]) == 1299
    assert updated["brand"] == "Blumen"

    remaining = amdb.get_all_articles(conn, 1)
    assert len(remaining) == 1
    conn.close()


def test_classify_flags_duplicate_blumen_bluemen_rows(tmp_path):
    conn = _schema_conn(tmp_path / "dup_classify.sqlite3")
    lookup = {"Bed": ["brand", "TC", "size"]}
    amdb.ensure_default_brand_aliases(conn, 1)
    amdb.insert_article(conn, 1, {
        "category": "Bed", "brand": "Blumen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1129, "ptr": 790.3, "ex_mill_price": 621, "bale_pack_size": 18,
        "item_key": "BLUMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    })
    amdb.insert_article(conn, 1, {
        "category": "Bed", "brand": "Bluemen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1299, "ptr": 866, "ex_mill_price": 733.9, "bale_pack_size": 18,
        "item_key": "BLUEMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    })
    upload_row = {
        "category": "Bed", "brand": "Blumen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1299, "ptr": 866, "ex_mill_price": 733.9, "bale_pack_size": 18,
        "item_key": "BLUMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    }
    result = amdb.classify_upload_article(conn, 1, upload_row, ["brand", "TC", "size"])
    assert result["action"] == "conflict"
    assert result["conflict_reason"] == "duplicate_entries_in_master"
    assert len(result["duplicate_ids"]) == 1
    conn.close()


def test_upload_applies_alias_before_conflict(tmp_path, monkeypatch):
    db_path = tmp_path / "upload_alias.sqlite3"
    conn = _schema_conn(db_path)
    amdb.insert_article(conn, 1, {
        "category": "Bed", "brand": "Blumen", "size": "DB BS", "product_type": "Sheet Sets",
        "mrp": 1129, "ptr": 790.3, "ex_mill_price": 621, "bale_pack_size": 18,
        "item_key": "BLUMEN|104|DB BS", "extra_attributes": {"TC": "104"},
    }, workspace_id="ws-1")
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "alias-upload-test")

    import app.init_db as init_db_module
    import app.web_app as web_app_module
    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB
    db = CentralizedDB(str(db_path))
    db.create_user("alias_user", "pass123", role="sales_executive", workspace_id="ws-1")
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "alias_user", "password": "pass123"},
    ).get_json()["data"]["access_token"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Brand", "Size", "Product", "MRP", "PTR", "Ex-Mill", "Bale Size", "TC"])
    ws.append(["Blumen", "DB BS", "Sheet Sets", 1299, 866, 733.9, 18, 104])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = client.post(
        "/api/v1/article-master/upload",
        data={
            "file": (io.BytesIO(buf.read()), "aw26.xlsx"),
            "confirmed_category": "Bed",
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = resp.get_json()
    assert resp.status_code == 200, payload
    assert payload["status"] == "price_mismatch_confirmation_required"
    assert len(payload["conflicts"]) == 1
    assert payload["conflicts"][0]["brand"] == "Blumen"


def test_urban_living_luxury_new_is_not_fuzzy_duplicate():
    assert amparser.brands_match_fuzzy("Blumen", "Blumen")
    assert not amparser.brands_match_fuzzy(
        "Urban Living Luxury",
        "Urban Living Luxury New",
    )
    assert not amparser.brands_match_fuzzy(
        "Urban Living Luxury",
        "Urban Living Luxury / New",
    )
