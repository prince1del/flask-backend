from pathlib import Path

import pandas as pd

from centralized_db_system.db import CentralizedDB


def _write_excel(path: Path, dataframe: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name="Sheet1")


def test_bulk_master_upload_populates_distributors_and_retailers(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "bulk_masters.sqlite3"))

    distributor_file = tmp_path / "distributors.xlsx"
    distributor_df = pd.DataFrame(
        [
            {
                "Distributor Code": "D-ALPHA",
                "Firm Name": "Alpha Group",
                "Firm nick name": "AG",
                "Distributor Name": "Alpha Traders",
                "Mobile Number": "9876543210",
                "Location": "Andheri",
                "Address": "Main Road",
                "Pincode": "400001",
                "Email id": "alpha@example.com",
                "Distribution State": "Maharashtra",
                "Distribution Area": "Mumbai",
                "GST Number": "27AAAA0000A1Z5",
                "Payment Terms": "30 Days",
                "Birthday": "1990-01-01",
                "Anniversary": "2015-05-20",
                "Credit Limit": 15000,
                "Opening Balance": 2500,
            }
        ]
    )
    _write_excel(distributor_file, distributor_df)

    distributor_result = db.bulk_upload_masters(
        "distributors",
        distributor_file,
        template_config={
            "headers": {
                "distributor_code": "Distributor Code",
                "firm_name": "Firm Name",
                "firm_nick_name": "Firm nick name",
                "distributor_name": "Distributor Name",
                "phone_number": "Mobile Number",
                "location": "Location",
                "address": "Address",
                "pincode": "Pincode",
                "email": "Email id",
                "distribution_state": "Distribution State",
                "distribution_area": "Distribution Area",
                "gst_no": "GST Number",
                "payment_terms": "Payment Terms",
                "birthday": "Birthday",
                "anniversary": "Anniversary",
                "credit_limit": "Credit Limit",
                "initial_outstanding_balance": "Opening Balance",
            }
        },
    )
    assert distributor_result["inserted"] == 1

    retailer_file = tmp_path / "retailers.xlsx"
    retailer_df = pd.DataFrame(
        [
            {
                "Retailer Name": "Shop One",
                "Distributor": "Alpha Traders",
                "Location": "Andheri",
                "Phone": "9876543210",
            }
        ]
    )
    _write_excel(retailer_file, retailer_df)

    retailer_result = db.bulk_upload_masters(
        "retailers",
        retailer_file,
        template_config={
            "headers": {
                "retailer_name": "Retailer Name",
                "linked_distributor_gst_or_name": "Distributor",
                "location": "Location",
                "phone_number": "Phone",
            }
        },
    )
    assert retailer_result["inserted"] == 1

    distributor = db.get_master_distributor_by_name("Alpha Traders")
    assert distributor is not None
    assert distributor["distributor_id"] == "D-ALPHA"
    assert distributor["firm_name"] == "Alpha Group"
    assert distributor["firm_nick_name"] == "AG"
    assert distributor["phone_number"] == "9876543210"
    assert distributor["location"] == "Andheri"
    assert distributor["address"] == "Main Road"
    assert distributor["pincode"] == "400001"
    assert distributor["email"] == "alpha@example.com"
    assert distributor["zone"] == "Maharashtra"
    assert distributor["region"] == "Mumbai"
    assert distributor["payment_terms"] == "30 Days"
    assert distributor["birthday"] == "1990-01-01"
    assert distributor["anniversary"] == "2015-05-20"
    retailer = db.get_master_retailer(1)
    assert retailer is not None
    assert retailer["distributor_id"] == distributor["id"]


def test_bulk_master_upload_accepts_common_retailer_header_aliases(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "retailer_aliases.sqlite3"))
    db.add_master_distributor(
        name="Alpha Traders", firm_name="Alpha Group", firm_nick_name="AG"
    )

    retailer_file = tmp_path / "retailers_aliases.xlsx"
    retailer_df = pd.DataFrame(
        [
            {
                "Retailer Name": "Shop One",
                "Distributor": "Alpha Traders",
                "Retailer Code": "RT-001",
                "Location": "Andheri",
                "Retailer Mobile Number": "9876543210",
                "Retailer Email": "shop@example.com",
                "Address": "Main Road",
                "GSTIN": "27AAAA0000A1Z5",
                "Secondary Contact Name": "Aman",
                "Secondary Contact Mobile Number": "7777777777",
                "Secondary Contact Birthday": "1993-03-03",
                "Secondary Contact Anniversary": "2018-04-04",
                "Sales Executive": "Nisha",
                "Sales Executive Mobile Number": "6666666666",
                "Sales Executive Email": "nisha@example.com",
                "Sales Executive Birthday": "1991-05-05",
                "Sales Executive Anniversary": "2019-06-06",
            }
        ]
    )
    _write_excel(retailer_file, retailer_df)

    result = db.bulk_upload_masters("retailers", retailer_file)
    assert result["inserted"] == 1

    retailer = db.get_master_retailer(1)
    assert retailer is not None
    assert retailer["retailer_code"] == "RT-001"
    assert retailer["phone_number"] == "9876543210"
    assert retailer["email"] == "shop@example.com"
    assert retailer["secondary_retailer_name"] == "Aman"
    assert retailer["sales_executive_name"] == "Nisha"


def test_bulk_master_upload_accepts_common_distributor_header_aliases(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "distributor_aliases.sqlite3"))

    distributor_file = tmp_path / "distributors_aliases.xlsx"
    distributor_df = pd.DataFrame(
        [
            {
                "Distributor Name": "Alpha Traders",
                "Distributor Code": "D-ALPHA",
                "Firm Name": "Alpha Group",
                "Firm nick name": "AG",
                "Distributor Mobile Number": "9876543210",
                "Distributor Email": "alpha@example.com",
                "Location": "Andheri",
                "Address": "Main Road",
                "Pincode": "400001",
                "Distribution State": "Maharashtra",
                "Distribution Area": "Mumbai",
                "GSTIN": "27AAAA0000A1Z5",
                "Payment Terms": "30 Days",
                "Birthday": "1990-01-01",
                "Anniversary": "2015-05-20",
                "Secondary Contact Name": "Ravi",
                "Secondary Contact Mobile Number": "9999999999",
                "Secondary Contact Birthday": "1992-02-02",
                "Secondary Contact Anniversary": "2016-03-03",
                "Sales Executive": "Meera",
                "Sales Executive Mobile Number": "8888888888",
                "Sales Executive Email": "meera@example.com",
                "Sales Executive Birthday": "1991-04-04",
                "Sales Executive Anniversary": "2018-05-05",
            }
        ]
    )
    _write_excel(distributor_file, distributor_df)

    result = db.bulk_upload_masters("distributors", distributor_file)
    assert result["inserted"] == 1

    distributor = db.get_master_distributor_by_name("Alpha Traders")
    assert distributor is not None
    assert distributor["distributor_id"] == "D-ALPHA"
    assert distributor["phone_number"] == "9876543210"
    assert distributor["email"] == "alpha@example.com"
    assert distributor["secondary_distributor_name"] == "Ravi"
    assert distributor["sales_executive_name"] == "Meera"


def test_bulk_master_upload_allows_similar_but_distinct_distributor_names(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "similar_distributors.sqlite3"))

    distributor_file = tmp_path / "distributors.xlsx"
    distributor_df = pd.DataFrame(
        [
            {
                "Distributor Name": "Alpha Traders",
                "GST Number": "27AAAA0000A1Z5",
                "Zone": "West",
                "Region": "Mumbai",
                "Credit Limit": 15000,
                "Opening Balance": 2500,
            },
            {
                "Distributor Name": "Alpha Trader",
                "GST Number": "27AAAA0000A2Z5",
                "Zone": "West",
                "Region": "Mumbai",
                "Credit Limit": 12000,
                "Opening Balance": 1800,
            },
        ]
    )
    _write_excel(distributor_file, distributor_df)

    result = db.bulk_upload_masters(
        "distributors",
        distributor_file,
        template_config={
            "headers": {
                "distributor_name": "Distributor Name",
                "gst_no": "GST Number",
                "zone": "Zone",
                "region": "Region",
                "credit_limit": "Credit Limit",
                "initial_outstanding_balance": "Opening Balance",
            }
        },
    )

    assert result["inserted"] == 2
    assert result["skipped"] == 0
    assert db.get_master_distributor_by_name("Alpha Traders") is not None
    assert db.get_master_distributor_by_name("Alpha Trader") is not None


def test_distributor_bulk_upload_saves_extended_fields(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "distributor_extended.sqlite3"))

    distributor_file = tmp_path / "distributors.xlsx"
    distributor_df = pd.DataFrame(
        [
            {
                "Distributor Code": "D-ALPHA-EXT",
                "Firm Name": "Alpha Group",
                "Firm nick name": "AG",
                "Distributor Name": "Alpha Traders",
                "GST Number": "27AAAA0000A1Z5",
                "Distribution State": "West",
                "Distribution Area": "Mumbai",
                "Credit Limit": 15000,
                "Mobile Number": "9876543210",
                "Email id": "alpha@example.com",
                "Location": "Andheri",
                "Address": "Main Road",
                "Pincode": "400001",
                "Payment Terms": "30 Days",
                "Birthday": "1990-01-01",
                "Anniversary": "2015-05-20",
            }
        ]
    )
    _write_excel(distributor_file, distributor_df)

    result = db.bulk_upload_masters(
        "distributors",
        distributor_file,
        template_config={
            "headers": {
                "distributor_code": "Distributor Code",
                "firm_name": "Firm Name",
                "firm_nick_name": "Firm nick name",
                "distributor_name": "Distributor Name",
                "gst_no": "GST Number",
                "distribution_state": "Distribution State",
                "distribution_area": "Distribution Area",
                "credit_limit": "Credit Limit",
                "phone_number": "Mobile Number",
                "email": "Email id",
                "location": "Location",
                "address": "Address",
                "pincode": "Pincode",
                "payment_terms": "Payment Terms",
                "birthday": "Birthday",
                "anniversary": "Anniversary",
            }
        },
    )

    assert result["inserted"] == 1
    stored = db.get_master_distributor_by_name("Alpha Traders")
    assert stored is not None
    assert stored["distributor_id"] == "D-ALPHA-EXT"
    assert stored["firm_name"] == "Alpha Group"
    assert stored["firm_nick_name"] == "AG"
    assert stored["phone_number"] == "9876543210"
    assert stored["email"] == "alpha@example.com"
    assert stored["location"] == "Andheri"
    assert stored["address"] == "Main Road"
    assert stored["pincode"] == "400001"
    assert stored["payment_terms"] == "30 Days"
    assert stored["birthday"] == "1990-01-01"
    assert stored["anniversary"] == "2015-05-20"


def test_retailer_bulk_upload_saves_extended_fields(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "retailer_extended.sqlite3"))

    distributor_file = tmp_path / "distributors.xlsx"
    distributor_df = pd.DataFrame(
        [
            {
                "Distributor Name": "Alpha Traders",
                "GSTIN": "27AAAA0000A1Z5",
                "Zone": "West",
                "Region": "Mumbai",
                "Credit Limit": 15000,
                "Opening Balance": 2500,
            }
        ]
    )
    _write_excel(distributor_file, distributor_df)

    db.bulk_upload_masters(
        "distributors",
        distributor_file,
        template_config={
            "headers": {
                "distributor_name": "Distributor Name",
                "gst_no": "GSTIN",
                "zone": "Zone",
                "region": "Region",
                "credit_limit": "Credit Limit",
                "initial_outstanding_balance": "Opening Balance",
            }
        },
    )

    retailer_file = tmp_path / "retailers.xlsx"
    retailer_df = pd.DataFrame(
        [
            {
                "Retailer Name": "Shop One",
                "Distributor": "Alpha Traders",
                "Location": "Andheri",
                "Phone": "9876543210",
                "Email": "shop@example.com",
                "Address": "Main Road",
                "GSTIN": "27ABCDE1234F1Z5",
            }
        ]
    )
    _write_excel(retailer_file, retailer_df)

    result = db.bulk_upload_masters(
        "retailers",
        retailer_file,
        template_config={
            "headers": {
                "retailer_name": "Retailer Name",
                "linked_distributor_gst_or_name": "Distributor",
                "location": "Location",
                "phone_number": "Phone",
                "email": "Email",
                "address": "Address",
                "gst_no": "GSTIN",
            }
        },
    )

    assert result["inserted"] == 1
    stored = db.get_master_retailer_by_name("Shop One")
    assert stored is not None
    assert stored["phone_number"] == "9876543210"
    assert stored["email"] == "shop@example.com"
    assert stored["address"] == "Main Road"
    assert stored["gst_no"] == "27ABCDE1234F1Z5"


def test_article_bulk_upload_sanitizes_and_saves_custom_headers(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "articles.sqlite3"))

    article_file = tmp_path / "articles.xlsx"
    article_df = pd.DataFrame(
        [
            {
                "Category": "towels",
                "Design Code": "A100",
                "Colour": "blue",
                "Base Rate": 1200,
                "GST %": 5,
                "Pcs / Bale": 40,
            }
        ]
    )
    _write_excel(article_file, article_df)

    result = db.bulk_upload_articles(
        article_file,
        template_config={
            "headers": {
                "category_name": "Category",
                "design_code": "Design Code",
                "color_way": "Colour",
                "base_rate": "Base Rate",
                "gst_percentage": "GST %",
                "pcs_per_bale": "Pcs / Bale",
            }
        },
    )

    assert result["inserted"] == 1
    rows = db.article_service.list_articles_by_category()
    assert rows[0]["category_name"] == "Towels"
    assert rows[0]["design_name"] == "A100"
    assert rows[0]["color_way"] == "BLUE"
    assert rows[0]["base_rate"] == 1200


def test_distributor_bulk_upload_updates_same_record_without_mixing(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "distributor_updates.sqlite3"))

    distributor_file_1 = tmp_path / "distributors_first.xlsx"
    distributor_df_1 = pd.DataFrame(
        [
            {
                "Firm Name": "Alpha Group",
                "Firm nick name": "AG",
                "Distributor Name": "Alpha Traders",
                "GST Number": "27AAAA0000A1Z5",
                "Mobile Number": "9876543210",
                "Address": "Main Road",
            }
        ]
    )
    _write_excel(distributor_file_1, distributor_df_1)

    first = db.bulk_upload_masters("distributors", distributor_file_1)
    assert first["inserted"] == 1

    distributor_file_2 = tmp_path / "distributors_second.xlsx"
    distributor_df_2 = pd.DataFrame(
        [
            {
                "Firm Name": "Alpha Group Pvt Ltd",
                "Firm nick name": "AGP",
                "Distributor Name": "Alpha Traders",
                "GST Number": "27AAAA0000A1Z5",
                "Mobile Number": "9999999999",
                "Address": "Updated Road",
                "Payment Terms": "45 Days",
            }
        ]
    )
    _write_excel(distributor_file_2, distributor_df_2)

    second = db.bulk_upload_masters("distributors", distributor_file_2)
    assert second["inserted"] == 0
    assert second["updated"] == 1
    assert second["skipped"] == 0

    stored = db.get_master_distributor_by_name("Alpha Traders")
    assert stored is not None
    assert stored["firm_name"] == "Alpha Group Pvt Ltd"
    assert stored["firm_nick_name"] == "AGP"
    assert stored["phone_number"] == "9999999999"
    assert stored["address"] == "Updated Road"
    assert stored["payment_terms"] == "45 Days"


def test_distributor_bulk_upload_skips_conflict_when_name_and_gst_map_to_different_records(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "distributor_conflict.sqlite3"))

    base_file = tmp_path / "base_distributors.xlsx"
    base_df = pd.DataFrame(
        [
            {
                "Distributor Name": "Alpha Traders",
                "GST Number": "27AAAA0000A1Z5",
            },
            {
                "Distributor Name": "Beta Traders",
                "GST Number": "27BBBB0000B1Z5",
            },
        ]
    )
    _write_excel(base_file, base_df)
    seed = db.bulk_upload_masters("distributors", base_file)
    assert seed["inserted"] == 2

    conflict_file = tmp_path / "conflict_distributors.xlsx"
    conflict_df = pd.DataFrame(
        [
            {
                "Distributor Name": "Alpha Traders",
                "GST Number": "27BBBB0000B1Z5",
                "Mobile Number": "9000000000",
            }
        ]
    )
    _write_excel(conflict_file, conflict_df)

    result = db.bulk_upload_masters("distributors", conflict_file)
    assert result["inserted"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1
    assert any("Conflict" in message for message in result["errors"])

    alpha = db.get_master_distributor_by_name("Alpha Traders")
    beta = db.get_master_distributor_by_name("Beta Traders")
    assert alpha is not None
    assert beta is not None
    assert alpha["phone_number"] in {None, ""}
    assert beta["phone_number"] in {None, ""}
