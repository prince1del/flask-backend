"""AI scan of an order / handwritten note into a Sale Invoice or Quotation (Gemini faked)."""
from __future__ import annotations

import io
from pathlib import Path

from app.hop_db import connect, create_customer
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
from app.services import hop_ai

FAKE = {"customer": "Hotel Sea View", "customer_phone": "98765 43210", "date": "2026-11-16", "handwritten": True,
        "lines": [{"item": "Blackout curtain", "qty": 4, "unit": "pcs", "rate": 0},
                  {"item": "Sheer fabric", "qty": 20, "unit": "mtr", "rate": 236, "tax_pct": 18, "rate_includes_gst": True},
                  {"item": "Cushon cover", "qty": 6, "rate": 0, "unsure": True}],
        "total": 0, "notes": "deliver Monday"}


def test_match_sale(tmp_path: Path):
    p = str(tmp_path / "s.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Sea View Hotel Pvt Ltd", "mobile": "9876543210"})
        create_product(conn, WS, {"name": "Blackout Curtain", "selling_price": 1500, "gst_pct": 12, "unit": "PCS"})
        out = hop_ai.match_sale(conn, WS, FAKE, "quote")
        assert out["customer_id"] == c["id"] and out["handwritten"] is True and out["kind"] == "quote"
        l0, l1, l2 = out["lines"]
        assert (l0["item_name"], l0["rate"], l0["tax_pct"], l0["rate_from"]) == ("Blackout Curtain", 1500, 12, "catalogue")
        assert (l1["rate"], l1["unit"]) == (200, "MTR")          # 236 incl. 18% GST → 200
        assert l2["rate_from"] == "missing" and l2["unsure"] is True
        joined = " ".join(out["checks"])
        assert "No rate for" in joined and "Hard to read" in joined and "catalogue" in joined


def test_scan_route_kind(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    seen = {}

    def fake(data, mime, user_id, prompt=None):
        seen["prompt"] = prompt
        return FAKE

    monkeypatch.setattr(hop_ai, "_ask_gemini", fake)
    r = client.post("/api/v1/hop/ai/read-bill", headers=h, content_type="multipart/form-data",
                    data={"kind": "sale", "file": (io.BytesIO(b"\x89PNG fake"), "note.png", "image/png")})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["kind"] == "sale" and len(d["lines"]) == 3 and "HANDWRITTEN" in seen["prompt"]
