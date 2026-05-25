"""Deployment Manager - Event-driven workflow deployment lifecycle.

Implements n8n/Conductor pattern where:
- Workflow is a template stored in memory
- Trigger events spawn independent execution runs
- Runs execute concurrently (up to max_concurrent_runs)
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING

from core.logging import get_logger
from constants import POLLING_TRIGGER_TYPES, TOOLKIT_NODE_TYPES, WORKFLOW_TRIGGER_TYPES
from services import event_waiter
from .state import DeploymentState, TriggerInfo
from .triggers import TriggerManager

if TYPE_CHECKING:
    from core.database import Database

logger = get_logger(__name__)


# Listener Temporal workflow type-names. Used both for ``start_workflow``
# and as the Visibility filter for cancellation discovery. Push-based
# triggers (webhook, chat, task, telegram, whatsapp) use the
# signal-driven listener; polling triggers (gmail, twitter) use the
# workflow.sleep-driven listener (Wave 12 C2). Cancel queries both via
# an OR clause so deployment cancel reaches every canary listener
# without per-type code.
_PUSH_LISTENER_WORKFLOW_TYPE = "TriggerListenerWorkflow"
_POLLING_LISTENER_WORKFLOW_TYPE = "PollingTriggerWorkflow"
_LISTENER_WORKFLOW_TYPES = (
    _PUSH_LISTENER_WORKFLOW_TYPE,
    _POLLING_LISTENER_WORKFLOW_TYPE,
)
# Kept for back-compat with any reader that still imports the old
# single-type constant. Equals the push workflow name.
_LISTENER_WORKFLOW_TYPE = _PUSH_LISTENER_WORKFLOW_TYPE

# Wave 12 C1 canary: which trigger types route to TriggerListenerWorkflow
# instead of the legacy in-process collector/processor is owned by the
# plugins themselves via ``services.deployment.canary_registry``. Each
# canary-enabled plugin's ``__init__.py`` calls
# ``register_canary_trigger_type("<node_type>")`` and the deployment
# manager queries ``is_canary_trigger_type`` here — no framework-side
# allowlist to drift. Producer side (the plugin's ``_events.py`` calling
# ``services.events.dispatch.emit``) is the second half of opt-in.


class DeploymentManager:
    """Manages event-driven workflow deployment.

    Supports per-workflow deployments following n8n pattern:
    - Each workflow can be deployed independently
    - Multiple workflows can run concurrently
    - Each deployment has its own state, triggers, and runs
    """

    def __init__(
        self,
        database: "Database",
        execute_workflow_fn: Callable,
        store_output_fn: Callable,
        broadcaster: Any,
    ):
        self.database = database
        self._execute_workflow = execute_workflow_fn
        self._store_output = store_output_fn
        self._broadcaster = broadcaster

        # Per-workflow deployment state (n8n pattern)
        self._deployments: Dict[str, DeploymentState] = {}
        self._trigger_managers: Dict[str, TriggerManager] = {}
        self._active_runs: Dict[str, Dict[str, asyncio.Task]] = {}  # workflow_id -> {run_id: task}
        self._run_counters: Dict[str, int] = {}
        self._status_callbacks: Dict[str, Callable] = {}
        self._cron_iterations: Dict[str, int] = {}  # node_id -> iteration count
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        self._settings = {"stop_on_error": False, "max_concurrent_runs": 100, "use_parallel_executor": True}

    @property
    def is_running(self) -> bool:
        """Check if ANY deployment is running (backward compatibility)."""
        return any(state.is_running for state in self._deployments.values())

    def is_workflow_deployed(self, workflow_id: str) -> bool:
        """Check if a specific workflow is deployed."""
        state = self._deployments.get(workflow_id)
        return state is not None and state.is_running

    def get_deployed_workflows(self) -> List[str]:
        """Get list of deployed workflow IDs."""
        return [wid for wid, state in self._deployments.items() if state.is_running]

    # =========================================================================
    # DEPLOYMENT LIFECYCLE
    # =========================================================================

    async def deploy(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        session_id: str = "default",
        status_callback: Optional[Callable] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deploy workflow in event-driven mode.

        Args:
            nodes: Workflow nodes
            edges: Workflow edges
            session_id: Session identifier
            status_callback: Status update callback
            workflow_id: Workflow ID for per-workflow deployment tracking
        """
        # Generate workflow_id if not provided
        if not workflow_id:
            workflow_id = f"workflow_{int(time.time() * 1000)}"

        # Check if THIS workflow is already deployed
        if self.is_workflow_deployed(workflow_id):
            return {
                "success": False,
                "error": f"Workflow {workflow_id} is already deployed",
                "workflow_id": workflow_id,
                "deployment_id": self._deployments[workflow_id].deployment_id,
            }

        # Setup
        deployment_id = f"deploy_{workflow_id}_{int(time.time() * 1000)}"
        self._status_callbacks[workflow_id] = status_callback
        self._run_counters[workflow_id] = 0
        self._active_runs[workflow_id] = {}

        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = asyncio.get_event_loop()

        # Create trigger manager for this workflow
        trigger_manager = TriggerManager()
        trigger_manager.set_main_loop(self._main_loop)
        trigger_manager.set_running(True)
        self._trigger_managers[workflow_id] = trigger_manager

        # Load settings
        await self._load_settings()

        # Create state for this workflow
        self._deployments[workflow_id] = DeploymentState(
            deployment_id=deployment_id,
            workflow_id=workflow_id,
            is_running=True,
            nodes=nodes,
            edges=edges,
            session_id=session_id,
            settings=self._settings.copy(),
        )

        logger.info("Deployment starting", deployment_id=deployment_id, workflow_id=workflow_id, nodes=len(nodes))

        triggers_setup = []

        try:
            # Setup cron triggers
            for cron_node in TriggerManager.find_cron_nodes(nodes):
                info = await self._setup_cron_trigger(cron_node, workflow_id)
                triggers_setup.append(info.to_dict())

            # Find start and event triggers
            start_nodes, event_triggers = TriggerManager.find_trigger_nodes(nodes, edges)

            # Fire start nodes immediately
            for node in start_nodes:
                info = await self._fire_start_trigger(node, workflow_id)
                triggers_setup.append(info.to_dict())

            # Setup event triggers
            for node in event_triggers:
                info = await self._setup_event_trigger(node, workflow_id)
                triggers_setup.append(info.to_dict())

            # Notify started
            await self._notify(
                "started", {"deployment_id": deployment_id, "workflow_id": workflow_id, "triggers": triggers_setup}, workflow_id
            )

            return {
                "success": True,
                "deployment_id": deployment_id,
                "workflow_id": workflow_id,
                "message": "Workflow deployed",
                "triggers_setup": triggers_setup,
            }

        except Exception as e:
            logger.error("Deployment failed", workflow_id=workflow_id, error=str(e))
            await self.cancel(workflow_id)
            return {"success": False, "error": str(e), "workflow_id": workflow_id}

    async def cancel(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel deployment for a specific workflow.

        Args:
            workflow_id: Workflow to cancel. If None, cancels the first running deployment.
        """
        # Find workflow to cancel
        if workflow_id:
            if not self.is_workflow_deployed(workflow_id):
                return {"success": False, "error": f"Workflow {workflow_id} is not deployed"}
        else:
            # Backward compatibility: cancel first running deployment
            deployed = self.get_deployed_workflows()
            if not deployed:
                return {"success": False, "error": "No deployment running"}
            workflow_id = deployed[0]

        state = self._deployments.get(workflow_id)
        if not state:
            return {"success": False, "error": f"Deployment state not found for {workflow_id}"}

        deployment_id = state.deployment_id
        logger.info("Cancelling deployment", deployment_id=deployment_id, workflow_id=workflow_id)

        # Get trigger manager for this workflow
        trigger_manager = self._trigger_managers.get(workflow_id)
        if trigger_manager:
            trigger_manager.set_running(False)

        # Cancel active runs for this workflow
        workflow_runs = self._active_runs.get(workflow_id, {})
        listener_nodes = trigger_manager.get_listener_node_ids() if trigger_manager else []

        for task in workflow_runs.values():
            if not task.done():
                task.cancel()

        if workflow_runs:
            await asyncio.gather(*workflow_runs.values(), return_exceptions=True)
        run_count = len(workflow_runs)

        # Cleanup triggers for this workflow
        listener_count = 0
        cron_count = 0
        cron_node_ids = []
        if trigger_manager:
            # Get cron node IDs before teardown (they'll be cleared)
            cron_node_ids = trigger_manager.get_cron_node_ids()
            listener_count = await trigger_manager.teardown_all_listeners()
            cron_count = trigger_manager.teardown_all_crons()

        # Reset cron trigger node statuses to idle
        for node_id in cron_node_ids:
            await self._broadcaster.update_node_status(node_id, "idle", {}, workflow_id=workflow_id)

        # Reset listener node statuses to idle
        for node_id in listener_nodes:
            await self._broadcaster.update_node_status(node_id, "idle", {}, workflow_id=workflow_id)

        # Cancel event waiters for nodes in this workflow
        waiter_count = 0
        for node in state.nodes:
            waiter_count += event_waiter.cancel_for_node(node["id"])

        # Wave 12 C1 canary: cancel Temporal-durable listeners for this
        # deployment. Visibility-query-based; no local handle dict.
        canary_count = await self._cancel_canary_listeners(workflow_id)

        # Wave 12 C3 canary: delete Temporal Schedules created by this
        # deployment's cron triggers. Different Temporal resource type
        # from listener workflows so it needs its own sweep
        # (client.list_schedules vs client.list_workflows).
        cron_schedule_count = await self._cancel_canary_cron_schedules(workflow_id)

        # Sweep any downstream nodes still glowing on the canvas. The
        # cron + listener resets above only cover the trigger nodes the
        # manager owns directly; if a child run was mid-execution when
        # cancellation hit, downstream agents / tools / actions may have
        # been broadcast ``executing`` and would otherwise stay glowing
        # forever. ``include_waiting=True`` because explicit user-cancel
        # is the "every indicator goes quiet" signal — matches the
        # behaviour of the ``handle_cancel_execution`` WS path. The
        # delegation guard inside ``_clear_stuck_node_statuses`` still
        # protects in-flight fire-and-forget child agents.
        await self._broadcaster._clear_stuck_node_statuses(
            workflow_id,
            include_waiting=True,
        )

        # And broadcast a final ``executing=False`` for the deployment
        # so the toolbar Start/Stop indicator reflects the cancel. The
        # legacy path's run-counter eviction (``workflow_run_ended``)
        # doesn't fire here because deployment-level cancel can race
        # ahead of in-flight ``workflow_run_started`` callers — emit
        # the terminal state directly to avoid a stuck ``executing=True``
        # on the FE toolbar.
        await self._broadcaster.update_workflow_status(
            executing=False,
            workflow_id=workflow_id,
        )

        # Clear cron iteration counters for this workflow's cron nodes
        for node_id in cron_node_ids:
            self._cron_iterations.pop(node_id, None)

        # Clear state for this workflow
        self._deployments.pop(workflow_id, None)
        self._trigger_managers.pop(workflow_id, None)
        self._active_runs.pop(workflow_id, None)
        self._run_counters.pop(workflow_id, None)
        self._status_callbacks.pop(workflow_id, None)

        return {
            "success": True,
            "deployment_id": deployment_id,
            "workflow_id": workflow_id,
            "runs_cancelled": run_count,
            "listeners_cancelled": listener_count,
            "crons_cancelled": cron_count,
            "waiters_cancelled": waiter_count,
            "canary_listeners_cancelled": canary_count,
            "canary_cron_schedules_cancelled": cron_schedule_count,
            "cancelled_listener_node_ids": listener_nodes,
        }

    def get_status(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """Get deployment status.

        Args:
            workflow_id: Get status for specific workflow. If None, returns global status.
        """
        if workflow_id:
            # Status for specific workflow
            state = self._deployments.get(workflow_id)
            if not state or not state.is_running:
                return {"deployed": False, "deployment_id": None, "active_runs": 0, "workflow_id": workflow_id}

            workflow_runs = self._active_runs.get(workflow_id, {})
            execution_runs = [k for k in workflow_runs if k.startswith("run_")]
            return {
                "deployed": True,
                "deployment_id": state.deployment_id,
                "workflow_id": workflow_id,
                "active_runs": len(execution_runs),
                "active_listeners": len(workflow_runs) - len(execution_runs),
                "run_counter": self._run_counters.get(workflow_id, 0),
                "deployed_at": state.deployed_at,
            }

        # Global status (backward compatibility)
        if not self.is_running:
            return {"deployed": False, "deployment_id": None, "active_runs": 0}

        # Aggregate across all workflows
        total_runs = 0
        total_listeners = 0
        total_run_counter = 0
        deployed_workflows = []

        for wid, state in self._deployments.items():
            if state.is_running:
                deployed_workflows.append(wid)
                workflow_runs = self._active_runs.get(wid, {})
                execution_runs = [k for k in workflow_runs if k.startswith("run_")]
                total_runs += len(execution_runs)
                total_listeners += len(workflow_runs) - len(execution_runs)
                total_run_counter += self._run_counters.get(wid, 0)

        return {
            "deployed": True,
            "deployed_workflows": deployed_workflows,
            "active_runs": total_runs,
            "active_listeners": total_listeners,
            "run_counter": total_run_counter,
        }

    # =========================================================================
    # TRIGGER SETUP
    # =========================================================================

    async def _setup_cron_trigger(self, node: Dict, workflow_id: str) -> TriggerInfo:
        """Setup cron trigger for a node.

        Two paths:

        1. **Wave 12 C3 canary**: when ``Settings.event_framework_enabled``
           is on AND ``cronScheduler`` is in the canary registry, create
           a Temporal :class:`Schedule` whose action is the plugin's
           :class:`CronTriggerWorkflow`. Survives FastAPI process
           restart via the Temporal Schedule service.
        2. **Legacy**: APScheduler ``register_cron_job`` runs an
           in-process tick callback. Dies on process restart — kept as
           the default while the canary stabilises.
        """
        node_id = node["id"]
        node_type = node.get("type", "cronScheduler")
        params = await self.database.get_node_parameters(node_id) or {}

        cron_expr = TriggerManager.build_cron_expression(params)
        timezone = params.get("timezone", "UTC")
        frequency = params.get("frequency", "minutes")
        schedule_desc = self._get_schedule_description(params)

        # Path 1: Wave 12 C3 canary — Temporal Schedule.
        if await self._canary_listener_enabled_for(node_type):
            schedule_id = await self._start_canary_cron_schedule(
                node,
                workflow_id,
                params,
                cron_expr=cron_expr,
                timezone=timezone,
                frequency=frequency,
                schedule_desc=schedule_desc,
            )
            if schedule_id is not None:
                await self._broadcaster.update_node_status(
                    node_id,
                    "waiting",
                    {
                        "message": f"Waiting for schedule: {cron_expr} (Temporal-durable)",
                        "cron_expression": cron_expr,
                        "timezone": timezone,
                        "schedule_id": schedule_id,
                    },
                    workflow_id=workflow_id,
                )
                return TriggerInfo(node_id, "cron", job_id=schedule_id)
            # Fall through to legacy path if Temporal unavailable
            # (already logged inside _start_canary_cron_schedule).

        # Initialize iteration counter for this cron node (legacy path).
        self._cron_iterations[node_id] = 0

        def on_tick():
            if self._main_loop and self._main_loop.is_running():
                # Increment iteration counter
                self._cron_iterations[node_id] = self._cron_iterations.get(node_id, 0) + 1
                iteration = self._cron_iterations[node_id]

                trigger_data = {
                    "node_id": node_id,
                    "timestamp": datetime.now().isoformat(),
                    "trigger_type": "cron",
                    "event_data": {
                        "timestamp": datetime.now().isoformat(),
                        "iteration": iteration,
                        "frequency": frequency,
                        "timezone": timezone,
                        "schedule": schedule_desc,
                        "cron_expression": cron_expr,
                    },
                }
                asyncio.run_coroutine_threadsafe(self._spawn_run(node_id, trigger_data, workflow_id=workflow_id), self._main_loop)

        trigger_manager = self._trigger_managers.get(workflow_id)
        if not trigger_manager:
            raise RuntimeError(f"No trigger manager for workflow {workflow_id}")

        job_id = trigger_manager.setup_cron(node_id, cron_expr, timezone, on_tick)

        # Broadcast waiting status for cron trigger (like event triggers do)
        await self._broadcaster.update_node_status(
            node_id,
            "waiting",
            {"message": f"Waiting for schedule: {cron_expr}", "cron_expression": cron_expr, "timezone": timezone, "job_id": job_id},
            workflow_id=workflow_id,
        )

        return TriggerInfo(node_id, "cron", job_id=job_id)

    async def _fire_start_trigger(self, node: Dict, workflow_id: str) -> TriggerInfo:
        """Fire a start trigger immediately."""
        node_id = node["id"]
        params = await self.database.get_node_parameters(node_id) or {}

        initial_data_str = params.get("initial_data", "{}")
        try:
            initial_data = json.loads(initial_data_str) if initial_data_str else {}
        except json.JSONDecodeError:
            initial_data = {}

        trigger_data = {"node_id": node_id, "timestamp": datetime.now().isoformat(), "trigger_type": "start", "event_data": initial_data}

        await self._spawn_run(node_id, trigger_data, workflow_id=workflow_id)
        return TriggerInfo(node_id, "start", fired=True)

    async def _setup_event_trigger(self, node: Dict, workflow_id: str) -> TriggerInfo:
        """Setup event-based trigger.

        Three dispatch paths (in priority order):

        1. **Wave 12 C1 canary**: when ``Settings.event_framework_enabled``
           is on AND the plugin has opted into the canary via
           :func:`services.deployment.canary_registry.register_canary_trigger_type`
           (called from the plugin's ``__init__.py``), start a
           Temporal-durable :class:`TriggerListenerWorkflow`. Survives
           FastAPI process restart via Temporal Event-History replay.

        2. **Polling triggers** (Gmail, Twitter): API-polling factory
           registered by the plugin's ``PollingTriggerNode`` subclass.

        3. **Legacy**: in-process collector/processor task pair via
           ``trigger_manager.setup_event_trigger``. Dies on process
           restart — pre-Wave-12 behaviour, kept for triggers not yet
           on the canary list.
        """
        node_id = node["id"]
        node_type = node.get("type", "")
        params = await self.database.get_node_parameters(node_id) or {}

        # Path 1: Wave 12 C1 canary — Temporal-durable listener.
        if await self._canary_listener_enabled_for(node_type):
            listener_id = await self._start_canary_listener(node, workflow_id, params)
            if listener_id is not None:
                return TriggerInfo(node_id, node_type, job_id=listener_id)
            # Fall through to legacy path if Temporal client unavailable
            # (already logged inside _start_canary_listener).

        async def on_event(event_data: Dict):
            trigger_data = {
                "node_id": node_id,
                "timestamp": datetime.now().isoformat(),
                "trigger_type": node_type,
                "event_data": event_data,
            }
            await self._spawn_run(node_id, trigger_data, wait=True, workflow_id=workflow_id)

        trigger_manager = self._trigger_managers.get(workflow_id)
        if not trigger_manager:
            raise RuntimeError(f"No trigger manager for workflow {workflow_id}")

        # Polling triggers need active API polling instead of event_waiter.
        # Plugins register a factory via
        # ``services.plugin.PollingTriggerNode.__init_subclass__`` →
        # ``services.deployment.poll_registry.register_poll_coroutine_factory``.
        if node_type in POLLING_TRIGGER_TYPES:
            from services.deployment.poll_registry import (
                get_poll_coroutine_factory,
            )

            factory = get_poll_coroutine_factory(node_type)
            if factory is not None:
                poll_coroutine = factory(node_id, params)
                await trigger_manager.setup_polling_trigger(
                    node_id, node_type, params, poll_coroutine, on_event, self._broadcaster, workflow_id=workflow_id
                )
                return TriggerInfo(node_id, node_type)
            # Fall through to event_waiter if no polling factory registered
            logger.warning("No polling factory registered for trigger", node_type=node_type)

        await trigger_manager.setup_event_trigger(node_id, node_type, params, on_event, self._broadcaster, workflow_id=workflow_id)
        return TriggerInfo(node_id, node_type)

    # =========================================================================
    # WAVE 12 C1 CANARY: TEMPORAL-DURABLE LISTENERS
    # =========================================================================
    #
    # Pattern (cross-confirmed across Temporal docs, samples-python,
    # Inngest, Prefect, n8n): deterministic workflow_id mapped to
    # business entity (deployment + node), WorkflowIDConflictPolicy.
    # USE_EXISTING for idempotent re-deploy, Search Attributes set at
    # start, Visibility API queries for cross-workflow discovery on
    # cancel. The Temporal server's Visibility store IS the registry —
    # no Python dict of handles, no instance state to drift on
    # FastAPI restart. Cancellation uses ``cancel()`` (graceful) over
    # ``terminate()`` per docs.temporal.io/develop/python/cancellation.
    #
    # Refs:
    # - https://docs.temporal.io/develop/python/temporal-clients
    #   ("Workflow ID mapped to business entities")
    # - https://docs.temporal.io/visibility + /list-filter
    # - https://docs.temporal.io/develop/python/cancellation
    # - https://github.com/temporalio/samples-python/blob/main/hello/hello_search_attributes.py
    # =========================================================================

    @staticmethod
    def _listener_workflow_id(workflow_id: str, node_id: str) -> str:
        """Deterministic Temporal workflow_id for a canary listener.

        Mapped to the business entity (deployment workflow_id + trigger
        node_id) so re-deploy of the same workflow produces the same
        listener id. Pairs with ``WorkflowIDConflictPolicy.USE_EXISTING``
        for idempotent start.
        """
        return f"trigger-listener-{workflow_id}-{node_id}"

    @staticmethod
    def _trigger_kind_for(node_type: str) -> str:
        """Coarse-grained classification for the ``EventTriggerKind``
        Search Attribute. Derived from the node_type by stripping the
        ``Trigger`` / ``Receive`` suffix — same mapping the frontend
        uses for filter chips on the ops dashboard.

        Examples: ``webhookTrigger`` → ``webhook``, ``chatTrigger`` →
        ``chat``, ``telegramReceive`` → ``telegram``.
        """
        for suffix in ("Trigger", "Receive"):
            if node_type.endswith(suffix):
                return node_type[: -len(suffix)]
        return node_type

    @staticmethod
    async def _canary_listener_enabled_for(node_type: str) -> bool:
        """Whether the C1 canary applies to this trigger type.

        Two conditions, both required:

        1. Plugin has opted in via
           :func:`services.deployment.canary_registry.register_canary_trigger_type`
           — registry lookup, no framework-side allowlist.
        2. ``Settings.event_framework_enabled`` is on — lazy ``Settings()``
           call so a runtime flag flip takes effect on the next deploy
           without process restart.
        """
        from services.deployment.canary_registry import is_canary_trigger_type

        if not is_canary_trigger_type(node_type):
            return False
        from core.config import Settings

        return bool(Settings().event_framework_enabled)

    @staticmethod
    def _is_polling_trigger_class(node_type: str) -> bool:
        """True iff ``node_type`` resolves to a
        :class:`services.plugin.PollingTriggerNode` subclass.

        Used by :meth:`_start_canary_listener` to dispatch between the
        push-driven :class:`TriggerListenerWorkflow` and the poll-driven
        :class:`PollingTriggerWorkflow`. Lazy imports the registry +
        base class to keep the manager free of top-level cycles.
        """
        try:
            from services.node_registry import get_node_class
            from services.plugin import PollingTriggerNode
        except Exception:  # noqa: BLE001
            return False
        cls = get_node_class(node_type)
        return isinstance(cls, type) and issubclass(cls, PollingTriggerNode)

    async def _start_canary_listener(
        self,
        node: Dict,
        workflow_id: str,
        params: Dict,
    ) -> Optional[str]:
        """Start the canary listener workflow for a trigger.

        Dispatches by plugin class:
          - :class:`PollingTriggerNode` subclasses → :class:`PollingTriggerWorkflow`
            (Wave 12 C2 — workflow.sleep loop + per-cycle activity).
          - Everything else → :class:`TriggerListenerWorkflow`
            (Wave 12 C1 — signal-driven wait_condition).

        Returns the Temporal listener workflow_id on success, or
        ``None`` if the Temporal client isn't connected (caller falls
        through to the legacy collector/processor path).

        Idempotent: re-deploying the same MachinaOs workflow re-runs
        this with the same deterministic id; Temporal returns the
        existing handle via ``WorkflowIDConflictPolicy.USE_EXISTING``
        instead of erroring. Search Attributes provide the registry
        used by :meth:`_cancel_canary_listeners`.
        """
        from core.container import container
        from temporalio.common import (
            SearchAttributeKey,
            SearchAttributePair,
            TypedSearchAttributes,
            WorkflowIDConflictPolicy,
        )

        node_id = node["id"]
        node_type = node.get("type", "")

        wrapper = container.temporal_client()
        if wrapper is None or wrapper.client is None:
            logger.warning(
                "Canary listener requested but Temporal not connected; " "falling back to legacy collector/processor path",
                node_id=node_id,
                workflow_id=workflow_id,
                node_type=node_type,
            )
            return None

        state = self._deployments.get(workflow_id)
        if state is None:
            raise RuntimeError(f"No deployment state for workflow {workflow_id}")

        from services.deployment.canary_registry import cloudevent_type_for

        config = event_waiter.get_trigger_config(node_type)
        # Legacy snake_case event_type — only used in the listener_args
        # payload + node-status display strings. NOT the Search Attribute
        # value; that has to be the CloudEvents reverse-DNS string the
        # producer puts on outgoing envelopes (see EventType SA pair
        # below).
        event_type = config.event_type if config else f"unknown_{node_type}"

        # CloudEvents type the producer's _events.py factory emits on its
        # outgoing envelope. Used as the EventType Search Attribute value
        # so services.events.dispatch.emit's Visibility query
        # ``ListWorkflows(query="EventType='<event.type>'")`` actually
        # matches this listener.
        #
        # Pre-fix (2026-05-15) the EventType SA was set to ``event_type``
        # (the legacy snake_case), but dispatch.emit queries with the
        # producer's CloudEvents reverse-DNS string. The mismatch silently
        # zeroed the signal fan-out — listener started OK, never reacted.
        cloudevent_type = cloudevent_type_for(node_type)
        if cloudevent_type is None:
            logger.warning(
                "Canary listener: no cloudevent_type registered for node_type; "
                "falling back to legacy event_type for EventType SA. This will "
                "silently fail dispatch.emit fan-out — register cloudevent_type "
                "in the plugin's __init__.py.",
                node_id=node_id,
                workflow_id=workflow_id,
                node_type=node_type,
            )
            cloudevent_type = event_type

        # Pick workflow type by plugin class. EventTriggerKind picks up
        # the "polling" classification for ops dashboards independent
        # of the per-plugin kind (gmail / twitter / ...).
        is_polling = self._is_polling_trigger_class(node_type)
        workflow_type_name = _POLLING_LISTENER_WORKFLOW_TYPE if is_polling else _PUSH_LISTENER_WORKFLOW_TYPE
        trigger_kind = "polling" if is_polling else self._trigger_kind_for(node_type)

        listener_id = self._listener_workflow_id(workflow_id, node_id)

        # Common payload shape for both workflow types (signal-driven
        # TriggerListenerWorkflow ignores poll-specific fields like
        # ``version`` / ``seen_ids``; polling workflow reads them).
        listener_args: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "trigger_node_id": node_id,
            "node_type": node_type,
            "event_type": event_type,
            "filter_params": params,
            "nodes": state.nodes,
            "edges": state.edges,
            "session_id": state.session_id,
        }
        if is_polling:
            # ``version`` feeds the polling workflow's activity-name
            # construction (``poll.{type}.v{version}``). Pulled from the
            # plugin class — single source of truth.
            from services.node_registry import get_node_class

            cls = get_node_class(node_type)
            listener_args["version"] = getattr(cls, "version", 1) if cls else 1
            # First start: empty seen_ids; the workflow runs a
            # baseline-only cycle to establish the seen set without
            # emitting events.
            listener_args["seen_ids"] = []

        # Search Attribute keys mirror services/temporal/search_attributes.py
        # (the A4 registration spec). All Keyword-typed; values are scalars.
        event_type_key = SearchAttributeKey.for_keyword("EventType")
        trigger_node_id_key = SearchAttributeKey.for_keyword("TriggerNodeId")
        event_workflow_id_key = SearchAttributeKey.for_keyword("EventWorkflowId")
        event_trigger_kind_key = SearchAttributeKey.for_keyword("EventTriggerKind")

        await wrapper.client.start_workflow(
            workflow_type_name,
            args=[listener_args],
            id=listener_id,
            task_queue="machina-tasks",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            search_attributes=TypedSearchAttributes(
                [
                    # MUST be the CloudEvents type the producer emits on
                    # outgoing envelopes — dispatch.emit's Visibility query
                    # substitutes ``event.type`` into the EventType filter.
                    SearchAttributePair(event_type_key, cloudevent_type),
                    SearchAttributePair(trigger_node_id_key, node_id),
                    SearchAttributePair(event_workflow_id_key, workflow_id),
                    SearchAttributePair(event_trigger_kind_key, trigger_kind),
                ]
            ),
        )

        # Broadcast waiting status (parity with legacy path).
        await self._broadcaster.update_node_status(
            node_id,
            "waiting",
            {
                "message": f"Waiting for {config.display_name if config else node_type} (Temporal-durable)...",
                "event_type": event_type,
                "listener_id": listener_id,
            },
            workflow_id=workflow_id,
        )

        logger.info(
            "Canary listener started",
            listener_id=listener_id,
            workflow_id=workflow_id,
            node_id=node_id,
            event_type=event_type,
        )
        return listener_id

    async def _cancel_canary_cron_schedules(self, workflow_id: str) -> int:
        """Wave 12 C3: delete every Temporal Schedule created for this
        deployment's cron triggers.

        Uses ``services.temporal.schedules.delete_cron_schedules_for_deployment``
        which queries ``client.list_schedules`` with the ``EventWorkflowId``
        Search Attribute filter. Same no-local-dict contract as
        :meth:`_cancel_canary_listeners` but against the Schedule
        resource type (Temporal Schedules and Workflows have separate
        Visibility lists).
        """
        from core.container import container
        from services.temporal.schedules import delete_cron_schedules_for_deployment

        wrapper = container.temporal_client()
        if wrapper is None or wrapper.client is None:
            return 0

        return await delete_cron_schedules_for_deployment(
            wrapper.client,
            workflow_id,
        )

    async def _start_canary_cron_schedule(
        self,
        node: Dict,
        workflow_id: str,
        params: Dict,
        *,
        cron_expr: str,
        timezone: str,
        frequency: str,
        schedule_desc: str,
    ) -> Optional[str]:
        """Wave 12 C3 canary: create a Temporal Schedule for a cron trigger.

        Schedule action targets the plugin-owned ``CronTriggerWorkflow``
        (registered as a :class:`temporalio.plugin.SimplePlugin` from
        ``nodes/scheduler/cron_scheduler/__init__.py``). Each firing
        spawns a child :class:`MachinaWorkflow`.

        Returns the Schedule id on success, ``None`` when Temporal isn't
        reachable so the caller falls back to APScheduler.
        """
        from core.container import container
        from services.temporal.schedules import create_cron_schedule

        node_id = node["id"]

        wrapper = container.temporal_client()
        if wrapper is None or wrapper.client is None:
            logger.warning(
                "Canary cron Schedule requested but Temporal not connected; " "falling back to APScheduler",
                node_id=node_id,
                workflow_id=workflow_id,
            )
            return None

        state = self._deployments.get(workflow_id)
        if state is None:
            raise RuntimeError(f"No deployment state for workflow {workflow_id}")

        listener_data: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "trigger_node_id": node_id,
            "node_type": node.get("type", "cronScheduler"),
            "cron_expression": cron_expr,
            "frequency": frequency,
            "timezone": timezone,
            "schedule": schedule_desc,
            "filter_params": params,
            "nodes": state.nodes,
            "edges": state.edges,
            "session_id": state.session_id,
        }

        schedule_id = await create_cron_schedule(
            wrapper.client,
            deployment_workflow_id=workflow_id,
            node_id=node_id,
            cron_expression=cron_expr,
            timezone=timezone,
            listener_data=listener_data,
        )

        logger.info(
            "Canary cron Schedule created",
            schedule_id=schedule_id,
            workflow_id=workflow_id,
            node_id=node_id,
            cron_expression=cron_expr,
        )
        return schedule_id

    async def _cancel_canary_listeners(self, workflow_id: str) -> int:
        """Cancel all canary listeners for this deployment.

        Uses Visibility API query — the Temporal server's Visibility
        store IS the registry, no local dict. ``cancel()`` is graceful;
        listeners drain in-flight child spawns before exiting.

        Visibility is eventually consistent
        (https://docs.temporal.io/visibility) — a freshly-started listener
        might not appear in the query result for a few seconds. Acceptable
        here: the listener will eventually surface in a subsequent cancel
        sweep, AND its parent_close_policy=ABANDON means in-flight runs
        complete regardless.
        """
        from core.container import container

        wrapper = container.temporal_client()
        if wrapper is None or wrapper.client is None:
            return 0

        # Visibility List Filter query — operators per docs.temporal.io/list-filter.
        # Match BOTH workflow types via ``WorkflowType IN (...)`` so push
        # (TriggerListenerWorkflow) and polling (PollingTriggerWorkflow)
        # listeners drain in one sweep.
        wf_types_in = ", ".join(f"'{t}'" for t in _LISTENER_WORKFLOW_TYPES)
        query = f"EventWorkflowId='{workflow_id}' " f"AND WorkflowType IN ({wf_types_in}) " f"AND ExecutionStatus='Running'"

        cancelled = 0
        try:
            async for wf in wrapper.client.list_workflows(query=query):
                try:
                    await wrapper.client.get_workflow_handle(wf.id).cancel()
                    cancelled += 1
                except Exception as exc:  # noqa: BLE001
                    # Per-listener failures don't block sweep of the rest.
                    logger.warning(
                        f"Failed to cancel canary listener {wf.id}: {exc}",
                        workflow_id=workflow_id,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Visibility query for canary listeners failed: {exc} " f"(query={query!r})",
                workflow_id=workflow_id,
            )

        if cancelled:
            logger.info(
                "Canary listeners cancelled",
                workflow_id=workflow_id,
                count=cancelled,
            )
        return cancelled

    # =========================================================================
    # EXECUTION RUNS
    # =========================================================================

    async def _spawn_run(
        self, trigger_node_id: str, trigger_data: Dict[str, Any], wait: bool = False, workflow_id: Optional[str] = None
    ) -> Optional[asyncio.Task]:
        """Spawn a new execution run for a specific workflow."""
        if not workflow_id:
            # Backward compatibility: find workflow for this trigger node
            for wid, state in self._deployments.items():
                if state.is_running and any(n["id"] == trigger_node_id for n in state.nodes):
                    workflow_id = wid
                    break

        if not workflow_id or not self.is_workflow_deployed(workflow_id):
            return None

        state = self._deployments[workflow_id]

        # Check concurrent limit for this workflow
        workflow_runs = self._active_runs.get(workflow_id, {})
        active_count = sum(1 for k in workflow_runs if k.startswith("run_"))
        max_concurrent = self._settings.get("max_concurrent_runs", 100)
        if active_count >= max_concurrent:
            logger.warning("Max concurrent runs reached", workflow_id=workflow_id, active=active_count)
            return None

        # Generate run ID
        self._run_counters[workflow_id] = self._run_counters.get(workflow_id, 0) + 1
        run_id = f"run_{state.deployment_id}_{self._run_counters[workflow_id]}"

        await self._notify(
            "run_started",
            {"run_id": run_id, "workflow_id": workflow_id, "trigger_node_id": trigger_node_id, "active_runs": active_count + 1},
            workflow_id,
        )

        async def execute():
            try:
                result = await self._execute_from_trigger(run_id, trigger_node_id, trigger_data, workflow_id)
                await self._notify(
                    "run_completed",
                    {
                        "run_id": run_id,
                        "workflow_id": workflow_id,
                        "success": result.get("success", False),
                        "execution_time": result.get("execution_time"),
                    },
                    workflow_id,
                )
            except asyncio.CancelledError:
                logger.debug("Run cancelled", run_id=run_id, workflow_id=workflow_id)
            except Exception as e:
                logger.error("Run failed", run_id=run_id, workflow_id=workflow_id, error=str(e))
                await self._notify("run_failed", {"run_id": run_id, "error": str(e)}, workflow_id)
            finally:
                if workflow_id in self._active_runs:
                    self._active_runs[workflow_id].pop(run_id, None)

        task = asyncio.create_task(execute())
        if workflow_id not in self._active_runs:
            self._active_runs[workflow_id] = {}
        self._active_runs[workflow_id][run_id] = task

        if wait:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            return None

        return task

    async def _execute_from_trigger(
        self, run_id: str, trigger_node_id: str, trigger_data: Dict[str, Any], workflow_id: str
    ) -> Dict[str, Any]:
        """Execute workflow from a trigger node."""
        state = self._deployments.get(workflow_id)
        if not state:
            return {"success": False, "error": f"Workflow {workflow_id} not deployed"}

        start_time = time.time()
        run_session_id = f"{state.session_id}_{run_id}"

        # Store trigger output
        trigger_output = trigger_data.get("event_data", trigger_data)
        await self._store_output(run_session_id, trigger_node_id, "output_0", trigger_output)

        # Get downstream nodes
        downstream = self._get_downstream_nodes(trigger_node_id, state.nodes, state.edges)

        if not downstream:
            return {
                "success": True,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "nodes_executed": [trigger_node_id],
                "execution_time": time.time() - start_time,
                "message": "No downstream nodes",
            }

        # Build filtered graph
        run_filter = {trigger_node_id} | {n["id"] for n in downstream}
        logger.debug(f"[Run] run_filter has {len(run_filter)} nodes")

        filtered_nodes = []
        for node in state.nodes:
            if node["id"] not in run_filter:
                continue
            node_copy = node.copy()
            node_type = node.get("type", "")
            if node["id"] == trigger_node_id:
                node_copy["_pre_executed"] = True
                node_copy["_trigger_output"] = trigger_output
            elif node_type in WORKFLOW_TRIGGER_TYPES:
                # Non-firing triggers: pre-execute to prevent blocking as event waiters
                node_copy["_pre_executed"] = True
                node_copy["_trigger_output"] = {"not_triggered": True}
                logger.debug(f"[Run] Marking non-firing trigger as pre-executed: {node['id']} ({node_type})")
            filtered_nodes.append(node_copy)

        filtered_edges = [e for e in state.edges if e.get("source") in run_filter and e.get("target") in run_filter]
        logger.debug(f"[Run] filtered_edges: {len(filtered_edges)} edges")

        # Execute filtered graph with deployment's workflow_id for scoped status
        # Use Temporal for proper parallel branch execution
        status_callback = self._status_callbacks.get(workflow_id)
        result = await self._execute_workflow(
            nodes=filtered_nodes,
            edges=filtered_edges,
            session_id=run_session_id,
            status_callback=status_callback,
            skip_clear_outputs=True,
            workflow_id=workflow_id,  # Pass deployment's workflow_id for status scoping
            use_temporal=True,  # Force Temporal for parallel node execution
        )

        result["run_id"] = run_id
        result["workflow_id"] = workflow_id
        result["trigger_node_id"] = trigger_node_id
        return result

    def _get_downstream_nodes(self, node_id: str, nodes: List[Dict], edges: List[Dict]) -> List[Dict]:
        """Get all downstream nodes from a trigger."""
        downstream_ids = set()
        node_types = {n["id"]: n.get("type", "") for n in nodes}
        nodes_with_inputs = {e.get("target") for e in edges if e.get("target")}

        def collect(current_id: str):
            for edge in edges:
                if edge.get("source") != current_id:
                    continue
                target_id = edge.get("target")
                if not target_id or target_id in downstream_ids:
                    continue

                target_type = node_types.get(target_id, "")
                is_trigger = target_type in WORKFLOW_TRIGGER_TYPES

                # Stop at trigger nodes — they are independent event listeners,
                # not regular execution nodes. Each trigger spawns its own
                # execution run when its event fires (n8n pattern).
                if is_trigger:
                    continue

                downstream_ids.add(target_id)
                collect(target_id)

        collect(node_id)

        # Include config nodes connected to downstream nodes
        for edge in edges:
            target = edge.get("target")
            source = edge.get("source")
            handle = edge.get("targetHandle", "")

            is_config = handle and handle.startswith("input-") and handle != "input-main"
            if is_config and target in downstream_ids and source not in downstream_ids:
                # Never include trigger nodes as config dependencies -
                # they are event listeners, not configuration providers
                source_type = node_types.get(source, "")
                if source_type in WORKFLOW_TRIGGER_TYPES:
                    continue
                downstream_ids.add(source)

        # Include sub-nodes connected to toolkit nodes (n8n Sub-Node pattern).
        # Service nodes connect to a toolkit's input-main (not a config
        # handle) and need to be included so the toolkit can discover
        # them. ``TOOLKIT_NODE_TYPES`` is the canonical set; today only
        # ``androidTool`` is in it.
        toolkit_node_ids = {n["id"] for n in nodes if n.get("type") in TOOLKIT_NODE_TYPES and n["id"] in downstream_ids}
        for edge in edges:
            target = edge.get("target")
            source = edge.get("source")
            # Include nodes that connect to toolkit nodes
            if target in toolkit_node_ids and source not in downstream_ids:
                downstream_ids.add(source)
                logger.debug(f"[Deployment] Including sub-node {source} connected to toolkit {target}")

        # Include tool nodes connected to AI Agent nodes (for capability discovery)
        # When a child agent is included, we need its connected tools so the parent
        # can discover what capabilities the child has
        from constants import AI_AGENT_TYPES

        agent_node_ids = {n["id"] for n in nodes if n.get("type") in AI_AGENT_TYPES and n["id"] in downstream_ids}
        for edge in edges:
            target = edge.get("target")
            source = edge.get("source")
            target_handle = edge.get("targetHandle", "")
            # Include tool nodes connected to agent's input-tools handle
            if target in agent_node_ids and target_handle == "input-tools" and source not in downstream_ids:
                downstream_ids.add(source)
                logger.debug(f"[Deployment] Including tool node {source} connected to agent {target}")

        return [n for n in nodes if n["id"] in downstream_ids]

    # =========================================================================
    # HELPERS
    # =========================================================================

    # =========================================================================
    # POLLING TRIGGER FACTORIES
    # =========================================================================

    # _create_poll_coroutine + _create_gmail_poll_coroutine +
    # _create_email_poll_coroutine REMOVED in Wave 11.I, milestone L.
    # Polling-coroutine factories now self-register from each plugin's
    # PollingTriggerNode subclass (services.plugin.PollingTriggerNode)
    # via services.deployment.poll_registry.register_poll_coroutine_factory.
    # The dispatch path lives ~140 lines up in _setup_event_trigger.

    async def _load_settings(self):
        """Load deployment settings from database."""
        try:
            db_settings = await self.database.get_deployment_settings()
            if db_settings:
                self._settings.update(
                    {
                        "stop_on_error": db_settings.get("stop_on_error", False),
                        "max_concurrent_runs": db_settings.get("max_concurrent_runs", 100),
                        "use_parallel_executor": db_settings.get("use_parallel_executor", True),
                    }
                )
        except Exception:
            pass

    async def _notify(self, event: str, data: Dict[str, Any], workflow_id: Optional[str] = None):
        """Send status notification for a specific workflow."""
        status_callback = None
        if workflow_id:
            status_callback = self._status_callbacks.get(workflow_id)
        else:
            # Backward compatibility: use first available callback
            for cb in self._status_callbacks.values():
                if cb:
                    status_callback = cb
                    break

        if not status_callback:
            return

        try:
            await status_callback("__deployment__", event, {**data, "workflow_id": workflow_id, "timestamp": datetime.now().isoformat()})
        except Exception as e:
            logger.warning("Status callback failed", workflow_id=workflow_id, error=str(e))

    @staticmethod
    def _get_schedule_description(params: Dict[str, Any]) -> str:
        """Get human-readable schedule description from parameters."""
        frequency = params.get("frequency", "minutes")

        match frequency:
            case "seconds":
                interval = params.get("interval", 30)
                return f"Every {interval} seconds"
            case "minutes":
                interval = params.get("interval_minutes", 5)
                return f"Every {interval} minutes"
            case "hours":
                interval = params.get("interval_hours", 1)
                return f"Every {interval} hours"
            case "days":
                time_str = params.get("daily_time", "09:00")
                return f"Daily at {time_str}"
            case "weeks":
                weekday = params.get("weekday", "1")
                time_str = params.get("weekly_time", "09:00")
                days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
                day_name = days[int(weekday)] if str(weekday).isdigit() else weekday
                return f"Weekly on {day_name} at {time_str}"
            case "months":
                day = params.get("month_day", "1")
                time_str = params.get("monthly_time", "09:00")
                return f"Monthly on day {day} at {time_str}"
            case "once":
                return "Once (no repeat)"
            case _:
                return "Unknown schedule"
