import pytest
import os
from app.web_app import create_app
from app.db import db
from app.models import Inventory, InventoryMovement


@pytest.fixture
def app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["AUTH_ENABLED"] = "false"
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def create_inventory_item(app, item_code='ITEM001', name='Test Item', warehouse='default'):
    with app.app_context():
        item = Inventory(
            item_code=item_code,
            item_name=name,
            warehouse_id=warehouse,
            quantity_on_hand=100,
            reorder_level=10,
            reorder_quantity=50,
            unit_cost=10.0,
            unit_price=15.0
        )
        db.session.add(item)
        db.session.commit()
        return item.to_dict()


def test_list_inventory_empty(client):
    resp = client.get('/api/inventory')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data'] == []


def test_create_and_list_inventory(app, client):
    item = create_inventory_item(app)
    resp = client.get('/api/inventory')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['data']) == 1
    assert data['data'][0]['item_code'] == item['item_code']


def test_adjust_inventory_receipt(app, client):
    item = create_inventory_item(app)
    item_id = item['id']
    resp = client.post('/api/inventory/adjust', json={
        'item_id': item_id,
        'quantity': 50,
        'movement_type': 'receipt',
        'reason': 'Stock received',
        'reference_number': 'RCPT-123'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data']['item']['quantity_on_hand'] == 150


def test_adjust_inventory_issue_insufficient(app, client):
    item = create_inventory_item(app)
    item_id = item['id']
    # Try to issue more than available
    resp = client.post('/api/inventory/adjust', json={
        'item_id': item_id,
        'quantity': 1000,
        'movement_type': 'issue',
        'reason': 'Ship out',
    })
    assert resp.status_code == 400


def test_inventory_history(app, client):
    item = create_inventory_item(app)
    item_id = item['id']
    # Perform movements
    client.post('/api/inventory/adjust', json={
        'item_id': item_id,
        'quantity': 20,
        'movement_type': 'receipt',
        'reason': 'Restock'
    })
    client.post('/api/inventory/adjust', json={
        'item_id': item_id,
        'quantity': 10,
        'movement_type': 'issue',
        'reason': 'Sold'
    })
    resp = client.get(f'/api/inventory/{item_id}/history')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'movements' in data['data']
    assert len(data['data']['movements']) >= 2


def test_update_min_max_levels(app, client):
    item = create_inventory_item(app)
    item_id = item['id']
    resp = client.put(f'/api/inventory/{item_id}/min-max', json={'reorder_level': 5, 'reorder_quantity': 20})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['data']['reorder_level'] == 5
    assert data['data']['reorder_quantity'] == 20


def test_low_stock_items(app, client):
    # Create item below reorder level
    with app.app_context():
        item = Inventory(
            item_code='LOW001',
            item_name='Low Item',
            warehouse_id='default',
            quantity_on_hand=2,
            reorder_level=10,
            reorder_quantity=20,
            unit_cost=5.0,
            unit_price=8.0
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    resp = client.get('/api/inventory/low-stock')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total_items'] >= 1
