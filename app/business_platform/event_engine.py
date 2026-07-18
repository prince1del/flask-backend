"""
Platform Layer - Event Engine Service (Persisted Event Support)
Provides event emission for reactive architecture.
"""

from collections import defaultdict
from datetime import datetime, timezone

import requests
from flask import current_app

from app.db import db
from app.models import EventRecord, EventSubscription
from app.business_platform.cache_manager import CacheManager


class EventEngine:
    """Event emission and subscription system backed by the database."""

    _subscribers = {}
    _pending_events = defaultdict(list)

    @staticmethod
    def _ensure_app_context() -> None:
        try:
            current_app._get_current_object()
        except RuntimeError:
            from app.web_app import create_app

            create_app().app_context().push()

    @staticmethod
    def publish(event_type: str, workspace_id: str, data: dict) -> dict:
        EventEngine._ensure_app_context()
        event = EventRecord(
            workspace_id=workspace_id,
            event_type=event_type,
            payload=data,
        )
        db.session.add(event)
        db.session.commit()
        payload = {
            'id': event.id,
            'event_type': event_type,
            'type': event_type,
            'workspace_id': workspace_id,
            'timestamp': event.created_at.isoformat() if event.created_at else datetime.now(timezone.utc).isoformat(),
            'data': data,
        }
        EventEngine._pending_events[workspace_id].append(payload)
        redis_client = CacheManager._get_redis_client()
        if redis_client is not None:
            try:
                import json
                redis_client.publish(f"events:{workspace_id}:{event_type}", json.dumps(payload))
            except Exception:
                pass
        EventEngine.emit(event_type, workspace_id, data)
        return payload

    @staticmethod
    def list_events(workspace_id: str) -> list:
        EventEngine._ensure_app_context()
        return [event.to_dict() for event in EventRecord.query.filter_by(workspace_id=workspace_id).order_by(EventRecord.created_at.desc()).all()]

    @staticmethod
    def get_pending_events(workspace_id: str, event_type: str | None = None) -> list:
        EventEngine._ensure_app_context()
        events = EventEngine._pending_events.get(workspace_id, [])
        if event_type:
            return [event for event in events if event.get('event_type') == event_type]
        return list(events)

    @staticmethod
    def drain_queue(workspace_id: str, event_type: str | None = None) -> list:
        EventEngine._ensure_app_context()
        pending = EventEngine._pending_events.get(workspace_id, [])
        if event_type:
            drained = [event for event in pending if event.get('event_type') == event_type]
            EventEngine._pending_events[workspace_id] = [event for event in pending if event.get('event_type') != event_type]
        else:
            drained = list(pending)
            EventEngine._pending_events[workspace_id] = []
        return drained

    @staticmethod
    def replay_events(workspace_id: str, event_type: str | None = None) -> list:
        EventEngine._ensure_app_context()
        query = EventRecord.query.filter_by(workspace_id=workspace_id)
        if event_type:
            query = query.filter_by(event_type=event_type)
        return [event.to_dict() for event in query.order_by(EventRecord.created_at.asc()).all()]

    @staticmethod
    def create_subscription(event_type: str, workspace_id: str, config: dict | None = None) -> dict:
        EventEngine._ensure_app_context()
        callback_url = config.get('callback_url') if config else None
        existing = EventSubscription.query.filter_by(
            workspace_id=workspace_id,
            event_type=event_type,
            callback_url=callback_url,
            active=True,
        ).first()
        if existing:
            return existing.to_dict()

        subscription = EventSubscription(
            workspace_id=workspace_id,
            event_type=event_type,
            callback_url=callback_url,
            filters=config.get('filters') if config else {},
            active=config.get('active', True) if config else True,
        )
        db.session.add(subscription)
        db.session.commit()
        if callback_url:
            EventEngine.subscribe(
                event_type,
                workspace_id,
                lambda event, url=callback_url: requests.post(url, json=event),
            )
        else:
            EventEngine.subscribe(event_type, workspace_id, lambda event: None)
        return subscription.to_dict()

    @staticmethod
    def list_subscriptions(workspace_id: str) -> list:
        EventEngine._ensure_app_context()
        return [subscription.to_dict() for subscription in EventSubscription.query.filter_by(workspace_id=workspace_id, active=True).all()]

    @staticmethod
    def delete_subscription(subscription_id: str) -> bool:
        EventEngine._ensure_app_context()
        subscription = db.session.get(EventSubscription, int(subscription_id)) if str(subscription_id).isdigit() else None
        if subscription is None:
            return False
        subscription.active = False
        db.session.commit()
        return True

    @staticmethod
    def emit(event_type: str, workspace_id: str, data: dict) -> bool:
        try:
            event = {
                'type': event_type,
                'event_type': event_type,
                'workspace_id': workspace_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'data': data,
            }
            event_key = f"{workspace_id}:{event_type}"
            if event_type == 'inventory_adjusted':
                item_id = event.get('data', {}).get('item_id')
                if item_id is not None:
                    CacheManager.invalidate_inventory(workspace_id, item_id)
                CacheManager.invalidate_pattern(f"inventory_valuation:{workspace_id}")
                CacheManager.invalidate_pattern(f"low_stock_items:{workspace_id}")
            elif event_type == 'sales_order_created':
                CacheManager.invalidate_pattern(f"sales_summary:{workspace_id}:*")
                CacheManager.invalidate_pattern(f"sales_by_product:{workspace_id}:*")
            elif event_type == 'payment_received':
                party_id = event.get('data', {}).get('party_id')
                if party_id is not None:
                    CacheManager.invalidate_pattern(f"outstanding:{workspace_id}:{party_id}")
                    CacheManager.invalidate_party_ledger(workspace_id, party_id)
            elif event_type == 'invoice_created':
                party_id = event.get('data', {}).get('party_id')
                if party_id is not None:
                    CacheManager.invalidate_pattern(f"outstanding:{workspace_id}:*")
                    CacheManager.invalidate_pattern(f"ledger:{workspace_id}:{party_id}")

            if event_key not in EventEngine._subscribers or not EventEngine._subscribers[event_key]:
                for subscription in EventSubscription.query.filter_by(workspace_id=workspace_id, event_type=event_type, active=True).all():
                    if subscription.callback_url:
                        EventEngine.subscribe(
                            event_type,
                            workspace_id,
                            lambda event, url=subscription.callback_url: requests.post(url, json=event),
                        )
                    else:
                        EventEngine.subscribe(event_type, workspace_id, lambda event: None)

            if event_key in EventEngine._subscribers:
                for handler in EventEngine._subscribers[event_key]:
                    try:
                        handler(event)
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    @staticmethod
    def subscribe(event_type: str, workspace_id: str, handler_func: callable) -> bool:
        try:
            event_key = f"{workspace_id}:{event_type}"
            if event_key not in EventEngine._subscribers:
                EventEngine._subscribers[event_key] = []
            if handler_func not in EventEngine._subscribers[event_key]:
                EventEngine._subscribers[event_key].append(handler_func)
            return True
        except Exception:
            return False

# EVENT TYPES (Emitted by Business Module)
# ─────────────────────────────────────────

# EventEngine.emit('inventory_adjusted', workspace_id, {
#     'item_id': str,
#     'warehouse_id': str,
#     'previous_qty': float,
#     'new_qty': float,
#     'adjustment': float,
#     'reason': str,
#     'timestamp': ISO,
#     'user_id': str
# })

# EventEngine.emit('sales_order_created', workspace_id, {
#     'order_id': str,
#     'party_id': str,
#     'total': float,
#     'items': [{...}],
#     'timestamp': ISO,
#     'user_id': str
# })

# EventEngine.emit('invoice_created', workspace_id, {
#     'invoice_id': str,
#     'order_id': str,
#     'party_id': str,
#     'amount': float,
#     'due_date': ISO,
#     'timestamp': ISO
# })

# EventEngine.emit('payment_received', workspace_id, {
#     'payment_id': str,
#     'party_id': str,
#     'amount': float,
#     'invoice_id': str,
#     'timestamp': ISO
# })
