"""WebSocket Status Broadcaster Service.

Manages WebSocket connections and broadcasts status updates to all connected clients.
Supports all node types, variable updates, and workflow state changes.
"""

import asyncio
import orjson
from typing import Set, Dict, Any, Optional, List
from fastapi import WebSocket
from opentelemetry import trace

from core.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


class StatusBroadcaster:
    """Manages WebSocket connections and broadcasts status updates."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

        # Per-workflow active-run counter. Counts every concurrent
        # execution path (ad-hoc node run, whole-workflow run, deployed
        # trigger spawn, ...). The counter going 0->1 emits a
        # `workflow_status` broadcast with executing=true; 1->0 emits
        # executing=false. Prevents flapping while multiple runs overlap.
        self._workflow_active_runs: Dict[str, int] = {}
        self._workflow_active_lock = asyncio.Lock()

        # Per-workflow status cache for snapshot/resync
        self._workflow_statuses: Dict[str, Dict[str, Any]] = {}

        # Current state for all status types
        self._status: Dict[str, Any] = {
            "android": {
                "connected": False,
                "paired": False,
                "device_id": None,
                "device_name": None,
                "connected_devices": [],
                "connection_type": None,
                "qr_data": None,
                "session_token": None
            },
            "whatsapp": {
                "connected": False,
                "has_session": False,
                "running": False,
                "pairing": False,
                "device_id": None,
                "qr": None
            },
            "twitter": {
                "connected": False,
                "username": None,
                "user_id": None,
                "name": None,
                "profile_image_url": None
            },
            "google": {
                "connected": False,
                "email": None,
                "name": None,
            },
            "telegram": {
                "connected": False,
                "bot_id": None,
                "bot_username": None,
                "bot_name": None
            },
            "api_keys": {},  # provider -> validation status
            "nodes": {},  # node_id -> node status
            "variables": {},  # variable_name -> value
            "workflow": {
                "executing": False,
                "current_node": None
            },
            "workflow_lock": {
                "locked": False,
                "workflow_id": None,
                "locked_at": None,
                "reason": None
            },
            "deployment": {
                "isRunning": False,
                "activeRuns": 0,
                "status": "idle",
                "workflow_id": None
            }
        }

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection.

        Sends the cached status snapshot immediately. Status changes
        flow in via the originating code path (WhatsApp Go-service
        events, Telegram bot connect/disconnect, OAuth callbacks,
        Android relay events) -- the cache is kept fresh by those
        event-driven broadcasts, so the connecting client doesn't need
        a per-connect refresh-all storm.

        Initial cache population (and load-bearing auto-reconnects for
        Telegram + Android relay) happens in a one-time lifespan-startup
        invocation of :meth:`_refresh_all_services` from ``main.py``.
        """
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"[StatusBroadcaster] Client connected. Total: {len(self._connections)}")

        try:
            await websocket.send_json({
                "type": "initial_status",
                "data": self._status
            })
        except Exception as e:
            logger.error(f"[StatusBroadcaster] Failed to send initial status: {e}")

        # Reconcile snapshot — every fresh client gets the
        # currently-running-deployments truth so a stale FE
        # `deploymentStatus.isRunning=true` (carried forward through a
        # backend restart that wiped DeploymentManager._deployments)
        # gets cleared. CloudEvents-shaped envelope; empty list is
        # meaningful and triggers FE-side reset.
        try:
            await self._send_deployment_snapshot(websocket)
        except Exception as e:
            logger.error(f"[StatusBroadcaster] Failed to send deployment snapshot: {e}")

    async def _send_deployment_snapshot(self, websocket: WebSocket) -> None:
        """Build + send a CloudEvents deployment_snapshot to one client.

        Lazy container resolution because the broadcaster is constructed
        before WorkflowService and we do not want a circular dependency
        at module-load time.
        """
        try:
            from core.container import container
            from services.events import WorkflowEvent

            workflow_service = container.workflow_service()
            dm = workflow_service._get_deployment_manager()
            running_ids = dm.get_deployed_workflows()
        except Exception as e:
            # Backend startup race or DI not wired yet -- skip the snapshot
            # rather than fail the connect entirely. The empty-list path
            # would still be safer than crashing here.
            logger.debug(
                f"[StatusBroadcaster] DeploymentManager unavailable for snapshot: {e}",
            )
            return

        event = WorkflowEvent.deployment_snapshot(running_ids)
        await websocket.send_json({
            "type": "deployment_snapshot",
            "data": event.model_dump(mode="json"),
        })

    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"[StatusBroadcaster] Client disconnected. Total: {len(self._connections)}")

    async def _refresh_all_services(self):
        """Fan out plugin-registered refresh callbacks.

        Uses ``asyncio.TaskGroup`` (Python 3.11+) for structured
        concurrency: every refresh runs as an independent task and
        publishes its own ``<service>_status`` message the moment its
        cache slot is populated. The slowest service no longer gates
        the others -- the credentials/status UI hydrates incrementally.

        Wave 11.I, milestone J: every refresher now lives in its
        plugin folder (``nodes/<plugin>/_refresh.py``) and registers
        via :func:`register_service_refresh`. The broadcaster has zero
        per-plugin knowledge in this dispatch loop. Wave 11.I, V: this
        method runs ONCE at FastAPI lifespan startup (no longer per
        WS-client connect). State changes after that flow through the
        originating code path's event-driven broadcasts.

        Each callback swallows its own exceptions, so TaskGroup never
        sees one. Kept inside try/except* defensively in case a future
        refactor lets one escape.
        """
        with tracer.start_as_current_span("broadcaster.refresh_all_services"):
            try:
                async with asyncio.TaskGroup() as tg:
                    for callback in list(_SERVICE_REFRESH_CALLBACKS):
                        tg.create_task(callback(self))
            except* Exception as eg:
                for exc in eg.exceptions:
                    logger.warning("[StatusBroadcaster] Service refresh task failed: %s", exc)

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a message to all connected clients using TaskGroup.

        Uses asyncio.TaskGroup (Python 3.11+) for structured concurrency:
        - All tasks complete or cancel together
        - Proper exception handling via ExceptionGroup
        """
        if not self._connections:
            return

        # Get connections list while holding lock
        async with self._lock:
            connections_list = list(self._connections)

        if not connections_list:
            return

        message_bytes = orjson.dumps(message).decode()
        disconnected: set[WebSocket] = set()

        async def send_to_client(connection: WebSocket):
            """Send message to a single client."""
            try:
                await connection.send_text(message_bytes)
            except Exception as e:
                logger.warning(f"[StatusBroadcaster] Send failed: {e}")
                disconnected.add(connection)

        # Execute all sends concurrently with TaskGroup
        try:
            async with asyncio.TaskGroup() as tg:
                for conn in connections_list:
                    tg.create_task(send_to_client(conn))
        except* Exception as eg:
            # TaskGroup aggregates exceptions - log them but continue
            for exc in eg.exceptions:
                logger.warning(f"[StatusBroadcaster] TaskGroup exception: {exc}")

        # Remove failed connections
        if disconnected:
            async with self._lock:
                self._connections -= disconnected

    # =========================================================================
    # API Key Validation Status Updates
    # =========================================================================

    async def update_api_key_status(
        self,
        provider: str,
        valid: bool,
        message: Optional[str] = None,
        has_key: bool = True,
        models: Optional[List[str]] = None
    ):
        """Update API key validation status and broadcast."""
        self._status["api_keys"][provider] = {
            "valid": valid,
            "hasKey": has_key,
            "message": message,
            "models": models or [],
            "timestamp": asyncio.get_event_loop().time()
        }

        await self.broadcast({
            "type": "api_key_status",
            "provider": provider,
            "data": self._status["api_keys"][provider]
        })

    def get_api_key_status(self, provider: str) -> Optional[Dict[str, Any]]:
        """Get API key validation status for a provider."""
        return self._status["api_keys"].get(provider)

    # =========================================================================
    # Credential Mutation Broadcasts (CloudEvents v1.0)
    # =========================================================================
    #
    # Every backend handler that mutates credential state (store / remove API
    # keys, OAuth login / logout) MUST emit a credential event via this helper
    # so the frontend `useCatalogueQuery` cache stays coherent across all
    # connected clients. Wire-format key stays `credential_catalogue_updated`
    # for FE back-compat; the body is the CloudEvents-shaped `WorkflowEvent`
    # so future EventBridge / Knative interop is a JSON-schema swap.
    #
    # Pytest invariant `test_credential_broadcasts.py` locks the contract.

    async def broadcast_credential_event(
        self,
        event_type: str,
        *,
        provider: str,
        customer_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        **data_extra: Any,
    ) -> None:
        """Emit a CloudEvents-typed credential broadcast.

        Args:
            event_type: CloudEvents `type` field. Convention:
                ``"credential.api_key.saved"`` / ``".deleted"`` /
                ``".runtime_failed"``, ``"credential.oauth.connected"`` /
                ``".disconnected"`` / ``".runtime_failed"``.
            provider: Provider id (e.g. ``"openai"``, ``"twitter"``).
            customer_id: For multi-tenant OAuth flows. Default omitted.
            workflow_id: Optional CloudEvents extension attribute scoping
                runtime events to the workflow that triggered them.
            **data_extra: Additional fields merged into the envelope's
                ``data`` block (e.g. ``reason``, ``node_id``, ``error``
                for runtime failure events).
        """
        # Local import keeps the broadcaster module independent of the
        # event framework's load order during startup.
        from services.events import WorkflowEvent

        event = WorkflowEvent(
            source="machinaos://services/credentials",
            type=event_type,
            subject=provider,
            workflow_id=workflow_id,
            data={
                "provider": provider,
                **({"customer_id": customer_id} if customer_id else {}),
                **data_extra,
            },
        )

        await self.broadcast({
            "type": "credential_catalogue_updated",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_workflow_lifecycle(
        self,
        stage: str,
        *,
        workflow_id: str,
        **data_extra: Any,
    ) -> None:
        """Emit a CloudEvents-typed workflow.{stage} broadcast.

        Wraps :meth:`WorkflowEvent.workflow_lifecycle` so callers don't
        hand-build envelopes. ``stage`` matches the factory's Literal
        (``imported`` / ``deployment.started`` / etc.). Wire-format key
        is ``workflow_lifecycle`` — the frontend's ``WebSocketContext``
        routes on this key and invalidates the workflows query so the
        sidebar refreshes across all connected clients.
        """
        from services.events import WorkflowEvent

        event = WorkflowEvent.workflow_lifecycle(
            stage=stage,  # type: ignore[arg-type]
            workflow_id=workflow_id,
            data=data_extra or None,
        )
        await self.broadcast({
            "type": "workflow_lifecycle",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_node_parameters_updated(
        self,
        node_id: str,
        *,
        parameters: Dict[str, Any],
        workflow_id: Optional[str] = None,
        version: int = 1,
        source_hint: str = "user",
    ) -> None:
        """Emit a CloudEvents-typed ``node.parameters.updated`` event.

        Replaces three legacy raw-dict broadcast sites with one typed
        envelope (RFC §6.4 CloudEvents discipline). Wire-format key
        ``node_parameters_updated`` stays the same for FE back-compat;
        only the inner payload becomes the typed envelope.

        Callers:
          - ``routers/websocket.py:handle_save_node_parameters`` (user
            edited the parameter panel — ``source_hint="user"``).
          - ``services/cli_agent/service.py:_persist_memory`` (Claude
            Code CLI memory bridge appended a turn —
            ``source_hint="cli"``).
          - ``services/temporal/agent_activities.py:persist_agent_turn``
            (F4.B AgentWorkflow per-turn memory append —
            ``source_hint="agent"``).
        """
        from services.events import WorkflowEvent

        event = WorkflowEvent.node_parameters_updated(
            node_id,
            parameters=parameters,
            workflow_id=workflow_id,
            version=version,
            source_hint=source_hint,
        )
        await self.broadcast({
            "type": "node_parameters_updated",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_agent_progress(
        self,
        node_id: str,
        *,
        workflow_id: Optional[str],
        iteration: int,
        max_iterations: int,
        phase: Optional[str] = None,
    ) -> None:
        """Emit a CloudEvents-typed agent-progress event.

        Wire key ``agent_progress`` is a parallel channel to
        ``node_status``: same per-node scope, but the inner payload is a
        full CloudEvents envelope instead of a raw dict. The FE routes
        envelope.data into ``nodeStatusStore`` so the existing
        ``useNodeStatus`` consumers see ``iteration`` /
        ``max_iterations`` without a separate store.
        """
        from services.events import WorkflowEvent

        event = WorkflowEvent.agent_progress(
            node_id,
            workflow_id=workflow_id,
            iteration=iteration,
            max_iterations=max_iterations,
            phase=phase,
        )

        await self.broadcast({
            "type": "agent_progress",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_claude_session_spawned(
        self,
        memory_node_id: str,
        *,
        session_uuid: str,
        pid: int,
        workflow_id: Optional[str] = None,
    ) -> None:
        """Emit ``claude.session.spawned`` when a pooled claude is
        cold-started. Single wire key for all four session-lifecycle
        events; FE discriminates on envelope.type.
        """
        from services.events import WorkflowEvent

        event = WorkflowEvent.claude_session_spawned(
            memory_node_id,
            session_uuid=session_uuid,
            pid=pid,
            workflow_id=workflow_id,
        )
        await self.broadcast({
            "type": "claude_session_event",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_claude_session_cleared(
        self,
        memory_node_id: str,
        *,
        old_session_uuid: str,
        new_session_uuid: str,
        workflow_id: Optional[str] = None,
    ) -> None:
        """Emit ``claude.session.cleared`` after ``/clear`` minted a new
        session UUID. Carries both old + new so the FE can update
        any session-uuid display + warn if there are open references."""
        from services.events import WorkflowEvent

        event = WorkflowEvent.claude_session_cleared(
            memory_node_id,
            old_session_uuid=old_session_uuid,
            new_session_uuid=new_session_uuid,
            workflow_id=workflow_id,
        )
        await self.broadcast({
            "type": "claude_session_event",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_claude_session_terminated(
        self,
        memory_node_id: str,
        *,
        reason: str,
        session_uuid: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> None:
        """Emit ``claude.session.terminated`` when a pooled session
        ends. ``reason`` is one of ``idle / crashed / evicted /
        shutdown / explicit`` (typed enum in
        :class:`WorkflowEvent.claude_session_terminated`)."""
        from services.events import WorkflowEvent

        event = WorkflowEvent.claude_session_terminated(
            memory_node_id,
            reason=reason,  # type: ignore[arg-type]
            session_uuid=session_uuid,
            workflow_id=workflow_id,
        )
        await self.broadcast({
            "type": "claude_session_event",
            "data": event.model_dump(mode="json"),
        })

    async def broadcast_claude_session_usage(
        self,
        memory_node_id: str,
        *,
        session_uuid: str,
        total_cost_usd: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        duration_ms: Optional[int] = None,
        num_turns: Optional[int] = None,
        workflow_id: Optional[str] = None,
    ) -> None:
        """Emit ``claude.session.usage`` after each turn's ``result``
        event. Replaces the (unparseable) ``/usage`` TUI scrape with
        structured data straight from the JSONL ``result.usage`` block.
        FE renders a usage panel on simpleMemory by subscribing here."""
        from services.events import WorkflowEvent

        event = WorkflowEvent.claude_session_usage(
            memory_node_id,
            session_uuid=session_uuid,
            total_cost_usd=total_cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            duration_ms=duration_ms,
            num_turns=num_turns,
            workflow_id=workflow_id,
        )
        await self.broadcast({
            "type": "claude_session_usage",
            "data": event.model_dump(mode="json"),
        })

    # =========================================================================
    # Android Status Updates
    # =========================================================================

    async def _emit_connection_typed(
        self,
        plugin: str,
        connected: bool,
        subject: Optional[str],
        data: Dict[str, Any],
    ) -> None:
        """Emit a CloudEvents-typed sibling broadcast alongside the
        legacy ``{plugin}_status`` raw frame (Wave 11.I, X4).

        Wire key ``plugin_connection_status`` mirrors the
        ``credential_catalogue_updated`` pattern: single wire channel
        for typed-envelope listeners; the envelope's ``type`` field
        carries the specific action (``{plugin}.connection.opened`` /
        ``.closed``) and ``subject`` carries the device/account id.

        Frontend listeners that grew up on the raw ``{plugin}_status``
        frame keep working unchanged; future listeners can subscribe
        to ``plugin_connection_status`` for the typed channel and
        retire the raw frame in a coordinated FE migration (Phase 4 Y3).
        """
        from services.events import WorkflowEvent

        event = WorkflowEvent.connection_status(
            plugin=plugin,
            connected=connected,
            subject=subject,
            data=data,
        )
        await self.broadcast({
            "type": "plugin_connection_status",
            "data": event.model_dump(mode="json"),
        })

    # Wave 12 B1: ``update_android_status`` MOVED to
    # ``nodes/android/_events.py:broadcast_android_status``. Plugin
    # owns its broadcast shape + the dual-emit (legacy raw +
    # typed CloudEvents sibling). Status cache still lives in
    # ``self._status["android"]`` — the plugin's wrapper mutates it
    # so ``get_android_status()`` + the WS-connect initial-status
    # snapshot keep working unchanged.

    # =========================================================================
    # Status updates -- per-service refresh bodies live in their plugin
    # folders (Wave 11.I, milestone J): nodes/<plugin>/_refresh.py.
    # The plugin's __init__.py self-registers via
    # services.status_broadcaster.register_service_refresh; the central
    # _refresh_all_services fans out to every registered callback with
    # zero per-plugin knowledge.
    # =========================================================================

    # Wave 12 B2: ``update_whatsapp_status`` MOVED to
    # ``nodes/whatsapp/_events.py:broadcast_whatsapp_status``. Plus the
    # 7 send_custom_event callsites (message_sent/received, 4 newsletter
    # events, history_sync_complete) all moved to the plugin's
    # ``_events.py`` typed wrappers. Status cache slot stays here.

    # =========================================================================
    # Telegram Status Updates
    # =========================================================================

    async def update_telegram_status(
        self,
        connected: bool,
        bot_id: Optional[int] = None,
        bot_username: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_chat_id: Optional[int] = None
    ):
        """Update Telegram bot connection status and broadcast.

        Emits both the legacy ``telegram_status`` raw frame and a
        CloudEvents-typed sibling (Wave 11.I, X4).
        """
        import time
        self._status["telegram"] = {
            "connected": connected,
            "bot_id": bot_id,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "owner_chat_id": owner_chat_id,
            "timestamp": time.time()
        }

        await self.broadcast({
            "type": "telegram_status",
            "data": self._status["telegram"]
        })
        await self._emit_connection_typed(
            plugin="telegram",
            connected=connected,
            subject=bot_username,
            data=self._status["telegram"],
        )

    # =========================================================================
    # Node Status Updates
    # =========================================================================

    async def update_node_status(
        self,
        node_id: str,
        status: str,  # "idle", "executing", "waiting", "success", "error"
        data: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None
    ):
        """Update a specific node's status and broadcast.

        Args:
            node_id: The node ID
            status: Status string
            data: Optional status data
            workflow_id: Optional workflow ID to scope the status update (n8n pattern)
        """
        logger.debug(f"[BROADCAST] update_node_status: node={node_id}, status={status}, workflow={workflow_id}, connections={len(self._connections)}")
        self._status["nodes"][node_id] = {
            "status": status,
            "data": data or {},
            "timestamp": asyncio.get_event_loop().time(),
            "workflow_id": workflow_id
        }

        await self.broadcast({
            "type": "node_status",
            "node_id": node_id,
            "workflow_id": workflow_id,
            "data": self._status["nodes"][node_id]
        })

    async def update_node_output(
        self,
        node_id: str,
        output: Any,
        workflow_id: Optional[str] = None
    ):
        """Update a node's output data and broadcast."""
        if node_id not in self._status["nodes"]:
            self._status["nodes"][node_id] = {"status": "idle", "data": {}}

        self._status["nodes"][node_id]["output"] = output
        if workflow_id:
            self._status["nodes"][node_id]["workflow_id"] = workflow_id

        await self.broadcast({
            "type": "node_output",
            "node_id": node_id,
            "workflow_id": workflow_id,
            "output": output
        })

    # =========================================================================
    # Variable Updates
    # =========================================================================

    async def update_variable(self, name: str, value: Any):
        """Update a workflow variable and broadcast."""
        self._status["variables"][name] = value

        await self.broadcast({
            "type": "variable_update",
            "name": name,
            "value": value
        })

    async def update_variables(self, variables: Dict[str, Any]):
        """Update multiple variables at once and broadcast."""
        self._status["variables"].update(variables)

        await self.broadcast({
            "type": "variables_update",
            "variables": variables
        })

    # =========================================================================
    # Workflow Status Updates
    # =========================================================================

    async def update_workflow_status(
        self,
        executing: bool,
        current_node: Optional[str] = None,
        progress: Optional[float] = None,
        workflow_id: Optional[str] = None,
    ):
        """Update workflow execution status and broadcast.

        When `workflow_id` is provided the broadcast is scoped per-workflow
        so the frontend can drive its per-workflow Start/Stop button. The
        legacy global slot is kept up to date for backward compatibility.
        """
        payload = {
            "executing": executing,
            "current_node": current_node,
            "progress": progress,
            "workflow_id": workflow_id,
        }
        # Update legacy global slot
        self._status["workflow"] = {
            "executing": executing,
            "current_node": current_node,
            "progress": progress,
        }
        # Update per-workflow cache for snapshot/resync
        if workflow_id:
            self._workflow_statuses[workflow_id] = {
                "executing": executing,
                "current_node": current_node,
                "progress": progress,
            }

        await self.broadcast({
            "type": "workflow_status",
            "workflow_id": workflow_id,
            "data": payload,
        })

    async def workflow_run_started(self, workflow_id: Optional[str]) -> bool:
        """Mark a new active run for `workflow_id`.

        Returns True if this transition broadcast `executing=True` (i.e.
        the counter went 0->1). Subsequent overlapping runs return False
        but still increment the counter so the matching `workflow_run_ended`
        decrements properly.
        """
        if not workflow_id:
            return False
        async with self._workflow_active_lock:
            prev = self._workflow_active_runs.get(workflow_id, 0)
            self._workflow_active_runs[workflow_id] = prev + 1
            went_active = prev == 0
        if went_active:
            await self.update_workflow_status(
                executing=True, workflow_id=workflow_id,
            )
        return went_active

    async def workflow_run_ended(
        self,
        workflow_id: Optional[str],
        clear_stuck_nodes: bool = True,
    ) -> bool:
        """Mark an active run finished for `workflow_id`.

        Returns True if this transition broadcast `executing=False` (i.e.
        the counter went 1->0). When the counter reaches zero and
        `clear_stuck_nodes` is set, any node currently marked
        `executing` for this workflow is reset to `idle` -- protects the
        UI from glow leaks on crash paths.

        Crucially does NOT clear `waiting` nodes -- in deployed workflows
        with multiple triggers, every non-firing trigger sits in
        `waiting` for the lifetime of the deployment (its collector loop
        is still registered with event_waiter). Sweeping `waiting` on
        every run completion would visually de-indicate those listeners
        even though they're still alive. Explicit user cancels go through
        `_clear_stuck_node_statuses(include_waiting=True)` instead.
        """
        if not workflow_id:
            return False
        async with self._workflow_active_lock:
            prev = self._workflow_active_runs.get(workflow_id, 0)
            new_count = max(0, prev - 1)
            if new_count == 0:
                self._workflow_active_runs.pop(workflow_id, None)
            else:
                self._workflow_active_runs[workflow_id] = new_count
            went_idle = prev > 0 and new_count == 0
        if went_idle:
            await self.update_workflow_status(
                executing=False, workflow_id=workflow_id,
            )
            if clear_stuck_nodes:
                # include_waiting=False (default) -- don't touch deployment
                # trigger listeners, only sweep genuinely stuck `executing`
                # nodes from a crashed run.
                await self._clear_stuck_node_statuses(workflow_id)
        return went_idle

    async def _clear_stuck_node_statuses(
        self,
        workflow_id: str,
        include_waiting: bool = False,
    ) -> int:
        """Reset stuck nodes for a workflow.

        Default: only `executing` nodes are cleared. This is the right
        behavior at run-completion boundaries because deployed trigger
        nodes legitimately sit in `waiting` for the entire deployment
        lifecycle and must not be wiped between runs.

        Set `include_waiting=True` for explicit user cancels (toolbar
        Stop, cancel_execution) where the user wants every indicator to
        go quiet.

        Also skips children with an in-flight fire-and-forget delegation:
        the parent's workflow run completes the instant the
        ``delegate_to_<x>`` tool returns, but the child agent's
        background ``asyncio.Task`` keeps running for tens of seconds.
        Without this guard the cleanup wipes the child's glow even
        though it's legitimately still working. ``handlers.tools``
        owns the registry; lazy-imported to avoid a circular import.
        """
        # Lazy import — handlers.tools imports this module via
        # ``execute_tool`` -> status_broadcaster, so a top-level import
        # here would form a cycle.
        from services.handlers.tools import is_node_in_active_delegation

        statuses = ("executing", "waiting") if include_waiting else ("executing",)
        stuck = [
            (nid, info) for nid, info in self._status["nodes"].items()
            if info.get("workflow_id") == workflow_id
            and info.get("status") in statuses
            and not is_node_in_active_delegation(nid)
        ]
        for node_id, _info in stuck:
            try:
                await self.clear_node_status(node_id)
            except Exception as e:
                logger.warning(
                    "[StatusBroadcaster] Failed to clear stuck node %s: %s",
                    node_id, e,
                )
        return len(stuck)

    def get_workflow_status(self, workflow_id: str) -> Dict[str, Any]:
        """Return cached per-workflow execution status (for resync)."""
        cached = self._workflow_statuses.get(workflow_id)
        if cached:
            return {**cached, "workflow_id": workflow_id}
        return {
            "executing": False,
            "current_node": None,
            "progress": None,
            "workflow_id": workflow_id,
        }

    async def update_deployment_status(
        self,
        is_running: bool,
        status: str = "idle",
        active_runs: int = 0,
        workflow_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        """Update deployment status and broadcast.

        Follows n8n/Conductor pattern where deployment state is tracked centrally.
        See DESIGN.md for architecture details.

        Args:
            is_running: Whether deployment is active
            status: Current status (idle, starting, running, stopped, cancelled, error)
            active_runs: Number of concurrent execution runs
            workflow_id: The deployed workflow ID
            data: Optional additional data (e.g., run_id, trigger info)
            error: Optional error message if status is 'error'
        """
        self._status["deployment"] = {
            "isRunning": is_running,
            "activeRuns": active_runs,
            "status": status,
            "workflow_id": workflow_id
        }

        # Broadcast deployment_status message (matches frontend handler)
        await self.broadcast({
            "type": "deployment_status",
            "status": status,
            "workflow_id": workflow_id,
            "data": data,
            "error": error
        })

    # =========================================================================
    # Workflow Lock Management (Per-Workflow Locks - n8n pattern)
    # =========================================================================

    async def lock_workflow(
        self,
        workflow_id: str,
        reason: str = "deployment"
    ) -> bool:
        """Lock a specific workflow to prevent concurrent modifications.

        Per-workflow locking (n8n pattern): Each workflow has its own independent lock.
        Multiple workflows can be locked simultaneously.

        Args:
            workflow_id: The workflow ID to lock
            reason: Reason for locking (e.g., "deployment", "execution")

        Returns:
            True if lock acquired, False if THIS workflow is already locked
        """
        import time

        # Initialize workflow_locks if not present
        if "workflow_locks" not in self._status:
            self._status["workflow_locks"] = {}

        # Check if THIS workflow is already locked
        if workflow_id in self._status["workflow_locks"]:
            existing_lock = self._status["workflow_locks"][workflow_id]
            if existing_lock.get("locked"):
                logger.warning(
                    f"[WorkflowLock] Workflow {workflow_id} is already locked "
                    f"for {existing_lock.get('reason')}"
                )
                return False

        # Lock this specific workflow
        lock_info = {
            "locked": True,
            "workflow_id": workflow_id,
            "locked_at": time.time(),
            "reason": reason
        }
        self._status["workflow_locks"][workflow_id] = lock_info

        # Also update legacy single lock for backward compatibility
        self._status["workflow_lock"] = lock_info.copy()

        await self.broadcast({
            "type": "workflow_lock",
            "workflow_id": workflow_id,
            "data": lock_info
        })

        logger.info(f"[WorkflowLock] Locked workflow {workflow_id} for {reason}")
        return True

    async def unlock_workflow(self, workflow_id: str) -> bool:
        """Unlock a specific workflow after deployment/execution completes.

        Args:
            workflow_id: The workflow ID to unlock

        Returns:
            True if unlocked successfully
        """
        # Initialize workflow_locks if not present
        if "workflow_locks" not in self._status:
            self._status["workflow_locks"] = {}

        # Check if this workflow is locked
        if workflow_id not in self._status["workflow_locks"]:
            logger.debug(f"[WorkflowLock] Workflow {workflow_id} not locked")
            return True  # Already unlocked

        existing_lock = self._status["workflow_locks"].get(workflow_id, {})
        if not existing_lock.get("locked"):
            logger.debug(f"[WorkflowLock] Workflow {workflow_id} not locked")
            return True

        # Remove lock for this workflow
        del self._status["workflow_locks"][workflow_id]

        # Update legacy single lock if it was for this workflow
        if self._status["workflow_lock"].get("workflow_id") == workflow_id:
            self._status["workflow_lock"] = {
                "locked": False,
                "workflow_id": None,
                "locked_at": None,
                "reason": None
            }

        await self.broadcast({
            "type": "workflow_lock",
            "workflow_id": workflow_id,
            "data": {
                "locked": False,
                "workflow_id": workflow_id,
                "locked_at": None,
                "reason": None
            }
        })

        logger.info(f"[WorkflowLock] Unlocked workflow {workflow_id}")
        return True

    def is_workflow_locked(self, workflow_id: Optional[str] = None) -> bool:
        """Check if a specific workflow is locked.

        Args:
            workflow_id: Workflow ID to check. If None, checks if any workflow is locked.

        Returns:
            True if the specified workflow is locked (or any if workflow_id is None)
        """
        # Initialize workflow_locks if not present
        if "workflow_locks" not in self._status:
            self._status["workflow_locks"] = {}

        if workflow_id is None:
            # Check if ANY workflow is locked
            return any(
                lock.get("locked", False)
                for lock in self._status["workflow_locks"].values()
            )

        # Check specific workflow
        lock = self._status["workflow_locks"].get(workflow_id, {})
        return lock.get("locked", False)

    def get_workflow_lock(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """Get workflow lock status.

        Args:
            workflow_id: Specific workflow to check. If None, returns legacy single lock.

        Returns:
            Lock info for the specified workflow or legacy lock
        """
        if workflow_id:
            # Initialize workflow_locks if not present
            if "workflow_locks" not in self._status:
                self._status["workflow_locks"] = {}

            lock = self._status["workflow_locks"].get(workflow_id, {})
            return {
                "locked": lock.get("locked", False),
                "workflow_id": workflow_id,
                "locked_at": lock.get("locked_at"),
                "reason": lock.get("reason")
            }

        # Return legacy single lock for backward compatibility
        return self._status["workflow_lock"].copy()

    def get_all_workflow_locks(self) -> Dict[str, Dict[str, Any]]:
        """Get all active workflow locks."""
        if "workflow_locks" not in self._status:
            return {}
        return {
            wid: lock.copy()
            for wid, lock in self._status["workflow_locks"].items()
            if lock.get("locked")
        }

    # =========================================================================
    # Console Log Updates
    # =========================================================================

    async def broadcast_console_log(self, log_data: Dict[str, Any]):
        """Broadcast a console log entry to all connected clients.

        Used by Console nodes to send debug output to the frontend console panel.

        Args:
            log_data: Dict containing:
                - node_id: The console node ID
                - label: User-defined label or default
                - timestamp: ISO timestamp
                - data: The logged data (any type)
                - formatted: Pre-formatted string representation
                - format: Format type (json, json_compact, text, table)
                - workflow_id: Optional workflow ID for scoping
        """
        # Initialize console logs if not present
        if "console_logs" not in self._status:
            self._status["console_logs"] = []

        # Add to console log history (keep last 100 entries)
        self._status["console_logs"].append(log_data)
        if len(self._status["console_logs"]) > 100:
            self._status["console_logs"] = self._status["console_logs"][-100:]

        # Save to database for persistence
        try:
            from core.container import container
            database = container.database()
            await database.add_console_log(log_data)
        except Exception as e:
            logger.warning(f"[StatusBroadcaster] Failed to persist console log: {e}")

        # Broadcast to all clients
        await self.broadcast({
            "type": "console_log",
            "data": log_data
        })

        logger.debug(f"[StatusBroadcaster] Console log broadcast: label={log_data.get('label')}")

    def get_console_logs(self, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get console log history, optionally filtered by workflow_id."""
        if "console_logs" not in self._status:
            return []

        if workflow_id:
            return [
                log for log in self._status["console_logs"]
                if log.get("workflow_id") == workflow_id
            ]
        return list(self._status["console_logs"])

    async def clear_console_logs(self, workflow_id: Optional[str] = None):
        """Clear console log history."""
        if "console_logs" not in self._status:
            self._status["console_logs"] = []
            return

        if workflow_id:
            self._status["console_logs"] = [
                log for log in self._status["console_logs"]
                if log.get("workflow_id") != workflow_id
            ]
        else:
            self._status["console_logs"] = []

        await self.broadcast({
            "type": "console_logs_cleared",
            "workflow_id": workflow_id
        })

    # =========================================================================
    # Terminal Log Updates
    # =========================================================================

    async def broadcast_terminal_log(self, log_data: Dict[str, Any]):
        """Broadcast a terminal log entry to all connected clients.

        Used by the WebSocket logging handler to stream server logs to the frontend.

        Args:
            log_data: Dict containing:
                - timestamp: ISO timestamp
                - level: Log level (debug, info, warning, error)
                - message: The log message
                - source: Logger name/module (e.g., 'workflow', 'ai', 'android')
                - details: Optional additional context
        """
        # Initialize terminal logs if not present
        if "terminal_logs" not in self._status:
            self._status["terminal_logs"] = []

        # Add to terminal log history (keep last 200 entries)
        self._status["terminal_logs"].append(log_data)
        if len(self._status["terminal_logs"]) > 200:
            self._status["terminal_logs"] = self._status["terminal_logs"][-200:]

        # Broadcast to all clients
        await self.broadcast({
            "type": "terminal_log",
            "data": log_data
        })

    def get_terminal_logs(self) -> List[Dict[str, Any]]:
        """Get terminal log history."""
        if "terminal_logs" not in self._status:
            return []
        return list(self._status["terminal_logs"])

    async def clear_terminal_logs(self):
        """Clear terminal log history."""
        self._status["terminal_logs"] = []
        await self.broadcast({
            "type": "terminal_logs_cleared"
        })

    # =========================================================================
    # Agent Team Updates
    # =========================================================================

    async def broadcast_team_event(self, team_id: str, event_type: str, data: Dict[str, Any]):
        """Broadcast a team-related event to all connected clients.

        Args:
            team_id: The team ID
            event_type: Event type (team_created, task_added, task_claimed, etc.)
            data: Event data
        """
        await self.broadcast({
            "type": "team_event",
            "team_id": team_id,
            "event_type": event_type,
            "data": data
        })

    # =========================================================================
    # Generic Updates
    # =========================================================================

    async def send_custom_event(self, event_type: str, data: Any):
        """Send a custom event to all connected clients AND dispatch to event waiters.

        Uses dispatch_async() directly since we're in an async context.
        The sync dispatch() is for thread contexts like APScheduler callbacks.
        See DESIGN.md section "Cross-Thread Event Dispatch" for pattern details.
        """
        # Broadcast to all WebSocket clients
        await self.broadcast({
            "type": event_type,
            "data": data
        })

        # Dispatch to event waiters (for trigger nodes)
        # Use dispatch_async directly - we're in async context
        try:
            from services import event_waiter
            event_data = data if isinstance(data, dict) else {"data": data}
            resolved_count = await event_waiter.dispatch_async(event_type, event_data)
            if resolved_count > 0:
                logger.info(f"[StatusBroadcaster] Event {event_type} resolved {resolved_count} waiters")
        except Exception as e:
            logger.error(f"[StatusBroadcaster] Failed to dispatch to event waiters: {e}")

    # =========================================================================
    # Getters
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get the full current status."""
        return self._status.copy()

    def get_android_status(self) -> Dict[str, Any]:
        """Get Android connection status."""
        return self._status["android"].copy()

    def get_node_status(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific node's status."""
        return self._status["nodes"].get(node_id)

    async def clear_node_status(self, node_id: str) -> bool:
        """Reset a node's status to idle.

        Intentionally does NOT delete the dict entry. The previous behavior
        deleted the slot, which created a race window: an in-flight
        execution's subsequent `success` broadcast would re-create the
        entry, leaving the UI stuck on a stale "completed" indicator on a
        node the user just cancelled. Resetting to idle preserves entry
        identity (so subsequent broadcasts update it normally) while
        clearing the visible state.
        """
        had_status = node_id in self._status["nodes"]
        previous_workflow = (
            self._status["nodes"][node_id].get("workflow_id") if had_status else None
        )
        self._status["nodes"][node_id] = {
            "status": "idle",
            "data": {},
            "timestamp": asyncio.get_event_loop().time(),
            "workflow_id": previous_workflow,
            "cleared": True,
        }
        logger.info(f"[StatusBroadcaster] Reset node status to idle: {node_id}")
        await self.broadcast({
            "type": "node_status_cleared",
            "node_id": node_id,
            "workflow_id": previous_workflow,
        })
        return had_status

    def get_variable(self, name: str) -> Any:
        """Get a variable value."""
        return self._status["variables"].get(name)

    @property
    def connection_count(self) -> int:
        """Get the number of active WebSocket connections."""
        return len(self._connections)


# ---------------------------------------------------------------------------
# Plugin-registered service-refresh callbacks.
#
# The legacy ``_refresh_*`` instance methods (whatsapp/twitter/google/
# android) hardcode service-specific lookups inside the broadcaster.
# New plugin packages instead register an ``async def refresh(broadcaster)``
# callback here -- the broadcaster has zero per-plugin knowledge.
# ---------------------------------------------------------------------------

import typing as _typing  # local alias to avoid shadowing module-level name

_ServiceRefreshCallback = _typing.Callable[
    ["StatusBroadcaster"], _typing.Awaitable[None]
]
_SERVICE_REFRESH_CALLBACKS: _typing.List[_ServiceRefreshCallback] = []

from services.plugin.registry import IdempotentList as _IdempotentList  # noqa: E402

# Backed by the module-level _SERVICE_REFRESH_CALLBACKS list so the
# existing iterator at line 153 (``for callback in list(_SERVICE_REFRESH_CALLBACKS)``)
# keeps working unchanged.
_SERVICE_REFRESH_FANOUT: _IdempotentList[_ServiceRefreshCallback] = _IdempotentList(
    "service_refresh", items=_SERVICE_REFRESH_CALLBACKS
)


def register_service_refresh(callback: _ServiceRefreshCallback) -> None:
    """Register a per-service refresh callback.

    Idempotent on re-import (same callable is a no-op). Each registered
    callback runs once per ``_refresh_all_services()`` cycle (i.e. on
    every WebSocket client connect).
    """
    _SERVICE_REFRESH_FANOUT.register(callback)


# Global singleton instance
_broadcaster: Optional[StatusBroadcaster] = None


def get_status_broadcaster() -> StatusBroadcaster:
    """Get or create the global StatusBroadcaster instance."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = StatusBroadcaster()
    return _broadcaster
