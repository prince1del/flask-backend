from pathlib import Path

from centralized_db_system.db import CentralizedDB
from centralized_db_system.drive_storage import GoogleDriveStorage


def test_master_tables_and_target_variance(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "analytics.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        gst_no="27AAAAA0000A1Z5",
        zone="North",
        region="Mumbai",
        credit_limit=50000,
        status="active",
    )
    retailer_id = db.add_master_retailer(
        name="Shop One",
        distributor_id=distributor_id,
        location="Andheri",
        status="active",
    )

    targets_file = tmp_path / "targets.csv"
    targets_file.write_text(
        "year,month,distributor_id,zone,target_amount,achievement_amount\n"
        "2022,Jan,1,North,1000,800\n"
        "2022,Feb,1,North,1000,1200\n",
        encoding="utf-8",
    )

    imported_rows = db.bulk_upload_targets_achievements(targets_file)
    assert imported_rows == 2

    summary = db.get_target_variance_summary(distributor_id=distributor_id, year=2022)
    assert summary["rows"][0]["variance_percentage"] == -20.0
    assert summary["overall_variance_percentage"] == 0.0

    distributor = db.get_master_distributor(distributor_id)
    retailer = db.get_master_retailer(retailer_id)
    assert distributor["name"] == "Alpha Traders"
    assert retailer["location"] == "Andheri"


def test_primary_and_secondary_sales_flow(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "sales.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Beta Traders",
        gst_no="27BBBBB0000A1Z5",
        zone="West",
        region="Pune",
        credit_limit=20000,
        status="active",
    )

    db.record_primary_sales(
        {
            "distributor_id": distributor_id,
            "invoice_no": "INV-100",
            "invoice_date": "2022-01-15",
            "quantity": 50,
            "amount": 5000,
        }
    )

    secondary_file = tmp_path / "secondary_sales.csv"
    secondary_file.write_text(
        "distributor_id,retailer_id,invoice_no,sale_date,quantity,amount\n"
        "1,1,RET-1,2022-01-16,10,1000\n",
        encoding="utf-8",
    )
    imported_rows = db.bulk_upload_secondary_sales(secondary_file)
    assert imported_rows == 1

    summary = db.get_sales_flow_summary(distributor_id=distributor_id)
    assert summary["primary_volume"] == 50
    assert summary["secondary_volume"] == 10
    assert summary["difference"] == 40
    assert summary["variance_percentage"] == 20.0


def test_dashboard_payload_contains_analytics_sections(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "dashboard.sqlite3"))
    db.add_master_distributor(
        "Gamma Traders", "27GGGGG0000A1Z5", "South", "Chennai", 30000, "active"
    )
    payload = db.get_dashboard_payload()

    assert payload["masters"]["distributors"] == 1
    assert payload["targets"]["total_rows"] == 0
    assert payload["sales"]["primary_total"] == 0
    assert payload["sales"]["secondary_total"] == 0


def test_excel_and_pdf_exports_are_generated(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "exports.sqlite3"))
    db.add_master_distributor(
        name="Delta Traders",
        firm_name="Delta Group",
        firm_nick_name="DG",
        gst_no="27DDDD0000A1Z5",
        zone="East",
        region="Kolkata",
        credit_limit=15000,
        status="active",
        secondary_distributor_name="Ravi Sharma",
        secondary_distributor_phone_number="9876543210",
        sales_executive_name="Asha Mehta",
        sales_executive_email="asha@example.com",
    )

    csv_text = db.export_master_distributors()
    excel_bytes = db.export_master_distributors_excel()
    pdf_bytes = db.export_master_distributors_pdf()

    assert "firm_nick_name" in csv_text.splitlines()[0]
    assert "secondary_distributor_name" in csv_text.splitlines()[0]
    assert "sales_executive_name" in csv_text.splitlines()[0]
    assert "DG" in csv_text
    assert "Ravi Sharma" in csv_text
    assert "Asha Mehta" in csv_text
    assert excel_bytes.startswith(b"PK")
    assert pdf_bytes.startswith(b"%PDF")


def test_google_drive_storage_uses_metadata_and_download(tmp_path: Path) -> None:
    storage = GoogleDriveStorage(folder_id="demo-folder")
    sample_file = tmp_path / "catalog.jpg"
    sample_file.write_bytes(b"fake-image")

    metadata = storage.upload_file(sample_file, "catalog.jpg")
    assert metadata["name"] == "catalog.jpg"
    assert metadata["storage"] == "google_drive"

    downloaded = storage.download_local_file(sample_file)
    assert downloaded == b"fake-image"
