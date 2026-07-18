from app.business_platform import ConversationEngine, WorkflowEngine, EventEngine, RulesEngine, ContextEngine


def test_conversation_engine_roundtrip():
    conv = ConversationEngine.start_conversation('ws-1', 'Test session')
    assert conv['workspace_id'] == 'ws-1'
    assert conv['status'] == 'open'

    msg = ConversationEngine.add_message(conv['id'], 'user', 'hello')
    assert msg['content'] == 'hello'

    history = ConversationEngine.list_messages(conv['id'])
    assert len(history['messages']) == 1


def test_workflow_rules_and_events():
    wf = WorkflowEngine.create_workflow('ws-1', {'name': 'demo', 'steps': [{'id': 's1', 'type': 'manual'}]})
    assert wf['name'] == 'demo'

    result = RulesEngine.evaluate_rule('ws-1', {'all': [{'field': 'amount', 'op': '>=', 'value': 10}]}, {'amount': 20})
    assert result['passed'] is True

    event = EventEngine.publish('inventory_adjusted', 'ws-1', {'item_id': 1})
    assert event['type'] == 'inventory_adjusted'

    ctx = ContextEngine.build_context('ws-1', 'tenant-1', {'topic': 'orders'})
    assert ctx['workspace_id'] == 'ws-1'


def test_rules_engine_does_not_apply_hardcoded_thresholds_when_workspace_rule_missing():
    allowed, reason, details = RulesEngine.check_rule('ws-missing', 'minimum_order_value', {'amount': 150})

    assert allowed is True
    assert 'not configured' in reason.lower()
    assert details['configured'] is False


def test_rules_engine_uses_stored_business_rule_thresholds():
    RulesEngine.create_rule('ws-1', {'name': 'check_credit_limit', 'definition': {'credit_limit': 1500}})
    RulesEngine.create_rule('ws-1', {'name': 'minimum_order_value', 'definition': {'minimum': 200}})

    allowed, reason, details = RulesEngine.check_rule('ws-1', 'check_credit_limit', {'amount': 1600, 'outstanding': 0})
    assert allowed is False
    assert details['limit'] == 1500

    allowed, reason, details = RulesEngine.check_rule('ws-1', 'check_credit_limit', {'amount': 1600, 'outstanding': 0, 'credit_limit': 10000})
    assert allowed is False
    assert details['limit'] == 1500

    allowed, reason, details = RulesEngine.check_rule('ws-1', 'minimum_order_value', {'amount': 150})
    assert allowed is False
    assert details['minimum'] == 200

    allowed, reason, details = RulesEngine.check_rule('ws-1', 'minimum_order_value', {'amount': 150, 'minimum': 1000})
    assert allowed is False
    assert details['minimum'] == 200
