import os
import sqlite3
import tempfile
import time
from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_crud_flow(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    db = CentralizedDB(str(db_path))

    record_id = db.add_record("Ada Lovelace", "ada@example.com", "Research")
    assert record_id == 1

    records = db.list_records()
    assert len(records) == 1
    assert records[0]["name"] == "Ada Lovelace"

    updated = db.update_record(record_id, department="Engineering")
    assert updated is True
    assert db.get_record(record_id)["department"] == "Engineering"

    deleted = db.delete_record(record_id)
    assert deleted is True
    assert db.count_records() == 0


def test_uses_environment_database_url(tmp_path, monkeypatch):
    target_path = tmp_path / "cloud.sqlite3"
    monkeypatch.setenv("CLOUD_DATABASE_URL", f"sqlite:///{target_path}")

    db = CentralizedDB()

    assert target_path.exists() is True
    assert db.count_records() == 0


def test_backup_and_restore_database(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    restored_path = tmp_path / "restored.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"

    db = CentralizedDB(str(source_path))
    record_id = db.add_record("Backup Test", "backup@example.com", "Ops")

    backup_file = db.backup_database(backup_path)
    assert backup_file.exists()

    restored_db = CentralizedDB(str(restored_path))
    restored_db.restore_database(backup_file, overwrite=True)

    restored_record = restored_db.get_record(record_id)
    assert restored_record is not None
    assert restored_record["name"] == "Backup Test"


def test_audit_logs_are_recorded(tmp_path):
    db = CentralizedDB(str(tmp_path / "audit.sqlite3"))
    db.add_record("Audit User", "audit@example.com", "Audit")

    logs = db.list_audit_logs(limit=10)
    assert any(item["table_name"] == "records" and item["action"] == "create" for item in logs)


def test_cleanup_temp_uploads_removes_old_files(tmp_path):
    db = CentralizedDB(str(tmp_path / "cleanup.sqlite3"))
    temp_dir = tmp_path / "uploads"
    temp_dir.mkdir()
    old_file = temp_dir / "old-upload.tmp"
    old_file.write_text("stale")
    os.utime(old_file, (time.time() - 7200, time.time() - 7200))

    removed = db.cleanup_temp_uploads(temp_dir, max_age_hours=1)

    assert removed == 1
    assert not old_file.exists()


def test_distributor_order_upload_records_are_persisted(tmp_path):
    db = CentralizedDB(str(tmp_path / "uploads.sqlite3"))

    first_id = db.save_distributor_order_upload(
        verification_session_id="session-1",
        distributor_name="Alpha Traders",
        stage_key="filled_file",
        file_type="excel",
        filename="alpha_traders_filled.xlsx",
        file_path="instance/verification_uploads/session-1/filled_alpha_traders_filled.xlsx",
        metadata={"source": "test"},
    )
    second_id = db.save_distributor_order_upload(
        verification_session_id="session-1",
        distributor_name="Alpha Traders",
        stage_key="sales_order_file",
        file_type="pdf",
        filename="alpha_so.pdf",
        file_path="instance/verification_uploads/session-1/sales_alpha_so.pdf",
    )

    assert first_id > 0
    assert second_id > first_id

    rows = db.list_distributor_order_uploads(distributor_name="Alpha Traders", verification_session_id="session-1")
    assert len(rows) == 2
    assert rows[0]["stage_key"] == "sales_order_file"
    assert rows[1]["stage_key"] == "filled_file"
    assert rows[1]["metadata"].get("source") == "test"


def test_master_distributor_stores_distributor_code_and_exposes_it(tmp_path):
    db = CentralizedDB(str(tmp_path / "distributor_code.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        firm_name="Alpha Group",
        firm_nick_name="AG",
        distributor_code="DC-001",
    )

    stored = db.get_master_distributor(distributor_id)
    assert stored is not None
    assert stored["distributor_code"] == "DC-001"
    assert stored["firm_name"] == "Alpha Group"

    listed = db.list_master_distributors(limit=10)
    assert any(item["distributor_code"] == "DC-001" for item in listed)


def test_master_distributor_stores_secondary_contact_and_sales_executive_fields(tmp_path):
    db = CentralizedDB(str(tmp_path / "contact_fields.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        firm_name="Alpha Group",
        firm_nick_name="AG",
        distributor_code="DC-002",
        secondary_distributor_name="Ravi",
        secondary_distributor_phone_number="9999999999",
        secondary_distributor_birthday="1990-01-01",
        secondary_distributor_anniversary="2015-06-30",
        sales_executive_name="Meera",
        sales_executive_phone_number="8888888888",
        sales_executive_email="meera@example.com",
        sales_executive_birthday="1992-02-02",
        sales_executive_anniversary="2020-03-03",
    )

    stored = db.get_master_distributor(distributor_id)
    assert stored is not None
    assert stored["secondary_distributor_name"] == "Ravi"
    assert stored["secondary_distributor_phone_number"] == "9999999999"
    assert stored["sales_executive_name"] == "Meera"
    assert stored["sales_executive_email"] == "meera@example.com"


def test_master_retailer_stores_retailer_code_and_contact_fields(tmp_path):
    db = CentralizedDB(str(tmp_path / "retailer_fields.sqlite3"))

    retailer_id = db.add_master_retailer(
        name="Shop One",
        distributor_id=1,
        location="Andheri",
        retailer_code="RT-001",
        secondary_retailer_name="Aman",
        secondary_retailer_phone_number="7777777777",
        secondary_retailer_birthday="1993-03-03",
        secondary_retailer_anniversary="2018-04-04",
        sales_executive_name="Nisha",
        sales_executive_phone_number="6666666666",
        sales_executive_email="nisha@example.com",
        sales_executive_birthday="1991-05-05",
        sales_executive_anniversary="2019-06-06",
    )

    stored = db.get_master_retailer(retailer_id)
    assert stored is not None
    assert stored["retailer_code"] == "RT-001"
    assert stored["secondary_retailer_name"] == "Aman"
    assert stored["sales_executive_name"] == "Nisha"
    assert stored["sales_executive_email"] == "nisha@example.com"


def test_clear_master_contacts_removes_distributor_and_retailer_data(tmp_path):
    db = CentralizedDB(str(tmp_path / "clear_contacts.sqlite3"))

    db.add_master_distributor(name="Alpha Traders", firm_name="Alpha Group", firm_nick_name="AG")
    db.add_master_retailer(name="Shop One", distributor_id=1, location="Andheri")

    removed = db.clear_master_contacts()

    assert removed == 1
    assert db.list_master_distributors(limit=10) == []
    assert db.list_master_retailers(limit=10) == []


def test_clear_distributor_contacts_keeps_retailer_contacts(tmp_path):
    db = CentralizedDB(str(tmp_path / "clear_distributor_contacts.sqlite3"))

    db.add_master_distributor(name="Alpha Traders", firm_name="Alpha Group", firm_nick_name="AG")
    db.add_master_retailer(name="Shop One", distributor_id=1, location="Andheri")

    removed = db.clear_distributor_contacts()

    assert removed == 1
    assert db.list_master_distributors(limit=10) == []
    assert db.list_master_retailers(limit=10) != []


def test_clear_retailer_contacts_keeps_distributor_contacts(tmp_path):
    db = CentralizedDB(str(tmp_path / "clear_retailer_contacts.sqlite3"))

    db.add_master_distributor(name="Alpha Traders", firm_name="Alpha Group", firm_nick_name="AG")
    db.add_master_retailer(name="Shop One", distributor_id=1, location="Andheri")

    removed = db.clear_retailer_contacts()

    assert removed == 1
    assert db.list_master_distributors(limit=10) != []
    assert db.list_master_retailers(limit=10) == []


def test_known_distributor_aliases_are_resolved_to_canonical_names(tmp_path):
    db = CentralizedDB(str(tmp_path / "distributor_alias.sqlite3"))

    distributor_id = db.add_master_distributor(name="Bnd", firm_name="Bnd")

    stored = db.get_master_distributor(distributor_id)
    assert stored is not None
    assert stored["name"] == "Bernina International P Ltd"
    assert stored["firm_name"] == "Bernina International P Ltd"


def test_business_rules_are_seeded_and_upsertable(tmp_path):
    db = CentralizedDB(str(tmp_path / "rules.sqlite3"))

    rules = db.list_business_rules(locked_only=True)
    keys = {item["rule_key"] for item in rules}

    assert "pricing_exmill_definition" in keys
    assert "pricing_ptr_definition" in keys
    assert "size_prompt_precheck_rule" in keys

    db.upsert_business_rule("pricing_exmill_definition", "ExMill means distributor buying price")
    updated = db.list_business_rules(locked_only=True)
    updated_map = {item["rule_key"]: item["rule_value"] for item in updated}
    assert updated_map["pricing_exmill_definition"] == "ExMill means distributor buying price"
