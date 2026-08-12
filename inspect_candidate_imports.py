from pathlib import Path
import pandas as pd
import csv

root = Path('.')
keywords = ['master', 'draft', 'contact', 'buyer', 'distributor', 'retailer']

for path in sorted(root.rglob('*')):
    if path.is_file() and path.suffix.lower() in {'.xlsx', '.xls', '.csv'}:
        lname = path.name.lower()
        if any(k in lname for k in keywords):
            print('FILE', path)
            try:
                if path.suffix.lower() in {'.xlsx', '.xls'}:
                    xl = pd.ExcelFile(path)
                    for sheet in xl.sheet_names[:2]:
                        print('  SHEET', sheet)
                        df = xl.parse(sheet, nrows=5)
                        print('   COLS', [str(c).strip() for c in df.columns])
                else:
                    with path.open(newline='', encoding='utf-8', errors='ignore') as f:
                        row = next(csv.reader(f), None)
                        print('   CSV COLS', row)
            except Exception as exc:
                print('   ERROR', exc)
            print()
