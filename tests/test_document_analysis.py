import csv
from pathlib import Path

from openpyxl import Workbook

from app.document_analysis import analyze_documents, match_person_names


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_excel(path: Path, rows: list[dict[str, object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(rows[0].keys()))
    for row in rows:
        sheet.append([row.get(key) for key in rows[0].keys()])
    workbook.save(path)


def write_pdf(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_match_person_names_handles_aliases():
    assert match_person_names("Rahul Kumar Yadav", "RK Yadav") is True
    assert match_person_names("Rahul Kumar Yadav", "RKV") is True


def test_analyze_documents_supports_excel_and_pdf(tmp_path):
    order_path = tmp_path / "order.xlsx"
    invoice_path = tmp_path / "invoice.pdf"

    write_excel(
        order_path,
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
                "client_name": "Rahul Kumar Yadav",
                "invoice_amount": 1000,
            }
        ],
    )
    write_pdf(
        invoice_path,
        "Product: Milk Powder\nQuantity: 10\nRate: 100\nGST: 5\nDiscount: 2\nClient Name: Rahul Kumar Yadav\nInvoice Amount: 1000",
    )

    report = analyze_documents([order_path, invoice_path])

    assert report["summary"]["status"] == "ok"
    assert report["documents"][0]["rows"]
    assert report["documents"][1]["rows"]


def test_analyze_documents_detects_mismatches_and_person_matches(tmp_path):
    order_path = tmp_path / "order.csv"
    sales_order_path = tmp_path / "sales_order.csv"
    invoice_path = tmp_path / "invoice.csv"

    write_csv(
        order_path,
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 100,
                "gst": 5,
                "discount": 2,
                "client_name": "Rahul Kumar Yadav",
                "invoice_amount": 1000,
            }
        ],
    )
    write_csv(
        sales_order_path,
        [
            {
                "product": "Milk Powder",
                "quantity": 12,
                "rate": 100,
                "gst": 5,
                "discount": 2,
                "client_name": "RK Yadav",
                "invoice_amount": 1000,
            }
        ],
    )
    write_csv(
        invoice_path,
        [
            {
                "product": "Milk Powder",
                "quantity": 10,
                "rate": 110,
                "gst": 6,
                "discount": 3,
                "client_name": "RKV",
                "invoice_amount": 1200,
            }
        ],
    )

    report = analyze_documents(
        [order_path, sales_order_path, invoice_path],
        aliases={"rkv": ["rahul kumar yadav", "rk yadav"]},
    )

    assert report["summary"]["mismatch_count"] >= 2
    assert report["summary"]["person_match_count"] >= 1
    assert any(item["field"] == "quantity" for item in report["mismatches"])
    assert any(item["field"] == "client_name" for item in report["mismatches"])
