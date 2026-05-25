"""Execution cache service for Redis persistence.

Provides:
- Result caching (Prefect pattern) for idempotency
- Execution state persistence
- Distributed locking (Conductor pattern)
- Transaction checkpointing
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, Set, Union

from core.logging import get_logger
from core.cache import CacheService
from .models import ExecutionContext, WorkflowStatus, hash_inputs, DLQEntry

logger = get_logger(__name__)


def ensure_str(value: Union[str, bytes, None]) -> Optional[str]:
    """Ensure value is a string, handling both bytes and str.

    Redis with decode_responses=True returns strings directly.
    This helper handles both cases for compatibility.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


class ExecutionCache:
    """Redis-backed cache for workflow execution state.

    Key schema:
        execution:{id}:state     -> HASH {status, workflow_id, etc}
        execution:{id}:nodes     -> HASH {node_id -> NodeExecution JSON}
        execution:{id}:outputs   -> HASH {node_id -> output JSON}
        execution:{id}:events    -> STREAM (immutable event log)
        result:{exec}:{node}:{hash} -> JSON (cached result)
        executions:active        -> SET {execution_ids}
        lock:execution:{id}      -> STRING (lock token)
        heartbeat:{exec}:{node}  -> STRING (timestamp)
    """

    def __init__(self, cache_service: CacheService):
        self.cache = cache_service
        self._local_locks: Dict[str, asyncio.Lock] = {}

    # =========================================================================
    # EXECUTION STATE PERSISTENCE
    # =========================================================================

    async def save_execution_state(self, ctx: ExecutionContext) -> bool:
        """Persist execution context to Redis.

        Args:
            ctx: ExecutionContext to save

        Returns:
            True if saved successfully
        """
        try:
            key = f"execution:{ctx.execution_id}:state"
            data = ctx.to_dict()

            # Use Redis HSET for structured storage
            if self.cache.is_redis_available():
                mapping = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
                if mapping:  # Only call hset if mapping is not empty
                    await self.cache.redis.hset(key, mapping=mapping)
                # Set TTL (24 hours for completed, no TTL for active)
                if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                    await self.cache.redis.expire(key, 86400)

                # Track active executions
                if ctx.status == WorkflowStatus.RUNNING:
                    await self.cache.redis.sadd("executions:active", ctx.execution_id)
                else:
                    await self.cache.redis.srem("executions:active", ctx.execution_id)

                logger.debug("Saved execution state", execution_id=ctx.execution_id, status=ctx.status.value)
                return True
            else:
                # Fallback to simple key-value
                await self.cache.set(key, data, ttl=86400)
                return True

        except Exception as e:
            logger.error("Failed to save execution state", execution_id=ctx.execution_id, error=str(e))
            return False

    async def load_execution_state(
        self, execution_id: str, nodes: List[Dict] = None, edges: List[Dict] = None
    ) -> Optional[ExecutionContext]:
        """Load execution context from Redis.

        Args:
            execution_id: Execution ID to load
            nodes: Workflow nodes (not stored in Redis due to size)
            edges: Workflow edges (not stored in Redis due to size)

        Returns:
            ExecutionContext if found, None otherwise
        """
        try:
            key = f"execution:{execution_id}:state"

            if self.cache.is_redis_available():
                raw_data = await self.cache.redis.hgetall(key)
                if not raw_data:
                    return None

                # Deserialize Redis hash values
                # With decode_responses=True, values are already strings
                data = {}
                for k, v in raw_data.items():
                    key_str = ensure_str(k)
                    val_str = ensure_str(v)
                    try:
                        data[key_str] = json.loads(val_str)
                    except (json.JSONDecodeError, TypeError):
                        data[key_str] = val_str

                return ExecutionContext.from_dict(data, nodes, edges)
            else:
                # Fallback to simple key-value
                data = await self.cache.get(key)
                if data:
                    return ExecutionContext.from_dict(data, nodes, edges)
                return None

        except Exception as e:
            logger.error("Failed to load execution state", execution_id=execution_id, error=str(e))
            return None

    async def get_active_executions(self) -> Set[str]:
        """Get all active execution IDs.

        Returns:
            Set of execution IDs currently running
        """
        try:
            if self.cache.is_redis_available():
                members = await self.cache.redis.smembers("executions:active")
                return {ensure_str(m) for m in members}
            return set()
        except Exception as e:
            logger.error("Failed to get active executions", error=str(e))
            return set()

    async def delete_execution_state(self, execution_id: str) -> bool:
        """Delete execution state from Redis.

        Args:
            execution_id: Execution ID to delete

        Returns:
            True if deleted successfully
        """
        try:
            if self.cache.is_redis_available():
                keys = [
                    f"execution:{execution_id}:state",
                    f"execution:{execution_id}:events",
                ]
                await self.cache.redis.delete(*keys)
                await self.cache.redis.srem("executions:active", execution_id)
            return True
        except Exception as e:
            logger.error("Failed to delete execution state", execution_id=execution_id, error=str(e))
            return False

    # =========================================================================
    # RESULT CACHING (Prefect pattern)
    # =========================================================================

    async def get_cached_result(self, execution_id: str, node_id: str, inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get cached result for node execution (Prefect pattern).

        Args:
            execution_id: Execution ID
            node_id: Node ID
            inputs: Node inputs for cache key

        Returns:
            Cached result if found, None otherwise
        """
        try:
            input_hash = hash_inputs(inputs)
            cache_key = f"result:{execution_id}:{node_id}:{input_hash}"
            result = await self.cache.get(cache_key)
            if result:
                logger.debug("Cache hit", node_id=node_id, input_hash=input_hash[:8])
            return result
        except Exception as e:
            logger.error("Failed to get cached result", node_id=node_id, error=str(e))
            return None

    async def set_cached_result(
        self, execution_id: str, node_id: str, inputs: Dict[str, Any], result: Dict[str, Any], ttl: int = 3600
    ) -> bool:
        """Cache node execution result (Prefect pattern).

        Args:
            execution_id: Execution ID
            node_id: Node ID
            inputs: Node inputs for cache key
            result: Execution result to cache
            ttl: Time-to-live in seconds (default 1 hour)

        Returns:
            True if cached successfully
        """
        try:
            input_hash = hash_inputs(inputs)
            cache_key = f"result:{execution_id}:{node_id}:{input_hash}"
            await self.cache.set(cache_key, result, ttl=ttl)
            logger.debug("Cached result", node_id=node_id, input_hash=input_hash[:8])
            return True
        except Exception as e:
            logger.error("Failed to cache result", node_id=node_id, error=str(e))
            return False

    # =========================================================================
    # DISTRIBUTED LOCKING (Conductor pattern)
    # =========================================================================

    @asynccontextmanager
    async def distributed_lock(self, lock_name: str, timeout: int = 60):
        """Acquire distributed lock using Redis (Conductor pattern).

        Used to prevent concurrent workflow_decide() calls.

        Args:
            lock_name: Name of the lock (e.g., "execution:{id}:decide")
            timeout: Lock timeout in seconds

        Yields:
            Lock token if acquired

        Raises:
            TimeoutError: If lock cannot be acquired
        """
        lock_key = f"lock:{lock_name}"
        lock_token = str(uuid.uuid4())
        acquired = False

        try:
            # Try to acquire lock
            if self.cache.is_redis_available():
                # Redis SETNX with expiry
                acquired = await self.cache.redis.set(
                    lock_key,
                    lock_token,
                    ex=timeout,
                    nx=True,  # Only set if not exists
                )
            else:
                # Fallback to local asyncio lock
                if lock_name not in self._local_locks:
                    self._local_locks[lock_name] = asyncio.Lock()
                await asyncio.wait_for(self._local_locks[lock_name].acquire(), timeout=timeout)
                acquired = True

            if not acquired:
                raise TimeoutError(f"Could not acquire lock: {lock_name}")

            logger.debug("Lock acquired", lock_name=lock_name, token=lock_token[:8])
            yield lock_token

        finally:
            # Release lock
            if acquired:
                if self.cache.is_redis_available():
                    # Only release if we hold the lock (check token)
                    # With decode_responses=True, current is already a string
                    current = await self.cache.redis.get(lock_key)
                    if current and current == lock_token:
                        await self.cache.redis.delete(lock_key)
                        logger.debug("Lock released", lock_name=lock_name)
                else:
                    if lock_name in self._local_locks:
                        self._local_locks[lock_name].release()

    # =========================================================================
    # HEARTBEATS (for crash recovery)
    # =========================================================================

    async def update_heartbeat(self, execution_id: str, node_id: str) -> bool:
        """Update heartbeat for running node (for crash detection).

        Args:
            execution_id: Execution ID
            node_id: Node ID

        Returns:
            True if updated successfully
        """
        try:
            key = f"heartbeat:{execution_id}:{node_id}"
            timestamp = str(time.time())
            if self.cache.is_redis_available():
                await self.cache.redis.setex(key, 300, timestamp)  # 5 min TTL
            else:
                await self.cache.set(key, timestamp, ttl=300)
            return True
        except Exception as e:
            logger.error("Failed to update heartbeat", node_id=node_id, error=str(e))
            return False

    async def get_heartbeat(self, execution_id: str, node_id: str) -> Optional[float]:
        """Get last heartbeat timestamp for a node.

        Args:
            execution_id: Execution ID
            node_id: Node ID

        Returns:
            Timestamp if found, None otherwise
        """
        try:
            key = f"heartbeat:{execution_id}:{node_id}"
            if self.cache.is_redis_available():
                val = await self.cache.redis.get(key)
                # With decode_responses=True, val is already a string
                return float(val) if val else None
            else:
                val = await self.cache.get(key)
                return float(val) if val else None
        except Exception as e:
            logger.error("Failed to get heartbeat", node_id=node_id, error=str(e))
            return None

    # =========================================================================
    # EVENT HISTORY (for debugging and recovery)
    # =========================================================================

    async def add_event(self, execution_id: str, event_type: str, data: Dict[str, Any]) -> Optional[str]:
        """Add event to execution history stream.

        Args:
            execution_id: Execution ID
            event_type: Event type (e.g., 'node_started', 'node_completed')
            data: Event data

        Returns:
            Message ID if successful, None otherwise
        """
        try:
            stream_key = f"execution:{execution_id}:events"
            event_data = {"type": event_type, "timestamp": time.time(), **data}
            return await self.cache.stream_add(stream_key, event_data, maxlen=1000)
        except Exception as e:
            logger.error("Failed to add event", execution_id=execution_id, error=str(e))
            return None

    async def get_events(self, execution_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """Get execution event history.

        Args:
            execution_id: Execution ID
            count: Maximum events to return

        Returns:
            List of events
        """
        try:
            stream_key = f"execution:{execution_id}:events"
            if not self.cache.is_redis_available():
                return []

            # Read from stream
            result = await self.cache.stream_read({stream_key: "0"}, count=count)

            events = []
            if result:
                for stream_name, messages in result:
                    for msg_id, msg_data in messages:
                        # Deserialize event data
                        event = {}
                        for k, v in msg_data.items():
                            key_str = ensure_str(k)
                            val_str = ensure_str(v)
                            try:
                                event[key_str] = json.loads(val_str)
                            except (json.JSONDecodeError, TypeError):
                                event[key_str] = val_str
                        events.append(event)

            return events
        except Exception as e:
            logger.error("Failed to get events", execution_id=execution_id, error=str(e))
            return []

    # =========================================================================
    # TRANSACTION CHECKPOINTS (Prefect pattern)
    # =========================================================================

    async def checkpoint_transaction(self, transaction_id: str, node_id: str, result: Dict[str, Any]) -> bool:
        """Save transaction checkpoint (Prefect pattern).

        Args:
            transaction_id: Transaction ID
            node_id: Node that completed
            result: Node result

        Returns:
            True if saved successfully
        """
        try:
            key = f"txn:{transaction_id}:checkpoints"
            checkpoint = {"node_id": node_id, "result": result, "timestamp": time.time()}
            if self.cache.is_redis_available():
                await self.cache.redis.rpush(key, json.dumps(checkpoint))
                await self.cache.redis.expire(key, 86400)  # 24 hour TTL
            return True
        except Exception as e:
            logger.error("Failed to checkpoint", transaction_id=transaction_id, error=str(e))
            return False

    async def rollback_transaction(self, transaction_id: str) -> bool:
        """Rollback transaction by clearing checkpoints.

        Args:
            transaction_id: Transaction ID to rollback

        Returns:
            True if rolled back successfully
        """
        try:
            key = f"txn:{transaction_id}:checkpoints"
            if self.cache.is_redis_available():
                await self.cache.redis.delete(key)
            logger.info("Transaction rolled back", transaction_id=transaction_id)
            return True
        except Exception as e:
            logger.error("Failed to rollback", transaction_id=transaction_id, error=str(e))
            return False

    async def get_transaction_checkpoints(self, transaction_id: str) -> List[Dict[str, Any]]:
        """Get transaction checkpoints for recovery.

        Args:
            transaction_id: Transaction ID

        Returns:
            List of checkpoints
        """
        try:
            key = f"txn:{transaction_id}:checkpoints"
            if not self.cache.is_redis_available():
                return []

            raw_list = await self.cache.redis.lrange(key, 0, -1)
            return [json.loads(ensure_str(item)) for item in raw_list]
        except Exception as e:
            logger.error("Failed to get checkpoints", transaction_id=transaction_id, error=str(e))
            return []

    # =========================================================================
    # DEAD LETTER QUEUE (for failed executions)
    # =========================================================================

    async def add_to_dlq(self, entry: DLQEntry) -> bool:
        """Add failed node execution to Dead Letter Queue.

        Stores the entry in multiple indices for querying:
        - dlq:entries:{id} - Individual entry data
        - dlq:workflow:{workflow_id} - List of entry IDs for workflow
        - dlq:node_type:{node_type} - List of entry IDs by node type
        - dlq:all - Set of all entry IDs

        Args:
            entry: DLQEntry to add

        Returns:
            True if added successfully
        """
        try:
            entry_data = entry.to_dict()

            if self.cache.is_redis_available():
                # Store entry data
                entry_key = f"dlq:entries:{entry.id}"
                mapping = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in entry_data.items()}
                if mapping:  # Only call hset if mapping is not empty
                    await self.cache.redis.hset(entry_key, mapping=mapping)
                # Set TTL (7 days for DLQ entries)
                await self.cache.redis.expire(entry_key, 604800)

                # Add to workflow index
                workflow_key = f"dlq:workflow:{entry.workflow_id}"
                await self.cache.redis.lpush(workflow_key, entry.id)
                await self.cache.redis.expire(workflow_key, 604800)

                # Add to node type index
                node_type_key = f"dlq:node_type:{entry.node_type}"
                await self.cache.redis.lpush(node_type_key, entry.id)
                await self.cache.redis.expire(node_type_key, 604800)

                # Add to global set
                await self.cache.redis.sadd("dlq:all", entry.id)

                logger.info("Added to DLQ", entry_id=entry.id, node_id=entry.node_id, node_type=entry.node_type, error=entry.error[:100])
                return True
            else:
                # Fallback to simple key-value
                await self.cache.set(f"dlq:entries:{entry.id}", entry_data, ttl=604800)
                return True

        except Exception as e:
            logger.error("Failed to add to DLQ", entry_id=entry.id, error=str(e))
            return False

    async def get_dlq_entry(self, entry_id: str) -> Optional[DLQEntry]:
        """Get a single DLQ entry by ID.

        Args:
            entry_id: DLQ entry ID

        Returns:
            DLQEntry if found, None otherwise
        """
        try:
            entry_key = f"dlq:entries:{entry_id}"

            if self.cache.is_redis_available():
                raw_data = await self.cache.redis.hgetall(entry_key)
                if not raw_data:
                    return None

                # Deserialize Redis hash values
                data = {}
                for k, v in raw_data.items():
                    key_str = ensure_str(k)
                    val_str = ensure_str(v)
                    try:
                        data[key_str] = json.loads(val_str)
                    except (json.JSONDecodeError, TypeError):
                        data[key_str] = val_str

                return DLQEntry.from_dict(data)
            else:
                data = await self.cache.get(entry_key)
                if data:
                    return DLQEntry.from_dict(data)
                return None

        except Exception as e:
            logger.error("Failed to get DLQ entry", entry_id=entry_id, error=str(e))
            return None

    async def get_dlq_entries(self, workflow_id: Optional[str] = None, node_type: Optional[str] = None, limit: int = 100) -> List[DLQEntry]:
        """Get DLQ entries with optional filtering.

        Args:
            workflow_id: Filter by workflow ID
            node_type: Filter by node type
            limit: Maximum entries to return

        Returns:
            List of DLQEntry objects
        """
        try:
            if not self.cache.is_redis_available():
                return []

            # Determine which index to use
            if workflow_id:
                index_key = f"dlq:workflow:{workflow_id}"
            elif node_type:
                index_key = f"dlq:node_type:{node_type}"
            else:
                # Get all entries from global set
                entry_ids = await self.cache.redis.smembers("dlq:all")
                entry_ids = [ensure_str(eid) for eid in entry_ids][:limit]
                entries = []
                for entry_id in entry_ids:
                    entry = await self.get_dlq_entry(entry_id)
                    if entry:
                        entries.append(entry)
                # Sort by last_error_at descending
                entries.sort(key=lambda e: e.last_error_at, reverse=True)
                return entries

            # Get from LIST index
            raw_ids = await self.cache.redis.lrange(index_key, 0, limit - 1)
            entry_ids = [ensure_str(eid) for eid in raw_ids]

            entries = []
            for entry_id in entry_ids:
                entry = await self.get_dlq_entry(entry_id)
                if entry:
                    entries.append(entry)

            return entries

        except Exception as e:
            logger.error("Failed to get DLQ entries", error=str(e))
            return []

    async def remove_from_dlq(self, entry_id: str) -> bool:
        """Remove entry from DLQ after successful replay or manual purge.

        Args:
            entry_id: DLQ entry ID to remove

        Returns:
            True if removed successfully
        """
        try:
            if not self.cache.is_redis_available():
                return False

            # Get entry first to know which indices to update
            entry = await self.get_dlq_entry(entry_id)
            if not entry:
                return False

            # Remove from indices
            await self.cache.redis.lrem(f"dlq:workflow:{entry.workflow_id}", 0, entry_id)
            await self.cache.redis.lrem(f"dlq:node_type:{entry.node_type}", 0, entry_id)
            await self.cache.redis.srem("dlq:all", entry_id)

            # Delete entry data
            await self.cache.redis.delete(f"dlq:entries:{entry_id}")

            logger.info("Removed from DLQ", entry_id=entry_id)
            return True

        except Exception as e:
            logger.error("Failed to remove from DLQ", entry_id=entry_id, error=str(e))
            return False

    async def update_dlq_entry(self, entry_id: str, retry_count: int, error: str) -> bool:
        """Update DLQ entry after failed retry attempt.

        Args:
            entry_id: DLQ entry ID
            retry_count: New retry count
            error: Latest error message

        Returns:
            True if updated successfully
        """
        try:
            if not self.cache.is_redis_available():
                return False

            entry_key = f"dlq:entries:{entry_id}"
            await self.cache.redis.hset(
                entry_key, mapping={"retry_count": str(retry_count), "error": error, "last_error_at": str(time.time())}
            )

            logger.debug("Updated DLQ entry", entry_id=entry_id, retry_count=retry_count)
            return True

        except Exception as e:
            logger.error("Failed to update DLQ entry", entry_id=entry_id, error=str(e))
            return False

    async def get_dlq_stats(self) -> Dict[str, Any]:
        """Get DLQ statistics.

        Returns:
            Dictionary with DLQ stats (total count, by node type, by workflow)
        """
        try:
            if not self.cache.is_redis_available():
                return {"total": 0, "by_node_type": {}, "by_workflow": {}}

            # Get total count
            total = await self.cache.redis.scard("dlq:all")

            # Get all entries for breakdown
            entries = await self.get_dlq_entries(limit=1000)

            by_node_type = {}
            by_workflow = {}
            for entry in entries:
                by_node_type[entry.node_type] = by_node_type.get(entry.node_type, 0) + 1
                by_workflow[entry.workflow_id] = by_workflow.get(entry.workflow_id, 0) + 1

            return {"total": total, "by_node_type": by_node_type, "by_workflow": by_workflow}

        except Exception as e:
            logger.error("Failed to get DLQ stats", error=str(e))
            return {"total": 0, "by_node_type": {}, "by_workflow": {}}

    async def purge_dlq(
        self, workflow_id: Optional[str] = None, node_type: Optional[str] = None, older_than: Optional[float] = None
    ) -> int:
        """Purge entries from DLQ.

        Args:
            workflow_id: Only purge entries for this workflow
            node_type: Only purge entries for this node type
            older_than: Only purge entries older than this timestamp

        Returns:
            Number of entries purged
        """
        try:
            if not self.cache.is_redis_available():
                return 0

            entries = await self.get_dlq_entries(workflow_id=workflow_id, node_type=node_type, limit=10000)

            purged = 0
            for entry in entries:
                # Check age filter
                if older_than and entry.created_at > older_than:
                    continue

                if await self.remove_from_dlq(entry.id):
                    purged += 1

            logger.info("Purged DLQ entries", count=purged, workflow_id=workflow_id, node_type=node_type)
            return purged

        except Exception as e:
            logger.error("Failed to purge DLQ", error=str(e))
            return 0
