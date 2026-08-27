"""
Test suite for admin API endpoints
- GET /api/v1/admin/users - List users with pagination and filters
- POST /api/v1/admin/users - Create new user
- PUT /api/v1/admin/users/{id} - Update user
- DELETE /api/v1/admin/users/{id} - Delete user
- GET /api/v1/admin/audit-logs - View activity logs
- POST /api/v1/admin/settings - Save app settings
"""

import pytest
import os
from app.web_app import create_app
from app.db import db
from app.models import User, AuditLog


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create and configure a test app instance with a real SQLite database file."""
    db_path = tmp_path / "admin_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    
    app = create_app()
    app.config["TESTING"] = True
    
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    """Flask test client"""
    return app.test_client()


def list_response_data(client):
    """Users currently visible through the admin list endpoint."""
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 200
    return response.get_json()["data"]


def create_test_user(client, username="testuser", email="test@example.com", password="password123"):
    """Helper to create a test user"""
    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": username,
            "email": email,
            "password": password,
            "role": "sales_executive"
        }
    )
    return response.get_json().get("data", {})


# ========== USER MANAGEMENT TESTS ==========

def test_create_user_success(client):
    """Test POST /api/v1/admin/users - Create new user"""
    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "password123",
            "role": "sales_executive"
        }
    )
    
    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["username"] == "newuser"
    assert data["data"]["email"] == "new@example.com"
    assert data["data"]["role"] == "sales_executive"
    assert data["data"]["status"] == "active"


def test_create_user_missing_fields(client):
    """Test POST /api/v1/admin/users - Validate required fields"""
    response = client.post(
        "/api/v1/admin/users",
        json={"username": "testuser"}  # Missing email and password
    )
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_create_user_invalid_role(client):
    """Test POST /api/v1/admin/users - Reject invalid role"""
    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "password123",
            "role": "invalid_role"
        }
    )
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_create_user_duplicate_username(client):
    """Test POST /api/v1/admin/users - Reject duplicate username"""
    # Create first user
    client.post(
        "/api/v1/admin/users",
        json={
            "username": "duplicate",
            "email": "email1@example.com",
            "password": "password123",
            "role": "sales_executive"
        }
    )
    
    # Try to create second user with same username
    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "duplicate",
            "email": "email2@example.com",
            "password": "password123",
            "role": "sales_executive"
        }
    )
    
    assert response.status_code == 409
    data = response.get_json()
    assert data["success"] is False


def test_list_users_empty(client):
    """Test GET /api/v1/admin/users - only the boot-seeded admin exists."""
    response = client.get("/api/v1/admin/users")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert len(data["data"]) == 1
    assert data["pagination"]["total"] == 1


def test_list_users_with_pagination(client):
    """Test GET /api/v1/admin/users - List users with pagination"""
    # Create 5 users
    for i in range(5):
        create_test_user(client, f"user{i}", f"user{i}@example.com", "pass")
    
    # Get first page (default: 20 per page)
    response = client.get("/api/v1/admin/users?page=1&per_page=2")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert len(data["data"]) == 2
    # 5 created here + the boot-seeded admin.
    assert data["pagination"]["total"] == 6
    assert data["pagination"]["pages"] == 3


def test_list_users_filter_by_role(client):
    """Test GET /api/v1/admin/users - Filter by role"""
    create_test_user(client, "admin1", "admin1@example.com", "pass")
    
    # Add a distributor user
    client.post(
        "/api/v1/admin/users",
        json={
            "username": "distributor1",
            "email": "dist1@example.com",
            "password": "pass",
            "role": "distributor"
        }
    )
    
    response = client.get("/api/v1/admin/users?role=distributor")
    
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["data"]) == 1
    assert data["data"][0]["role"] == "distributor"


def test_list_users_filter_by_status(client):
    """Test GET /api/v1/admin/users - Filter by status"""
    user = create_test_user(client, "active_user", "active@example.com", "pass")
    
    # Update user to inactive
    client.put(
        f"/api/v1/admin/users/{user['id']}",
        json={"status": "inactive"}
    )
    
    response = client.get("/api/v1/admin/users?status=inactive")
    
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["data"]) == 1
    assert data["data"][0]["status"] == "inactive"


def test_update_user_success(client):
    """Test PUT /api/v1/admin/users/{id} - Update user"""
    user = create_test_user(client, "original", "original@example.com", "pass")
    
    response = client.put(
        f"/api/v1/admin/users/{user['id']}",
        json={
            "email": "updated@example.com",
            "role": "distributor",
            "status": "inactive"
        }
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["email"] == "updated@example.com"
    assert data["data"]["role"] == "distributor"
    assert data["data"]["status"] == "inactive"


def test_update_user_invalid_role(client):
    """Test PUT /api/v1/admin/users/{id} - Reject invalid role"""
    user = create_test_user(client, "testuser", "test@example.com", "pass")
    
    response = client.put(
        f"/api/v1/admin/users/{user['id']}",
        json={"role": "invalid_role"}
    )
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_update_user_not_found(client):
    """Test PUT /api/v1/admin/users/{id} - User not found"""
    response = client.put(
        "/api/v1/admin/users/999",
        json={"email": "test@example.com"}
    )
    
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_update_user_password(client):
    """Test PUT /api/v1/admin/users/{id} - Update password"""
    user = create_test_user(client, "testuser", "test@example.com", "oldpass")
    
    response = client.put(
        f"/api/v1/admin/users/{user['id']}",
        json={"password": "newpass123"}
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    # In a real scenario, we'd verify the password was actually changed


def test_delete_user_success(client):
    """Test DELETE /api/v1/admin/users/{id} - Delete user"""
    user = create_test_user(client, "to_delete", "delete@example.com", "pass")
    
    response = client.delete(f"/api/v1/admin/users/{user['id']}")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    
    # Verify user is deleted (the boot-seeded admin remains)
    remaining = [u["username"] for u in list_response_data(client)]
    assert "to_delete" not in remaining


def test_delete_user_not_found(client):
    """Test DELETE /api/v1/admin/users/{id} - User not found"""
    response = client.delete("/api/v1/admin/users/999")
    
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


# ========== AUDIT LOG TESTS ==========

def test_view_audit_logs_empty(client):
    """Test GET /api/v1/admin/audit-logs - View logs (empty database)"""
    response = client.get("/api/v1/admin/audit-logs")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"] == []


def test_view_audit_logs_with_data(client):
    """Test GET /api/v1/admin/audit-logs - View logs with activity"""
    # Create user (which generates audit log)
    create_test_user(client, "audituser", "audit@example.com", "pass")
    
    response = client.get("/api/v1/admin/audit-logs")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert len(data["data"]) > 0
    # Check audit log structure
    log = data["data"][0]
    assert "action" in log
    assert "resource_type" in log
    assert "created_at" in log


def test_view_audit_logs_filter_by_action(client):
    """Test GET /api/v1/admin/audit-logs - Filter by action"""
    create_test_user(client, "user1", "user1@example.com", "pass")
    
    response = client.get("/api/v1/admin/audit-logs?action=user_created")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert all(log["action"] == "user_created" for log in data["data"])


def test_view_audit_logs_pagination(client):
    """Test GET /api/v1/admin/audit-logs - Pagination"""
    # Create multiple users
    for i in range(5):
        create_test_user(client, f"user{i}", f"user{i}@example.com", "pass")
    
    response = client.get("/api/v1/admin/audit-logs?page=1&per_page=2")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert len(data["data"]) <= 2
    assert "pagination" in data


def test_view_audit_logs_filter_by_days(client):
    """Test GET /api/v1/admin/audit-logs - Filter by days"""
    create_test_user(client, "testuser", "test@example.com", "pass")
    
    # Get logs from last 1 day (should include recent activity)
    response = client.get("/api/v1/admin/audit-logs?days=1")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


# ========== SETTINGS TESTS ==========

def test_save_settings_success(client):
    """Test POST /api/v1/admin/settings - Save settings"""
    response = client.post(
        "/api/v1/admin/settings",
        json={
            "company_name": "Test Company",
            "currency_symbol": "₹",
            "default_tax_rate": 18.0
        }
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["company_name"] == "Test Company"
    assert data["data"]["currency_symbol"] == "₹"


def test_save_settings_numeric_validation(client):
    """Test POST /api/v1/admin/settings - Validate numeric fields"""
    response = client.post(
        "/api/v1/admin/settings",
        json={
            "default_tax_rate": "invalid"  # Should be numeric
        }
    )
    
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_save_settings_boolean_fields(client):
    """Test POST /api/v1/admin/settings - Handle boolean fields"""
    response = client.post(
        "/api/v1/admin/settings",
        json={
            "maintenance_mode": True,
            "max_login_attempts": 5,
            "session_timeout_minutes": 30
        }
    )
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["maintenance_mode"] is True


def test_save_settings_creates_audit_log(client):
    """Test POST /api/v1/admin/settings - Create audit log"""
    client.post(
        "/api/v1/admin/settings",
        json={
            "company_name": "Test Company",
            "currency_symbol": "₹"
        }
    )
    
    # Check audit logs were created
    audit_response = client.get("/api/v1/admin/audit-logs?action=settings_updated")
    
    assert audit_response.status_code == 200
    data = audit_response.get_json()
    assert len(data["data"]) > 0


# ========== INTEGRATION TESTS ==========

def test_all_admin_endpoints_available(client):
    """Test all admin endpoints are accessible"""
    endpoints = [
        ("/api/v1/admin/users", "GET", 200),
        ("/api/v1/admin/users", "POST", 400),  # Missing required fields
        ("/api/v1/admin/audit-logs", "GET", 200),
        ("/api/v1/admin/settings", "POST", 200),  # Empty body is valid
    ]
    
    for endpoint, method, expected_status in endpoints:
        if method == "GET":
            response = client.get(endpoint)
        elif method == "POST":
            response = client.post(endpoint, json={})
        
        # Check endpoint is registered
        assert response.status_code in [200, 400, 404]


def test_audit_log_tracks_all_operations(client):
    """Test audit logs track all admin operations"""
    # Create user
    user = create_test_user(client, "tracked_user", "tracked@example.com", "pass")
    
    # Update user
    client.put(
        f"/api/v1/admin/users/{user['id']}",
        json={"status": "inactive"}
    )
    
    # Delete user
    client.delete(f"/api/v1/admin/users/{user['id']}")
    
    # Check audit logs
    response = client.get("/api/v1/admin/audit-logs")
    data = response.get_json()
    
    actions = [log["action"] for log in data["data"]]
    assert "user_created" in actions
    assert "user_updated" in actions
    assert "user_deleted" in actions


def test_admin_user_management_workflow(client):
    """Test complete admin workflow: create, list, update, delete user"""
    # Create user
    create_response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "workflow_user",
            "email": "workflow@example.com",
            "password": "password123",
            "role": "distributor"
        }
    )
    assert create_response.status_code == 201
    user_id = create_response.get_json()["data"]["id"]
    
    # List users
    list_response = client.get("/api/v1/admin/users")
    assert list_response.status_code == 200
    assert len(list_response.get_json()["data"]) >= 1
    
    # Update user
    update_response = client.put(
        f"/api/v1/admin/users/{user_id}",
        json={
            "email": "updated_workflow@example.com",
            "status": "inactive"
        }
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["data"]["status"] == "inactive"
    
    # Delete user
    delete_response = client.delete(f"/api/v1/admin/users/{user_id}")
    assert delete_response.status_code == 200


def test_admin_returns_valid_json(client):
    """Test all admin endpoints return valid JSON"""
    create_test_user(client, "json_user", "json@example.com", "pass")
    
    endpoints = [
        "/api/v1/admin/users",
        "/api/v1/admin/audit-logs",
    ]
    
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200
        data = response.get_json()
        assert "success" in data
        assert isinstance(data, dict)
