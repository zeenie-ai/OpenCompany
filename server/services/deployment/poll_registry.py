"""Polling-coroutine factory registry (Wave 11.I, milestone L).

Plugin packages register a factory that produces an async polling
coroutine for a given trigger node type. ``DeploymentManager`` looks
up the factory at deploy time and hands the produced coroutine to
``TriggerManager.setup_polling_trigger`` -- the deployment manager no
longer hardcodes per-plugin polling switches.

Mirror of :func:`services.event_waiter.register_filter_builder`:
same idempotency contract, same audience (plugin ``__init__.py``
modules), built on the shared :class:`IdempotentRegistry`.

Factory signature
-----------------

    factory(node_id: str, params: Dict[str, Any]) -> async (
        queue: asyncio.Queue, is_running_fn: Callable[[], bool]
    ) -> None

The factory closes over node-specific config (auth, query, poll
interval); the returned coroutine drains until ``is_running_fn()``
returns False or the task is cancelled. New events go on ``queue``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from services.plugin.registry import IdempotentRegistry


# (queue, is_running) -> awaitable. The poll coroutine itself.
PollCoroutine = Callable[[asyncio.Queue, Callable[[], bool]], Awaitable[None]]

# (node_id, params) -> the bound poll coroutine.
PollCoroutineFactory = Callable[[str, Dict[str, Any]], PollCoroutine]


_REGISTRY: IdempotentRegistry[str, PollCoroutineFactory] = IdempotentRegistry(
    "poll_coroutine_factory"
)


def register_poll_coroutine_factory(
    node_type: str, factory: PollCoroutineFactory
) -> None:
    """Publish a polling-coroutine factory for a trigger node type.

    Idempotent on re-import (same callable for the same key is a
    no-op). A different callable for an existing key raises
    ``ValueError`` to surface plugin namespace collisions early.
    """
    _REGISTRY.register(node_type, factory)


def get_poll_coroutine_factory(node_type: str) -> Optional[PollCoroutineFactory]:
    """Return the factory for ``node_type``, or ``None`` if unregistered."""
    return _REGISTRY.get(node_type)
