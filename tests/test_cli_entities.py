from centralized_db_system.cli import main
from centralized_db_system.db import CentralizedDB


def test_add_distributor_cli_command_persists_record(tmp_path):
    db_path = tmp_path / "entities.sqlite3"

    exit_code = main(
        [
            "add-distributor",
            "ABC Traders",
            "Ravi",
            "9988776655",
            "ravi@example.com",
            "Main Road",
            "Delhi",
            "DL",
            "--db",
            str(db_path),
        ]
    )

    assert exit_code == 0
    db = CentralizedDB(str(db_path))
    distributors = db.list_distributors()
    assert len(distributors) == 1
    assert distributors[0]["name"] == "ABC Traders"


def test_add_retailer_cli_command_persists_record(tmp_path):
    db_path = tmp_path / "entities.sqlite3"

    exit_code = main(
        [
            "add-retailer",
            "Shop 24",
            "Meera",
            "8877665544",
            "meera@example.com",
            "Market Road",
            "Mumbai",
            "MH",
            "--db",
            str(db_path),
        ]
    )

    assert exit_code == 0
    db = CentralizedDB(str(db_path))
    retailers = db.list_retailers()
    assert len(retailers) == 1
    assert retailers[0]["name"] == "Shop 24"
