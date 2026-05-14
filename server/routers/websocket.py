"""WebSocket router for real-time bidirectional communication.

Handles WebSocket connections from frontend clients for ALL operations:
- Node parameters (get, save, delete)
- Node execution
- AI execution and model fetching
- API key validation and storage
- Android device operations
- Google Maps key validation
- Status broadcasts
"""

import time
import asyncio
import uuid
import weakref
from typing import Dict, Any, Callable, Awaitable, List, Optional, Set
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.status_broadcaster import get_status_broadcaster
from core.container import container
from core.logging import get_logger


def get_auth_service():
    """Get auth service from DI container."""
    return container.auth_service()

logger = get_logger(__name__)

router = APIRouter(tags=["websocket"])

# =============================================================================
# Concurrent Send Protection
# =============================================================================
# Use WeakKeyDictionary to auto-cleanup when WebSocket is garbage collected
_send_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

# Track running handler tasks per WebSocket for cleanup on disconnect
_handler_tasks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def _safe_send(websocket: WebSocket, data: dict):
    """Thread-safe WebSocket send with lock to prevent concurrent writes."""
    # Guard against sending on closed/disconnected WebSocket
    if websocket.client_state.name != "CONNECTED":
        return
    if websocket not in _send_locks:
        _send_locks[websocket] = asyncio.Lock()
    async with _send_locks[websocket]:
        try:
            await websocket.send_json(data)
        except Exception as e:
            logger.debug("[WebSocket] Send skipped (connection closed): %s", e)


# Type for message handlers
MessageHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]


def ws_handler(*required_fields: str):
    """Simple decorator for WebSocket handlers. Validates required fields and wraps errors."""
    import functools

    def decorator(func: MessageHandler) -> MessageHandler:
        @functools.wraps(func)
        async def wrapper(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
            for field in required_fields:
                if not data.get(field):
                    return {"success": False, "error": f"{field} required"}
            try:
                result = await func(data, websocket)
                if "success" not in result:
                    result = {"success": True, **result}
                return result
            except Exception as e:
                logger.error(f"Handler error: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        return wrapper
    return decorator


# ============================================================================
# Message Handlers
# ============================================================================

async def handle_ping(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Handle ping request."""
    return {"type": "pong", "timestamp": time.time()}


async def handle_get_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current full status."""
    broadcaster = get_status_broadcaster()
    return {"type": "full_status", "data": broadcaster.get_status()}


async def handle_get_android_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get Android connection status."""
    broadcaster = get_status_broadcaster()
    return {"type": "android_status", "data": broadcaster.get_android_status()}


async def handle_get_node_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get specific node status."""
    broadcaster = get_status_broadcaster()
    node_id = data.get("node_id")
    if node_id:
        status = broadcaster.get_node_status(node_id)
        return {"type": "node_status", "node_id": node_id, "data": status}
    return {"type": "error", "message": "node_id required"}


async def handle_get_variable(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get variable value."""
    broadcaster = get_status_broadcaster()
    name = data.get("name")
    if name:
        value = broadcaster.get_variable(name)
        return {"type": "variable_update", "name": name, "value": value}
    return {"type": "error", "message": "name required"}


# ============================================================================
# Node Parameters Handlers
# ============================================================================

@ws_handler("node_id")
async def handle_get_node_parameters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get parameters for a specific node."""
    database = container.database()
    node_id = data["node_id"]
    parameters = await database.get_node_parameters(node_id)
    logger.debug(f"[GET_PARAMS] Node ID: {node_id}")
    logger.debug(f"[GET_PARAMS] Raw from DB: {parameters}")
    logger.debug(f"[GET_PARAMS] Code length: {len(parameters.get('code', '')) if parameters and 'code' in parameters else 'no code field'}")
    return {"node_id": node_id, "parameters": parameters or {}, "version": 1, "timestamp": time.time()}


@ws_handler()
async def handle_get_all_node_parameters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get parameters for multiple nodes."""
    database = container.database()
    result = {}
    for node_id in data.get("node_ids", []):
        parameters = await database.get_node_parameters(node_id)
        if parameters:
            result[node_id] = {"parameters": parameters, "version": 1}
    return {"parameters": result, "timestamp": time.time()}


@ws_handler("node_id")
async def handle_save_node_parameters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save node parameters and broadcast to all clients."""
    database = container.database()
    broadcaster = get_status_broadcaster()
    node_id, parameters = data["node_id"], data.get("parameters", {})

    logger.debug(f"[SAVE_PARAMS] Node ID: {node_id}, has_code: {'code' in parameters}, code_len: {len(parameters.get('code', '')) if 'code' in parameters else 0}")
    await database.save_node_parameters(node_id, parameters)
    # CloudEvents v1.0 envelope (RFC §6.4) — type is
    # ``com.machinaos.node.parameters.updated``; ``source_hint="user"``
    # because this handler fires from the parameter-panel save flow.
    await broadcaster.broadcast_node_parameters_updated(
        node_id, parameters=parameters, source_hint="user",
    )
    return {"node_id": node_id, "parameters": parameters, "version": 1, "timestamp": time.time()}


@ws_handler("node_id")
async def handle_delete_node_parameters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete node parameters."""
    database = container.database()
    await database.delete_node_parameters(data["node_id"])
    return {"node_id": data["node_id"]}


# ============================================================================
# Tool Schema Handlers (Source of truth for tool node configurations)
# ============================================================================

@ws_handler("node_id")
async def handle_get_tool_schema(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get tool schema for a node."""
    database = container.database()
    schema = await database.get_tool_schema(data["node_id"])
    return {"node_id": data["node_id"], "schema": schema}


@ws_handler("node_id", "tool_name", "tool_description", "schema_config")
async def handle_save_tool_schema(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save tool schema for a node. Used by Android Toolkit to update connected service schemas."""
    database = container.database()
    broadcaster = get_status_broadcaster()

    node_id = data["node_id"]
    tool_name = data["tool_name"]
    tool_description = data["tool_description"]
    schema_config = data["schema_config"]
    connected_services = data.get("connected_services")

    success = await database.save_tool_schema(
        node_id=node_id,
        tool_name=tool_name,
        tool_description=tool_description,
        schema_config=schema_config,
        connected_services=connected_services
    )

    if success:
        # Broadcast schema update to all clients
        await broadcaster.broadcast({
            "type": "tool_schema_updated",
            "node_id": node_id,
            "tool_name": tool_name,
            "timestamp": time.time()
        })

    return {
        "node_id": node_id,
        "tool_name": tool_name,
        "saved": success
    }


@ws_handler("node_id")
async def handle_delete_tool_schema(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete tool schema for a node."""
    database = container.database()
    await database.delete_tool_schema(data["node_id"])
    return {"node_id": data["node_id"]}


async def handle_get_all_tool_schemas(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all tool schemas."""
    database = container.database()
    schemas = await database.get_all_tool_schemas()
    return {"success": True, "schemas": schemas}


# ============================================================================
# Node Output Schemas (n8n-style: backend as single source of truth for
# the drag-drop variable panel's "before first run" fallback shape).
# See docs-internal/schema_source_of_truth_rfc.md.
# ============================================================================


@ws_handler("node_type")
async def handle_get_node_output_schema(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Return the JSON Schema for a node type's runtime output, or
    ``{schema: null}`` when no schema is declared. Frontend caches the
    result per node type in-memory (mirrors n8n's schemaPreview.store)."""
    from services.node_output_schemas import get_node_output_schema

    schema = get_node_output_schema(data["node_type"])
    return {"node_type": data["node_type"], "schema": schema}


@ws_handler("node_type")
async def handle_get_node_spec(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Return the unified NodeSpec (input schema + output schema +
    display metadata) for a node type, or ``{spec: null}`` when the
    type is unknown. Wave 6 Phase 2 WS mirror of the REST endpoint
    GET /api/schemas/nodes/{type}/spec.json."""
    from services.node_spec import get_node_spec

    spec = get_node_spec(data["node_type"])
    return {"node_type": data["node_type"], "spec": spec}


@ws_handler()
async def handle_list_node_specs(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Return the sorted list of node types that have a NodeSpec, plus
    a content-hash revision the editor uses to invalidate its persisted
    spec cache when the backend catalogue changes between deploys."""
    from services.node_spec import list_node_types_with_spec, node_spec_revision

    return {
        "node_types": list_node_types_with_spec(),
        "revision": node_spec_revision(),
    }


@ws_handler("method")
async def handle_load_options(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Wave 6 Phase 4: unified loadOptionsMethod dispatcher.

    Replaces the per-method WS handlers (whatsapp_groups, whatsapp_newsletters,
    etc.) with a single registry-driven endpoint. ``loadOptionsMethod`` strings
    declared via Pydantic ``Field(json_schema_extra={"loadOptionsMethod": "..."})``
    on the backend NodeSpec are resolved here.

    Body: ``{"method": "...", "params": {...}}``
    Response: ``{"options": [{"value": ..., "label": ...}]}``
    """
    from services.ws_handler_registry import dispatch_load_options

    options = await dispatch_load_options(data["method"], data.get("params", {}))
    return {"method": data["method"], "options": options}


@ws_handler()
async def handle_list_load_options_methods(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Return registered loadOptionsMethod names. Editor uses this to
    know which dynamic-option loaders are wired."""
    from services.ws_handler_registry import list_load_options_methods

    return {"methods": list_load_options_methods()}


@ws_handler()
async def handle_get_node_groups(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Wave 6 Phase 5: {group_name: [node_type, ...]} index derived from
    every NodeSpec's ``group`` array. Replaces the 34 hand-rolled
    ``*_NODE_TYPES`` arrays scattered across the frontend."""
    from services.node_spec import list_node_groups

    return {"groups": list_node_groups()}


# ============================================================================
# Credential Registry Handler (Nango-style bulk fetch for 20 -> 5000 providers)
# ============================================================================

@ws_handler()
async def handle_get_credential_catalogue(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Return the full credential provider catalogue with live stored-key status.

    Response shape: {providers, categories, version}. Each provider includes
    a `stored: boolean` field indicating whether a key/token is present in the
    encrypted credentials database. The frontend renders this directly — no
    client-side credential checks needed.
    """
    from services.credential_registry import get_credential_registry

    registry = get_credential_registry()
    since = data.get("since")
    version = registry.get_version()
    if since and since == version:
        return {"unchanged": True, "version": version}

    catalogue = registry.get_catalogue()

    # Enrich each provider with live stored-key status from AuthService.
    # This keeps credential state as a backend concern — the frontend is
    # purely a renderer with zero business logic about key existence.
    auth_service = container.auth_service()
    for provider in catalogue.get("providers", []):
        pid = provider.get("id", "")
        kind = provider.get("kind", "")
        status_hook = provider.get("status_hook")

        tokens = None
        # Declarative per-provider override for the "stored" check.
        # Lets Telegram (kind=oauth + status_hook but actual storage
        # is api_key for the bot token) signal that "stored" should
        # be ``has_valid_key("telegram")`` rather than the default
        # ``get_oauth_tokens(status_hook)`` lookup. Other providers
        # don't declare ``stored_check`` and keep the original
        # kind/status_hook-based logic untouched -- so Google's saved
        # client_secret (password field) does NOT flip the connected
        # dot before the user actually completes the OAuth flow.
        stored_check = provider.get("stored_check")
        if stored_check and stored_check.get("type") == "api_key":
            provider["stored"] = await auth_service.has_valid_key(stored_check.get("key", pid))
        elif status_hook:
            # Status-hook providers (whatsapp, android, twitter, google,
            # claude_code, codex_cli) use OAuth tokens for the runtime
            # connection state.
            tokens = await auth_service.get_oauth_tokens(status_hook)
            provider["stored"] = tokens is not None
        elif kind == "apiKey":
            # API key providers — check encrypted credentials DB.
            provider["stored"] = await auth_service.has_valid_key(pid)
        elif kind == "oauth":
            # OAuth providers without a status_hook — check token storage.
            tokens = await auth_service.get_oauth_tokens(pid)
            provider["stored"] = tokens is not None
        else:
            provider["stored"] = False

        # Surface the connected account identifier (email > display name)
        # so the modal can render "Connected as foo@bar.com" without a
        # per-provider status hook. Twitter / Google / Stripe / Claude
        # all populate `email` / `name` via `auth_service.store_oauth_tokens`.
        if tokens:
            provider["account_label"] = tokens.get("email") or tokens.get("name")
        else:
            provider["account_label"] = None

    return catalogue


# ============================================================================
# Node Execution Handlers
# ============================================================================

@ws_handler("node_id", "node_type")
async def handle_execute_node(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Execute a workflow node with per-workflow status scoping (n8n pattern).

    Generates an `execution_id` correlation token (uuid4 hex) and propagates
    it through every broadcast for this run plus the response payload.
    Frontend uses it for idempotent dedup -- two executions with identical
    output payloads were previously collapsing because the dedup key was
    JSON.stringify(outputs). This is the standard request-id / trace-id
    pattern (OpenTelemetry, AWS X-Ray, etc.).
    """
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()
    node_id, node_type = data["node_id"], data["node_type"]
    workflow_id = data.get("workflow_id")  # Per-workflow isolation
    execution_id = uuid.uuid4().hex

    await broadcaster.update_node_status(
        node_id, "executing", {"execution_id": execution_id}, workflow_id=workflow_id,
    )
    # Mark this workflow active so the toolbar Start->Stop reflects ad-hoc runs.
    # finally: ensures the counter rolls back even on crash.
    await broadcaster.workflow_run_started(workflow_id)
    result: Dict[str, Any]
    try:
        result = await workflow_service.execute_node(
            node_id=node_id, node_type=node_type,
            parameters=data.get("parameters", {}),
            nodes=data.get("nodes", []), edges=data.get("edges", []),
            session_id=data.get("session_id", "default"),
            workflow_id=workflow_id,
            outputs=data.get("outputs", {}),  # Upstream node outputs for data flow
        )

        if result.get("success"):
            success_payload = {**(result.get("result") or {}), "execution_id": execution_id}
            await broadcaster.update_node_status(node_id, "success", success_payload, workflow_id=workflow_id)
            await broadcaster.update_node_output(node_id, success_payload, workflow_id=workflow_id)
        elif result.get("error") == "Cancelled by user":
            # Cancelled trigger nodes go back to idle, not error
            await broadcaster.update_node_status(
                node_id, "idle", {"message": "Cancelled", "execution_id": execution_id}, workflow_id=workflow_id,
            )
        else:
            await broadcaster.update_node_status(
                node_id, "error", {"error": result.get("error"), "execution_id": execution_id}, workflow_id=workflow_id,
            )
    except Exception:
        # Mark the node as errored so the UI doesn't keep glowing on crash
        await broadcaster.update_node_status(
            node_id, "error",
            {"error": "execution crashed", "execution_id": execution_id},
            workflow_id=workflow_id,
        )
        raise
    finally:
        await broadcaster.workflow_run_ended(workflow_id)

    # Explicitly pass through success status (don't let decorator default to True)
    ws_result = {
        "success": result.get("success", False),
        "node_id": node_id,
        "execution_id": execution_id,
        "result": result.get("result"),
        "error": result.get("error"),
        "execution_time": result.get("execution_time"),
        "timestamp": time.time()
    }
    # Debug: Log what we're returning to WebSocket
    result_data = result.get("result")
    logger.debug(f"[WS execute_node] Returning: success={ws_result['success']}, result.response={repr(result_data.get('response', 'MISSING')[:100] if result_data and result_data.get('response') else 'None')}")
    return ws_result


@ws_handler()
async def handle_cancel_execution(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Cancel ad-hoc node execution(s).

    Two modes:
      * `node_id` — reset that single node to idle (legacy single-node cancel).
      * `workflow_id` only — reset every node in this workflow that's
        currently `executing` or `waiting`, and reset the active-run counter.
        Used by the toolbar Stop button when no deployment is active.

    Either argument is sufficient; if both are present the node-specific
    reset still happens and the workflow-level cleanup runs as well.
    """
    broadcaster = get_status_broadcaster()
    workflow_id = data.get("workflow_id")
    node_id = data.get("node_id")

    if node_id:
        await broadcaster.update_node_status(node_id, "idle", workflow_id=workflow_id)

    cleared = 0
    if workflow_id:
        # Reset run counter to zero (broadcasts executing=false + clears stuck nodes)
        async with broadcaster._workflow_active_lock:
            broadcaster._workflow_active_runs.pop(workflow_id, None)
        await broadcaster.update_workflow_status(executing=False, workflow_id=workflow_id)
        # Explicit user cancel -- include `waiting` so the user sees every
        # indicator go quiet. (Default sweep on run-end skips `waiting` to
        # protect deployment trigger listeners.)
        cleared = await broadcaster._clear_stuck_node_statuses(
            workflow_id, include_waiting=True,
        )

    return {
        "node_id": node_id,
        "workflow_id": workflow_id,
        "cleared_nodes": cleared,
        "message": "Execution cancelled",
    }


@ws_handler()
async def handle_get_workflow_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Return cached per-workflow execution status for resync (reconnect / workflow switch)."""
    broadcaster = get_status_broadcaster()
    workflow_id = data.get("workflow_id")
    if not workflow_id:
        return {"success": False, "error": "workflow_id required"}
    return {"workflow_id": workflow_id, "data": broadcaster.get_workflow_status(workflow_id)}


@ws_handler()
async def handle_cancel_event_wait(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Cancel an active event waiter (for trigger nodes).

    Can cancel by waiter_id or node_id.
    Note: Status update to "idle" happens in handle_execute_node when it catches CancelledError.
    """
    from services import event_waiter

    waiter_id = data.get("waiter_id")
    node_id = data.get("node_id")

    logger.debug(f"[WebSocket] handle_cancel_event_wait called: waiter_id={waiter_id}, node_id={node_id}")

    if waiter_id:
        success = event_waiter.cancel(waiter_id)
        logger.debug(f"[WebSocket] cancel by waiter_id result: success={success}")
        return {"success": success, "waiter_id": waiter_id, "message": "Cancelled" if success else "Not found"}
    elif node_id:
        count = event_waiter.cancel_for_node(node_id)
        logger.debug(f"[WebSocket] cancel by node_id result: cancelled_count={count}")
        return {"success": count > 0, "node_id": node_id, "cancelled_count": count}
    else:
        return {"success": False, "error": "waiter_id or node_id required"}


@ws_handler()
async def handle_get_active_waiters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get list of active event waiters (for debugging/UI)."""
    from services import event_waiter
    return {"waiters": event_waiter.get_active_waiters()}


# ============================================================================
# Dead Letter Queue (DLQ) Handlers
# ============================================================================

@ws_handler()
async def handle_get_dlq_entries(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get DLQ entries with optional filtering.

    Optional params:
        workflow_id: Filter by workflow ID
        node_type: Filter by node type
        limit: Max entries to return (default 100)

    Returns:
        List of DLQ entries
    """
    from services.execution import ExecutionCache
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)

    entries = await execution_cache.get_dlq_entries(
        workflow_id=data.get("workflow_id"),
        node_type=data.get("node_type"),
        limit=data.get("limit", 100)
    )

    return {
        "entries": [entry.to_dict() for entry in entries],
        "count": len(entries),
        "timestamp": time.time()
    }


@ws_handler("entry_id")
async def handle_get_dlq_entry(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get a single DLQ entry by ID.

    Required:
        entry_id: DLQ entry ID

    Returns:
        DLQ entry details
    """
    from services.execution import ExecutionCache
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)

    entry = await execution_cache.get_dlq_entry(data["entry_id"])

    if entry:
        return {"entry": entry.to_dict(), "timestamp": time.time()}
    else:
        return {"success": False, "error": "DLQ entry not found"}


@ws_handler()
async def handle_get_dlq_stats(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get DLQ statistics.

    Returns:
        Total count, breakdown by node type and workflow
    """
    from services.execution import ExecutionCache
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)

    stats = await execution_cache.get_dlq_stats()
    return {"stats": stats, "timestamp": time.time()}


@ws_handler("entry_id")
async def handle_replay_dlq_entry(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Replay a failed node from the DLQ.

    Required:
        entry_id: DLQ entry ID to replay
        nodes: Workflow nodes
        edges: Workflow edges

    Returns:
        Replay execution result
    """
    from services.execution import ExecutionCache, WorkflowExecutor
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    entry_id = data["entry_id"]
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Get the entry to find the node_id
    entry = await execution_cache.get_dlq_entry(entry_id)
    if not entry:
        return {"success": False, "error": "DLQ entry not found"}

    # Update status
    await broadcaster.update_node_status(entry.node_id, "executing", {
        "message": "Replaying from DLQ"
    })

    # Create executor with node adapter
    async def node_executor(node_id: str, node_type: str, params: dict, context: dict) -> dict:
        return await workflow_service.execute_node(
            node_id=node_id,
            node_type=node_type,
            parameters=params,
            nodes=context.get("nodes", []),
            edges=context.get("edges", []),
            session_id=context.get("session_id", "dlq_replay"),
            execution_id=context.get("execution_id")
        )

    async def status_callback(node_id: str, status: str, status_data: dict):
        await broadcaster.update_node_status(node_id, status, status_data)

    # DLQ replay needs DLQ enabled to re-add on failure
    settings = container.settings()
    executor = WorkflowExecutor(
        cache=execution_cache,
        node_executor=node_executor,
        status_callback=status_callback,
        dlq_enabled=settings.dlq_enabled
    )

    result = await executor.replay_dlq_entry(entry_id, nodes, edges)

    # Update final status
    if result.get("success"):
        await broadcaster.update_node_status(entry.node_id, "success", result.get("result"))
    else:
        await broadcaster.update_node_status(entry.node_id, "error", {"error": result.get("error")})

    return result


@ws_handler("entry_id")
async def handle_remove_dlq_entry(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Remove an entry from the DLQ without replaying.

    Required:
        entry_id: DLQ entry ID to remove

    Returns:
        Success status
    """
    from services.execution import ExecutionCache
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)

    success = await execution_cache.remove_from_dlq(data["entry_id"])
    return {"removed": success, "entry_id": data["entry_id"]}


@ws_handler()
async def handle_purge_dlq(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Purge entries from the DLQ.

    Optional params:
        workflow_id: Only purge entries for this workflow
        node_type: Only purge entries for this node type
        older_than_hours: Only purge entries older than X hours

    Returns:
        Number of entries purged
    """
    from services.execution import ExecutionCache
    cache_service = container.cache()
    execution_cache = ExecutionCache(cache_service)

    older_than = None
    if data.get("older_than_hours"):
        older_than = time.time() - (data["older_than_hours"] * 3600)

    purged = await execution_cache.purge_dlq(
        workflow_id=data.get("workflow_id"),
        node_type=data.get("node_type"),
        older_than=older_than
    )

    return {"purged": purged, "timestamp": time.time()}


@ws_handler("node_id")
async def handle_get_node_output(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get output data for a specific node."""
    workflow_service = container.workflow_service()
    node_id = data["node_id"]
    output_name = data.get("output_name", "output_0")
    output_data = await workflow_service.get_node_output(data.get("session_id", "default"), node_id, output_name)
    return {"node_id": node_id, "output_name": output_name, "data": output_data, "timestamp": time.time()}


@ws_handler("node_id")
async def handle_clear_node_output(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear output data for a specific node from memory, database, and broadcaster cache."""
    workflow_service = container.workflow_service()
    database = container.database()
    broadcaster = get_status_broadcaster()
    node_id = data["node_id"]

    # Clear from memory - find keys ending with _{node_id}
    memory_cleared = 0
    keys_to_delete = [key for key in workflow_service.node_outputs.keys() if key.endswith(f"_{node_id}")]
    for key in keys_to_delete:
        del workflow_service.node_outputs[key]
        memory_cleared += 1

    # Clear from database (persisted storage)
    db_cleared = await database.delete_node_output(node_id)

    # Clear from broadcaster's status cache (prevents reload from showing old data)
    broadcaster_cleared = await broadcaster.clear_node_status(node_id)

    logger.info("Cleared node output", node_id=node_id, memory_cleared=memory_cleared,
                db_cleared=db_cleared, broadcaster_cleared=broadcaster_cleared)

    return {"node_id": node_id, "cleared": True, "memory_cleared": memory_cleared,
            "db_cleared": db_cleared, "broadcaster_cleared": broadcaster_cleared}


@ws_handler()
async def handle_validate_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Run pre-execution validation on a workflow graph.

    Serves three call sites: editor live-lint (debounced), pre-execute gate
    (called internally from ``handle_execute_workflow``), and import dry-run
    (called by the frontend after ``importWorkflowFromFile``).

    Expects:
        nodes: List of workflow nodes with {id, type, data}
        edges: List of edges with {id, source, target}
        parameters_by_id: Optional map of node_id -> parameters for INVALID_PARAM
            check. Defaults to ``node.data.parameters`` (rarely populated).

    Returns:
        ``{"success": True, "report": {"errors": [...], "warnings": [...]}}``.
    """
    from services.workflow_validator import validate_workflow
    report = await validate_workflow(
        nodes=data.get("nodes", []),
        edges=data.get("edges", []),
        parameters_by_id=data.get("parameters_by_id"),
    )
    return {"report": report}


@ws_handler()
async def handle_execute_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Execute entire workflow from start node to end.

    Expects:
        workflow_id: Workflow identifier for per-workflow status scoping
        nodes: List of workflow nodes with {id, type, data}
        edges: List of edges with {id, source, target}
        session_id: Optional session identifier
        force: Optional bool — bypass validation errors. When False/missing,
            validation errors short-circuit execution and the response carries
            the report so the frontend can prompt the user.

    Returns:
        Workflow execution result with all node outputs
    """
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")  # Per-workflow isolation (n8n pattern)
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    session_id = data.get("session_id", "default")
    force = bool(data.get("force"))

    if not nodes:
        return {"success": False, "error": "No nodes provided"}

    # Pre-execute validation gate. force=True overrides errors (matches the
    # "Run anyway" UX in Windmill); warnings never block.
    if not force:
        from services.workflow_validator import validate_workflow
        report = await validate_workflow(
            nodes=nodes, edges=edges,
            parameters_by_id=data.get("parameters_by_id"),
        )
        if report["errors"]:
            return {
                "success": False,
                "error": "validation_failed",
                "report": report,
            }

    # Mark this workflow active so the toolbar Start->Stop reflects whole-workflow runs
    await broadcaster.workflow_run_started(workflow_id)

    # Create status callback with workflow_id for per-workflow scoping (n8n pattern)
    async def status_callback(node_id: str, status: str, node_data: Optional[Dict] = None):
        await broadcaster.update_node_status(node_id, status, node_data, workflow_id=workflow_id)
        if status == "executing":
            position = node_data.get("position", 0) if node_data else 0
            total = node_data.get("total", 1) if node_data else 1
            progress = int((position / total) * 100) if total > 0 else 0
            await broadcaster.update_workflow_status(
                executing=True,
                current_node=node_id,
                progress=progress,
                workflow_id=workflow_id,
            )

    result: Dict[str, Any]
    try:
        result = await workflow_service.execute_workflow(
            nodes=nodes,
            edges=edges,
            session_id=session_id,
            status_callback=status_callback,
            workflow_id=workflow_id,
        )
    finally:
        # Always release the active-run counter so the button never gets stuck
        await broadcaster.workflow_run_ended(workflow_id)

    return {
        "success": result.get("success", False),
        "nodes_executed": result.get("nodes_executed", []),
        "node_results": result.get("node_results", {}),
        "execution_order": result.get("execution_order", []),
        "errors": result.get("errors", []),
        "error": result.get("error"),
        "total_nodes": result.get("total_nodes", 0),
        "completed_nodes": result.get("completed_nodes", 0),
        "execution_time": result.get("execution_time", 0),
        "timestamp": time.time()
    }


# Per-workflow deployment tasks for proper cancellation (Temporal/n8n pattern)
# Maps workflow_id -> asyncio.Task for parallel workflow deployments
_deployment_tasks: Dict[str, asyncio.Task] = {}


@ws_handler()
async def handle_deploy_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Deploy workflow to run continuously until cancelled.

    Expects:
        workflow_id: Workflow identifier (required for locking)
        nodes: List of workflow nodes with {id, type, data}
        edges: List of edges with {id, source, target}
        session_id: Optional session identifier
        delay_between_runs: Optional delay in seconds between iterations (default: 1.0)

    Returns:
        Deployment start confirmation (deployment runs in background)
    """
    global _deployment_tasks
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    session_id = data.get("session_id", "default")

    # DEBUG: Log received edges to trace tool connection issues
    logger.debug(f"[Deploy] Received {len(edges)} edges for workflow {workflow_id}")
    for e in edges:
        target_handle = e.get('targetHandle')
        if target_handle and target_handle.startswith('input-') and target_handle != 'input-main':
            logger.debug(f"[Deploy] Config edge: {e.get('source')} -> {e.get('target')} (handle={target_handle})")

    # Check for tool connections to AI Agent
    tool_edges = [e for e in edges if e.get('targetHandle') == 'input-tools']
    if tool_edges:
        logger.debug(f"[Deploy] Tool edges found: {len(tool_edges)}")
        for te in tool_edges:
            logger.debug(f"[Deploy] Tool edge: source={te.get('source')} -> target={te.get('target')}")
    else:
        logger.debug("[Deploy] No input-tools edges found")

    if not nodes:
        return {"success": False, "error": "No nodes provided"}

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required for deployment"}

    # Pre-deploy validation gate. Deploy never honors a force-override —
    # a broken workflow running on a schedule is far worse than a failed
    # one-shot manual run.
    from services.workflow_validator import validate_workflow
    deploy_report = await validate_workflow(
        nodes=nodes, edges=edges,
        parameters_by_id=data.get("parameters_by_id"),
    )
    if deploy_report["errors"]:
        return {
            "success": False,
            "error": "validation_failed",
            "report": deploy_report,
        }

    # Check if THIS specific workflow is already deployed (per-workflow isolation)
    if workflow_service.is_workflow_deployed(workflow_id):
        status = workflow_service.get_deployment_status(workflow_id)
        return {
            "success": False,
            "error": f"Workflow {workflow_id} is already deployed. Cancel it first.",
            "workflow_id": workflow_id,
            "is_running": True,
            "run_counter": status.get("run_counter", 0)
        }

    # Acquire workflow lock before starting deployment (per-workflow locking - n8n pattern)
    lock_acquired = await broadcaster.lock_workflow(workflow_id, reason="deployment")
    if not lock_acquired:
        lock_info = broadcaster.get_workflow_lock(workflow_id)
        return {
            "success": False,
            "error": f"Workflow {workflow_id} is already locked for {lock_info.get('reason', 'deployment')}",
            "locked_by": lock_info.get("workflow_id"),
            "locked_at": lock_info.get("locked_at")
        }

    # Broadcast deployment starting status using centralized method
    await broadcaster.update_workflow_status(executing=True, current_node=None, progress=0)
    await broadcaster.update_deployment_status(
        is_running=True,
        status="starting",
        active_runs=0,
        workflow_id=workflow_id
    )

    # Create status callback to broadcast node and deployment updates
    # Include workflow_id in all node status updates (n8n pattern for workflow isolation)
    async def status_callback(node_id: str, status: str, node_data: Optional[Dict] = None):
        if node_id == "__deployment__":
            # Deployment-level status update using centralized method
            active_runs = node_data.get("active_runs", 0) if node_data else 0
            await broadcaster.update_deployment_status(
                is_running=True,
                status=status,
                active_runs=active_runs,
                workflow_id=workflow_id,
                data=node_data
            )
        else:
            # Node-level status update with workflow_id for frontend filtering
            await broadcaster.update_node_status(node_id, status, node_data, workflow_id=workflow_id)
            if status == "executing":
                position = node_data.get("position", 0) if node_data else 0
                total = node_data.get("total", 1) if node_data else 1
                progress = int((position / total) * 100) if total > 0 else 0
                await broadcaster.update_workflow_status(executing=True, current_node=node_id, progress=progress)

    # Start deployment as background task (per-workflow - Temporal/n8n pattern)
    # In the event-driven pattern, deploy_workflow() returns immediately after setting up triggers.
    # The workflow stays locked until cancel_deployment is called.
    async def run_deployment():
        try:
            result = await workflow_service.deploy_workflow(
                nodes=nodes,
                edges=edges,
                session_id=session_id,
                status_callback=status_callback,
                workflow_id=workflow_id
            )

            # In event-driven mode, deploy_workflow returns immediately after trigger setup.
            # If it failed, unlock and report error. If successful, stay running.
            if not result.get("success"):
                # Setup failed - unlock and report error
                logger.error("Deployment setup failed", error=result.get("error"), workflow_id=workflow_id)
                await broadcaster.update_deployment_status(
                    is_running=False,
                    status="error",
                    active_runs=0,
                    workflow_id=workflow_id,
                    error=result.get("error")
                )
                await broadcaster.unlock_workflow(workflow_id)
                # Clean up task reference for this workflow
                _deployment_tasks.pop(workflow_id, None)
            else:
                # Deployment successful - triggers are set up and running.
                # Workflow stays locked until cancel_deployment is called.
                await broadcaster.update_deployment_status(
                    is_running=True,
                    status="running",
                    active_runs=0,
                    workflow_id=workflow_id,
                    data={
                        "triggers_setup": result.get("triggers_setup", []),
                        "deployment_id": result.get("deployment_id")
                    }
                )
                logger.info("[Deployment] Event-driven deployment active",
                           deployment_id=result.get("deployment_id"),
                           workflow_id=workflow_id,
                           triggers=len(result.get("triggers_setup", [])))

        except Exception as e:
            logger.error("Deployment task error", workflow_id=workflow_id, error=str(e))
            await broadcaster.update_deployment_status(
                is_running=False,
                status="error",
                active_runs=0,
                workflow_id=workflow_id,
                error=str(e)
            )
            await broadcaster.unlock_workflow(workflow_id)
            # Clean up task reference for this workflow
            _deployment_tasks.pop(workflow_id, None)

    # Store task per workflow for independent cancellation
    _deployment_tasks[workflow_id] = asyncio.create_task(run_deployment())

    return {
        "success": True,
        "message": "Deployment started",
        "workflow_id": workflow_id,
        "is_running": True,
        "locked": True,
        "timestamp": time.time()
    }


@ws_handler()
async def handle_cancel_deployment(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Cancel running deployment for a specific workflow (Temporal/n8n pattern).

    Expects:
        workflow_id: Workflow to cancel (required).

    Also cancels any active event waiters (trigger nodes) and unlocks the workflow.

    Returns:
        Cancellation result with iterations completed
    """
    global _deployment_tasks

    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required for cancellation"}

    result = await workflow_service.cancel_deployment(workflow_id)

    # Cancel event waiters for this specific workflow's nodes
    cancelled_waiters = 0
    if result.get("success"):
        cancelled_waiters = result.get("waiters_cancelled", 0)

    # Cancel the deployment task for THIS specific workflow only (per-workflow isolation)
    task = _deployment_tasks.pop(workflow_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info("[Deployment] Deployment task cancelled", workflow_id=workflow_id)

    # Unlock this specific workflow
    if workflow_id:
        await broadcaster.unlock_workflow(workflow_id)

    if result.get("success"):
        # Clear node statuses for all trigger nodes that were waiting
        # The result contains info about cancelled listeners (listener_{node_id})
        # Use existing clear_node_status method which broadcasts node_status_cleared
        for node_id in result.get("cancelled_listener_node_ids", []):
            await broadcaster.clear_node_status(node_id)

        # Broadcast deployment cancelled status using centralized method
        await broadcaster.update_workflow_status(executing=False, current_node=None, progress=0)
        await broadcaster.update_deployment_status(
            is_running=False,
            status="cancelled",
            active_runs=0,
            workflow_id=workflow_id,
            data={
                "iterations_completed": result.get("iterations_completed", 0)
            }
        )

    return {
        "success": result.get("success", False),
        "message": result.get("message", result.get("error")),
        "workflow_id": workflow_id,
        "was_running": result.get("was_running", False),
        "iterations_completed": result.get("iterations_completed", 0),
        "cancelled_waiters": cancelled_waiters,
        "unlocked": workflow_id is not None,
        "timestamp": time.time()
    }


@ws_handler()
async def handle_get_deployment_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current deployment status including workflow lock info.

    Expects (optional):
        workflow_id: Get status for specific workflow. If not provided, returns global status.

    Returns:
        Current deployment state, iteration count, and lock status
    """
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")
    status = workflow_service.get_deployment_status(workflow_id)

    return {
        "is_running": workflow_service.is_deployment_running(workflow_id),
        "run_counter": status.get("run_counter", 0),
        "active_runs": status.get("active_runs", 0),
        "settings": workflow_service.get_deployment_settings(),
        "workflow_id": workflow_id or status.get("workflow_id"),
        "deployed_workflows": status.get("deployed_workflows", []),
        "lock": broadcaster.get_workflow_lock(),
        "timestamp": time.time()
    }


@ws_handler()
async def handle_get_workflow_lock(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current workflow lock status.

    Returns:
        Current lock state including locked workflow_id and reason
    """
    broadcaster = get_status_broadcaster()

    return {
        "lock": broadcaster.get_workflow_lock(),
        "timestamp": time.time()
    }


@ws_handler()
async def handle_update_deployment_settings(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Update deployment settings (can be called during active deployment).

    Expects any of:
        delay_between_runs: float - Seconds to wait between iterations
        stop_on_error: bool - Stop deployment when a node fails
        max_iterations: int - Max iterations (0 = unlimited)

    Returns:
        Updated settings and current deployment state
    """
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    settings_to_update = {}
    if "delay_between_runs" in data:
        settings_to_update["delay_between_runs"] = data["delay_between_runs"]
    if "stop_on_error" in data:
        settings_to_update["stop_on_error"] = data["stop_on_error"]
    if "max_iterations" in data:
        settings_to_update["max_iterations"] = data["max_iterations"]

    updated_settings = await workflow_service.update_deployment_settings(settings_to_update)

    # Broadcast settings update
    status = workflow_service.get_deployment_status()
    await broadcaster.broadcast({
        "type": "deployment_settings_updated",
        "settings": updated_settings,
        "is_running": workflow_service.is_deployment_running(),
        "run_counter": status.get("run_counter", 0)
    })

    return {
        "success": True,
        "settings": updated_settings,
        "is_running": workflow_service.is_deployment_running(),
        "run_counter": status.get("run_counter", 0),
        "active_runs": status.get("active_runs", 0),
        "timestamp": time.time()
    }


# ============================================================================
# AI Handlers
# ============================================================================

@ws_handler("node_id", "node_type")
async def handle_execute_ai_node(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Execute an AI node (chat model or agent)."""
    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()
    node_id, node_type = data["node_id"], data["node_type"]
    workflow_id = data.get("workflow_id")  # Per-workflow isolation for tool node glowing

    await broadcaster.update_node_status(node_id, "executing", workflow_id=workflow_id)
    await broadcaster.workflow_run_started(workflow_id)
    result: Dict[str, Any]
    try:
        result = await workflow_service.execute_node(
            node_id=node_id, node_type=node_type,
            parameters=data.get("parameters", {}),
            nodes=data.get("nodes", []), edges=data.get("edges", []),
            session_id=data.get("session_id", "default"),
            workflow_id=workflow_id,
        )

        if result.get("success"):
            await broadcaster.update_node_status(node_id, "success", result.get("result"), workflow_id=workflow_id)
            await broadcaster.update_node_output(node_id, result.get("result"), workflow_id=workflow_id)
        else:
            await broadcaster.update_node_status(node_id, "error", {"error": result.get("error")}, workflow_id=workflow_id)
    except Exception:
        await broadcaster.update_node_status(node_id, "error", {"error": "execution crashed"}, workflow_id=workflow_id)
        raise
    finally:
        await broadcaster.workflow_run_ended(workflow_id)

    return {"success": result.get("success", False), "node_id": node_id, "result": result.get("result"), "error": result.get("error"),
            "execution_time": result.get("execution_time"), "timestamp": time.time()}


@ws_handler("provider", "api_key")
async def handle_get_ai_models(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get available AI models for a provider."""
    ai_service = container.ai_service()
    models = await ai_service.fetch_models(data["provider"], data["api_key"])
    return {"provider": data["provider"], "models": models, "timestamp": time.time()}


# ============================================================================
# API Key Handlers
# ============================================================================

@ws_handler("provider", "api_key")
async def handle_validate_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Validate and store an API key.

    Pure dispatch — looks up the plugin's ``Credential`` subclass in
    ``CREDENTIAL_REGISTRY`` and calls its ``validate`` classmethod. The
    base ``Credential.validate`` (defined in
    ``services/plugin/credential.py``) wires the shared scaffold
    (storage + status broadcast + error classification + response
    envelope) and dispatches the per-provider probe via the
    subclass-supplied ``_probe`` hook. Cloud LLM providers inherit
    ``_LLMApiKey._probe`` (``ai_service.fetch_models``); Maps + Apify +
    local-LLM credentials override ``_probe`` (or the whole ``validate``
    method, in the local-LLM case) with their own bespoke probes.

    The router doesn't know about specific providers — adding a new
    provider with a special validator is a single new ``_probe``
    override on the plugin's ``Credential`` subclass.
    """
    from services.plugin.credential import CREDENTIAL_REGISTRY

    provider = data["provider"].lower()
    normalized = dict(data, provider=provider)

    cred_cls = CREDENTIAL_REGISTRY.get(provider)
    if cred_cls is None:
        return {
            "success": False,
            "valid": False,
            "error": f"Unknown provider '{provider}' — no Credential class registered.",
        }
    return await cred_cls.validate(normalized)


def _lookup_credential_default(storage_key: str) -> Optional[str]:
    """Look up a field's catalogue ``default`` for the given storage key.

    Storage keys are either the provider id (``"openai"`` for cloud
    providers whose field key is ``apiKey``) or the field key itself
    (``"lmstudio_proxy"`` etc. for local-LLM providers). Both shapes
    map back to a credential_providers.json field; this helper finds
    the one and returns its ``default`` value if declared. Used by
    ``handle_get_stored_api_key`` to surface canonical defaults
    (e.g. local-LLM Base URL ``http://localhost:1234/v1``) to the
    frontend without requiring per-panel pre-fill logic.
    """
    from services.credential_registry import get_credential_registry
    registry = get_credential_registry()
    for provider in registry.get_all_providers():
        provider_id = provider.get("id") or provider.get("name", "").lower()
        for field in (provider.get("fields") or []):
            field_key = field.get("key")
            if not field_key:
                continue
            field_storage_key = provider_id if field_key == "apiKey" else field_key
            if field_storage_key == storage_key:
                default = field.get("default")
                return default if default else None
    return None


@ws_handler("provider")
async def handle_get_stored_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get stored API key for a provider.

    Response uses camelCase (``hasKey`` / ``apiKey``) to match the
    ``update_api_key_status`` broadcast shape — every WS payload the
    frontend receives for API key state uses the same convention, so no
    per-field adapter is needed on the TypeScript side.

    When nothing is stored AND the catalogue declares a ``default``
    for this field (e.g. local-LLM canonical Base URL), the default
    value is returned in ``apiKey`` with ``hasKey: false``. The
    frontend renders the value but tracks ``stored`` separately via
    ``hasKey`` so the validated/connected badge stays honest. Lets
    users click Fetch on a fresh install without retyping the URL.
    """
    auth_service = container.auth_service()
    provider = data["provider"].lower()
    api_key = await auth_service.get_api_key(provider, data.get("session_id", "default"))
    if not api_key:
        default = _lookup_credential_default(provider)
        if default is not None:
            return {"provider": provider, "hasKey": False, "apiKey": default}
        return {"provider": provider, "hasKey": False}
    models = await auth_service.get_stored_models(provider, data.get("session_id", "default"))
    return {"provider": provider, "hasKey": True, "apiKey": api_key, "models": models, "timestamp": time.time()}


@ws_handler("provider", "api_key")
async def handle_save_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save an API key (without validation).

    Supports client-side idempotency: if the client supplies a
    `request_id` (opaque UUID), duplicate calls within 60 s return the
    cached result instead of re-running the mutation. Prevents
    double-writes on retry / reconnect / double-click.
    """
    from services.idempotency import get_idempotency_store

    store = get_idempotency_store("credentials")
    provider = data["provider"].lower()

    async def _do_save() -> Dict[str, Any]:
        auth_service = container.auth_service()
        broadcaster = get_status_broadcaster()
        await auth_service.store_api_key(
            provider=provider,
            api_key=data["api_key"].strip(),
            models=data.get("models", []),
            session_id=data.get("session_id", "default"),
        )
        # Symmetric broadcast: tells every connected client to refetch
        # the catalogue so the `stored` flag flips on this provider.
        # Don't claim validity here — save_api_key doesn't validate.
        await broadcaster.broadcast_credential_event(
            "credential.api_key.saved",
            provider=provider,
        )
        return {"provider": data["provider"]}

    return await store.run(data.get("request_id"), _do_save)


@ws_handler("provider")
async def handle_delete_api_key(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete stored API key.

    Idempotent on `request_id` — see `handle_save_api_key`.
    """
    from services.idempotency import get_idempotency_store

    store = get_idempotency_store("credentials")
    provider = data["provider"].lower()

    async def _do_delete() -> Dict[str, Any]:
        auth_service = container.auth_service()
        broadcaster = get_status_broadcaster()
        await auth_service.remove_api_key(provider, data.get("session_id", "default"))
        # Two broadcasts: api_key_status clears `apiKeyStatuses[provider]`
        # on every connected client (in-memory validation cache); the
        # CloudEvents-typed credential.api_key.deleted invalidates the
        # catalogue so the `stored` flag flips. Both go through the
        # 300 ms invalidateCatalogue debounce — one refetch.
        await broadcaster.update_api_key_status(
            provider, valid=False, has_key=False, message="deleted", models=[],
        )
        await broadcaster.broadcast_credential_event(
            "credential.api_key.deleted",
            provider=provider,
        )
        return {"provider": data["provider"]}

    return await store.run(data.get("request_id"), _do_delete)


# ============================================================================
# Claude OAuth Handlers
# ============================================================================

@ws_handler()
async def handle_claude_oauth_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Initiate Claude OAuth in isolated session."""
    from services.claude_oauth import initiate_claude_oauth
    return await initiate_claude_oauth()


@ws_handler()
async def handle_claude_oauth_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Check Claude OAuth credentials status via ``claude auth status``."""
    from services.claude_oauth import claude_auth_status
    has_token = await claude_auth_status()
    return {"success": True, "has_token": has_token}


# NOTE: `cli_login` / `cli_auth_status` handlers are owned by
# `services/cli_agent/_handlers.py` and self-registered into
# `services.ws_handler_registry` on package import — no entries needed
# here. See `services/cli_agent/__init__.py`.



@ws_handler("url")
async def handle_test_ai_proxy(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Test connectivity to an AI proxy server."""
    import httpx

    url = data["url"].rstrip("/")
    timeout = data.get("timeout", 5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Try common health/models endpoints
            for endpoint in ["/v1/models", "/api/tags", "/health", "/"]:
                try:
                    response = await client.get(f"{url}{endpoint}")
                    if response.status_code < 500:
                        return {
                            "success": True,
                            "url": url,
                            "status_code": response.status_code,
                            "endpoint": endpoint,
                        }
                except httpx.RequestError:
                    continue

            return {
                "success": False,
                "url": url,
                "error": "No responding endpoints found",
            }
    except httpx.ConnectError:
        return {"success": False, "url": url, "error": "Connection refused"}
    except httpx.TimeoutException:
        return {"success": False, "url": url, "error": "Connection timeout"}
    except Exception as e:
        return {"success": False, "url": url, "error": str(e)}


# ============================================================================
# Android handlers (5 of them: get_android_devices, execute_android_action,
# android_relay_{connect,disconnect,reconnect}) live in
# ``nodes/android/_handlers.py`` and self-register via
# ``register_ws_handlers``. The plugin's HTTP router lives in
# ``nodes/android/_router.py`` and mounts via the plugin-router loop.
# ============================================================================


# ----------------------------------------------------------------------------
# Per-provider credential validators (Maps geocode probe, Apify /users/me,
# Ollama / LM Studio SDK probes) live on their plugins Credential
# subclass via the shared Credential._probe / Credential.validate scaffold
# in services/plugin/credential.py. handle_validate_api_key (above) is a
# pure dispatch through CREDENTIAL_REGISTRY  no per-provider branches in
# this router.
# ----------------------------------------------------------------------------


# ============================================================================
# Plugin-owned WS handlers (telegram, future plugins, ...) live in their
# own ``nodes/<group>/`` package and self-register via
# ``register_ws_handlers`` at import time.  The dispatch table below
# merges them in via ``get_ws_handlers()`` -- no plugin names hardcoded
# in this file.
# ============================================================================


# ============================================================================
# Workflow Storage Operations
# ============================================================================

async def handle_save_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save workflow to database."""
    database = container.database()
    success = await database.save_workflow(
        workflow_id=data["workflow_id"],
        name=data["name"],
        data=data.get("data", {})
    )
    return {"success": success, "workflow_id": data["workflow_id"]}


@ws_handler()
async def handle_import_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Import a workflow JSON. Two-step UX:

    First call with just the workflow object returns a preview if
    confirmations are needed (name conflict, missing credentials). The
    frontend prompts the user, then re-calls with ``name`` set and
    ``force_credentials=True`` to commit.

    Body fields:
        workflow: Raw workflow dict (nodes, edges, optional nodeParameters).
        name: User-confirmed final workflow name; omit on first call to
            let the server report a name conflict.
        force_credentials: Skip the missing-credential preview gate when
            the user has acknowledged the warning.

    See ``services.workflow_import.import_workflow`` for the full
    orchestrator contract.
    """
    from services.workflow_import import import_workflow

    workflow_payload = data.get("workflow")
    if not isinstance(workflow_payload, dict):
        return {"success": False, "error": "workflow payload required"}

    return await import_workflow(
        workflow_payload,
        name=data.get("name"),
        force_credentials=bool(data.get("force_credentials")),
        auth_service=container.auth_service(),
        database=container.database(),
    )


async def handle_get_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get workflow by ID."""
    database = container.database()
    workflow = await database.get_workflow(data["workflow_id"])
    if workflow:
        return {
            "success": True,
            "workflow": {
                "id": workflow.id,
                "name": workflow.name,
                "data": workflow.data,
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None
            }
        }
    return {"success": False, "error": "Workflow not found"}


async def handle_get_all_workflows(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all workflows."""
    database = container.database()
    workflows = await database.get_all_workflows()
    return {
        "success": True,
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "nodeCount": len(w.data.get("nodes", [])) if w.data else 0,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None
            }
            for w in workflows
        ]
    }


async def handle_delete_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete workflow."""
    database = container.database()
    success = await database.delete_workflow(data["workflow_id"])
    return {"success": success, "workflow_id": data["workflow_id"]}


# ============================================================================
# Chat Message Handler (for chatTrigger nodes)
# ============================================================================

@ws_handler("message")
async def handle_send_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Handle chat message from console panel - dispatches to chatTrigger nodes.

    This handler receives messages from the frontend chat panel and dispatches
    them as 'chat_message_received' events to any waiting chatTrigger nodes.
    Also saves the message to database for persistence across restarts.
    """
    # Wave 12 B7: route through chat_trigger plugin _events.py wrapper —
    # the chat_message_received event wire shape lives with the plugin,
    # not in this central WS router.
    from nodes.trigger.chat_trigger._events import dispatch_chat_message_received

    message = data["message"]
    role = data.get("role", "user")
    session_id = data.get("session_id", "default")
    timestamp = data.get("timestamp") or datetime.now().isoformat()

    # Save to database for persistence
    database = container.database()
    await database.add_chat_message(session_id, role, message)

    # Build event data matching chatTrigger output schema
    event_data = {
        "message": message,
        "timestamp": timestamp,
        "session_id": session_id
    }

    # Dispatch to chatTrigger waiters
    resolved = dispatch_chat_message_received(event_data)

    logger.info(f"[ChatMessage] Dispatched message to {resolved} chatTrigger waiter(s)")

    return {
        "success": True,
        "message": "Chat message sent",
        "resolved_count": resolved,
        "timestamp": timestamp
    }


@ws_handler()
async def handle_get_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get chat messages from database for a session."""
    session_id = data.get("session_id", "default")
    limit = data.get("limit")  # Optional limit

    database = container.database()
    messages = await database.get_chat_messages(session_id, limit)

    return {
        "success": True,
        "messages": messages,
        "session_id": session_id
    }


@ws_handler()
async def handle_clear_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear all chat messages for a session."""
    session_id = data.get("session_id", "default")

    database = container.database()
    count = await database.clear_chat_messages(session_id)

    return {
        "success": True,
        "message": f"Cleared {count} chat messages",
        "cleared_count": count
    }


@ws_handler()
async def handle_get_console_logs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get console logs from database, optionally scoped to a workflow."""
    limit = data.get("limit", 100)
    workflow_id = data.get("workflow_id")

    database = container.database()
    logs = await database.get_console_logs(limit, workflow_id=workflow_id)

    return {
        "success": True,
        "logs": logs,
        "workflow_id": workflow_id,
    }


@ws_handler()
async def handle_clear_console_logs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear console logs from database and in-memory broadcaster cache.

    With ``workflow_id`` set, only that workflow's history is cleared
    (DB rows + in-memory entries). Without it, the legacy "clear all"
    behaviour is preserved. Broadcasts ``console_logs_cleared`` carrying
    the workflow_id so other tabs viewing the same workflow drop their
    local list, while tabs on a different workflow are unaffected
    (the frontend filter already keys on this).
    """
    workflow_id = data.get("workflow_id")
    database = container.database()
    count = await database.clear_console_logs(workflow_id=workflow_id)

    # Also clear / filter in-memory logs
    broadcaster = get_status_broadcaster()
    if "console_logs" in broadcaster._status:
        if workflow_id:
            broadcaster._status["console_logs"] = [
                log for log in broadcaster._status["console_logs"]
                if log.get("workflow_id") != workflow_id
            ]
        else:
            broadcaster._status["console_logs"] = []

    # Tell connected clients to drop their local copy for this scope.
    await broadcaster.broadcast({
        "type": "console_logs_cleared",
        "workflow_id": workflow_id,
    })

    return {
        "success": True,
        "message": f"Cleared {count} console logs",
        "cleared_count": count,
        "workflow_id": workflow_id,
    }


@ws_handler("message", "role")
async def handle_save_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save a single chat message (used for assistant responses)."""
    message = data["message"]
    role = data["role"]
    session_id = data.get("session_id", "default")

    database = container.database()
    success = await database.add_chat_message(session_id, role, message)

    return {
        "success": success,
        "message": "Chat message saved" if success else "Failed to save chat message"
    }


@ws_handler()
async def handle_get_chat_sessions(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get list of all chat sessions."""
    database = container.database()
    sessions = await database.get_chat_sessions()

    return {
        "success": True,
        "sessions": sessions
    }


# ============================================================================
# Terminal Logs Handlers
# ============================================================================

@ws_handler()
async def handle_get_terminal_logs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get terminal log history."""
    broadcaster = get_status_broadcaster()
    logs = broadcaster.get_terminal_logs()
    return {"success": True, "logs": logs}


@ws_handler()
async def handle_clear_terminal_logs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear terminal log history."""
    broadcaster = get_status_broadcaster()
    await broadcaster.clear_terminal_logs()
    return {"success": True, "message": "Terminal logs cleared"}


# =============================================================================
# Process Manager Handlers
# =============================================================================

@ws_handler()
async def handle_process_list(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """List running processes."""
    from services.process_service import get_process_service
    svc = get_process_service()
    workflow_id = data.get("workflow_id", "default")
    return {"success": True, "processes": svc.list_processes(workflow_id), "max_processes": svc.max_processes}


@ws_handler("name")
async def handle_process_get_output(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get output from a process's log file."""
    from services.process_service import get_process_service
    name = data["name"]
    workflow_id = data.get("workflow_id", "default")
    stream = data.get("stream", "stdout")
    tail = int(data.get("tail", 50))
    offset = int(data.get("offset", 0))
    return {"success": True, **get_process_service().get_output(name, workflow_id, stream, tail, offset)}


@ws_handler("name", "text")
async def handle_process_send_input(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send stdin text to a running process."""
    from services.process_service import get_process_service
    name = data["name"]
    text = data["text"]
    workflow_id = data.get("workflow_id", "default")
    return await get_process_service().send_input(name, workflow_id, text)


# ============================================================================
# User Skills Handlers
# ============================================================================

@ws_handler("skill_name")
async def handle_get_skill_content(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get skill content (instructions) by skill name.

    Works for both built-in skills (from SKILL.md files) and user skills (from database).
    """
    from services.skill_loader import get_skill_loader

    skill_name = data["skill_name"]
    skill_loader = get_skill_loader()

    # Use load_skill_async which checks both filesystem and database
    skill = await skill_loader.load_skill_async(skill_name)
    if skill:
        return {
            "success": True,
            "skill_name": skill_name,
            "instructions": skill.instructions,
            "description": skill.metadata.description,
            "allowed_tools": skill.metadata.allowed_tools,
            "is_builtin": skill.metadata.path is not None,
            "timestamp": time.time()
        }

    return {"success": False, "error": f"Skill '{skill_name}' not found"}


@ws_handler("skill_name", "instructions")
async def handle_save_skill_content(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save skill content (instructions) by skill name.

    For built-in skills, writes to the SKILL.md file.
    For user skills, updates the database.
    """
    import re
    from services.skill_loader import get_skill_loader

    skill_name = data["skill_name"]
    new_instructions = data["instructions"]
    skill_loader = get_skill_loader()

    # Check if it's a built-in skill
    if skill_name in skill_loader._registry:
        metadata = skill_loader._registry[skill_name]
        if metadata.path is not None:
            # It's a built-in skill - update SKILL.md file
            skill_md_path = metadata.path / "SKILL.md"

            if not skill_md_path.exists():
                return {"success": False, "error": f"SKILL.md not found for '{skill_name}'"}

            # Read existing file to preserve frontmatter
            content = skill_md_path.read_text(encoding='utf-8')

            # Parse frontmatter
            frontmatter_match = re.match(r'^(---\s*\n.*?\n---\s*\n)', content, re.DOTALL)
            if frontmatter_match:
                # Keep frontmatter, replace body
                new_content = frontmatter_match.group(1) + new_instructions
            else:
                # No frontmatter, just write instructions
                new_content = new_instructions

            # Write back to file
            skill_md_path.write_text(new_content, encoding='utf-8')

            # Clear cache so next load gets fresh content
            skill_loader.clear_cache()

            logger.info(f"[Skills] Updated built-in skill: {skill_name}")
            return {
                "success": True,
                "skill_name": skill_name,
                "is_builtin": True,
                "message": f"Skill '{skill_name}' saved to SKILL.md",
                "timestamp": time.time()
            }

    # It's a user skill - update in database
    database = container.database()
    user_skill = await database.get_user_skill(skill_name)
    if user_skill:
        updated = await database.update_user_skill(
            name=skill_name,
            instructions=new_instructions
        )
        if updated:
            logger.info(f"[Skills] Updated user skill: {skill_name}")
            return {
                "success": True,
                "skill_name": skill_name,
                "is_builtin": False,
                "message": f"Skill '{skill_name}' saved to database",
                "timestamp": time.time()
            }

    return {"success": False, "error": f"Skill '{skill_name}' not found"}


@ws_handler("action", "source_type", "target_type", "target_handle")
async def handle_evaluate_auto_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Decide what to do when a tool->agent edge is connected/disconnected.

    Owns the auto-add-skill policy: visuals.json reverse map +
    plugin-registry agent classification + canonical SkillConfig
    shape. Frontend forwards minimal edge details and the current
    Master Skill state; this returns a standard workflow-ops batch
    (see docs-internal/workflow_ops_protocol.md).
    """
    from services import auto_skill

    result = auto_skill.evaluate(
        action=data["action"],
        source_type=data["source_type"],
        target_type=data["target_type"],
        target_handle=data["target_handle"],
        target_node_id=data.get("target_node_id"),
        master_skill_id=data.get("master_skill_id"),
        master_skill_config=data.get("master_skill_config"),
    )
    return {"success": True, **result}


@ws_handler()
async def handle_list_skill_folders(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """List top-level subdirectories under server/skills/.

    Returns folder names for the skill folder dropdown in MasterSkillEditor.
    """
    from pathlib import Path

    server_dir = Path(__file__).parent.parent
    skills_dir = server_dir / "skills"

    folders = []
    if skills_dir.exists():
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                # Count SKILL.md files inside
                skill_count = len(list(item.rglob("SKILL.md")))
                folders.append({
                    "name": item.name,
                    "skill_count": skill_count
                })

    return {"success": True, "folders": folders}


@ws_handler("folder")
async def handle_scan_skill_folder(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Scan a subfolder under server/skills/ for SKILL.md files.

    Returns list of discovered skills with their metadata.
    Used by MasterSkillEditor when skillFolder is set.
    Also registers discovered skills in the global registry for get_skill_content.
    """
    from pathlib import Path
    from services.skill_loader import get_skill_loader

    folder = data["folder"]
    server_dir = Path(__file__).parent.parent
    target_dir = server_dir / "skills" / folder

    if not target_dir.exists():
        return {"success": False, "error": f"Folder not found: skills/{folder}"}

    skill_loader = get_skill_loader()
    skills = []
    for skill_md in target_dir.rglob("SKILL.md"):
        metadata = skill_loader._parse_skill_metadata(skill_md)
        if metadata:
            # Register the skill in the global registry so get_skill_content can find it
            metadata.path = skill_md.parent
            skill_loader._registry[metadata.name] = metadata

            skills.append({
                "name": metadata.name,
                "description": metadata.description,
                "metadata": metadata.metadata
            })

    return {"success": True, "skills": skills, "folder": folder}


@ws_handler()
async def handle_lookup_skill_metadata(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    """Look up SKILL.md metadata for a list of skill names across every
    folder (and the user-skills DB).

    The Master Skill editor lets users keep a skill enabled even after
    switching the node's `skill_folder`, so the AI agent's Connected
    Skills panel needs to resolve metadata for skills outside the
    currently-selected folder. `scan_skill_folder` is folder-scoped;
    this handler is name-scoped and queries the shared registry.
    """
    from services.skill_loader import get_skill_loader

    names = data.get("names") or []
    if not isinstance(names, list):
        return {"success": False, "error": "names must be a list", "skills": []}

    skill_loader = get_skill_loader()
    # Ensure the registry is hydrated -- skip the DB pass since we only
    # need the filesystem-derived metadata for the icon/color/desc fields.
    if not skill_loader._registry:
        skill_loader.scan_skills()

    skills: List[Dict[str, Any]] = []
    for name in names:
        meta = skill_loader._registry.get(name)
        if meta:
            skills.append({
                "name": meta.name,
                "description": meta.description,
                "metadata": meta.metadata,
            })

    return {"success": True, "skills": skills}


@ws_handler()
async def handle_get_user_skills(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all user-created skills."""
    database = container.database()
    active_only = data.get("active_only", True)
    skills = await database.get_all_user_skills(active_only=active_only)
    return {"skills": skills, "count": len(skills), "timestamp": time.time()}


@ws_handler("name")
async def handle_get_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get a specific user skill by name."""
    database = container.database()
    skill = await database.get_user_skill(data["name"])
    if skill:
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


@ws_handler("name", "display_name", "instructions")
async def handle_create_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create a new user skill."""
    database = container.database()
    broadcaster = get_status_broadcaster()

    skill = await database.create_user_skill(
        name=data["name"],
        display_name=data["display_name"],
        description=data.get("description", ""),
        instructions=data["instructions"],
        allowed_tools=data.get("allowed_tools"),
        category=data.get("category", "custom"),
        icon=data.get("icon", "star"),
        color=data.get("color", "#6366F1"),
        metadata_json=data.get("metadata"),
        created_by=data.get("created_by")
    )

    if skill:
        # Broadcast skill created to all clients
        await broadcaster.broadcast({
            "type": "user_skill_created",
            "skill": skill,
            "timestamp": time.time()
        })
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Failed to create skill. Name '{data['name']}' may already exist."}


@ws_handler("name")
async def handle_update_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Update an existing user skill."""
    database = container.database()
    broadcaster = get_status_broadcaster()

    skill = await database.update_user_skill(
        name=data["name"],
        display_name=data.get("display_name"),
        description=data.get("description"),
        instructions=data.get("instructions"),
        allowed_tools=data.get("allowed_tools"),
        category=data.get("category"),
        icon=data.get("icon"),
        color=data.get("color"),
        metadata_json=data.get("metadata"),
        is_active=data.get("is_active")
    )

    if skill:
        # Broadcast skill updated to all clients
        await broadcaster.broadcast({
            "type": "user_skill_updated",
            "skill": skill,
            "timestamp": time.time()
        })
        return {"skill": skill, "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


@ws_handler("name")
async def handle_delete_user_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete a user skill."""
    database = container.database()
    broadcaster = get_status_broadcaster()

    deleted = await database.delete_user_skill(data["name"])

    if deleted:
        # Broadcast skill deleted to all clients
        await broadcaster.broadcast({
            "type": "user_skill_deleted",
            "name": data["name"],
            "timestamp": time.time()
        })
        logger.info(f"[Skills] Deleted user skill: {data['name']}")
        return {"success": True, "deleted": True, "name": data["name"], "timestamp": time.time()}
    return {"success": False, "error": f"Skill '{data['name']}' not found"}


# ============================================================================
# Memory and Skill Clear/Reset Handlers
# ============================================================================

@ws_handler()
async def handle_clear_memory(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear conversation memory and sibling agent session state.

    Business logic lives in :func:`services.memory.clear_agent_session_state`
    — this handler only decodes the request and shapes the response.

    When ``memory_node_id`` is provided (claude_code_agent JSONL bridge
    surface), the simpleMemory node's ``memory_content`` is reset and
    ``memory_jsonl`` + ``last_session_id`` are wiped server-side. Legacy
    callers that omit it still get ``default_content`` for the
    frontend's existing markdown reset path.
    """
    from services.memory import clear_agent_session_state

    session_id = data.get("session_id", "default")
    workflow_id = data.get("workflow_id")
    clear_long_term = data.get("clear_long_term", False)
    memory_node_id = data.get("memory_node_id")

    cleared = await clear_agent_session_state(
        session_id=session_id,
        workflow_id=workflow_id,
        clear_long_term=clear_long_term,
        memory_node_id=memory_node_id,
    )

    return {
        "success": True,
        "default_content": "# Conversation History\n\n*No messages yet.*\n",
        "cleared_vector_store": cleared["cleared_vector_store"],
        "cleared_todo_keys": cleared["cleared_todo_keys"],
        "cleared_memory_node": cleared["cleared_memory_node"],
        "session_id": session_id,
    }


@ws_handler("skill_name")
async def handle_reset_skill(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get original SKILL.md content for resetting a built-in skill."""
    import re
    from services.skill_loader import get_skill_loader

    skill_name = data["skill_name"]
    skill_loader = get_skill_loader()

    # Check if skill exists in registry
    if skill_name not in skill_loader._registry:
        return {"success": False, "error": f"Skill '{skill_name}' not found"}

    metadata = skill_loader._registry[skill_name]

    # User skills don't have a default to reset to
    if metadata.path is None:
        return {"success": False, "error": f"Cannot reset user skill '{skill_name}' - no default exists"}

    skill_md_path = metadata.path / "SKILL.md"
    if not skill_md_path.exists():
        return {"success": False, "error": f"SKILL.md not found for '{skill_name}'"}

    content = skill_md_path.read_text(encoding='utf-8')

    # Extract body after frontmatter
    frontmatter_match = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
    if frontmatter_match:
        original_instructions = content[frontmatter_match.end():]
    else:
        original_instructions = content

    logger.info(f"[Skill] Reset skill '{skill_name}' to default content")

    return {
        "success": True,
        "skill_name": skill_name,
        "original_content": original_instructions,
        "is_builtin": True
    }


# ============================================================================
# User Settings Handlers
# ============================================================================

@ws_handler()
async def handle_get_user_settings(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get user settings from database."""
    database = container.database()
    user_id = data.get("user_id", "default")
    settings = await database.get_user_settings(user_id)

    # Return default settings if none exist
    if settings is None:
        settings = {
            "user_id": user_id,
            "auto_save": True,
            "auto_save_interval": 30,
            "sidebar_default_open": True,
            "component_palette_default_open": True
        }

    return {"settings": settings}


@ws_handler()
async def handle_save_user_settings(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save user settings to database."""
    database = container.database()
    user_id = data.get("user_id", "default")
    settings_data = data.get("settings", {})

    success = await database.save_user_settings(settings_data, user_id)

    if success:
        # Sync process limit if changed
        if "max_processes" in settings_data:
            from services.process_service import get_process_service
            get_process_service().max_processes = int(settings_data["max_processes"])

        # Fetch the saved settings to return
        settings = await database.get_user_settings(user_id)
        return {"settings": settings}
    else:
        return {"success": False, "error": "Failed to save settings"}


# ============================================================================
# Provider Defaults Handlers
# ============================================================================

@ws_handler()
async def handle_get_provider_defaults(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get default parameters for a provider."""
    from services.ai import get_default_model
    from services.model_registry import get_model_registry
    database = container.database()
    provider = data.get("provider", "").lower()
    defaults = await database.get_provider_defaults(provider)

    # Get default model from JSON config as fallback
    config_default_model = get_default_model(provider)

    if defaults:
        # If DB record exists but default_model is empty, use config default
        if not defaults.get("default_model"):
            defaults["default_model"] = config_default_model
        return {"provider": provider, "defaults": defaults}

    # No DB record - fetch model constraints from registry for sensible defaults
    registry = get_model_registry()
    model_max_tokens = registry.get_max_output_tokens(config_default_model, provider)

    return {
        "provider": provider,
        "defaults": {
            "default_model": config_default_model,
            "temperature": 0.7,
            "max_tokens": model_max_tokens,
            "thinking_enabled": False,
            "thinking_budget": 2048,
            "reasoning_effort": "medium",
            "reasoning_format": "parsed",
        }
    }


@ws_handler()
async def handle_save_provider_defaults(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save default parameters for a provider."""
    database = container.database()
    provider = data.get("provider", "").lower()
    defaults = data.get("defaults", {})
    success = await database.save_provider_defaults(provider, defaults)
    return {"success": success, "provider": provider}


@ws_handler()
async def handle_get_validated_ai_providers(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all AI providers with stored API keys and their popular models.

    Returns providers that have validated keys, their stored models,
    and the current global default provider/model from UserSettings.
    """
    import json
    from pathlib import Path

    auth_service = container.auth_service()
    database = container.database()

    from services.ai import PROVIDER_CONFIGS
    AI_PROVIDERS = list(PROVIDER_CONFIGS.keys())

    # Load popular models from llm_defaults.json
    defaults_path = Path(__file__).parent.parent / "config" / "llm_defaults.json"
    try:
        with open(defaults_path) as f:
            llm_defaults = json.load(f)
    except Exception:
        llm_defaults = {"providers": {}}

    providers = []
    for provider in AI_PROVIDERS:
        api_key = await auth_service.get_api_key(provider, data.get("session_id", "default"))
        if not api_key:
            continue

        # Get stored models (full list from validation)
        stored_models = await auth_service.get_stored_models(provider, data.get("session_id", "default"))

        # Get popular models from llm_defaults (the explicit entries, not _default)
        provider_config = llm_defaults.get("providers", {}).get(provider, {})
        default_model = provider_config.get("default_model", "")
        popular_models = [
            m for m in provider_config.get("max_output_tokens", {}).keys()
            if m != "_default"
        ]

        # Get per-provider default model override
        provider_defaults = await database.get_provider_defaults(provider)
        if provider_defaults and provider_defaults.get("default_model"):
            default_model = provider_defaults["default_model"]

        providers.append({
            "provider": provider,
            "models": stored_models or [],
            "popular_models": popular_models,
            "default_model": default_model,
        })

    # Get global default from UserSettings
    user_id = data.get("user_id", "default")
    settings = await database.get_user_settings(user_id)
    global_provider = settings.get("default_llm_provider") if settings else None
    global_model = settings.get("default_llm_model") if settings else None

    return {
        "providers": providers,
        "global_provider": global_provider,
        "global_model": global_model,
    }


@ws_handler()
async def handle_save_global_model(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save the global default provider + model to UserSettings."""
    database = container.database()
    provider = data.get("provider", "")
    model = data.get("model", "")
    user_id = data.get("user_id", "default")

    success = await database.save_user_settings({
        "default_llm_provider": provider,
        "default_llm_model": model,
    }, user_id)

    return {"success": success, "provider": provider, "model": model}


# ============================================================================
# Pricing Config Handlers
# ============================================================================

@ws_handler()
async def handle_get_pricing_config(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get full pricing configuration for display/editing."""
    from services.pricing import get_pricing_service
    pricing = get_pricing_service()
    return {"success": True, "config": pricing.get_config()}


@ws_handler()
async def handle_save_pricing_config(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save updated pricing configuration."""
    from services.pricing import get_pricing_service

    config = data.get('config')
    if not config:
        return {"success": False, "error": "No config provided"}

    pricing = get_pricing_service()
    success = pricing.save_config(config)
    return {"success": success}


@ws_handler()
async def handle_get_node_allowlist(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Return the node allowlist that controls which nodes appear in the palette."""
    from services.node_allowlist import get_node_allowlist_service
    return get_node_allowlist_service().get_config()


@ws_handler()
async def handle_get_api_usage_summary(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get aggregated API usage and cost by service (Twitter, etc.)."""
    database = container.database()
    service = data.get('service')  # Optional filter by service
    services = await database.get_api_usage_summary(service)
    return {"success": True, "services": services}


# ============================================================================
# Compaction Handlers
# ============================================================================

@ws_handler("session_id")
async def handle_get_compaction_stats(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get compaction statistics for a session.

    Optional model/provider params enable model-aware threshold (50% of context window).
    """
    from services.compaction import get_compaction_service
    svc = get_compaction_service()
    if not svc:
        return {"success": False, "error": "Compaction service not initialized"}
    return await svc.stats(
        data["session_id"],
        model=data.get("model", ""),
        provider=data.get("provider", ""),
    )


@ws_handler("session_id")
async def handle_configure_compaction(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Configure compaction settings for a session."""
    from services.compaction import get_compaction_service
    svc = get_compaction_service()
    if not svc:
        return {"success": False, "error": "Compaction service not initialized"}
    success = await svc.configure(data["session_id"], data.get("threshold"), data.get("enabled"))
    return {"success": success}


@ws_handler()
async def handle_get_provider_usage_summary(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get aggregated token usage and cost by provider for Credentials Modal."""
    database = container.database()
    providers = await database.get_provider_usage_summary()
    return {"success": True, "providers": providers}


# ============================================================================
# Agent Team Handlers
# ============================================================================

@ws_handler("workflow_id", "team_lead_node_id")
async def handle_create_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create a new agent team."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    team = await service.create_team(
        team_lead_node_id=data["team_lead_node_id"],
        teammate_node_ids=data.get("teammates", []),
        workflow_id=data["workflow_id"],
        config=data.get("config")
    )
    return {"team": team} if team else {"success": False, "error": "Failed to create team"}


@ws_handler("team_id")
async def handle_get_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team info."""
    database = container.database()
    team = await database.get_team(data["team_id"])
    return {"team": team} if team else {"success": False, "error": "Team not found"}


@ws_handler()
async def handle_get_team_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team status with stats.

    Can provide team_id directly, or team_lead_node_id to find active team.
    """
    from services.agent_team import get_agent_team_service

    try:
        service = get_agent_team_service()
    except RuntimeError:
        # Service not initialized - no active teams
        return {"status": {"members": [], "task_count": 0, "completed_count": 0, "active_count": 0, "pending_count": 0, "failed_count": 0, "active_tasks": []}}

    team_id = data.get("team_id")

    # If no team_id, try to find by team_lead_node_id
    if not team_id and data.get("team_lead_node_id"):
        # Look up active team for this workflow (most recent)
        # This is a simple approach - check if there's an active team with this lead
        # For now, return empty status - teams are created when AI Employee runs
        return {"status": {"members": [], "task_count": 0, "completed_count": 0, "active_count": 0, "pending_count": 0, "failed_count": 0, "active_tasks": [], "message": "No active team yet"}}

    if not team_id:
        return {"status": {"members": [], "task_count": 0, "completed_count": 0, "active_count": 0, "pending_count": 0, "failed_count": 0, "active_tasks": [], "message": "No team connected"}}

    status = await service.get_team_status(team_id)
    return {"status": status}


@ws_handler("team_id")
async def handle_dissolve_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Dissolve a team."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    success = await service.dissolve_team(data["team_id"], data.get("workflow_id"))
    return {"success": success}


@ws_handler("team_id", "title", "created_by")
async def handle_add_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Add task to team."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    task = await service.add_task(
        team_id=data["team_id"],
        title=data["title"],
        created_by=data["created_by"],
        description=data.get("description"),
        priority=data.get("priority", 3),
        depends_on=data.get("depends_on")
    )
    return {"task": task} if task else {"success": False, "error": "Failed to add task"}


@ws_handler("team_id", "task_id", "agent_node_id")
async def handle_claim_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Claim a task."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    task = await service.claim_task(data["team_id"], data["task_id"], data["agent_node_id"])
    return {"task": task} if task else {"success": False, "error": "Task unavailable"}


@ws_handler("team_id", "task_id")
async def handle_complete_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Complete a task."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    success = await service.complete_task(data["team_id"], data["task_id"], data.get("result"))
    return {"success": success}


@ws_handler("team_id")
async def handle_get_team_tasks(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team tasks."""
    database = container.database()
    tasks = await database.get_team_tasks(data["team_id"], data.get("status"))
    return {"tasks": tasks}


@ws_handler("team_id", "from_agent", "content")
async def handle_send_team_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send message in team."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    msg = await service.send_message(
        team_id=data["team_id"],
        from_agent=data["from_agent"],
        content=data["content"],
        to_agent=data.get("to_agent"),
        message_type=data.get("message_type", "direct")
    )
    return {"message": msg} if msg else {"success": False, "error": "Failed to send message"}


@ws_handler("team_id")
async def handle_get_team_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team messages."""
    from services.agent_team import get_agent_team_service
    service = get_agent_team_service()
    messages = await service.get_messages(
        team_id=data["team_id"],
        agent_node_id=data.get("agent_node_id"),
        unread_only=data.get("unread_only", False)
    )
    return {"messages": messages}


# ============================================================================
# Model Registry Handlers
# ============================================================================

@ws_handler()
async def handle_get_model_constraints(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get model constraints (max_output_tokens, temperature range, thinking support, etc.)."""
    from services.model_registry import get_model_registry
    registry = get_model_registry()
    model = data.get("model", "")
    provider = data.get("provider", "")
    if not model or not provider:
        return {"success": False, "error": "model and provider are required"}
    constraints = registry.get_model_constraints(model, provider)
    return {"success": True, **constraints}


@ws_handler()
async def handle_refresh_model_registry(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Force refresh model registry from OpenRouter."""
    from services.model_registry import get_model_registry
    registry = get_model_registry()
    try:
        count = await registry.refresh()
        return {"success": True, "model_count": count}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# Message Router
# ============================================================================

# Plugin packages (nodes/<group>/__init__.py) self-register their WS
# handlers into ``services.ws_handler_registry`` at import time. This
# router doesn't know any plugin's message-type names — it consults the
# registry at dispatch time via :func:`_resolve_handler`.
from services.ws_handler_registry import get_ws_handlers


def _resolve_handler(msg_type: str):
    """Resolve a WS message_type to its handler.

    Looks first in the legacy core ``MESSAGE_HANDLERS`` table, then
    falls back to the plugin-owned registry. Plugin handlers are
    discovered at import time (via the side-effect ``import nodes`` in
    ``main.lifespan``), so by the time messages flow they're registered.
    """
    handler = MESSAGE_HANDLERS.get(msg_type)
    if handler is None:
        handler = get_ws_handlers().get(msg_type)
    return handler


MESSAGE_HANDLERS: Dict[str, MessageHandler] = {
    # Status/ping
    "ping": handle_ping,
    "get_status": handle_get_status,
    "get_android_status": handle_get_android_status,
    "get_node_status": handle_get_node_status,
    "get_variable": handle_get_variable,

    # Node parameters
    "get_node_parameters": handle_get_node_parameters,
    "get_all_node_parameters": handle_get_all_node_parameters,
    "save_node_parameters": handle_save_node_parameters,
    "delete_node_parameters": handle_delete_node_parameters,

    # Tool schemas (source of truth for tool configurations)
    "get_tool_schema": handle_get_tool_schema,
    "save_tool_schema": handle_save_tool_schema,
    "delete_tool_schema": handle_delete_tool_schema,
    "get_all_tool_schemas": handle_get_all_tool_schemas,

    # Node output schemas (Pydantic-backed registry; see
    # docs-internal/schema_source_of_truth_rfc.md).
    "get_node_output_schema": handle_get_node_output_schema,
    # Wave 6 Phase 2: unified NodeSpec (input + output + metadata).
    "get_node_spec": handle_get_node_spec,
    "list_node_specs": handle_list_node_specs,
    # Wave 6 Phase 4: generic loadOptionsMethod dispatcher.
    "load_options": handle_load_options,
    "list_load_options_methods": handle_list_load_options_methods,
    # Wave 6 Phase 5: node-groups index (replaces *_NODE_TYPES helpers).
    "get_node_groups": handle_get_node_groups,

    # Credential registry (Nango-style bulk catalogue for credentials panel)
    "get_credential_catalogue": handle_get_credential_catalogue,

    # Node execution
    "execute_node": handle_execute_node,
    "execute_workflow": handle_execute_workflow,
    "validate_workflow": handle_validate_workflow,
    "cancel_execution": handle_cancel_execution,
    "get_workflow_status": handle_get_workflow_status,
    "get_node_output": handle_get_node_output,
    "clear_node_output": handle_clear_node_output,

    # Trigger/event waiting
    "cancel_event_wait": handle_cancel_event_wait,
    "get_active_waiters": handle_get_active_waiters,

    # Dead Letter Queue (DLQ) operations
    "get_dlq_entries": handle_get_dlq_entries,
    "get_dlq_entry": handle_get_dlq_entry,
    "get_dlq_stats": handle_get_dlq_stats,
    "replay_dlq_entry": handle_replay_dlq_entry,
    "remove_dlq_entry": handle_remove_dlq_entry,
    "purge_dlq": handle_purge_dlq,

    # Deployment operations
    "deploy_workflow": handle_deploy_workflow,
    "cancel_deployment": handle_cancel_deployment,
    "get_deployment_status": handle_get_deployment_status,
    "get_workflow_lock": handle_get_workflow_lock,
    "update_deployment_settings": handle_update_deployment_settings,

    # AI operations
    "execute_ai_node": handle_execute_ai_node,
    "get_ai_models": handle_get_ai_models,

    # API key operations
    "validate_api_key": handle_validate_api_key,
    "get_stored_api_key": handle_get_stored_api_key,
    "save_api_key": handle_save_api_key,
    "delete_api_key": handle_delete_api_key,

    # Claude OAuth operations
    "claude_oauth_login": handle_claude_oauth_login,
    "claude_oauth_status": handle_claude_oauth_status,

    # Twitter OAuth operations

    # Google Workspace OAuth operations

    # Android operations

    # Maps + Apify validation now flow through ``handle_validate_api_key``
    # (which dispatches via ``CREDENTIAL_REGISTRY`` to
    # ``GoogleMapsCredential._probe`` / ``ApifyCredential._probe``).
    # The legacy ``validate_maps_key`` / ``validate_apify_key`` WS message
    # types are no longer needed — the frontend already uses
    # ``validate_api_key`` for all providers.

    # WhatsApp operations

    # Telegram operations live in nodes/telegram/_handlers.py and
    # self-register via services.ws_handler_registry. Dispatch hits them
    # via _resolve_handler() defined above.

    # Workflow storage operations
    "save_workflow": handle_save_workflow,
    "import_workflow": handle_import_workflow,
    "get_workflow": handle_get_workflow,
    "get_all_workflows": handle_get_all_workflows,
    "delete_workflow": handle_delete_workflow,

    # Chat message (for chatTrigger nodes)
    "send_chat_message": handle_send_chat_message,
    "get_chat_messages": handle_get_chat_messages,
    "clear_chat_messages": handle_clear_chat_messages,
    "save_chat_message": handle_save_chat_message,

    # Console logs (for Console nodes)
    "get_console_logs": handle_get_console_logs,
    "clear_console_logs": handle_clear_console_logs,

    # Terminal logs
    "get_terminal_logs": handle_get_terminal_logs,
    "clear_terminal_logs": handle_clear_terminal_logs,

    # Process Manager
    "process_list": handle_process_list,
    "process_get_output": handle_process_get_output,
    "process_send_input": handle_process_send_input,

    # User Skills
    "get_user_skills": handle_get_user_skills,
    "get_user_skill": handle_get_user_skill,
    "create_user_skill": handle_create_user_skill,
    "update_user_skill": handle_update_user_skill,
    "delete_user_skill": handle_delete_user_skill,

    # Skill Content (built-in and user skills)
    "get_skill_content": handle_get_skill_content,
    "save_skill_content": handle_save_skill_content,
    "scan_skill_folder": handle_scan_skill_folder,
    "list_skill_folders": handle_list_skill_folders,
    "evaluate_auto_skill": handle_evaluate_auto_skill,
    "lookup_skill_metadata": handle_lookup_skill_metadata,

    # Memory and Skill Clear/Reset
    "clear_memory": handle_clear_memory,
    "reset_skill": handle_reset_skill,

    # User Settings
    "get_user_settings": handle_get_user_settings,
    "save_user_settings": handle_save_user_settings,

    # Provider Defaults
    "get_provider_defaults": handle_get_provider_defaults,
    "save_provider_defaults": handle_save_provider_defaults,
    "get_validated_ai_providers": handle_get_validated_ai_providers,
    "save_global_model": handle_save_global_model,

    # Compaction
    "get_compaction_stats": handle_get_compaction_stats,
    "configure_compaction": handle_configure_compaction,

    # Provider Usage Summary
    "get_provider_usage_summary": handle_get_provider_usage_summary,

    # Pricing Config
    "get_pricing_config": handle_get_pricing_config,
    "save_pricing_config": handle_save_pricing_config,
    "get_api_usage_summary": handle_get_api_usage_summary,

    # Node Allowlist (UI palette filter)
    "get_node_allowlist": handle_get_node_allowlist,

    # Model Registry
    "get_model_constraints": handle_get_model_constraints,
    "refresh_model_registry": handle_refresh_model_registry,

    # Agent Teams
    "create_team": handle_create_team,
    "get_team": handle_get_team,
    "get_team_status": handle_get_team_status,
    "dissolve_team": handle_dissolve_team,
    "add_team_task": handle_add_team_task,
    "claim_team_task": handle_claim_team_task,
    "complete_team_task": handle_complete_team_task,
    "get_team_tasks": handle_get_team_tasks,
    "send_team_message": handle_send_team_message,
    "get_team_messages": handle_get_team_messages,
}


async def _execute_handler(
    handler: MessageHandler,
    data: Dict[str, Any],
    websocket: WebSocket,
    msg_type: str,
    request_id: Optional[str]
):
    """Execute handler and send response using safe send."""
    try:
        result = await handler(data, websocket)

        if request_id:
            await _safe_send(websocket, {
                "type": f"{msg_type}_result",
                "request_id": request_id,
                **result
            })
        else:
            await _safe_send(websocket, result)

    except asyncio.CancelledError:
        # Task was cancelled (e.g., WebSocket disconnected)
        logger.debug(f"[WebSocket] Handler cancelled: {msg_type}")
        raise
    except Exception as e:
        logger.error("Handler error", msg_type=msg_type, error=str(e))
        if request_id:
            await _safe_send(websocket, {
                "type": f"{msg_type}_result",
                "request_id": request_id,
                "success": False,
                "error": str(e)
            })


@router.websocket("/ws/status")
async def websocket_status_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time bidirectional communication.

    Uses decoupled receive/process pattern with asyncio.Queue:
    - Receive task: continuously receives messages into queue (never blocks)
    - Process task: reads from queue and spawns handler tasks (can be long-running)

    This ensures cancel messages are always processed immediately, even when
    long-running handlers (like trigger node execution) are active.

    All client requests include a request_id for correlation.
    The server responds with the same request_id for request/response matching.
    Broadcasts (without request_id) are sent to all connected clients.
    """
    # Authenticate via cookie before accepting connection
    settings = container.settings()

    # Check if auth is disabled (VITE_AUTH_ENABLED=false)
    auth_disabled = settings.vite_auth_enabled and settings.vite_auth_enabled.lower() == 'false'

    if not auth_disabled:
        # Auth enabled - verify token
        token = websocket.cookies.get(settings.jwt_cookie_name)

        if not token:
            await websocket.close(code=4001, reason="Not authenticated")
            return

        user_auth = container.user_auth_service()
        payload = user_auth.verify_token(token)

        if not payload:
            await websocket.close(code=4001, reason="Invalid or expired session")
            return

    broadcaster = get_status_broadcaster()
    await broadcaster.connect(websocket)

    # Message queue for decoupling receive from processing
    message_queue: asyncio.Queue = asyncio.Queue()

    # Track handler tasks for this WebSocket
    handler_tasks: Set[asyncio.Task] = set()
    _handler_tasks[websocket] = handler_tasks

    async def receive_loop():
        """Receives messages and puts them in queue - never blocks on handlers."""
        try:
            while True:
                data = await websocket.receive_json()
                await message_queue.put(data)
        except WebSocketDisconnect:
            # Don't log here - logging during shutdown can raise KeyboardInterrupt
            await message_queue.put(None)  # Signal shutdown
        except asyncio.CancelledError:
            # Task cancelled during shutdown - this is expected
            await message_queue.put(None)
            raise
        except Exception as e:
            # Only log if it's not a shutdown-related error
            if not isinstance(e, (KeyboardInterrupt, SystemExit)):
                logger.error(f"[WebSocket] Receive error: {e}")
            await message_queue.put(None)

    async def process_loop():
        """Processes messages from queue - spawns handler tasks that can run concurrently."""
        while True:
            data = await message_queue.get()

            if data is None:  # Shutdown signal
                break

            msg_type = data.get("type", "")
            request_id = data.get("request_id")

            logger.debug("WebSocket message received", msg_type=msg_type, has_request_id=bool(request_id))

            handler = _resolve_handler(msg_type)

            if handler:
                # Run handler as task so it doesn't block queue processing
                # This allows cancel_event_wait to be processed while execute_node is waiting
                task = asyncio.create_task(
                    _execute_handler(handler, data, websocket, msg_type, request_id)
                )
                handler_tasks.add(task)
                task.add_done_callback(handler_tasks.discard)
            else:
                logger.warning("Unknown message type", msg_type=msg_type)
                if request_id:
                    await _safe_send(websocket, {
                        "type": "error",
                        "request_id": request_id,
                        "code": "UNKNOWN_MESSAGE_TYPE",
                        "message": f"Unknown message type: {msg_type}"
                    })

    try:
        # Run receive and process loops concurrently using TaskGroup (Python 3.11+)
        async with asyncio.TaskGroup() as tg:
            tg.create_task(receive_loop())
            tg.create_task(process_loop())

    except* WebSocketDisconnect:
        pass  # Normal disconnect - don't log during shutdown
    except* asyncio.CancelledError:
        pass  # Task cancelled during shutdown - expected
    except* (KeyboardInterrupt, SystemExit):
        pass  # Server shutdown - don't log
    except* Exception as eg:
        for exc in eg.exceptions:
            if not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                logger.error(f"[WebSocket] TaskGroup error: {exc}")
    finally:
        # Cancel any running handler tasks on disconnect
        for task in list(handler_tasks):
            if not task.done():
                task.cancel()

        # Wait for tasks to finish cancellation
        if handler_tasks:
            await asyncio.gather(*handler_tasks, return_exceptions=True)

        # Cleanup
        _handler_tasks.pop(websocket, None)
        await broadcaster.disconnect(websocket)


@router.websocket("/ws/internal")
async def websocket_internal_endpoint(websocket: WebSocket):
    """Internal WebSocket endpoint for Temporal workers.

    This endpoint bypasses authentication and is intended for internal
    service-to-service communication (e.g., Temporal activity -> MachinaOs).

    Security: Should only be exposed on localhost/internal network.
    """
    get_status_broadcaster()
    await websocket.accept()

    logger.info("[WebSocket Internal] Temporal worker connected")

    # Message queue for decoupling receive from processing
    message_queue: asyncio.Queue = asyncio.Queue()

    # Track handler tasks for this WebSocket
    handler_tasks: Set[asyncio.Task] = set()

    async def receive_loop():
        """Receives messages and puts them in queue."""
        try:
            while True:
                data = await websocket.receive_json()
                await message_queue.put(data)
        except WebSocketDisconnect:
            await message_queue.put(None)
        except asyncio.CancelledError:
            await message_queue.put(None)
            raise
        except Exception as e:
            if not isinstance(e, (KeyboardInterrupt, SystemExit)):
                logger.error(f"[WebSocket Internal] Receive error: {e}")
            await message_queue.put(None)

    async def process_loop():
        """Processes messages from queue."""
        while True:
            data = await message_queue.get()

            if data is None:
                break

            msg_type = data.get("type", "")
            request_id = data.get("request_id")

            handler = _resolve_handler(msg_type)

            if handler:
                task = asyncio.create_task(
                    _execute_handler(handler, data, websocket, msg_type, request_id)
                )
                handler_tasks.add(task)
                task.add_done_callback(handler_tasks.discard)
            else:
                logger.warning(f"[WebSocket Internal] Unknown message type: {msg_type}")
                if request_id:
                    await _safe_send(websocket, {
                        "type": "error",
                        "request_id": request_id,
                        "code": "UNKNOWN_MESSAGE_TYPE",
                        "message": f"Unknown message type: {msg_type}"
                    })

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(receive_loop())
            tg.create_task(process_loop())

    except* WebSocketDisconnect:
        pass  # Normal disconnect
    except* asyncio.CancelledError:
        pass  # Task cancelled during shutdown
    except* (KeyboardInterrupt, SystemExit):
        pass  # Server shutdown
    except* Exception as eg:
        for exc in eg.exceptions:
            if not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                logger.error(f"[WebSocket Internal] TaskGroup error: {exc}")
    finally:
        for task in list(handler_tasks):
            if not task.done():
                task.cancel()

        if handler_tasks:
            await asyncio.gather(*handler_tasks, return_exceptions=True)


@router.get("/ws/info")
async def websocket_info():
    """Get WebSocket connection info."""
    broadcaster = get_status_broadcaster()
    return {
        "endpoint": "/ws/status",
        "connected_clients": broadcaster.connection_count,
        "current_status": broadcaster.get_status(),
        "supported_message_types": sorted(
            set(MESSAGE_HANDLERS.keys()) | set(get_ws_handlers().keys())
        ),
    }
