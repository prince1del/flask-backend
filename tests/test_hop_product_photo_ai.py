"""Photo → new product (QnA step 2): HSN GST rules, photo fingerprint match, read-photo flow (AI mocked, no cost)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import hop_hsn_gst, hop_product_ai, hop_product_photos as PH
from app.hop_db import connect
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def _img(kind: int, size=(400, 300)) -> bytes:
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    if kind == 1:
        for x in range(0, size[0], 40):
            d.rectangle([x, 0, x + 20, size[1]], fill="navy")
    else:
        d.ellipse([60, 40, 340, 260], fill="red")
        d.rectangle([150, 120, 250, 180], fill="yellow")
    out = io.BytesIO()
    im.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "ph.sqlite3")
    ensure_hop_schema(p)
    return p


def test_hsn_gst_rules():
    assert hop_hsn_gst.suggest("63022100", 899)["gst_pct"] == 5
    assert hop_hsn_gst.suggest("6302", 3200)["gst_pct"] == 18
    no_price = hop_hsn_gst.suggest("6303", None)
    assert no_price["gst_pct"] == 5 and "no price" in no_price["why"]
    assert hop_hsn_gst.suggest("5703", 9000)["gst_pct"] == 5
    assert hop_hsn_gst.suggest("8517", 100) is None and hop_hsn_gst.suggest("", 1) is None


def test_photo_fingerprint_match(db):
    with connect(db) as conn:
        a = create_product(conn, WS, {"name": "Stripe Sheet"})
        b = create_product(conn, WS, {"name": "Red Towel"})
        PH.save(conn, db, WS, a["id"], _img(1))
        PH.save(conn, db, WS, b["id"], _img(2))
        assert conn.execute("SELECT photo_hash FROM hop_products WHERE id=?", (a["id"],)).fetchone()[0]
        # a new photo of the stripes, a bit bigger → the stripe sheet comes first
        top = PH.match(conn, db, WS, _img(1, (600, 450)))
        assert top and top[0]["name"] == "Stripe Sheet" and top[0]["score"] >= 60
        PH.remove(conn, db, WS, a["id"])
        assert all(m["name"] != "Stripe Sheet" for m in PH.match(conn, db, WS, _img(1)))


def test_read_photo_known_ean_skips_ai(db, monkeypatch):
    from app.services import hop_ai

    monkeypatch.setattr(hop_ai, "read_product_photo", lambda *a, **k: pytest.fail("AI must not be called"))
    with connect(db) as conn:
        create_product(conn, WS, {"name": "Aster Sheet", "barcode": "8901234567890"})
        out = hop_product_ai.read_photo(conn, db, WS, _img(1), "image/jpeg", user_id=1, ean="8901234567890")
        assert out["existing"]["name"] == "Aster Sheet" and out["ai_used"] is False


def test_read_photo_new_item(db, monkeypatch):
    from app.services import hop_ai

    calls = []

    def fake(data, mime, *, user_id):
        calls.append(user_id)
        return {"name": "Flora Towel", "brand": "", "category": "Towel", "mrp": 499, "size": "", "design": "Flora",
                "colour": "Blue", "material": "", "hsn": "63026000", "ean": "", "unit": "Pcs", "confidence": "high"}

    monkeypatch.setattr(hop_ai, "read_product_photo", fake)
    with connect(db) as conn:
        out = hop_product_ai.read_photo(conn, db, WS, _img(2), "image/jpeg", user_id=7, ean="8909999999999")
    assert calls == [7]
    assert out["fields"]["name"] == "Flora Towel" and out["fields"]["ean"] == "8909999999999"
    assert out["gst"]["gst_pct"] == 5 and out["existing"] is None


def test_photo_routes(tmp_path, monkeypatch):
    from app.services import hop_ai
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    monkeypatch.setattr(hop_ai, "read_product_photo", lambda *a, **k: {
        "name": "Lamp P", "category": "", "mrp": 0, "hsn": "", "ean": "", "unit": "Pcs", "confidence": "low"})
    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.post("/api/v1/hop/products/read-photo", headers=headers,
                    data={"photo": (io.BytesIO(_img(1)), "p.jpg", "image/jpeg")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["fields"]["name"] == "Lamp P" and any("MRP" in c for c in d["checks"])
    r = client.post("/api/v1/hop/products/match-photo", headers=headers,
                    data={"photo": (io.BytesIO(_img(1)), "p.jpg", "image/jpeg")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["data"]["matches"] == []
    assert client.post("/api/v1/hop/products/read-photo", headers=headers).status_code == 400
