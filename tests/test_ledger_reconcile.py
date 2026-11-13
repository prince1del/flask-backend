"""Ledger check: missing CI / payment / credit note and amount differences (synthetic data)."""

from __future__ import annotations

from datetime import date

from app.services.ledger_reconcile import match_distributor, reconcile


def _dist():
    return {
        "distributor_id": 7, "distributor_name": "Acme Traders Pvt Ltd",
        "categories": [{
            "category": "Bed", "season": "AW26",
            "cis": [
                {"invoice_no": "1400000001", "invoice_date": "2026-08-10", "gross": 42000.0, "tracking_id": 1},
                {"invoice_no": "1400000002", "invoice_date": "2026-08-20", "gross": 21000.0, "tracking_id": 2},
                {"invoice_no": "1400000009", "invoice_date": "2026-08-25", "gross": 5000.0, "tracking_id": 9},
            ],
            "deposits": [
                {"amount": 30000.0, "payment_date": "2026-08-06"},
                {"amount": 7000.0, "payment_date": "2026-08-21"},
            ],
            "credit_notes": [{"id": 1, "cn_no": "CN-1", "cn_date": "2026-08-28", "amount": 500.0}],
        }],
        "unlinked_cis": [],
    }


def _lines():
    L = lambda kind, d, ref, amt: {"kind": kind, "date": d, "ref": ref, "so_ref": "", "text": "",  # noqa: E731
                                    "amount": amt, "signed": amt}
    return [
        L("invoice", date(2026, 8, 10), "1400000001", 42000.0),      # matches
        L("invoice", date(2026, 8, 20), "1400000002", 21500.0),      # amount differs
        L("invoice", date(2026, 8, 22), "1400000003", 9000.0),       # missing in NEXORA
        L("payment", date(2026, 8, 5), "UTR1", 30000.0),             # matches (1 day apart)
        L("payment", date(2026, 8, 27), "UTR2", 15000.0),            # missing in NEXORA
        L("credit_note", date(2026, 8, 29), "CN-1", 500.0),          # matches by number
        L("credit_note", date(2026, 8, 30), "CN-2", 1200.0),         # missing in NEXORA
    ]


def test_reconcile_finds_every_mismatch():
    out = reconcile(_lines(), _dist())
    titles = " | ".join(f["title"] for f in out["findings"])
    assert "Invoice 1400000003 is missing in NEXORA" in titles
    assert "Invoice 1400000002: amount differs" in titles
    assert "CI 1400000009 is in NEXORA but not in the ledger" in titles
    assert "Payment ₹15,000 on 27 Aug 2026 is missing in NEXORA" in titles
    assert "Payment ₹7,000 on 21 Aug 2026 is not in the ledger" in titles
    assert "Credit note CN-2 ₹1,200 is missing in NEXORA" in titles
    assert "CN-1" not in titles  # matched
    assert len(out["findings"]) == 6
    assert out["findings"][0]["level"] == "alert"  # missing items first
    comp = {c["label"]: c for c in out["compare"]}
    assert comp["Payments"]["ledger"] == 45000.0 and comp["Payments"]["nexora"] == 37000.0


def test_all_clear_and_name_match():
    lines = [l for l in _lines() if l["ref"] in ("1400000001", "UTR1", "CN-1")]
    d = _dist()
    d["categories"][0]["cis"] = d["categories"][0]["cis"][:1]
    d["categories"][0]["deposits"] = d["categories"][0]["deposits"][:1]
    assert reconcile(lines, d)["all_clear"] is True
    assert match_distributor("ACME TRADERS PRIVATE LIMITED", [d])["distributor_id"] == 7
    assert match_distributor("Other Party", [d]) is None


def test_reconcile_route_reads_sap_excel(tmp_path, monkeypatch):
    import io

    from openpyxl import Workbook

    from tests.test_watch import _app

    _app_obj, client, h = _app(tmp_path, monkeypatch)
    wb = Workbook()
    ws = wb.active
    ws.append(["Account", "Name 1", "Type1", "Reference", "Assignment", "Doc. Date", "LC amnt", "Text"])
    ws.append(["100", "Acme Traders", "Invoice", "1400000001", "102870001", "2026-08-10", 42000, ""])
    ws.append(["100", "Acme Traders", "Bank Receipt", "UTR1", "", "2026-08-12", -30000, ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/v1/distributor-ledger/reconcile", headers=h,
                    data={"file": (io.BytesIO(buf.getvalue()), "ledger.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()["data"]
    assert d["need_distributor"] is True and d["party"] == "Acme Traders"
    assert client.post("/api/v1/distributor-ledger/reconcile").status_code == 401


def test_sap_lines_ci_in_assignment_and_cd_tds_split():
    from app.services.ledger_reconcile import lines_from_statement

    st = {"lines": [
        {"type": "Sale Invoice", "date": date(2026, 8, 10), "reference": "802541158",
         "assignment": "1400000070", "text": "", "signed": 58111.2},
        {"type": "Credit Note", "date": date(2026, 8, 13), "reference": "102876077",
         "assignment": "102876077", "text": "3%CD - Acme - RFA0377 CI 1400000070", "signed": -1660.32},
        {"type": "Credit Note", "date": date(2026, 8, 10), "reference": "RFA 0377",
         "assignment": "RFA 0377", "text": "TDS:  Acme : RFA 0377 Bedsheet", "signed": -87.17},
        {"type": "Credit Note", "date": date(2026, 8, 11), "reference": "CN9", "assignment": "",
         "text": "Rate difference", "signed": -500.0},
    ]}
    ls = lines_from_statement(st)
    assert [l["kind"] for l in ls] == ["invoice", "cd", "tds", "credit_note"]
    assert ls[0]["ref"] == "1400000070"
