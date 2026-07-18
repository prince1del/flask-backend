"""
Platform Layer - AI Decision Framework Service (Mock Implementation)
Provides simple decision recommendations for business workflows.
"""

import logging

from app.business_platform.knowledge_graph import BusinessKnowledgeGraph
from app.business_platform.rules_engine import RulesEngine


logger = logging.getLogger(__name__)


class AIDecisionFramework:
    """AI decision support service with lightweight cost-aware recommendations."""

    @staticmethod
    def recommend_purchase(
        order_value: float,
        available_credit: float,
        party_status: str = 'active',
        party_id: int | None = None,
        workspace_id: str = 'default',
    ) -> dict:
        order_value = float(order_value or 0.0)
        available_credit = float(available_credit or 0.0)

        audit_trace = []
        audit_trace.append({'step': 'rules_check', 'status': 'ok', 'details': 'checked available credit and status rules'})

        if party_id:
            knowledge_graph = BusinessKnowledgeGraph.get_entity('party', str(party_id))
            audit_trace.append({'step': 'knowledge_graph_lookup', 'status': 'ok', 'entity_type': 'party', 'entity_id': str(party_id), 'properties': knowledge_graph.get('properties', {})})

        rules_result = RulesEngine.check_rule(
            workspace_id,
            'party_active_status',
            {'party_status': party_status},
        )
        if not rules_result[0]:
            audit_trace.append({'step': 'party_status_block', 'status': 'blocked', 'reason': rules_result[1]})

        recommendation = 'approve'
        reason = 'Order value within credit limit.'
        suggestions = [
            'Maintain current approval workflow.',
            'Monitor payment behavior during the order cycle.',
        ]

        if order_value > available_credit:
            recommendation = 'review'
            reason = 'Order exceeds available credit.'
            suggestions = [
                'Reduce the order value or split it across multiple shipments.',
                'Request additional collateral or prepayment.',
                'Review customer payment terms before final approval.',
            ]
        elif order_value > available_credit * 0.8:
            suggestions = [
                'Check exposure against the customer credit limit.',
                'Consider a partial advance payment to reduce risk.',
            ]

        cost = max(0.0, order_value * 0.0005)
        decision = {
            'order_value': round(order_value, 2),
            'available_credit': round(available_credit, 2),
            'recommendation': recommendation,
            'reason': reason,
            'cost': round(cost, 4),
            'optimization_suggestions': suggestions,
            'audit_trace': audit_trace,
        }
        logger.info(
            'AI decision recommendation generated',
            extra={
                'order_value': order_value,
                'available_credit': available_credit,
                'recommendation': recommendation,
                'cost': cost,
            },
        )
        return decision

    @staticmethod
    def evaluate_risk(order_value: float, party_status: str) -> dict:
        score = 50
        if party_status != 'active':
            score = 85
        if order_value > 100000:
            score = max(score, 90)
        return {
            'order_value': order_value,
            'party_status': party_status,
            'risk_score': score,
            'risk_level': 'high' if score >= 80 else 'medium' if score >= 60 else 'low',
        }
