"""Vyapar → NEXORA rest of the books: banks, payment modes, transfers, cheques, loans, links, units, groups.
Uses a tiny Vyapar-shaped database (same table / column names as a real backup)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import hop_app_settings, hop_ops
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
from app.services.vyapar_money_import import import_money_and_masters


def _vyapar() -> sqlite3.Connection:
    s = sqlite3.connect(":memory:")
    s.executescript("""
    CREATE TABLE kb_paymentTypes (paymentType_id INTEGER, paymentType_type TEXT, paymentType_name TEXT,
        paymentType_bankName TEXT, paymentType_accountNumber TEXT, paymentType_opening_balance REAL,
        paymentType_opening_date TEXT, pt_bank_ifsc_code TEXT);
    INSERT INTO kb_paymentTypes VALUES (1,'CASH','Cash','', '', 500, '2025-04-01', ''),
        (2,'CHEQUE','Cheque','', '', 0, '', ''), (5,'BANK','HDFC Current','HDFC','5010','1000','2025-04-01','HDFC0001'),
        (6,'BANK','SBI Savings','SBI','3020',0,'2025-04-01','SBIN0001');
    CREATE TABLE txn_payment_mapping (id INTEGER, payment_id INTEGER, cheque_id INTEGER, txn_id INTEGER, amount REAL,
        payment_reference TEXT);
    INSERT INTO txn_payment_mapping VALUES (1,5,NULL,101,700,''), (2,1,NULL,102,200,''), (3,2,9,103,300,'CHQ123'),
        (4,5,NULL,104,0,'');
    CREATE TABLE kb_cheque_status (cheque_id INTEGER, cheque_txn_id INTEGER, cheque_current_status INTEGER,
        cheque_transfer_date TEXT, cheque_close_desc TEXT, transferred_To_Account INTEGER, check_creation_date TEXT,
        check_modification_date TEXT, cheque_closed_txn_ref_id INTEGER, cheque_issued_date TEXT);
    INSERT INTO kb_cheque_status VALUES (9,103,1,'2025-05-10','',6,'','',NULL,'2025-05-01');
    CREATE TABLE kb_bank_adjustments (bank_adj_id INTEGER, bank_adj_bank_id INTEGER, bank_adj_type INTEGER,
        bank_adj_amount REAL, bank_adj_date TEXT, bank_adj_description TEXT, bank_adj_to_bank_id INTEGER);
    INSERT INTO kb_bank_adjustments VALUES (1,5,25,400,'2025-05-15','to SBI',6), (2,6,99,50,'2025-05-16','odd',NULL);
    CREATE TABLE kb_cash_adjustments (cash_adj_id INTEGER, cash_adj_type INTEGER, cash_adj_amount REAL,
        cash_adj_date TEXT, cash_adj_description TEXT);
    CREATE TABLE loan_accounts (loan_account_id INTEGER, loan_account_name TEXT, lender TEXT, account_number TEXT,
        firm_id INTEGER, loan_desc TEXT, opening_bal REAL, opening_date TEXT, interest_rate REAL);
    INSERT INTO loan_accounts VALUES (1,'Car loan','HDFC Bank','x',1,'',90000,'2025-04-01',9.5);
    CREATE TABLE loan_transactions (loan_txn_id INTEGER, loan_account_id INTEGER, loan_txn_type INTEGER,
        principal_amount REAL, interest_amount REAL, payment_acc_id INTEGER, txn_date TEXT);
    INSERT INTO loan_transactions VALUES (1,1,40,0,0,1,'2025-04-01'), (2,1,42,3000,700,5,'2025-05-05');
    CREATE TABLE kb_txn_links (txn_links_id INTEGER, txn_links_txn_1_id INTEGER, txn_links_txn_2_id INTEGER,
        txn_links_amount REAL, txn_links_txn_1_type INTEGER, txn_links_txn_2_type INTEGER);
    INSERT INTO kb_txn_links VALUES (1,105,101,700,1,3);
    CREATE TABLE kb_item_units (unit_id INTEGER, unit_name TEXT, unit_short_name TEXT);
    INSERT INTO kb_item_units VALUES (1,'Pieces','PCS'), (2,'Box','BOX');
    CREATE TABLE kb_item_units_mapping (unit_mapping_id INTEGER, base_unit_id INTEGER, secondary_unit_id INTEGER,
        conversion_rate REAL);
    INSERT INTO kb_item_units_mapping VALUES (1,1,2,10);
    CREATE TABLE kb_items (item_name TEXT, base_unit_id INTEGER, secondary_unit_id INTEGER, unit_mapping_id INTEGER);
    INSERT INTO kb_items VALUES ('Curtain hook', 1, 2, 1);
    CREATE TABLE kb_party_groups (party_group_id INTEGER, party_group_name TEXT);
    INSERT INTO kb_party_groups VALUES (1,'General'), (7,'Tailor');
    CREATE TABLE kb_names (full_name TEXT, name_group_id INTEGER);
    INSERT INTO kb_names VALUES ('Raju Tailors', 7);
    CREATE TABLE kb_payment_terms (payment_term_id INTEGER, term_name TEXT, term_days INTEGER, is_default INTEGER);
    INSERT INTO kb_payment_terms VALUES (1,'Net 30',30,1);
    CREATE TABLE audit_trails (id INTEGER); INSERT INTO audit_trails VALUES (1),(2);
    """)
    return s


@pytest.fixture
def books(tmp_path: Path):
    p = str(tmp_path / "vm.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Raju Tailors"})
        v = hop_ops.create_vendor(conn, WS, {"company": "Fabric Mill"})
        hop_ops.create_product(conn, WS, {"name": "Curtain hook"})
        conn.execute("UPDATE hop_products SET unit=NULL WHERE workspace_id=?", (WS,))
        # Vyapar txns already imported as party transactions (source_txn_id = Vyapar txn id)
        for src, ty, party in ((101, 3, c), (102, 2, v), (103, 3, c), (104, 1, c), (105, 1, c)):
            body = {"txn_type": ty, "txn_date": "2025-05-01", "lines": [{"item_name": "x", "qty": 1, "rate": 700}]}
            body["vendor_id" if ty == 2 else "customer_id"] = party["id"]
            if ty == 3:
                body = {"txn_type": 3, "customer_id": c["id"], "txn_date": "2025-05-01", "amount": 700,
                        "payment_account_id": None}
            try:
                t = create(conn, WS, body)
            except ValueError:
                t = create(conn, WS, {**body, "lines": [{"item_name": "x", "qty": 1, "rate": 700}]})
            conn.execute("UPDATE hop_party_transactions SET source_txn_id=? WHERE id=?", (src, t["party_txn_id"]))
        conn.execute("DELETE FROM hop_bank_entries WHERE workspace_id=?", (WS,))
        conn.commit()
    return p


def _bal(conn, name):
    return conn.execute("SELECT current_balance FROM hop_bank_accounts WHERE workspace_id=? AND name=?", (WS, name)).fetchone()[0]


def test_money_links_masters_and_run_twice(books):
    with connect(books) as conn:
        out = import_money_and_masters(_vyapar(), conn, WS)
        assert out["bank_accounts"] == 2 and out["payments_posted"] == 2 and out["cheques"] == 1
        assert out["transfers"] == 1 and out["loans"] == 1 and out["loan_entries"] == 2 and out["payment_links"] == 1
        # HDFC: opening 1000 + payment-in 700 − transfer 400 − EMI 3000 − interest 700
        assert _bal(conn, "HDFC Current") == pytest.approx(1000 + 700 - 400 - 3700)
        # SBI: + transfer 400 + cheque deposited 300
        assert _bal(conn, "SBI Savings") == pytest.approx(700)
        # Cash: opening 500 − purchase paid 200
        assert _bal(conn, "Cash in hand") == pytest.approx(300)
        chq = conn.execute("SELECT status, cheque_no, account_id FROM hop_cheques WHERE workspace_id=?", (WS,)).fetchone()
        assert chq[0] == "closed" and chq[1] == "CHQ123"
        assert any("type 99" in x for x in out["skipped"]) and any("change log" in x for x in out["skipped"])
        p = conn.execute("SELECT unit, alt_unit, alt_unit_factor FROM hop_products WHERE workspace_id=?", (WS,)).fetchone()
        assert tuple(p) == ("PCS", "BOX", 10)
        assert conn.execute("SELECT customer_type FROM hop_customers WHERE company='Raju Tailors'").fetchone()[0] == "Tailor"
        s = hop_app_settings.get_all(conn, WS)
        assert "Tailor" in s["party_groups"] and int(s["default_due_days"]) == 30
        # second run: nothing added twice, balances unchanged
        again = import_money_and_masters(_vyapar(), conn, WS)
        assert again["payments_posted"] == again["transfers"] == again["cheques"] == again["loan_entries"] == 0
        assert again["payment_links"] == 0 and _bal(conn, "SBI Savings") == pytest.approx(700)
