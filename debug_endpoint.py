import requests

base_url = 'https://flask-backend-wnlq.onrender.com'

# Test the POST sales-orders endpoint
url = f'{base_url}/api/v1/sales-orders'
data = {
    "distributor_id": 1, 
    "retailer_id": 1, 
    "items": [{"product_code": "P001", "product_name": "Product 1", "quantity": 10, "unit_price": 100}]
}

print(f"Testing: POST {url}")
try:
    resp = requests.post(url, json=data, timeout=10)
    print(f"Status: {resp.status_code}")
    print(f"Response:\n{resp.text[:500]}")
except Exception as e:
    print(f"ERROR: {e}")
