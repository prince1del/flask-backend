import sys
import requests

BASE_URL = "http://127.0.0.1:5000"
USERNAME = "bd_gt_north_head"
PASSWORD = "@Princeking123"

if len(sys.argv) < 2:
    print("Usage: python test_real_retailer_upload.py <path_to_file.csv_or_.xlsx>")
    sys.exit(1)

FILE_PATH = sys.argv[1]

login_resp = requests.post(
    f"{BASE_URL}/api/v1/auth/login",
    json={"username": USERNAME, "password": PASSWORD},
)
print("Login successful." if login_resp.status_code == 200 else "Login FAILED.")
login_resp.raise_for_status()
token = login_resp.json()["data"]["access_token"]

content_type = "text/csv" if FILE_PATH.lower().endswith(".csv") else \
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

with open(FILE_PATH, "rb") as f:
    upload_resp = requests.post(
        f"{BASE_URL}/api/v1/masters/retailers/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (FILE_PATH, f, content_type)},
    )

print("Status code:", upload_resp.status_code)
print("Response:")
print(upload_resp.text)
