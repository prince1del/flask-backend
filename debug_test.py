import pytest
import os
import sys

# Set up environment
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AUTH_ENABLED"] = "false"

from app.web_app import create_app

app = create_app()
app.config["TESTING"] = True
client = app.test_client()

# Create test data
import random

unique_id = random.randint(10000, 99999)
dist_resp = client.post(
    "/api/v1/parties/distributors",
    json={
        "name": f"Test Distributor {unique_id}",
        "gst_number": f"12ABC{unique_id}E5Z0",
        "territory": "North"
    },
)
print(f"Distributor response: {dist_resp.status_code}")
dist = dist_resp.get_json().get("data", {})
print(f"Distributor data: {dist}")

# Create retailer
unique_id2 = random.randint(10000, 99999)
ret_resp = client.post(
    "/api/v1/parties/retailers",
    json={
        "distributor_id": dist["id"],
        "name": f"Test Retailer {unique_id2}",
        "location": "Test Location"
    },
)
print(f"Retailer response: {ret_resp.status_code}")
ret = ret_resp.get_json().get("data", {})
print(f"Retailer data: {ret}")

# Create sales order
so_resp = client.post(
    "/api/v1/sales-orders",
    json={
        "distributor_id": dist["id"],
        "retailer_id": ret["id"],
        "items": [
            {
                "product_code": "P001",
                "product_name": "Test Product",
                "quantity": 10,
                "unit_price": 1000
            }
        ]
    },
)
print(f"Sales Order response: {so_resp.status_code}")

# Test the report endpoint
print(f"\nTesting report endpoint: /api/v1/reports/distributor-sales/{dist['id']}")
report_resp = client.get(f"/api/v1/reports/distributor-sales/{dist['id']}")
print(f"Report response status: {report_resp.status_code}")
print(f"Report response: {report_resp.get_json()}")
