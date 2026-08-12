from pathlib import Path
import pandas as pd

candidates = [
    Path('Order sheet AW26.xlsx'),
    Path("MBO's list as on 31.05.26 by SMs.xlsx"),
    Path('instance/verification_uploads/0acfe1e4-4c2b-486b-ae1d-f5bcd8ef4974/filled_file_alpha_traders_filled.xlsx'),
]
for path in candidates:
    print('PATH', path, 'exists', path.exists())
    if not path.exists():
        continue
    try:
        xl = pd.ExcelFile(path)
        print('  sheets', xl.sheet_names)
        for sheet in xl.sheet_names[:2]:
            df = xl.parse(sheet, nrows=5)
            print('  sheet', sheet, 'columns', list(df.columns))
    except Exception as exc:
        print('  ERROR', exc)
