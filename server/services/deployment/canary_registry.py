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

    register_canary_trigger_type("webhookTrigger")
    register_canary_trigger_type("chatTrigger")
    is_canary_trigger_type("webhookTrigger")  # True
    is_canary_trigger_type("whatsappReceive") # False (not yet opted in)

Idempotent on re-import (multiple registrations of the same type are a
no-op). No collision semantics because there's no "value" to conflict —
membership is the contract.
"""

from __future__ import annotations

from typing import FrozenSet, Iterator, Set


_REGISTERED: Set[str] = set()


def register_canary_trigger_type(node_type: str) -> None:
    """Opt this trigger node type into the Wave 12 C1 canary path.

    Called from a plugin's ``__init__.py``. Idempotent: re-importing the
    plugin module (e.g. during ``importlib.reload`` in the test suite)
    is a no-op.

    Args:
        node_type: The trigger node's ``BaseNode.type`` string. The
            string MUST match what the workflow JSON carries for nodes
            of this type — that's the key DeploymentManager looks up
            when deciding which trigger goes to the canary listener.

    Raises:
        TypeError: if ``node_type`` is not a non-empty string.
    """
    if not isinstance(node_type, str) or not node_type:
        raise TypeError(
            f"register_canary_trigger_type expected a non-empty str, "
            f"got {type(node_type).__name__}: {node_type!r}"
        )
    _REGISTERED.add(node_type)


def is_canary_trigger_type(node_type: str) -> bool:
    """True iff ``node_type`` has been opted into the canary."""
    return node_type in _REGISTERED


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
    "canary_trigger_types",
]
