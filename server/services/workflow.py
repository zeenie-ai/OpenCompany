"""Workflow Service - Facade for workflow execution and deployment.

This is a thin facade that delegates to specialized modules:
- NodeExecutor: Single node execution
- ParameterResolver: Template variable resolution
- DeploymentManager: Event-driven deployment lifecycle
- WorkflowExecutor: Parallel/sequential orchestration
- TemporalExecutor: Durable workflow execution (optional)

Following n8n/Conductor patterns for clean separation of concerns.
"""

import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from uuid import uuid4

from core.logging import get_logger
from constants import WORKFLOW_TRIGGER_TYPES
from services.node_executor import NodeExecutor
from services.parameter_resolver import ParameterResolver
from services.deployment import DeploymentManager
from services.execution import WorkflowExecutor, ExecutionCache
from services.execution.conditions import evaluate_edge_condition

if TYPE_CHECKING:
    from core.config import Settings
    from core.database import Database
    from core.cache import CacheService
    from services.ai import AIService
    from nodes.location._service import MapsService
    from services.text import TextService
    from nodes.android._dispatcher import AndroidService
    from services.temporal import TemporalExecutor

logger = get_logger(__name__)

_parallel_user_id: ContextVar[str] = ContextVar(
    "workflow_parallel_user_id",
    default="owner",
)
_fallback_execution_scope: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "workflow_fallback_execution_scope", default=None,
)


# A Run's result reports failures as ``errors`` plus their first message as
# ``error`` on every path, because the editor's Run dialog shows only
# ``error`` (docs-internal/TEMPORAL_ARCHITECTURE.md, "4. The run result").
def _run_errors(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A run's failures. A run-level ``error`` alone becomes the one entry."""
    if result.get("errors"):
        return list(result["errors"])
    return [{"error": result["error"]}] if result.get("error") else []


def _node_failure(node_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """One node's entry in ``errors``, shaped like MachinaWorkflow's."""
    failure = {"node_id": node_id, "error": result.get("error") or "Unknown error"}
    failure.update({key: result[key] for key in ("hint", "requires_user_action", "retryable") if key in result})
    return failure


class WorkflowService:
    """Workflow execution and deployment service.

    Thin facade delegating to specialized modules for:
    - Node execution (NodeExecutor)
    - Parameter resolution (ParameterResolver)
    - Deployment lifecycle (DeploymentManager)
    - Workflow orchestration (WorkflowExecutor or TemporalExecutor)
    """

    def __init__(
        self,
        database: "Database",
        ai_service: "AIService",
        maps_service: "MapsService",
        text_service: "TextService",
        android_service: "AndroidService",
        cache: "CacheService",
        settings: "Settings",
    ):
        self.database = database
        self.settings = settings

        # In-memory output storage (fast access during execution).
        # Single-threaded asyncio: synchronous dict ops between awaits
        # are atomic, so store_node_output / clear_all_outputs need no
        # lock. The one real concurrency hazard is the DB-fallback
        # re-cache in get_node_output, which yields between read and
        # write -- handled with dict.setdefault (atomic at the GIL).
        self._outputs: Dict[str, Dict[str, Any]] = {}

        # Initialize NodeExecutor
        self._node_executor = NodeExecutor(
            database=database,
            ai_service=ai_service,
            maps_service=maps_service,
            text_service=text_service,
            android_service=android_service,
            settings=settings,
            output_store=self.store_node_output,
        )

        # Initialize ParameterResolver
        self._param_resolver = ParameterResolver(
            database=database,
            get_output_fn=self.get_node_output,
        )

        # Initialize Execution Cache
        self._execution_cache = ExecutionCache(cache)
        self._workflow_executor: Optional[WorkflowExecutor] = None

        # Temporal executor (set via set_temporal_executor when enabled)
        self._temporal_executor: Optional["TemporalExecutor"] = None

        # Initialize DeploymentManager (lazy - needs broadcaster)
        self._deployment_manager: Optional[DeploymentManager] = None
        self._broadcaster = None

        # Deployment settings
        self._settings = {
            "stop_on_error": False,
            "max_concurrent_runs": 100,
            "use_parallel_executor": True,
        }

    def set_temporal_executor(self, executor: "TemporalExecutor") -> None:
        """Set the Temporal executor for durable workflow execution.

        Args:
            executor: Configured TemporalExecutor instance
        """
        self._temporal_executor = executor
        logger.info("Temporal executor configured for workflow execution")

    def _get_deployment_manager(self) -> DeploymentManager:
        """Get or create DeploymentManager."""
        if self._deployment_manager is None:
            from services.status_broadcaster import get_status_broadcaster

            self._broadcaster = get_status_broadcaster()
            self._deployment_manager = DeploymentManager(
                database=self.database,
                execute_workflow_fn=self.execute_workflow,
                store_output_fn=self.store_node_output,
                broadcaster=self._broadcaster,
            )
        return self._deployment_manager

    def _get_workflow_executor(self, status_callback=None) -> WorkflowExecutor:
        """Get or create WorkflowExecutor."""
        if self._workflow_executor is None or status_callback:
            self._workflow_executor = WorkflowExecutor(
                cache=self._execution_cache,
                node_executor=self._execute_node_adapter,
                status_callback=status_callback,
                dlq_enabled=self.settings.dlq_enabled,
            )
        return self._workflow_executor

    # =========================================================================
    # NODE EXECUTION
    # =========================================================================

    def _get_workspace_dir(self, workflow_slug: Optional[str]) -> str:
        """Get or create workspace directory for a workflow.

        Keyed by the human-readable ``workflow_slug`` (Wave 14) so the
        on-disk dir name matches the Temporal Web UI listing and the
        sidebar entry. Callers that don't have a slug yet pass
        ``"default"`` (one-off Run, no DB row) — preserved as the
        anonymous workspace.
        """
        base = Path(self.settings.workspace_base_resolved)
        slug = workflow_slug or "default"
        workspace = base / slug
        workspace.mkdir(parents=True, exist_ok=True)
        resolved = str(workspace.resolve())
        logger.info("[Workspace] workflow_slug=%s -> %s", slug, resolved)
        return resolved

    async def _resolve_workflow_slug(self, workflow_id: Optional[str]) -> Optional[str]:
        """Look up the slug for a workflow_id (one query, opportunistic).

        Returns ``None`` if the row doesn't exist (one-off Run without
        a saved workflow). Callers fall back to ``"default"`` in that
        case via :meth:`_get_workspace_dir`.
        """
        if not workflow_id:
            return None
        try:
            wf = await self.database.get_workflow(workflow_id)
            return wf.slug if wf and wf.slug else None
        except Exception:
            return None

    async def execute_node(
        self,
        node_id: str,
        node_type: str,
        parameters: Dict[str, Any],
        nodes: List[Dict] = None,
        edges: List[Dict] = None,
        session_id: str = "default",
        execution_id: str = None,
        workflow_id: str = None,
        workflow_slug: str = None,
        outputs: Dict[str, Any] = None,
        extras: Optional[Dict[str, Any]] = None,
        user_id: str = "owner",
    ) -> Dict[str, Any]:
        """Execute a single workflow node.

        ``extras`` is merged into the ``NodeContext.raw`` dict so callers
        (notably the F4.A per-type activity wrapper) can plumb context
        fields like ``auto_rebind_tools`` through without adding a
        dedicated parameter per flag.
        """
        context = await self._node_run_context(
            nodes=nodes, edges=edges, session_id=session_id, execution_id=execution_id, workflow_id=workflow_id,
            workflow_slug=workflow_slug, outputs=outputs, extras=extras, user_id=user_id,
        )
        recorded_pi_admission = context.get("pi_harness_version") == 1
        from temporalio import activity
        if not activity.in_activity() and context.get("coding_native_version") != 1:
            # Inline agents keep their runtime, but cannot silently invoke the
            # old Coding Agent implementation from a new admission.
            admitted_generation = int(context.get("generation") or 0)
            previous_control = None
            recorded_harness = context.get("pi_harness_version") == 1
            if admitted_generation > 0 and workflow_id:
                previous_control = await self.database.get_latest_workflow_control(workflow_id)
            if (previous_control is not None and previous_control.generation == admitted_generation
                    and (previous_control.resource_manifest or {}).get("pi_harness_version") != 1):
                context.pop("pi_harness_version", None)
                context["coding_native_version"] = 1
            else:
                generation_admitted_pi = bool(previous_control is not None
                    and previous_control.generation == admitted_generation
                    and (previous_control.resource_manifest or {}).get("pi_harness_version") == 1)
                recorded_pi_admission = recorded_pi_admission or generation_admitted_pi
                if not recorded_harness and not generation_admitted_pi:
                    from services.coding_agent_runtime import assert_pi_admissions_enabled, coding_agent_ancestors
                    if node_type != "coding_agent" and node_id in coding_agent_ancestors(nodes or [], edges or []):
                        try:
                            assert_pi_admissions_enabled(self.settings)
                        except Exception as exc:
                            from services.plugin import NodeUserError
                            if not isinstance(exc, NodeUserError):
                                raise
                            return {"success": False, "error": str(exc), **exc.as_dict()}
                context.setdefault("pi_harness_version", 1)
                if not recorded_harness and not generation_admitted_pi:
                    from services.coding_agent_runtime import coding_agent_ancestors, freeze_pi_local_control
                    if node_id in coding_agent_ancestors(nodes or [], edges or []):
                        freeze_pi_local_control(context)
        if node_type == "coding_agent":
            # A native v1 Activity may be retried months after admission. Its
            # callable remains native; new manual/sequential starts enter the
            # Pi workflow directly rather than waiting inside a node Activity.
            legacy_activity = activity.in_activity() and context.get("pi_harness_version") != 1
            legacy_admission = context.get("coding_native_version") == 1
            generation = int(context.get("generation") or 0)
            if generation > 0 and workflow_id and not legacy_activity and not legacy_admission:
                control = await self.database.get_latest_workflow_control(workflow_id)
                legacy_admission = bool(control and control.generation == generation
                                        and (control.resource_manifest or {}).get("pi_harness_version") != 1)
            if not legacy_activity and not legacy_admission:
                if activity.in_activity():
                    return {"success": False, "error": "Pi Coding Agent must be dispatched as a workflow, not a node Activity",
                            "error_type": "CodingAgentWorkflowRequired"}
                return await self._execute_pi_coding_node(node_id, parameters, context, check_admission=not recorded_pi_admission)
        return await self._node_executor.execute(
            node_id=node_id,
            node_type=node_type,
            parameters=parameters,
            context=context,
            resolve_params_fn=self._param_resolver.resolve,
        )

    async def _node_run_context(
        self,
        *,
        nodes: Optional[List[Dict]],
        edges: Optional[List[Dict]],
        session_id: str,
        execution_id: Optional[str],
        workflow_id: Optional[str],
        workflow_slug: Optional[str],
        outputs: Optional[Dict[str, Any]],
        extras: Optional[Dict[str, Any]],
        user_id: str,
    ) -> Dict[str, Any]:
        """The context a node runs in (:meth:`execute_node`)."""
        # Resolve slug from DB if caller passed only workflow_id.
        if workflow_slug is None:
            workflow_slug = await self._resolve_workflow_slug(workflow_id)
        workspace_dir = self._get_workspace_dir(workflow_slug)
        context = {
            "nodes": nodes,
            "edges": edges,
            "session_id": session_id,
            "execution_id": execution_id,
            "workflow_id": workflow_id,  # UUID — stable system identity, FK target
            "workflow_slug": workflow_slug,  # Human-readable, mutable on rename
            "user_id": str(user_id or "owner"),
            "workspace_dir": workspace_dir,  # Per-workflow filesystem for nodes and agents
            "get_output_fn": self.get_node_output,
            "outputs": outputs or {},  # Upstream node outputs for data flow (e.g., taskTrigger -> chatAgent)
        }
        if extras:
            context.update(extras)
        return context

    async def prepare_node_parameters(
        self,
        node_id: str,
        node_type: str,
        parameters: Dict[str, Any],
        nodes: List[Dict] = None,
        edges: List[Dict] = None,
        session_id: str = "default",
        execution_id: str = None,
        workflow_id: str = None,
        workflow_slug: str = None,
        outputs: Dict[str, Any] = None,
        extras: Optional[Dict[str, Any]] = None,
        user_id: str = "owner",
    ) -> Dict[str, Any]:
        """The parameters :meth:`execute_node` would run a node with, its
        templates resolved but no API keys added, without running it. A card
        that keeps what a graph node sent rebuilds it here
        (services/approvals/tool_calls.py)."""
        context = await self._node_run_context(
            nodes=nodes, edges=edges, session_id=session_id, execution_id=execution_id, workflow_id=workflow_id,
            workflow_slug=workflow_slug, outputs=outputs, extras=extras, user_id=user_id,
        )
        params, _ = await self._node_executor.prepare_parameters(
            node_id, node_type, parameters, context, resolve_params_fn=self._param_resolver.resolve, inject_keys=False,
        )
        return params

    async def _execute_pi_coding_node(self, node_id: str, parameters: dict, context: dict, *, check_admission: bool = True) -> dict:
        """Admit an isolated manual Coding Agent invocation into Temporal."""
        from services.coding_agent_runtime import assert_pi_admissions_enabled
        admission_error = None
        try:
            if check_admission:
                assert_pi_admissions_enabled(self.settings)
        except Exception as exc:
            from services.plugin import NodeUserError
            if not isinstance(exc, NodeUserError):
                raise
            admission_error = {"success": False, "error": str(exc), **exc.as_dict()}
        if not self.settings.temporal_enabled or self._temporal_executor is None:
            if admission_error is not None:
                return admission_error
            return {"success": False, "error": "Coding Agent requires a connected Temporal runtime. Start Temporal and its workers, then run again.",
                    "error_type": "CodingAgentTemporalRequired", "requires_user_action": True, "retryable": False}
        from services.workflow_sanitizer import sanitize_runtime_payload
        from services.temporal.trigger_listener_workflow import event_workflow_search_attributes
        snapshot = dict(context.get("parameter_snapshot") or {})
        for node in context.get("nodes") or []:
            identifier = str(node.get("id") or "")
            if identifier and identifier not in snapshot:
                snapshot[identifier] = await self.database.get_node_parameters(identifier) or {}
        snapshot[node_id] = {**(snapshot.get(node_id) or await self.database.get_node_parameters(node_id) or {}), **parameters}
        safe_context = {key: value for key, value in context.items() if key != "get_output_fn"}
        admitted = sanitize_runtime_payload({**safe_context, "node_id": node_id, "node_type": "coding_agent",
                    "node_data": snapshot[node_id], "parameter_snapshot": snapshot,
                    "inputs": context.get("outputs") or {},
                    "pi_harness_version": 1, "temporal_worker_pool_enabled": self.settings.temporal_worker_pool_enabled})
        if check_admission:
            from services.coding_agent_runtime import freeze_pi_local_control
            freeze_pi_local_control(admitted)
        execution_id = str(context.get("execution_id") or uuid4().hex)
        admitted["execution_id"] = execution_id
        if not admitted.get("root_execution_id"):
            admitted["root_execution_id"] = execution_id
        import hashlib
        identifier = "coding-pi-" + hashlib.sha256(
            f"{context.get('workflow_id') or ''}:{node_id}:{execution_id}".encode()
        ).hexdigest()
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError
        from services.temporal.pi_protocol import canonical_digest
        digest = canonical_digest(admitted)
        options = {"id": identifier, "task_queue": self.settings.temporal_task_queue,
                   "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE, "memo": {"pi_manual_digest": digest}}
        attributes = event_workflow_search_attributes(context.get("workflow_id"))
        if attributes is not None:
            options["search_attributes"] = attributes
        client = self._temporal_executor.client
        async def recorded_handle(*, run_id=None):
            handle = client.get_workflow_handle(identifier, run_id=run_id)
            description = await handle.describe()
            if (await description.memo()).get("pi_manual_digest") != digest:
                return None
            return handle
        if admission_error is not None:
            from temporalio.service import RPCError, RPCStatusCode
            try:
                handle = await recorded_handle()
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                return admission_error
        else:
            try:
                handle = await client.start_workflow("PiCodingAgentWorkflow", admitted, **options)
            except WorkflowAlreadyStartedError as exc:
                handle = await recorded_handle(run_id=exc.run_id)
        if handle is None:
            return {"success": False, "error": "The recorded Coding invocation was retried with different content.",
                    "error_type": "PiAdmissionConflict", "requires_user_action": True, "retryable": False}
        # This path is outside counted node Activities. A response retry waits
        # for the admitted execution chain, including an already completed one.
        return await handle.result()

    async def _execute_node_adapter(
        self,
        node_id: str,
        node_type: str,
        parameters: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Adapter for WorkflowExecutor to call NodeExecutor."""
        # Conversation scope keys travel as extras so the Context descriptor
        # builder sees ``generation`` on the in-process path as well.
        extras = {
            key: context[key]
            for key in (
                "generation",
                "graphVersion",
                "root_execution_id",
                "context_execution_id",
                "context_session_id",
                "data_scope_id",
                "pi_harness_version",
                "coding_native_version",
                "pi_local_control_version",
                "pi_local_control_state",
                "pi_local_control_revision",
            )
            if key in context
        }
        extras.update(_fallback_execution_scope.get() or {})
        return await self.execute_node(
            node_id=node_id,
            node_type=node_type,
            parameters=parameters,
            nodes=context.get("nodes"),
            edges=context.get("edges"),
            session_id=context.get("session_id", "default"),
            execution_id=context.get("execution_id"),
            workflow_id=context.get("workflow_id"),
            workflow_slug=context.get("workflow_slug"),
            extras=extras or None,
            user_id=(
                context.get("user_id")
                or _parallel_user_id.get()
            ),
        )

    # =========================================================================
    # WORKFLOW EXECUTION
    # =========================================================================

    async def execute_workflow(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        session_id: str = "default",
        status_callback=None,
        use_parallel: bool = None,
        skip_clear_outputs: bool = False,
        workflow_id: Optional[str] = None,
        use_temporal: bool = None,
        graph_version: int = 0,
        generation: int = 0,
        user_id: str = "owner",
        parameter_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute entire workflow.

        Args:
            nodes: Workflow nodes
            edges: Workflow edges
            session_id: Session identifier
            status_callback: Status update callback
            use_parallel: Force parallel/sequential execution
            skip_clear_outputs: Skip clearing outputs (for deployment runs)
            workflow_id: Workflow ID for per-workflow status scoping (n8n pattern)
            use_temporal: Force Temporal execution (None = use settings default)
            graph_version: Normalized graph version for the Context topology
            generation: Durable deployment generation for the Context store
            user_id: Authenticated server-owned user identity
        """
        start_time = time.time()
        user_id = str(user_id or "owner")

        from services.coding_agent_runtime import assert_pi_admissions_enabled, enabled_coding_agents
        pi_required = enabled_coding_agents(nodes)
        admitted_control = None
        if pi_required and int(generation or 0) > 0 and workflow_id:
            admitted_control = await self.database.get_latest_workflow_control(workflow_id)
            if admitted_control is not None and admitted_control.generation == int(generation):
                pi_required = (admitted_control.resource_manifest or {}).get("pi_harness_version") == 1
        if pi_required:
            if admitted_control is None:
                try:
                    assert_pi_admissions_enabled(self.settings)
                except Exception as exc:
                    from services.plugin import NodeUserError
                    if not isinstance(exc, NodeUserError):
                        raise
                    return {**self._error_result(str(exc), start_time), **exc.as_dict()}
            if not (self.settings.temporal_enabled if use_temporal is None else use_temporal) or self._temporal_executor is None:
                return {**self._error_result(
                    "Coding Agent requires a connected Temporal runtime. Start Temporal and its workers, then run again.", start_time,
                ), "error_type": "CodingAgentTemporalRequired", "requires_user_action": True, "retryable": False}

        # Clear outputs unless skipped
        if not skip_clear_outputs:
            await self.clear_all_outputs(session_id)

        if not nodes:
            return self._error_result("No nodes in workflow", start_time)

        # Find start node
        start_node = self._find_start_node(nodes)
        if not start_node:
            return self._error_result("No start node found", start_time)

        # Determine execution mode
        if use_parallel is None:
            use_parallel = self._settings.get("use_parallel_executor", True)

        # Check if Temporal execution is requested
        if use_temporal is None:
            use_temporal = self.settings.temporal_enabled

        # Routing visibility — always logged at INFO so silent fallback to
        # parallel/sequential is diagnosable without grepping warnings.
        # If `chosen_path` is anything other than "temporal" while Temporal
        # is enabled, that's the bug.
        executor_wired = self._temporal_executor is not None
        if use_temporal and executor_wired:
            chosen = "temporal"
        elif use_parallel and self.settings.redis_enabled:
            chosen = "parallel"
        else:
            chosen = "sequential"
        logger.info(
            "[execute_workflow] routing decision: %s " "(temporal_enabled=%s, executor_wired=%s, parallel=%s, redis=%s)",
            chosen,
            use_temporal,
            executor_wired,
            use_parallel,
            self.settings.redis_enabled,
            extra={"workflow_id": workflow_id},
        )

        # Use Temporal if enabled and executor is configured
        if use_temporal and self._temporal_executor is not None:
            return await self._execute_temporal(
                nodes,
                edges,
                session_id,
                status_callback,
                start_time,
                workflow_id,
                graph_version,
                generation,
                user_id,
                **({"parameter_snapshot": parameter_snapshot} if parameter_snapshot is not None else {}),
            )

        # Loud error if Temporal was requested but the executor never finished
        # wiring. Previously a WARNING; bumped to ERROR because a silent
        # fallthrough during a deployed-trigger run looks identical to a
        # successful Temporal run from the user's perspective.
        if use_temporal and self._temporal_executor is None:
            logger.error(
                "Temporal execution requested but executor not configured. "
                "Falling back to %s execution. Check 'Temporal Worker started' "
                "appeared in startup logs and that the Python client successfully "
                "connected (server-up != client-connected).",
                chosen,
                extra={"workflow_id": workflow_id},
            )

        # Freeze the same generation/configuration at the fallback boundary.
        # A pre-Pi generation must not become a new manual Pi admission merely
        # because the in-process executor omitted its recorded runtime marker.
        fallback_scope = {"generation": int(generation or 0), "graphVersion": graph_version}
        if parameter_snapshot is not None:
            fallback_scope["parameter_snapshot"] = parameter_snapshot
        if (admitted_control is not None and admitted_control.generation == int(generation or 0)
                and (admitted_control.resource_manifest or {}).get("pi_harness_version") != 1):
            fallback_scope["coding_native_version"] = 1
        scope_token = _fallback_execution_scope.set(fallback_scope)
        try:
            if use_parallel and self.settings.redis_enabled:
                return await self._execute_parallel(
                    nodes, edges, session_id, status_callback, start_time, workflow_id, user_id,
                )
            return await self._execute_sequential(
                nodes, edges, session_id, status_callback, start_time, workflow_id, user_id,
            )
        finally:
            _fallback_execution_scope.reset(scope_token)

    async def _execute_temporal(
        self,
        nodes,
        edges,
        session_id,
        status_callback,
        start_time,
        workflow_id: Optional[str] = None,
        graph_version: int = 0,
        generation: int = 0,
        user_id: str = "owner",
        parameter_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """Execute with Temporal for durable workflow orchestration."""
        # Use passed workflow_id (from deployment) or generate new one
        if not workflow_id:
            workflow_id = f"temporal_{session_id}_{int(time.time() * 1000)}"

        # Look up the human-readable slug so Temporal's Web UI shows
        # ``<slug>_<uuid8>`` instead of the opaque UUID.
        workflow_slug = await self._resolve_workflow_slug(workflow_id)

        logger.info(
            "Executing workflow via Temporal",
            workflow_id=workflow_id,
            workflow_slug=workflow_slug,
            node_count=len(nodes),
        )

        result = await self._temporal_executor.execute_workflow(
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            session_id=session_id,
            enable_caching=True,
            workflow_slug=workflow_slug,
            graph_version=graph_version,
            generation=generation,
            user_id=user_id,
            **({"parameter_snapshot": parameter_snapshot} if parameter_snapshot is not None else {}),
        )

        # No status is sent here: every node already reported its own final
        # `success` or `error`, with its result, while the run executed (the
        # per-type activity, the legacy activity through handle_execute_node,
        # and the agent workflow each broadcast it). Sending another status
        # per node after the run would overwrite those with a word the editor
        # does not know and a different payload. `status_callback` stays in
        # the signature for the paths that need it.
        return {
            "success": result.get("success", False),
            "execution_id": result.get("execution_id"),
            "nodes_executed": result.get("nodes_executed", []),
            "outputs": result.get("outputs", {}),
            "errors": result.get("errors", []),
            "error": result.get("error"),
            "total_nodes": result.get("total_nodes", 0),
            "completed_nodes": result.get("completed_nodes", 0),
            "execution_time": result.get("execution_time", time.time() - start_time),
            "temporal_execution": True,
            "timestamp": datetime.now().isoformat(),
        }

    async def _execute_parallel(
        self,
        nodes,
        edges,
        session_id,
        status_callback,
        start_time,
        workflow_id: Optional[str] = None,
        user_id: str = "owner",
    ) -> Dict:
        """Execute with parallel orchestration engine."""
        # Use passed workflow_id (from deployment) or generate new one
        if not workflow_id:
            workflow_id = f"workflow_{session_id}_{int(time.time() * 1000)}"
        executor = self._get_workflow_executor(status_callback)

        token = _parallel_user_id.set(str(user_id or "owner"))
        try:
            result = await executor.execute_workflow(
                workflow_id=workflow_id,
                nodes=nodes,
                edges=edges,
                session_id=session_id,
                enable_caching=True,
            )
        finally:
            _parallel_user_id.reset(token)

        errors = _run_errors(result)
        nodes_executed = result.get("nodes_executed", [])
        return {
            "success": result.get("success", False),
            "execution_id": result.get("execution_id"),
            "nodes_executed": nodes_executed,
            "outputs": result.get("outputs", {}),
            "errors": errors,
            "error": errors[0].get("error") if errors else None,
            "total_nodes": result.get("total_nodes", 0),
            "completed_nodes": len(nodes_executed),
            "execution_time": result.get("execution_time", time.time() - start_time),
            "parallel_execution": True,
            "timestamp": datetime.now().isoformat(),
        }

    async def _execute_sequential(
        self,
        nodes,
        edges,
        session_id,
        status_callback,
        start_time,
        workflow_id: Optional[str] = None,
        user_id: str = "owner",
    ) -> Dict:
        """Execute nodes sequentially (fallback mode)."""
        execution_id = uuid4().hex
        start_node = self._find_start_node(nodes)
        execution_order = self._build_execution_order(start_node, nodes, edges)
        conditional_edges = self._incoming_conditional_edges(edges)

        results = {}
        executed = []
        # What each finished node looks like to an edge condition, in the
        # envelope shape Temporal evaluates against. Skipped and failed nodes
        # are absent, so a condition that reads them does not match.
        envelopes: Dict[str, Dict[str, Any]] = {}

        for node in execution_order:
            node_id = node["id"]
            node_type = node.get("type", "unknown")

            # Skip pre-executed trigger nodes
            if node.get("_pre_executed"):
                envelopes[node_id] = {"success": True, "result": node.get("_trigger_output") or {}}
                executed.append(node_id)
                continue

            # Skip disabled nodes (n8n-style disable)
            if node.get("data", {}).get("disabled"):
                logger.debug(f"Skipping disabled node: {node_id}")
                executed.append(node_id)
                if status_callback:
                    try:
                        await status_callback(node_id, "skipped", {"disabled": True})
                    except Exception:
                        pass
                continue

            # Edge conditions: OR-any across the node's conditional incoming
            # edges, and skipping is not transitive, the same rules as the
            # in-process executor and MachinaWorkflow.
            incoming = conditional_edges.get(node_id)
            if incoming and not self._incoming_conditions_met(incoming, envelopes):
                logger.info("Skipping node: no incoming edge condition matched", node_id=node_id)
                executed.append(node_id)
                if status_callback:
                    try:
                        await status_callback(node_id, "skipped", {"reason": "condition"})
                    except Exception:
                        pass
                continue

            # Notify executing
            if status_callback:
                try:
                    await status_callback(node_id, "executing", {})
                except Exception:
                    pass

            # Execute with workflow_id for per-workflow status scoping (n8n pattern)
            result = await self.execute_node(
                node_id=node_id,
                node_type=node_type,
                parameters={},
                nodes=nodes,
                edges=edges,
                session_id=session_id,
                execution_id=execution_id,
                workflow_id=workflow_id,
                user_id=user_id,
                extras=_fallback_execution_scope.get(),
            )

            results[node_id] = result
            executed.append(node_id)
            if result.get("success"):
                envelopes[node_id] = result

            # Notify the final status, in the editor's vocabulary: `success`
            # or `error`, the same words handle_execute_node sends.
            if status_callback:
                status = "success" if result.get("success") else "error"
                try:
                    await status_callback(node_id, status, result)
                except Exception:
                    pass

            if not result.get("success") and self._settings.get("stop_on_error"):
                break

        errors = [_node_failure(node_id, result) for node_id, result in results.items() if not result.get("success")]
        return {
            "success": all(r.get("success", False) for r in results.values()),
            "execution_id": execution_id,
            "nodes_executed": executed,
            "node_results": results,
            "errors": errors,
            "error": errors[0]["error"] if errors else None,
            "total_nodes": len(execution_order),
            "completed_nodes": len(executed) - len(errors),
            "execution_time": time.time() - start_time,
            "parallel_execution": False,
            "timestamp": datetime.now().isoformat(),
        }

    # =========================================================================
    # DEPLOYMENT
    # =========================================================================

    async def deploy_workflow(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        session_id: str = "default",
        status_callback=None,
        workflow_id: Optional[str] = None,
        graph_version: int = 0,
        generation: int = 0,
        user_id: str = "owner",
        parameter_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Deploy workflow in event-driven mode.

        Args:
            nodes: Workflow nodes
            edges: Workflow edges
            session_id: Session identifier
            status_callback: Status update callback
            workflow_id: Workflow ID for per-workflow deployment tracking
            graph_version: Normalized graph contract version
            generation: Durable workflow-control generation
            user_id: Authenticated server-owned user identity
        """
        manager = self._get_deployment_manager()
        if int(graph_version or 0) < 2 or int(generation or 0) <= 0:
            return await manager.deploy(
                nodes,
                edges,
                session_id,
                status_callback,
                workflow_id,
                user_id=user_id,
            )
        return await manager.deploy(
            nodes, edges, session_id, status_callback, workflow_id, graph_version, generation,
            user_id=user_id,
            **({"parameter_snapshot": parameter_snapshot} if parameter_snapshot is not None else {}),
        )

    async def cancel_deployment(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel active deployment.

        Args:
            workflow_id: Specific workflow to cancel. If None, cancels first running deployment.
        """
        manager = self._get_deployment_manager()
        result = await manager.cancel(workflow_id)

        # Also cancel any in-flight CLI agent batches (claude_code_agent /
        # codex_agent) for this workflow. Best-effort — failure to cancel
        # CLI sessions must not block the deployment cancel.
        if workflow_id:
            try:
                from services.cli_agent.service import get_ai_cli_service

                cli_svc = get_ai_cli_service()
                cancelled = await cli_svc.cancel_workflow(workflow_id)
                if cancelled:
                    logger.info(
                        "[workflow] cancelled %d CLI agent session(s) for workflow %s",
                        cancelled,
                        workflow_id,
                    )
            except Exception as exc:
                logger.debug("[workflow] CLI agent cancel: %s", exc)

        return result

    def get_deployment_status(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """Get deployment status.

        Args:
            workflow_id: Get status for specific workflow. If None, returns global status.
        """
        manager = self._get_deployment_manager()
        return manager.get_status(workflow_id)

    def is_deployment_running(self, workflow_id: Optional[str] = None) -> bool:
        """Check if deployment is running.

        Args:
            workflow_id: Check specific workflow. If None, checks if ANY deployment is running.
        """
        manager = self._get_deployment_manager()
        if workflow_id:
            return manager.is_workflow_deployed(workflow_id)
        return manager.is_running

    def is_workflow_deployed(self, workflow_id: str) -> bool:
        """Check if a specific workflow is deployed."""
        return self._get_deployment_manager().is_workflow_deployed(workflow_id)

    def get_deployed_workflows(self) -> List[str]:
        """Get list of deployed workflow IDs."""
        return self._get_deployment_manager().get_deployed_workflows()

    def pause_deployment(self, workflow_id: str) -> bool:
        return self._get_deployment_manager().pause(workflow_id)

    async def resume_deployment(self, workflow_id: str) -> int:
        return await self._get_deployment_manager().resume(workflow_id)

    async def update_trigger_pause_status(self, workflow_id: str, *, paused: bool) -> int:
        return await self._get_deployment_manager().update_trigger_pause_status(workflow_id, paused=paused)

    # =========================================================================
    # OUTPUT STORAGE
    # =========================================================================

    async def store_node_output(
        self,
        session_id: str,
        node_id: str,
        output_name: str,
        data: Dict[str, Any],
    ) -> None:
        """Store node execution output."""
        key = f"{session_id}_{node_id}"
        # setdefault is atomic at the GIL level; this single statement
        # replaces the old check-then-set + indexed assignment with two
        # ops that are individually atomic and together race-free since
        # there is no await between them.
        self._outputs.setdefault(key, {})[output_name] = data
        logger.debug(
            f"[store_node_output] Stored in memory: key={key}, output_name={output_name}, _outputs keys={list(self._outputs.keys())}"
        )
        await self.database.save_node_output(node_id, session_id, output_name, data)

    async def get_node_output(
        self,
        session_id: str,
        node_id: str,
        output_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Get stored node output."""
        key = f"{session_id}_{node_id}"
        logger.debug(f"[get_node_output] Looking for: key={key}, output_name={output_name}, _outputs keys={list(self._outputs.keys())}")
        output = self._outputs.get(key, {}).get(output_name)
        logger.debug(f"[get_node_output] Memory lookup result: {'FOUND' if output else 'NOT_FOUND'}")

        if output is None:
            output = await self.database.get_node_output(node_id, session_id, output_name)
            logger.debug(f"[get_node_output] DB lookup result: {'FOUND' if output else 'NOT_FOUND'}")
            if output:
                # The await above yielded control; another coroutine may
                # have written a fresher value via store_node_output. Use
                # nested setdefault so we never overwrite an existing
                # in-memory entry with a possibly stale DB read. Then
                # re-read the slot to return whichever value won.
                self._outputs.setdefault(key, {}).setdefault(output_name, output)
                output = self._outputs[key][output_name]

        # Special handling for start nodes
        if output is None and node_id.startswith("start-"):
            import json

            params = await self.database.get_node_parameters(node_id)
            if params and "initial_data" in params:
                try:
                    output = json.loads(params.get("initial_data", "{}"))
                except Exception:
                    output = {}

        return output

    async def get_workflow_node_output(
        self,
        node_id: str,
        output_name: str = "output_0",
        session_id: str = "default",
    ) -> Dict[str, Any]:
        """Get stored output data for a node."""
        output = await self.get_node_output(session_id, node_id, output_name)
        if output:
            return {"success": True, "node_id": node_id, "data": output}
        return {"success": False, "node_id": node_id, "error": "No output found"}

    async def clear_all_outputs(self, session_id: str = "default") -> None:
        """Clear all outputs for a session."""
        keys = [k for k in self._outputs if k.startswith(f"{session_id}_")]
        for k in keys:
            self._outputs.pop(k, None)
        await self.database.clear_session_outputs(session_id)

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _find_start_node(self, nodes: List[Dict]) -> Optional[Dict]:
        """Find workflow entry point."""
        # Priority: start > cronScheduler > other triggers
        for node in nodes:
            if node.get("type") == "start":
                return node
        for node in nodes:
            if node.get("type") == "cronScheduler":
                return node
        for node in nodes:
            if node.get("type") in WORKFLOW_TRIGGER_TYPES:
                return node
        return None

    def _build_execution_order(self, start: Dict, nodes: List[Dict], edges: List[Dict]) -> List[Dict]:
        """Order the nodes reachable from ``start`` so each runs after its parents.

        Reachability is a BFS from the start node. The order is then a
        topological sort of that subgraph, ties broken by BFS discovery order,
        so it matches the old BFS order whenever that order already respected
        dependencies. A plain BFS could run a node before one of its parents
        (reached by a shorter path first), which matters once edge conditions
        read those parents. Edges from unreachable nodes (tools, memory and
        other config nodes wired into an agent) are ignored, as before. A
        cycle cannot be ordered, so its remaining nodes keep BFS order.
        """
        if not start:
            return []

        adj: Dict[str, List[str]] = {}
        for e in edges:
            src = e.get("source")
            if src:
                adj.setdefault(src, []).append(e.get("target"))

        node_map = {n["id"]: n for n in nodes}

        # BFS reachability, recording discovery order for tie-breaking.
        discovery: List[str] = []
        seen = set()
        queue = [start["id"]]
        while queue:
            nid = queue.pop(0)
            if nid in seen or nid not in node_map:
                continue
            seen.add(nid)
            discovery.append(nid)
            queue.extend(t for t in adj.get(nid, []) if t not in seen)

        rank = {nid: i for i, nid in enumerate(discovery)}
        pending_parents: Dict[str, int] = {nid: 0 for nid in discovery}
        children: Dict[str, List[str]] = {}
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if src in rank and tgt in rank and src != tgt:
                pending_parents[tgt] += 1
                children.setdefault(src, []).append(tgt)

        ready = sorted((nid for nid in discovery if pending_parents[nid] == 0), key=rank.__getitem__)
        order_ids: List[str] = []
        placed = set()
        while ready:
            nid = ready.pop(0)
            order_ids.append(nid)
            placed.add(nid)
            for child in children.get(nid, []):
                pending_parents[child] -= 1
                if pending_parents[child] == 0:
                    ready.append(child)
            ready.sort(key=rank.__getitem__)

        order_ids.extend(nid for nid in discovery if nid not in placed)
        return [node_map[nid] for nid in order_ids]

    @staticmethod
    def _incoming_conditional_edges(edges: List[Dict]) -> Dict[str, List[Dict]]:
        """Edges that carry a condition, grouped by their target node."""
        incoming: Dict[str, List[Dict]] = {}
        for edge in edges or []:
            target = edge.get("target")
            if target and (edge.get("data") or {}).get("condition"):
                incoming.setdefault(target, []).append(edge)
        return incoming

    @staticmethod
    def _incoming_conditions_met(incoming: List[Dict], envelopes: Dict[str, Dict[str, Any]]) -> bool:
        """OR-any across a node's conditional incoming edges."""
        for edge in incoming:
            envelope = envelopes.get(edge.get("source")) or {}
            inner = envelope.get("result", {}) if envelope else {}
            if evaluate_edge_condition(edge["data"]["condition"], envelope=envelope, inner=inner):
                return True
        return False

    def _error_result(self, error: str, start_time: float) -> Dict:
        """Build error result."""
        return {
            "success": False,
            "error": error,
            "nodes_executed": [],
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }

    # =========================================================================
    # SETTINGS
    # =========================================================================

    async def load_deployment_settings(self) -> Dict[str, Any]:
        """Load deployment settings from database."""
        try:
            db = await self.database.get_deployment_settings()
            if db:
                self._settings.update(db)
        except Exception:
            pass
        return self._settings.copy()

    async def update_deployment_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Update deployment settings."""
        self._settings.update(settings)
        await self.database.save_deployment_settings(self._settings)
        return self._settings.copy()

    def get_deployment_settings(self) -> Dict[str, Any]:
        """Get current deployment settings."""
        return self._settings.copy()

    @property
    def node_outputs(self) -> Dict[str, Dict[str, Any]]:
        """Backward compatibility: expose outputs as node_outputs."""
        return self._outputs
