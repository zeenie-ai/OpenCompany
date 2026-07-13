"""Cache service with Redis (production) or SQLite (development) backend.

Follows n8n pattern where SQLite is sufficient for single-process deployments,
with Redis used only for distributed queue mode or high-performance needs.
"""

import json
from typing import Any, Dict, Optional, List, TYPE_CHECKING

try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

from core.config import Settings
from core.logging import get_logger, log_cache_operation

if TYPE_CHECKING:
    from core.database import Database

logger = get_logger(__name__)


class CacheService:
    """Async cache service with Redis or SQLite backend.

    Backend selection:
    - Redis: When REDIS_ENABLED=true and Redis is available (production)
    - SQLite: When Redis disabled or unavailable (development)
    - Memory: Temporary fallback if both fail
    """

    def __init__(self, settings: Settings, database: Optional["Database"] = None):
        self.settings = settings
        self.database = database  # SQLite backend
        self.redis: Optional[redis.Redis] = None
        self.memory_cache: Dict[str, Any] = {}  # Emergency fallback only
        self.use_redis = settings.redis_enabled and REDIS_AVAILABLE
        self.use_sqlite = not self.use_redis and database is not None
        self._streams_available = False  # Checked during startup

    async def startup(self):
        """Initialize cache connection."""
        if self.use_redis and self.settings.redis_url:
            try:
                self.redis = redis.from_url(
                    self.settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                )

                # Test connection
                await self.redis.ping()
                logger.info("Redis cache initialized", url=self.settings.redis_url)

                # Test Redis Streams availability (required for trigger nodes)
                await self._check_streams_support()

            except Exception as e:
                logger.warning("Redis connection failed, falling back", error=str(e))
                self.use_redis = False
                self.redis = None
                # Try SQLite fallback
                if self.database:
                    self.use_sqlite = True
                    logger.info("Using SQLite cache (Redis fallback)")
        else:
            if self.use_sqlite:
                logger.info("Using SQLite cache (n8n pattern - no Redis required for single-process)")
            else:
                logger.info("Using in-memory cache", redis_enabled=self.settings.redis_enabled, redis_available=REDIS_AVAILABLE)

    async def _check_streams_support(self):
        """Check if Redis supports Streams (XADD/XREAD commands).

        Some Redis-compatible services (e.g., certain cloud providers) don't support Streams.
        We test this at startup to avoid runtime failures in trigger nodes.
        """
        if not self.redis:
            self._streams_available = False
            return

        test_stream = "_opencompany_streams_test"
        try:
            # Try XADD - this will fail if Streams aren't supported
            msg_id = await self.redis.xadd(test_stream, {"test": "1"}, maxlen=1)
            if msg_id:
                # Clean up test stream
                await self.redis.delete(test_stream)
                self._streams_available = True
                logger.info("Redis Streams available - trigger nodes will use Redis persistence")
            else:
                self._streams_available = False
                logger.warning("Redis Streams test failed - trigger nodes will use memory mode")
        except Exception as e:
            self._streams_available = False
            error_str = str(e).lower()
            if "unknown command" in error_str:
                logger.warning("Redis Streams not supported by server - trigger nodes will use memory mode")
            else:
                logger.warning(f"Redis Streams check failed: {e} - trigger nodes will use memory mode")

    async def shutdown(self):
        """Close cache connections."""
        if self.redis:
            await self.redis.close()
            logger.info("Redis cache connections closed")

        # Clear memory cache
        self.memory_cache.clear()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        try:
            if self.use_redis and self.redis:
                value = await self.redis.get(key)
                if value:
                    log_cache_operation(logger, "get", key, hit=True)
                    return json.loads(value)
                else:
                    log_cache_operation(logger, "get", key, hit=False)
                    return None
            elif self.use_sqlite and self.database:
                # SQLite cache
                value = await self.database.get_cache_entry(key)
                if value:
                    log_cache_operation(logger, "get", key, hit=True)
                    return json.loads(value)
                else:
                    log_cache_operation(logger, "get", key, hit=False)
                    return None
            else:
                # Memory cache fallback
                value = self.memory_cache.get(key)
                log_cache_operation(logger, "get", key, hit=value is not None)
                return value

        except Exception as e:
            logger.error("Cache get failed", key=key, error=str(e))
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache with optional TTL."""
        try:
            ttl = ttl or self.settings.cache_ttl

            if self.use_redis and self.redis:
                serialized = json.dumps(value, default=str)
                await self.redis.setex(key, ttl, serialized)
                log_cache_operation(logger, "set", key, ttl=ttl)
                return True
            elif self.use_sqlite and self.database:
                # SQLite cache with TTL support
                serialized = json.dumps(value, default=str)
                await self.database.set_cache_entry(key, serialized, ttl)
                log_cache_operation(logger, "set", key, ttl=ttl)
                return True
            else:
                # Memory cache fallback (no TTL)
                self.memory_cache[key] = value
                log_cache_operation(logger, "set", key, ttl=ttl)
                return True

        except Exception as e:
            logger.error("Cache set failed", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        try:
            if self.use_redis and self.redis:
                deleted = await self.redis.delete(key)
                log_cache_operation(logger, "delete", key, deleted=bool(deleted))
                return bool(deleted)
            elif self.use_sqlite and self.database:
                # SQLite cache
                deleted = await self.database.delete_cache_entry(key)
                log_cache_operation(logger, "delete", key, deleted=deleted)
                return deleted
            else:
                # Memory cache fallback
                deleted = key in self.memory_cache
                if deleted:
                    del self.memory_cache[key]
                log_cache_operation(logger, "delete", key, deleted=deleted)
                return deleted

        except Exception as e:
            logger.error("Cache delete failed", key=key, error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        try:
            if self.use_redis and self.redis:
                exists = await self.redis.exists(key)
                return bool(exists)
            elif self.use_sqlite and self.database:
                return await self.database.cache_exists(key)
            else:
                return key in self.memory_cache

        except Exception as e:
            logger.error("Cache exists check failed", key=key, error=str(e))
            return False

    async def expire(self, key: str, ttl: int) -> bool:
        """Set TTL for existing key."""
        try:
            if self.use_redis and self.redis:
                return bool(await self.redis.expire(key, ttl))
            else:
                # Memory cache doesn't support TTL updates
                return key in self.memory_cache

        except Exception as e:
            logger.error("Cache expire failed", key=key, error=str(e))
            return False

    async def clear_pattern(self, pattern: str) -> int:
        """Clear keys matching pattern."""
        try:
            if self.use_redis and self.redis:
                keys = await self.redis.keys(pattern)
                if keys:
                    deleted = await self.redis.delete(*keys)
                    log_cache_operation(logger, "clear_pattern", pattern, deleted=deleted)
                    return deleted
                return 0
            elif self.use_sqlite and self.database:
                # SQLite cache pattern matching
                deleted = await self.database.delete_cache_pattern(pattern)
                log_cache_operation(logger, "clear_pattern", pattern, deleted=deleted)
                return deleted
            else:
                # Memory cache pattern matching
                keys_to_delete = [k for k in self.memory_cache.keys() if pattern.replace("*", "") in k]
                for key in keys_to_delete:
                    del self.memory_cache[key]
                log_cache_operation(logger, "clear_pattern", pattern, deleted=len(keys_to_delete))
                return len(keys_to_delete)

        except Exception as e:
            logger.error("Cache clear pattern failed", pattern=pattern, error=str(e))
            return 0

    # ============================================================================
    # API Key Specific Cache Methods
    # ============================================================================

    async def cache_api_key(self, provider: str, session_id: str, key_data: Dict[str, Any]) -> bool:
        """Cache API key data."""
        cache_key = f"api_key:{provider}:{session_id}"
        return await self.set(cache_key, key_data, self.settings.api_key_cache_ttl)

    async def get_cached_api_key(self, provider: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Get cached API key data."""
        cache_key = f"api_key:{provider}:{session_id}"
        return await self.get(cache_key)

    async def remove_cached_api_key(self, provider: str, session_id: str) -> bool:
        """Remove cached API key."""
        cache_key = f"api_key:{provider}:{session_id}"
        return await self.delete(cache_key)

    async def cache_models(self, provider: str, models: List[str]) -> bool:
        """Cache available models for provider."""
        cache_key = f"models:{provider}"
        return await self.set(cache_key, {"models": models, "cached_at": "now"}, ttl=3600)  # 1 hour

    async def get_cached_models(self, provider: str) -> Optional[List[str]]:
        """Get cached models for provider."""
        cache_key = f"models:{provider}"
        data = await self.get(cache_key)
        return data.get("models") if data else None

    # ============================================================================
    # Redis Streams Methods for Event Waiting
    # ============================================================================

    async def stream_add(self, stream: str, data: Dict[str, Any], maxlen: int = 1000) -> Optional[str]:
        """Add message to Redis Stream.

        Args:
            stream: Stream name (e.g., 'events:whatsapp_message_received')
            data: Event data to store
            maxlen: Maximum stream length (approximate, uses ~)

        Returns:
            Message ID if successful, None otherwise
        """
        try:
            if self.use_redis and self.redis and self._streams_available:
                # Serialize ALL values with json.dumps to preserve types
                # This matches the pattern used in set() and ensures proper round-trip:
                # - json.dumps(True) → "true" (lowercase, valid JSON)
                # - json.loads("true") → True (Python bool)
                # Using str() would break: str(True) → "True" → json.loads fails
                serialized = {k: json.dumps(v, default=str) for k, v in data.items()}
                msg_id = await self.redis.xadd(stream, serialized, maxlen=maxlen, approximate=True)
                logger.debug(f"Stream add: {stream} -> {msg_id}")
                return msg_id
            return None
        except Exception as e:
            logger.error(f"Stream add failed: {stream}", error=str(e))
            return None

    async def stream_read(self, streams: Dict[str, str], count: int = 1, block: Optional[int] = None) -> Optional[List[Any]]:
        """Read from Redis Streams.

        Args:
            streams: Dict of stream_name -> last_id (use '$' for new messages only, '0' for all)
            count: Maximum number of messages to read
            block: Milliseconds to block (None = no blocking, 0 = infinite)

        Returns:
            List of [stream_name, [(msg_id, data), ...]] or None
        """
        try:
            if self.use_redis and self.redis:
                result = await self.redis.xread(streams, count=count, block=block)
                return result
            return None
        except Exception as e:
            logger.error(f"Stream read failed: {streams.keys()}", error=str(e))
            return None

    # Consumer-group methods (stream_create_group / stream_read_group /
    # stream_ack / stream_delete) and the public is_streams_available()
    # were retired in Wave 15.3 with the event_waiter Redis branch.
    # stream_add/stream_read stay for ExecutionCache.add_event/get_events;
    # the _streams_available flag stays because stream_add guards on it.

    def is_redis_available(self) -> bool:
        """Check if Redis is available and connected."""
        return self.use_redis and self.redis is not None
