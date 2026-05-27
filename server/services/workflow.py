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
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from core.logging import get_logger
from constants import WORKFLOW_TRIGGER_TYPES
from services.node_executor import NodeExecutor
from services.parameter_resolver import ParameterResolver
from services.deployment import DeploymentManager
from services.execution import WorkflowExecutor, ExecutionCache

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
    ) -> Dict[str, Any]:
        """Execute a single workflow node."""
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
            "workspace_dir": workspace_dir,  # Per-workflow filesystem for nodes and agents
            "get_output_fn": self.get_node_output,
            "outputs": outputs or {},  # Upstream node outputs for data flow (e.g., taskTrigger -> chatAgent)
        }
        return await self._node_executor.execute(
            node_id=node_id,
            node_type=node_type,
            parameters=parameters,
            context=context,
            resolve_params_fn=self._param_resolver.resolve,
        )

    async def _execute_node_adapter(
        self,
        node_id: str,
        node_type: str,
        parameters: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Adapter for WorkflowExecutor to call NodeExecutor."""
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
        """
        start_time = time.time()

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
            return await self._execute_temporal(nodes, edges, session_id, status_callback, start_time, workflow_id)

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

        # Use parallel executor if enabled and Redis available
        if use_parallel and self.settings.redis_enabled:
            return await self._execute_parallel(nodes, edges, session_id, status_callback, start_time, workflow_id)

        # Fall back to sequential
        return await self._execute_sequential(nodes, edges, session_id, status_callback, start_time, workflow_id)

    async def _execute_temporal(self, nodes, edges, session_id, status_callback, start_time, workflow_id: Optional[str] = None) -> Dict:
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
        )

        # Notify status callback for completed nodes if provided
        if status_callback and result.get("success"):
            for node_id in result.get("nodes_executed", []):
                try:
                    await status_callback(
                        node_id,
                        "completed",
                        result.get("outputs", {}).get(node_id, {}),
                    )
                except Exception:
                    pass

        return {
            "success": result.get("success", False),
            "execution_id": result.get("execution_id"),
            "nodes_executed": result.get("nodes_executed", []),
            "outputs": result.get("outputs", {}),
            "errors": result.get("errors", []),
            "execution_time": result.get("execution_time", time.time() - start_time),
            "temporal_execution": True,
            "timestamp": datetime.now().isoformat(),
        }

    async def _execute_parallel(self, nodes, edges, session_id, status_callback, start_time, workflow_id: Optional[str] = None) -> Dict:
        """Execute with parallel orchestration engine."""
        # Use passed workflow_id (from deployment) or generate new one
        if not workflow_id:
            workflow_id = f"workflow_{session_id}_{int(time.time() * 1000)}"
        executor = self._get_workflow_executor(status_callback)

        result = await executor.execute_workflow(
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            session_id=session_id,
            enable_caching=True,
        )

        return {
            "success": result.get("success", False),
            "execution_id": result.get("execution_id"),
            "nodes_executed": result.get("nodes_executed", []),
            "outputs": result.get("outputs", {}),
            "errors": result.get("errors", []),
            "execution_time": result.get("execution_time", time.time() - start_time),
            "parallel_execution": True,
            "timestamp": datetime.now().isoformat(),
        }

    async def _execute_sequential(self, nodes, edges, session_id, status_callback, start_time, workflow_id: Optional[str] = None) -> Dict:
        """Execute nodes sequentially (fallback mode)."""
        start_node = self._find_start_node(nodes)
        execution_order = self._build_execution_order(start_node, nodes, edges)

        results = {}
        executed = []

        for node in execution_order:
            node_id = node["id"]
            node_type = node.get("type", "unknown")

            # Skip pre-executed trigger nodes
            if node.get("_pre_executed"):
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
                workflow_id=workflow_id,
            )

            results[node_id] = result
            executed.append(node_id)

            # Notify completed
            if status_callback:
                status = "completed" if result.get("success") else "error"
                try:
                    await status_callback(node_id, status, result)
                except Exception:
                    pass

            if not result.get("success") and self._settings.get("stop_on_error"):
                break

        return {
            "success": all(r.get("success", False) for r in results.values()),
            "nodes_executed": executed,
            "node_results": results,
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
    ) -> Dict[str, Any]:
        """Deploy workflow in event-driven mode.

        Args:
            nodes: Workflow nodes
            edges: Workflow edges
            session_id: Session identifier
            status_callback: Status update callback
            workflow_id: Workflow ID for per-workflow deployment tracking
        """
        manager = self._get_deployment_manager()
        return await manager.deploy(nodes, edges, session_id, status_callback, workflow_id)

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
        """Build BFS execution order from start node."""
        visited = set()
        order = []
        queue = [start["id"]]

        # Build adjacency map
        adj = {}
        for e in edges:
            src = e.get("source")
            if src:
                adj.setdefault(src, []).append(e.get("target"))

        node_map = {n["id"]: n for n in nodes}

        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = node_map.get(nid)
            if node:
                order.append(node)
                queue.extend(t for t in adj.get(nid, []) if t not in visited)

        return order

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
