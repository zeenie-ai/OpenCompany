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

from datetime import timedelta
from typing import Any, Dict

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy


@workflow.defn(name="CronTriggerWorkflow", sandboxed=False)
class CronTriggerWorkflow:
    """One-shot per-firing workflow that spawns a child MachinaWorkflow.

    Determinism note: the only mutable state is the firing-time
    timestamp from ``workflow.now()`` (deterministic per run);
    everything else is computed from the static action args.
    """

    @workflow.run
    async def run(self, listener_data: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn one MachinaWorkflow child per cron firing.

        ``listener_data`` shape (deployment-supplied, frozen at
        schedule creation)::

            {
                "workflow_id": str,        # MachinaOs deployment workflow_id
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
        firing_iso = trigger_output["timestamp"]
        child_id = f"cron-{deployment_workflow_id}-{trigger_node_id}-{firing_iso}"

        await workflow.start_child_workflow(
            "MachinaWorkflow",
            args=[
                {
                    "nodes": filtered_nodes,
                    "edges": filtered_edges,
                    "session_id": session_id,
                    "workflow_id": deployment_workflow_id,
                    "tenant_id": tenant_id,
                }
            ],
            id=child_id,
            parent_close_policy=ParentClosePolicy.ABANDON,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            execution_timeout=timedelta(hours=1),
            run_timeout=timedelta(hours=1),
        )

        workflow.logger.info(f"CronTriggerWorkflow spawned child run: child_id={child_id} " f"timestamp={firing_iso}")

        return {
            "spawned_child_id": child_id,
            "timestamp": firing_iso,
        }


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


__all__ = ["CronTriggerWorkflow"]
