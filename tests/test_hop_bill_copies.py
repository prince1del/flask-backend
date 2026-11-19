"""Supplier bill copies on purchase bills + bulk bill PDF (made-up company)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import hop_app_settings, hop_bill_copies as BC
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 1600), "white").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def books(tmp_path: Path):
    path = str(tmp_path / "copies.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        ven = create_vendor(conn, WS, {"company": "Cloth Mill"})
        cust = create_customer(conn, WS, {"company": "Hotel B"})
        pb = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ven["id"], "txn_date": "2026-11-02",
                               "party_bill_no": "CM-77",
                               "lines": [{"item_name": "Cloth", "qty": 2, "rate": 500, "tax_pct": 5}]})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-05",
                                "lines": [{"item_name": "Cloth", "qty": 1, "rate": 900, "tax_pct": 5}]})
    return path, pb["party_txn_id"], inv["party_txn_id"]


def test_copy_pending_until_added_and_setting_switch(books):
    path, pb, _ = books
    with connect(path) as conn:
        p = BC.pending(conn, WS)
        assert p["on"] and [b["id"] for b in p["bills"]] == [pb] and p["bills"][0]["party_name"] == "Cloth Mill"
        out = BC.add_copy(conn, path, WS, pb, _png(), "bill.png")
        assert len(out["copies"]) == 1 and out["copies"][0]["file_kind"] == "jpg"
        assert BC.pending(conn, WS)["count"] == 0
        cid = out["copies"][0]["id"]
        f, mime = BC.get_copy(conn, path, WS, pb, cid)
        assert mime == "image/jpeg" and Path(f).exists()
        BC.remove_copy(conn, path, WS, pb, cid)
        assert BC.pending(conn, WS)["count"] == 1
        hop_app_settings.save(conn, WS, {"purchase_copy_track": False})
        assert BC.pending(conn, WS) == {"on": False, "count": 0, "bills": []}


def test_pdf_copy_kept_as_pdf_and_bad_file_refused(books):
    path, pb, _ = books
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    with connect(path) as conn:
        out = BC.add_copy(conn, path, WS, pb, bytes(pdf.output()), "bill.pdf")
        assert out["copies"][0]["file_kind"] == "pdf"
        with pytest.raises(ValueError):
            BC.add_copy(conn, path, WS, pb, b"hello", "x.txt")


def test_bulk_pdf_with_and_without_copies(books):
    import pypdfium2 as pdfium

    path, pb, _ = books
    with connect(path) as conn:
        BC.add_copy(conn, path, WS, pb, _png(), "bill.png")
        s = BC.bulk_summary(conn, WS, "2026-11-01", "2026-11-30", ["sale", "purchase"])
        assert s["count"] == 2 and s["with_copy"] == 1 and s["copies"] == 1
        plain, n = BC.build_bulk_pdf(conn, path, WS, "2026-11-01", "2026-11-30", ["sale", "purchase"], False)
        with_c, _ = BC.build_bulk_pdf(conn, path, WS, "2026-11-01", "2026-11-30", ["sale", "purchase"], True)
        assert n == 2
        assert len(pdfium.PdfDocument(with_c)) == len(pdfium.PdfDocument(plain)) + 1
        only_sale, n1 = BC.build_bulk_pdf(conn, path, WS, "2026-11-01", "2026-11-30", ["sale"], True)
        assert n1 == 1
        with pytest.raises(ValueError):
            BC.build_bulk_pdf(conn, path, WS, "2026-12-01", "2026-12-31", ["sale"], False)
        with pytest.raises(ValueError):
            BC.bulk_summary(conn, WS, "2026-11-30", "2026-11-01", ["sale"])
