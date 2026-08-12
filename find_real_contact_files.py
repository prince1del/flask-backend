from pathlib import Path
import re
root = Path('.')
keywords = ['distributor', 'retailer', 'contact', 'master', 'buyer_code', 'buyer code']
for path in sorted(root.rglob('*')):
    if path.is_file() and path.suffix.lower() in {'.xlsx', '.xls', '.csv'}:
        name = str(path.name).lower()
        if any(k in name for k in keywords):
            print('NAME_MATCH', path)
