import io
from pathlib import Path

import pandas as pd

from app.web_app import create_app

app = create_app()
client = app.test_client()

order_path = Path('tmp_order.xlsx')
filled_path = Path('tmp_filled.xlsx')
so_path = Path('tmp_so.pdf')
inv_path = Path('tmp_inv.pdf')

pd.DataFrame([{'Product':'ABC','Quantity':10,'Rate':100,'GST':18,'Discount':0}]).to_excel(order_path, index=False)
pd.DataFrame([{'Product':'ABC','Quantity':10,'Rate':100,'GST':18,'Discount':0}]).to_excel(filled_path, index=False)
so_path.write_text('Product: ABC\nQuantity: 10\nRate: 100\nGST: 18', encoding='utf-8')
inv_path.write_text('Client Name: Test Client\nInvoice Amount: 1000\nTotal GST: 180', encoding='utf-8')

with open(order_path, 'rb') as f1, open(filled_path, 'rb') as f2, open(so_path, 'rb') as f3, open(inv_path, 'rb') as f4:
    resp = client.post('/', data={
        'order_file': (io.BytesIO(f1.read()), order_path.name),
        'filled_file': (io.BytesIO(f2.read()), filled_path.name),
        'sales_order_file': (io.BytesIO(f3.read()), so_path.name),
        'invoice_file': (io.BytesIO(f4.read()), inv_path.name),
    }, content_type='multipart/form-data')

print(resp.status_code)
print(resp.data.decode('utf-8')[:5000])
