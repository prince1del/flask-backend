import pandas as pd

from io import BytesIO



from app.services.sales_achievement_parser import parse_sales_achievement_excel





def test_parse_pivot_total_rows():

    headers = [

        "NICK NAME or Distributor",

        "CUSTOMER NAME",

        "CATEGORY",

        "Apr-25",

        "May-25",

        "Grand Total",

    ]

    rows = [

        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 20, 30],

        ["BND", "BERNINA INTERNATIONAL P LTD Total", "", 10, 20, 287.31],

        ["Zirise Haryana", "Zirise Technologies Private Limited Total", "", 5, 5, 123.41],

    ]

    df = pd.DataFrame([headers] + rows)

    buf = BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        df.to_excel(writer, index=False, header=False)

    parsed = parse_sales_achievement_excel(buf.getvalue(), "test.xlsx")

    assert parsed["unit"] == "lakhs"

    assert parsed["distributor_count"] == 2

    by_name = {d["name"]: d["achievement_lakhs"] for d in parsed["distributors"]}

    assert by_name["BERNINA INTERNATIONAL P LTD"] == 287.31

    assert round(parsed["total_achievement_lakhs"], 2) == round(287.31 + 123.41, 2)





def test_parse_two_sheet_workbook_merges_totals_and_categories():

    headers = [

        "NICK NAME or Distributor",

        "CUSTOMER NAME",

        "CATEGORY",

        "Apr-25",

        "Grand Total",

    ]

    category_rows = [

        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 10],

        ["BND", "BERNINA INTERNATIONAL P LTD", "Towels", 5, 5],

    ]

    total_rows = [

        ["BND", "BERNINA INTERNATIONAL P LTD Total", "", 15, 287.31],

    ]

    buf = BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        pd.DataFrame([headers] + category_rows).to_excel(

            writer, sheet_name="Sheet1", index=False, header=False

        )

        pd.DataFrame([headers] + total_rows).to_excel(

            writer, sheet_name="Sheet1 (2)", index=False, header=False

        )

    parsed = parse_sales_achievement_excel(buf.getvalue(), "Primary 2025-26.xlsx")

    by_name = {d["name"]: d["achievement_lakhs"] for d in parsed["distributors"]}

    assert by_name["BERNINA INTERNATIONAL P LTD"] == 287.31

    assert parsed["has_category_detail"] is True

    assert "Bed Sheet" in parsed["category_matrix"]["categories"]

    assert "Towels" in parsed["category_matrix"]["categories"]





def test_parse_duplicate_category_rows_are_merged():
    headers = [
        "NICK NAME or Distributor",
        "CUSTOMER NAME",
        "CATEGORY",
        "Apr-25",
        "Grand Total",
    ]
    rows = [
        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 10],
        ["BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 5, 5],
        ["BND", "BERNINA INTERNATIONAL P LTD Total", "", 15, 15],
    ]
    df = pd.DataFrame([headers] + rows)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False)
    parsed = parse_sales_achievement_excel(buf.getvalue(), "test.xlsx")
    bed = [
        c
        for c in parsed["categories"]
        if c["distributor"] == "BERNINA INTERNATIONAL P LTD" and c["category"] == "Bed Sheet"
    ]
    assert len(bed) == 1
    assert bed[0]["achievement_lakhs"] == 15


def test_parse_category_rows_with_forward_fill():

    headers = [

        "SM",

        "NICK NAME or Distributor",

        "CUSTOMER NAME",

        "CATEGORY",

        "Apr-25",

        "Grand Total",

    ]

    rows = [

        ["Kunwar", "BND", "BERNINA INTERNATIONAL P LTD", "Bed Sheet", 10, 10],

        [None, None, None, "Towels", 5, 5],

        [None, None, "BERNINA INTERNATIONAL P LTD Total", None, 15, 15],

    ]

    df = pd.DataFrame([headers] + rows)

    buf = BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        df.to_excel(writer, index=False, header=False)

    parsed = parse_sales_achievement_excel(buf.getvalue(), "test.xlsx")

    assert parsed["has_category_detail"] is True

    matrix = parsed["category_matrix"]

    assert "Bed Sheet" in matrix["categories"]

    assert "Towels" in matrix["categories"]

    assert matrix["grand_total"] == 15

