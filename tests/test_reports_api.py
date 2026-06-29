import pytest
import random
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

from app.models import SalesOrder, Invoice, InvoicePayment, Distributor, Retailer
from app.db import db
from app.web_app import create_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def create_test_distributor(client, name=None, territory="North"):
    """Helper to create a test distributor"""
    if name is None:
        name = f"Test Distributor {random.randint(1000, 9999)}"
    
    unique_id = random.randint(10000, 99999)
    response = client.post(
        "/api/v1/parties/distributors",
        json={
            "name": name,
            "gst_number": f"12ABC{unique_id}E5Z0",
            "territory": territory
        },
    )
    if response.status_code not in [200, 201]:
        print(f"Error creating distributor: {response.get_json()}")
    assert response.status_code in [200, 201], f"Failed to create distributor: {response.get_json()}"
    return response.get_json().get("data", {})


def create_test_retailer(client, distributor_id, name=None):
    """Helper to create a test retailer"""
    if name is None:
        name = f"Test Retailer {random.randint(1000, 9999)}"
    
    response = client.post(
        "/api/v1/parties/retailers",
        json={
            "distributor_id": distributor_id,
            "name": name,
            "location": "Test Location"
        },
    )
    if response.status_code not in [200, 201]:
        print(f"Error creating retailer: {response.get_json()}")
    assert response.status_code in [200, 201], f"Failed to create retailer: {response.get_json()}"
    return response.get_json().get("data", {})


def create_test_sales_order(client, distributor_id, retailer_id, amount=1000.0):
    """Helper to create a test sales order"""
    response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor_id,
            "retailer_id": retailer_id,
            "items": [
                {
                    "product_code": "P001",
                    "product_name": "Test Product",
                    "quantity": 10,
                    "unit_price": amount / 10
                }
            ]
        },
    )
    if response.status_code in [200, 201]:
        return response.get_json().get("data", {})
    return None


# ========== TEST REPORT 1: SALES REPORT ==========
def test_sales_report_by_distributor(client):
    """Test GET /api/v1/reports/sales?group_by=distributor"""
    dist1 = create_test_distributor(client, "Distributor A")
    dist2 = create_test_distributor(client, "Distributor B")
    ret1 = create_test_retailer(client, dist1["id"], "Retailer 1")
    ret2 = create_test_retailer(client, dist2["id"], "Retailer 2")
    
    # Create sales orders
    create_test_sales_order(client, dist1["id"], ret1["id"], 1000.0)
    create_test_sales_order(client, dist1["id"], ret1["id"], 2000.0)
    create_test_sales_order(client, dist2["id"], ret2["id"], 3000.0)
    
    response = client.get("/api/v1/reports/sales?group_by=distributor")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "data" in data
    assert isinstance(data["data"], list)
    
    # Verify data structure
    for item in data["data"]:
        assert "id" in item
        assert "name" in item
        assert "total_amount" in item
        assert "order_count" in item
        assert "avg_amount" in item


def test_sales_report_by_retailer(client):
    """Test GET /api/v1/reports/sales?group_by=retailer"""
    dist = create_test_distributor(client)
    ret1 = create_test_retailer(client, dist["id"], "Retailer X")
    ret2 = create_test_retailer(client, dist["id"], "Retailer Y")
    
    create_test_sales_order(client, dist["id"], ret1["id"], 5000.0)
    create_test_sales_order(client, dist["id"], ret2["id"], 7000.0)
    
    response = client.get("/api/v1/reports/sales?group_by=retailer")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert isinstance(data["data"], list)


def test_sales_report_by_territory(client):
    """Test GET /api/v1/reports/sales?group_by=territory"""
    dist1 = create_test_distributor(client, "Dist North", "North")
    dist2 = create_test_distributor(client, "Dist South", "South")
    ret1 = create_test_retailer(client, dist1["id"])
    ret2 = create_test_retailer(client, dist2["id"])
    
    create_test_sales_order(client, dist1["id"], ret1["id"], 5000.0)
    create_test_sales_order(client, dist2["id"], ret2["id"], 7000.0)
    
    response = client.get("/api/v1/reports/sales?group_by=territory")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert isinstance(data["data"], list)


def test_sales_report_with_date_filter(client):
    """Test sales report with start_date and end_date filters"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 1000.0)
    
    today = date.today()
    start_date = (today - timedelta(days=30)).isoformat()
    end_date = today.isoformat()
    
    response = client.get(
        f"/api/v1/reports/sales?group_by=distributor&start_date={start_date}&end_date={end_date}"
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def test_sales_report_invalid_group_by(client):
    """Test sales report with invalid group_by parameter"""
    response = client.get("/api/v1/reports/sales?group_by=invalid")
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


# ========== TEST REPORT 2: TRENDS REPORT ==========
def test_trends_report(client):
    """Test GET /api/v1/reports/trends"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    response = client.get("/api/v1/reports/trends?months_back=6")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "data" in data
    assert isinstance(data["data"], list)
    
    # Verify data structure
    for item in data["data"]:
        assert "month" in item
        assert "current_sales" in item
        assert "previous_sales" in item
        assert "growth_percent" in item
        assert "trend" in item
        assert item["trend"] in ["up", "down"]


def test_trends_report_invalid_months_back(client):
    """Test trends report with invalid months_back"""
    response = client.get("/api/v1/reports/trends?months_back=30")
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


# ========== TEST REPORT 3: TERRITORY PERFORMANCE ==========
def test_territory_performance_report(client):
    """Test GET /api/v1/reports/territory-performance"""
    dist_north = create_test_distributor(client, "North Dist", "North")
    dist_south = create_test_distributor(client, "South Dist", "South")
    ret_north = create_test_retailer(client, dist_north["id"])
    ret_south = create_test_retailer(client, dist_south["id"])
    
    create_test_sales_order(client, dist_north["id"], ret_north["id"], 5000.0)
    create_test_sales_order(client, dist_south["id"], ret_south["id"], 3000.0)
    
    response = client.get("/api/v1/reports/territory-performance")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "data" in data
    assert "grand_total" in data
    assert isinstance(data["data"], list)
    
    # Verify data structure
    for item in data["data"]:
        assert "rank" in item
        assert "territory" in item
        assert "total_sales" in item
        assert "percent_of_total" in item


# ========== TEST REPORT 4: DISTRIBUTOR SALES DETAIL ==========
def test_distributor_sales_detail(client):
    """Test GET /api/v1/reports/distributor-sales/{distributor_id}"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 10000.0)
    
    response = client.get(f"/api/v1/reports/distributor-sales/{dist['id']}")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "data" in data
    assert data["data"]["distributor_id"] == dist["id"]
    assert data["data"]["total_orders"] >= 1
    assert data["data"]["total_sales"] > 0


def test_distributor_sales_detail_not_found(client):
    """Test distributor sales detail with non-existent distributor"""
    response = client.get("/api/v1/reports/distributor-sales/99999")
    
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


# ========== TEST REPORT 5: CUSTOM REPORTS ==========
def test_custom_report_json_format(client):
    """Test POST /api/v1/reports/custom with JSON format"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    response = client.post(
        "/api/v1/reports/custom",
        json={
            "group_by": "distributor",
            "export_format": "json"
        },
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "data" in data
    assert "filters" in data
    assert "record_count" in data


def test_custom_report_with_filters(client):
    """Test custom report with various filters"""
    dist = create_test_distributor(client, "Test Dist")
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    response = client.post(
        "/api/v1/reports/custom",
        json={
            "group_by": "retailer",
            "filter_by": "territory",
            "filter_value": "North",
            "export_format": "json"
        },
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def test_custom_report_with_date_range(client):
    """Test custom report with date range"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    start_date = (date.today() - timedelta(days=30)).isoformat()
    end_date = date.today().isoformat()
    
    response = client.post(
        "/api/v1/reports/custom",
        json={
            "group_by": "distributor",
            "start_date": start_date,
            "end_date": end_date,
            "export_format": "json"
        },
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def test_custom_report_csv_format(client):
    """Test custom report with CSV format"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    response = client.post(
        "/api/v1/reports/custom",
        json={
            "group_by": "distributor",
            "export_format": "csv"
        },
    )
    
    assert response.status_code == 200
    # CSV format returns text response with different headers


def test_custom_report_invalid_export_format(client):
    """Test custom report with invalid export format"""
    response = client.post(
        "/api/v1/reports/custom",
        json={
            "group_by": "distributor",
            "export_format": "pdf"
        },
    )
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


# ========== AGGREGATE TESTS ==========
def test_all_reports_endpoints_available(client):
    """Verify all 5 report endpoints are accessible"""
    endpoints = [
        ("/api/v1/reports/sales", "GET"),
        ("/api/v1/reports/trends", "GET"),
        ("/api/v1/reports/territory-performance", "GET"),
        ("/api/v1/reports/custom", "POST"),
    ]
    
    for endpoint, method in endpoints:
        if method == "GET":
            response = client.get(endpoint)
        else:
            response = client.post(endpoint, json={})
        
        # Endpoints should either return 200 or require auth (401)
        assert response.status_code in [200, 400, 401], f"{endpoint} failed with {response.status_code}"


def test_reports_return_valid_json(client):
    """Verify all report responses are valid JSON"""
    dist = create_test_distributor(client)
    ret = create_test_retailer(client, dist["id"])
    create_test_sales_order(client, dist["id"], ret["id"], 5000.0)
    
    endpoints = [
        "/api/v1/reports/sales",
        "/api/v1/reports/trends",
        "/api/v1/reports/territory-performance",
    ]
    
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200
        
        # Verify it's valid JSON
        data = response.get_json()
        assert isinstance(data, dict)
        assert "success" in data
        assert "data" in data
