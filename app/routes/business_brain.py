from flask import Blueprint, jsonify, request

from app.business_platform import (
    ConversationEngine,
    EventEngine,
    RulesEngine,
    WorkflowEngine,
)
from app.db import db
from app.models import AIResponse
from app.routes.auth import get_workspace_id, require_jwt_auth

business_bp = Blueprint('business_brain', __name__)


@business_bp.before_request
def _require_business_auth():
    return require_jwt_auth(lambda: None)()


def _workspace_id():
    return get_workspace_id()


# Conversation endpoints
@business_bp.route('/api/business/conversation', methods=['POST'])
def create_conversation():
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    title = payload.get('title')
    conversation = ConversationEngine.start_conversation(workspace_id, title)
    return jsonify({'success': True, 'data': conversation}), 201


@business_bp.route('/api/business/conversation/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    conversation = ConversationEngine.get_conversation(conversation_id)
    if conversation is None:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404
    return jsonify({'success': True, 'data': conversation}), 200


@business_bp.route('/api/business/conversation/<conversation_id>/messages', methods=['GET'])
def list_conversation_messages(conversation_id):
    try:
        return jsonify({'success': True, 'data': ConversationEngine.list_messages(conversation_id)}), 200
    except KeyError:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404


@business_bp.route('/api/business/conversation/<conversation_id>/message', methods=['POST'])
def add_conversation_message(conversation_id):
    payload = request.get_json(silent=True) or {}
    try:
        message = ConversationEngine.add_message(conversation_id, payload.get('role', 'user'), payload.get('content', ''))
        return jsonify({'success': True, 'data': message}), 201
    except KeyError:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404


@business_bp.route('/api/business/conversation/<conversation_id>/close', methods=['PUT'])
def close_conversation(conversation_id):
    try:
        return jsonify({'success': True, 'data': ConversationEngine.close_conversation(conversation_id)}), 200
    except KeyError:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404


@business_bp.route('/api/business/conversation/search', methods=['GET'])
def search_conversations():
    query = request.args.get('query', '')
    workspace_id = _workspace_id()
    return jsonify({'success': True, 'data': ConversationEngine.search_conversations(workspace_id, query)}), 200


@business_bp.route('/api/business/conversation/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    deleted = ConversationEngine.delete_conversation(conversation_id)
    return jsonify({'success': deleted, 'data': None, 'message': 'Conversation deleted' if deleted else 'Conversation not found'}), 200 if deleted else 404


@business_bp.route('/api/business/conversation/<conversation_id>/context', methods=['GET'])
def get_conversation_context(conversation_id):
    try:
        return jsonify({'success': True, 'data': ConversationEngine.get_context(conversation_id)}), 200
    except KeyError:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404


@business_bp.route('/api/business/conversation/<conversation_id>/context', methods=['PUT'])
def update_conversation_context(conversation_id):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({'success': True, 'data': ConversationEngine.update_context(conversation_id, payload)}), 200
    except KeyError:
        return jsonify({'success': False, 'data': None, 'message': 'Conversation not found'}), 404


# Workflow endpoints
@business_bp.route('/api/workflows', methods=['POST'])
def create_workflow():
    payload = request.get_json(silent=True) or {}
    workflow = WorkflowEngine.create_workflow(_workspace_id(), payload)
    return jsonify({'success': True, 'data': workflow}), 201


@business_bp.route('/api/workflows/<workflow_id>', methods=['GET'])
def get_workflow(workflow_id):
    workflow = WorkflowEngine.get_workflow(workflow_id)
    if workflow is None:
        return jsonify({'success': False, 'data': None, 'message': 'Workflow not found'}), 404
    return jsonify({'success': True, 'data': workflow}), 200


@business_bp.route('/api/workflows/<workflow_id>', methods=['PUT'])
def update_workflow(workflow_id):
    payload = request.get_json(silent=True) or {}
    workflow = WorkflowEngine.update_workflow(workflow_id, payload)
    if workflow is None:
        return jsonify({'success': False, 'data': None, 'message': 'Workflow not found'}), 404
    return jsonify({'success': True, 'data': workflow}), 200


@business_bp.route('/api/workflows/<workflow_id>', methods=['DELETE'])
def delete_workflow(workflow_id):
    removed = WorkflowEngine.delete_workflow(workflow_id)
    return jsonify({'success': removed, 'data': None, 'message': 'Workflow deleted' if removed else 'Workflow not found'}), 200 if removed else 404


@business_bp.route('/api/workflows/<workflow_id>/start', methods=['POST'])
def start_workflow(workflow_id):
    payload = request.get_json(silent=True) or {}
    execution = WorkflowEngine.start_workflow(workflow_id, payload)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Workflow not found'}), 404
    return jsonify({'success': True, 'data': execution}), 201


@business_bp.route('/api/workflows/<workflow_id>/executions', methods=['GET'])
def list_workflow_executions(workflow_id):
    return jsonify({'success': True, 'data': WorkflowEngine.get_executions(workflow_id)}), 200


@business_bp.route('/api/workflows/<workflow_id>/executions/<execution_id>', methods=['GET'])
def get_workflow_execution(workflow_id, execution_id):
    execution = WorkflowEngine.get_execution(workflow_id, execution_id)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Execution not found'}), 404
    return jsonify({'success': True, 'data': execution}), 200


@business_bp.route('/api/workflows/<workflow_id>/executions/<execution_id>', methods=['PUT'])
def update_workflow_execution(workflow_id, execution_id):
    payload = request.get_json(silent=True) or {}
    execution = WorkflowEngine.update_execution(workflow_id, execution_id, payload)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Execution not found'}), 404
    return jsonify({'success': True, 'data': execution}), 200


@business_bp.route('/api/workflows/<workflow_id>/executions/<execution_id>/pause', methods=['POST'])
def pause_workflow_execution(workflow_id, execution_id):
    execution = WorkflowEngine.pause_execution(workflow_id, execution_id)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Execution not found'}), 404
    return jsonify({'success': True, 'data': execution}), 200


@business_bp.route('/api/workflows/<workflow_id>/executions/<execution_id>/resume', methods=['POST'])
def resume_workflow_execution(workflow_id, execution_id):
    execution = WorkflowEngine.resume_execution(workflow_id, execution_id)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Execution not found'}), 404
    return jsonify({'success': True, 'data': execution}), 200


@business_bp.route('/api/workflows/<workflow_id>/executions/<execution_id>/cancel', methods=['POST'])
def cancel_workflow_execution(workflow_id, execution_id):
    execution = WorkflowEngine.cancel_execution(workflow_id, execution_id)
    if execution is None:
        return jsonify({'success': False, 'data': None, 'message': 'Execution not found'}), 404
    return jsonify({'success': True, 'data': execution}), 200


@business_bp.route('/api/workflows/<workflow_id>/steps', methods=['POST'])
def add_workflow_step(workflow_id):
    payload = request.get_json(silent=True) or {}
    step = WorkflowEngine.add_step(workflow_id, payload)
    if step is None:
        return jsonify({'success': False, 'data': None, 'message': 'Workflow not found'}), 404
    return jsonify({'success': True, 'data': step}), 201


@business_bp.route('/api/workflows/<workflow_id>/steps/<step_id>', methods=['PUT'])
def update_workflow_step(workflow_id, step_id):
    payload = request.get_json(silent=True) or {}
    step = WorkflowEngine.update_step(workflow_id, step_id, payload)
    if step is None:
        return jsonify({'success': False, 'data': None, 'message': 'Step not found'}), 404
    return jsonify({'success': True, 'data': step}), 200


@business_bp.route('/api/workflows/<workflow_id>/steps/<step_id>', methods=['DELETE'])
def delete_workflow_step(workflow_id, step_id):
    removed = WorkflowEngine.delete_step(workflow_id, step_id)
    return jsonify({'success': removed, 'data': None, 'message': 'Step deleted' if removed else 'Step not found'}), 200 if removed else 404


@business_bp.route('/api/workflow-steps/execute', methods=['POST'])
def execute_workflow_step():
    payload = request.get_json(silent=True) or {}
    result = WorkflowEngine.execute_step(payload)
    return jsonify({'success': True, 'data': result}), 200


# Rule endpoints
@business_bp.route('/api/workspace/<workspace_id>/rules', methods=['POST'])
def create_rule(workspace_id):
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    rule = RulesEngine.create_rule(workspace_id, payload)
    return jsonify({'success': True, 'data': rule}), 201


@business_bp.route('/api/workspace/<workspace_id>/rules', methods=['GET'])
def list_rules(workspace_id):
    workspace_id = _workspace_id()
    return jsonify({'success': True, 'data': RulesEngine.list_rules(workspace_id)}), 200


@business_bp.route('/api/workspace/<workspace_id>/rules/<rule_id>', methods=['GET'])
def get_rule(workspace_id, rule_id):
    workspace_id = _workspace_id()
    rule = RulesEngine.get_rule(workspace_id, rule_id)
    if rule is None:
        return jsonify({'success': False, 'data': None, 'message': 'Rule not found'}), 404
    return jsonify({'success': True, 'data': rule}), 200


@business_bp.route('/api/workspace/<workspace_id>/rules/<rule_id>', methods=['PUT'])
def update_rule(workspace_id, rule_id):
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    rule = RulesEngine.update_rule(workspace_id, rule_id, payload)
    if rule is None:
        return jsonify({'success': False, 'data': None, 'message': 'Rule not found'}), 404
    return jsonify({'success': True, 'data': rule}), 200


@business_bp.route('/api/workspace/<workspace_id>/rules/<rule_id>', methods=['DELETE'])
def delete_rule(workspace_id, rule_id):
    workspace_id = _workspace_id()
    removed = RulesEngine.delete_rule(workspace_id, rule_id)
    return jsonify({'success': removed, 'data': None, 'message': 'Rule deleted' if removed else 'Rule not found'}), 200 if removed else 404


@business_bp.route('/api/rules/evaluate', methods=['POST'])
def evaluate_rule():
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    result = RulesEngine.evaluate_rule(workspace_id, payload.get('rule', {}), payload.get('context', {}))
    return jsonify({'success': True, 'data': result}), 200


@business_bp.route('/api/rules/<rule_id>/test', methods=['POST'])
def test_rule(rule_id):
    payload = request.get_json(silent=True) or {}
    result = RulesEngine.test_rule(rule_id, payload.get('context', {}))
    return jsonify({'success': True, 'data': result}), 200


# Events endpoints
@business_bp.route('/api/events/<event_type>', methods=['POST'])
def publish_event(event_type):
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    event = EventEngine.publish(event_type, workspace_id, payload.get('data', payload))
    return jsonify({'success': True, 'data': event}), 201


@business_bp.route('/api/events', methods=['GET'])
def list_events():
    workspace_id = _workspace_id()
    return jsonify({'success': True, 'data': EventEngine.list_events(workspace_id)}), 200


@business_bp.route('/api/events/replay', methods=['GET'])
def replay_events():
    workspace_id = _workspace_id()
    event_type = request.args.get('event_type')
    return jsonify({'success': True, 'data': EventEngine.replay_events(workspace_id, event_type)}), 200


@business_bp.route('/api/events/subscriptions', methods=['GET'])
def list_event_subscriptions():
    workspace_id = _workspace_id()
    return jsonify({'success': True, 'data': EventEngine.list_subscriptions(workspace_id)}), 200


@business_bp.route('/api/events/subscriptions', methods=['POST'])
def create_event_subscription():
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    subscription = EventEngine.create_subscription(
        payload.get('event_type'),
        workspace_id,
        payload.get('config') or payload,
    )
    return jsonify({'success': True, 'data': subscription}), 201


@business_bp.route('/api/events/subscriptions/<sub_id>', methods=['DELETE'])
def delete_event_subscription(sub_id):
    removed = EventEngine.delete_subscription(sub_id)
    return jsonify({'success': removed, 'data': None, 'message': 'Subscription removed' if removed else 'Subscription not found'}), 200 if removed else 404


@business_bp.route('/api/events/log', methods=['GET'])
def get_event_log():
    workspace_id = _workspace_id()
    return jsonify({'success': True, 'data': EventEngine.list_events(workspace_id)}), 200


# AI response endpoints
@business_bp.route('/api/ai-responses', methods=['POST'])
def create_ai_response():
    payload = request.get_json(silent=True) or {}
    workspace_id = _workspace_id()
    response = AIResponse(
        workspace_id=workspace_id,
        conversation_id=payload.get('conversation_id'),
        prompt=payload.get('prompt'),
        response_text=payload.get('response_text'),
        status=payload.get('status', 'completed'),
        model_name=payload.get('model_name'),
        token_count=payload.get('token_count', 0),
        latency_ms=payload.get('latency_ms', 0),
        feedback=payload.get('feedback') or {},
        extra_metadata=payload.get('metadata') or payload.get('extra_metadata') or {},
    )
    db.session.add(response)
    db.session.commit()
    return jsonify({'success': True, 'data': response.to_dict()}), 201


@business_bp.route('/api/ai-responses', methods=['GET'])
def list_ai_responses():
    workspace_id = _workspace_id()
    responses = AIResponse.query.filter_by(workspace_id=workspace_id).order_by(AIResponse.created_at.desc()).all()
    return jsonify({'success': True, 'data': [response.to_dict() for response in responses]}), 200


@business_bp.route('/api/ai-responses/<response_id>', methods=['GET'])
def get_ai_response(response_id):
    response = db.session.get(AIResponse, int(response_id)) if str(response_id).isdigit() else None
    if response is None:
        return jsonify({'success': False, 'data': None, 'message': 'AI response not found'}), 404
    return jsonify({'success': True, 'data': response.to_dict()}), 200


@business_bp.route('/api/ai-responses/<response_id>/feedback', methods=['POST'])
def submit_ai_feedback(response_id):
    payload = request.get_json(silent=True) or {}
    response = db.session.get(AIResponse, int(response_id)) if str(response_id).isdigit() else None
    if response is None:
        return jsonify({'success': False, 'data': None, 'message': 'AI response not found'}), 404
    response.feedback = payload
    db.session.commit()
    return jsonify({'success': True, 'data': response.to_dict()}), 200


@business_bp.route('/api/ai-responses/analytics', methods=['GET'])
def get_ai_analytics():
    workspace_id = _workspace_id()
    query = AIResponse.query.filter_by(workspace_id=workspace_id)
    total = query.count()
    average_latency = db.session.query(db.func.avg(AIResponse.latency_ms)).filter(AIResponse.workspace_id == workspace_id).scalar() or 0
    return jsonify({'success': True, 'data': {'total': total, 'average_latency_ms': float(average_latency)}}), 200
