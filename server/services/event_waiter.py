"""Event Waiter Service - Generic event waiting for trigger nodes.

Supports any trigger type (WhatsApp, Email, Webhook, MQTT, etc.)
Uses Redis Streams when available for persistence, falls back to asyncio.Future.

Architecture:
- Redis mode: Events stored in Redis Streams, waiters poll streams with blocking XREAD
- Memory mode: Events dispatched to in-memory asyncio.Future waiters
"""

import asyncio
import json
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List, TYPE_CHECKING

from core.logging import get_logger

if TYPE_CHECKING:
    from core.cache import CacheService

logger = get_logger(__name__)


# =============================================================================
# CACHE SERVICE REFERENCE
# =============================================================================

_cache_service: Optional["CacheService"] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_cache_service(cache: "CacheService") -> None:
    """Set the cache service for Redis Streams support.

    Called during application startup from main.py.
    """
    global _cache_service, _main_loop
    _cache_service = cache
    # Store reference to the main event loop for thread-safe dispatch
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None
    mode = "Redis Streams" if cache and cache.is_redis_available() else "asyncio.Future"
    logger.info(f"[EventWaiter] Initialized with {mode} backend")


def get_cache_service() -> Optional["CacheService"]:
    """Get the cache service if available."""
    return _cache_service


def is_redis_mode() -> bool:
    """Check if Redis Streams mode is active.

    Returns True only if Redis is connected AND supports Streams commands.
    This prevents runtime failures when Redis doesn't support XREADGROUP/XADD.
    """
    return _cache_service is not None and _cache_service.is_streams_available()


# =============================================================================
# NOTE: LID TO PHONE RESOLUTION
# =============================================================================
# LID (Linked ID) resolution is now handled by the Go WhatsApp RPC (service.go).
# The Go RPC resolves LIDs to phone numbers before sending the message_received event.
# The sender_phone field in the event data contains the already-resolved phone number.
# No Python-side LID cache is needed anymore.
# =============================================================================


# =============================================================================
# TRIGGER CONFIGURATION REGISTRY
# =============================================================================


@dataclass
class TriggerConfig:
    """Configuration for a trigger node type."""

    node_type: str
    event_type: str  # Event to wait for (e.g., 'whatsapp_message_received')
    display_name: str


# Registry of supported trigger types (event-based triggers only)
# Note: cronScheduler is NOT an event-based trigger - it uses APScheduler directly
TRIGGER_REGISTRY: Dict[str, TriggerConfig] = {
    # Framework-level triggers — not owned by any plugin domain.
    "start": TriggerConfig(node_type="start", event_type="deploy_triggered", display_name="Deploy Start"),
    "webhookTrigger": TriggerConfig(node_type="webhookTrigger", event_type="webhook_received", display_name="Webhook Request"),
    "chatTrigger": TriggerConfig(node_type="chatTrigger", event_type="chat_message_received", display_name="Chat Message"),
    "taskTrigger": TriggerConfig(node_type="taskTrigger", event_type="task_completed", display_name="Task Completed"),
    # Plugin-owned trigger entries (whatsappReceive, twitterReceive,
    # telegramReceive, emailReceive, googleGmailReceive) live in their
    # plugin folders' ``_filters.py`` and are backfilled here from each
    # plugin's ``event_type`` ClassVar via
    # ``_auto_populate_from_plugins`` (Wave 11.I, milestone K). Gmail's
    # explicit alias entry was retired in milestone P after the
    # downstream callers (POLLING_TRIGGER_TYPES, deployment manager,
    # frontend trigger list) were renamed to the canonical class type.
    # Future triggers - just add to registry:
    # 'mqttTrigger': TriggerConfig('mqttTrigger', 'mqtt_message', 'MQTT Message'),
}


def _auto_populate_from_plugins() -> None:
    """Wave 11.D.11: walk registered TriggerNode subclasses and backfill
    TRIGGER_REGISTRY + FILTER_BUILDERS for any plugin that declares
    ``event_type`` + ``build_filter``. Explicit hardcoded entries in
    this module always win so plugin upgrades never silently replace
    hand-maintained behaviour.

    Called lazily on first access (``get_trigger_config`` /
    ``build_filter``) — importing the plugin class registry at
    module-load time would risk a circular import with services.plugin.
    """
    try:
        from services.node_registry import registered_node_classes
        from services.plugin import TriggerNode
    except Exception:
        return

    for node_type, cls in registered_node_classes().items():
        if not isinstance(cls, type) or not issubclass(cls, TriggerNode):
            continue
        event_type = getattr(cls, "event_type", "") or ""
        if not event_type:
            continue
        # Never override a hardcoded entry — authoritative.
        if node_type not in TRIGGER_REGISTRY:
            TRIGGER_REGISTRY[node_type] = TriggerConfig(
                node_type=node_type,
                event_type=event_type,
                display_name=getattr(cls, "display_name", node_type),
            )
        if node_type not in FILTER_BUILDERS:
            # Instance-bound build_filter — instantiate once per node_type.
            instance = cls()
            FILTER_BUILDERS[node_type] = lambda params, _inst=instance: _inst.build_filter(
                _inst.Params.model_validate(params) if params else _inst.Params(),
            )


_populated = False


def _ensure_populated() -> None:
    global _populated
    if _populated:
        return
    _populated = True
    _auto_populate_from_plugins()


def is_trigger_node(node_type: str) -> bool:
    """Check if a node type is a trigger node (workflow starting point).

    Uses constants.WORKFLOW_TRIGGER_TYPES for comprehensive trigger detection.
    This includes all trigger types: start, cronScheduler, and event-based triggers.
    """
    from constants import WORKFLOW_TRIGGER_TYPES

    return node_type in WORKFLOW_TRIGGER_TYPES


def is_event_trigger_node(node_type: str) -> bool:
    """Check if a node type is an event-based trigger (waits for events).

    Event-based triggers are registered in TRIGGER_REGISTRY and wait for
    external events to fire. This excludes 'start' and 'cronScheduler' which
    have their own execution mechanisms.
    """
    _ensure_populated()
    return node_type in TRIGGER_REGISTRY


def get_trigger_config(node_type: str) -> Optional[TriggerConfig]:
    """Get trigger configuration for a node type."""
    _ensure_populated()
    return TRIGGER_REGISTRY.get(node_type)


# =============================================================================
# FILTER BUILDERS - One per trigger type
# =============================================================================


def build_webhook_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for webhook requests.

    Filters by webhook path to ensure the event is for the correct trigger node.

    Args:
        params: Node parameters with 'path' field

    Returns:
        Filter function that checks if event path matches
    """
    webhook_path = params.get("path", "")

    def matches(data: Dict) -> bool:
        event_path = data.get("path", "")
        if webhook_path and event_path != webhook_path:
            return False
        return True

    return matches


def build_chat_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for chat messages from console input.

    Args:
        params: Node parameters with 'session_id' field (Pydantic
            schema-canonical name).

    Returns:
        Filter function that checks if event session_id matches
    """
    session_id = params.get("session_id", "default")

    def matches(data: Dict) -> bool:
        event_session = data.get("session_id", "default")
        if session_id != "default" and event_session != session_id:
            return False
        return True

    return matches


def build_task_completed_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for task completed events.

    Filters by:
    - task_id: Specific task ID to watch (optional)
    - agent_name: Filter by child agent name (optional)
    - status_filter: 'all', 'completed', 'error' (default: 'all')
    - parent_node_id: Filter by parent node (optional, for scoping)

    Args:
        params: Node parameters

    Returns:
        Filter function that checks if event matches criteria
    """
    task_id_filter = params.get("task_id", "")
    agent_name_filter = params.get("agent_name", "")
    status_filter = params.get("status_filter", "all")  # all, completed, error
    parent_node_id = params.get("parent_node_id", "")

    def matches(data: Dict) -> bool:
        # Task ID filter (exact match if specified)
        if task_id_filter:
            if data.get("task_id") != task_id_filter:
                return False

        # Agent name filter (contains match)
        if agent_name_filter:
            event_agent = data.get("agent_name", "")
            if agent_name_filter.lower() not in event_agent.lower():
                return False

        # Status filter
        event_status = data.get("status", "")
        if status_filter == "completed" and event_status != "completed":
            return False
        if status_filter == "error" and event_status != "error":
            return False

        # Parent node filter (for scoping to specific parent agent)
        if parent_node_id:
            if data.get("parent_node_id") != parent_node_id:
                return False

        return True

    return matches


# Registry of filter builders per trigger type. Plugin packages
# (nodes/<group>/) call :func:`register_filter_builder` from their
# package ``__init__.py`` to publish per-trigger filters without this
# module needing to import them. The hardcoded entries below are
# **framework-level** triggers (webhook + chat + delegated-task) --
# they don't belong to any one plugin domain and intentionally stay
# here. The plugin entries (whatsappReceive, twitterReceive,
# googleGmailReceive, emailReceive) live in their plugin folders'
# ``_filters.py`` and self-register at import time.
FILTER_BUILDERS: Dict[str, Callable[[Dict], Callable[[Dict], bool]]] = {
    "webhookTrigger": build_webhook_filter,
    "chatTrigger": build_chat_filter,
    "taskTrigger": build_task_completed_filter,
}

from services.plugin.registry import IdempotentRegistry as _IdempotentRegistry  # noqa: E402

# Backed by the module-level FILTER_BUILDERS dict so existing readers
# (e.g. build_filter, _ensure_populated, tests) keep working.
_FILTER_REGISTRY: _IdempotentRegistry[str, Callable[[Dict], Callable[[Dict], bool]]] = _IdempotentRegistry(
    "filter_builder", items=FILTER_BUILDERS
)


def register_filter_builder(
    node_type: str,
    builder: Callable[[Dict], Callable[[Dict], bool]],
) -> None:
    """Publish a filter builder for a trigger node type.

    Idempotent on re-import (same callable for same key is a no-op).
    Used by plugin packages to keep all per-node-type knowledge inside
    the plugin folder instead of hardcoding it here.
    """
    _FILTER_REGISTRY.register(node_type, builder)


def build_filter(node_type: str, params: Dict) -> Callable[[Dict], bool]:
    """Build a filter function for the given trigger type and parameters.

    Plugin-derived builders (Wave 11.D.11) are lazily populated on
    first access; hardcoded entries in this module always win so
    plugin upgrades never silently replace hand-maintained behaviour.
    """
    _ensure_populated()
    builder = FILTER_BUILDERS.get(node_type)
    if builder:
        return builder(params)
    # Default: accept all events
    return lambda x: True


# =============================================================================
# TRIGGER PRE-CHECKS (plugin-registered)
# =============================================================================
#
# Some trigger nodes need to short-circuit with a friendly error before
# entering the wait loop -- e.g. "Telegram bot not connected, add token
# in Credentials". The pre-check used to be a hardcoded ``if node_type
# == 'telegramReceive'`` branch in handlers/triggers.py; it now lives in
# the plugin folder and registers itself here.
#
# Signature: ``async def precheck(parameters: Dict) -> Optional[str]``
#   - return None  -> proceed normally
#   - return str   -> short-circuit with that error message

import inspect as _inspect

_TriggerPrecheck = Callable[[Dict[str, Any]], Any]
_TRIGGER_PRECHECKS: Dict[str, _TriggerPrecheck] = {}
_TRIGGER_PRECHECK_REGISTRY: _IdempotentRegistry[str, _TriggerPrecheck] = _IdempotentRegistry("trigger_precheck", items=_TRIGGER_PRECHECKS)


def register_trigger_precheck(node_type: str, fn: _TriggerPrecheck) -> None:
    """Register a pre-execution check for a trigger node type.

    Idempotent on re-import. The callback may be sync or async; ``run_trigger_precheck``
    awaits the coroutine when needed.
    """
    _TRIGGER_PRECHECK_REGISTRY.register(node_type, fn)


async def run_trigger_precheck(node_type: str, parameters: Dict) -> Any:
    """Run the registered precheck for ``node_type`` (if any).

    Returns the precheck's return value (typically ``None`` or an
    error-message string).
    """
    fn = _TRIGGER_PRECHECKS.get(node_type)
    if fn is None:
        return None
    result = fn(parameters)
    if _inspect.isawaitable(result):
        result = await result
    return result


# =============================================================================
# WAITER DATA STRUCTURES
# =============================================================================


@dataclass
class Waiter:
    """Single event waiter.

    In memory mode: uses asyncio.Future
    In Redis mode: uses stream polling with stored metadata
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    node_id: str = ""
    node_type: str = ""
    event_type: str = ""
    params: Dict = field(default_factory=dict)  # Store params for Redis mode filter rebuild
    filter_fn: Callable[[Dict], bool] = field(default_factory=lambda: lambda x: True)
    future: Optional[asyncio.Future] = None  # Only used in memory mode
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)


# Module-level waiter storage (used in both modes for tracking)
_waiters: Dict[str, Waiter] = {}

# Redis stream names
EVENTS_STREAM_PREFIX = "events:"
WAITERS_KEY_PREFIX = "waiters:"
# NOTE: Each waiter uses its own consumer group to ensure ALL waiters receive ALL messages.
# Redis consumer groups deliver each message to only ONE consumer in the group.
# For trigger nodes, we want broadcast semantics where every waiter evaluates every event.
CONSUMER_GROUP_PREFIX = "waiter_group_"  # Each waiter gets: waiter_group_{waiter_id}


def _get_stream_name(event_type: str) -> str:
    """Get Redis stream name for event type."""
    return f"{EVENTS_STREAM_PREFIX}{event_type}"


# =============================================================================
# WAITER REGISTRATION
# =============================================================================


async def register(node_type: str, node_id: str, params: Dict) -> Waiter:
    """Register a waiter for a trigger node.

    Args:
        node_type: Type of trigger node (e.g., 'whatsappReceive')
        node_id: ID of the node waiting
        params: Node parameters for building filter

    Returns:
        Waiter object to await
    """
    config = get_trigger_config(node_type)
    if not config:
        raise ValueError(f"Unknown trigger type: {node_type}")

    # Create waiter
    waiter = Waiter(
        node_id=node_id,
        node_type=node_type,
        event_type=config.event_type,
        params=params,
        filter_fn=build_filter(node_type, params),
    )

    if is_redis_mode():
        # Redis mode: store waiter metadata in Redis
        cache = get_cache_service()
        waiter_key = f"{WAITERS_KEY_PREFIX}{waiter.id}"

        # Each waiter gets its own consumer group for broadcast semantics
        # This ensures ALL waiters receive ALL messages (not load-balanced)
        consumer_group = f"{CONSUMER_GROUP_PREFIX}{waiter.id}"

        waiter_data = {
            "id": waiter.id,
            "node_id": node_id,
            "node_type": node_type,
            "event_type": config.event_type,
            "params": json.dumps(params),
            "created_at": waiter.created_at,
            "consumer_group": consumer_group,  # Store for cleanup
        }
        await cache.set(waiter_key, waiter_data, ttl=86400)  # 24 hour TTL

        # Create unique consumer group for this waiter
        # start_id='$' means only new messages from this point forward
        stream_name = _get_stream_name(config.event_type)
        await cache.stream_create_group(stream_name, consumer_group, start_id="$")

        logger.debug(f"[EventWaiter] Registered {node_type} waiter {waiter.id} (Redis)")
    else:
        # Memory mode: create asyncio.Future
        try:
            loop = asyncio.get_running_loop()
            waiter.future = loop.create_future()
        except RuntimeError:
            waiter.future = asyncio.get_event_loop().create_future()

        logger.debug(f"[EventWaiter] Registered {node_type} waiter {waiter.id}")

    _waiters[waiter.id] = waiter
    return waiter


async def wait_for_event(waiter: Waiter, timeout: Optional[float] = None) -> Dict:
    """Wait for an event matching the waiter's filter.

    Args:
        waiter: The registered waiter
        timeout: Optional timeout in seconds (None = wait forever)

    Returns:
        Event data when matched

    Raises:
        asyncio.CancelledError: If waiter was cancelled
        asyncio.TimeoutError: If timeout exceeded
    """
    if is_redis_mode():
        return await _wait_redis(waiter, timeout)
    else:
        return await _wait_memory(waiter, timeout)


async def _wait_memory(waiter: Waiter, timeout: Optional[float]) -> Dict:
    """Wait using asyncio.Future (memory mode)."""
    if waiter.future is None:
        raise RuntimeError("Waiter has no Future (memory mode not initialized)")

    try:
        if timeout:
            return await asyncio.wait_for(waiter.future, timeout)
        else:
            return await waiter.future
    except asyncio.CancelledError:
        _cleanup_waiter(waiter.id)
        raise


async def _wait_redis(waiter: Waiter, timeout: Optional[float]) -> Dict:
    """Wait using Redis Streams polling.

    Polls the event stream with blocking XREAD, checking each message against the filter.
    Each waiter has its own consumer group for broadcast semantics.
    """
    cache = get_cache_service()
    stream_name = _get_stream_name(waiter.event_type)

    # Use waiter-specific consumer group for broadcast (all waiters see all messages)
    consumer_group = f"{CONSUMER_GROUP_PREFIX}{waiter.id}"
    consumer_name = f"consumer_{waiter.id}"

    # Start reading from now (new messages only)
    block_ms = 5000  # 5 second blocks to allow cancellation checks

    start_time = time.time()

    while not waiter.cancelled:
        # Check timeout
        if timeout and (time.time() - start_time) > timeout:
            raise asyncio.TimeoutError(f"Waiter {waiter.id} timed out after {timeout}s")

        # Read from stream with blocking using waiter's own consumer group
        try:
            result = await cache.stream_read_group(
                consumer_group,  # Each waiter has its own group
                consumer_name,
                {stream_name: ">"},  # '>' = new messages for this consumer
                count=10,
                block=block_ms,
            )

            if not result:
                # No messages, continue polling
                continue

            # Process messages
            for stream_data in result:
                stream, messages = stream_data
                for msg_id, fields in messages:
                    # Deserialize event data
                    event_data = {}
                    for k, v in fields.items():
                        try:
                            event_data[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            event_data[k] = v

                    # Check filter
                    if waiter.filter_fn(event_data):
                        # Match found - acknowledge and return
                        await cache.stream_ack(stream_name, consumer_group, msg_id)
                        _cleanup_waiter(waiter.id)
                        logger.info(f"[EventWaiter] Waiter {waiter.id} matched event {msg_id}")
                        return event_data
                    else:
                        # No match - acknowledge but continue waiting
                        await cache.stream_ack(stream_name, consumer_group, msg_id)

        except asyncio.CancelledError:
            _cleanup_waiter(waiter.id)
            raise

    # Waiter was cancelled via cancel() flag
    _cleanup_waiter(waiter.id)
    raise asyncio.CancelledError(f"Waiter {waiter.id} cancelled")


def _cleanup_waiter(waiter_id: str) -> None:
    """Remove waiter from storage."""
    _waiters.pop(waiter_id, None)

    # Also remove from Redis if in Redis mode
    if is_redis_mode():
        cache = get_cache_service()
        waiter_key = f"{WAITERS_KEY_PREFIX}{waiter_id}"
        asyncio.create_task(cache.delete(waiter_key))


# =============================================================================
# EVENT DISPATCH
# =============================================================================


def _unpack_event(
    event: "Any",
    data: Optional[Dict] = None,
) -> tuple[str, Dict]:
    """Normalise either ``(WorkflowEvent,)`` or ``(event_type, data)`` to
    the underlying ``(event_type, data)`` pair the dispatcher uses.

    Wave 11.I, milestone Q: ``dispatch`` and ``dispatch_async`` accept a
    ``WorkflowEvent`` directly so the ``WorkflowEvent(**event)`` rewrap
    in ``events/triggers.py`` becomes a no-op. The legacy
    ``(event_type, data)`` shape stays supported via
    :meth:`WorkflowEvent.from_legacy` upstream of the dispatcher (the
    public API just forwards either form).
    """
    # Lazy import: services.events imports event_waiter for trigger
    # adaptation, so a top-level import would be circular.
    from services.events.envelope import WorkflowEvent

    if isinstance(event, WorkflowEvent):
        return event.type, event.data if isinstance(event.data, dict) else {"data": event.data}
    if isinstance(event, str):
        return event, data or {}
    raise TypeError(f"dispatch expects a WorkflowEvent or (event_type: str, data: Dict); " f"got {type(event).__name__}")


async def dispatch_async(
    event: "Any",
    data: Optional[Dict] = None,
) -> int:
    """Dispatch event asynchronously (for Redis mode).

    Args:
        event: Either a :class:`WorkflowEvent` (preferred, Wave 11.I) or
            an event-type string. The legacy ``(event_type, data)`` form
            stays supported.
        data: Event payload (when ``event`` is a string).

    Returns:
        1 if event was added to stream, 0 otherwise
    """
    event_type, payload = _unpack_event(event, data)
    logger.debug(f"[EventWaiter] dispatch_async: event_type='{event_type}'")

    if is_redis_mode():
        cache = get_cache_service()
        stream_name = _get_stream_name(event_type)
        msg_id = await cache.stream_add(stream_name, payload)
        if msg_id:
            logger.debug(f"[EventWaiter] Added event to stream {stream_name}: {msg_id}")
            return 1
        return 0
    else:
        # Fall back to sync dispatch for memory mode
        return dispatch(event_type, payload)


def dispatch(
    event: "Any",
    data: Optional[Dict] = None,
) -> int:
    """Dispatch event to matching waiters (synchronous, memory mode).

    Thread-safe: Can be called from APScheduler threads or async context.

    Args:
        event: Either a :class:`WorkflowEvent` (preferred, Wave 11.I) or
            an event-type string. The legacy ``(event_type, data)`` form
            stays supported.
        data: Event payload (when ``event`` is a string).

    Returns:
        Number of waiters resolved
    """
    event_type, data = _unpack_event(event, data)
    if is_redis_mode():
        # In Redis mode, use async dispatch
        # Handle both async context and thread context (e.g., APScheduler callbacks)
        try:
            # Try to get the current running loop
            asyncio.get_running_loop()
            # We're in an async context - schedule task normally
            asyncio.create_task(dispatch_async(event_type, data))
        except RuntimeError:
            # No running loop - we're in a thread (e.g., APScheduler callback)
            # Use the stored main loop with thread-safe dispatch
            if _main_loop is not None and _main_loop.is_running():
                asyncio.run_coroutine_threadsafe(dispatch_async(event_type, data), _main_loop)
            else:
                logger.warning(f"[EventWaiter] No event loop available for dispatch of {event_type}")
        return 0  # Actual resolution happens in _wait_redis

    resolved = 0
    to_remove = []

    matching_waiters = [(wid, w) for wid, w in _waiters.items() if w.event_type == event_type and w.future and not w.future.done()]

    if not matching_waiters:
        logger.debug(f"[EventWaiter] No active waiters for {event_type} (total waiters: {len(_waiters)})")
    else:
        logger.info(
            f"[EventWaiter] Dispatching {event_type} to {len(matching_waiters)} waiter(s)",
            event_type=event_type,
            from_id=data.get("from_id"),
            text=str(data.get("text", ""))[:50],
        )

    for wid, w in matching_waiters:
        try:
            if w.filter_fn(data):
                w.future.set_result(data)
                to_remove.append(wid)
                resolved += 1
                logger.info(f"[EventWaiter] Resolved {w.node_type} waiter {wid}")
            else:
                logger.debug(f"[EventWaiter] Filter rejected for {w.node_type} waiter {wid}")
        except Exception as e:
            logger.error(f"[EventWaiter] Filter error for waiter {wid}: {e}")

    for wid in to_remove:
        _waiters.pop(wid, None)

    return resolved


# =============================================================================
# WAITER CANCELLATION
# =============================================================================


def cancel(waiter_id: str) -> bool:
    """Cancel a waiter by ID."""
    if w := _waiters.pop(waiter_id, None):
        w.cancelled = True

        if w.future and not w.future.done():
            w.future.cancel()

        # Also remove from Redis if in Redis mode
        if is_redis_mode():
            cache = get_cache_service()
            waiter_key = f"{WAITERS_KEY_PREFIX}{waiter_id}"
            asyncio.create_task(cache.delete(waiter_key))

        logger.debug(f"[EventWaiter] Cancelled waiter {waiter_id}")
        return True

    return False


def cancel_for_node(node_id: str) -> int:
    """Cancel all waiters for a node."""
    to_cancel = [wid for wid, w in _waiters.items() if w.node_id == node_id]
    for wid in to_cancel:
        cancel(wid)
    return len(to_cancel)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def get_active_waiters() -> List[Dict[str, Any]]:
    """Get info about active waiters (for debugging/UI)."""
    return [
        {
            "id": w.id,
            "node_id": w.node_id,
            "node_type": w.node_type,
            "event_type": w.event_type,
            "done": w.future.done() if w.future else False,
            "cancelled": w.cancelled,
            "age_seconds": time.time() - w.created_at,
            "mode": "redis" if is_redis_mode() else "memory",
        }
        for w in _waiters.values()
    ]


def clear_all() -> int:
    """Clear all waiters (for testing/cleanup)."""
    count = len(_waiters)
    for w in _waiters.values():
        w.cancelled = True
        if w.future and not w.future.done():
            w.future.cancel()
    _waiters.clear()

    # Clear Redis waiter keys if in Redis mode
    if is_redis_mode():
        cache = get_cache_service()
        asyncio.create_task(cache.clear_pattern(f"{WAITERS_KEY_PREFIX}*"))

    return count


def get_backend_mode() -> str:
    """Get current backend mode for debugging."""
    return "redis" if is_redis_mode() else "memory"
