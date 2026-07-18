"""
Platform Layer - Cache Manager Service (Mock Implementation)
Provides performance caching for frequently accessed data.

Phase 4 Status: Mock implementation (no-op for now)
Production will: Use Redis/Memcached for distributed caching
"""

from datetime import datetime, timedelta, timezone
import logging


logger = logging.getLogger(__name__)


class CacheManager:
    """
    Performance caching for frequently accessed data.
    
    Phase 4: Simple in-memory mock with optional Redis backend support.
    """
    
    _cache: dict[str, tuple[datetime, object]] = {}
    _redis_client = None

    @staticmethod
    def _backend_kind() -> str:
        return 'redis' if CacheManager._get_redis_client() is not None else 'memory'

    @staticmethod
    def _get_redis_client():
        try:
            import os
            import redis
        except Exception:
            return None

        if CacheManager._redis_client is None:
            redis_url = os.getenv('REDIS_URL') or os.getenv('CACHE_URL')
            if redis_url:
                CacheManager._redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        return CacheManager._redis_client
    
    @staticmethod
    def cache_inventory_levels(workspace_id: str, ttl_minutes: int = 15) -> bool:
        """
        Cache current inventory levels (expensive query).
        
        Args:
            workspace_id: Tenant identifier
            ttl_minutes: Time-to-live in minutes
            
        Returns: bool (True if cached)
        """
        try:
            cache_key = f"inventory_levels:{workspace_id}"
            CacheManager.set_cache(cache_key, True, ttl_minutes=ttl_minutes)
            return True
        except Exception as e:
            logger.error(f"cache_inventory_levels failed: {e}")
            return False
    
    @staticmethod
    def invalidate_inventory(workspace_id: str, item_id: str = None) -> bool:
        """
        Invalidate cached inventory when items change.
        
        Args:
            workspace_id: Tenant identifier
            item_id: Specific item to invalidate (None = invalidate all)
            
        Returns: bool (True if invalidated)
        """
        try:
            if item_id is not None:
                pattern = f"inventory:{workspace_id}:{item_id}"
            else:
                pattern = f"inventory:*{workspace_id}*"
            CacheManager.invalidate_pattern(pattern)
            return True
        except Exception as e:
            logger.error(f"invalidate_inventory failed: {e}")
            return False
    
    @staticmethod
    def invalidate_party_ledger(workspace_id: str, party_id: int) -> bool:
        """
        Invalidate cached ledger when payment received.
        
        Args:
            workspace_id: Tenant identifier
            party_id: Party ID
            
        Returns: bool (True if invalidated)
        """
        try:
            pattern = f"ledger:{workspace_id}:*" if party_id is None else f"ledger:{workspace_id}:{party_id}"
            CacheManager.invalidate_pattern(pattern)
            return True
        except Exception as e:
            logger.error(f"invalidate_party_ledger failed: {e}")
            return False
    
    @staticmethod
    def delete_cache(key: str) -> bool:
        """Delete a specific cached key and return whether it existed."""
        redis_client = CacheManager._get_redis_client()
        if redis_client is not None:
            try:
                return bool(redis_client.delete(key))
            except Exception:
                pass
        if key in CacheManager._cache:
            CacheManager._cache.pop(key, None)
            return True
        return False

    @staticmethod
    def get_cache(key: str, default=None):
        """Get cached value."""
        redis_client = CacheManager._get_redis_client()
        if redis_client is not None:
            try:
                raw = redis_client.get(key)
                if raw is not None:
                    import json
                    value = json.loads(raw)
                    if isinstance(value, dict):
                        return value
                    return value
            except Exception:
                pass

        cached = CacheManager._cache.get(key)
        if not cached:
            return default
        expires_at, value = cached
        if datetime.now(timezone.utc) >= expires_at:
            CacheManager._cache.pop(key, None)
            return default
        return value
    
    @staticmethod
    def set_cache(key: str, value, ttl_minutes: int = 15):
        """Set cached value with TTL."""
        redis_client = CacheManager._get_redis_client()
        if redis_client is not None:
            try:
                import json
                ttl_seconds = int(ttl_minutes * 60)
                redis_client.setex(key, ttl_seconds, json.dumps(value))
                return True
            except Exception:
                pass

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        CacheManager._cache[key] = (expires_at, value)
        return True

    @staticmethod
    def invalidate_pattern(pattern: str) -> None:
        """Invalidate all cache keys matching a simple glob-style pattern."""
        import fnmatch

        redis_client = CacheManager._get_redis_client()
        if redis_client is not None:
            try:
                matching_keys = redis_client.keys(pattern)
                for key in matching_keys:
                    redis_client.delete(key)
            except Exception:
                pass

        keys = [k for k in list(CacheManager._cache.keys()) if fnmatch.fnmatch(k, pattern)]
        for key in keys:
            CacheManager._cache.pop(key, None)
