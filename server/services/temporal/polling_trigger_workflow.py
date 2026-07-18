"""Wave 12 C2: long-lived Temporal workflow for polling triggers.

Closes the durability gap on polling triggers (gmailReceive,
twitterReceive, …) the same way :class:`TriggerListenerWorkflow`
closed it for push triggers. Today's polling lives in
``services/deployment/triggers.py::setup_polling_trigger`` —
a collector/processor ``asyncio.Task`` pair that dies on FastAPI
restart, losing the seen-ID baseline and any in-flight cycle.

This workflow owns the poll loop INSIDE Temporal:

- ``workflow.sleep(interval)`` between cycles → replayable, no
  per-cycle heartbeat overhead, survives worker restarts
  (per temporal.io/blog/very-long-running-workflows).
- Per-cycle Temporal activity (``poll.{type}.v{version}``, emitted
  by :meth:`PollingTriggerNode.as_poll_activity`) does ONE cycle and
  returns new events + the updated seen-id set.
- For each new event, spawn a child :class:`MachinaWorkflow` with
  the trigger pre-executed (same ``_build_run_graph`` helper as
  the push-trigger listener — single source of truth for run-filter
  semantics).
- ``continueAsNew`` every ~16K processed events to keep Event
  History bounded.

Cross-confirmed pattern with Temporal docs + samples-python:
- Long-lived workflow with sleep+continueAsNew: temporal.io/blog/
  very-long-running-workflows
- Activities for external I/O (Gmail / Twitter API calls): docs.
  temporal.io/develop/python/core-application#activities
- ParentClosePolicy.ABANDON for child run workflows so listener
  cancel never strands in-flight executions
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Set

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy

from ._retry_policies import DEFAULT_ACTIVITY_RETRY


# Same continueAsNew threshold as TriggerListenerWorkflow — keeps Event
# History under Temporal's soft ceiling. The actual count depends on
# both cycles AND events spawned per cycle; 16K events ≈ 16K spawn
# entries + N cycles, well below the 50K guidance.
_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 16_000

# Default poll interval (seconds) if listener_data doesn't supply one.
# Mirrors PollingTriggerNode.default_poll_interval defaults.
_DEFAULT_POLL_INTERVAL_S = 60

# Activity timeout: 4× the poll interval gives the activity plenty of
# headroom for slow Gmail / Twitter responses without hanging forever.
# Workflow ``RetryPolicy`` (default) handles transient failures.
_ACTIVITY_TIMEOUT_MULT = 4


@workflow.defn(name="PollingTriggerWorkflow", sandboxed=False)
class PollingTriggerWorkflow:
    """Long-lived polling-trigger workflow.

    Determinism note: all state lives on ``self`` and is reconstructed
    from Event History on replay. Provider-side ``seen_ids`` (e.g.
    Gmail message IDs) is the activity's output → workflow state →
    next activity's input — never mutated mid-workflow. Event-id dedup
    set drains on ``continueAsNew`` (intentional; events arriving in
    the new run are by definition not duplicates of the prior run).
    """

    def __init__(self) -> None:
        self._seen_event_ids: Set[str] = set()
        self._processed_count: int = 0

    @workflow.run
    async def run(self, listener_data: Dict[str, Any]) -> Dict[str, Any]:
        """Poll loop body.

        ``listener_data`` shape (deployment-supplied)::

            {
                "workflow_id": str,        # OpenCompany deployment workflow_id
                "trigger_node_id": str,    # node id that fires on each event
                "node_type": str,          # e.g. "googleGmailReceive"
                "version": int,            # plugin class version (for activity name)
                "filter_params": Dict,     # plugin params (poll_interval etc.)
                "nodes": List[Dict],       # full deployment graph snapshot
                "edges": List[Dict],
                "session_id": str,
                "tenant_id": Optional[str],
                "seen_ids": List[str],     # carried across continueAsNew; empty on first start
            }

        Returns when ``continueAsNew`` fires. Deployment cancel uses
        a graceful ``workflow.cancel()`` — the loop's
        ``CancelledError`` propagates and the workflow ends.
        """
        node_type = listener_data["node_type"]
        version = listener_data.get("version", 1)
        activity_name = f"poll.{node_type}.v{version}"

        params = listener_data.get("filter_params", {}) or {}
        poll_interval = int(params.get("poll_interval") or _DEFAULT_POLL_INTERVAL_S)
        activity_timeout_s = max(30, poll_interval * _ACTIVITY_TIMEOUT_MULT)

        # Carry seen_ids across continueAsNew. First-start payload has
        # ``seen_ids=[]`` and the first activity call is baseline-only.
        seen_ids: Set[str] = set(listener_data.get("seen_ids") or [])
        is_baseline = not seen_ids

        workflow.logger.info(
            f"PollingTriggerWorkflow started: workflow_id={listener_data.get('workflow_id')} "
            f"node={listener_data.get('trigger_node_id')} type={node_type} "
            f"interval={poll_interval}s baseline={is_baseline}"
        )

        while True:
            if is_baseline:
                # Establish seen baseline immediately on first run so we
                # don't re-emit items the user has had since before deploy.
                is_baseline = False
                cycle_payload = {
                    "node_id": listener_data["trigger_node_id"],
                    "params": params,
                    "seen_ids": [],
                    "baseline_only": True,
                }
            else:
                await workflow.sleep(timedelta(seconds=poll_interval))
                cycle_payload = {
                    "node_id": listener_data["trigger_node_id"],
                    "params": params,
                    "seen_ids": list(seen_ids),
                    "baseline_only": False,
                }

            try:
                # Wave 12 D1: explicit RetryPolicy with
                # non_retryable_error_types=("NodeUserError", ...) —
                # poll-cycle failures from user-correctable causes
                # (bad filter expression, missing credential) fail fast
                # instead of burning 3 retries per cycle.
                result = await workflow.execute_activity(
                    activity_name,
                    cycle_payload,
                    activity_id=listener_data["trigger_node_id"],
                    start_to_close_timeout=timedelta(seconds=activity_timeout_s),
                    retry_policy=DEFAULT_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                # Activity exhausted its RetryPolicy. Log + continue —
                # don't terminate the listener over one bad cycle.
                # Workflow Event History records the failure for ops.
                workflow.logger.error(f"PollingTriggerWorkflow cycle failed (will retry next interval): {exc}")
                continue

            seen_ids = set(result.get("seen_ids") or [])
            events = result.get("events") or []

            for event in events:
                event_id = event.get("id")
                if not event_id or event_id in self._seen_event_ids:
                    continue
                self._seen_event_ids.add(event_id)
                try:
                    await self._spawn_child_run(event, listener_data)
                except Exception as spawn_exc:  # noqa: BLE001
                    # Per-event spawn failure logged; subsequent events
                    # still try. Same isolation contract as the push
                    # listener.
                    workflow.logger.error(f"PollingTriggerWorkflow spawn failed for event.id={event_id}: {spawn_exc}")
                self._processed_count += 1

            if self._processed_count >= _MAX_EVENTS_BEFORE_CONTINUE_AS_NEW:
                workflow.logger.info(f"PollingTriggerWorkflow continue_as_new: processed={self._processed_count}")
                # Carry seen_ids forward so the new run doesn't re-emit
                # what's already been seen by the provider.
                listener_data["seen_ids"] = list(seen_ids)
                workflow.continue_as_new(listener_data)

    async def _spawn_child_run(
        self,
        event: Dict[str, Any],
        listener_data: Dict[str, Any],
    ) -> None:
        """Start a child :class:`MachinaWorkflow` with the trigger
        pre-executed against this event payload.

        Reuses ``_build_run_graph`` from
        :mod:`services.temporal.trigger_listener_workflow` so the
        filtered-graph semantics (n8n stop-at-trigger downstream walk,
        config nodes via input handles, toolkit sub-nodes, agent tool
        nodes) stay single-source. Mirrors
        :meth:`TriggerListenerWorkflow._spawn_child_run` exactly.
        """
        from services.temporal.trigger_listener_workflow import (
            _broadcast_trigger_idle,
            _broadcast_trigger_waiting,
            _build_run_graph,
        )

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
        # Set at deploy time from the workflow's display name.
        workflow_slug = listener_data.get("workflow_slug") or workflow_id
        # Trigger node's label (``gmailReceive`` / ``twitterReceive`` /
        # F2-renamed). Pre-computed at deploy time so the workflow
        # sandbox doesn't have to slugify.
        trigger_label = listener_data.get("trigger_label") or listener_data.get("trigger_node_id")
        tenant_id = listener_data.get("tenant_id")

        # Polling activity returns plugin-native payload dicts (Gmail
        # email envelope, Twitter tweet payload). For Temporal-side
        # introspection we pass the dict as both the trigger output
        # AND nest it under ``_event_envelope`` so downstream nodes
        # can route off the original shape — matches the push-listener
        # contract.
        trigger_output = {**event, "_event_envelope": event}

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id=trigger_node_id,
            trigger_output=trigger_output,
            nodes=nodes,
            edges=edges,
        )

        # Lazy fallback to workflow.uuid4() only when event.id is missing —
        # eager-eval default-arg form would trip _NotInWorkflowEventLoopError
        # in unit tests + waste entropy on the hot path.
        # Format: ``<slug>-<trigger_label>-<event_id>`` — workflow name
        # + trigger label (which conveys the kind) + per-firing event id.
        event_id = event.get("id") or workflow.uuid4().hex
        child_id = f"{workflow_slug}-{trigger_label}-{event_id}"

        await _broadcast_trigger_idle(
            node_id=trigger_node_id,
            workflow_id=workflow_id,
            event_id=event_id,
            event_type=listener_data.get("event_type", ""),
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

        workflow.logger.info(f"PollingTriggerWorkflow spawned child run: child_id={child_id} " f"event.id={event.get('id')}")

        await _broadcast_trigger_waiting(
            node_id=trigger_node_id,
            workflow_id=workflow_id,
            event_type=listener_data.get("event_type", ""),
            processed_count=self._processed_count + 1,
        )


__all__ = ["PollingTriggerWorkflow"]
