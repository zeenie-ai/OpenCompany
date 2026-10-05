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

from dataclasses import replace
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,  # noqa: F401 — re-exported for any caller that needs it
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)

from core.logging import get_logger

logger = get_logger(__name__)


# Schedule + child-workflow IDs follow the ``<slug>-<trigger_label>``
# shape — same convention as the push listener id in
# DeploymentManager._listener_workflow_id. The label conveys the kind
# (``cronScheduler`` / F2-renamed), so no middle tag.
def cron_schedule_id(workflow_slug: str, trigger_label: str) -> str:
    """Deterministic Schedule ID for a (workflow_slug, cron-node label) pair.

    Re-deploying the same OpenCompany workflow (slug + label unchanged)
    targets the same Schedule, which :func:`create_cron_schedule` then
    updates in place.
    """
    return f"{workflow_slug}-{trigger_label}"


# Child workflow ID Temporal stamps onto each firing. The
# ``{{.ScheduledStartTime}}`` placeholder is substituted by Temporal
# server-side, so each tick produces a unique workflow_id without us
# needing per-tick code (per
# https://docs.temporal.io/develop/python/schedules#use-a-pre-generated-workflow-id).
def cron_action_workflow_id(workflow_slug: str, trigger_label: str) -> str:
    return f"{workflow_slug}-{trigger_label}-{{{{.ScheduledStartTime}}}}"


#: ``TriggerManager.build_cron_expression``'s value for the ``once``
#: frequency. Temporal has no such shorthand: the Schedule gets no times and
#: runs once, when it is created (``trigger_immediately``). Re-creating it on
#: a restart updates it in place, so it does not run again.
CRON_ONCE = "@once"

#: Temporal rejects calendar years outside 2000-2100, so 2100 is the last
#: year ``_last_day_of_month`` can name.
_LAST_YEAR = 2100


def cron_schedule_spec(cron_expression: str, timezone: str) -> ScheduleSpec:
    """The ScheduleSpec for a string from ``TriggerManager.build_cron_expression``.

    Temporal parses the string itself, except for two values it has no
    syntax for: ``CRON_ONCE`` gets no times at all, and ``L`` in the
    day-of-month field (the last day of the month) gets the calendars of
    ``_last_day_of_month``.
    """
    zone = timezone or "UTC"
    if cron_expression == CRON_ONCE:
        return ScheduleSpec(time_zone_name=zone)
    fields = cron_expression.split()
    if len(fields) == 5 and fields[2] == "L" and fields[3:] == ["*", "*"]:
        return ScheduleSpec(calendars=_last_day_of_month(int(fields[0]), int(fields[1])), time_zone_name=zone)
    return ScheduleSpec(cron_expressions=[cron_expression], time_zone_name=zone)


def _last_day_of_month(minute: int, hour: int) -> List[ScheduleCalendarSpec]:
    """Calendars for the last day of every month at ``hour:minute``.

    A Temporal calendar has no "last day", so this takes four, and Temporal
    fires on any of them: the 31st of the 31-day months, the 30th of the
    30-day months, 29 February (a date only leap years have) and 28 February
    in the other years. Those are the years not divisible by 4, plus 2100.
    Temporal's calendar years end at ``_LAST_YEAR``, so after 2100 February
    has no last-day run.
    """

    def on(day: int, months: Tuple[int, ...], years: Tuple[ScheduleRange, ...] = ()) -> ScheduleCalendarSpec:
        return ScheduleCalendarSpec(
            minute=(ScheduleRange(minute),),
            hour=(ScheduleRange(hour),),
            day_of_month=(ScheduleRange(day),),
            month=tuple(ScheduleRange(month) for month in months),
            year=years,
        )

    non_leap_years = tuple(ScheduleRange(first, _LAST_YEAR, 4) for first in (2025, 2026, 2027)) + (ScheduleRange(2100),)
    return [
        on(31, (1, 3, 5, 7, 8, 10, 12)),
        on(30, (4, 6, 9, 11)),
        on(29, (2,)),
        on(28, (2,), non_leap_years),
    ]


class _OwnedElsewhere(RuntimeError):
    """The Schedule id is held by another workflow's Schedule."""

    def __init__(self, schedule_id: str, owner: str) -> None:
        super().__init__(f"cron_schedule_ownership_conflict:{schedule_id}")
        self.owner = owner


async def create_cron_schedule(
    client: Client,
    *,
    workflow_id: str,
    workflow_slug: str,
    node_id: str,
    trigger_label: str,
    cron_expression: str,
    timezone: str,
    listener_data: Dict[str, Any],
    task_queue: str = "machina-tasks",
    overlap_policy: ScheduleOverlapPolicy = ScheduleOverlapPolicy.SKIP,
    owner_gone: Optional[Callable[[str], Awaitable[bool]]] = None,
) -> str:
    """Create a Temporal Schedule for a cron trigger, or update the one there.

    Returns the Schedule's id, which comes from ``(workflow_slug,
    trigger_label)``. When that Schedule already exists (a re-deploy, or the
    boot re-arm), Temporal raises :exc:`ScheduleAlreadyRunningError` and the
    Schedule is updated in place: new spec, action args and Search
    Attributes, same paused state. One owned by another workflow raises
    ``cron_schedule_ownership_conflict`` instead, unless ``owner_gone`` says
    that workflow was deleted: its Schedule is then left over (a workflow
    deleted before deleting stopped it first), and since slugs are handed
    out again, it is replaced. An updated ``CRON_ONCE``
    Schedule does not run again, since ``trigger_immediately`` applies only
    when the Schedule is created.

    Args:
        client: Connected Temporal client.
        workflow_id: Stable UUID for ``EventWorkflowId`` Search Attribute
            (used by the cancel sweep + Visibility queries — must
            survive workflow rename).
        workflow_slug: Human-readable slug for the Schedule + child
            workflow IDs visible in Temporal Web UI.
        node_id: Canvas node id for the ``TriggerNodeId`` Search
            Attribute (used by admin queries — stable, not label).
        trigger_label: Cron-trigger node's label (``cronScheduler`` by
            default, or F2-renamed). Forms the suffix of the Schedule
            id and child workflow ids.
        cron_expression: Cron string with 5 fields, or 7 with the second
            first and the year last (Temporal reads 6 as minute through year).
            ``CRON_ONCE`` and ``L`` in the day-of-month field are translated
            by ``cron_schedule_spec``.
        timezone: IANA tz name (e.g. ``"America/New_York"``).
        listener_data: Frozen action args for the workflow run
            (deployment graph snapshot + cron metadata).
        task_queue: Worker task queue that hosts ``CronTriggerWorkflow``.
        overlap_policy: How concurrent firings interact. ``SKIP`` (default)
            drops a firing if the prior run is still going; mirrors the
            pre-Wave-12 APScheduler behaviour for slow workflows.
        owner_gone: Whether the workflow an existing Schedule names was
            deleted (the deployment manager asks its database).
    """
    schedule_id = cron_schedule_id(workflow_slug, trigger_label)
    action_workflow_id = cron_action_workflow_id(workflow_slug, trigger_label)
    execution_search_attributes = TypedSearchAttributes(
        [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("EventWorkflowId"),
                workflow_id,
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("TriggerNodeId"),
                node_id,
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("EventTriggerKind"),
                "cron",
            ),
        ]
    )

    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "CronTriggerWorkflow",
            args=[listener_data],
            id=action_workflow_id,
            task_queue=task_queue,
            typed_search_attributes=execution_search_attributes,
        ),
        spec=cron_schedule_spec(cron_expression, timezone),
        # Wave 17.1: bound the catch-up burst after downtime. A laptop
        # asleep past firings gets at most 24h of make-up ticks on wake
        # (the SKIP overlap policy then collapses that burst to a single
        # firing since each make-up run overlaps the previous one). A
        # host offline for a week does NOT replay 168 hourly ticks.
        policy=SchedulePolicy(
            overlap=overlap_policy,
            catchup_window=timedelta(hours=24),
        ),
    )

    # Search Attributes mirror the listener-canary contract so the
    # cancel sweep can find the Schedule via the same EventWorkflowId
    # filter. EventTriggerKind="cron" lets ops dashboards filter by
    # canary kind independently of the underlying primitive. Note:
    # ``search_attributes`` is a ``client.create_schedule`` kwarg, not
    # a field on the ``Schedule`` dataclass itself (per the Temporal
    # SDK API).
    schedule_search_attributes = execution_search_attributes

    try:
        await client.create_schedule(
            schedule_id,
            schedule,
            search_attributes=schedule_search_attributes,
            trigger_immediately=cron_expression == CRON_ONCE,
        )
        logger.info(
            "Created Temporal cron Schedule",
            schedule_id=schedule_id,
            cron_expression=cron_expression,
            timezone=timezone,
        )
    except ScheduleAlreadyRunningError:
        # Idempotent re-deploy must still refresh the frozen action args and
        # execution Search Attributes. Otherwise a Schedule created before
        # the lifecycle-control release would keep spawning untagged roots
        # that Reset cannot discover. Preserve the server-owned paused state
        # while replacing the deployment snapshot and metadata.
        handle = client.get_schedule_handle(schedule_id)

        def update_existing(update_input):
            description = update_input.description
            existing_attributes = {
                pair.key.name: pair.value
                for pair in description.typed_search_attributes
            }
            existing_owner = existing_attributes.get("EventWorkflowId")
            if existing_owner is None:
                # Compatibility with Schedules created before execution
                # Search Attributes were copied onto the action. Only claim a
                # legacy id when its frozen payload proves the same owner.
                existing_args = getattr(
                    description.schedule.action,
                    "args",
                    (),
                )
                if (
                    existing_args
                    and isinstance(existing_args[0], dict)
                ):
                    existing_owner = existing_args[0].get("workflow_id")
            if str(existing_owner or "") != str(workflow_id):
                raise _OwnedElsewhere(schedule_id, str(existing_owner or ""))
            updated_schedule = replace(
                schedule,
                state=description.schedule.state,
            )
            return ScheduleUpdate(
                schedule=updated_schedule,
                search_attributes=schedule_search_attributes,
            )

        try:
            await handle.update(update_existing)
        except _OwnedElsewhere as taken:
            if owner_gone is None or not taken.owner or not await owner_gone(taken.owner):
                raise
            await handle.delete()
            await client.create_schedule(
                schedule_id,
                schedule,
                search_attributes=schedule_search_attributes,
                trigger_immediately=cron_expression == CRON_ONCE,
            )
            logger.warning(
                "Replaced a cron Schedule left by a deleted workflow",
                schedule_id=schedule_id,
                previous_owner=taken.owner,
                workflow_id=workflow_id,
            )
            return schedule_id
        logger.info(
            "Updated existing Temporal cron Schedule",
            schedule_id=schedule_id,
        )

    return schedule_id


async def delete_cron_schedules_for_deployment(
    client: Client,
    deployment_workflow_id: str,
    *,
    strict: bool = False,
) -> int:
    """Delete every cron Schedule for ``deployment_workflow_id``.

    Visibility-equivalent sweep: ``client.list_schedules(query=...)``
    is the registry, no local handle dict. Per-Schedule failures don't
    block the sweep. With ``strict=True``, the sweep still visits every
    target and then raises if any deletion failed, so Reset cannot publish
    completion while a durable Schedule survives.

    Returns count of Schedules deleted.
    """
    query = f"EventWorkflowId='{deployment_workflow_id}' " f"AND EventTriggerKind='cron'"

    deleted = 0
    failures: list[Exception] = []
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
                failures.append(exc)
                logger.warning(
                    f"Failed to delete cron Schedule {sched_id!r}: {exc}",
                    deployment_workflow_id=deployment_workflow_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Visibility query for cron Schedules failed: {exc} " f"(query={query!r})",
            deployment_workflow_id=deployment_workflow_id,
        )
        if strict:
            raise RuntimeError("cron_schedule_visibility_cleanup_failed") from exc

    if strict and failures:
        raise RuntimeError(
            f"cron_schedule_cleanup_failed:{len(failures)}"
        ) from failures[0]

    if deleted:
        logger.info(
            "Cron Schedules deleted",
            deployment_workflow_id=deployment_workflow_id,
            count=deleted,
        )
    return deleted


async def set_cron_schedules_paused(
    client: Client,
    deployment_workflow_id: str,
    *,
    paused: bool,
    strict: bool = False,
) -> int:
    """Pause or unpause every cron Schedule owned by one deployment.

    Strict lifecycle transitions still sweep every matching Schedule, then
    fail if visibility or any individual mutation failed. This prevents the
    database from publishing a stable Pause/Resume state while a producer is
    known to be in the opposite state.
    """
    query = f"EventWorkflowId='{deployment_workflow_id}' AND EventTriggerKind='cron'"
    changed = 0
    failures: list[Exception] = []
    try:
        iterator = await client.list_schedules(query=query)
        async for desc in iterator:
            try:
                handle = client.get_schedule_handle(desc.id)
                if paused:
                    await handle.pause(note="OpenCompany workflow paused")
                else:
                    await handle.unpause(note="OpenCompany workflow resumed")
                changed += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(exc)
                logger.warning(
                    f"Failed to {'pause' if paused else 'resume'} cron Schedule {desc.id!r}: {exc}",
                    deployment_workflow_id=deployment_workflow_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Cron Schedule pause sweep failed: {exc}",
            deployment_workflow_id=deployment_workflow_id,
        )
        if strict:
            raise RuntimeError(
                "cron_schedule_pause_visibility_failed"
            ) from exc
    if strict and failures:
        raise RuntimeError(
            f"cron_schedule_pause_failed:{len(failures)}"
        ) from failures[0]
    return changed


__all__ = [
    "CRON_ONCE",
    "cron_schedule_id",
    "cron_schedule_spec",
    "create_cron_schedule",
    "delete_cron_schedules_for_deployment",
    "set_cron_schedules_paused",
]
