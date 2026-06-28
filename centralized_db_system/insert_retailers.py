import openpyxl
import sqlite3

wb = openpyxl.load_workbook("MBO's list as on 31.05.26 by SMs.xlsx", read_only=True)
ws = wb["PAN India MBOs"]

my_awds = {"BND", "ShriRam", "KAG", "GEB", "Balaji", "PTJ", "Savitri", "SUP"}
awd_to_id = {
    "BND": 194,
    "SUP": 196,
    "Savitri": 197,
    "Balaji": 198,
    "GEB": 199,
    "KAG": 200,
    "PTJ": 201,
    "ShriRam": 202,
}

conn = sqlite3.connect("centralized_db.sqlite3")
inserted = 0
errors = 0

for row in ws.iter_rows(values_only=True):
    if row[0] not in my_awds:
        continue
    name = str(row[2]).strip() if row[2] else None
    if not name or name == "Name of Retailer":
        continue
    dist_id = awd_to_id.get(row[0])
    area = str(row[3]).strip() if row[3] else None
    state = str(row[4]).strip() if row[4] else None
    location = f"{area}, {state}" if area and state else (area or state)
    phone = str(row[7]).strip() if row[7] else None
    address = str(row[6]).strip() if row[6] else None
    try:
        conn.execute(
            "INSERT INTO master_retailers (name, distributor_id, location, phone_number, address, created_at) VALUES (?,?,?,?,?,?)",
            (name, dist_id, location, phone, address, "2026-06-27T00:00:00+00:00"),
        )
        inserted += 1
    except Exception as e:
        print(f"Error: {e} — {name}")
        errors += 1

conn.commit()
conn.close()
print(f"Done! {inserted} inserted, {errors} errors")
