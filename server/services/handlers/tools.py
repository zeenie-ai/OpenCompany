"""Tool execution handlers for AI Agent tool calling.

This module contains handlers for executing tools called by the AI Agent.
Each tool type has its own handler function that processes the tool call
and returns results.
"""

import asyncio
import uuid
import hashlib
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING

from core.logging import get_logger
from constants import AI_AGENT_TYPES, ANDROID_SERVICE_NODE_TYPES

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def _plugin_connection_factory(plugin_cls, context):
    """Build a per-call :class:`Connection` factory bound to a plugin
    class's declared credentials. Mirrors
    ``services.plugin.base._make_connection_factory`` but usable from
    this module without a circular import.
    """
    from services.plugin.connection import Connection

    user_id = context.get("user_id", "owner")
    session_id = context.get("session_id", "default")
    creds_by_id = {c.id: c for c in plugin_cls.credentials}

    def factory(credential_id: str):
        cred_cls = creds_by_id.get(credential_id)
        if cred_cls is None:
            raise RuntimeError(f"Plugin {plugin_cls.type} did not declare credential " f"'{credential_id}' but tried to use it.")
        return Connection(cred_cls, user_id=user_id, session_id=session_id)

    return factory


# Track running delegated tasks for status checking
_delegated_tasks: Dict[str, asyncio.Task] = {}

# In-memory cache of delegation results (fast path, survives task cleanup)
# Follows Celery AsyncResult / Ray ObjectRef pattern
_delegation_results: Dict[str, Dict[str, Any]] = {}

# Track active delegations to prevent duplicate calls: (parent_node_id, child_node_id, task_hash) -> task_id
_active_delegations: Dict[Tuple[str, str, str], str] = {}

# Ref-counted set of child node ids with an in-flight fire-and-forget
# delegation. Queried by ``StatusBroadcaster._clear_stuck_node_statuses``
# so the post-run cleanup that protects against glow-leaks on crash paths
# does NOT wipe the glow of a child agent whose background task is still
# running after its parent's workflow run completed. A node can host
# multiple concurrent delegations (rare but possible), hence the refcount
# instead of a plain set.
_active_delegated_nodes: Dict[str, int] = {}

# Legacy (non-Temporal) executions are single-process, so an asyncio
# semaphore provides the same bounded fan-out contract as AgentWorkflow.
# Temporal owns its own durable scheduling path. Semaphores are scoped by the
# root execution and intentionally exclude the root agent itself.
_delegation_semaphores: Dict[str, asyncio.Semaphore] = {}


def is_node_in_active_delegation(node_id: str) -> bool:
    """Return True iff ``node_id`` has at least one in-flight
    fire-and-forget delegation. See ``_active_delegated_nodes`` docstring."""
    return _active_delegated_nodes.get(node_id, 0) > 0


async def execute_tool(tool_name: str, tool_args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool by name and own the tool node's status lifecycle.

    This is the single source of truth for the tool node's
    ``executing``/``success``/``error`` broadcasts. Caller-side
    ``tool_executor`` closures in :mod:`services.ai` only emit
    *parent-agent* phase broadcasts (``executing_tool``/``tool_completed``)
    and must not duplicate the tool-node lifecycle here.

    Handlers that own their own asynchronous lifecycle opt out of the
    terminal ``success`` broadcast by returning a dict with
    ``status in {"delegated", "ALREADY_DELEGATED"}`` — the canonical
    contract for fire-and-forget agent delegation
    (:func:`_execute_delegated_agent` then drives the child's
    executing/success/error timeline from inside its background task).

    Args:
        tool_name: Name of the tool (for logging + broadcast messages).
        tool_args: Arguments provided by the AI model.
        config: Tool configuration containing ``node_type``, ``node_id``,
            ``workflow_id``, ``parameters``, and any injected services.

    Returns:
        Tool execution result dict (re-raises on dispatch failure after
        emitting the ``error`` broadcast).
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    node_id = config.get("node_id")
    workflow_id = config.get("workflow_id")

    if node_id and broadcaster:
        await broadcaster.update_node_status(
            node_id,
            "executing",
            {"message": f"Executing {tool_name}"},
            workflow_id=workflow_id,
        )

    try:
        result = await _dispatch_tool(tool_name, tool_args, config)
    except Exception as e:
        logger.error("[Tool] Execution failed: %s", tool_name, exc_info=True)
        if node_id and broadcaster:
            await broadcaster.update_node_status(
                node_id,
                "error",
                {"message": f"{tool_name} failed", "error": str(e)},
                workflow_id=workflow_id,
            )
        raise

    # Awaited-delegation results (delegation_wait_seconds > 0) carry a
    # delegation_lifecycle marker: run_child_agent already broadcast the
    # child node's terminal status, so a duplicate `success` here would
    # stomp an `error` glow. pop() keeps the marker out of the payload
    # the LLM sees.
    handler_owns_lifecycle = isinstance(result, dict) and (
        result.get("status") in ("delegated", "ALREADY_DELEGATED") or result.pop("delegation_lifecycle", False)
    )
    if node_id and broadcaster and not handler_owns_lifecycle:
        await broadcaster.update_node_status(
            node_id,
            "success",
            {"message": f"{tool_name} completed", "result": result},
            workflow_id=workflow_id,
        )
    return result


async def _dispatch_tool(tool_name: str, tool_args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Route a tool call to the right handler based on ``node_type``.

    Pure dispatch — no broadcasting. Lifecycle is owned by
    :func:`execute_tool`.
    """
    node_type = config.get("node_type", "")
    node_params = config.get("parameters", {})

    # Execution context for tool handlers. Includes canvas state
    # (nodes/edges/workflow_id) that callers (chat_tool_executor in
    # ai.py / rlm adapter) plumb in via `config`.
    # Tools that need canvas awareness (e.g. agentBuilder.inspect_canvas)
    # read these via NodeContext.nodes / .edges / .workflow_id; tools
    # that don't simply ignore them.
    context = {
        "workspace_dir": config.get("workspace_dir", ""),
        "nodes": config.get("nodes", []),
        "edges": config.get("edges", []),
        "workflow_id": config.get("workflow_id"),
        "parent_node_id": config.get("parent_node_id"),
        # Stable per-run id so session-keyed tools (browser) reuse one
        # instance across the agent loop instead of falling back to a
        # shared default session.
        "execution_id": config.get("execution_id"),
    }

    logger.info("[Tool] Executing '%s' (node_type=%s, workspace=%s)", tool_name, node_type, context["workspace_dir"])

    # ----------------------------------------------------------------
    # Plugin fast-path (Wave 11.B.1): if this node_type is a
    # BaseNode subclass, invoke BaseNode.execute_as_tool() — the
    # plugin is the single source of truth for both workflow-node
    # execution and AI-tool invocation. Legacy branches below only
    # fire for node types not yet migrated to the plugin pattern.
    #
    # Exception: AI agent types must fall through to the delegation
    # branch below. ``execute_as_tool`` runs the plugin synchronously
    # without resolving the child's api_key from the credentials DB,
    # whereas ``_execute_delegated_agent`` injects credentials, applies
    # duplicate-call de-dupe, and spawns the child as a fire-and-forget
    # background task — the contract callers (and the agent system
    # prompt) expect for delegation tools.
    # ----------------------------------------------------------------
    from services.node_registry import get_node_class

    plugin_cls = None if node_type in AI_AGENT_TYPES else get_node_class(node_type)
    if plugin_cls is not None:
        from services.plugin import NodeContext

        instance = plugin_cls()
        ctx = NodeContext.from_legacy(
            node_id=config.get("node_id", f"tool_{node_type}"),
            node_type=node_type,
            context={**context, "parameters": node_params},
            connection_factory=_plugin_connection_factory(plugin_cls, context),
        )
        return await instance.execute_as_tool(tool_args, node_params, ctx)

    # Wave 11.E.2: every per-type legacy dispatch branch retired —
    # the plugin fast-path above intercepts every registered plugin
    # (httpRequest / pythonExecutor / javascriptExecutor / currentTime
    # / filesystem / writeTodos / processManager / taskManager /
    # browser / email{Send,Read} / proxy{Request,Status,Config}).
    # Branches below are only for built-ins without a plugin
    # representation or cases needing special argument translation.

    # Direct Android service nodes need LLM-arg translation that doesn't
    # fit the plugin Params shape.
    if node_type in ANDROID_SERVICE_NODE_TYPES:
        from nodes.android._base import execute_android_service_tool

        return await execute_android_service_tool(tool_args, config)

    # taskManager: dispatcher reads through to the agent-delegation
    # tracking dicts (_delegated_tasks / _delegation_results) defined
    # below in this module. The operation matrix moved to the plugin
    # (Wave 11.I, milestone O).
    if node_type == "taskManager":
        from nodes.tool.task_manager import _execute_task_manager

        return await _execute_task_manager(tool_args, config)

    # proxyConfig: 10-operation matrix lives on the plugin
    # (nodes/proxy/proxy_config.execute_proxy_config). Plugin params win.
    if node_type == "proxyConfig":
        from nodes.proxy.proxy_config import execute_proxy_config

        merged = {**config.get("parameters", {}), **tool_args}
        return await execute_proxy_config(merged)

    # Built-in: Check delegated task results
    # Auto-injected when parent has delegation tools
    if node_type == "_builtin_check_delegated_tasks":
        return await _execute_check_delegated_tasks(tool_args, config)

    # AI Agent delegation (fire-and-forget async delegation).
    # ``AI_AGENT_TYPES`` (imported above from ``constants``) is the
    # canonical 18-entry frozenset; a new agent type added there picks
    # up delegation support without touching this dispatcher.
    if node_type in AI_AGENT_TYPES:
        return await _execute_delegated_agent(tool_args, config)

    # Generic fallback for unknown node types
    logger.warning(f"[Tool] Unknown tool type: {node_type}, using generic handler")
    return await _execute_generic(tool_args, config)


# _execute_calculator: deleted in Wave 11.C cleanup. Logic moved to
# nodes/tool/calculator_tool.py CalculatorToolNode.calculate().


# _execute_duckduckgo_search: Wave 11.C moved logic to
# nodes/search/duckduckgo_search.py DuckDuckGoSearchNode.search(). Wave
# 11.I milestone O moved the flat (args, config) -> dict shim used by
# contract tests to tests/nodes/_compat.py -- production code never
# called it.
#
# Wave 11.D.9: _execute_whatsapp_{send,db} deleted. WhatsApp tool execution
# now routes through the plugin fast-path (nodes/whatsapp/*.py).


# Wave 11.D.10: _execute_geocoding / _execute_nearby_places deleted.
# gmaps_locations + gmaps_nearby_places now route through the plugin
# fast-path (nodes/location/*.py).


# _execute_google_gmail: deleted in Wave 11.C cleanup. gmail now routes
# through the plugin fast-path (nodes/google/gmail.py).


# Wave 11.D.4: _execute_google_{calendar,drive,sheets,tasks,contacts} deleted.
# Google Workspace tool execution now routes through the plugin fast-path
# (nodes/google/*.py). These functions were defined but unreferenced.


async def _execute_generic(args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a generic tool (fallback handler).

    For node types without specific handlers, this returns the input
    along with node information.

    Args:
        args: Tool arguments
        config: Tool configuration

    Returns:
        Dict with input echoed and node info
    """
    return {
        "input": args.get("input", ""),
        "node_type": config.get("node_type"),
        "node_id": config.get("node_id"),
        "message": "Generic tool executed - no specific handler for this node type",
    }


async def _execute_delegated_agent(args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Delegate a task to a child AI Agent (fire-and-forget pattern).

    This spawns the child agent as an asyncio background task and returns
    immediately, allowing the parent agent to continue working.

    Args:
        args: Dict with 'task' and optional 'context'
        config: Tool config with node_id, node_type, parameters, ai_service, database,
                nodes, edges, workflow_id

    Returns:
        Immediate acknowledgment with task_id (child runs in background)
    """
    from services.status_broadcaster import get_status_broadcaster

    node_id = config.get("node_id")
    node_type = config.get("node_type")
    workflow_id = config.get("workflow_id")
    task_description = args.get("task", "")
    task_context = args.get("context", "")

    # Get injected services
    ai_service = config.get("ai_service")
    database = config.get("database")
    nodes = config.get("nodes", [])
    edges = config.get("edges", [])

    if not ai_service or not database:
        return {
            "error": "Agent delegation requires ai_service and database in config",
            "hint": "Ensure nodes/edges are passed to tool config",
        }

    # Get parent node ID for duplicate tracking
    parent_node_id = config.get("parent_node_id", "")

    # Opt-in blocking wait (delegation_wait_seconds in config): bridged
    # cloud agents (vertex_managed_agent) cannot poll cheaply — a
    # check_delegated_tasks round trip costs a full Interactions API turn
    # — so they await the child inline and fall back to the polling
    # contract only on timeout. Absent key = fire-and-forget (native
    # agent loop, unchanged).
    wait_seconds = float(config.get("delegation_wait_seconds") or 0)

    # Generate hash of task to detect duplicate delegation attempts
    task_hash = hashlib.md5(f"{task_description}:{task_context}".encode()).hexdigest()[:16]
    delegation_key = (parent_node_id, node_id, task_hash)

    # Check for duplicate delegation (prevents LLM from calling same delegation twice)
    existing_task_id = _active_delegations.get(delegation_key)
    if existing_task_id:
        logger.warning(f"[Delegated Agent] Duplicate delegation detected: task_hash={task_hash}, existing_task_id={existing_task_id}")
        if wait_seconds > 0:
            # Re-call after a timed-out wait: await the existing in-flight
            # task instead of duplicating work.
            resolved = await wait_for_delegation(existing_task_id, timeout=wait_seconds, database=database)
            if resolved is not None:
                return _delegation_result_reply(existing_task_id, resolved)
        return {
            "success": True,
            "status": "ALREADY_DELEGATED",
            "task_id": existing_task_id,
            "agent_name": config.get("parameters", {}).get("label", node_type),
            "result": (
                f"This task was ALREADY delegated (task_id: {existing_task_id}). "
                f"Do NOT call this tool again. Use 'check_delegated_tasks' to check status."
            ),
        }

    # Generate unique task ID
    task_id = f"delegated_{node_id}_{uuid.uuid4().hex[:8]}"

    root_execution_id = str(config.get("root_execution_id") or config.get("execution_id") or workflow_id or parent_node_id)
    delegation_depth = int(config.get("delegation_depth") or 0)
    from core.config import Settings as _DelegationSettings

    delegation_settings = _DelegationSettings()
    maybe_user_settings = database.get_user_settings()
    user_settings = await maybe_user_settings if hasattr(maybe_user_settings, "__await__") else None
    max_depth = int(
        config.get("max_delegation_depth")
        or (user_settings or {}).get("max_delegation_depth")
        or delegation_settings.max_delegation_depth
    )
    if delegation_depth >= max_depth:
        return {
            "success": False,
            "status": "error",
            "error": f"Maximum delegation depth {max_depth} exceeded",
        }

    max_concurrency = max(
        1,
        int(
            config.get("max_concurrent_subagents")
            or (user_settings or {}).get("max_concurrent_subagents")
            or delegation_settings.max_concurrent_subagents
        ),
    )
    semaphore = _delegation_semaphores.setdefault(root_execution_id, asyncio.Semaphore(max_concurrency))

    # Team-handle delegation participates in the durable AgentTeam lifecycle.
    # Ordinary agent-as-tool edges keep their historical standalone behavior.
    team_id: Optional[str] = config.get("team_id")
    is_team_delegation = any(
        edge.get("source") == node_id
        and edge.get("target") == parent_node_id
        and (edge.get("targetHandle") or edge.get("target_handle")) == "input-teammates"
        for edge in edges
    )
    team_service = None
    if is_team_delegation:
        from services.agent_team import get_agent_team_service
        from services.plugin.edge_walker import collect_teammate_connections

        team_service = get_agent_team_service()
        parent_node = next((item for item in nodes if item.get("id") == parent_node_id), {})
        teammates = await collect_teammate_connections(
            parent_node_id,
            {"nodes": nodes, "edges": edges, "workflow_id": workflow_id},
            database,
        )
        execution_key = str(config.get("execution_id") or root_execution_id)
        team = await team_service.get_or_create_execution_team(
            team_lead_node_id=parent_node_id,
            teammates=teammates,
            workflow_id=str(workflow_id or "default"),
            execution_id=execution_key,
            root_execution_id=root_execution_id,
            team_lead_type=parent_node.get("type", "orchestrator_agent"),
            team_lead_label=(parent_node.get("data") or {}).get("label"),
            config={"mode": "parallel", "max_concurrent_subagents": max_concurrency},
        )
        team_id = (team or {}).get("team_id") or (team or {}).get("id")
        if not team_id:
            return {"success": False, "status": "error", "error": "Failed to persist agent execution team"}
        persisted = await team_service.add_task(
            team_id,
            title=task_description[:500] or f"Delegation to {node_type}",
            description=task_context or task_description,
            created_by=parent_node_id,
            task_id=task_id,
        )
        if not persisted:
            return {"success": False, "status": "error", "error": "Failed to persist delegated team task"}

    # Register this delegation to prevent duplicates
    _active_delegations[delegation_key] = task_id

    # Mark this child node as having a live delegation so the workflow's
    # post-run cleanup (StatusBroadcaster._clear_stuck_node_statuses) does
    # not reset its glow to idle while the background task is still
    # working — the parent's workflow run completes the moment the
    # delegate_to_<x> tool returns, but the child runs ~tens of seconds
    # longer in its own asyncio task.
    _active_delegated_nodes[node_id] = _active_delegated_nodes.get(node_id, 0) + 1

    # Get child agent parameters from database
    child_params = await database.get_node_parameters(node_id) or {}

    # Inject API key - delegated agents bypass NodeExecutor._inject_api_keys,
    # so we must resolve the key here from the credential store
    if not child_params.get("api_key"):
        from constants import detect_ai_provider

        provider = detect_ai_provider(node_type, child_params)
        key = await ai_service.auth.get_api_key(provider, "default")
        if key:
            child_params["api_key"] = key
            logger.debug(f"[Delegated Agent] Injected API key for provider={provider}")

    # Inject default model if not set
    if not child_params.get("model"):
        from constants import detect_ai_provider

        provider = detect_ai_provider(node_type, child_params)
        models = await ai_service.auth.get_stored_models(provider, "default")
        if models:
            child_params["model"] = models[0]

    # Task goes into system_message (directive), context data goes into prompt.
    # Schema-canonical key is snake_case; drop any pre-migration camelCase
    # mirror so the saved-params dict downstream uses the canonical key.
    child_params["system_message"] = task_description
    child_params.pop("systemMessage", None)
    child_params["prompt"] = task_context if task_context else task_description

    # Create execution context for child agent. Forward the parent's
    # execution_id so session-keyed tools (browser) used by the child
    # share the parent run's instance.
    child_context = {
        "nodes": nodes,
        "edges": edges,
        "workflow_id": workflow_id,
        "outputs": {},
        "parent_task_id": task_id,
        "execution_id": config.get("execution_id"),
        "root_execution_id": root_execution_id,
        "parent_node_id": parent_node_id,
        "delegation_depth": delegation_depth + 1,
        "team_id": team_id,
        "team_task_id": task_id if team_id else None,
        "trace_id": task_id,
    }

    broadcaster = get_status_broadcaster()
    agent_label = child_params.get("label", node_type)

    logger.info(f"[Delegated Agent] Starting task {task_id} for '{agent_label}' (node: {node_id})")
    logger.debug(
        f"[Delegated Agent] Context: {len(nodes)} nodes, {len(edges)} edges, " f"edge_targets={set(e.get('target') for e in edges)}"
    )

    async def finalize_team_task(*, result_data: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        if not team_service or not team_id:
            return
        if error is not None:
            await team_service.fail_task(team_id, task_id, error)
            event_type, message_type, content = "failed", "task_failed", error
        else:
            await team_service.complete_task(team_id, task_id, result_data)
            event_type, message_type, content = "completed", "task_complete", str((result_data or {}).get("result", ""))
        await team_service.send_message(
            team_id,
            from_agent=node_id,
            to_agent=parent_node_id,
            content=content,
            message_type=message_type,
            event_id=f"{task_id}:{event_type}",
            extra_data={"task_id": task_id, "root_execution_id": root_execution_id, **(result_data or {})},
        )
        if await team_service.is_team_done(team_id):
            tasks = await team_service.database.get_team_tasks(team_id)
            final_status = "failed" if any(item.get("status") == "failed" for item in tasks) else "completed"
            await team_service.database.update_team_status(team_id, final_status)

    # Define the background coroutine
    async def run_child_agent():
        durable_permit_acquired = False
        try:
            async with semaphore:
                if team_service and team_id:
                    while not await team_service.acquire_subagent_permit(
                        root_execution_id, task_id, max_concurrency
                    ):
                        await asyncio.sleep(0.25)
                    durable_permit_acquired = True
                    claimed = await team_service.claim_task(team_id, task_id, node_id)
                    if not claimed:
                        raise RuntimeError("Delegated team task could not be claimed")
                    await team_service.send_message(
                        team_id,
                        from_agent=parent_node_id,
                        to_agent=node_id,
                        content=task_description,
                        message_type="task_assignment",
                        event_id=f"{task_id}:assigned",
                        extra_data={"task_id": task_id, "root_execution_id": root_execution_id},
                    )
            # Broadcast that child agent is starting
                await broadcaster.update_node_status(
                    node_id,
                    "executing",
                    {"phase": "delegated_task", "task_id": task_id, "message": f"Working on: {task_description[:100]}..."},
                    workflow_id=workflow_id,
                )

            # Execute the child agent via its own plugin class.
            # Wave 11.E.3: replaces the legacy handle_ai_agent /
            # handle_chat_agent imports — every agent type already owns
            # an @Operation method that wraps prepare_agent_call +
            # AIService dispatch, so we just go through BaseNode.execute.
                from services.node_registry import get_node_class
                from services.plugin import NodeContext

                plugin_cls = get_node_class(node_type)
                if plugin_cls is None:
                    raise RuntimeError(f"Unknown delegated agent type: {node_type}")
                instance = plugin_cls()
                child_ctx = NodeContext.from_legacy(
                    node_id=node_id,
                    node_type=node_type,
                    context=child_context,
                )
                result = await instance.execute(node_id, child_params, child_ctx)

            logger.info(f"[Delegated Agent] Task {task_id} completed: success={result.get('success')}")

            # Check if child agent actually succeeded
            if not result.get("success"):
                # Child agent returned failure - treat as error
                error_msg = result.get("error", "Child agent returned failure")
                logger.warning(f"[Delegated Agent] Task {task_id} returned success=False: {error_msg}")

                await broadcaster.update_node_status(
                    node_id, "error", {"phase": "delegated_error", "task_id": task_id, "error": error_msg}, workflow_id=workflow_id
                )

                # Cache error for parent retrieval
                _delegation_results[task_id] = {
                    "task_id": task_id,
                    "status": "error",
                    "agent_name": agent_label,
                    "agent_node_id": node_id,
                    "result": None,
                    "error": error_msg,
                }

                # Persist error to DB
                if database:
                    await database.save_node_output(
                        node_id=node_id,
                        session_id=f"delegation_{task_id}",
                        output_name="delegation_result",
                        data={
                            "task_id": task_id,
                            "parent_node_id": config.get("parent_node_id", ""),
                            "agent_name": agent_label,
                            "status": "error",
                            "error": error_msg,
                        },
                    )

                # Dispatch error event for trigger nodes — Wave 12 B8.
                from nodes.agent._events import broadcast_agent_task_failed

                await broadcast_agent_task_failed(
                    task_id=task_id,
                    agent_name=agent_label,
                    agent_node_id=node_id,
                    parent_node_id=config.get("parent_node_id", ""),
                    workflow_id=workflow_id,
                    error=error_msg,
                )

                await finalize_team_task(error=error_msg)

                return result

            # Success case - extract response properly
            response_text = result.get("result", {}).get("response", "")
            if not response_text:
                # Fallback: try to stringify the result dict
                response_text = str(result.get("result", "")) if result.get("result") else "No response generated"

            # Broadcast completion
            response_preview = response_text[:200] if response_text else ""
            await broadcaster.update_node_status(
                node_id,
                "success",
                {"phase": "delegated_complete", "task_id": task_id, "result_summary": response_preview},
                workflow_id=workflow_id,
            )

            # Cache result for parent retrieval (Layer 2: in-memory)
            _delegation_results[task_id] = {
                "task_id": task_id,
                "status": "completed",
                "agent_name": agent_label,
                "agent_node_id": node_id,
                "result": response_text,
                "error": None,
            }

            # Persist to DB (Layer 3: cross-restart via existing NodeOutput)
            if database:
                await database.save_node_output(
                    node_id=node_id,
                    session_id=f"delegation_{task_id}",
                    output_name="delegation_result",
                    data={
                        "task_id": task_id,
                        "parent_node_id": config.get("parent_node_id", ""),
                        "agent_name": agent_label,
                        "status": "completed",
                        "result": response_text,
                    },
                )

            # Dispatch task_completed event for trigger nodes — Wave 12 B8.
            from nodes.agent._events import broadcast_agent_task_completed

            await broadcast_agent_task_completed(
                task_id=task_id,
                agent_name=agent_label,
                agent_node_id=node_id,
                parent_node_id=config.get("parent_node_id", ""),
                workflow_id=workflow_id,
                result=response_text,
            )

            await finalize_team_task(result_data={"result": response_text})

            return result

        except Exception as e:
            logger.error(f"[Delegated Agent] Task {task_id} failed: {e}")
            await broadcaster.update_node_status(
                node_id, "error", {"phase": "delegated_error", "task_id": task_id, "error": str(e)}, workflow_id=workflow_id
            )

            # Cache error for parent retrieval (Layer 2: in-memory)
            _delegation_results[task_id] = {
                "task_id": task_id,
                "status": "error",
                "agent_name": agent_label,
                "agent_node_id": node_id,
                "result": None,
                "error": str(e),
            }

            # Persist to DB (Layer 3: cross-restart)
            if database:
                await database.save_node_output(
                    node_id=node_id,
                    session_id=f"delegation_{task_id}",
                    output_name="delegation_result",
                    data={
                        "task_id": task_id,
                        "parent_node_id": config.get("parent_node_id", ""),
                        "agent_name": agent_label,
                        "status": "error",
                        "error": str(e),
                    },
                )

            # Dispatch task_completed event for trigger nodes (error case) — Wave 12 B8.
            from nodes.agent._events import broadcast_agent_task_failed

            await broadcast_agent_task_failed(
                task_id=task_id,
                agent_name=agent_label,
                agent_node_id=node_id,
                parent_node_id=config.get("parent_node_id", ""),
                workflow_id=workflow_id,
                error=str(e),
            )

            await finalize_team_task(error=str(e))

            return {"success": False, "error": str(e)}

        finally:
            if durable_permit_acquired and team_service:
                released = await team_service.release_subagent_permit(root_execution_id, task_id)
                if not released:
                    logger.error(
                        "[Delegated Agent] Failed to release durable permit %s for root %s",
                        task_id,
                        root_execution_id,
                    )
            # Cleanup task reference
            _delegated_tasks.pop(task_id, None)
            # Cleanup delegation tracking (allows re-delegation after completion)
            _active_delegations.pop(delegation_key, None)
            # Decrement active-delegation refcount so the broadcaster's
            # post-run cleanup can sweep this node's glow on the next
            # workflow run if it ever does get stuck.
            current = _active_delegated_nodes.get(node_id, 0)
            if current <= 1:
                _active_delegated_nodes.pop(node_id, None)
            else:
                _active_delegated_nodes[node_id] = current - 1

    # Spawn as background task (fire-and-forget)
    task = asyncio.create_task(run_child_agent())
    _delegated_tasks[task_id] = task

    if wait_seconds > 0:
        resolved = await wait_for_delegation(task_id, timeout=wait_seconds, task=task, database=database)
        if resolved is not None:
            return _delegation_result_reply(task_id, resolved)
        return {
            "success": True,
            "status": "delegated",
            "task_id": task_id,
            "agent_node_id": node_id,
            "agent_name": agent_label,
            "message": (
                f"'{agent_label}' is STILL WORKING after {int(wait_seconds)}s "
                f"(task_id: {task_id}). It continues in the background. "
                f"Do NOT call this tool again for this task. Use "
                f"'check_delegated_tasks' with task_id='{task_id}' to "
                f"retrieve the result later."
            ),
        }

    # Return immediately - Parent agent continues working
    return {
        "success": True,
        "status": "delegated",
        "task_id": task_id,
        "agent_node_id": node_id,
        "agent_name": agent_label,
        "message": (
            f"SUCCESS: Task delegated to '{agent_label}' (task_id: {task_id}). "
            f"Agent is now working INDEPENDENTLY in the background. "
            f"IMPORTANT: Delegation is COMPLETE. Do NOT call this tool again for this task. "
            f"To check results later, use 'check_delegated_tasks' with task_id='{task_id}'."
        ),
    }


def _delegation_result_reply(task_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a terminal delegation entry as an awaited tool result.

    ``delegation_lifecycle`` tells :func:`execute_tool` that
    ``run_child_agent`` already owns the child node's terminal broadcast.
    """
    completed = entry.get("status") == "completed"
    return {
        "success": completed,
        "status": entry.get("status"),  # "completed" | "error" | "not_found"
        "task_id": task_id,
        "agent_name": entry.get("agent_name"),
        "result": entry.get("result"),
        "error": entry.get("error"),
        "delegation_lifecycle": True,
    }


async def wait_for_delegation(
    task_id: str,
    *,
    timeout: float,
    task: Optional[asyncio.Task] = None,
    database=None,
) -> Optional[Dict[str, Any]]:
    """Block until a delegated task reaches a terminal state, or timeout.

    Returns the ``_delegation_results``-shaped entry on completion/error,
    or ``None`` when the child is still running after ``timeout``. The
    child is NEVER cancelled — ``asyncio.shield`` cancels only the waiter,
    so ``_delegation_results`` caching / taskTrigger events still fire
    later and the result stays retrievable via ``check_delegated_tasks``.
    """
    live = task if task is not None else _delegated_tasks.get(task_id)
    if live is not None:
        try:
            await asyncio.wait_for(asyncio.shield(live), timeout=timeout)
        except asyncio.TimeoutError:
            return None  # still working in the background
        except Exception:  # noqa: BLE001 — run_child_agent caches its own errors
            pass
    cached = _delegation_results.get(task_id)
    if cached:
        return cached
    # Cleanup race / cross-restart: 3-layer lookup (live -> cache -> DB).
    status = await get_delegated_task_status(task_ids=[task_id], database=database)
    entry = status["tasks"][0]
    return None if entry.get("status") == "running" else entry


async def get_delegated_task_status(task_ids: Optional[List[str]] = None, database=None) -> Dict[str, Any]:
    """Check status and retrieve results of delegated tasks.

    3-layer lookup: live tasks -> memory cache -> DB (NodeOutput).
    Follows Celery AsyncResult / Ray ObjectRef pattern.

    Args:
        task_ids: Optional list of specific task IDs to check. If None, returns all known tasks.
        database: Database instance for Layer 3 (SQLite) lookup.

    Returns:
        Dict with 'tasks' list containing status and results for each task.
    """
    if not task_ids:
        # Return all known from memory
        task_ids = list(set(list(_delegated_tasks.keys()) + list(_delegation_results.keys())))

    tasks = []
    db_lookup_ids = []

    for tid in task_ids:
        # Layer 1: Live asyncio.Task (still running or just finished)
        live_task = _delegated_tasks.get(tid)
        if live_task is not None:
            if not live_task.done():
                tasks.append({"task_id": tid, "status": "running"})
            else:
                # Task finished -- extract result via task.result()
                try:
                    result = live_task.result()
                    response = result.get("result", {}).get("response", str(result.get("result", "")))
                    tasks.append({"task_id": tid, "status": "completed", "result": response})
                except Exception as e:
                    tasks.append({"task_id": tid, "status": "error", "error": str(e)})
            continue

        # Layer 2: In-memory result cache
        cached = _delegation_results.get(tid)
        if cached:
            tasks.append(cached)
            continue

        # Layer 3: Need DB lookup
        db_lookup_ids.append(tid)

    # DB fallback for results not in memory (cross-restart)
    if db_lookup_ids and database:
        for tid in list(db_lookup_ids):
            db_result = await database.get_node_output_by_session(session_id=f"delegation_{tid}", output_name="delegation_result")
            if db_result:
                data = db_result.get("data", {})
                result_data = data.get("result", {})
                response_text = result_data.get("response", str(result_data)) if isinstance(result_data, dict) else str(result_data)
                tasks.append(
                    {
                        "task_id": tid,
                        "status": data.get("status", "completed"),
                        "agent_name": data.get("agent_name", ""),
                        "result": response_text,
                        "error": data.get("error"),
                    }
                )
                db_lookup_ids.remove(tid)

    # Remaining IDs not found anywhere
    for tid in db_lookup_ids:
        tasks.append({"task_id": tid, "status": "not_found"})

    return {"tasks": tasks}


async def _execute_check_delegated_tasks(args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-callable tool: check on delegated child agents.

    Returns status and results for previously delegated tasks.
    Follows Celery AsyncResult / Ray ObjectRef patterns.
    """
    task_ids = args.get("task_ids")
    database = config.get("database")
    result = await get_delegated_task_status(task_ids=task_ids, database=database)

    formatted = []
    for task in result.get("tasks", []):
        entry = {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "agent_name": task.get("agent_name"),
        }
        if task.get("status") == "completed":
            text = str(task.get("result", ""))
            entry["result"] = text[:4000] + "... [truncated]" if len(text) > 4000 else text
        elif task.get("status") == "error":
            entry["error"] = task.get("error")
        elif task.get("status") == "running":
            entry["message"] = f"Agent '{task.get('agent_name', 'unknown')}' is still working"
        formatted.append(entry)

    return {
        "total_tasks": len(formatted),
        "completed": sum(1 for t in formatted if t.get("status") == "completed"),
        "running": sum(1 for t in formatted if t.get("status") == "running"),
        "errors": sum(1 for t in formatted if t.get("status") == "error"),
        "tasks": formatted,
    }


# _execute_task_manager + handle_task_manager: Wave 11.I milestone O moved
# the operation matrix to nodes/tool/task_manager.py. The plugin reads
# through to the delegation registry (``_delegated_tasks`` /
# ``_delegation_results`` / ``get_delegated_task_status``) which still
# lives in this module -- delegation lifecycle is genuine cross-cutting
# framework state. ``handle_task_manager`` had no callers and was deleted.


# =============================================================================
# SEARCH TOOL WRAPPERS (for AI Agent tool calling)
# =============================================================================

# brave + perplexity tool wrappers: deleted in Wave 11.C cleanup. Both
# now route through the plugin fast-path in execute_tool which calls
# the BaseNode.execute_as_tool method on the plugin class.
# _execute_serper_search_tool stays until serperSearch is migrated.
