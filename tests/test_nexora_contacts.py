"""Ask Nexora contacts: finds the number in this company's data only; clear match → phone action."""
from __future__ import annotations

import sqlite3

from app.services import nexora_contacts as C


def _db(tmp_path):
    p = str(tmp_path / "c.sqlite3")
    with sqlite3.connect(p) as c:
        c.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, phone TEXT, workspace_id TEXT);
            CREATE TABLE hop_customers (id INTEGER PRIMARY KEY, company TEXT, contact_person TEXT, mobile TEXT, workspace_id TEXT);
        """)
        c.execute("INSERT INTO users (username, full_name, phone, workspace_id) VALUES ('ravi', 'Ravi Kumar', '98765 43210', 'ws1')")
        c.execute("INSERT INTO hop_customers (company, contact_person, mobile, workspace_id) VALUES ('Sharma Traders', 'Anil', '9811111111', 'ws1')")
        c.execute("INSERT INTO hop_customers (company, contact_person, mobile, workspace_id) VALUES ('Sharma Traders', 'X', '9822222222', 'other_ws')")
    return p


def test_call_prepared_for_clear_match_only_in_own_company(tmp_path):
    p = _db(tmp_path)
    out = C.contact_action(p, ["ws1"], "sharma tradrs", "call")
    assert out["device_action"] == {"kind": "call", "name": "Sharma Traders", "phone": "919811111111",
                                    "display_phone": "9811111111", "message": ""}
    assert all(m["phone"] != "9822222222" for m in out["matches"])  # other company's number never shown


def test_whatsapp_message_and_not_found(tmp_path):
    p = _db(tmp_path)
    out = C.contact_action(p, ["ws1"], "Ravi", "whatsapp", "Payment bhej do")
    assert out["device_action"]["kind"] == "whatsapp" and out["device_action"]["message"] == "Payment bhej do"
    assert C.contact_action(p, ["ws1"], "Nobody Here", "call")["found"] is False
