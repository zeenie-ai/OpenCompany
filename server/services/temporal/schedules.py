"""Wave 12 C3: Temporal Schedule helpers for cron triggers.

Thin framework-level wrappers around the Temporal Schedule API. Plugin
ownership stays in the plugin folder — this module's only job is the
**mechanics** of creating + cancelling Schedules. The plugin's workflow
class (``CronTriggerWorkflow``) and its specific action args are
plugin-owned.

Same shape as the Visibility-based cancel sweep in
:meth:`services.deployment.manager.DeploymentManager._cancel_canary_listeners`
— deterministic id, no in-memory tracking, server-side state is the
registry. ``Schedule`` resources live in their OWN Visibility list
(``client.list_schedules``), distinct from workflow Visibility.

Refs:
  - https://docs.temporal.io/develop/python/schedules
  - https://python.temporal.io/temporalio.client.Schedule.html
  - https://docs.temporal.io/encyclopedia/scheduled-execution
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,  # noqa: F401 — re-exported for any caller that needs it
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)

from core.logging import get_logger

logger = get_logger(__name__)


# Schedule ID prefix used by both create + cancel-sweep. Mirrors the
# ``trigger-listener-{wf}-{node}`` deterministic-id shape that the C1
# canary uses for its listener workflows — same locality + uniqueness
# properties.
def cron_schedule_id(deployment_workflow_id: str, node_id: str) -> str:
    """Deterministic Schedule ID for a (deployment, cron-node) pair.

    Mapped to the business entity so re-deploying the same MachinaOs
    workflow targets the same Schedule. Pairs with the create call's
    ``"already exists" → no-op`` semantics (per
    :exc:`temporalio.client.ScheduleAlreadyRunningError`).
    """
    return f"cron-schedule-{deployment_workflow_id}-{node_id}"


# Child workflow ID prefix Temporal stamps onto each firing. The
# ``{{.ScheduledStartTime}}`` placeholder is substituted by Temporal
# server-side, so each tick produces a unique workflow_id without us
# needing per-tick code (per
# https://docs.temporal.io/develop/python/schedules#use-a-pre-generated-workflow-id).
def cron_action_workflow_id(deployment_workflow_id: str, node_id: str) -> str:
    return f"cron-fire-{deployment_workflow_id}-{node_id}-{{{{.ScheduledStartTime}}}}"


async def create_cron_schedule(
    client: Client,
    *,
    deployment_workflow_id: str,
    node_id: str,
    cron_expression: str,
    timezone: str,
    listener_data: Dict[str, Any],
    task_queue: str = "machina-tasks",
    overlap_policy: ScheduleOverlapPolicy = ScheduleOverlapPolicy.SKIP,
) -> str:
    """Create-or-reuse a Temporal Schedule for a cron trigger.

    Returns the Schedule's id. Idempotent: a re-deploy with the same
    ``(deployment_workflow_id, node_id)`` pair reuses the existing
    Schedule (Temporal raises
    :exc:`ScheduleAlreadyRunningError` which we swallow as a no-op).

    Args:
        client: Connected Temporal client.
        deployment_workflow_id: MachinaOs deployment workflow_id.
        node_id: Cron-trigger node id.
        cron_expression: Crontab string (5 or 6 field).
        timezone: IANA tz name (e.g. ``"America/New_York"``).
        listener_data: Frozen action args for the workflow run
            (deployment graph snapshot + cron metadata).
        task_queue: Worker task queue that hosts ``CronTriggerWorkflow``.
        overlap_policy: How concurrent firings interact. ``SKIP`` (default)
            drops a firing if the prior run is still going; mirrors the
            pre-Wave-12 APScheduler behaviour for slow workflows.
    """
    schedule_id = cron_schedule_id(deployment_workflow_id, node_id)
    action_workflow_id = cron_action_workflow_id(deployment_workflow_id, node_id)

    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "CronTriggerWorkflow",
            args=[listener_data],
            id=action_workflow_id,
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(
            cron_expressions=[cron_expression],
            time_zone_name=timezone or "UTC",
        ),
        policy=SchedulePolicy(overlap=overlap_policy),
    )

    # Search Attributes mirror the listener-canary contract so the
    # cancel sweep can find the Schedule via the same EventWorkflowId
    # filter. EventTriggerKind="cron" lets ops dashboards filter by
    # canary kind independently of the underlying primitive. Note:
    # ``search_attributes`` is a ``client.create_schedule`` kwarg, not
    # a field on the ``Schedule`` dataclass itself (per the Temporal
    # SDK API).
    schedule_search_attributes = TypedSearchAttributes([
        SearchAttributePair(
            SearchAttributeKey.for_keyword("EventWorkflowId"),
            deployment_workflow_id,
        ),
        SearchAttributePair(
            SearchAttributeKey.for_keyword("TriggerNodeId"),
            node_id,
        ),
        SearchAttributePair(
            SearchAttributeKey.for_keyword("EventTriggerKind"),
            "cron",
        ),
    ])

    try:
        await client.create_schedule(
            schedule_id,
            schedule,
            search_attributes=schedule_search_attributes,
        )
        logger.info(
            "Created Temporal cron Schedule",
            schedule_id=schedule_id,
            cron_expression=cron_expression,
            timezone=timezone,
        )
    except ScheduleAlreadyRunningError:
        # Idempotent re-deploy. The pre-existing Schedule keeps its
        # state; the caller's create-or-reuse contract is satisfied.
        logger.info(
            "Temporal cron Schedule already exists (re-deploy idempotency)",
            schedule_id=schedule_id,
        )

    return schedule_id


async def delete_cron_schedules_for_deployment(
    client: Client,
    deployment_workflow_id: str,
) -> int:
    """Delete every cron Schedule for ``deployment_workflow_id``.

    Visibility-equivalent sweep: ``client.list_schedules(query=...)``
    is the registry, no local handle dict. Per-Schedule failures don't
    block the sweep (mirror of
    :meth:`DeploymentManager._cancel_canary_listeners` semantics).

    Returns count of Schedules deleted.
    """
    query = (
        f"EventWorkflowId='{deployment_workflow_id}' "
        f"AND EventTriggerKind='cron'"
    )

    deleted = 0
    try:
        # ``Client.list_schedules`` is ``async def`` (returns a coroutine
        # that resolves to ``ScheduleAsyncIterator``), unlike
        # ``Client.list_workflows`` which is a plain function returning
        # the iterator directly. Calling ``async for`` on the coroutine
        # raises "'async for' requires an object with __aiter__".
        # Reference:
        #   https://python.temporal.io/temporalio.client.Client.html#list_schedules
        iterator = await client.list_schedules(query=query)
        async for desc in iterator:
            sched_id = desc.id
            try:
                handle = client.get_schedule_handle(sched_id)
                await handle.delete()
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Failed to delete cron Schedule {sched_id!r}: {exc}",
                    deployment_workflow_id=deployment_workflow_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Visibility query for cron Schedules failed: {exc} "
            f"(query={query!r})",
            deployment_workflow_id=deployment_workflow_id,
        )

    if deleted:
        logger.info(
            "Cron Schedules deleted",
            deployment_workflow_id=deployment_workflow_id,
            count=deleted,
        )
    return deleted


__all__ = [
    "cron_schedule_id",
    "create_cron_schedule",
    "delete_cron_schedules_for_deployment",
]
