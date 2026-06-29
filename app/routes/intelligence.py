"""
Intelligence API routes for the platform layer.
"""

from flask import Blueprint, request, jsonify
from app.routes.auth import require_jwt_auth
from app.platform import (
    BusinessBrain,
    BusinessKnowledgeGraph,
    ContextEngine,
    BusinessMemory,
    AIDecisionFramework,
    RulesEngine,
    WorkflowEngine,
)

intelligence_bp = Blueprint('intelligence', __name__, url_prefix='/api/intelligence')


@intelligence_bp.route('/brain/outstanding/<int:party_id>', methods=['GET'])
@require_jwt_auth
def get_party_outstanding(party_id):
    summary = BusinessBrain.calculate_outstanding('default', party_id)
    return jsonify({'success': True, 'data': summary}), 200


@intelligence_bp.route('/brain/order-summary/<int:order_id>', methods=['GET'])
@require_jwt_auth
def get_order_summary(order_id):
    summary = BusinessBrain.calculate_order_summary('default', order_id)
    if 'error' in summary:
        return jsonify({'success': False, 'data': None, 'message': summary['error']}), 404
    return jsonify({'success': True, 'data': summary}), 200


@intelligence_bp.route('/rules/check', methods=['POST'])
@require_jwt_auth
def check_business_rule():
    payload = request.get_json(silent=True) or {}
    rule_name = payload.get('rule_name')
    context = payload.get('context', {})

    if not rule_name:
        return jsonify({'success': False, 'data': None, 'message': 'rule_name is required'}), 400

    allowed, reason, details = RulesEngine.check_rule('default', rule_name, context)
    return jsonify({'success': True, 'data': {'allowed': allowed, 'reason': reason, 'details': details}}), 200


@intelligence_bp.route('/workflow/<string:workflow_type>', methods=['GET'])
@require_jwt_auth
def get_workflow_definition(workflow_type):
    workflow = WorkflowEngine.get_workflow(workflow_type)
    if workflow is None:
        return jsonify({'success': False, 'data': None, 'message': 'Workflow not found'}), 404
    return jsonify({'success': True, 'data': workflow}), 200


@intelligence_bp.route('/context/summary', methods=['POST'])
@require_jwt_auth
def summarize_context():
    payload = request.get_json(silent=True) or {}
    workspace_id = payload.get('workspace_id', 'default')
    tenant_id = payload.get('tenant_id', 'default')
    context = ContextEngine.build_context(workspace_id, tenant_id, payload.get('payload', {}))
    summary = ContextEngine.summarize_context(context)
    return jsonify({'success': True, 'data': {'context': context, 'summary': summary}}), 200


@intelligence_bp.route('/decision/recommend', methods=['POST'])
@require_jwt_auth
def recommend_decision():
    payload = request.get_json(silent=True) or {}
    order_value = float(payload.get('order_value', 0.0))
    available_credit = float(payload.get('available_credit', 0.0))
    party_status = payload.get('party_status', 'active')

    decision = AIDecisionFramework.recommend_purchase(order_value, available_credit)
    risk = AIDecisionFramework.evaluate_risk(order_value, party_status)
    return jsonify({'success': True, 'data': {'decision': decision, 'risk': risk}}), 200


@intelligence_bp.route('/knowledge-graph/<string:entity_type>/<string:entity_id>', methods=['GET'])
@require_jwt_auth
def lookup_knowledge_graph_entity(entity_type, entity_id):
    entity = BusinessKnowledgeGraph.get_entity(entity_type, entity_id)
    return jsonify({'success': True, 'data': entity}), 200


@intelligence_bp.route('/memory/store', methods=['POST'])
@require_jwt_auth
def store_business_memory():
    event = request.get_json(silent=True) or {}
    result = BusinessMemory.store_event(event)
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/memory/recent', methods=['GET'])
@require_jwt_auth
def recall_business_memory():
    limit = request.args.get('limit', 10, type=int)
    result = BusinessMemory.recall_recent(limit)
    return jsonify({'success': True, 'data': result}), 200
