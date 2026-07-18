"""Wave 12 C1 (canary): long-lived Temporal workflow that owns a trigger's wait.

One :class:`TriggerListenerWorkflow` runs per (deployment, event-trigger)
pair. It survives FastAPI process restarts because Temporal replays its
state from Event History — the durability gap that the in-process
``services/deployment/triggers.py:setup_event_trigger`` collector/processor
``asyncio.Task`` pair leaves open.

Flow per signal:

1. ``dispatch.emit(event)`` runs a Visibility query
   ``EventType='<type>' AND ExecutionStatus='Running'`` and signals each
   matching listener (this class) with ``on_event``.
2. ``on_event`` dedups by ``event.id`` (per-run state, reconstructed
   deterministically from Event History on replay) and queues the
   payload.
3. The main ``run`` loop ``wait_condition``s for any queued event, pops
   the head, builds the filtered downstream graph, and starts a child
   :class:`MachinaWorkflow` with the trigger node marked ``_pre_executed``.
4. Child workflow uses ``parent_close_policy=ABANDON`` so listener
   cancellation never kills in-flight execution runs.

Canary scope (2026-05-14): only ``webhookTrigger`` ships with this
wiring on the deployment-manager side. Other trigger types stay on the
legacy collector/processor path until the canary proves out. The
workflow itself is type-agnostic — the listener treats the event
payload as opaque and the filter shape is provided by the deployment
at workflow start.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy


# 50K events is the Temporal Event-History soft ceiling per the
# very-long-running-workflows blog post — past it the workflow
# spends more time replaying history than doing work. The listener
# does ~2-3 events per inbound trigger (signal accept + child spawn),
# so this caps at ~16K triggers between continueAsNew checkpoints.
# Per-event histogram is empirical; tune later.
_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 16_000


@workflow.defn(name="TriggerListenerWorkflow", sandboxed=False)
class TriggerListenerWorkflow:
    """Long-lived listener that funnels signals into child MachinaWorkflow runs.

    Determinism note: all state lives on ``self`` and is reconstructed
    from Event History on replay. The ``_seen_event_ids`` set drains
    on ``continueAsNew`` (intentional — different listener runs are
    different consumers).
    """

    def __init__(self) -> None:
        self._seen_event_ids: Set[str] = set()
        self._matched_events: List[Dict[str, Any]] = []
        self._processed_count: int = 0

    @workflow.signal
    async def on_event(self, event_payload: Dict[str, Any]) -> None:
        """Receive an event from ``services.events.dispatch.emit``.

        Same shape + dedup contract as :meth:`MachinaWorkflow.on_event`.
        Malformed payloads (missing ``id``) are dropped — every other
        path mints an id at envelope construction, so missing-id means
        a producer wired itself wrong.
        """
        event_id = event_payload.get("id")
        if not event_id:
            workflow.logger.warning("TriggerListener.on_event: skipping malformed envelope without 'id'")
            return
        if event_id in self._seen_event_ids:
            workflow.logger.debug(f"TriggerListener.on_event: dedup hit for event.id={event_id}")
            return
        self._seen_event_ids.add(event_id)
        self._matched_events.append(event_payload)

    @workflow.run
    async def run(self, listener_data: Dict[str, Any]) -> Dict[str, Any]:
        """Loop forever spawning a child :class:`MachinaWorkflow` per matched event.

        ``listener_data`` shape (deployment-supplied):
            {
                "workflow_id": str,        # OpenCompany deployment workflow_id
                "trigger_node_id": str,    # node id that "fires" on each event
                "node_type": str,          # e.g. "webhookTrigger"
                "event_type": str,         # e.g. "com.opencompany.webhook.received"
                "filter_params": Dict,     # used by the event-side filter via dispatch.emit
                "nodes": List[Dict],       # full deployment graph snapshot
                "edges": List[Dict],
                "session_id": str,
                "tenant_id": Optional[str],
            }

        Returns when ``continueAsNew`` fires — the new run picks up
        immediately under the same workflow-id. Cancellation by the
        deployment manager cancels the workflow normally.
        """
        workflow.logger.info(
            f"TriggerListener started: workflow_id={listener_data.get('workflow_id')} "
            f"node={listener_data.get('trigger_node_id')} "
            f"event_type={listener_data.get('event_type')}"
        )

        while True:
            await workflow.wait_condition(lambda: bool(self._matched_events))
            event = self._matched_events.pop(0) if self._matched_events else None
            if event is None:
                continue

            try:
                await self._spawn_child_run(event, listener_data)
            except Exception as exc:  # noqa: BLE001
                # Per-event spawn failures don't kill the listener.
                # Log and move on; the rejected event still counts as
                # "seen" so a producer retry with the same id won't
                # re-fire (correct: server-side dedup applies even on
                # downstream failure).
                workflow.logger.error(f"TriggerListener spawn failed for event.id={event.get('id')}: {exc}")

            self._processed_count += 1
            if self._processed_count >= _MAX_EVENTS_BEFORE_CONTINUE_AS_NEW:
                workflow.logger.info(f"TriggerListener continue_as_new: processed={self._processed_count}")
                workflow.continue_as_new(args=[listener_data])

    async def _spawn_child_run(
        self,
        event: Dict[str, Any],
        listener_data: Dict[str, Any],
    ) -> None:
        """Build the filtered downstream graph + start a child MachinaWorkflow.

        The trigger node is marked ``_pre_executed=True`` with the event
        envelope's ``data`` payload as its output. Sibling triggers in
        the same deployment are marked ``_pre_executed=True`` with
        ``{not_triggered: True}`` so MachinaWorkflow doesn't block on
        them (mirrors ``DeploymentManager._execute_from_trigger``).

        Status broadcast lifecycle (matches legacy
        ``deployment/triggers.py`` collector/processor):
          - Before spawn → trigger node ``"idle"`` with "Graph executing..."
          - After spawn returns → trigger node ``"waiting"`` (child runs
            independently per ``parent_close_policy=ABANDON``).
        """
        trigger_node_id = listener_data["trigger_node_id"]
        nodes = listener_data["nodes"]
        edges = listener_data["edges"]
        session_id = listener_data.get("session_id", "default")
        workflow_id = listener_data.get("workflow_id")
        try:
            use_latest_graph = bool(workflow_id) and workflow.patched("trigger-latest-graph-v1")
        except RuntimeError:  # direct unit invocation outside Temporal runtime
            use_latest_graph = False
        if use_latest_graph:
            try:
                latest = await workflow.execute_activity(
                    "load_persisted_workflow_graph_activity",
                    {"workflow_id": workflow_id},
                    start_to_close_timeout=timedelta(seconds=10),
                )
                if latest.get("found"):
                    nodes = latest.get("nodes") or []
                    edges = latest.get("edges") or []
            except Exception as exc:  # snapshot remains a safe fallback
                workflow.logger.warning(
                    f"Current graph lookup failed for {workflow_id}; using deployment snapshot: {exc}"
                )
        # Human-readable slug prefix for the Temporal Web UI listing.
        # Set at deploy time from the workflow's display name; falls back
        # to workflow_id for one-off deploys without a saved DB row.
        workflow_slug = listener_data.get("workflow_slug") or workflow_id
        # Trigger node's label (``telegramReceive`` / ``chatTrigger`` /
        # F2-renamed). Pre-computed at deploy time so the workflow
        # sandbox doesn't have to slugify.
        trigger_label = listener_data.get("trigger_label") or trigger_node_id
        tenant_id = listener_data.get("tenant_id")

        # event.data is the producer-supplied payload (webhook body /
        # message details / etc.). The full envelope (specversion, type,
        # subject, …) stays in event for any downstream introspection
        # via the agent layer, but the trigger output is the data dict.
        trigger_output = event.get("data") if isinstance(event.get("data"), dict) else {}
        trigger_output = {**trigger_output, "_event_envelope": event}

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id=trigger_node_id,
            trigger_output=trigger_output,
            nodes=nodes,
            edges=edges,
        )

        # Stable child workflow ID derived from event.id gives free
        # idempotency via WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY:
        # if the same event.id arrives twice (producer retry across
        # listener restart), Temporal rejects the duplicate start.
        # Lazy fallback to workflow.uuid4() only if event.id is missing
        # (shouldn't happen — on_event drops malformed payloads).
        # Format: ``<slug>-<trigger_label>-<event_id>`` — workflow name
        # + trigger label (which conveys the kind) + per-firing event id.
        event_id = event.get("id") or workflow.uuid4().hex
        child_id = f"{workflow_slug}-{trigger_label}-{event_id}"

        await _broadcast_trigger_idle(
            node_id=trigger_node_id,
            workflow_id=workflow_id,
            event_id=event_id,
            event_type=event.get("type", ""),
        )

        await workflow.start_child_workflow(
            "MachinaWorkflow",
            args=[
                {
                    "nodes": filtered_nodes,
                    "edges": filtered_edges,
                    "session_id": session_id,
                    "workflow_id": workflow_id,
                    "workflow_slug": workflow_slug,
                    "tenant_id": tenant_id,
                }
            ],
            id=child_id,
            parent_close_policy=ParentClosePolicy.ABANDON,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            execution_timeout=timedelta(hours=1),
            run_timeout=timedelta(hours=1),
        )

        workflow.logger.info(
            f"TriggerListener spawned child run: child_id={child_id} " f"event.id={event.get('id')} event.type={event.get('type')}"
        )

        await _broadcast_trigger_waiting(
            node_id=trigger_node_id,
            workflow_id=workflow_id,
            event_type=listener_data.get("event_type", ""),
            processed_count=self._processed_count + 1,
        )


# ---------------------------------------------------------------------------
# Status-broadcast helpers — thin wrappers around the activity so the
# spawn loop reads cleanly. Activity name string matches the @activity.defn
# registration in services/temporal/activities.py.
# ---------------------------------------------------------------------------


_STATUS_ACTIVITY_NAME = "broadcast_trigger_status_activity"
_STATUS_ACTIVITY_TIMEOUT = timedelta(seconds=5)


async def _broadcast_trigger_idle(
    *,
    node_id: str,
    workflow_id: Optional[str],
    event_id: str,
    event_type: str,
) -> None:
    """Broadcast trigger node ``"idle"`` status with a "Graph executing..."
    message — matches the legacy collector/processor transition so FE
    shows a firing pulse instead of a stuck "waiting" indicator."""
    await workflow.execute_activity(
        _STATUS_ACTIVITY_NAME,
        {
            "node_id": node_id,
            "status": "idle",
            "data": {
                "message": "Graph executing...",
                "is_processing": True,
                "event_id": event_id,
                "event_type": event_type,
            },
            "workflow_id": workflow_id,
        },
        start_to_close_timeout=_STATUS_ACTIVITY_TIMEOUT,
    )


async def _broadcast_trigger_waiting(
    *,
    node_id: str,
    workflow_id: Optional[str],
    event_type: str,
    processed_count: int,
) -> None:
    """Broadcast trigger node back to ``"waiting"`` after the child run
    has been spawned (child completes independently per
    ``parent_close_policy=ABANDON``)."""
    await workflow.execute_activity(
        _STATUS_ACTIVITY_NAME,
        {
            "node_id": node_id,
            "status": "waiting",
            "data": {
                "message": "Waiting for next event...",
                "is_processing": False,
                "event_type": event_type,
                "processed_count": processed_count,
            },
            "workflow_id": workflow_id,
        },
        start_to_close_timeout=_STATUS_ACTIVITY_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Pure graph builder — kept in this module on purpose: the only consumer is
# this workflow class, and inlining keeps the import surface deterministic
# (workflow.defn(sandboxed=False) still benefits from minimal imports).
# Mirrors the logic in DeploymentManager._execute_from_trigger + ._get_downstream_nodes
# but specialised for the listener's "trigger fires once, spawn run" path.
# ---------------------------------------------------------------------------


def _build_run_graph(
    *,
    trigger_node_id: str,
    trigger_output: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build filtered (nodes, edges) for one execution run.

    Marks the firing trigger ``_pre_executed=True`` with ``trigger_output``;
    marks sibling triggers ``_pre_executed=True`` with ``{not_triggered:
    True}`` so MachinaWorkflow doesn't try to wait on them.
    """
    from constants import (
        AI_AGENT_TYPES,
        TOOLKIT_NODE_TYPES,
        WORKFLOW_TRIGGER_TYPES,
    )

    node_types = {n["id"]: n.get("type", "") for n in nodes}

    downstream_ids: Set[str] = set()

    def _collect(current_id: str) -> None:
        for edge in edges:
            if edge.get("source") != current_id:
                continue
            target_id = edge.get("target")
            if not target_id or target_id in downstream_ids:
                continue
            target_type = node_types.get(target_id, "")
            if target_type in WORKFLOW_TRIGGER_TYPES:
                # Stop at trigger nodes — independent event listeners.
                continue
            downstream_ids.add(target_id)
            _collect(target_id)

    _collect(trigger_node_id)

    # Config nodes (memory, tools, etc.) connected to downstream nodes.
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        handle = edge.get("targetHandle") or edge.get("target_handle") or ""
        is_config = handle and handle.startswith("input-") and handle != "input-main"
        if is_config and target in downstream_ids and source not in downstream_ids:
            if node_types.get(source, "") in WORKFLOW_TRIGGER_TYPES:
                continue
            downstream_ids.add(source)

    # Sub-nodes connected to toolkit nodes (n8n Sub-Node pattern).
    toolkit_ids = {n["id"] for n in nodes if n.get("type") in TOOLKIT_NODE_TYPES and n["id"] in downstream_ids}
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        if target in toolkit_ids and source not in downstream_ids:
            downstream_ids.add(source)

    # Tool nodes connected to AI Agent's input-tools handle.
    agent_ids = {n["id"] for n in nodes if n.get("type") in AI_AGENT_TYPES and n["id"] in downstream_ids}
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        handle = edge.get("targetHandle") or edge.get("target_handle") or ""
        if target in agent_ids and handle == "input-tools" and source not in downstream_ids:
            downstream_ids.add(source)

    run_filter = {trigger_node_id} | downstream_ids
    filtered_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        if node["id"] not in run_filter:
            continue
        node_copy = dict(node)
        node_type = node.get("type", "")
        if node["id"] == trigger_node_id:
            node_copy["_pre_executed"] = True
            node_copy["_trigger_output"] = trigger_output
        elif node_type in WORKFLOW_TRIGGER_TYPES:
            node_copy["_pre_executed"] = True
            node_copy["_trigger_output"] = {"not_triggered": True}
        filtered_nodes.append(node_copy)

    filtered_edges = [e for e in edges if e.get("source") in run_filter and e.get("target") in run_filter]

    return filtered_nodes, filtered_edges


__all__ = ["TriggerListenerWorkflow", "_build_run_graph"]
