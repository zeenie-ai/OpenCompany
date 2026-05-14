"""Plugin-owned dispatch registry for the social-messaging facade.

Closes the cross-plugin reach from ``nodes/social/_base.py:478`` —
``from nodes.whatsapp._service import handle_whatsapp_send`` — which
made the ``social`` plugin depend on the ``whatsapp`` plugin's
internals and violated the "framework knows no plugin names" rule.

Each social platform plugin (whatsapp, telegram, slack, discord, …)
registers a send handler keyed by the platform identifier from its
own ``__init__.py``. The social node queries the registry instead of
importing platform internals — same Wave-11.I plugin-self-registration
pattern as ``register_filter_builder`` / ``register_poll_coroutine_factory``
/ ``register_ws_handlers`` / ``register_canary_trigger_type``.

Handler signature
-----------------

::

    handler(params: Dict[str, Any]) -> Awaitable[Dict[str, Any]]

The social node maps its own parameter shape onto the platform's
expected shape and calls the handler. The handler returns the
platform's native result dict; the social node passes that through.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from services.plugin.registry import IdempotentRegistry


# (params: Dict) -> Awaitable[Dict]
SocialSendHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


_REGISTRY: IdempotentRegistry[str, SocialSendHandler] = IdempotentRegistry(
    "social_send_handler"
)


def register_social_send_handler(platform: str, handler: SocialSendHandler) -> None:
    """Publish a send handler for one social platform.

    Idempotent on re-import (same callable for the same platform key
    is a no-op). A different callable for an existing platform raises
    ``ValueError`` to surface plugin namespace collisions at import time.

    Args:
        platform: Lower-case platform identifier (``"whatsapp"``,
            ``"telegram"``, …). Matches the value the social node's
            ``platform`` parameter holds at runtime.
        handler: Async function accepting platform-specific
            ``params: Dict`` and returning the platform's native
            result dict. The social node builds the params dict by
            mapping its generic shape onto the platform's keys.
    """
    _REGISTRY.register(platform, handler)


def get_social_send_handler(platform: str) -> Optional[SocialSendHandler]:
    """Return the handler for ``platform``, or ``None`` if unregistered.

    A ``None`` return surfaces as a clear "unsupported platform" error
    at the social node call site instead of an ``ImportError`` deep
    inside the platform's ``_service.py``.
    """
    return _REGISTRY.get(platform)


def registered_platforms() -> frozenset[str]:
    """Return an immutable snapshot of registered platform identifiers."""
    return frozenset(_REGISTRY.keys())


__all__ = [
    "register_social_send_handler",
    "get_social_send_handler",
    "registered_platforms",
    "SocialSendHandler",
]
