import os
import pytest
from datetime import date, datetime, timezone
from app.web_app import create_app
from app.db import db
from app.models import Distributor, Retailer, SalesOrder, SalesOrderItem, Invoice


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "business_brain_api.sqlite3"
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
    return app.test_client()


def create_sample_order(app, distributor_id, retailer_id, workspace_id='default', so_number=None):
    with app.app_context():
        so_number = so_number or f"SO-{workspace_id}-{distributor_id}-{retailer_id}-{datetime.now(timezone.utc).timestamp()}"
        order = SalesOrder(
            so_number=so_number,
            distributor_id=distributor_id,
            retailer_id=retailer_id,
            total_amount=5000.0,
            tax_amount=450.0,
            net_amount=4550.0,
            status='confirmed',
            workspace_id=workspace_id,
        )
        db.session.add(order)
        db.session.commit()

        item = SalesOrderItem(
            so_id=order.id,
            product_code='P001',
            product_name='Product One',
            quantity=10,
            unit_price=500.0,
            line_total=5000.0,
        )
        db.session.add(item)
        db.session.commit()
        return order.id


def create_sample_invoice(app, order_id, workspace_id='default'):
    with app.app_context():
        invoice = Invoice(
            invoice_number='INV-1001',
            so_id=order_id,
            invoice_date=date(2026, 6, 1),
            due_date=date(2026, 6, 10),
            total_amount=5000.0,
            tax_amount=450.0,
            net_amount=4550.0,
            paid_amount=1500.0,
            payment_status='pending',
            workspace_id=workspace_id,
        )
        db.session.add(invoice)
        db.session.commit()
        return invoice


def test_sales_summary_endpoint(client, app):
    with app.app_context():
        dist = Distributor(name='Dist A', workspace_id='default')
        db.session.add(dist)
        db.session.commit()
        ret = Retailer(name='Retail A', distributor_id=dist.id, workspace_id='default')
        db.session.add(ret)
        db.session.commit()
        create_sample_order(app, dist.id, ret.id, workspace_id='default')

    response = client.get('/api/intelligence/brain/sales-summary')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert 'total_sales' in data['data']


def test_sales_by_product_endpoint(client, app):
    with app.app_context():
        dist = Distributor(name='Dist B', workspace_id='default')
        db.session.add(dist)
        db.session.commit()
        ret = Retailer(name='Retail B', distributor_id=dist.id, workspace_id='default')
        db.session.add(ret)
        db.session.commit()
        create_sample_order(app, dist.id, ret.id, workspace_id='default')

    response = client.get('/api/intelligence/brain/sales-by-product')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert isinstance(data['data'], list)


def test_outstanding_summary_endpoint(client, app):
    with app.app_context():
        dist = Distributor(name='Dist C', workspace_id='default')
        db.session.add(dist)
        db.session.commit()
        ret = Retailer(name='Retail C', distributor_id=dist.id, workspace_id='default')
        db.session.add(ret)
        db.session.commit()
        order_id = create_sample_order(app, dist.id, ret.id, workspace_id='default')
        create_sample_invoice(app, order_id, workspace_id='default')

    response = client.get('/api/intelligence/brain/outstanding-summary')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert 'total_outstanding' in data['data']


def test_financial_kpis_endpoint(client, app):
    with app.app_context():
        dist = Distributor(name='Dist D', workspace_id='default')
        db.session.add(dist)
        db.session.commit()
        ret = Retailer(name='Retail D', distributor_id=dist.id, workspace_id='default')
        db.session.add(ret)
        db.session.commit()
        order_id = create_sample_order(app, dist.id, ret.id, workspace_id='default')
        create_sample_invoice(app, order_id, workspace_id='default')

    response = client.get('/api/intelligence/brain/financial-kpis')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert 'revenue' in data['data']


def test_sales_summary_workspace_isolation(client, app):
    with app.app_context():
        dist1 = Distributor(name='Dist X', workspace_id='workspace1')
        db.session.add(dist1)
        db.session.commit()
        ret1 = Retailer(name='Retail X', distributor_id=dist1.id, workspace_id='workspace1')
        db.session.add(ret1)
        db.session.commit()
        create_sample_order(app, dist1.id, ret1.id, workspace_id='workspace1')

        dist2 = Distributor(name='Dist Y', workspace_id='workspace2')
        db.session.add(dist2)
        db.session.commit()
        ret2 = Retailer(name='Retail Y', distributor_id=dist2.id, workspace_id='workspace2')
        db.session.add(ret2)
        db.session.commit()
        create_sample_order(app, dist2.id, ret2.id, workspace_id='workspace2')

    response1 = client.get('/api/intelligence/brain/sales-summary?workspace_id=workspace1')
    response2 = client.get('/api/intelligence/brain/sales-summary?workspace_id=workspace2')

    assert response1.status_code == 200
    assert response2.status_code == 200
    data1 = response1.get_json()
    data2 = response2.get_json()
    assert data1['success'] is True
    assert data2['success'] is True
    assert data1['data']['total_sales'] == 5000.0
    assert data2['data']['total_sales'] == 5000.0
    assert data1['data']['total_sales'] == data2['data']['total_sales']
