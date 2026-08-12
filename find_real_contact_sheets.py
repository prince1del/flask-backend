from pathlib import Path
import pandas as pd
import re

root = Path('.')
patterns = [re.compile(r'distributor', re.I), re.compile(r'retailer', re.I), re.compile(r'gst', re.I), re.compile(r'buyer code', re.I)]
count = 0
for path in sorted(root.rglob('*.xlsx')):
    if 'node_modules' in str(path).lower() or 'dist' in str(path.parts):
        continue
    if path.name.startswith('~$'):
        continue
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df = xl.parse(sheet, nrows=5)
            cols = [str(c).strip() for c in df.columns]
            if any(p.search(' '.join(cols)) for p in patterns):
                print('MATCH', path, 'sheet', sheet, 'cols', cols)
                raise SystemExit
            # also scan first few cell values
            sample = df.astype(str).head(5).values.flatten()
            text = ' '.join(sample)
            if any(p.search(text) for p in patterns):
                print('CELL_MATCH', path, 'sheet', sheet, 'cols', cols)
                raise SystemExit
    except Exception:
        pass
    count += 1
    if count % 100 == 0:
        print('checked', count)
print('done', count)
