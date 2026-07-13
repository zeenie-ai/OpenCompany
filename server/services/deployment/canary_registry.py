"""Wave 12 C1 canary registry — plugin-owned opt-in for Temporal-durable triggers.

Plugin packages call :func:`register_canary_trigger_type` from their
``__init__.py`` to opt their trigger node type into the
:class:`TriggerListenerWorkflow` consumer path inside
:class:`DeploymentManager`. The deployment manager queries via
:func:`is_canary_trigger_type` instead of hardcoding a per-plugin list.

This mirrors the established Wave-11.I plugin-self-registration pattern
(``register_filter_builder``, ``register_poll_coroutine_factory``,
``register_router``, ``register_ws_handlers``, etc.) — DeploymentManager
stays plugin-agnostic and never edits when a new canary trigger lands.

The producer side (each plugin's ``_events.py`` calling
``services.events.dispatch.emit``) is a separate, mechanical change
that doesn't touch this registry. The two together activate the
canary for one trigger type: the registry tells DeploymentManager to
start a ``TriggerListenerWorkflow`` for it; the dispatch.emit call
ensures events reach that listener.

Membership semantics (set-shaped):

    register_canary_trigger_type("webhookTrigger", "com.opencompany.webhook.received")
    register_canary_trigger_type("chatTrigger", "com.opencompany.chat.message.received")
    is_canary_trigger_type("webhookTrigger")  # True
    is_canary_trigger_type("whatsappReceive") # False (not yet opted in)
    cloudevent_type_for("webhookTrigger")     # "com.opencompany.webhook.received"

Idempotent on re-import (multiple registrations of the same type with
the same cloudevent_type are a no-op; a conflicting cloudevent_type
raises so plugin upgrades surface drift loudly).

**Why cloudevent_type is required (Wave 12 bug fix, 2026-05-15).** The
``TriggerListenerWorkflow`` is tagged at start with an ``EventType``
Search Attribute. :func:`services.events.dispatch.emit` runs a
Visibility query ``EventType='<value>' AND ExecutionStatus='Running'``
to find listeners and signal them. The query uses ``event.type`` from
the CloudEvents envelope (reverse-DNS, e.g.
``com.opencompany.chat.message.received``). The Search Attribute MUST
carry the same value or the query never matches and no signal is
delivered — symptom was "TriggerListener started ok but never reacts
to incoming events". Pre-fix the deployment manager registered the
legacy snake_case ``event_type`` from ``TriggerConfig`` (e.g.
``chat_message_received``), which silently broke every C1 canary.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterator, Optional


_REGISTERED: Dict[str, str] = {}


def register_canary_trigger_type(node_type: str, cloudevent_type: str) -> None:
    """Opt this trigger node type into the Wave 12 C1 canary path.

    Called from a plugin's ``__init__.py``. Idempotent on re-import when
    the same ``cloudevent_type`` is supplied; a conflicting
    ``cloudevent_type`` raises :class:`ValueError` so an accidentally
    diverging plugin upgrade can't silently produce events that the
    listener can't see.

    Args:
        node_type: The trigger node's ``BaseNode.type`` string. The
            string MUST match what the workflow JSON carries for nodes
            of this type — that's the key DeploymentManager looks up
            when deciding which trigger goes to the canary listener.
        cloudevent_type: The CloudEvents ``type`` field the plugin's
            ``_events.py`` factory will populate on outgoing envelopes
            (reverse-DNS, e.g. ``"com.opencompany.chat.message.received"``).
            Used as the ``EventType`` Search Attribute value when the
            listener workflow is started so
            :func:`services.events.dispatch.emit`'s Visibility query
            matches it.

    Raises:
        TypeError: if ``node_type`` or ``cloudevent_type`` is not a
            non-empty string.
        ValueError: if ``node_type`` was already registered with a
            different ``cloudevent_type``.
    """
    if not isinstance(node_type, str) or not node_type:
        raise TypeError(
            f"register_canary_trigger_type expected a non-empty str for " f"node_type, got {type(node_type).__name__}: {node_type!r}"
        )
    if not isinstance(cloudevent_type, str) or not cloudevent_type:
        raise TypeError(
            f"register_canary_trigger_type expected a non-empty str for "
            f"cloudevent_type, got {type(cloudevent_type).__name__}: "
            f"{cloudevent_type!r}"
        )
    existing = _REGISTERED.get(node_type)
    if existing is not None and existing != cloudevent_type:
        raise ValueError(
            f"register_canary_trigger_type: node_type={node_type!r} already "
            f"registered with cloudevent_type={existing!r}; refusing to "
            f"overwrite with {cloudevent_type!r}. A diverging registration "
            f"would silently break the Visibility-query → Signal fan-out."
        )
    _REGISTERED[node_type] = cloudevent_type


def is_canary_trigger_type(node_type: str) -> bool:
    """True iff ``node_type`` has been opted into the canary."""
    return node_type in _REGISTERED


def cloudevent_type_for(node_type: str) -> Optional[str]:
    """Return the CloudEvents ``type`` registered for ``node_type``.

    ``None`` when the trigger isn't canary-registered — caller decides
    whether to fall back to a legacy event_type or error.
    """
    return _REGISTERED.get(node_type)


def canary_trigger_types() -> FrozenSet[str]:
    """Return an immutable snapshot of registered canary trigger types.

    Useful for diagnostics / ops dashboards. Don't iterate during a
    deploy — call :func:`is_canary_trigger_type` instead so a future
    plugin reload sees fresh registrations.
    """
    return frozenset(_REGISTERED)


def __iter__() -> Iterator[str]:  # pragma: no cover — module dunders only
    return iter(_REGISTERED)


__all__ = [
    "register_canary_trigger_type",
    "is_canary_trigger_type",
    "cloudevent_type_for",
    "canary_trigger_types",
]
