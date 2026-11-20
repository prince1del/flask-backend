"""Distributor bulk import of the company's SO / CI / credit / debit notes (made-up firm, no row limit)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import hop_dist_bulk as B
from app import hop_dist_extra as X
from app import hop_distributor as D
from app.hop_db import connect
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def _xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Alpha Mills — SO register"])          # title row above the header
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "b.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        D.ensure_schema(conn); X.ensure_schema(conn); B.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills"})
        D.set_principal(conn, WS, a["id"], True)
        t = X.save_article(conn, WS, {"code": "T-1", "name": "Alpha Towel", "mrp": 300, "purchase_price": 150, "gst_pct": 5})
    return path, a["id"], t["id"]


SO_ROWS = [["SO No", "SO Date", "Material", "Material Description", "Order Qty", "Net Price", "Net Value"],
           ["5001", "01.11.2026", "T-1", "Alpha Towel", 10, 150, 1500],
           ["", "", "T-1", "Alpha Towel", 5, 150, 750],
           ["5002", "02.11.2026", "T-1", "Alpha Towel", 4, 150, 600]]


def test_so_sheet_no_limit_and_duplicates(firm):
    path, vid, _ = firm
    many = SO_ROWS + [[str(9000 + i), "03.11.2026", "T-1", "Alpha Towel", 1, 150, 150] for i in range(3500)]
    with connect(path) as conn:
        p = B.start(conn, WS, "so", None, [("so.xlsx", _xlsx(many))])
        assert p["vendor_id"] == vid and p["count"] == 3502 and p["new"] == 3502   # more than the old 3000 cap
        assert p["sheets"][0]["header_row"] == 2
        roles = {c["header"]: c["role"] for c in p["sheets"][0]["columns"]}
        assert roles["SO No"] == "doc_no" and roles["SO Date"] == "doc_date" and roles["Order Qty"] == "qty"
        first = B.doc_detail(conn, WS, p["job_id"], "5001")
        assert len(first["lines"]) == 2 and first["doc_date"] == "2026-11-01"   # blank number row = same SO
        r = B.commit(conn, WS, p["job_id"], skip=["9000"])
        assert r["saved"] == 3501 and r["skipped"] == 1
        # same sheet again: everything is already saved
        p2 = B.start(conn, WS, "so", vid, [("so.xlsx", _xlsx(SO_ROWS))])
        assert p2["already"] == 2 and p2["new"] == 0
        assert B.commit(conn, WS, p2["job_id"])["saved"] == 0


def test_ci_csv_in_zip_links_so_and_moves_stock(firm):
    path, vid, tid = firm
    csv_text = "Invoice No,Invoice Date,SO No,Article Code,Description,Qty,Rate,GST %\n" \
               "INV-77,05/11/2026,5001,T-1,Alpha Towel,10,150,5\nINV-77,05/11/2026,5001,T-1,Alpha Towel,5,150,5\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ci/nov.csv", csv_text)
        z.writestr("__MACOSX/._nov.csv", "junk")
    with connect(path) as conn:
        B.commit(conn, WS, B.start(conn, WS, "so", vid, [("so.xlsx", _xlsx(SO_ROWS))])["job_id"])
        p = B.start(conn, WS, "ci", vid, [("pack.zip", buf.getvalue())])
        assert p["count"] == 1 and p["files_waiting"] == 0
        cols = {c["header"]: c["role"] for c in p["sheets"][0]["columns"]}
        assert cols["Invoice No"] == "doc_no" and cols["SO No"] == "ref_no"
        stock0 = conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (tid,)).fetchone()[0] or 0
        r = B.commit(conn, WS, p["job_id"])
        assert r["saved"] == 1 and r["linked_to_so"] == 1
        bill = conn.execute("SELECT id, party_bill_no FROM hop_party_transactions WHERE txn_type=2 AND party_bill_no='INV-77'").fetchone()
        assert bill
        stock1 = conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (tid,)).fetchone()[0]
        assert stock1 - stock0 == 15
        with pytest.raises(ValueError):
            B.commit(conn, WS, p["job_id"])            # a job is saved once


def test_credit_and_debit_notes_amount_only(firm):
    path, vid, tid = firm
    rows = [["Credit Note No", "Date", "Against Invoice", "Particulars", "Amount"],
            ["CN-1", "2026-11-10", "INV-77", "Scheme discount", 500]]
    with connect(path) as conn:
        p = B.start(conn, WS, "cn", vid, [("cn.xlsx", _xlsx(rows))])
        cols = {c["header"]: c["role"] for c in p["sheets"][0]["columns"]}
        assert cols["Credit Note No"] == "doc_no" and cols["Against Invoice"] == "ref_no"
        stock0 = conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (tid,)).fetchone()[0]
        assert B.commit(conn, WS, p["job_id"])["saved"] == 1
        cn = conn.execute("SELECT total_amount, party_bill_no FROM hop_party_transactions WHERE txn_type=16").fetchone()
        assert cn and cn[1] == "CN-1" and abs(float(cn[0]) - 500) < 1
        assert conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (tid,)).fetchone()[0] == stock0   # money only
        dn = [["Debit Note No", "Date", "Particulars", "Amount"], ["DN-9", "2026-11-11", "Freight", 200]]
        p = B.start(conn, WS, "dn", vid, [("dn.xlsx", _xlsx(dn))])
        assert B.commit(conn, WS, p["job_id"])["saved"] == 1
        assert conn.execute("SELECT 1 FROM hop_party_transactions WHERE txn_type=2 AND party_bill_no='DN-9'").fetchone()


def test_unknown_columns_fixed_by_user_and_company_pick(firm):
    path, vid, _ = firm
    rows = [["Ref", "When", "Thing", "Pcs", "Rs"], ["A1", "01-11-2026", "Alpha Towel", 2, 150]]
    with connect(path) as conn:
        b = create_vendor(conn, WS, {"company": "Beta Textiles"})
        D.set_principal(conn, WS, b["id"], True)
        p = B.start(conn, WS, "so", None, [("odd.xlsx", _xlsx(rows))])
        assert p["vendor_id"] is None and len(p["companies"]) == 2     # two companies → user picks
        sh = p["sheets"][0]
        assert sh["error"] and p["count"] == 0                           # "Ref" not known as the SO number
        idx = {c["header"]: str(c["i"]) for c in sh["columns"]}
        p = B.set_roles(conn, WS, p["job_id"], sh["sig"], {idx["Ref"]: "doc_no", idx["When"]: "doc_date",
                                                           idx["Thing"]: "name", idx["Pcs"]: "qty", idx["Rs"]: "rate"})
        assert p["count"] == 1
        with pytest.raises(ValueError):
            B.commit(conn, WS, p["job_id"])                              # no company yet
        B.set_company(conn, WS, p["job_id"], vid)
        assert B.commit(conn, WS, p["job_id"])["saved"] == 1
        # the layout is remembered: the next sheet like it maps itself
        assert B.start(conn, WS, "so", vid, [("odd2.xlsx", _xlsx([rows[0], ["A2", "02-11-2026", "Alpha Towel", 1, 150]]))])["count"] == 1


def test_pdf_waits_for_ai_and_bad_file(firm):
    path, vid, _ = firm
    with connect(path) as conn:
        p = B.start(conn, WS, "ci", vid, [("ci.pdf", b"%PDF-1.4 fake")])
        assert p["files_waiting"] == 1 and p["ai_reads_needed"] == 1 and p["count"] == 0
        with pytest.raises(ValueError):
            B.start(conn, WS, "ci", vid, [("notes.docx", b"xx")])
