"""Wave 12 C3: per-firing Temporal workflow for the cron trigger.

Plugin-owned per RFC §6.1 — the cron-specific workflow lives in the
cron_scheduler plugin folder, not at the framework level. The plugin's
``__init__.py`` publishes the class via
:func:`services.temporal.workflow_registry.register_temporal_workflow`;
the Temporal worker collects it on startup.

How it fits with the Temporal Schedule
--------------------------------------

Wave 12 C3 replaces APScheduler-driven cron with a Temporal Schedule
(``client.create_schedule``). The Schedule's action is
``ScheduleActionStartWorkflow`` targeting THIS class. Each firing
(per the cron expression) starts one :class:`CronTriggerWorkflow`
run; the run spawns a child :class:`MachinaWorkflow` with the cron
trigger node pre-executed, then exits. ``parent_close_policy=ABANDON``
keeps the spawned MachinaWorkflow alive after this workflow returns.

Why we need a separate workflow (vs the Schedule starting MachinaWorkflow
directly): Schedule action args are **frozen at create time**. Per-tick
data (firing timestamp) must be computed inside a workflow. This thin
shim does exactly that and nothing more.

Refs:
  - https://docs.temporal.io/develop/python/schedules
  - https://docs.temporal.io/encyclopedia/scheduled-execution
"""

from __future__ import annotations

from typing import Any, Dict

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy
from services.temporal.execution_control import ExecutionControl


@workflow.defn(name="CronTriggerWorkflow", sandboxed=False)
class CronTriggerWorkflow:
    """One-shot per-firing workflow that spawns a child MachinaWorkflow.

    Determinism note: the only mutable state is the firing-time
    timestamp from ``workflow.now()`` (deterministic per run);
    everything else is computed from the static action args.
    """

    @workflow.init
    def __init__(self, listener_data: Dict[str, Any] | None = None) -> None:
        self._control_paused = False
        self._execution_control = ExecutionControl()
        if listener_data is not None:
            self._execution_control.bind(listener_data)

    @workflow.signal
    async def pause(self) -> None:
        if self._execution_control.enabled:
            return
        self._control_paused = True

    @workflow.signal
    async def resume(self) -> None:
        if self._execution_control.enabled:
            return
        self._control_paused = False

    async def _wait_until_resumed(self) -> None:
        if self._execution_control.enabled:
            await self._execution_control.wait_until_running()
            return
        if self._control_paused:
            await workflow.wait_condition(lambda: not self._control_paused)

    @workflow.run
    async def run(self, listener_data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        listener_data = listener_data or {}
        self._execution_control.bind(listener_data)
        if not self._execution_control.enabled:
            return await self._run_firing(listener_data)
        await self._execution_control.register_root()
        try:
            return await self._run_firing(listener_data)
        finally:
            await self._execution_control.unregister_root()
            await workflow.wait_condition(workflow.all_handlers_finished)

    @workflow.update
    async def set_control_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._execution_control.set_control_state(payload)

    @set_control_state.validator
    def validate_set_control_state(self, payload: Dict[str, Any]) -> None:
        self._execution_control.validate_control(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._execution_control.wait_for_checkpoint(payload)

    @wait_for_checkpoint.validator
    def validate_wait_for_checkpoint(self, payload: Dict[str, Any]) -> None:
        self._execution_control.validate_control(payload)

    @workflow.query
    def execution_control_status(self) -> Dict[str, Any]:
        return self._execution_control.status()

    async def _run_firing(self, listener_data: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn one MachinaWorkflow child per cron firing.

        ``listener_data`` shape (deployment-supplied, frozen at
        schedule creation)::

            {
                "workflow_id": str,        # OpenCompany deployment workflow_id
                "trigger_node_id": str,    # cron node id
                "node_type": "cronScheduler",
                "cron_expression": str,    # raw crontab string
                "frequency": str,          # human-readable bucket
                "timezone": str,           # IANA tz name
                "schedule": str,           # human-readable description
                "filter_params": Dict,     # plugin params
                "nodes": List[Dict],       # full deployment graph snapshot
                "edges": List[Dict],
                "session_id": str,
                "tenant_id": Optional[str],
            }

        Returns ``{spawned_child_id, timestamp}`` for the schedule's
        per-firing history visibility.
        """
        trigger_output = _build_trigger_output(listener_data)

        # Reuse the listener filter-graph helper so cron / push / poll
        # canary paths share identical n8n stop-at-trigger / config-node /
        # toolkit / agent-tool semantics — single source of truth.
        from services.temporal.trigger_listener_workflow import _build_run_graph

        trigger_node_id = listener_data["trigger_node_id"]
        nodes = listener_data["nodes"]
        edges = listener_data["edges"]
        session_id = listener_data.get("session_id", "default")
        deployment_workflow_id = listener_data.get("workflow_id")
        # Wave 14: human-readable id components — slug + trigger label
        # set at deploy time. Both fall back to their UUID/id counterparts
        # so older listener_data payloads still produce a valid id.
        workflow_slug = listener_data.get("workflow_slug") or deployment_workflow_id
        trigger_label = listener_data.get("trigger_label") or trigger_node_id
        tenant_id = listener_data.get("tenant_id")

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id=trigger_node_id,
            trigger_output=trigger_output,
            nodes=nodes,
            edges=edges,
        )

        # Schedule fires multiple times; the firing-time component in
        # the child workflow id makes each tick unique.
        # WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY guards
        # against a duplicate at the same instant (Temporal retry of
        # the schedule action) double-spawning a successful run.
        # Format: ``<slug>-<trigger_label>-<firing_iso>`` — same
        # convention as push / poll triggers, with firing time as the
        # per-tick uniqueness suffix.
        firing_iso = trigger_output["timestamp"]
        child_id = f"{workflow_slug}-{trigger_label}-{firing_iso}"

        from services.temporal.trigger_listener_workflow import (
            event_workflow_search_attributes,
        )
        # A spawned run may execute — or stay cooperatively paused — for
        # months, and Temporal's timeout timer keeps ticking through a
        # pause, so children start without lifetime caps. Liveness is
        # enforced at the activity layer via heartbeats.
        child_options = {
            "id": child_id,
            "parent_close_policy": ParentClosePolicy.ABANDON,
            "id_reuse_policy": (
                WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
            ),
            "search_attributes": event_workflow_search_attributes(
                deployment_workflow_id
            ),
        }

        await self._wait_until_resumed()
        if self._execution_control.enabled:
            await self._execution_control.begin_child_start()
        try:
            await self._start_graph(listener_data, child_options, filtered_nodes, filtered_edges,
                                    session_id, deployment_workflow_id, workflow_slug, tenant_id)
        finally:
            if self._execution_control.enabled:
                self._execution_control.end_child_start()

        workflow.logger.info(f"CronTriggerWorkflow spawned child run: child_id={child_id} " f"timestamp={firing_iso}")
        return {"spawned_child_id": child_id, "timestamp": firing_iso}

    async def _start_graph(self, listener_data, child_options, filtered_nodes, filtered_edges,
                           session_id, deployment_workflow_id, workflow_slug, tenant_id):
        from services.temporal.workflow import TEMPORAL_ROUTING_INPUT_KEY
        await workflow.start_child_workflow(
            "MachinaWorkflow",
            args=[
                {
                    "nodes": filtered_nodes,
                    "edges": filtered_edges,
                    "session_id": session_id,
                    "workflow_id": deployment_workflow_id,
                    "workflow_slug": workflow_slug,
                    "tenant_id": tenant_id,
                    # Forward the deployer's identity and the frozen routing
                    # snapshot. Omitting them made every cron firing run as
                    # the anonymous owner AND fall back to the safe routing
                    # default, silently bypassing per-queue rate limits.
                    **{
                        key: listener_data[key]
                        for key in ("user_id", "graphVersion", "generation", "execution_id", "root_execution_id",
                                    "data_scope_id", "parameter_snapshot", "execution_control_version", "controller_workflow_id")
                        if key in listener_data
                    },
                    **(
                        {
                            TEMPORAL_ROUTING_INPUT_KEY: listener_data[
                                TEMPORAL_ROUTING_INPUT_KEY
                            ]
                        }
                        if TEMPORAL_ROUTING_INPUT_KEY in listener_data
                        else {}
                    ),
                }
            ],
            **child_options,
        )

def _build_trigger_output(listener_data: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the cron trigger's output payload for one firing.

    Shape matches the pre-Wave-12 APScheduler tick callback in
    ``DeploymentManager._setup_cron_trigger.on_tick`` so downstream
    nodes that read ``{{cronTrigger.timestamp}}`` etc. keep working.

    **Iteration counter trade-off**: APScheduler kept an in-memory
    ``self._cron_iterations[node_id]`` that incremented per firing.
    Temporal Schedules don't expose a built-in firing counter and
    persisting one would require either a long-lived workflow (we'd
    lose the one-shot simplicity) or DB writes on the hot path.
    The canary intentionally sets ``iteration`` to ``None`` —
    downstream nodes that need monotonic iteration can switch to the
    firing ``timestamp`` (deterministic per run, sortable).
    """
    return {
        "timestamp": workflow.now().isoformat(),
        "iteration": None,
        "frequency": listener_data.get("frequency"),
        "timezone": listener_data.get("timezone"),
        "schedule": listener_data.get("schedule"),
        "cron_expression": listener_data.get("cron_expression"),
    }


__all__ = [
    "CronTriggerWorkflow",
]
