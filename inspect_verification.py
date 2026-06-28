from pathlib import Path
import pandas as pd
from app.three_step_verification import (
    parse_step1_order_excel,
    parse_step2_sales_order_pdf,
    compare_step2,
)
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    filled_file = tmp / "filled.xlsx"
    so_pdf = tmp / "sales_order.pdf"
    pd.DataFrame(
        [{"product": "Sheet Sets", "quantity": 1728, "rate": 1049, "gst": 5}]
    ).to_excel(filled_file, index=False)
    so_pdf.write_text("Sheet Sets 1728 1049\nComforter 72 4999", encoding="utf-8")
    filled = parse_step1_order_excel(filled_file)
    sales = parse_step2_sales_order_pdf(so_pdf)
    print("FILLED", filled)
    print("SALES", sales)
    print("COMPARE", compare_step2(filled_file, so_pdf))
