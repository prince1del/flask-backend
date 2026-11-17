"""Payment reminders + daily backup email: groups by due date, sends only what the owner switched on,
does not remind the same customer again before the repeat gap (email sending is faked)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app import hop_app_settings as S, hop_reminders as R
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

TODAY = date(2026, 11, 17)


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    p = str(tmp_path / "rem.sqlite3")
    ensure_hop_schema(p)
    sent = []
    from app.services import outbound_mail

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setattr(outbound_mail, "send_plain_email",
                        lambda **kw: (sent.append(kw), (True, "sent"))[1])
    with connect(p) as conn:
        a = create_customer(conn, WS, {"company": "Hotel Late", "email": "late@hotel.test", "mobile": "9876543210"})
        b = create_customer(conn, WS, {"company": "Shop Today"})
        c = create_customer(conn, WS, {"company": "Cafe Soon", "email": "soon@cafe.test"})
        for cust, due in ((a, TODAY - timedelta(days=5)), (b, TODAY), (c, TODAY + timedelta(days=1)),
                          (c, TODAY + timedelta(days=30))):
            create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-10-01",
                              "due_date": due.isoformat(), "lines": [{"item_name": "x", "qty": 1, "rate": 1000}]})
        conn.execute("UPDATE hop_party_transactions SET due_date=CASE party_name "
                     "WHEN 'Hotel Late' THEN ? WHEN 'Shop Today' THEN ? ELSE due_date END",
                     ((TODAY - timedelta(days=5)).isoformat(), TODAY.isoformat()))
        conn.execute("INSERT INTO hop_firm_profile (workspace_id, firm_name, email, updated_at) "
                     "VALUES (?, 'Test Firm', 'owner@firm.test', '2026-11-17') "
                     "ON CONFLICT(workspace_id) DO UPDATE SET email='owner@firm.test', firm_name='Test Firm'", (WS,))
        conn.commit()
    return p, sent


def test_groups_and_reminder_text(db):
    p, _ = db
    with connect(p) as conn:
        dl = R.due_list(conn, WS, TODAY)
        assert [x["party"] for x in dl["overdue"]] == ["Hotel Late"] and dl["overdue"][0]["days"] == -5
        assert [x["party"] for x in dl["today"]] == ["Shop Today"]
        assert [x["party"] for x in dl["soon"]] == ["Cafe Soon"]  # the bill due in 30 days is not listed
        assert "Hotel Late" in dl["overdue"][0]["text"] and dl["overdue"][0]["whatsapp_url"].startswith("https://wa.me/")


def test_nothing_sent_unless_switched_on(db):
    p, sent = db
    with connect(p) as conn:
        res = R.run_for_workspace(conn, WS, TODAY)
    assert res["customers"] == 0 and not res["summary"] and not res["backup"] and sent == []


def test_customers_summary_backup_and_repeat_gap(db):
    p, sent = db
    with connect(p) as conn:
        S.save(conn, WS, {"reminder_email_customers": True, "reminder_owner_summary": True, "backup_email_daily": True})
        res = R.run_for_workspace(conn, WS, TODAY)
        assert res["customers"] == 2 and res["summary"] and res["backup"]  # Shop Today has no email
        to = [m["to"] for m in sent]
        assert "late@hotel.test" in to and "soon@cafe.test" in to and to.count("owner@firm.test") == 2
        backup = next(m for m in sent if m["subject"].startswith("NEXORA daily backup"))
        assert backup["attachments"][0][0].endswith(".kunwar")
        sent.clear()
        res = R.run_for_workspace(conn, WS, TODAY)  # same day again: customers not reminded twice
        assert res["customers"] == 0


def test_bill_without_due_date_uses_bill_date(db):
    p, _ = db
    with connect(p) as conn:
        conn.execute("UPDATE hop_party_transactions SET due_date=NULL, txn_date=? WHERE party_name='Shop Today'",
                     (TODAY.isoformat(),))
        dl = R.due_list(conn, WS, TODAY)
        assert [x["party"] for x in dl["today"]] == ["Shop Today"] and dl["today"][0]["due_date"] == TODAY.isoformat()
