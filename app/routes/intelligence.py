"""
Intelligence API routes for the platform layer.
"""

from flask import Blueprint, request, jsonify
from app.routes.auth import get_workspace_id, require_jwt_auth
from app.business_platform import (
    BusinessBrain,
    BusinessKnowledgeGraph,
    ContextEngine,
    BusinessMemory,
    AIDecisionFramework,
    RulesEngine,
    WorkflowEngine,
)

intelligence_bp = Blueprint('intelligence', __name__, url_prefix='/api/intelligence')


def _get_workspace_id():
    return get_workspace_id()


@intelligence_bp.route('/brain/outstanding/<int:party_id>', methods=['GET'])
@require_jwt_auth
def get_party_outstanding(party_id):
    summary = BusinessBrain.calculate_outstanding(_get_workspace_id(), party_id)
    return jsonify({'success': True, 'data': summary}), 200


@intelligence_bp.route('/brain/order-summary/<int:order_id>', methods=['GET'])
@require_jwt_auth
def get_order_summary(order_id):
    summary = BusinessBrain.calculate_order_summary(_get_workspace_id(), order_id)
    if 'error' in summary:
        return jsonify({'success': False, 'data': None, 'message': summary['error']}), 404
    return jsonify({'success': True, 'data': summary}), 200


@intelligence_bp.route('/brain/sales-summary', methods=['GET'])
@require_jwt_auth
def get_sales_summary():
    summary = BusinessBrain.get_sales_summary(
        _get_workspace_id(),
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
        retailer_id=request.args.get('retailer_id', type=int),
        product_code=request.args.get('product_code'),
    )
    return jsonify({'success': True, 'data': summary}), 200


@intelligence_bp.route('/brain/sales-by-product', methods=['GET'])
@require_jwt_auth
def get_sales_by_product():
    result = BusinessBrain.get_sales_by_product(
        _get_workspace_id(),
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    )
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/brain/low-stock', methods=['GET'])
@require_jwt_auth
def get_low_stock_items():
    result = BusinessBrain.get_low_stock_items(_get_workspace_id())
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/brain/customer-ltv/<int:retailer_id>', methods=['GET'])
@require_jwt_auth
def get_customer_ltv(retailer_id):
    result = BusinessBrain.get_customer_lifetime_value(_get_workspace_id(), retailer_id)
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/brain/outstanding-summary', methods=['GET'])
@require_jwt_auth
def get_outstanding_summary():
    result = BusinessBrain.get_outstanding_summary(_get_workspace_id())
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/brain/financial-kpis', methods=['GET'])
@require_jwt_auth
def get_financial_kpis():
    period = request.args.get('period', 'MTD')
    result = BusinessBrain.get_financial_kpis(_get_workspace_id(), period)
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/brain/party-credit-status/<int:party_id>', methods=['GET'])
@require_jwt_auth
def get_party_credit_status(party_id):
    result = BusinessBrain.get_party_credit_status(_get_workspace_id(), party_id)
    return jsonify({'success': True, 'data': result}), 200


@intelligence_bp.route('/rules/check', methods=['POST'])
@require_jwt_auth
def check_business_rule():
    payload = request.get_json(silent=True) or {}
    rule_name = payload.get('rule_name')
    context = payload.get('context', {})

    if not rule_name:
        return jsonify({'success': False, 'data': None, 'message': 'rule_name is required'}), 400

    allowed, reason, details = RulesEngine.check_rule(_get_workspace_id(), rule_name, context)
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
    workspace_id = get_workspace_id()
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
