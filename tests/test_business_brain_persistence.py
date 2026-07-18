import json
import os
from datetime import date

import pytest
from app.web_app import create_app
from app.db import db
from app.business_platform import EventEngine, ConversationEngine, WorkflowEngine
from app.models import Conversation, Workflow, BusinessRule, AIResponse, WorkflowStep, WorkflowStepExecution, RuleExecution, ConversationContext, WorkflowExecution, WorkflowNote, WorkflowExecutionStatusHistory, Distributor, Retailer, SalesOrder, Invoice


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "business_brain_persistence.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_business_brain_entities_persist_to_db(app):
    with app.app_context():
        conv = Conversation(workspace_id='ws-1', title='Persistent chat')
        db.session.add(conv)
        db.session.commit()

        wf = Workflow(workspace_id='ws-1', name='Persisted workflow', definition={'steps': []})
        db.session.add(wf)
        db.session.commit()

        rule = BusinessRule(workspace_id='ws-1', name='Persisted rule', definition={'all': [{'field': 'amount', 'op': '>=', 'value': 1}]}, priority=5)
        db.session.add(rule)
        db.session.commit()

        assert Conversation.query.count() == 1
        assert Workflow.query.count() == 1
        assert BusinessRule.query.count() == 1


def test_business_brain_event_persistence(app):
    with app.app_context():
        subscription = EventEngine.create_subscription('inventory_adjusted', 'ws-1', {'callback_url': 'https://example.test/hook'})
        event = EventEngine.publish('inventory_adjusted', 'ws-1', {'item_id': 1, 'quantity': 5})

        assert subscription['event_type'] == 'inventory_adjusted'
        assert event['event_type'] == 'inventory_adjusted'
        assert EventEngine.list_events('ws-1')[0]['data']['item_id'] == 1
        assert EventEngine.list_subscriptions('ws-1')[0]['event_type'] == 'inventory_adjusted'


def test_business_brain_event_replay_supports_workspace_history(app):
    with app.app_context():
        EventEngine.publish('inventory_adjusted', 'ws-1', {'item_id': 1, 'quantity': 5})
        EventEngine.publish('inventory_adjusted', 'ws-1', {'item_id': 2, 'quantity': 3})
        EventEngine.publish('payment_received', 'ws-1', {'invoice_id': 42})

        replay = EventEngine.replay_events('ws-1')
        assert len(replay) == 3
        assert replay[0]['event_type'] == 'inventory_adjusted'
        assert replay[0]['data']['item_id'] == 1
        assert replay[-1]['event_type'] == 'payment_received'
        assert replay[-1]['data']['invoice_id'] == 42

        filtered = EventEngine.replay_events('ws-1', 'inventory_adjusted')
        assert len(filtered) == 2
        assert all(item['event_type'] == 'inventory_adjusted' for item in filtered)


def test_business_brain_event_publish_queues_for_realtime_delivery(app):
    with app.app_context():
        observed = []

        EventEngine.subscribe('inventory_adjusted', 'ws-queue-1', lambda event: observed.append(event))
        EventEngine.publish('inventory_adjusted', 'ws-queue-1', {'item_id': 7, 'quantity': 1})

        pending = EventEngine.get_pending_events('ws-queue-1')
        assert len(pending) == 1
        assert pending[0]['event_type'] == 'inventory_adjusted'
        assert pending[0]['data']['item_id'] == 7
        assert observed[-1]['data']['item_id'] == 7

        drained = EventEngine.drain_queue('ws-queue-1', 'inventory_adjusted')
        assert len(drained) == 1
        assert drained[0]['event_type'] == 'inventory_adjusted'
        assert EventEngine.get_pending_events('ws-queue-1') == []


def test_business_brain_subscription_delivers_via_callback(app):
    with app.app_context():
        captured = []

        def fake_post(url, json):
            captured.append((url, json))
            return type('Response', (), {'status_code': 200})()

        import app.business_platform.event_engine as event_mod
        original_post = event_mod.requests.post if hasattr(event_mod, 'requests') else None
        event_mod.requests = type('Requests', (), {'post': staticmethod(fake_post)})()

        try:
            EventEngine.create_subscription('inventory_adjusted', 'ws-cb', {'callback_url': 'https://example.test/hook'})
            EventEngine.publish('inventory_adjusted', 'ws-cb', {'item_id': 9, 'quantity': 2})
        finally:
            if original_post is not None:
                event_mod.requests = original_post

        assert len(captured) == 1
        assert captured[0][0] == 'https://example.test/hook'
        assert captured[0][1]['event_type'] == 'inventory_adjusted'
        assert captured[0][1]['data']['item_id'] == 9


def test_business_brain_subscription_deduplicates_callback_handlers(app):
    with app.app_context():
        captured = []

        def fake_post(url, json):
            captured.append((url, json))
            return type('Response', (), {'status_code': 200})()

        import app.business_platform.event_engine as event_mod
        original_post = event_mod.requests.post if hasattr(event_mod.requests, 'post') else None
        event_mod.requests = type('Requests', (), {'post': staticmethod(fake_post)})

        try:
            EventEngine.create_subscription('inventory_adjusted', 'ws-dedup', {'callback_url': 'https://example.test/hook'})
            EventEngine.create_subscription('inventory_adjusted', 'ws-dedup', {'callback_url': 'https://example.test/hook'})
            EventEngine.publish('inventory_adjusted', 'ws-dedup', {'item_id': 42, 'quantity': 1})
        finally:
            if original_post is not None:
                event_mod.requests = type('Requests', (), {'post': staticmethod(original_post)})

        assert len(captured) == 1
        assert captured[0][1]['data']['item_id'] == 42


def test_business_brain_subscription_delivers_after_restart_from_persisted_subscriptions(app):
    with app.app_context():
        captured = []

        def fake_post(url, json):
            captured.append((url, json))
            return type('Response', (), {'status_code': 200})()

        import app.business_platform.event_engine as event_mod
        original_post = event_mod.requests.post if hasattr(event_mod.requests, 'post') else None
        event_mod.requests = type('Requests', (), {'post': staticmethod(fake_post)})

        try:
            EventEngine.create_subscription('inventory_adjusted', 'ws-restart', {'callback_url': 'https://example.test/restart'})
            EventEngine._subscribers.clear()
            EventEngine.publish('inventory_adjusted', 'ws-restart', {'item_id': 99, 'quantity': 4})
        finally:
            if original_post is not None:
                event_mod.requests = type('Requests', (), {'post': staticmethod(original_post)})

        assert len(captured) == 1
        assert captured[0][0] == 'https://example.test/restart'
        assert captured[0][1]['data']['item_id'] == 99


def test_event_engine_publish_uses_redis_queue_when_configured(app, monkeypatch):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        class DummyRedisClient:
            def __init__(self):
                self.published = []

            def publish(self, channel, payload):
                self.published.append((channel, payload))
                return 1

        dummy_client = DummyRedisClient()
        monkeypatch.setattr(CacheManager, '_get_redis_client', staticmethod(lambda: dummy_client))

        EventEngine.publish('inventory_adjusted', 'ws-queue', {'item_id': 7, 'quantity': 2})

        assert dummy_client.published[-1][0] == 'events:ws-queue:inventory_adjusted'
        assert json.loads(dummy_client.published[-1][1])['data']['item_id'] == 7


def test_event_engine_inventory_adjusted_invalidates_inventory_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('inventory:ws-inv:9', {'stock': 1}, ttl_minutes=5)
        EventEngine.publish('inventory_adjusted', 'ws-inv', {'item_id': 9, 'warehouse_id': 'W1', 'previous_qty': 1, 'new_qty': 2, 'adjustment': 1})

        assert CacheManager.get_cache('inventory:ws-inv:9') is None


def test_event_engine_inventory_adjusted_invalidates_valuation_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('inventory_valuation:ws-inv', {'total_valuation': 100}, ttl_minutes=5)
        EventEngine.publish('inventory_adjusted', 'ws-inv', {'item_id': 9, 'warehouse_id': 'W1', 'previous_qty': 1, 'new_qty': 2, 'adjustment': 1})

        assert CacheManager.get_cache('inventory_valuation:ws-inv') is None


def test_business_brain_get_low_stock_items_uses_cached_result(app):
    with app.app_context():
        from app.business_platform.business_brain import BusinessBrain
        from app.business_platform.cache_manager import CacheManager

        expected = [{'item_id': 9, 'item_code': 'SKU-9', 'item_name': 'Widget', 'warehouse_id': 'W1', 'quantity_on_hand': 1, 'reorder_level': 5, 'shortage': 4.0}]
        CacheManager.set_cache('low_stock_items:ws-cache', expected, ttl_minutes=5)

        assert BusinessBrain.get_low_stock_items('ws-cache') == expected


def test_event_engine_inventory_adjusted_invalidates_low_stock_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('low_stock_items:ws-inv', [{'item_id': 9}], ttl_minutes=5)
        EventEngine.publish('inventory_adjusted', 'ws-inv', {'item_id': 9, 'warehouse_id': 'W1', 'previous_qty': 1, 'new_qty': 2, 'adjustment': 1})

        assert CacheManager.get_cache('low_stock_items:ws-inv') is None


def test_business_brain_calculate_outstanding_uses_cached_result(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager
        from app.business_platform.business_brain import BusinessBrain

        expected = {
            'party_id': 42,
            'outstanding': 123.45,
            'overdue': 12.34,
            'current': 111.11,
            'last_updated': '2026-06-30T00:00:00+00:00',
            'payment_terms': 30,
            'credit_limit': 500.0,
            'available_credit': 376.55,
        }
        CacheManager.set_cache('outstanding:ws-cache:42', expected, ttl_minutes=5)

        assert BusinessBrain.calculate_outstanding('ws-cache', 42) == expected


def test_event_engine_payment_received_invalidates_outstanding_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('outstanding:ws-ledger:42', {'outstanding': 100}, ttl_minutes=5)
        EventEngine.publish('payment_received', 'ws-ledger', {'party_id': 42, 'amount': 25})

        assert CacheManager.get_cache('outstanding:ws-ledger:42') is None


def test_event_engine_payment_received_invalidates_ledger_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('ledger:ws-ledger:42', {'outstanding': 100}, ttl_minutes=5)
        EventEngine.publish('payment_received', 'ws-ledger', {'party_id': 42, 'amount': 25})

        assert CacheManager.get_cache('ledger:ws-ledger:42') is None

def test_event_engine_invoice_created_invalidates_finance_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('outstanding:ws-finance:party=42', {'outstanding': 50}, ttl_minutes=5)
        EventEngine.publish('invoice_created', 'ws-finance', {'invoice_id': 101, 'party_id': 42, 'amount': 1000})

        assert CacheManager.get_cache('outstanding:ws-finance:party=42') is None


def test_event_engine_sales_order_created_invalidates_sales_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        cache_key = 'sales_summary:ws-sales:start_date=2026-01-01:end_date=2026-01-31:retailer_id=any:product_code=any'
        CacheManager.set_cache(cache_key, {'total_sales': 999}, ttl_minutes=5)
        EventEngine.publish('sales_order_created', 'ws-sales', {'order_id': 202, 'total': 1000})

        assert CacheManager.get_cache(cache_key) is None


def test_rules_engine_cache_invalidates_on_rule_update(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager
        from app.business_platform.rules_engine import RulesEngine

        rule = RulesEngine.create_rule('ws-rules', {'name': 'check_credit_limit', 'definition': {'credit_limit': 1200}})
        assert RulesEngine._get_rule_definition('ws-rules', 'check_credit_limit') == {'credit_limit': 1200}

        RulesEngine.update_rule('ws-rules', str(rule['id']), {'definition': {'credit_limit': 1800}})

        assert RulesEngine._get_rule_definition('ws-rules', 'check_credit_limit') == {'credit_limit': 1800}
        assert CacheManager.get_cache('rule_definition:ws-rules:check_credit_limit') == {'credit_limit': 1800}


def test_business_brain_ai_response_persistence(app):
    with app.app_context():
        response = AIResponse(
            workspace_id='ws-1',
            conversation_id=1,
            prompt='Hello',
            response_text='Hi there',
            status='completed',
            token_count=4,
            latency_ms=120,
        )
        db.session.add(response)
        db.session.commit()

        stored = AIResponse.query.filter_by(workspace_id='ws-1').first()
        assert stored is not None
        assert stored.response_text == 'Hi there'
        assert stored.status == 'completed'


def test_business_brain_workflow_step_persistence(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        step = WorkflowStep(workflow_id=workflow.id, step_type='manual', config={'label': 'Approve'}, order_index=1)
        db.session.add(step)
        db.session.commit()

        execution = WorkflowStepExecution(workflow_execution_id=1, workflow_step_id=step.id, status='completed', input_data={'x': 1}, output_data={'y': 2})
        db.session.add(execution)
        db.session.commit()

        rule_execution = RuleExecution(rule_id=1, workspace_id='ws-1', result={'passed': True}, status='completed')
        db.session.add(rule_execution)
        db.session.commit()

        assert WorkflowStep.query.filter_by(workflow_id=workflow.id).count() == 1
        assert WorkflowStepExecution.query.filter_by(workflow_step_id=step.id).count() == 1
        assert RuleExecution.query.filter_by(workspace_id='ws-1').count() == 1


def test_business_brain_workflow_execution_history_persistence(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow History', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        execution = WorkflowExecution(workflow_id=workflow.id, workspace_id='ws-1', status='running', input_data={'foo': 'bar'})
        db.session.add(execution)
        db.session.commit()

        stored = WorkflowExecution.query.filter_by(workflow_id=workflow.id).first()
        assert stored is not None
        assert stored.status == 'running'
        assert stored.input_data == {'foo': 'bar'}


def test_business_brain_workflow_execution_status_history_persistence(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow Status History', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        execution = WorkflowExecution(workflow_id=workflow.id, workspace_id='ws-1', status='running', input_data={})
        db.session.add(execution)
        db.session.commit()

        history = WorkflowExecutionStatusHistory(workflow_execution_id=execution.id, status='paused', notes='Waiting for approval')
        db.session.add(history)
        db.session.commit()

        stored = WorkflowExecutionStatusHistory.query.filter_by(workflow_execution_id=execution.id).first()
        assert stored is not None
        assert stored.status == 'paused'
        assert stored.notes == 'Waiting for approval'


def test_business_brain_workflow_execution_status_history_exposed_in_execution_payload(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow Lifecycle', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        execution = WorkflowEngine.start_workflow(str(workflow.id), {'trigger': 'test'})
        assert execution is not None
        assert execution['status'] == 'running'
        assert execution['status_history'][0]['status'] == 'running'

        execution = WorkflowEngine.pause_execution(str(workflow.id), str(execution['id']))
        assert execution['status'] == 'paused'
        assert [entry['status'] for entry in execution['status_history']] == ['running', 'paused']


def test_business_brain_workflow_definition_is_retrieved_from_persistence(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {
            'name': 'Persisted workflow',
            'definition': {'steps': [{'id': 's1', 'type': 'manual'}]},
        })

        stored = WorkflowEngine.get_workflow(str(workflow['id']))
        assert stored is not None
        assert stored['name'] == 'Persisted workflow'
        assert stored['definition']['steps'][0]['id'] == 's1'

        named = WorkflowEngine.get_workflow('Persisted workflow')
        assert named is not None
        assert named['name'] == 'Persisted workflow'
        assert named['definition']['steps'][0]['id'] == 's1'


def test_business_brain_workflow_step_persistence_via_engine(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Step workflow', 'definition': {'steps': []}})

        added_step = WorkflowEngine.add_step(str(workflow['id']), {'step_type': 'manual', 'config': {'label': 'Approve'}, 'order_index': 1})

        assert added_step is not None
        assert added_step['step_type'] == 'manual'
        assert WorkflowStep.query.filter_by(workflow_id=workflow['id']).count() == 1


def test_business_brain_workflow_resume_recovers_state_from_history(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Recoverable workflow', 'definition': {'steps': []}})
        execution = WorkflowEngine.start_workflow(str(workflow['id']), {'trigger': 'recover'})

        assert execution['status'] == 'running'
        paused = WorkflowEngine.pause_execution(str(workflow['id']), str(execution['id']))
        assert paused['status'] == 'paused'

        resumed = WorkflowEngine.resume_execution(str(workflow['id']), str(execution['id']))
        assert resumed['status'] == 'running'
        assert resumed['status_history'][-1]['status'] == 'running'


def test_business_brain_workflow_lifecycle_events_are_published(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Lifecycle workflow', 'definition': {'steps': []}})

        started = WorkflowEngine.start_workflow(str(workflow['id']), {'trigger': 'lifecycle'})
        paused = WorkflowEngine.pause_execution(str(workflow['id']), str(started['id']))
        resumed = WorkflowEngine.resume_execution(str(workflow['id']), str(paused['id']))
        cancelled = WorkflowEngine.cancel_execution(str(workflow['id']), str(resumed['id']))

        events = EventEngine.list_events('ws-1')
        event_types = [event['event_type'] for event in events]

        assert 'workflow_started' in event_types
        assert 'workflow_paused' in event_types
        assert 'workflow_resumed' in event_types
        assert 'workflow_cancelled' in event_types
        assert any(event['event_type'] == 'workflow_cancelled' and event['data'].get('finished_at') for event in events)
        assert cancelled['status'] == 'cancelled'


def test_business_brain_workflow_resume_and_cancel_track_finish_state(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Recovery workflow', 'definition': {'steps': []}})

        execution = WorkflowEngine.start_workflow(str(workflow['id']), {'trigger': 'resume-check'})
        paused = WorkflowEngine.pause_execution(str(workflow['id']), str(execution['id']))
        resumed = WorkflowEngine.resume_execution(str(workflow['id']), str(paused['id']))
        cancelled = WorkflowEngine.cancel_execution(str(workflow['id']), str(resumed['id']))

        assert resumed['status'] == 'running'
        assert resumed['finished_at'] is None
        assert cancelled['status'] == 'cancelled'
        assert cancelled['finished_at'] is not None


def test_business_brain_workflow_update_execution_tracks_finished_timestamp(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Finish state workflow', 'definition': {'steps': []}})
        execution = WorkflowEngine.start_workflow(str(workflow['id']), {'trigger': 'terminal'})

        updated = WorkflowEngine.update_execution(str(workflow['id']), str(execution['id']), {'status': 'completed', 'output_data': {'result': 'ok'}})

        assert updated['status'] == 'completed'
        assert updated['finished_at'] is not None


def test_business_brain_workflow_resume_clears_finished_at_after_cancel(app):
    with app.app_context():
        workflow = WorkflowEngine.create_workflow('ws-1', {'name': 'Recoverable cancellation workflow', 'definition': {'steps': []}})

        execution = WorkflowEngine.start_workflow(str(workflow['id']), {'trigger': 'cancel-then-resume'})
        cancelled = WorkflowEngine.cancel_execution(str(workflow['id']), str(execution['id']))
        resumed = WorkflowEngine.resume_execution(str(workflow['id']), str(cancelled['id']))

        assert cancelled['status'] == 'cancelled'
        assert cancelled['finished_at'] is not None
        assert resumed['status'] == 'running'
        assert resumed['finished_at'] is None


def test_business_brain_workflow_transition_publishes_event(app):
    with app.app_context():
        distributor = Distributor(uuid='dist-4', name='Dist Four', email='dist4@example.com', phone='444', address='A', city='C', state='S', pin_code='44444')
        retailer = Retailer(uuid='ret-4', name='Retailer Four', distributor_id=1, email='ret4@example.com', phone='555', address='B', city='C', state='S', pin_code='55555')
        db.session.add_all([distributor, retailer])
        db.session.commit()

        order = SalesOrder(so_number='SO-TRANS-EVENT', distributor_id=distributor.id, retailer_id=retailer.id, workspace_id='ws-event', status='draft')
        db.session.add(order)
        db.session.commit()

        WorkflowEngine.transition('ws-event', str(order.id), 'confirmed', 'draft')

        events = EventEngine.list_events('ws-event')
        assert any(event['event_type'] == 'workflow_transitioned' for event in events)


def test_business_brain_workflow_transition_persists_order_state(app):
    with app.app_context():
        distributor = Distributor(uuid='dist-1', name='Dist One', email='dist@example.com', phone='123', address='A', city='C', state='S', pin_code='12345')
        retailer = Retailer(uuid='ret-1', name='Retailer One', distributor_id=1, email='ret@example.com', phone='456', address='B', city='C', state='S', pin_code='54321')
        db.session.add_all([distributor, retailer])
        db.session.commit()

        order = SalesOrder(so_number='SO-TRANS-1', distributor_id=distributor.id, retailer_id=retailer.id, workspace_id='ws-trans', status='draft')
        db.session.add(order)
        db.session.commit()

        success, new_state = WorkflowEngine.transition('ws-trans', str(order.id), 'confirmed', 'draft')

        assert success is True
        assert new_state == 'confirmed'
        db.session.refresh(order)
        assert order.status == 'confirmed'


def test_business_brain_invoice_transition_uses_invoice_workflow(app):
    with app.app_context():
        distributor = Distributor(uuid='dist-2', name='Dist Two', email='dist2@example.com', phone='222', address='A', city='C', state='S', pin_code='22222')
        retailer = Retailer(uuid='ret-2', name='Retailer Two', distributor_id=1, email='ret2@example.com', phone='333', address='B', city='C', state='S', pin_code='33333')
        db.session.add_all([distributor, retailer])
        db.session.commit()

        invoice = Invoice(
            invoice_number='INV-12345',
            so_id=1,
            invoice_date=date(2026, 6, 30),
            due_date=date(2026, 7, 10),
            total_amount=1000,
            tax_amount=100,
            net_amount=900,
            paid_amount=0,
            workspace_id='ws-invoice',
        )
        db.session.add(invoice)
        db.session.commit()

        allowed, reason = WorkflowEngine.can_transition('ws-invoice', str(invoice.id), 'draft', 'sent')

        assert allowed is True
        assert reason == 'Transition allowed'


def test_business_brain_invoice_transition_persists_invoice_state(app):
    with app.app_context():
        distributor = Distributor(uuid='dist-3', name='Dist Three', email='dist3@example.com', phone='321', address='A', city='C', state='S', pin_code='32123')
        retailer = Retailer(uuid='ret-3', name='Retailer Three', distributor_id=1, email='ret3@example.com', phone='654', address='B', city='C', state='S', pin_code='65432')
        db.session.add_all([distributor, retailer])
        db.session.commit()

        invoice = Invoice(
            invoice_number='INV-TRANS-1',
            so_id=1,
            invoice_date=date(2026, 6, 30),
            due_date=date(2026, 7, 10),
            total_amount=1000,
            tax_amount=100,
            net_amount=900,
            paid_amount=0,
            workspace_id='ws-invoice-state',
            payment_status='draft',
        )
        db.session.add(invoice)
        db.session.commit()

        success, new_state = WorkflowEngine.transition('ws-invoice-state', str(invoice.id), 'sent', 'draft')

        assert success is True
        assert new_state == 'sent'
        db.session.refresh(invoice)
        assert invoice.payment_status == 'sent'


def test_business_brain_workflow_step_execution_is_persisted(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow Step Log', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        step = WorkflowStep(workflow_id=workflow.id, step_type='manual', config={'label': 'Approve'}, order_index=1)
        db.session.add(step)
        db.session.commit()

        execution = WorkflowExecution(workflow_id=workflow.id, workspace_id='ws-1', status='running')
        db.session.add(execution)
        db.session.commit()

        outcome = WorkflowEngine.execute_step({
            'workflow_execution_id': execution.id,
            'workflow_step_id': step.id,
            'status': 'completed',
            'input': {'approved': True},
            'output': {'result': 'approved'},
        })

        assert outcome['status'] == 'completed'
        assert WorkflowStepExecution.query.filter_by(workflow_execution_id=execution.id, workflow_step_id=step.id).count() == 1


def test_workflow_creation_requires_workspace_id(app):
    with app.app_context():
        with pytest.raises(ValueError, match='workspace_id is required'):
            WorkflowEngine.create_workflow('', {'name': 'Invalid workflow'})


def test_cache_manager_uses_memory_backend_without_redis_config(app, monkeypatch):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        monkeypatch.delenv('REDIS_URL', raising=False)
        assert CacheManager._backend_kind() == 'memory'


def test_cache_manager_uses_redis_backend_when_available(app, monkeypatch):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        class DummyRedis:
            pass

        monkeypatch.setattr(CacheManager, '_get_redis_client', staticmethod(lambda: DummyRedis()))
        assert CacheManager._backend_kind() == 'redis'


def test_cache_manager_invalidates_expired_and_pattern_entries(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        assert CacheManager.set_cache('inventory:ws-1:1', {'stock': 10}, ttl_minutes=1) is True
        assert CacheManager.get_cache('inventory:ws-1:1') == {'stock': 10}

        CacheManager.invalidate_pattern('inventory:*ws-1*')
        assert CacheManager.get_cache('inventory:ws-1:1') is None


def test_cache_manager_invalidate_inventory_removes_only_requested_item_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('inventory:ws-cache-1:42', {'stock': 20}, ttl_minutes=10)
        CacheManager.set_cache('inventory:ws-cache-1:99', {'stock': 30}, ttl_minutes=10)

        assert CacheManager.invalidate_inventory('ws-cache-1', '42') is True
        assert CacheManager.get_cache('inventory:ws-cache-1:42') is None
        assert CacheManager.get_cache('inventory:ws-cache-1:99') == {'stock': 30}


def test_cache_manager_invalidate_inventory_removes_workspace_cache(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('inventory:ws-cache-1:42', {'stock': 20}, ttl_minutes=10)
        CacheManager.set_cache('inventory:ws-cache-2:42', {'stock': 30}, ttl_minutes=10)

        assert CacheManager.invalidate_inventory('ws-cache-1') is True
        assert CacheManager.get_cache('inventory:ws-cache-1:42') is None
        assert CacheManager.get_cache('inventory:ws-cache-2:42') == {'stock': 30}


def test_cache_manager_uses_redis_backend_when_configured_for_storage(app, monkeypatch):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        class DummyRedis:
            def __init__(self):
                self.store = {}

            def setex(self, key, ttl_seconds, value):
                self.store[key] = value

            def get(self, key):
                return self.store.get(key)

            def delete(self, key):
                return 1 if self.store.pop(key, None) is not None else 0

        dummy_client = DummyRedis()
        monkeypatch.setattr(CacheManager, '_get_redis_client', staticmethod(lambda: dummy_client))
        CacheManager._cache.clear()

        assert CacheManager.set_cache('inventory:ws-redis:1', {'stock': 10}, ttl_minutes=1) is True
        assert dummy_client.store['inventory:ws-redis:1'] == '{"stock": 10}'
        assert CacheManager.get_cache('inventory:ws-redis:1') == {'stock': 10}
        assert CacheManager.delete_cache('inventory:ws-redis:1') is True
        assert CacheManager.get_cache('inventory:ws-redis:1') is None


def test_cache_manager_invalidate_pattern_removes_redis_keys(app, monkeypatch):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        class DummyRedis:
            def __init__(self):
                self.store = {}

            def keys(self, pattern):
                return [key for key in self.store if pattern == key or __import__('fnmatch').fnmatch(key, pattern)]

            def delete(self, key):
                return 1 if self.store.pop(key, None) is not None else 0

        dummy_client = DummyRedis()
        dummy_client.store['inventory:ws-redis-pattern:1'] = '{"stock": 11}'
        monkeypatch.setattr(CacheManager, '_get_redis_client', staticmethod(lambda: dummy_client))
        CacheManager._cache.clear()

        CacheManager.invalidate_pattern('inventory:*ws-redis-pattern*')

        assert 'inventory:ws-redis-pattern:1' not in dummy_client.store


def test_cache_manager_delete_removes_specific_key(app):
    with app.app_context():
        from app.business_platform.cache_manager import CacheManager

        CacheManager.set_cache('inventory:ws-delete:7', {'stock': 5}, ttl_minutes=5)
        assert CacheManager.delete_cache('inventory:ws-delete:7') is True
        assert CacheManager.get_cache('inventory:ws-delete:7') is None


def test_business_brain_workflow_note_persistence(app):
    with app.app_context():
        workflow = Workflow(workspace_id='ws-1', name='Workflow Notes', definition={'steps': []})
        db.session.add(workflow)
        db.session.commit()

        note = WorkflowNote(workflow_id=workflow.id, content='Review before launch', author='admin')
        db.session.add(note)
        db.session.commit()

        stored = WorkflowNote.query.filter_by(workflow_id=workflow.id).first()
        assert stored is not None
        assert stored.content == 'Review before launch'


def test_business_brain_conversation_context_persistence(app):
    with app.app_context():
        conversation = Conversation(workspace_id='ws-1', title='Context chat')
        db.session.add(conversation)
        db.session.commit()

        context = ConversationContext(conversation_id=conversation.id, key='topic', value='orders')
        db.session.add(context)
        db.session.commit()

        stored = ConversationContext.query.filter_by(conversation_id=conversation.id, key='topic').first()
        assert stored is not None
        assert stored.value == 'orders'


def test_business_brain_conversation_context_engine_uses_persisted_table(app):
    with app.app_context():
        conversation = Conversation(workspace_id='ws-1', title='Context engine')
        db.session.add(conversation)
        db.session.commit()

        updated = ConversationEngine.update_context(conversation.id, {'topic': 'orders', 'stage': 'review'})
        assert updated == {'topic': 'orders', 'stage': 'review'}
        assert ConversationContext.query.filter_by(conversation_id=conversation.id).count() == 2
        assert ConversationEngine.get_context(conversation.id) == {'topic': 'orders', 'stage': 'review'}


def test_business_brain_ai_response_api_persists(client):
    response = client.post('/api/ai-responses', json={
        'workspace_id': 'ws-1',
        'conversation_id': 1,
        'prompt': 'Hello',
        'response_text': 'Hi there',
        'status': 'completed',
        'token_count': 4,
        'latency_ms': 120,
    })
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['data']['response_text'] == 'Hi there'
    assert payload['data']['status'] == 'completed'


def test_business_brain_conversation_api_persists(client):
    response = client.post('/api/business/conversation', json={'workspace_id': 'ws-1', 'title': 'Hello'})
    assert response.status_code == 201
    data = response.get_json()
    conversation_id = data['data']['id']

    response = client.post(f'/api/business/conversation/{conversation_id}/message', json={'role': 'user', 'content': 'Hi'})
    assert response.status_code == 201

    response = client.get(f'/api/business/conversation/{conversation_id}/messages')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['data']['messages'][-1]['content'] == 'Hi'
