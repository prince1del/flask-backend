"""
Platform Layer - Rules Engine Service (Mock Implementation)
Provides configurable business rules enforcement.

Phase 4 Status: Mock implementation with hardcoded rules
Production will: Read rules from database, allow admin to configure
"""

import json
import uuid

from flask import current_app

from app.business_platform.cache_manager import CacheManager
from app.db import db
from app.models import BusinessRule


class RulesEngine:
    """
    Business rules enforcement (configurable without code changes).
    Admin can enable/disable rules via admin panel.
    """

    @staticmethod
    def _normalize_definition(value) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                return parsed
        return {}
    
    @staticmethod
    def _get_rule_definition(workspace_id: str, rule_name: str) -> dict:
        RulesEngine._ensure_app_context()
        cache_key = f"rule_definition:{workspace_id}:{rule_name}"
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached

        rule = BusinessRule.query.filter_by(workspace_id=workspace_id, name=rule_name, enabled=True).order_by(BusinessRule.priority.desc(), BusinessRule.id.desc()).first()
        definition = RulesEngine._normalize_definition(rule.definition if rule else {})
        if not definition and rule is not None:
            definition = RulesEngine._normalize_definition(rule.rule_value)
        CacheManager.set_cache(cache_key, definition, ttl_minutes=30)
        return definition

    @staticmethod
    def check_rule(workspace_id: str, rule_name: str, context: dict) -> tuple:
        """
        Check if a business rule allows an action.
        
        Args:
            workspace_id: Tenant identifier
            rule_name: Name of the rule to check
            context: dict with context data for rule evaluation
            
        Returns: (allowed: bool, reason: str, details: dict)
        """
        definition = RulesEngine._get_rule_definition(workspace_id, rule_name)
        
        # RULE: Check Credit Limit
        if rule_name == 'check_credit_limit':
            party_id = context.get('party_id')
            amount = context.get('amount', 0)
            credit_limit = definition.get('credit_limit')
            if credit_limit is None:
                credit_limit = context.get('credit_limit')
            if credit_limit is None:
                return (True, 'Credit limit rule not configured', {
                    'rule': 'check_credit_limit',
                    'configured': False,
                    'requested': amount,
                    'outstanding': context.get('outstanding', 0),
                })
            outstanding = context.get('outstanding', 0)
            
            total = outstanding + amount
            if total > credit_limit:
                return (False, f'Credit limit exceeded. Available: {credit_limit - outstanding}', {
                    'rule': 'check_credit_limit',
                    'requested': amount,
                    'outstanding': outstanding,
                    'limit': credit_limit,
                    'total': total
                })
            return (True, 'Credit check passed', {})
        
        # RULE: Minimum Order Value
        elif rule_name == 'minimum_order_value':
            amount = context.get('amount', 0)
            minimum = definition.get('minimum')
            if minimum is None:
                minimum = context.get('minimum')
            if minimum is None:
                return (True, 'Minimum order value rule not configured', {
                    'rule': 'minimum_order_value',
                    'configured': False,
                    'requested': amount,
                })
            
            if amount < minimum:
                return (False, f'Minimum order value is {minimum}', {
                    'rule': 'minimum_order_value',
                    'requested': amount,
                    'minimum': minimum
                })
            return (True, 'Order value check passed', {})
        
        # RULE: Party Active Status
        elif rule_name == 'party_active_status':
            party_status = context.get('party_status', 'active')
            
            if party_status != 'active':
                return (False, f'Party status is {party_status}. Only active parties can order.', {
                    'rule': 'party_active_status',
                    'status': party_status
                })
            return (True, 'Party status check passed', {})
        
        # RULE: Discount Approval Required
        elif rule_name == 'discount_approval_required':
            discount_percent = context.get('discount_percent', 0)
            user_role = context.get('user_role', 'user')
            threshold = definition.get('threshold')
            if threshold is None:
                threshold = context.get('threshold')
            if threshold is None:
                return (True, 'Discount approval rule not configured', {
                    'rule': 'discount_approval_required',
                    'configured': False,
                    'discount': discount_percent,
                    'user_role': user_role,
                })
            
            if discount_percent > threshold and user_role != 'admin':
                return (False, f'Discount > {threshold}% requires admin approval', {
                    'rule': 'discount_approval_required',
                    'discount': discount_percent,
                    'threshold': threshold,
                    'user_role': user_role
                })
            return (True, 'Discount check passed', {})
        
        # RULE: Inventory Available
        elif rule_name == 'inventory_available':
            item_id = context.get('item_id')
            requested_qty = context.get('requested_qty', 0)
            available_qty = context.get('available_qty', 0)
            
            if requested_qty > available_qty:
                return (False, f'Insufficient inventory. Available: {available_qty}', {
                    'rule': 'inventory_available',
                    'requested': requested_qty,
                    'available': available_qty,
                    'short': requested_qty - available_qty
                })
            return (True, 'Inventory check passed', {})
        
        # Generic configurable rule evaluation for tests / future Studio usage.
        elif rule_name == 'generic_rule':
            return (True, 'Generic rule passed', {})

        # Unknown rule - allow by default
        else:
            return (True, f'Rule {rule_name} not configured', {})

    @staticmethod
    def _ensure_app_context() -> None:
        try:
            current_app._get_current_object()
        except RuntimeError:
            from app.web_app import create_app

            create_app().app_context().push()

    @staticmethod
    def create_rule(workspace_id: str, payload: dict) -> dict:
        RulesEngine._ensure_app_context()
        rule_key = payload.get('rule_key') or payload.get('name', 'Untitled rule')
        definition = payload.get('definition', {}) or {}
        name = payload.get('name', 'Untitled rule')

        rule = BusinessRule.query.filter_by(workspace_id=workspace_id, name=name).first()
        if rule is None:
            rule = BusinessRule(
                workspace_id=workspace_id,
                rule_key=rule_key,
                rule_value=json.dumps(definition) if isinstance(definition, dict) else str(definition),
                name=name,
                definition=definition,
                priority=payload.get('priority', 0),
                enabled=payload.get('enabled', True),
                is_locked=payload.get('is_locked', True),
            )
            db.session.add(rule)
        else:
            rule.rule_key = rule_key
            rule.rule_value = json.dumps(definition) if isinstance(definition, dict) else str(definition)
            rule.definition = definition
            rule.priority = payload.get('priority', rule.priority)
            rule.enabled = payload.get('enabled', rule.enabled)
            rule.is_locked = payload.get('is_locked', rule.is_locked)

        db.session.commit()
        CacheManager.invalidate_pattern(f"rule_definition:{workspace_id}:*")
        return rule.to_dict()

    @staticmethod
    def list_rules(workspace_id: str) -> list:
        RulesEngine._ensure_app_context()
        return [rule.to_dict() for rule in BusinessRule.query.filter_by(workspace_id=workspace_id).all()]

    @staticmethod
    def get_rule(workspace_id: str, rule_id: str) -> dict | None:
        RulesEngine._ensure_app_context()
        rule = BusinessRule.query.filter_by(workspace_id=workspace_id, id=int(rule_id)).first() if str(rule_id).isdigit() else None
        return rule.to_dict() if rule else None

    @staticmethod
    def update_rule(workspace_id: str, rule_id: str, payload: dict) -> dict | None:
        RulesEngine._ensure_app_context()
        rule = BusinessRule.query.filter_by(workspace_id=workspace_id, id=int(rule_id)).first() if str(rule_id).isdigit() else None
        if rule is None:
            return None
        for key, value in payload.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        db.session.commit()
        CacheManager.invalidate_pattern(f"rule_definition:{workspace_id}:*")
        return rule.to_dict()

    @staticmethod
    def delete_rule(workspace_id: str, rule_id: str) -> bool:
        RulesEngine._ensure_app_context()
        rule = BusinessRule.query.filter_by(workspace_id=workspace_id, id=int(rule_id)).first() if str(rule_id).isdigit() else None
        if rule is None:
            return False
        db.session.delete(rule)
        db.session.commit()
        CacheManager.invalidate_pattern(f"rule_definition:{workspace_id}:*")
        return True

    @staticmethod
    def evaluate_rule(workspace_id: str, rule_definition: dict, context: dict) -> dict:
        """Evaluate a simple rule structure against the supplied context."""
        conditions = rule_definition.get('all', [])
        passed = True
        details = []
        for condition in conditions:
            field = condition.get('field')
            op = condition.get('op', '==')
            expected = condition.get('value')
            actual = context.get(field)
            if op == '==':
                result = actual == expected
            elif op == '>=':
                result = actual >= expected
            elif op == '<=':
                result = actual <= expected
            elif op == '>':
                result = actual > expected
            elif op == '<':
                result = actual < expected
            else:
                result = actual == expected
            details.append({'field': field, 'op': op, 'expected': expected, 'actual': actual, 'passed': result})
            passed = passed and result
        return {'workspace_id': workspace_id, 'passed': passed, 'details': details}

    @staticmethod
    def test_rule(rule_id: str, context: dict) -> dict:
        RulesEngine._ensure_app_context()
        rule = BusinessRule.query.get(int(rule_id)) if str(rule_id).isdigit() else None
        if rule is None:
            return {'passed': False, 'error': 'Rule not found'}
        definition = rule.definition or {}
        return RulesEngine.evaluate_rule(rule.workspace_id, definition, context)    
    @staticmethod
    def validate_order(workspace_id: str, order_data: dict) -> tuple:
        """
        Validate order against all applicable rules.
        
        Args:
            workspace_id: Tenant identifier
            order_data: Order being validated
            
        Returns: (valid: bool, errors: list[str])
        """
        errors = []
        
        # Rule 1: Credit Limit
        allowed, reason, _ = RulesEngine.check_rule(workspace_id, 'check_credit_limit', {
            'party_id': order_data.get('party_id'),
            'amount': order_data.get('total', 0),
            'outstanding': order_data.get('outstanding', 0)
        })
        if not allowed:
            errors.append(reason)
        
        # Rule 2: Minimum Order Value
        allowed, reason, _ = RulesEngine.check_rule(workspace_id, 'minimum_order_value', {
            'amount': order_data.get('total', 0)
        })
        if not allowed:
            errors.append(reason)
        
        # Rule 3: Party Status
        allowed, reason, _ = RulesEngine.check_rule(workspace_id, 'party_active_status', {
            'party_status': order_data.get('party_status', 'active')
        })
        if not allowed:
            errors.append(reason)
        
        return (len(errors) == 0, errors)
