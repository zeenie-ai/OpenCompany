"""Wave 11.F: per-plugin Temporal activities.

Walks :func:`services.node_registry.registered_node_classes` and exposes
each plugin's ``cls.as_activity()`` as a Temporal activity. Workers can
register these alongside (or instead of) the single
``NodeExecutionActivities.execute_node_activity`` to get per-node
scaling knobs:

- ``cls.task_queue`` routes activities to specialised worker pools
  (``rest-api`` / ``ai-heavy`` / ``code-exec`` / ``triggers-poll`` /
  ``triggers-event`` / ``android`` / ``browser`` / ``messaging`` /
  ``machina-default``).
- ``cls.retry_policy`` per-node Temporal retry knobs.
- ``cls.start_to_close_timeout`` + ``cls.heartbeat_timeout`` per-node.

Activity name convention: ``node.{type}.v{version}``.

Wire-up:
    from services.temporal.plugin_activities import collect_plugin_activities
    activities = collect_plugin_activities()
    worker = Worker(client, task_queue=..., activities=activities, ...)

:func:`collect_plugin_activities_for_queue` returns only the activities
whose plugin declares a matching ``task_queue`` — use this when running
a specialised worker pool.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


def collect_plugin_activities(
    *,
    task_queue: Optional[str] = None,
    include_types: Optional[Iterable[str]] = None,
) -> List[Callable]:
    """Return ``@activity.defn``-decorated callables for every plugin
    class registered via :func:`register_node_class`.

    Args:
        task_queue: if given, only include activities whose plugin's
            ``cls.task_queue`` matches this value. Use this to build
            specialised worker pools.
        include_types: optional whitelist of node-type strings. Useful
            for tests and incremental rollout.

    Returns:
        A list of Temporal activity callables ready to pass to
        ``Worker(activities=[...])``.
    """
    # Lazy import so this module can be imported before nodes have been
    # discovered (e.g. during worker bootstrap).
    from services.node_registry import registered_node_classes

    activities: List[Callable] = []
    classes = registered_node_classes()
    for node_type, cls in classes.items():
        if include_types is not None and node_type not in include_types:
            continue
        cls_queue = getattr(cls, "task_queue", None)
        if task_queue is not None and cls_queue != task_queue:
            continue
        try:
            activity_fn = cls.as_activity()
        except Exception as e:
            logger.warning(
                "Failed to build activity for %s: %s",
                node_type,
                e,
            )
            continue
        activities.append(activity_fn)

    logger.info(
        "Collected %d plugin activities%s",
        len(activities),
        f" for queue={task_queue!r}" if task_queue else "",
    )
    return activities


def collect_plugin_activities_for_queue(task_queue: str) -> List[Callable]:
    """Convenience wrapper for the single-queue case."""
    return collect_plugin_activities(task_queue=task_queue)


def collect_polling_activities() -> List[Callable]:
    """Return ``@activity.defn``-decorated per-cycle activities for every
    :class:`services.plugin.PollingTriggerNode` subclass.

    Wave 12 C2: each polling plugin's
    :meth:`PollingTriggerNode.as_poll_activity` produces an activity that
    does ONE poll cycle (vs ``cls.as_activity()`` which does ONE workflow
    execution). The new :class:`PollingTriggerWorkflow` calls these per
    ``workflow.sleep`` tick.

    Stable activity name: ``poll.{type}.v{version}``. Registered alongside
    the regular per-type activities in the Temporal worker.
    """
    from services.node_registry import registered_node_classes
    from services.plugin import PollingTriggerNode

    activities: List[Callable] = []
    for node_type, cls in registered_node_classes().items():
        if not isinstance(cls, type) or not issubclass(cls, PollingTriggerNode):
            continue
        try:
            activities.append(cls.as_poll_activity())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to build poll activity for %s: %s",
                node_type,
                exc,
            )

    logger.info("Collected %d polling activities", len(activities))
    return activities


def distinct_task_queues() -> List[str]:
    """Return every task_queue string declared by any registered plugin.

    Lets worker-pool orchestration discover which queues need workers
    without hardcoding the list.
    """
    from services.node_registry import registered_node_classes

    seen: Dict[str, int] = {}
    for cls in registered_node_classes().values():
        q = getattr(cls, "task_queue", None)
        if q:
            seen[q] = seen.get(q, 0) + 1
    queues = sorted(seen.keys())
    logger.info(
        "Plugins declare %d distinct task queues: %s",
        len(queues),
        {q: seen[q] for q in queues},
    )
    return queues
