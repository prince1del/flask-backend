"""Spreadsheet preview for Drive files."""

import io

import openpyxl

from app.storage.spreadsheet_preview import preview_spreadsheet_bytes


def test_preview_xlsx_bytes():
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Brand", "Qty", "Rate"])
    ws.append(["Tulip", 100, 45.5])
    ws.append(["Aster", 200, 52])
    wb.save(buf)
    payload = preview_spreadsheet_bytes(buf.getvalue(), "BND.xlsx")
    assert payload["kind"] == "spreadsheet"
    assert payload["file_name"] == "BND.xlsx"
    assert len(payload["sheets"]) == 1
    sheet = payload["sheets"][0]
    assert sheet["name"] == "Orders"
    assert sheet["rows"][0] == ["Brand", "Qty", "Rate"]
    assert sheet["rows"][1][0] == "Tulip"


def test_preview_csv_bytes():
    content = b"Name,Qty\nBernina,50\n"
    payload = preview_spreadsheet_bytes(content, "orders.csv")
    assert payload["sheets"][0]["rows"][1] == ["Bernina", "50"]
