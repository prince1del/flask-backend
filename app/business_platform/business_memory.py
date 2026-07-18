"""
Platform Layer - Business Memory Service (Mock Implementation)
Stores and recalls lightweight recent events for business decisions.
"""


class BusinessMemory:
    """In-memory event storage for business memory."""

    _memory = []

    @staticmethod
    def store_event(event: dict) -> dict:
        BusinessMemory._memory.append(event)
        return {'stored': True, 'total_events': len(BusinessMemory._memory)}

    @staticmethod
    def recall_recent(limit: int = 10) -> dict:
        recent = BusinessMemory._memory[-limit:]
        return {
            'total_events': len(BusinessMemory._memory),
            'recent_events': recent,
        }

    @staticmethod
    def clear_memory() -> dict:
        BusinessMemory._memory = []
        return {'cleared': True}
