"""
Tests the real /api/v1/masters/distributors/bulk-upload endpoint
against your ACTUAL distributors.xlsx file — run this against your
live, running Flask server (python -m flask run must already be
running in another terminal).

Usage:
    python test_real_distributor_upload.py
"""
import requests

BASE_URL = "http://127.0.0.1:5000"
USERNAME = "bd_gt_north_head"
PASSWORD = "@Princeking123"
FILE_PATH = "distributors.xlsx"  # place your real file here, in this same folder

# Step 1: log in to get a real JWT token
login_resp = requests.post(
    f"{BASE_URL}/api/v1/auth/login",
    json={"username": USERNAME, "password": PASSWORD},
)
if login_resp.status_code != 200:
    print("LOGIN FAILED:", login_resp.status_code, login_resp.text)
    raise SystemExit(1)

token = login_resp.json()["data"]["access_token"]
print("Login successful.\n")

# Step 2: upload the real distributors.xlsx file
with open(FILE_PATH, "rb") as f:
    upload_resp = requests.post(
        f"{BASE_URL}/api/v1/masters/distributors/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (FILE_PATH, f)},
    )

print("Status code:", upload_resp.status_code)
print("Response:")
print(upload_resp.text)
