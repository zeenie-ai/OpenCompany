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
   memory + event loop, so no IPC hop is needed (the ``main.py``
   lifespan schedules ``services.temporal.lifecycle.run_temporal_lifecycle``
   as an in-process task, and that task starts the workers).

No EventDispatchWorkflow, no Redis Streams, no DLQ table. Temporal's
Visibility + Signal API + Event History provide the durability primitives
this function depends on; everything else is a thin pass-through.

Behind ``Settings.event_framework_enabled`` (default on since Wave 12).
When disabled, :func:`emit` is a no-op pass-through that returns the
envelope unchanged. That is no longer a working rollback: most canary
producers call only :func:`emit` (no ``event_waiter.dispatch``), so with
the flag off their deployed triggers never fire and the WS broadcast
below stops too. See docs-internal/event_framework.md, "Rollback (known
gap)".
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
    broadcast: bool,
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
        broadcast: Whether the envelope also goes to every connected
            socket. Required, so every producer decides: an event whose
            payload is someone's content (a chat or channel message, a
            webhook body) passes ``False``; its consumers are the workflows
            signalled here. ``True`` only for a frame a client reads.

    Returns:
        The envelope unchanged — callers may chain.

    Behaviour when ``Settings.event_framework_enabled=False`` (the
    default is True): pass-through no-op. Logged at DEBUG so a process
    running with the flag off is observable. See the module docstring:
    the flag no longer works as a rollback.
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
    deliveries = [_signal_running_consumers(event)]
    if broadcast:
        deliveries.append(_broadcast_in_process(event, wire_routing_key or _DEFAULT_WIRE_ROUTING_KEY))
    await asyncio.gather(*deliveries, return_exceptions=False)
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

    # Workflow-scoped delivery (core dispatch rule, not per-plugin code):
    # an envelope whose ``workflow_id`` is set by its producer's call site
    # reaches ONLY that workflow's consumers. Every consumer shape carries
    # the EventWorkflowId Search Attribute (controllers via
    # _start_controller, canary listeners via the deployment manager,
    # direct-exec MachinaWorkflow via the executor), so the narrowing is
    # uniform. Unscoped envelopes (telegram / webhook / gmail — genuinely
    # broadcast semantics) are unchanged. Without this, one workflow's
    # chat message fired every deployed workflow's chatTrigger.
    scope_clause = ""
    scoped_workflow_id = getattr(event, "workflow_id", None)
    if scoped_workflow_id:
        literal = str(scoped_workflow_id).replace("'", "''")
        scope_clause = f" AND EventWorkflowId='{literal}'"

    try:
        # Controlled deployments keep trigger definitions in their controller
        # history. Controllers filter the event against those definitions, so
        # no TriggerListenerWorkflow/ PollingTriggerWorkflow execution exists.
        query = f"({query}{scope_clause}) OR ({_RUNNING_CONTROLLERS_QUERY}{scope_clause})"
        consumers = []
        async for wf in client.list_workflows(query=query):
            consumers.append(wf)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"emit: Visibility query failed (query={query!r}): {exc}")
        return

    # Every signal is immutable history in the receiving workflow —
    # controllers used to receive EVERY platform event (matching or not)
    # and filter in-workflow, so one busy deployment burned down every
    # other controller's continue-as-new budget. Controllers advertise
    # their push event types via the ControlEventTypes Search Attribute
    # (upserted as triggers register); skip the ones with no match.
    # Controllers without the attribute (pre-upgrade histories) keep the
    # legacy match-all behaviour.
    matched_types = {str(t) for t in event_types}
    targets = []
    skipped = 0
    for wf in consumers:
        if _is_controller(wf):
            declared = _controller_event_types(wf)
            if declared is not None and not (declared & matched_types):
                skipped += 1
                continue
        targets.append(wf)

    if not targets:
        logger.debug(
            f"emit: 0 consumers for event.type={event.type!r} "
            f"(event.id={event.id}, controllers_skipped={skipped})",
        )
        return

    # Signal each consumer concurrently. Per-target failures don't
    # block other consumers (return_exceptions=True surfaces failures
    # as exceptions in the result list).
    signal_results = await asyncio.gather(
        *[_signal_one(client, wf.id, event) for wf in targets],
        return_exceptions=True,
    )

    delivered = sum(1 for r in signal_results if not isinstance(r, Exception))
    failed = len(signal_results) - delivered
    logger.info(
        f"emit: signalled {delivered}/{len(targets)} consumers for "
        f"event.type={event.type!r} (failed={failed}, "
        f"controllers_skipped={skipped}, event.id={event.id})",
    )


def _is_controller(wf: object) -> bool:
    """True when a Visibility-listed execution is a WorkflowControlWorkflow."""
    wf_type = getattr(wf, "workflow_type", None)
    wf_type = getattr(wf_type, "name", None) or wf_type
    return str(wf_type) == "WorkflowControlWorkflow"


def _controller_event_types(wf: object) -> Optional[set]:
    """Read the ControlEventTypes keyword-list Search Attribute off a
    Visibility-listed execution.

    ``None`` means the attribute is absent (pre-upgrade controller or a
    controller whose upsert failed) — callers treat that as match-all so
    narrowing can never silently disconnect a live deployment. The SDK's
    listed-execution shape varies across versions (typed pairs vs plain
    dict), so both forms are read defensively.
    """
    try:
        typed = getattr(wf, "typed_search_attributes", None)
        if typed is not None:
            for pair in typed:
                key = getattr(getattr(pair, "key", None), "name", None)
                if key != "ControlEventTypes":
                    continue
                value = getattr(pair, "value", None)
                if value is None:
                    return None
                values = value if isinstance(value, (list, tuple)) else [value]
                return {str(v) for v in values}
        raw = getattr(wf, "search_attributes", None)
        if isinstance(raw, dict) and "ControlEventTypes" in raw:
            value = raw.get("ControlEventTypes")
            if value is None:
                return None
            values = value if isinstance(value, (list, tuple)) else [value]
            return {str(v) for v in values}
    except Exception:  # noqa: BLE001 — unreadable attribute == match-all
        return None
    return None


async def _signal_one(client, workflow_id: str, event: WorkflowEvent) -> None:
    """Send the ``on_event`` signal to one workflow.

    Kept as its own coroutine so :func:`_signal_running_consumers` can
    ``asyncio.gather`` independently per-consumer.
    """
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(_SIGNAL_NAME, event.model_dump(mode="json"))


async def signal_generation_event(event: WorkflowEvent, controller_id: str, generation: int) -> bool:
    """Address a recorded generation directly. A transient failure retries the publishing activity."""
    from core.container import container
    from temporalio.service import RPCError, RPCStatusCode

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        raise RuntimeError("Temporal is unavailable for a generation event")
    try:
        await _signal_one(wrapper.client, controller_id, event)
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        control = await container.database().get_workflow_control_generation(event.workflow_id, generation)
        if control is None or control.status in {"reset", "resetting", "completed", "failed"}:
            return False
        raise
    return True


async def _broadcast_in_process(event: WorkflowEvent, wire_routing_key: str) -> None:
    """Direct in-process WS fan-out via the status broadcaster.

    Same asyncio event loop as the FastAPI handlers — the ``main.py``
    lifespan schedules ``run_temporal_lifecycle`` with
    ``asyncio.create_task``, and it starts the TemporalWorkerManager.
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
