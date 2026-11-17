"""Bill numbers carry the business's own initials, never another company's (HOP stays only where it is used)."""
from __future__ import annotations

from pathlib import Path

from app.hop_db import connect, create_customer
from app.hop_doc_numbers import business_code
from app.hop_doc_preview import create_manual_party_document as create, next_manual_doc_number
from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema


def test_codes():
    assert business_code("Sharma Electronics Pvt Ltd") == "SE"
    assert business_code("Lumina") == "LUM"
    assert business_code("") == "INV"


def test_new_business_gets_its_own_prefix(tmp_path: Path):
    p = str(tmp_path / "px.sqlite3")
    ensure_hop_schema(p)
    ws = "sharma_shop"
    with connect(p) as conn:
        conn.execute("INSERT INTO hop_firm_profile (workspace_id, firm_name, updated_at) VALUES (?, 'Sharma Electronics', '2026')", (ws,))
        c = create_customer(conn, ws, {"company": "Buyer"})
        assert next_manual_doc_number(conn, ws, 1, "2026-11-18") == "SE/2026-27/1"
        assert next_manual_doc_number(conn, ws, 27, "2026-11-18") == "SEEST/2026-27/1"
        t = create(conn, ws, {"txn_type": 1, "customer_id": c["id"], "txn_date": "2026-11-18",
                              "lines": [{"item_name": "x", "qty": 1, "rate": 10}]})
        assert t["txn_number"] == "SE/2026-27/1"
        # House of Prizm keeps its HOP series
        assert next_manual_doc_number(conn, HOP_WORKSPACE_ID, 1, "2026-11-18").startswith("HOP/")
