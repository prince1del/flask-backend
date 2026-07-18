"""
Platform Layer - Business Brain Service (Mock Implementation)
Provides business logic calculations for multi-module use.

To be replaced with real implementation from platform core.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, case, desc

from app.db import db
from app.models import (
    Distributor,
    Retailer,
    Invoice,
    InvoicePayment,
    SalesOrder,
    SalesOrderItem,
    Inventory,
)
from app.business_platform.cache_manager import CacheManager


class BusinessBrain:
    """
    Core business calculations used across multiple modules.
    
    Phase 4 Status: Mock implementation (uses live database)
    Production will: Optimize queries, add caching, scale to multiple tenants
    """

    @staticmethod
    def _parse_date(value):
        if isinstance(value, date):
            return value
        if not value:
            return None
        return date.fromisoformat(value)

    @staticmethod
    def _validate_workspace(workspace_id: str):
        if not workspace_id:
            raise ValueError('workspace_id is required')
        return workspace_id

    @staticmethod
    def _workspace_filter(query, model, workspace_id: str):
        return query.filter(getattr(model, 'workspace_id') == workspace_id)

    @staticmethod
    def _cache_key(prefix: str, workspace_id: str, **kwargs) -> str:
        args = ":".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"{prefix}:{workspace_id}:{args}"

    @staticmethod
    def get_sales_summary(workspace_id: str, start_date=None, end_date=None, retailer_id=None, product_code=None) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        start_date = BusinessBrain._parse_date(start_date) or date.today() - timedelta(days=30)
        end_date = BusinessBrain._parse_date(end_date) or date.today()

        cache_key = BusinessBrain._cache_key(
            'sales_summary',
            workspace_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            retailer_id=retailer_id or 'any',
            product_code=product_code or 'any',
        )
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached

        order_query = db.session.query(
            func.coalesce(func.sum(SalesOrder.total_amount), 0).label('total_sales'),
            func.coalesce(func.sum(SalesOrder.net_amount), 0).label('net_sales'),
            func.coalesce(func.sum(SalesOrder.tax_amount), 0).label('tax_amount'),
            func.count(SalesOrder.id).label('order_count'),
        ).filter(
            SalesOrder.workspace_id == workspace_id,
            SalesOrder.order_date >= start_date,
            SalesOrder.order_date <= end_date,
        )

        item_query = db.session.query(func.coalesce(func.sum(SalesOrderItem.quantity), 0).label('total_quantity'))
        item_query = item_query.join(SalesOrder).filter(
            SalesOrder.workspace_id == workspace_id,
            SalesOrder.order_date >= start_date,
            SalesOrder.order_date <= end_date,
        )

        if product_code:
            order_query = order_query.join(SalesOrderItem)
            item_query = item_query.filter(SalesOrderItem.product_code == product_code)

        if retailer_id:
            order_query = order_query.filter(SalesOrder.retailer_id == retailer_id)
            item_query = item_query.filter(SalesOrder.retailer_id == retailer_id)

        summary = order_query.first()
        total_quantity = item_query.scalar() or 0
        order_count = int(summary.order_count or 0)

        result = {
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'total_sales': float(summary.total_sales or 0.0),
            'net_sales': float(summary.net_sales or 0.0),
            'tax_amount': float(summary.tax_amount or 0.0),
            'order_count': order_count,
            'total_quantity': int(total_quantity),
            'average_order_value': float(summary.total_sales / order_count) if order_count else 0.0,
        }
        CacheManager.set_cache(cache_key, result, ttl_minutes=5)
        return result

    @staticmethod
    def get_sales_by_product(workspace_id: str, start_date=None, end_date=None) -> list:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        start_date = BusinessBrain._parse_date(start_date) or date.today() - timedelta(days=30)
        end_date = BusinessBrain._parse_date(end_date) or date.today()

        cache_key = BusinessBrain._cache_key(
            'sales_by_product',
            workspace_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached

        results = (
            db.session.query(
                SalesOrderItem.product_code.label('product_code'),
                SalesOrderItem.product_name.label('product_name'),
                func.coalesce(func.sum(SalesOrderItem.quantity), 0).label('quantity'),
                func.coalesce(func.sum(SalesOrderItem.line_total), 0).label('revenue'),
            )
            .join(SalesOrder)
            .filter(
                SalesOrder.workspace_id == workspace_id,
                SalesOrder.order_date >= start_date,
                SalesOrder.order_date <= end_date,
            )
            .group_by(SalesOrderItem.product_code, SalesOrderItem.product_name)
            .order_by(desc('revenue'))
            .all()
        )

        result = [
            {
                'product_code': row.product_code,
                'product_name': row.product_name,
                'quantity': int(row.quantity),
                'revenue': float(row.revenue),
            }
            for row in results
        ]
        CacheManager.set_cache(cache_key, result, ttl_minutes=5)
        return result

    @staticmethod
    def calculate_outstanding(workspace_id: str, party_id: int) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        cache_key = f"outstanding:{workspace_id}:{party_id}"
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached
        try:
            outstanding = (
                db.session.query(func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0))
                .filter(
                    Invoice.workspace_id == workspace_id,
                    Invoice.payment_status != 'paid',
                )
                .scalar()
            )
            overdue = (
                db.session.query(func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0))
                .filter(
                    Invoice.workspace_id == workspace_id,
                    Invoice.payment_status != 'paid',
                    Invoice.due_date < date.today(),
                )
                .scalar()
            )
            current = float(outstanding or 0) - float(overdue or 0)

            credit_limit = 100000.0
            party = (
                db.session.query(Retailer)
                .filter(Retailer.id == party_id, Retailer.workspace_id == workspace_id)
                .one_or_none()
            )
            if party is None:
                party = (
                    db.session.query(Distributor)
                    .filter(Distributor.id == party_id, Distributor.workspace_id == workspace_id)
                    .one_or_none()
                )
            if party is not None and hasattr(party, 'credit_limit'):
                credit_limit = float(getattr(party, 'credit_limit', credit_limit) or credit_limit)

            available_credit = max(credit_limit - float(outstanding or 0), 0.0)

            result = {
                'party_id': party_id,
                'outstanding': round(float(outstanding or 0.0), 2),
                'overdue': round(float(overdue or 0.0), 2),
                'current': round(float(current), 2),
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'payment_terms': 30,
                'credit_limit': round(credit_limit, 2),
                'available_credit': round(available_credit, 2),
            }
            CacheManager.set_cache(cache_key, result, ttl_minutes=5)
            return result
        except Exception as e:
            return {
                'party_id': party_id,
                'outstanding': 0.0,
                'overdue': 0.0,
                'current': 0.0,
                'error': str(e),
            }

    @staticmethod
    def calculate_order_summary(workspace_id: str, order_id: int) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        try:
            order = (
                db.session.query(SalesOrder)
                .filter(SalesOrder.id == order_id, SalesOrder.workspace_id == workspace_id)
                .one_or_none()
            )
            if not order:
                return {'error': 'Order not found', 'order_id': order_id}

            subtotal = float(order.total_amount or 0.0)
            tax = float(order.tax_amount or 0.0)
            discount = round(subtotal - float(order.net_amount or 0.0), 2)
            total = subtotal
            items_count = sum(item.quantity for item in order.items or [])

            return {
                'order_id': order.id,
                'subtotal': subtotal,
                'tax': tax,
                'tax_rate': float(order.tax_amount / subtotal) if subtotal else 0.0,
                'total': total,
                'items_count': items_count,
                'status': order.status,
                'discount': discount,
                'final_total': float(order.net_amount or 0.0),
            }
        except Exception as e:
            return {'error': str(e), 'order_id': order_id}

    @staticmethod
    def calculate_inventory_valuation(workspace_id: str) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        cache_key = BusinessBrain._cache_key('inventory_valuation', workspace_id)
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached

        items = (
            Inventory.query.filter(
                Inventory.status == 'active',
                Inventory.workspace_id == workspace_id,
            ).all()
        )
        total_valuation = sum((item.quantity_on_hand or 0.0) * (item.unit_cost or 0.0) for item in items)

        by_category = {}
        by_warehouse = {}
        for item in items:
            category = item.category or 'uncategorized'
            warehouse = item.warehouse_id or 'default'
            value = (item.quantity_on_hand or 0.0) * (item.unit_cost or 0.0)
            by_category[category] = by_category.get(category, 0.0) + value
            by_warehouse[warehouse] = by_warehouse.get(warehouse, 0.0) + value

        result = {
            'total_valuation': round(total_valuation, 2),
            'by_category': {k: round(v, 2) for k, v in by_category.items()},
            'by_warehouse': {k: round(v, 2) for k, v in by_warehouse.items()},
            'items_count': len(items),
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }
        CacheManager.set_cache(cache_key, result, ttl_minutes=10)
        return result

    @staticmethod
    def get_low_stock_items(workspace_id: str) -> list:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        cache_key = f"low_stock_items:{workspace_id}"
        cached = CacheManager.get_cache(cache_key)
        if cached is not None:
            return cached

        items = Inventory.query.filter(
            Inventory.workspace_id == workspace_id,
            Inventory.status == 'active',
            Inventory.quantity_on_hand < Inventory.reorder_level,
        ).all()

        result = [
            {
                'item_id': item.id,
                'item_code': item.item_code,
                'item_name': item.item_name,
                'warehouse_id': item.warehouse_id,
                'quantity_on_hand': item.quantity_on_hand,
                'reorder_level': item.reorder_level,
                'shortage': round((item.reorder_level or 0.0) - (item.quantity_on_hand or 0.0), 2),
            }
            for item in items
        ]
        CacheManager.set_cache(cache_key, result, ttl_minutes=5)
        return result

    @staticmethod
    def get_customer_lifetime_value(workspace_id: str, retailer_id: int) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        summary = db.session.query(
            func.count(SalesOrder.id).label('order_count'),
            func.coalesce(func.sum(SalesOrder.total_amount), 0).label('total_revenue'),
            func.coalesce(func.avg(SalesOrder.total_amount), 0).label('avg_order_value'),
            func.max(SalesOrder.order_date).label('last_order_date'),
            func.min(SalesOrder.order_date).label('first_order_date'),
        ).filter(
            SalesOrder.workspace_id == workspace_id,
            SalesOrder.retailer_id == retailer_id,
        ).first()

        return {
            'retailer_id': retailer_id,
            'order_count': int(summary.order_count or 0),
            'lifetime_value': round(float(summary.total_revenue or 0.0), 2),
            'average_order_value': round(float(summary.avg_order_value or 0.0), 2),
            'last_order_date': summary.last_order_date.isoformat() if summary.last_order_date else None,
            'first_order_date': summary.first_order_date.isoformat() if summary.first_order_date else None,
        }

    @staticmethod
    def get_outstanding_summary(workspace_id: str) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        outstanding = db.session.query(
            func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0).label('outstanding')
        ).filter(
            Invoice.workspace_id == workspace_id,
            Invoice.payment_status != 'paid',
        ).scalar()

        overdue_30 = db.session.query(
            func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0).label('overdue')
        ).filter(
            Invoice.workspace_id == workspace_id,
            Invoice.payment_status != 'paid',
            Invoice.due_date < date.today() - timedelta(days=30),
        ).scalar()

        overdue_60 = db.session.query(
            func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0).label('overdue')
        ).filter(
            Invoice.workspace_id == workspace_id,
            Invoice.payment_status != 'paid',
            Invoice.due_date < date.today() - timedelta(days=60),
        ).scalar()

        overdue_90 = db.session.query(
            func.coalesce(func.sum(Invoice.net_amount - Invoice.paid_amount), 0).label('overdue')
        ).filter(
            Invoice.workspace_id == workspace_id,
            Invoice.payment_status != 'paid',
            Invoice.due_date < date.today() - timedelta(days=90),
        ).scalar()

        return {
            'total_outstanding': round(float(outstanding or 0.0), 2),
            'overdue_30_days': round(float(overdue_30 or 0.0), 2),
            'overdue_60_days': round(float(overdue_60 or 0.0), 2),
            'overdue_90_days': round(float(overdue_90 or 0.0), 2),
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def get_financial_kpis(workspace_id: str, period: str = 'MTD') -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        today = date.today()
        if period == 'MTD':
            start_date = today.replace(day=1)
        elif period == 'QTD':
            quarter = (today.month - 1) // 3
            start_date = date(today.year, quarter * 3 + 1, 1)
        elif period == 'YTD':
            start_date = date(today.year, 1, 1)
        elif period == 'LAST_12M':
            start_date = today - timedelta(days=365)
        else:
            start_date = today - timedelta(days=30)

        invoices = db.session.query(
            func.coalesce(func.sum(Invoice.total_amount), 0).label('revenue'),
            func.coalesce(func.sum(Invoice.tax_amount), 0).label('tax'),
            func.coalesce(func.count(Invoice.id), 0).label('invoice_count'),
            func.coalesce(func.avg(Invoice.net_amount), 0).label('avg_invoice_value'),
        ).filter(
            Invoice.workspace_id == workspace_id,
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= today,
        ).first()

        outstanding = BusinessBrain.get_outstanding_summary(workspace_id)

        return {
            'period': period,
            'start_date': start_date.isoformat(),
            'end_date': today.isoformat(),
            'revenue': round(float(invoices.revenue or 0.0), 2),
            'tax': round(float(invoices.tax or 0.0), 2),
            'invoice_count': int(invoices.invoice_count or 0),
            'average_invoice_value': round(float(invoices.avg_invoice_value or 0.0), 2),
            'total_outstanding': outstanding['total_outstanding'],
            'overdue_30_days': outstanding['overdue_30_days'],
            'overdue_60_days': outstanding['overdue_60_days'],
            'overdue_90_days': outstanding['overdue_90_days'],
        }

    @staticmethod
    def get_party_credit_status(workspace_id: str, party_id: int) -> dict:
        workspace_id = BusinessBrain._validate_workspace(workspace_id)
        outstanding = BusinessBrain.calculate_outstanding(workspace_id, party_id)
        credit_limit = 100000.0
        party = (
            db.session.query(Retailer)
            .filter(Retailer.id == party_id, Retailer.workspace_id == workspace_id)
            .one_or_none()
        )
        if party is None:
            party = (
                db.session.query(Distributor)
                .filter(Distributor.id == party_id, Distributor.workspace_id == workspace_id)
                .one_or_none()
            )
        if party is not None and hasattr(party, 'credit_limit'):
            credit_limit = float(getattr(party, 'credit_limit', credit_limit) or credit_limit)

        available = max(credit_limit - outstanding['outstanding'], 0.0)

        return {
            'party_id': party_id,
            'credit_limit': round(credit_limit, 2),
            'outstanding': outstanding['outstanding'],
            'available': round(available, 2),
            'can_purchase': available > 0,
            'reason': 'OK' if available > 0 else 'Credit limit exceeded',
        }
