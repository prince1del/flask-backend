from pathlib import Path

import pandas as pd

from app.three_step_verification import (
    _load_product_aliases,
    _normalize_product_key,
    compare_step1,
    compare_step2,
    compare_step3,
    run_full_verification,
)


def test_run_full_verification_returns_structured_report(tmp_path):
    order_file = tmp_path / "order.xlsx"
    filled_file = tmp_path / "filled.xlsx"
    so_pdf = tmp_path / "sales_order.pdf"
    invoice_pdf = tmp_path / "invoice.pdf"

    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_file, index=False)

    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_file, index=False)

    so_pdf.write_text(
        "Product: Milk Powder\nQuantity: 12\nRate: 100\nGST: 5\nClient Name: Rahul Kumar Yadav\nInvoice Amount: 1000\nTotal GST: 5",
        encoding="utf-8",
    )
    invoice_pdf.write_text(
        "Product: Milk Powder\nQuantity: 12\nRate: 100\nGST: 5\nClient Name: Rahul K Yadav\nInvoice Amount: 1000\nTotal GST: 5",
        encoding="utf-8",
    )

    report = run_full_verification(order_file, filled_file, so_pdf, invoice_pdf)

    assert report["step1"]["status"] == "mismatches-found"
    assert report["step2"]["status"] in {"ok", "mismatches-found"}
    assert report["step3"]["status"] == "ok"
    assert report["step1"]["mismatch_count"] >= 1


def test_run_full_verification_skips_missing_steps(tmp_path):
    order_file = tmp_path / "order.xlsx"
    filled_file = tmp_path / "filled.xlsx"
    so_pdf = tmp_path / "sales_order.pdf"

    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(order_file, index=False)

    pd.DataFrame(
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
            },
        ]
    ).to_excel(filled_file, index=False)

    so_pdf.write_text(
        "Product: Milk Powder\nQuantity: 12\nRate: 100\nGST: 5", encoding="utf-8"
    )

    report = run_full_verification(order_file, filled_file, so_pdf, None)

    assert report["step1"]["status"] == "mismatches-found"
    assert report["step2"]["status"] == "ok"
    assert report["step3"]["status"] == "skipped"


def test_compare_step1_matches_rows_by_product(tmp_path):
    from app.three_step_verification import compare_step1

    order_file = tmp_path / "order.xlsx"
    filled_file = tmp_path / "filled.xlsx"

    pd.DataFrame(
        [
            {"product": "Aster", "quantity": 18, "rate": 12, "gst": 6, "discount": 750},
            {
                "product": "Bluemen",
                "quantity": 24,
                "rate": 6,
                "gst": 6,
                "discount": 250,
            },
        ]
    ).to_excel(order_file, index=False)

    pd.DataFrame(
        [
            {
                "product": "Bluemen",
                "quantity": 24,
                "rate": 6,
                "gst": 6,
                "discount": 250,
            },
            {"product": "Aster", "quantity": 18, "rate": 12, "gst": 6, "discount": 750},
        ]
    ).to_excel(filled_file, index=False)

    report = compare_step1(order_file, filled_file)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_compare_step1_uses_descriptors_for_same_product_rows(tmp_path):
    from app.three_step_verification import compare_step1

    order_file = tmp_path / "order_same_product.xlsx"
    filled_file = tmp_path / "filled_same_product.xlsx"

    pd.DataFrame(
        [
            {
                "product": "Bedsheet",
                "size": "King",
                "tc": 180,
                "rate": 2000,
                "quantity": 100,
            },
            {
                "product": "Bedsheet",
                "size": "Queen",
                "tc": 144,
                "rate": 1500,
                "quantity": 80,
            },
        ]
    ).to_excel(order_file, index=False)

    # Intentionally reverse order to ensure matching is not index-based and not product-only greedy.
    pd.DataFrame(
        [
            {
                "product": "Bedsheet",
                "size": "Queen",
                "tc": 144,
                "rate": 1500,
                "quantity": 80,
            },
            {
                "product": "Bedsheet",
                "size": "King",
                "tc": 180,
                "rate": 2000,
                "quantity": 100,
            },
        ]
    ).to_excel(filled_file, index=False)

    report = compare_step1(order_file, filled_file)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_compare_step1_allows_rate_tolerance(tmp_path):
    from app.three_step_verification import compare_step1

    order_file = tmp_path / "order_tolerance.xlsx"
    filled_file = tmp_path / "filled_tolerance.xlsx"

    pd.DataFrame(
        [
            {"product": "Bedsheet", "size": "King", "rate": 2000, "quantity": 100},
        ]
    ).to_excel(order_file, index=False)

    # 10% variation should be tolerated by default (25%).
    pd.DataFrame(
        [
            {"product": "Bedsheet", "size": "King", "rate": 2200, "quantity": 100},
        ]
    ).to_excel(filled_file, index=False)

    report = compare_step1(order_file, filled_file)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_compare_step1_matches_bale_vs_pcs_quantity(tmp_path):
    from app.three_step_verification import compare_step1

    order_file = tmp_path / "order_bales.xlsx"
    filled_file = tmp_path / "filled_pcs.xlsx"

    pd.DataFrame(
        [
            {
                "product": "Sheet Sets",
                "size": "DB BS",
                "quantity": 8,  # bale count
                "no_of_bales": 8,
                "min_bale_pack": 216,
                "rate": 1049,
            }
        ]
    ).to_excel(order_file, index=False)

    pd.DataFrame(
        [
            {
                "product": "Sheet Sets",
                "size": "DB BS",
                "qty": 1728,  # pieces
                "rate": 1049,
            }
        ]
    ).to_excel(filled_file, index=False)

    report = compare_step1(order_file, filled_file)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_parse_step1_accepts_headers_on_later_rows(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order.xlsx"
    pd.DataFrame(
        [
            ["Exported from ERP", "", "", "", ""],
            ["Item Name", "Order Qty", "Unit Price", "GST%", "Discount"],
            ["Milk Powder", 10, 100, 5, 2],
        ],
        dtype=object,
    ).to_excel(order_file, header=False, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert "error" not in parsed
    assert parsed["rows"][0]["product"] == "Milk Powder"
    assert parsed["rows"][0]["quantity"] == 10
    assert parsed["rows"][0]["rate"] == 100


def test_parse_step1_reports_missing_columns_details(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order.xlsx"
    pd.DataFrame(
        [
            ["Aster", None, None],
            ["Bluemen", None, None],
        ],
        dtype=object,
    ).to_excel(order_file, header=False, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert parsed["error"] == "Missing required item columns"
    assert parsed["missing_columns"] == ["quantity", "rate"]
    assert parsed["available_columns"] == ["0"]
    assert parsed["inferred_columns"]["product"] is not None


def test_parse_step1_infers_columns_from_noisy_rows(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order.xlsx"
    pd.DataFrame(
        [
            ["Aster", "100 (One in a dent)", 1728, 1049, 5, 2],
            ["Bluemen", "104 (One in a dent)", 288, 799, 5, None],
        ],
        dtype=object,
    ).to_excel(order_file, header=False, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert "error" not in parsed
    assert parsed["rows"][0]["product"] == "Aster"
    assert parsed["rows"][0]["quantity"] == 1728
    assert parsed["rows"][0]["rate"] == 1049
    assert parsed["rows"][1]["product"] == "Bluemen"
    assert parsed["rows"][1]["quantity"] == 288
    assert parsed["rows"][1]["rate"] == 799


def test_compare_step2_reads_item_rows_from_sales_order_pdf(tmp_path):
    filled_file = tmp_path / "filled.xlsx"
    so_pdf = tmp_path / "sales_order.pdf"

    pd.DataFrame(
        [
            {"product": "Sheet Sets", "quantity": 1728, "rate": 1049, "gst": 5},
        ]
    ).to_excel(filled_file, index=False)
    so_pdf.write_text("Sheet Sets 1728 1049\nComforter 72 4999", encoding="utf-8")

    report = compare_step2(filled_file, so_pdf)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_compare_step3_parses_client_name_without_colon(tmp_path):
    so_pdf = tmp_path / "sales_order.pdf"
    invoice_pdf = tmp_path / "invoice.pdf"

    so_pdf.write_text(
        "Client Name Rahul Kumar Yadav\nInvoice Amount 1000\nTotal GST 5",
        encoding="utf-8",
    )
    invoice_pdf.write_text(
        "Client Name Rahul Kumar Yadav\nInvoice Amount 1000\nTotal GST 5",
        encoding="utf-8",
    )

    report = compare_step3(so_pdf, invoice_pdf)

    assert report["status"] == "ok"
    assert report["mismatch_count"] == 0


def test_compare_step1_returns_error_for_implausible_mapping(tmp_path):
    order_file = tmp_path / "order.xlsx"
    filled_file = tmp_path / "filled.xlsx"

    pd.DataFrame(
        [
            {"product": "Aster", "quantity": 18, "rate": 12, "gst": 6, "discount": 750},
            {
                "product": "Bluemen",
                "quantity": 24,
                "rate": 6,
                "gst": 6,
                "discount": 250,
            },
        ]
    ).to_excel(order_file, index=False)

    pd.DataFrame(
        [
            {
                "product": "Aster",
                "quantity": 1728,
                "rate": 12,
                "gst": 120,
                "discount": 1080000,
            },
            {
                "product": "Bluemen",
                "quantity": 288,
                "rate": 6,
                "gst": 144,
                "discount": 634176,
            },
        ]
    ).to_excel(filled_file, index=False)

    report = compare_step1(order_file, filled_file)

    assert report["status"] == "error"
    assert any(
        "mapping issue" in message.lower() for message in report.get("errors", [])
    )


def test_compare_step2_returns_error_for_repetitive_pdf_values(tmp_path):
    filled_file = tmp_path / "filled.xlsx"
    so_pdf = tmp_path / "sales_order.pdf"

    pd.DataFrame(
        [
            {"product": "Item A", "quantity": 10, "rate": 100},
            {"product": "Item B", "quantity": 11, "rate": 110},
            {"product": "Item C", "quantity": 12, "rate": 120},
            {"product": "Item D", "quantity": 13, "rate": 130},
        ]
    ).to_excel(filled_file, index=False)

    so_pdf.write_text(
        """
Item A 63041910 8
Item B 63041910 8
Item C 63041910 8
Item D 63041910 8
        """.strip(),
        encoding="utf-8",
    )

    report = compare_step2(filled_file, so_pdf)

    assert report["status"] == "error"
    assert any(
        "parser issue" in message.lower() for message in report.get("errors", [])
    )


def test_parse_step1_avoids_bale_and_design_as_core_fields(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order.xlsx"
    pd.DataFrame(
        [
            {
                "Product": "Aster",
                "Bale Size": 18,
                "Selling Price": 1049,
                "No of Design": 5,
                "Qnty Per Color": 750,
                "Qty": 1728,
                "GST": 6,
                "Discount": 250,
            }
        ]
    ).to_excel(order_file, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert "error" not in parsed
    assert parsed["inferred_columns"]["quantity"] == "Qty"
    assert parsed["inferred_columns"]["gst"] == "GST"
    assert parsed["inferred_columns"]["discount"] == "Discount"
    assert parsed["rows"][0]["quantity"] == 1728
    assert parsed["rows"][0]["gst"] == 6
    assert parsed["rows"][0]["discount"] == 250


def test_parse_step1_prefers_qnty_over_bale_size_on_content_fallback(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order_qnty.xlsx"
    pd.DataFrame(
        [
            {
                "Brand": "Wonder",
                "Product": "Aster",
                "Bale Size": 18,
                "No of Design": 5,
                "Qnty Per Color": 750,
                "Qnty": 1728,
                "Selling Price": 1049,
                "GST": 6,
                "Discount": 250,
            }
        ]
    ).to_excel(order_file, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert "error" not in parsed
    assert parsed["inferred_columns"]["quantity"] == "Qnty"
    assert parsed["rows"][0]["quantity"] == 1728


def test_parse_step1_uses_qnty_values_when_quantity_column_is_blank(tmp_path):
    from app.three_step_verification import parse_step1_order_excel

    order_file = tmp_path / "order_qnty_collision.xlsx"
    pd.DataFrame(
        [
            {
                "Product": "Aster",
                "Quantity": None,
                "Qnty": 1728,
                "Selling Price": 1049,
            },
            {
                "Product": "Bluemen",
                "Quantity": None,
                "Qnty": 288,
                "Selling Price": 799,
            },
        ]
    ).to_excel(order_file, index=False)

    parsed = parse_step1_order_excel(order_file)

    assert "error" not in parsed
    quantities = [row["quantity"] for row in parsed["rows"]]
    assert quantities[:2] == [1728, 288]
