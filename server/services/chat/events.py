"""Chat's CloudEvents: the owner's message to a workflow, and run events.

**The owner's message.** ``chat_message_received`` carries a message to the
workflow's chatTrigger listeners through ``dispatch.emit`` (Temporal Signals,
narrowed to the workflow by the envelope's ``workflow_id``). It is never
broadcast: it carries the owner's text, and a broadcast reaches every socket;
clients learn of new messages from the identity-only ``chat.updated``. When
the message started a run, the event id is the run id, so the run a listener
spawns has a predictable id (``<slug>-<trigger label>-<run id>``) and a
re-sent event starts nothing twice. ``data.run_id`` names the run;
MachinaWorkflow trusts it only from this module's source and type
(``services/temporal/workflow.py``).

**Run events.** ``chat_run_event`` frames tell a session's subscribers how a
run moves (``services/chat/hub.py``). AG-UI's event model: the type is
``com.opencompany.chat.run.<suffix>``, the id ``<run id>:<seq>``, the subject
the run id, and the scope (``workflow_id``, ``session_id``, ``run_id``, ``seq``,
``hub_epoch``) sits in ``data``, never in top-level extension attributes.
docs-internal/chat_protocol.md lists every suffix and its fields.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from services.events.envelope import WorkflowEvent

SOURCE = "opencompany://services/chat"
MESSAGE_RECEIVED_TYPE = "com.opencompany.chat.message.received"
RUN_TYPE_PREFIX = "com.opencompany.chat.run."
#: WebSocket ``type`` of a run event frame.
RUN_WIRE_KEY = "chat_run_event"
#: ``dispatch.emit``'s wire routing key for the owner's message; matches
#: ``ChatTriggerNode.event_type``.
MESSAGE_WIRE_ROUTING_KEY = "chat_message_received"

RUN_SUFFIXES = frozenset(
    {
        "started",
        "finished",
        "failed",
        "step.started",
        "step.finished",
        "text.started",
        "text.content",
        "text.ended",
        "activity.snapshot",
        "activity.delta",
        "custom",
    }
)
#: Suffixes after which a run publishes nothing more.
TERMINAL_SUFFIXES = frozenset({"finished", "failed"})


def chat_message_received(
    event_data: Mapping[str, Any],
    *,
    workflow_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> WorkflowEvent:
    """The owner's message as the chatTrigger listeners receive it.
    ``subject`` is the session. ``workflow_id`` scopes delivery to one
    workflow's listeners; the caller decides it."""
    payload = dict(event_data)
    session_id = payload.get("session_id")
    fields: Dict[str, Any] = {}
    if event_id:
        fields["id"] = event_id
    return WorkflowEvent(
        source=SOURCE,
        type=MESSAGE_RECEIVED_TYPE,
        subject=str(session_id) if session_id else None,
        workflow_id=workflow_id,
        data=payload,
        **fields,
    )


async def dispatch_chat_message_received(
    event_data: Mapping[str, Any],
    *,
    workflow_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> None:
    """Signal the message to the listeners, without a broadcast."""
    from services.events.dispatch import emit

    await emit(
        chat_message_received(event_data, workflow_id=workflow_id, event_id=event_id),
        wire_routing_key=MESSAGE_WIRE_ROUTING_KEY,
        broadcast=False,
    )


def chat_run_event(
    *,
    suffix: str,
    run_id: str,
    seq: int,
    hub_epoch: str,
    workflow_id: Optional[str],
    session_id: str,
    fields: Optional[Mapping[str, Any]] = None,
) -> WorkflowEvent:
    """One run event. ``fields`` are the suffix's own fields; the scope is
    added here and cannot be overridden by them."""
    if suffix not in RUN_SUFFIXES:
        raise ValueError(f"not a chat run event: {suffix!r}")
    data: Dict[str, Any] = {**dict(fields or {})}
    data.update(
        {
            "workflow_id": workflow_id,
            "session_id": session_id,
            "run_id": run_id,
            "seq": seq,
            "hub_epoch": hub_epoch,
        }
    )
    return WorkflowEvent(
        id=f"{run_id}:{seq}",
        source=SOURCE,
        type=f"{RUN_TYPE_PREFIX}{suffix}",
        subject=run_id,
        data=data,
    )


def run_event_suffix(event_type: str) -> Optional[str]:
    """``started`` for ``com.opencompany.chat.run.started``; None for any
    other type."""
    if not event_type.startswith(RUN_TYPE_PREFIX):
        return None
    suffix = event_type[len(RUN_TYPE_PREFIX):]
    return suffix if suffix in RUN_SUFFIXES else None


__all__ = [
    "MESSAGE_RECEIVED_TYPE",
    "MESSAGE_WIRE_ROUTING_KEY",
    "RUN_SUFFIXES",
    "RUN_TYPE_PREFIX",
    "RUN_WIRE_KEY",
    "SOURCE",
    "TERMINAL_SUFFIXES",
    "chat_message_received",
    "chat_run_event",
    "dispatch_chat_message_received",
    "run_event_suffix",
]
