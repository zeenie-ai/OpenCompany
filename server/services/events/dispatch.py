"""Wave 12 A6: Temporal-native event dispatch.

One public function — :func:`emit` — takes a :class:`WorkflowEvent` and
routes it to:

1. **Running consumer workflows** via Temporal Visibility query +
   ``Client.signal_workflow``. Workflows tag themselves with the
   ``EventType`` Search Attribute when they're started; this dispatch
   finds them via ``ListWorkflows(query="EventType='X' AND
   ExecutionStatus='Running'")`` and signals each.

2. **In-process FastAPI WebSocket clients** via direct
   ``status_broadcaster.broadcast()`` call. Worker + WS pool share
   memory + event loop, so no IPC hop is needed (audit confirmed —
   ``main.py:211-292``).

No EventDispatchWorkflow, no Redis Streams, no DLQ table. Temporal's
Visibility + Signal API + Event History provide the durability primitives
this function depends on; everything else is a thin pass-through.

Behind ``Settings.event_framework_enabled`` (default off in Phase A).
When disabled, :func:`emit` is a no-op pass-through that returns the
envelope unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.config import Settings
from core.logging import get_logger
from services.events.envelope import WorkflowEvent, equivalent_event_types

logger = get_logger(__name__)


# Wire-routing key the FastAPI WebSocket layer switches on. Plugin emits
# its own wire key today (``credential_catalogue_updated`` /
# ``agent_progress`` / ``plugin_connection_status`` / etc.). This
# default is the generic CloudEvents-envelope channel used by events
# that don't carry a plugin-specific wire key.
_DEFAULT_WIRE_ROUTING_KEY = "cloudevent"

# Visibility query template — produces a SQL-like List Filter string.
# ``ExecutionStatus`` enum value 'Running' matches Temporal's status
# encoding; see https://docs.temporal.io/list-filter.
_RUNNING_CONSUMERS_QUERY = "EventType='{event_type}' AND ExecutionStatus='Running'"
_RUNNING_COMPAT_CONSUMERS_QUERY = "({event_type_clauses}) AND ExecutionStatus='Running'"
_RUNNING_CONTROLLERS_QUERY = "WorkflowType='WorkflowControlWorkflow' AND ExecutionStatus='Running'"

# Signal handler name on consumer workflows. Plain identifier rather
# than CloudEvents type because Temporal Signal names are Python
# identifiers (cannot contain dots).
_SIGNAL_NAME = "on_event"


async def emit(
    event: WorkflowEvent,
    *,
    wire_routing_key: Optional[str] = None,
) -> WorkflowEvent:
    """Route ``event`` to running consumer workflows + in-process WS clients.

    Args:
        event: The :class:`WorkflowEvent` to dispatch. CloudEvents
            envelope with a populated ``type`` (the Visibility filter
            keys off this).
        wire_routing_key: Outer WebSocket routing key (the ``type``
            field on the WS frame). Defaults to the generic
            ``cloudevent`` channel; plugin emitters override per their
            existing wire key (e.g. ``"telegram_message_received"``).

    Returns:
        The envelope unchanged — callers may chain.

    Behaviour when ``Settings.event_framework_enabled=False`` (Phase A
    default): pass-through no-op. Logged at DEBUG so opt-in dogfooding
    is observable without flipping the flag globally.
    """
    if not Settings().event_framework_enabled:
        logger.debug(
            f"event-framework disabled — emit no-op for event.id={event.id} " f"type={event.type}",
        )
        return event

    # Concurrent fan-out: Visibility query + Signal each consumer, AND
    # broadcast to in-process WS clients. asyncio.gather lets the
    # broadcast happen even if the Temporal Visibility query fails (and
    # vice versa).
    await asyncio.gather(
        _signal_running_consumers(event),
        _broadcast_in_process(event, wire_routing_key or _DEFAULT_WIRE_ROUTING_KEY),
        return_exceptions=False,
    )
    return event


async def _signal_running_consumers(event: WorkflowEvent) -> None:
    """Find running workflows tagged with ``EventType=event.type`` and
    signal each with ``on_event``.

    Fail-soft: if the Temporal client is unavailable or the Visibility
    query errors, log a warning and continue. The in-process broadcast
    path is independent and still delivers to WS clients.
    """
    try:
        from core.container import container

        wrapper = container.temporal_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"emit: container.temporal_client unavailable: {exc}")
        return

    if wrapper is None or wrapper.client is None:
        logger.debug(
            f"emit: Temporal not connected — skipping consumer fan-out for " f"event.id={event.id}",
        )
        return

    client = wrapper.client
    event_types = equivalent_event_types(event.type)
    if len(event_types) == 1:
        query = _RUNNING_CONSUMERS_QUERY.format(event_type=event_types[0])
    else:
        clauses = " OR ".join(f"EventType='{event_type}'" for event_type in event_types)
        query = _RUNNING_COMPAT_CONSUMERS_QUERY.format(event_type_clauses=clauses)

    try:
        # Controlled deployments keep trigger definitions in their controller
        # history. Controllers filter the event against those definitions, so
        # no TriggerListenerWorkflow/ PollingTriggerWorkflow execution exists.
        query = f"({query}) OR ({_RUNNING_CONTROLLERS_QUERY})"
        consumers = []
        async for wf in client.list_workflows(query=query):
            consumers.append(wf)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"emit: Visibility query failed (query={query!r}): {exc}")
        return

    if not consumers:
        logger.debug(
            f"emit: 0 consumers for event.type={event.type!r} " f"(event.id={event.id})",
        )
        return

    # Signal each consumer concurrently. Per-target failures don't
    # block other consumers (return_exceptions=True surfaces failures
    # as exceptions in the result list).
    signal_results = await asyncio.gather(
        *[_signal_one(client, wf.id, event) for wf in consumers],
        return_exceptions=True,
    )

    delivered = sum(1 for r in signal_results if not isinstance(r, Exception))
    failed = len(signal_results) - delivered
    logger.info(
        f"emit: signalled {delivered}/{len(consumers)} consumers for " f"event.type={event.type!r} (failed={failed}, event.id={event.id})",
    )


async def _signal_one(client, workflow_id: str, event: WorkflowEvent) -> None:
    """Send the ``on_event`` signal to one workflow.

    Kept as its own coroutine so :func:`_signal_running_consumers` can
    ``asyncio.gather`` independently per-consumer.
    """
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(_SIGNAL_NAME, event.model_dump(mode="json"))


async def _broadcast_in_process(event: WorkflowEvent, wire_routing_key: str) -> None:
    """Direct in-process WS fan-out via the status broadcaster.

    Same asyncio event loop as the FastAPI handlers — see ``main.py:
    211-292`` (TemporalWorkerManager starts as ``asyncio.create_task``).
    Activity → broadcaster is a direct method call against in-memory
    ``Set[WebSocket]``; no IPC.
    """
    try:
        from services.status_broadcaster import get_status_broadcaster

        broadcaster = get_status_broadcaster()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"emit: status_broadcaster unavailable: {exc}")
        return

    try:
        await broadcaster.broadcast(
            {
                "type": wire_routing_key,
                "data": event.model_dump(mode="json"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"emit: WS broadcast failed (wire_key={wire_routing_key!r}): {exc}")


__all__ = ["emit"]
