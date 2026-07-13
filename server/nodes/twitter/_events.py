"""Wave 12: CloudEvents factories + dispatcher for twitter.

Plugin-specific event emission for Twitter/X inbound events (mentions,
search hits, timeline updates, DMs).

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Legacy ``event_type`` (``"twitter_event_received"``) is preserved on
the dispatch path so the ``twitterReceive`` trigger node's
``event_type`` ClassVar still matches without a coordinated
registry-side rename.

Canary status (2026-05-15):
  - Typed envelope + dispatcher infrastructure: ready (this file).
  - Canary registry opt-in: **deferred**. ``twitterReceive`` is a plain
    ``TriggerNode`` with ``mode="polling"``; the Temporal-durable
    polling canary path (``PollingTriggerWorkflow`` + ``as_poll_activity``)
    requires the node class to subclass :class:`PollingTriggerNode` and
    declare the 4 hooks (``setup_service`` / ``fetch_ids`` /
    ``fetch_detail`` / ``post_emit``) — same shape as ``GmailReceiveNode``.
    Once that refactor lands, add
    ``register_canary_trigger_type("twitterReceive")`` to
    ``nodes/twitter/__init__.py`` and the deployment manager picks up
    the polling canary path automatically.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Legacy event_type the event_waiter dispatches by; trigger nodes
# subscribe on this string (matches ``TwitterReceiveNode.event_type``).
_LEGACY_EVENT_TYPE = "twitter_event_received"


# ---- Typed factory ---------------------------------------------------------


def twitter_event_received(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Inbound Twitter/X event envelope (mention / search hit /
    timeline update / DM).

    ``subject`` is the tweet/DM id when available so consumers can
    dedup; the originating Twitter user id is in
    ``data.author_id`` or ``data.user_id``. The event ``type``
    distinguishes the kind via the data payload, not the envelope —
    every twitter inbound shares ``com.opencompany.twitter.event.received``
    since the producer (polling loop) doesn't always know which kind a
    given tweet is until the downstream filter runs.
    """
    payload = dict(event_data)
    subject = payload.get("tweet_id") or payload.get("id") or payload.get("dm_id")
    return WorkflowEvent(
        source="opencompany://nodes/twitter",
        type="com.opencompany.twitter.event.received",
        subject=str(subject) if subject else None,
        data=payload,
    )


# ---- Dispatcher wrapper ----------------------------------------------------


async def dispatch_twitter_event_received(event_data: Mapping[str, Any]) -> int:
    """Dispatch an incoming Twitter/X event to waiting ``twitterReceive``
    trigger nodes.

    Two delivery paths (mirrors :func:`email._events.dispatch_email_received`):

    1. **Legacy event_waiter waiters** via :func:`event_waiter.dispatch`
       (in-process collector/processor; the default while
       ``twitterReceive`` is not in the canary registry).
    2. **Temporal-durable listeners** via
       :func:`services.events.dispatch.emit`. ``emit`` is a pass-through
       no-op when ``event_framework_enabled`` is off; otherwise it
       fan-outs to running consumer workflows via Visibility query.

    Returns the count of legacy waiters resolved.
    """
    from services import event_waiter
    from services.events.dispatch import emit

    payload = dict(event_data)
    resolved = event_waiter.dispatch(_LEGACY_EVENT_TYPE, payload)

    await emit(
        twitter_event_received(payload),
        wire_routing_key=_LEGACY_EVENT_TYPE,
    )

    return resolved


__all__ = [
    "dispatch_twitter_event_received",
    "twitter_event_received",
]
