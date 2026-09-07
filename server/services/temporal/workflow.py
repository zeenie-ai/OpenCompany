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

from ._failures import activity_failure_envelope
from ._retry_policies import DEFAULT_ACTIVITY_RETRY, QUICK_ACTIVITY_RETRY
from services.workflow_naming import node_label_slug

# ``conditions`` is pure -- ``re`` + comparisons, no IO, no clock, no
# randomness -- so it is safe to evaluate inside a workflow.
#
# Importing the submodule still executes ``services/execution/__init__.py``
# first (Python always initialises the parent package), which drags in the
# executor, cache, recovery sweeper and DLQ. That is tolerated, not avoided:
# nothing in that subtree imports back into ``services.temporal``, so there is
# no cycle, and none of it pulls redis or sqlalchemy at import time. The
# pass-through keeps the sandbox from re-importing the chain per workflow.
with workflow.unsafe.imports_passed_through():
    from services.execution.conditions import evaluate_condition

# Conditional edges were not evaluated on this path at all before this patch --
# a condition set in the editor rendered a label and did nothing once execution
# routed through Temporal. Gating the skip keeps replay deterministic for
# histories recorded while every node was scheduled unconditionally.
CONDITIONAL_EDGES_PATCH = "machina-conditional-edges-v1"

# Plugin failures now raise typed ApplicationErrors at the activity boundary,
# so the RetryPolicy recorded on each node activity finally matters. Under
# this patch the policy comes from ``BaseNode.effective_retry_policy`` (a
# declared policy, else one attempt for mutating nodes and triggers, else the
# default three). Histories recorded before it keep the class ``retry_policy``
# they were scheduled with. Shared with AgentWorkflow, whose tool-call
# activities gain a policy under the same marker.
PLUGIN_FAILURE_RETRY_PATCH = "machina-plugin-failure-retries-v1"

# Config handles - nodes connecting via these are config nodes (not executed)
# AI Agent handles: input-context, input-tools, input-model, input-task, input-teammates
# Zeenie handles: input-skill, input-tools
CONFIG_HANDLES = {
    "input-context",
    "input-tools",
    "input-memory",  # replay/import compatibility for V1 graph snapshots
    "input-model",
    "input-skill",
    "input-task",
    "input-teammates",
}


def _is_config_edge(edge: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> bool:
    """Return whether an edge attaches configuration rather than runtime data.

    ``input-task`` is normally a configuration handle, but a taskTrigger emits
    runtime completion data through that handle.  Treating that edge as config
    removes both the pre-executed trigger and its payload before dependency
    resolution, leaving the downstream agent with an empty prompt.
    """
    handle = edge.get("targetHandle") or edge.get("target_handle") or ""
    if handle not in CONFIG_HANDLES:
        return False
    source = node_map.get(edge.get("source"), {})
    return not (handle == "input-task" and source.get("type") == "taskTrigger")

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
# Excluded: ``rlm_agent``, ``claude_code_agent``, ``vertex_managed_agent``.
# Their internal session state (RLM REPL / Claude CLI --resume with stable
# cwd / Interactions API previous_interaction_id + environment chaining and
# post-turn canvas minting) requires single-process continuity and breaks
# across activity boundaries. They continue via F4.A per-type activities.
AGENT_WORKFLOW_TYPES = frozenset(
    [
        "aiAgent",
        "chatAgent",
        # Specialized agents (11)
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

AGENT_CONTEXT_GRAPH_VERSION = 2
# Root dispatch consumes an immutable routing snapshot carried in workflow
# input rather than process-local Settings, so flipping an environment
# value can never change how an in-flight run dispatches.
TEMPORAL_ROUTING_INPUT_KEY = "_temporal_routing_v1"
TEMPORAL_ROUTING_INPUT_VERSION = 1
# Direct Temporal callers predating the input helper may omit the snapshot.
# Keep that path deterministic and on the manager's queue while retaining the
# shipped child/per-type protocols. Normal executor/deployment starts always
# provide an explicit snapshot.
_SAFE_FROZEN_ROUTING = {
    "version": TEMPORAL_ROUTING_INPUT_VERSION,
    "agent_workflow_enabled": True,
    "per_type_dispatch_enabled": True,
    "worker_pool_enabled": False,
}
# Agent child workflows used to carry 1h execution/run timeouts, and node
# activities a 10-minute start_to_close. A run may legitimately execute —
# or stay cooperatively paused — for months, and Temporal's timeout
# timers keep ticking through a pause, so the caps silently terminated
# long/paused runs. New executions start agent children unbounded and
# give node activities a generous start_to_close; liveness is enforced
# by the 2-minute heartbeat timeout (activities self-heartbeat every
# 30s), not by lifetime caps.
# Circuit breaker: a failed trigger-spawned run schedules the
# workflow_control.pause_on_failure.v1 activity so the deployment pauses
# (user fixes + resumes) instead of the trigger firing into the same
# error indefinitely. The WORKFLOW_CONTROL_PAUSE_ON_FAILURE knob is
# evaluated on the activity side, so flipping config never touches
# recorded workflow commands; this patch only gates the activity
# scheduling itself for replay compatibility with older histories.
# start_to_close for node activities. Generous by
# design: a single node step (agent turn batch, browser op, long shell
# command) may run for hours; worker-death detection is the heartbeat
# timeout's job, so a long ceiling costs nothing in liveness.
_NODE_ACTIVITY_START_TO_CLOSE = timedelta(hours=24)


def _agent_child_workflow_id(root_workflow_id: str, agent_node_id: str) -> str:
    """Return a deterministic child id unique to one canvas agent and run."""
    return f"{root_workflow_id}-agent-{agent_node_id}"


def _frozen_routing_from_input(
    workflow_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize the immutable routing snapshot without consulting Settings."""
    raw = workflow_data.get(TEMPORAL_ROUTING_INPUT_KEY)
    if (
        not isinstance(raw, dict)
        or raw.get("version") != TEMPORAL_ROUTING_INPUT_VERSION
    ):
        raw = _SAFE_FROZEN_ROUTING
    return {
        "version": TEMPORAL_ROUTING_INPUT_VERSION,
        "agent_workflow_enabled": raw.get("agent_workflow_enabled") is True,
        "per_type_dispatch_enabled": (
            raw.get("per_type_dispatch_enabled") is True
        ),
        "worker_pool_enabled": raw.get("worker_pool_enabled") is True,
    }


# Durable protocol identifier: existing Temporal histories and child-workflow
# start calls replay against this exact type string. It intentionally retains
# the pre-rebrand name even though the product is now OpenCompany.
@workflow.defn(name="MachinaWorkflow", sandboxed=False)
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
        self._control_paused = False

    @workflow.signal
    async def pause(self) -> None:
        """Cooperatively gate new node/activity scheduling."""
        self._control_paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._control_paused = False

    async def _wait_until_resumed(self) -> None:
        if self._control_paused:
            await workflow.wait_condition(lambda: not self._control_paused)

    async def _pause_deployment_on_failure(
        self,
        workflow_data: Dict[str, Any],
        nodes: List[Dict[str, Any]],
        errors: List[Dict],
    ) -> None:
        """Circuit breaker: a failed trigger-spawned run pauses its deployment.

        Only deployment-spawned runs qualify — they always carry a
        ``_pre_executed`` firing trigger, which one-off manual canvas runs
        never do, so a failed test run cannot pause a live deployment.
        Legacy uncontrolled deployments no-op inside the activity (no
        running control row). Non-fatal by design: the run's own failure
        result is the primary outcome either way.
        """
        workflow_id = workflow_data.get("workflow_id")
        spawned_by_trigger = any(node.get("_pre_executed") for node in nodes)
        if not workflow_id or not spawned_by_trigger:
            return
        try:
            await workflow.execute_activity(
                "workflow_control.pause_on_failure",
                {
                    "workflow_id": workflow_id,
                    "reason": str((errors[0] or {}).get("error", "run_failed"))[:500],
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=QUICK_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — cosmetic relative to the run result
            workflow.logger.warning(f"pause-on-failure activity failed (non-fatal): {exc}")

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
        graph_version = int(
            workflow_data.get("graphVersion")
            or workflow_data.get("graph_version")
            or 0
        )
        generation = int(workflow_data.get("generation") or 0)
        # Stable per-run execution id. The executor passes the same value
        # it uses as this workflow's Temporal id (``<slug>-<uuid8>``), so
        # the fallback to ``workflow.info().workflow_id`` is identical by
        # construction. Threaded into every node activity context so
        # session-keyed nodes (browser) share one instance per run instead
        # of minting a fresh uuid per call (node_executor.py fallback).
        execution_id = workflow_data.get("execution_id") or workflow.info().workflow_id
        # Command attributes are part of Temporal Event History. Existing
        # histories must keep the label-derived child ids they recorded;
        # new histories use the root Temporal id + exact canvas node id so
        # two same-label agents can start in the same graph execution.
        frozen_routing = _frozen_routing_from_input(workflow_data)

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
        conditional_edges = self._build_conditional_edge_map(exec_edges, node_map)
        use_conditional_edges = workflow.patched(CONDITIONAL_EDGES_PATCH)
        use_effective_retry = workflow.patched(PLUGIN_FAILURE_RETRY_PATCH)

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
                    await self._wait_until_resumed()
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
            # Avoid emitting a redundant wait command on the normal running
            # path.  Once paused, the condition is durable and replay-safe;
            # the resume signal flips the flag and releases scheduling.
            if self._control_paused:
                await workflow.wait_condition(lambda: not self._control_paused)
            # Find ready nodes (all deps completed, not running/completed)
            ready = self._find_ready_nodes(deps, completed, running, node_map)
            workflow.logger.debug(f"Loop {loop_count}: ready={len(ready)}, running={len(running)}, completed={len(completed)}")

            # Triggers auto-completed by the skip branch below unblock their
            # downstream nodes without putting anything into ``running`` —
            # the exit check must re-evaluate readiness in that case instead
            # of concluding the graph is drained (direct canvas-Run path:
            # ``start`` is NOT pre-executed, so without this the loop broke
            # after the start node and downstream never ran).
            auto_completed_this_pass = 0

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
                    auto_completed_this_pass += 1
                    continue

                # Every dependency is done, so any incoming condition is now
                # decidable. Mirrors WorkflowExecutor._find_ready_nodes: OR-any
                # across the conditional edges, and a skip still counts as
                # "completed" for dependency purposes, so skipping is NOT
                # transitive -- an unconditional downstream node still runs.
                if use_conditional_edges and node_id in conditional_edges:
                    if not self._conditions_met(node_id, conditional_edges[node_id], outputs):
                        workflow.logger.info(f"Skipping {node_id}: no incoming edge condition matched")
                        completed.add(node_id)
                        execution_trace.append(node_id)
                        auto_completed_this_pass += 1
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
                if (
                    graph_version >= AGENT_CONTEXT_GRAPH_VERSION
                    and generation > 0
                ):
                    context.update(
                        {
                            "graphVersion": graph_version,
                            "generation": generation,
                            "root_execution_id": workflow_data.get(
                                "root_execution_id"
                            )
                            or execution_id,
                            "data_scope_id": workflow_data.get(
                                "data_scope_id"
                            ),
                            # Context thread identity is distinct from the
                            # output/data namespace. Trigger listeners provide
                            # a per-firing execution id and, for chat events,
                            # the explicit conversation session.
                            "context_execution_id": workflow_data.get(
                                "context_execution_id"
                            ),
                            "context_session_id": workflow_data.get(
                                "context_session_id"
                            ),
                        }
                    )
                if "user_id" in workflow_data:
                    context["user_id"] = workflow_data.get("user_id")
                context["temporal_worker_pool_enabled"] = bool(
                    frozen_routing.get("worker_pool_enabled")
                )

                # F4.B: agent-as-child-workflow takes precedence over the
                # activity path for the 15 migrating agent types when its
                # flag is on. Tool calls inside the agent loop become
                # per-type activities (F4.A path) automatically.
                dispatch = self._resolve_dispatch(
                    node_type,
                    graph_version=graph_version,
                    generation=generation,
                    context_v2_enabled=(
                        graph_version >= AGENT_CONTEXT_GRAPH_VERSION
                        and generation > 0
                    ),
                    routing_snapshot=frozen_routing,
                )
                # A preceding child start yields to the workflow event loop,
                # so a pause signal may have landed since ``ready`` was
                # computed. Re-admit every command in the batch.
                await self._wait_until_resumed()
                if dispatch["kind"] == "child_workflow":
                    # ``workflow.start_child_workflow`` is ``async def`` —
                    # awaiting it returns the ``ChildWorkflowHandle`` once
                    # the child has been started by the server (fast; not
                    # blocking on child completion). The handle is a
                    # Task-like with ``.done()`` so it slots into the same
                    # FIRST_COMPLETED loop as activity handles.
                    # Patch-gated for replay safety. The legacy label-derived
                    # id is preserved for histories recorded before
                    # ``machina-agent-child-id-v2``. New runs use the actual
                    # root Temporal workflow id plus the exact canvas node id;
                    # labels are mutable and need not be unique.
                    child_workflow_id = _agent_child_workflow_id(
                        workflow.info().workflow_id,
                        node_id,
                    )
                    # Unbounded by design: a run may execute — or stay
                    # cooperatively paused — for months, and Temporal's
                    # timeout timers keep ticking through a pause.
                    child_start_kwargs: Dict[str, Any] = dict(
                        args=[context],
                        id=child_workflow_id,
                    )
                    handle = await workflow.start_child_workflow(
                        dispatch["name"],
                        **child_start_kwargs,
                    )
                    running[node_id] = handle
                    workflow.logger.info(f"Scheduled child workflow for node: {node_id} " f"(workflow={dispatch['name']})")
                else:
                    # F4.A activity path: per-type when the plugin class is
                    # registered AND the per-type flag is on; legacy
                    # execute_node_activity otherwise. ``activity_id`` =
                    # node_id so the Temporal Web UI history tab labels
                    # each activity by the canvas node it ran.
                    # Honor each plugin's declared RetryPolicy.
                    activity_retry_policy = retry_policy
                    from services.node_registry import get_node_class

                    node_cls = get_node_class(node_type)
                    if node_cls is not None:
                        # Patch-open: the effective policy (annotations
                        # decide attempts). Patch-closed: the class
                        # attribute, exactly as pre-patch histories recorded.
                        declared_retry = (
                            node_cls.effective_retry_policy()
                            if use_effective_retry
                            else node_cls.retry_policy
                        )
                        activity_retry_policy = declared_retry.to_temporal()
                    start_kwargs: Dict[str, Any] = dict(
                        args=[context],
                        activity_id=node_id,
                        # Heartbeat is the liveness mechanism (activities
                        # self-heartbeat every 30s); start_to_close only
                        # bounds a single legitimate step. Pre-patch
                        # histories replay against the old 10min cap.
                        start_to_close_timeout=_NODE_ACTIVITY_START_TO_CLOSE,
                        heartbeat_timeout=timedelta(minutes=2),
                        retry_policy=activity_retry_policy,
                    )
                    if dispatch.get("queue") is not None:
                        start_kwargs["task_queue"] = dispatch["queue"]

                    handle = workflow.start_activity(dispatch["name"], **start_kwargs)
                    running[node_id] = handle
                    workflow.logger.info(
                        f"Scheduled activity for node: {node_id} "
                        f"(activity={dispatch['name']}, queue={dispatch.get('queue') or 'default'})"
                    )

            # Exit only when nothing is running AND this pass made no
            # progress. Auto-completed triggers count as progress — they
            # may have unblocked downstream nodes that the next
            # _find_ready_nodes pass will pick up.
            if not running:
                if auto_completed_this_pass:
                    continue
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
                    "error_type": result.get("error_type", "Error"),
                }
                errors.append(error_info)
                workflow.logger.error(f"Node failed: {done_id} - {error_info['error']}")

                # Stop workflow on failure
                # TODO: Could add option to continue with partial results
                break

        # Build final result
        success = len(errors) == 0 and len(completed) == len(node_map)

        if errors:
            await self._pause_deployment_on_failure(workflow_data, nodes, errors)

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

    def _resolve_dispatch(
        self,
        node_type: str,
        *,
        graph_version: int = 0,
        generation: int = 0,
        context_v2_enabled: bool = False,
        routing_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve dispatch kind for a node type.

        Returns one of:
          - ``{"kind": "child_workflow", "name": "AgentWorkflow"}`` — when
            F4.B is enabled AND node_type is in ``AGENT_WORKFLOW_TYPES``.
          - ``{"kind": "activity", "name": <activity_name>, "queue": <queue|None>}``
            — F4.A per-type activity OR legacy fallback, depending on
            ``temporal_per_type_dispatch``.

        New histories pass ``routing_snapshot`` from workflow input so no
        mutable Settings value can alter their command shape. ``None`` is the
        replay-only compatibility path for pre-patch histories.
        """
        if routing_snapshot is None:
            from core.config import Settings

            settings = Settings()
            agent_workflow_enabled = getattr(
                settings,
                "temporal_agent_workflow_enabled",
                False,
            )
            per_type_dispatch_enabled = getattr(
                settings,
                "temporal_per_type_dispatch",
                False,
            )
            worker_pool_enabled = getattr(
                settings,
                "temporal_worker_pool_enabled",
                False,
            )
        else:
            agent_workflow_enabled = (
                routing_snapshot.get("agent_workflow_enabled") is True
            )
            per_type_dispatch_enabled = (
                routing_snapshot.get("per_type_dispatch_enabled") is True
            )
            worker_pool_enabled = (
                routing_snapshot.get("worker_pool_enabled") is True
            )
        if agent_workflow_enabled and node_type in AGENT_WORKFLOW_TYPES:
            return {"kind": "child_workflow", "name": "AgentWorkflow"}

        name, queue = self._resolve_activity(
            node_type,
            per_type_dispatch_enabled=per_type_dispatch_enabled,
            worker_pool_enabled=worker_pool_enabled,
        )
        return {"kind": "activity", "name": name, "queue": queue}

    def _resolve_activity(
        self,
        node_type: str,
        *,
        per_type_dispatch_enabled: Optional[bool] = None,
        worker_pool_enabled: Optional[bool] = None,
    ) -> tuple[str, str | None]:
        """Resolve (activity_name, task_queue) for a node type.

        F4.A: when ``settings.temporal_per_type_dispatch`` is on AND the
        plugin class is registered, returns the per-type activity name
        ``node.{type}.v{version}``.

        Wave 16.3: the returned queue is ``cls.task_queue`` when
        ``settings.temporal_worker_pool_enabled`` is on (each declared
        queue then has a dedicated ``TemporalWorkerPool`` worker polling
        it — wired in main.py right after the manager starts), or
        ``None`` otherwise so the activity stays on the workflow's
        default queue, which the single ``TemporalWorkerManager`` polls.
        The flag is the rollback channel: flipping it off routes every
        activity back to the manager worker without code changes.

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
        from services.node_registry import get_node_class

        if (
            per_type_dispatch_enabled is None
            or worker_pool_enabled is None
        ):
            from core.config import Settings

            settings = Settings()
            if per_type_dispatch_enabled is None:
                per_type_dispatch_enabled = (
                    settings.temporal_per_type_dispatch
                )
            if worker_pool_enabled is None:
                worker_pool_enabled = (
                    settings.temporal_worker_pool_enabled
                )

        if not per_type_dispatch_enabled:
            return "execute_node_activity", None

        cls = get_node_class(node_type)
        if cls is None:
            return "execute_node_activity", None

        queue = cls.task_queue if worker_pool_enabled else None
        return f"node.{cls.type}.v{cls.version}", queue

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
                    # ``str(ActivityError)`` is the SDK wrapper text; the
                    # plugin's envelope rides on the cause. Unwrap so
                    # ``errors[]`` and the pause-on-failure reason keep it.
                    return node_id, activity_failure_envelope(e)

        # Wait for first completion using Temporal's wait
        await workflow.wait_condition(lambda: any(h.done() for _, h in items))

        # Find the completed one
        for node_id, handle in items:
            if handle.done():
                del running[node_id]
                try:
                    result = await handle
                    return node_id, result
                except Exception as e:
                    # ``str(ActivityError)`` is the SDK wrapper text; the
                    # plugin's envelope rides on the cause. Unwrap so
                    # ``errors[]`` and the pause-on-failure reason keep it.
                    return node_id, activity_failure_envelope(e)

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
            source_id = edge.get("source")

            # Edges to config handles mean source is a config node
            if _is_config_edge(edge, node_map):
                config_ids.add(source_id)

            # Android services connected as direct tools
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
            if e.get("source") not in config_ids
            and e.get("target") not in config_ids
            and not _is_config_edge(e, node_map)
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

    def _build_conditional_edge_map(
        self,
        edges: List[Dict],
        node_map: Dict[str, Dict],
    ) -> Dict[str, List[Dict]]:
        """Group incoming edges that carry a condition, keyed by target node.

        Only targets that appear here are gated; a node with no conditional
        incoming edge keeps the unconditional "all deps done" rule.
        """
        conditional: Dict[str, List[Dict]] = {}
        for edge in edges:
            target = edge.get("target")
            if target not in node_map:
                continue
            if not (edge.get("data") or {}).get("condition"):
                continue
            conditional.setdefault(target, []).append(edge)
        return conditional

    def _conditions_met(
        self,
        target_node_id: str,
        edges: List[Dict],
        outputs: Dict[str, Any],
    ) -> bool:
        """Return whether any conditional incoming edge admits this node.

        OR-any, matching ``WorkflowExecutor._evaluate_incoming_conditions``.
        A source that produced no output evaluates against ``{}`` rather than
        raising, so a skipped upstream cannot wedge the graph.
        """
        for edge in edges:
            condition = (edge.get("data") or {}).get("condition")
            if not condition:
                continue
            source_output = outputs.get(edge.get("source")) or {}
            if evaluate_condition(condition, source_output):
                return True
        return False

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
