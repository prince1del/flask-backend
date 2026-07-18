import os
import pytest
from app.web_app import create_app
from app.db import db
from app.business_platform import AIDecisionFramework, BusinessKnowledgeGraph


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "intelligence_api.sqlite3"
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


def test_get_party_outstanding_empty(client):
    resp = client.get('/api/intelligence/brain/outstanding/1')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data']['party_id'] == 1


def test_get_order_summary_not_found(client):
    resp = client.get('/api/intelligence/brain/order-summary/12345')
    assert resp.status_code == 404
    data = resp.get_json()
    assert data['success'] is False


def test_check_business_rule_missing_rule_name(client):
    resp = client.post('/api/intelligence/rules/check', json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False
    assert 'rule_name is required' in data['message']


def test_get_workflow_definition(client):
    resp = client.get('/api/intelligence/workflow/sales_order')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data']['name'] == 'Sales Order'


def test_summarize_context(client):
    resp = client.post('/api/intelligence/context/summary', json={
        'workspace_id': 'default',
        'tenant_id': 'default',
        'payload': {'order': 1}
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert 'context' in data['data']
    assert 'summary' in data['data']


def test_recommend_decision(client):
    resp = client.post('/api/intelligence/decision/recommend', json={
        'order_value': 1000,
        'available_credit': 1200,
        'party_status': 'active'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data']['decision']['recommendation'] == 'approve'
    assert data['data']['decision']['cost'] >= 0
    assert isinstance(data['data']['decision']['optimization_suggestions'], list)
    assert data['data']['decision']['optimization_suggestions']


def test_ai_decision_framework_tracks_audit_trace():
    decision = AIDecisionFramework.recommend_purchase(
        1000,
        1200,
        party_status='active',
        party_id=42,
        workspace_id='ws-audit',
    )

    assert decision['recommendation'] == 'approve'
    assert 'audit_trace' in decision
    assert any(step['step'] == 'rules_check' for step in decision['audit_trace'])
    assert any(step['step'] == 'knowledge_graph_lookup' for step in decision['audit_trace'])


def test_knowledge_graph_persists_entities_per_workspace(app):
    BusinessKnowledgeGraph.add_entity('party', 'party-1', 'Acme Traders', {'status': 'active'}, workspace_id='ws-kg')
    BusinessKnowledgeGraph.add_relationship('party', 'party-1', 'related_to', 'party', 'party-2', {'confidence': 0.92}, workspace_id='ws-kg')

    entity = BusinessKnowledgeGraph.get_entity('party', 'party-1', workspace_id='ws-kg')

    assert entity['entity_id'] == 'party-1'
    assert entity['name'] == 'Acme Traders'
    assert entity['properties']['status'] == 'active'
    assert entity['relationships'][0]['type'] == 'related_to'
    assert entity['relationships'][0]['target'] == 'party-2'


def test_knowledge_graph_lookup(client):
    resp = client.get('/api/intelligence/knowledge-graph/party/abc123')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['data']['entity_type'] == 'party'


def test_business_memory_store_and_recall(client):
    event = {'type': 'inventory_update', 'details': {'item_id': 1}}
    resp = client.post('/api/intelligence/memory/store', json=event)
    assert resp.status_code == 200
    store_data = resp.get_json()
    assert store_data['success'] is True
    assert store_data['data']['stored'] is True

    resp = client.get('/api/intelligence/memory/recent')
    assert resp.status_code == 200
    recall_data = resp.get_json()
    assert recall_data['success'] is True
    assert recall_data['data']['total_events'] >= 1
    assert recall_data['data']['recent_events'][-1]['type'] == 'inventory_update'
