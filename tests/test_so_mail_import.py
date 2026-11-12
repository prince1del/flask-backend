"""SO mail import: parser (both Bombay Dyeing templates), compare, staging, confirm.

All numbers are synthetic.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.services import so_mail_import as smi
from app.services.so_mail_parser import parse_so_mail

BED_HTML = """
<p>please arrange for payment on or before Monday, August 3, 2026.</p>
<table>
<tr><td>Name</td><td>City</td><td>ORD QTY</td><td>BASIC VALUE</td><td>Gross Value</td>
<td>Cash discount</td><td>Cash Discount</td><td>TDS 194Q @0.1%</td><td>Total</td></tr>
<tr><td>Acme</td><td>Pune</td><td>300</td><td>1,00,000.00</td><td>1,05,000.00</td>
<td>3.00%</td><td>3,150.00</td><td>-</td><td>1,01,850.00</td></tr>
</table>
<table>
<tr><td>Name</td><td>City</td><td>SO</td><td>Qty</td><td>Basic Value</td><td>Gross Value</td></tr>
<tr><td>Acme</td><td>Pune</td><td>102870001</td><td>100</td><td>40,000.00</td><td>42,000.00</td></tr>
<tr><td></td><td></td><td>102870002</td><td>200</td><td>60,000.00</td><td>63,000.00</td></tr>
<tr><td>Acme Total</td><td></td><td></td><td>300</td><td>1,00,000.00</td><td>1,05,000.00</td></tr>
</table>
"""

TOWEL_HTML = """
<p>please arrange payment on or before Monday, August 31, 2026.</p>
<table>
<tr><td>Name</td><td>SO</td><td>Qty</td><td>Basic Value</td><td>Gross Value</td><td>Cash discount</td>
<td>Cash Discount</td><td>TDS 194Q @0.1%</td><td>Total</td></tr>
<tr><td>Zed</td><td>102870011</td><td>10</td><td>10,000.00</td><td>10,500.00</td><td>3.00%</td>
<td>300.00</td><td>10.50</td><td>10,189.50</td></tr>
<tr><td></td><td>102870012</td><td>20</td><td>20,000.00</td><td>21,000.00</td><td>3.00%</td>
<td>600.00</td><td>21.00</td><td>20,379.00</td></tr>
<tr><td>Zed Total</td><td></td><td>30</td><td>30,000.00</td><td>31,500.00</td><td>900.00</td>
<td>31.50</td><td>30,568.50</td></tr>
</table>
"""

TOWEL_NO_TDS_HTML = """
<table>
<tr><td>Name</td><td>SO</td><td>Qty</td><td>Basic Value</td><td>Gross Value</td><td>Cash discount</td>
<td>Cash Discount</td><td>Total</td></tr>
<tr><td>Zed</td><td>102870021</td><td>5</td><td>5,000.00</td><td>5,250.00</td><td>3.00%</td>
<td>157.50</td><td>5,092.50</td></tr>
<tr><td>Zed Total</td><td></td><td>5</td><td>5,000.00</td><td>5,250.00</td><td>157.50</td><td>5,092.50</td></tr>
</table>
"""


def test_parse_bedsheet_template_summary_and_so_rows():
    r = parse_so_mail("Fwd: RFA 0001 BEDSHEET orders (Jun-26) - for Cash payment", BED_HTML)
    assert r["category"] == "Bed"
    assert r["due_date"] == "2026-08-03"
    assert r["tds_rate"] == 0.1
    assert [x["so_number"] for x in r["so_rows"]] == ["102870001", "102870002"]
    s = r["summary"]
    assert (s["qty"], s["basic"], s["gross"]) == (300.0, 100000.0, 105000.0)
    assert s["cd_percent"] == 3.0 and s["cd_amount"] == 3150.0
    assert s["total"] == 101850.0
    assert s["tds_applied"] is False  # "-" in the TDS column


def test_parse_towel_template_with_tds():
    r = parse_so_mail("RFA 0002 TOWEL orders - for Cash payment", TOWEL_HTML)
    assert r["category"] == "Bath" and r["due_date"] == "2026-08-31"
    s = r["summary"]
    assert (s["qty"], s["basic"], s["cd_amount"], s["tds_amount"], s["total"]) == (
        30.0, 30000.0, 900.0, 31.5, 30568.5)
    assert s["cd_percent"] == 3.0 and s["tds_applied"] is True
    assert r["so_rows"][0]["tds_amount"] == 10.5


def test_parse_towel_template_without_tds_column():
    s = parse_so_mail("RFA TOWEL", TOWEL_NO_TDS_HTML)["summary"]
    assert (s["cd_amount"], s["total"]) == (157.5, 5092.5)
    assert s["tds_applied"] is False


def _pack(rows):
    return {
        "so_summary": [
            {"so_number": so, "total_qty": q, "net_amount": n, "total_amount": round(n * 1.05, 2),
             "buyer_name": "ACME TRADERS, PUNE"}
            for so, q, n in rows
        ],
        "meta": {"total_qty": sum(q for _, q, _ in rows), "net_amount": sum(n for _, _, n in rows),
                 "total_amount": round(sum(n for _, _, n in rows) * 1.05, 2)},
        "line_detail": [{"so_number": so} for so, _, _ in rows],
    }


def test_compare_flags_so_where_pdf_differs_from_mail():
    parsed = parse_so_mail("RFA BEDSHEET", BED_HTML)
    same = smi.compare_mail_vs_pack(parsed, _pack([("102870001", 100, 40000.0), ("102870002", 200, 60000.0)]))
    assert same["all_match"] is True
    diff = smi.compare_mail_vs_pack(parsed, _pack([("102870001", 100, 40000.0), ("102870002", 248, 72000.0)]))
    assert diff["all_match"] is False
    assert [r["status"] for r in diff["rows"]] == ["match", "mismatch"]
    assert "102870002" in diff["diffs"][0]


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE master_distributors (id INTEGER PRIMARY KEY, workspace_id TEXT, firm_name TEXT,
            firm_nick_name TEXT, name TEXT);
        INSERT INTO master_distributors VALUES (7, 'ws', 'Acme Traders', 'Acme', 'Ravi');
        CREATE TABLE fo_so_match_so_index (so_number TEXT PRIMARY KEY, run_id INTEGER, user_id INTEGER,
            filled_order_id INTEGER, created_at TEXT);
        """
    )
    return conn


def _mail(html=BED_HTML):
    return {"subject": "RFA 0001 BEDSHEET orders - for Cash payment", "email_date": "2026-07-27T12:00:00",
            "html": html, "plain": "", "pdfs": [("ACME 102870001.pdf", b"%PDF"), ("ACME 102870002.pdf", b"%PDF")]}


@pytest.fixture
def staged(monkeypatch):
    conn = _db()
    monkeypatch.setattr(
        smi, "candidate_filled_orders",
        lambda c, u, d, cat: [{"id": 55, "category": "Bed", "season": "AW26",
                               "source_filename": "acme.xlsx", "total_piece_qty": 300}],
    )
    analyze = lambda pdfs, label: _pack([("102870001", 100, 40000.0), ("102870002", 200, 60000.0)])
    res = smi.stage_mail(conn, user_id=1, workspace_id="ws", message_id="m1", mail=_mail(), analyze_pdfs=analyze)
    return conn, res


def test_stage_mail_builds_review_proposal(staged):
    conn, res = staged
    assert res["status"] == "review" and res["new"] is True
    rec = smi.get_import(conn, 1, "ws", res["id"])
    p = rec["proposal"]
    assert p["distributor"]["id"] == 7
    assert p["filled_order_ids"] == [55] and p["season"] == "AW26" and p["category"] == "Bed"
    assert p["cd_percent"] == 3.0 and p["tds_applicable"] is False and p["due_date"] == "2026-08-03"
    assert rec["compare"]["all_match"] is True


def test_stage_is_idempotent_per_message(staged):
    conn, res = staged
    again = smi.stage_mail(conn, user_id=1, workspace_id="ws", message_id="m1", mail=_mail(),
                           analyze_pdfs=lambda p, l: _pack([]))
    assert again == {"id": res["id"], "status": "review", "new": False}
    assert len(smi.list_imports(conn, 1, "ws")) == 1


def test_stage_marks_already_imported(monkeypatch):
    conn = _db()
    conn.execute("INSERT INTO fo_so_match_so_index VALUES ('102870001', 1, 1, 55, 'x')")
    conn.execute("INSERT INTO fo_so_match_so_index VALUES ('102870002', 1, 1, 55, 'x')")
    monkeypatch.setattr(smi, "candidate_filled_orders", lambda *a: [{"id": 55, "season": "AW26"}])
    res = smi.stage_mail(conn, user_id=1, workspace_id="ws", message_id="m2", mail=_mail(),
                         analyze_pdfs=lambda p, l: _pack([("102870001", 100, 40000.0), ("102870002", 200, 60000.0)]))
    assert res["status"] == "already_imported"


class _FakeDb:
    def __init__(self):
        self.calls = []

    def set_distributor_cd_rate(self, *a):
        self.calls.append(("cd", a))

    def set_distributor_tds_applicable(self, *a):
        self.calls.append(("tds", a))


def test_confirm_runs_match_then_writes_payment_settings(staged):
    conn, res = staged
    seen = []

    def match(pack, fo_id):
        seen.append((len(pack["so_summary"]), fo_id))
        return {"status_code": 200, "body": {"success": True}}

    db = _FakeDb()
    out = smi.confirm_import(conn, user_id=1, workspace_id="ws", import_id=res["id"], match_fn=match, db=db)
    assert seen == [(2, 55)]
    assert out["payment"] == {"cd_percent": 3.0, "tds_applicable": False, "due_date": "2026-08-03"}
    assert ("cd", (1, 7, "AW26", 3.0, "Bed")) in db.calls
    assert ("tds", (1, 7, "AW26", "Bed", False)) in db.calls
    assert smi.due_dates_for_user(conn, 1)[(7, "AW26", "Bed")]["due_date"] == "2026-08-03"
    assert smi.get_import(conn, 1, "ws", res["id"])["status"] == "confirmed"
    with pytest.raises(ValueError):
        smi.confirm_import(conn, user_id=1, workspace_id="ws", import_id=res["id"], match_fn=match, db=db)


def test_confirm_stops_when_order_desk_match_fails(staged):
    conn, res = staged
    db = _FakeDb()
    with pytest.raises(RuntimeError):
        smi.confirm_import(conn, user_id=1, workspace_id="ws", import_id=res["id"], db=db,
                           match_fn=lambda p, f: {"status_code": 400, "body": {"success": False,
                                                                                 "error": {"message": "no"}}})
    assert db.calls == []  # nothing written to payments
    assert smi.get_import(conn, 1, "ws", res["id"])["status"] == "review"


def test_reject_and_other_users_isolated(staged):
    conn, res = staged
    assert smi.get_import(conn, 2, "ws", res["id"]) is None
    assert smi.reject_import(conn, user_id=2, workspace_id="ws", import_id=res["id"]) is False
    assert smi.reject_import(conn, user_id=1, workspace_id="ws", import_id=res["id"]) is True
