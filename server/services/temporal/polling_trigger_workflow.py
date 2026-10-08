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
from .workflow import TEMPORAL_ROUTING_INPUT_KEY


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
# Child runs may execute — or stay cooperatively paused — for months,
# so they start without lifetime caps; Temporal's timeout timer keeps
# ticking through a pause. Liveness comes from activity heartbeats.
#
# _processed_count increments only per EMITTED event, but every poll
# cycle burns ~8-11 history events (sleep timer + activity + workflow
# tasks) regardless — a quiet mailbox at the 60s default reached the
# server's ~51,200-event hard termination in ~3 days with the counter
# frozen at 0. History pressure is therefore checked every cycle
# (server suggestion + the soft cap below), poll intervals are clamped
# to a sane floor, and the pause flag is carried across the rollover
# instead of blocking it behind a resume that may be months away.
_HISTORY_SOFT_CAP = 10_000
# Defensive floor for user-supplied poll intervals: a few-second
# interval burns ~100K+ history events/day. Mirrors the plugin-side
# clamp (PollingTriggerNode.poll_interval_clamp) that the legacy
# asyncio path applies but workflow payloads historically did not.
# Applied unconditionally in ``run`` (no ``workflow.patched`` marker), as
# is the identical floor in WorkflowControlWorkflow's polling path.
_MIN_POLL_INTERVAL_S = 30


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
        self._control_paused = False

    @workflow.signal
    async def pause(self) -> None:
        self._control_paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._control_paused = False

    async def _wait_until_resumed(self) -> None:
        if self._control_paused:
            await workflow.wait_condition(lambda: not self._control_paused)

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
        # Rehydrate rollover-carried pause state.
        self._control_paused = bool(listener_data.get("control_paused"))

        params = listener_data.get("filter_params", {}) or {}
        poll_interval = int(params.get("poll_interval") or _DEFAULT_POLL_INTERVAL_S)
        poll_interval = max(_MIN_POLL_INTERVAL_S, poll_interval)
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
            if self._control_paused:
                await workflow.wait_condition(lambda: not self._control_paused)
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
                await self._wait_until_resumed()
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
                    await self._wait_until_resumed()
                    from services.temporal.trigger_listener_workflow import (
                        event_workflow_search_attributes,
                    )

                    await self._spawn_child_run(
                        event,
                        listener_data,
                        admission_check=self._wait_until_resumed,
                        search_attributes=event_workflow_search_attributes(
                            listener_data.get("workflow_id")
                        ),
                    )
                except Exception as spawn_exc:  # noqa: BLE001
                    # Per-event spawn failure logged; subsequent events
                    # still try. Same isolation contract as the push
                    # listener.
                    workflow.logger.error(f"PollingTriggerWorkflow spawn failed for event.id={event_id}: {spawn_exc}")
                self._processed_count += 1

            should_rollover = self._processed_count >= _MAX_EVENTS_BEFORE_CONTINUE_AS_NEW
            if not should_rollover:
                # Poll cycles burn history even with zero emitted events —
                # check pressure every cycle, not only per processed event.
                from services.temporal.trigger_listener_workflow import _history_pressure

                should_rollover = _history_pressure(_HISTORY_SOFT_CAP)
            if should_rollover:
                workflow.logger.info(f"PollingTriggerWorkflow continue_as_new: processed={self._processed_count}")
                # Carry the pause flag instead of blocking the rollover
                # behind a resume that may be months away (signal traffic
                # would overflow the history mid-pause).
                listener_data["control_paused"] = self._control_paused
                # Carry seen_ids forward so the new run doesn't re-emit
                # what's already been seen by the provider.
                listener_data["seen_ids"] = list(seen_ids)
                workflow.continue_as_new(listener_data)

    async def _spawn_child_run(
        self,
        event: Dict[str, Any],
        listener_data: Dict[str, Any],
        admission_check=None,
        search_attributes=None,
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
        from services.temporal._retry_policies import QUICK_ACTIVITY_RETRY
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
        graph_version = int(
            listener_data.get("graphVersion")
            or listener_data.get("graph_version")
            or 0
        )
        generation = int(listener_data.get("generation") or 0)
        # Controlled generations execute their immutable admitted snapshot.
        # Legacy uncontrolled deployments retain hot graph lookup.
        if bool(workflow_id) and not listener_data.get("data_scope_id"):
            try:
                latest = await workflow.execute_activity(
                    "load_persisted_workflow_graph_activity",
                    {"workflow_id": workflow_id},
                    start_to_close_timeout=timedelta(seconds=10),
                )
                if latest.get("found"):
                    nodes = latest.get("nodes") or []
                    edges = latest.get("edges") or []
                    graph_version = int(
                        latest.get("graphVersion") or graph_version
                    )
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

        # Status broadcasts are cosmetic UI signalling; without an explicit
        # policy Temporal retries a failing activity forever, wedging the
        # serialized spawn loop behind a dead broadcaster.
        broadcast_retry = QUICK_ACTIVITY_RETRY

        try:
            await _broadcast_trigger_idle(
                node_id=trigger_node_id,
                workflow_id=workflow_id,
                event_id=event_id,
                event_type=listener_data.get("event_type", ""),
                retry_policy=broadcast_retry,
            )
        except Exception as exc:  # noqa: BLE001 — cosmetic UI signalling
            workflow.logger.warning(f"Trigger status broadcast failed (non-fatal): {exc}")

        if admission_check is not None:
            await admission_check()

        child_options = {
            "id": child_id,
            "parent_close_policy": ParentClosePolicy.ABANDON,
            "id_reuse_policy": (
                WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
            ),
        }
        if search_attributes is not None:
            child_options["search_attributes"] = search_attributes

        child_payload = {
            "nodes": filtered_nodes,
            "edges": filtered_edges,
            "session_id": session_id,
            "workflow_id": workflow_id,
            "workflow_slug": workflow_slug,
            "tenant_id": tenant_id,
        }
        frozen_routing = listener_data.get(TEMPORAL_ROUTING_INPUT_KEY)
        if listener_data.get("browser_routing_version") == 1:
            child_payload.update(browser_routing_version=1, browser_bindings=listener_data.get("browser_bindings") or {})
        if isinstance(frozen_routing, dict):
            child_payload[TEMPORAL_ROUTING_INPUT_KEY] = dict(
                frozen_routing
            )
            if "user_id" in listener_data:
                child_payload["user_id"] = listener_data.get("user_id")
        if graph_version >= 2 and generation > 0:
            event_session_id = str(event.get("session_id") or "").strip()
            child_payload.update(
                {
                    "graphVersion": graph_version,
                    "generation": generation,
                    "context_execution_id": child_id,
                    "execution_id": listener_data.get("execution_id"),
                    "root_execution_id": listener_data.get(
                        "root_execution_id"
                    ),
                    "data_scope_id": listener_data.get("data_scope_id"),
                }
            )
            if event_session_id and event_session_id != "default":
                child_payload["context_session_id"] = event_session_id

        if listener_data.get("parameter_snapshot"):
            child_payload["parameter_snapshot"] = listener_data["parameter_snapshot"]
        await workflow.start_child_workflow(
            "MachinaWorkflow",
            args=[child_payload],
            **child_options,
        )

        workflow.logger.info(f"PollingTriggerWorkflow spawned child run: child_id={child_id} " f"event.id={event.get('id')}")

        try:
            await _broadcast_trigger_waiting(
                node_id=trigger_node_id,
                workflow_id=workflow_id,
                event_type=listener_data.get("event_type", ""),
                processed_count=self._processed_count + 1,
                retry_policy=broadcast_retry,
            )
        except Exception as exc:  # noqa: BLE001 — cosmetic UI signalling
            workflow.logger.warning(f"Trigger status broadcast failed (non-fatal): {exc}")


__all__ = ["PollingTriggerWorkflow"]
