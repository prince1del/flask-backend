"""HoP Phase 3 rest — print theme, recycle, loans, FA, FY, tally."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_phase3_ext
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_doc_preview import delete_manual_party_document as delete
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    path = str(tmp_path / "hop_p3.sqlite3")
    ensure_hop_schema(path)
    return path


LINE = {"item_name": "Chair", "qty": 1, "unit": "Pcs", "rate": 1000, "tax_pct": 0}


def test_print_theme(db):
    with connect(db) as conn:
        t = hop_phase3_ext.set_print_theme(conn, WS, "forest")
        assert t["id"] == "forest" and t["accent"] == [22, 101, 52]
        assert hop_phase3_ext.get_print_theme(conn, WS)["id"] == "forest"


def test_recycle_document_and_product(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Recycle Co"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "lines": [LINE]})
        out = delete(conn, WS, inv["party_txn_id"])
        assert out.get("soft") is True
        bin_ = hop_phase3_ext.list_recycle_bin(conn, WS)
        assert any(d["id"] == inv["party_txn_id"] for d in bin_["docs"])
        hop_phase3_ext.restore_document(conn, WS, inv["party_txn_id"])
        bin2 = hop_phase3_ext.list_recycle_bin(conn, WS)
        assert not any(d["id"] == inv["party_txn_id"] for d in bin2["docs"])

        prod = create_product(conn, WS, {"name": "Gone", "selling_price": 10})
        hop_phase3_ext.soft_delete_product(conn, WS, prod["id"])
        assert any(p["id"] == prod["id"] for p in hop_phase3_ext.list_recycle_bin(conn, WS)["products"])
        hop_phase3_ext.restore_product(conn, WS, prod["id"])


def test_loans_and_fixed_assets_on_bs(db):
    from app import hop_reports

    with connect(db) as conn:
        loan = hop_phase3_ext.create_loan_account(conn, WS, {"name": "HDFC Term", "opening_balance": 50000})
        assert loan["outstanding"] == 50000
        asset = hop_phase3_ext.create_fixed_asset(conn, WS, {
            "name": "Van", "purchase_cost": 120000, "life_months": 60, "salvage_value": 0,
        })
        assert asset["book_value"] == 120000
        dep = hop_phase3_ext.run_depreciation(conn, WS, "2026-11-15")
        assert dep["count"] == 1
        bs = hop_reports.balance_sheet(conn, WS)
        names = [a["name"] for a in bs["assets"]] + [l["name"] for l in bs["liabilities"]]
        assert any("Fixed Assets" in n for n in names)
        assert any("Loan" in n for n in names)


def test_fy_close_blocks_writes(db):
    with connect(db) as conn:
        fy = hop_phase3_ext.ensure_current_fy(conn, WS)
        hop_phase3_ext.close_financial_year(conn, WS, int(fy["id"]))
        with pytest.raises(ValueError, match="closed"):
            hop_phase3_ext.assert_period_open(conn, WS, fy["start_date"])


def test_tally_exports(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Tally Co"})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-01", "lines": [LINE]})
        xml = hop_phase3_ext.build_tally_xml(conn, WS, "2026-11-01", "2026-11-30")
        assert b"ENVELOPE" in xml and b"VOUCHER" in xml
        csv_text = hop_phase3_ext.build_tally_daybook_csv(conn, WS, "2026-11-01", "2026-11-30")
        assert "Date" in csv_text.splitlines()[0]


def test_api_routes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    assert client.get("/api/v1/hop/print-theme", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/recycle-bin", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/loan-accounts", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/fixed-assets", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/financial-years", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/export/tally.xml", headers=headers).status_code == 200
