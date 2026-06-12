"""Temporal workflow - Pure orchestrator for distributed node execution.

The workflow ONLY orchestrates:
- Parses graph structure
- Filters config nodes (tools, memory, services)
- Determines execution order based on dependencies
- Schedules node activities (can run on ANY worker)
- Collects results and routes outputs to dependent nodes

NO business logic in workflow - all execution happens in activities.
This enables massive horizontal scaling and multi-tenant distribution.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from temporalio import workflow

from ._retry_policies import DEFAULT_ACTIVITY_RETRY
from services.workflow_naming import node_label_slug

# Config handles - nodes connecting via these are config nodes (not executed)
# AI Agent handles: input-memory, input-tools, input-model, input-task, input-teammates
# Zeenie handles: input-skill, input-tools
CONFIG_HANDLES = {"input-tools", "input-memory", "input-model", "input-skill", "input-task", "input-teammates"}

# Trigger node types — event listeners that should never be scheduled
# as blocking activities. Imported from constants to avoid drift (was
# previously redefined here with a "keep in sync" comment — Wave 11.E.2).
# Android service types follow the same pattern: imported from constants
# so the canonical 16-entry list (Wave 11.I, milestone P -- the local
# 6-entry copy that lived here was a stale subset).
from constants import (
    ANDROID_SERVICE_NODE_TYPES as ANDROID_SERVICE_TYPES,
    WORKFLOW_TRIGGER_TYPES as TRIGGER_NODE_TYPES,
)

# Skill node types (connect to Zeenie's input-skill, not executed directly)
SKILL_NODE_TYPES = {
    "masterSkill",
}

# F4.B: agent types that migrate to AgentWorkflow (Temporal child workflow).
# When ``temporal_agent_workflow_enabled`` is True the orchestrator schedules
# AgentWorkflow for these node types instead of an activity. Tool calls
# inside the agent loop become per-type activities (F4.A path).
#
# Excluded: ``rlm_agent``, ``claude_code_agent``. Their internal session
# state (RLM REPL / Claude CLI --resume with stable cwd) requires
# single-process continuity and breaks across activity boundaries. They
# continue via F4.A per-type activities.
AGENT_WORKFLOW_TYPES = frozenset(
    [
        "aiAgent",
        "chatAgent",
        # Specialized agents (12)
        "android_agent",
        "coding_agent",
        "web_agent",
        "task_agent",
        "social_agent",
        "travel_agent",
        "tool_agent",
        "productivity_agent",
        "payments_agent",
        "consumer_agent",
        "autonomous_agent",
        # Team leads (2)
        "orchestrator_agent",
        "ai_employee",
    ]
)


@workflow.defn(sandboxed=False)
class MachinaWorkflow:
    """Distributed workflow orchestrator.

    This workflow ONLY orchestrates - all execution happens in activities
    that can run on any worker in the cluster.

    Features:
    - Continuous scheduling (FIRST_COMPLETED pattern)
    - Per-node retry policies
    - Config node filtering (tools, memory, services)
    - Multi-tenant support via tenant_id in context
    - Wave 12 A7: event-framework signal handler — receives Temporal
      Signals from ``services/events/dispatch.py:emit`` and parks
      pending events in ``_matched_events`` for trigger nodes to
      consume via ``workflow.wait_condition(_has_event_matching, ...)``.
      Per-run dedup via ``_seen_event_ids`` (reconstructed
      deterministically from Event History on replay).
    """

    def __init__(self) -> None:
        # Wave 12 A7: per-run event-framework state. Populated by the
        # ``on_event`` signal handler; consumed by Phase C1's
        # ``wait_condition`` predicate in the trigger-waiter rewrite.
        # Today these stay empty unless the event-framework feature
        # flag is on AND a producer signals this workflow.
        self._seen_event_ids: Set[str] = set()
        self._matched_events: List[Dict[str, Any]] = []

    @workflow.signal
    async def on_event(self, event_payload: Dict[str, Any]) -> None:
        """Wave 12 A7: receive an event from ``services.events.dispatch.emit``.

        Drops duplicates by ``event.id`` (per-run dedup — the set is
        reconstructed deterministically from Event History on replay).
        Matching events are queued for the workflow body to consume via
        :meth:`_has_event_matching` in a ``wait_condition`` (wired in
        Phase C1).
        """
        event_id = event_payload.get("id")
        if not event_id:
            workflow.logger.warning("on_event: skipping malformed envelope without 'id'")
            return
        if event_id in self._seen_event_ids:
            workflow.logger.debug(f"on_event: dedup hit for event.id={event_id}")
            return
        self._seen_event_ids.add(event_id)
        self._matched_events.append(event_payload)
        workflow.logger.info(
            f"on_event: queued event.id={event_id} " f"type={event_payload.get('type', '?')} " f"(matched={len(self._matched_events)})"
        )

    def _has_event_matching(self, predicate=None) -> bool:
        """Wave 12 A7: ``wait_condition`` predicate for trigger nodes.

        Returns True when at least one queued event matches the optional
        ``predicate(event_payload) -> bool``. Defaults to "any event
        queued" — most trigger nodes filter further by inspecting the
        envelope after pop.

        Used by Phase C1's trigger-waiter rewrite to replace the
        in-process ``asyncio.Future`` waiter:

            await workflow.wait_condition(
                lambda: self._has_event_matching(my_filter),
                timeout=...,
            )
            event = self._pop_matching_event(my_filter)
        """
        if not self._matched_events:
            return False
        if predicate is None:
            return True
        return any(predicate(e) for e in self._matched_events)

    def _pop_matching_event(self, predicate=None) -> Optional[Dict[str, Any]]:
        """Wave 12 A7: dequeue the first event that satisfies ``predicate``.

        Pairs with :meth:`_has_event_matching` — Phase C1 trigger
        waiters call ``wait_condition`` on the predicate, then pop the
        actual envelope here. FIFO order: the predicate is evaluated
        against the head of ``_matched_events`` first, so events
        arrive in the order Temporal delivered the signals (and on
        replay, the order Event History records them — deterministic).

        Returns ``None`` if no queued event matches; callers should
        only reach this method after ``wait_condition`` resolves, in
        which case at least one match exists. The ``None`` return path
        exists for defensive symmetry — never silently raises mid-
        replay.

        Determinism note: list mutation inside a workflow is safe per
        Temporal's Python SDK contract (the workflow's local state is
        rebuilt from Event History on replay; the same signals deliver
        the same envelopes, so the same pops happen in the same order).
        """
        for idx, event in enumerate(self._matched_events):
            if predicate is None or predicate(event):
                return self._matched_events.pop(idx)
        return None

    @workflow.run
    async def run(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute workflow by orchestrating node activities.

        Args:
            workflow_data: Dict containing:
                - nodes: List of node definitions from React Flow
                - edges: List of edge definitions from React Flow
                - session_id: Session identifier
                - workflow_id: Workflow ID for tracking
                - tenant_id: Tenant identifier for multi-tenancy

        Returns:
            Dict with success, outputs, execution_trace, and errors
        """
        nodes = workflow_data.get("nodes", [])
        edges = workflow_data.get("edges", [])
        session_id = workflow_data.get("session_id", "default")
        workflow_id = workflow_data.get("workflow_id")
        # Human-readable slug for child workflow IDs visible in the
        # Temporal Web UI. Falls back to workflow_id when missing
        # (one-off Runs without a saved DB row).
        workflow_slug = workflow_data.get("workflow_slug") or workflow_id
        tenant_id = workflow_data.get("tenant_id")
        # Stable per-run execution id. The executor passes the same value
        # it uses as this workflow's Temporal id (``<slug>-<uuid8>``), so
        # the fallback to ``workflow.info().workflow_id`` is identical by
        # construction. Threaded into every node activity context so
        # session-keyed nodes (browser) share one instance per run instead
        # of minting a fresh uuid per call (node_executor.py fallback).
        execution_id = workflow_data.get("execution_id") or workflow.info().workflow_id

        workflow.logger.info(f"Starting workflow orchestration: {len(nodes)} nodes, {len(edges)} edges")

        if not nodes:
            return {
                "success": False,
                "error": "No nodes provided",
                "outputs": {},
                "execution_trace": [],
            }

        # 1. Filter out config nodes (tools, memory, services)
        exec_nodes, exec_edges = self._filter_executable_graph(nodes, edges)

        workflow.logger.info(
            f"After filtering: {len(exec_nodes)} executable nodes " f"(filtered {len(nodes) - len(exec_nodes)} config nodes)"
        )

        # 2. Build dependency maps
        deps, node_map = self._build_dependency_maps(exec_nodes, exec_edges)

        # 3. Initialize state
        outputs: Dict[str, Any] = {}  # node_id -> result
        completed: Set[str] = set()
        running: Dict[str, Any] = {}  # node_id -> activity handle
        errors: List[Dict] = []
        execution_trace: List[str] = []

        # 4. Handle pre-executed triggers (already have their output)
        pre_executed_count = 0
        for node in exec_nodes:
            if node.get("_pre_executed"):
                node_id = node["id"]
                trigger_output = node.get("_trigger_output", {})
                outputs[node_id] = {
                    "success": True,
                    "result": trigger_output,
                    "pre_executed": True,
                }
                completed.add(node_id)
                execution_trace.append(node_id)
                pre_executed_count += 1
                workflow.logger.info(f"Pre-executed trigger: {node_id}")

                # Persist the trigger output to the workflow output
                # store so ParameterResolver can resolve
                # ``{{triggerNodeName.field}}`` in downstream nodes.
                # Pre-executed nodes bypass NodeExecutor.execute (the
                # normal write site), so without this activity the
                # legacy path's ``_store_output(trigger_node_id, ...)``
                # behaviour is missing on the canary path and templates
                # against the trigger silently resolve to empty.
                # Skip the persist for non-firing siblings — their
                # ``_trigger_output`` is ``{not_triggered: True}``, no
                # downstream template should resolve against them.
                if not trigger_output.get("not_triggered"):
                    await workflow.execute_activity(
                        "store_node_output_activity",
                        {
                            "node_id": node_id,
                            "session_id": session_id,
                            "result": trigger_output,
                        },
                        start_to_close_timeout=timedelta(seconds=10),
                    )

        workflow.logger.info(f"Pre-executed: {pre_executed_count}, To execute: {len(node_map) - pre_executed_count}")

        # 5. Retry policy for node activities. Wave 12 D1: imported
        # shared constant declares ``non_retryable_error_types`` so
        # NodeUserError fails fast instead of burning 3 retries on
        # user-correctable failures.
        retry_policy = DEFAULT_ACTIVITY_RETRY

        # 6. Continuous scheduling loop
        loop_count = 0
        while True:
            loop_count += 1
            # Find ready nodes (all deps completed, not running/completed)
            ready = self._find_ready_nodes(deps, completed, running, node_map)
            workflow.logger.debug(f"Loop {loop_count}: ready={len(ready)}, running={len(running)}, completed={len(completed)}")

            # Start activities for ready nodes
            for node_id in ready:
                node = node_map[node_id]
                node_type = node.get("type", "unknown")

                # Safety: auto-complete trigger nodes that weren't pre-executed.
                # Trigger nodes are event listeners - scheduling them as activities
                # would block indefinitely waiting for external events.
                if node_type in TRIGGER_NODE_TYPES and not node.get("_pre_executed"):
                    workflow.logger.warning(f"Skipping non-pre-executed trigger: {node_id} ({node_type})")
                    outputs[node_id] = {
                        "success": True,
                        "result": {"not_triggered": True},
                        "skipped_trigger": True,
                    }
                    completed.add(node_id)
                    execution_trace.append(node_id)
                    continue

                # Build immutable context for this node
                context = {
                    "node_id": node_id,
                    "node_type": node.get("type", "unknown"),
                    "node_data": node.get("data", {}),
                    "inputs": self._get_node_inputs(node_id, deps, outputs),
                    "workflow_id": workflow_id,
                    "workflow_slug": workflow_slug,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "execution_id": execution_id,
                    "nodes": nodes,  # Full list for tool/memory detection
                    "edges": edges,  # Full list for tool/memory detection
                    # Include pre-executed info if applicable
                    "pre_executed": node.get("_pre_executed", False),
                    "trigger_output": node.get("_trigger_output"),
                }

                # F4.B: agent-as-child-workflow takes precedence over the
                # activity path for the 15 migrating agent types when its
                # flag is on. Tool calls inside the agent loop become
                # per-type activities (F4.A path) automatically.
                dispatch = self._resolve_dispatch(node_type)
                if dispatch["kind"] == "child_workflow":
                    # ``workflow.start_child_workflow`` is ``async def`` —
                    # awaiting it returns the ``ChildWorkflowHandle`` once
                    # the child has been started by the server (fast; not
                    # blocking on child completion). The handle is a
                    # Task-like with ``.done()`` so it slots into the same
                    # FIRST_COMPLETED loop as activity handles.
                    # Child id format: ``<slug>-<node_label>`` — same
                    # ``<workflow_slug>-<node_label>`` convention as the
                    # trigger ids. ``node_label`` is the user-renamed
                    # F2 label or the node type (``aiAgent`` / ``orchestrator_agent``
                    # / ...) — already conveys "agent" so no middle tag.
                    handle = await workflow.start_child_workflow(
                        dispatch["name"],
                        args=[context],
                        id=f"{workflow_slug}-{node_label_slug(node)}",
                        # Child workflow execution timeout: agent loops can run
                        # 10+ minutes with multiple LLM turns. Set generously.
                        execution_timeout=timedelta(hours=1),
                        run_timeout=timedelta(hours=1),
                    )
                    running[node_id] = handle
                    workflow.logger.info(f"Scheduled child workflow for node: {node_id} " f"(workflow={dispatch['name']})")
                else:
                    # F4.A activity path: per-type when the plugin class is
                    # registered AND the per-type flag is on; legacy
                    # execute_node_activity otherwise. ``activity_id`` =
                    # node_id so the Temporal Web UI history tab labels
                    # each activity by the canvas node it ran.
                    start_kwargs: Dict[str, Any] = dict(
                        args=[context],
                        activity_id=node_id,
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(minutes=2),
                        retry_policy=retry_policy,
                    )
                    if dispatch.get("queue") is not None:
                        start_kwargs["task_queue"] = dispatch["queue"]

                    handle = workflow.start_activity(dispatch["name"], **start_kwargs)
                    running[node_id] = handle
                    workflow.logger.info(
                        f"Scheduled activity for node: {node_id} "
                        f"(activity={dispatch['name']}, queue={dispatch.get('queue') or 'default'})"
                    )

            # Exit if nothing running and nothing ready
            if not running:
                break

            # Wait for ANY activity to complete (FIRST_COMPLETED pattern)
            done_id, result = await self._wait_any_complete(running)

            if result.get("success"):
                outputs[done_id] = result
                completed.add(done_id)
                execution_trace.append(done_id)
                workflow.logger.info(f"Node completed: {done_id}")
            else:
                # Node failed after all retries
                error_info = {
                    "node_id": done_id,
                    "error": result.get("error", "Unknown error"),
                }
                errors.append(error_info)
                workflow.logger.error(f"Node failed: {done_id} - {error_info['error']}")

                # Stop workflow on failure
                # TODO: Could add option to continue with partial results
                break

        # Build final result
        success = len(errors) == 0 and len(completed) == len(node_map)

        workflow.logger.info(f"Workflow complete: success={success}, " f"executed={len(execution_trace)}/{len(node_map)}")

        return {
            "success": success,
            "outputs": outputs,
            "execution_trace": execution_trace,
            "errors": errors if errors else None,
        }

    def _get_node_inputs(
        self,
        node_id: str,
        deps: Dict[str, Set[str]],
        outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get outputs from upstream nodes as inputs for this node."""
        inputs = {}
        for dep_id in deps.get(node_id, set()):
            if dep_id in outputs:
                inputs[dep_id] = outputs[dep_id].get("result", {})
        return inputs

    def _resolve_dispatch(self, node_type: str) -> Dict[str, Any]:
        """Resolve dispatch kind for a node type.

        Returns one of:
          - ``{"kind": "child_workflow", "name": "AgentWorkflow"}`` — when
            F4.B is enabled AND node_type is in ``AGENT_WORKFLOW_TYPES``.
          - ``{"kind": "activity", "name": <activity_name>, "queue": <queue|None>}``
            — F4.A per-type activity OR legacy fallback, depending on
            ``temporal_per_type_dispatch``.

        Deterministic: all lookups go through frozen dicts (Settings,
        _NODE_CLASS_REGISTRY, AGENT_WORKFLOW_TYPES). Safe inside
        ``MachinaWorkflow.run`` per the workflow-definition contract.
        """
        from core.config import Settings

        settings = Settings()
        if getattr(settings, "temporal_agent_workflow_enabled", False) and node_type in AGENT_WORKFLOW_TYPES:
            return {"kind": "child_workflow", "name": "AgentWorkflow"}

        name, queue = self._resolve_activity(node_type)
        return {"kind": "activity", "name": name, "queue": queue}

    def _resolve_activity(self, node_type: str) -> tuple[str, str | None]:
        """Resolve (activity_name, task_queue) for a node type.

        F4.A: when ``settings.temporal_per_type_dispatch`` is on AND the
        plugin class is registered, returns
        ``("node.{type}.v{version}", None)`` so the activity is scheduled
        by per-type name but stays on the workflow's default task queue.
        The default queue is what ``TemporalWorkerManager`` polls today;
        per-queue routing (``cls.task_queue``) becomes meaningful only
        once ``TemporalWorkerPool`` is wired with one worker per queue
        — until then, returning ``cls.task_queue`` would schedule the
        activity to a queue no worker polls and the workflow would hang.

        Falls back to ``("execute_node_activity", None)`` when:
          - the flag is off (preserves pre-F4.A behavior exactly), OR
          - the node type isn't registered as a BaseNode subclass
            (covers legacy types still on the metadata-only path).

        Determinism: lookups go through frozen module-level dicts
        (``_NODE_CLASS_REGISTRY``, ``Settings``) — no I/O. Safe inside
        ``MachinaWorkflow.run`` per the workflow-definition contract.
        Imports are inside the method to keep the workflow module's
        top-level import set minimal and to avoid import-cycle drift.
        """
        from core.config import Settings
        from services.node_registry import get_node_class

        if not Settings().temporal_per_type_dispatch:
            return "execute_node_activity", None

        cls = get_node_class(node_type)
        if cls is None:
            return "execute_node_activity", None

        return f"node.{cls.type}.v{cls.version}", None

    async def _wait_any_complete(self, running: Dict[str, Any]) -> tuple:
        """Wait for any activity to complete, return (node_id, result).

        Uses Temporal's native wait mechanism for efficient polling.
        """
        # Convert to list for iteration
        items = list(running.items())

        # Check if any already done
        for node_id, handle in items:
            if handle.done():
                del running[node_id]
                try:
                    result = await handle
                    return node_id, result
                except Exception as e:
                    return node_id, {"success": False, "error": str(e)}

        # Wait for first completion using Temporal's wait
        [h for _, h in items]

        # Use asyncio.wait pattern via workflow.wait
        await workflow.wait_condition(lambda: any(h.done() for _, h in items))

        # Find the completed one
        for node_id, handle in items:
            if handle.done():
                del running[node_id]
                try:
                    result = await handle
                    return node_id, result
                except Exception as e:
                    return node_id, {"success": False, "error": str(e)}

        # Should not reach here
        raise RuntimeError("No activity completed after wait")

    def _filter_executable_graph(
        self,
        nodes: List[Dict],
        edges: List[Dict],
    ) -> tuple:
        """Filter out config nodes based on edge handles.

        Config nodes (tools, memory, model configs) connect via special handles
        and are consumed by their target nodes, not executed independently.

        Returns:
            Tuple of (executable_nodes, executable_edges)
        """
        node_map = {n["id"]: n for n in nodes}
        config_ids = set()

        for edge in edges:
            handle = edge.get("targetHandle", "")
            source_id = edge.get("source")

            # Edges to config handles mean source is a config node
            if handle in CONFIG_HANDLES:
                config_ids.add(source_id)

            # Android services connecting to androidTool
            source_node = node_map.get(source_id, {})
            if source_node.get("type") in ANDROID_SERVICE_TYPES:
                config_ids.add(source_id)

            # Skill nodes (always config, connect to Zeenie)
            if source_node.get("type") in SKILL_NODE_TYPES:
                config_ids.add(source_id)

        # Filter nodes and edges
        exec_nodes = [n for n in nodes if n["id"] not in config_ids]
        exec_edges = [
            e
            for e in edges
            if e.get("source") not in config_ids and e.get("target") not in config_ids and e.get("targetHandle", "") not in CONFIG_HANDLES
        ]

        return exec_nodes, exec_edges

    def _build_dependency_maps(
        self,
        nodes: List[Dict],
        edges: List[Dict],
    ) -> tuple:
        """Build dependency graph from nodes and edges.

        Returns:
            Tuple of (dependencies_map, node_map)
            - dependencies_map: node_id -> set of node IDs it depends on
            - node_map: node_id -> node definition
        """
        node_map = {n["id"]: n for n in nodes}
        node_ids = set(node_map.keys())

        deps = {nid: set() for nid in node_ids}

        for edge in edges:
            src, tgt = edge.get("source"), edge.get("target")
            if src in node_ids and tgt in node_ids:
                deps[tgt].add(src)

        return deps, node_map

    def _find_ready_nodes(
        self,
        deps: Dict[str, Set[str]],
        completed: Set[str],
        running: Dict[str, Any],
        node_map: Dict[str, Dict],
    ) -> List[str]:
        """Find nodes ready to execute.

        A node is ready when:
        - All its dependencies have completed
        - It's not already running
        - It's not already completed
        """
        ready = []
        for node_id in node_map:
            if node_id in completed or node_id in running:
                continue
            if deps[node_id] <= completed:
                ready.append(node_id)
        return ready
