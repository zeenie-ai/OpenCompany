"""CloudEvents factories for the Discord plugin.

The type constants live here and nowhere else. The canary registration in
``__init__.py`` passes the same string to
``register_canary_trigger_type``, where it becomes the ``EventType`` Search
Attribute a Temporal Visibility query matches on. If the two ever differed,
the trigger listener would start cleanly and then never fire -- a failure with
no error anywhere.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent

SOURCE = "opencompany://nodes/discord"

MESSAGE_RECEIVED_TYPE = "com.opencompany.discord.message.received"
INTERACTION_CREATED_TYPE = "com.opencompany.discord.interaction.created"
CONNECTION_OPENED_TYPE = "com.opencompany.discord.connection.opened"
CONNECTION_CLOSED_TYPE = "com.opencompany.discord.connection.closed"

# Shared across plugins; the frontend routes on envelope.source.
STATUS_WIRE_KEY = "plugin_connection_status"
MESSAGE_WIRE_KEY = "discord_message_received"
INTERACTION_WIRE_KEY = "discord_interaction_created"


def discord_message_received(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Envelope for one inbound Discord message."""
    message_id = event_data.get("message_id")
    fields = {"id": f"discord:message:{message_id}"} if message_id else {}
    return WorkflowEvent(
        **fields,
        source=SOURCE,
        type=MESSAGE_RECEIVED_TYPE,
        # Snowflakes are stringified upstream, in _dispatch. The envelope
        # requires a string subject regardless.
        subject=str(event_data.get("channel_id") or ""),
        data=dict(event_data),
    )


def discord_interaction_created(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Envelope for one slash command or component click.

    A separate type from messages, not a flag on one: a canary trigger
    registers exactly one CloudEvents type, because that string becomes the
    Search Attribute its listener is found by. One type per trigger node is
    the only shape the routing supports.
    """
    interaction_id = event_data.get("interaction_id")
    fields = {"id": f"discord:interaction:{interaction_id}"} if interaction_id else {}
    return WorkflowEvent(
        **fields,
        source=SOURCE,
        type=INTERACTION_CREATED_TYPE,
        subject=str(event_data.get("interaction_id") or ""),
        data=dict(event_data),
    )


def discord_connection_status(
    *,
    connected: bool,
    accounts: list[dict[str, Any]] | None = None,
) -> WorkflowEvent:
    """Envelope describing every account's gateway state.

    ``connected`` stays a plain boolean so single-connection renderers keep
    working; per-account detail rides in ``accounts``.
    """
    return WorkflowEvent(
        source=SOURCE,
        type=CONNECTION_OPENED_TYPE if connected else CONNECTION_CLOSED_TYPE,
        subject="discord",
        data={"connected": connected, "accounts": accounts or []},
    )


async def broadcast_discord_status(
    *, connected: bool, accounts: list[dict[str, Any]] | None = None
) -> None:
    from services.status_broadcaster import get_status_broadcaster

    event = discord_connection_status(connected=connected, accounts=accounts)
    broadcaster = get_status_broadcaster()
    await broadcaster.broadcast(
        {"type": STATUS_WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)}
    )


async def dispatch_discord_message_received(event_data: Mapping[str, Any]) -> None:
    """Single delivery path: Temporal listeners and the in-process socket.

    ``emit`` is a no-op unless ``Settings.event_framework_enabled``, so the
    legacy path stays the default.
    """
    from services.events.dispatch import emit

    await emit(discord_message_received(event_data), wire_routing_key=MESSAGE_WIRE_KEY)


async def dispatch_discord_interaction_created(event_data: Mapping[str, Any]) -> None:
    from services.events.dispatch import emit

    await emit(discord_interaction_created(event_data), wire_routing_key=INTERACTION_WIRE_KEY)


__all__ = [
    "CONNECTION_CLOSED_TYPE",
    "CONNECTION_OPENED_TYPE",
    "INTERACTION_CREATED_TYPE",
    "MESSAGE_RECEIVED_TYPE",
    "SOURCE",
    "broadcast_discord_status",
    "discord_connection_status",
    "discord_interaction_created",
    "discord_message_received",
    "dispatch_discord_interaction_created",
    "dispatch_discord_message_received",
]
