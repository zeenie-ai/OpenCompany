"""Plugin-owned shutdown-hook registry for FastAPI lifespan teardown.

Closes the cross-plugin reaches in ``main.py``:

  - ``from nodes.android._relay.manager import close_relay_client``
  - ``from nodes.browser._service import shutdown_browser_service``

Both made the framework's lifespan code know about specific plugins'
internals. Plugins with cleanup logic now self-register a hook; the
lifespan awaits all registered hooks via :func:`run_shutdown_hooks`.

Distinct from :mod:`services._supervisor`: that registry is for
managed-subprocess supervisors (Go binaries, etc.) with a well-defined
``shutdown()`` contract. This registry is the general async-cleanup
hatch — any coroutine-shaped teardown (close a connection, drain a
queue, flush a cache, ...) goes here.

Hook signature
--------------

::

    hook() -> Awaitable[None]

The hook is named (label) so failures during shutdown surface with a
plugin identifier instead of just a traceback. Failures DO NOT block
sibling hooks — every hook runs even if one raises, mirroring the
``shutdown_all_supervisors`` semantics.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

from core.logging import get_logger
from services.plugin.registry import IdempotentRegistry

logger = get_logger(__name__)


ShutdownHook = Callable[[], Awaitable[None]]


# Registry is keyed by label so each plugin can register exactly one
# hook under a stable name. The `items` view preserves insertion order
# (CPython 3.7+ dict ordering guarantee) which gives deterministic
# shutdown ordering. Plugins that need ordering invariants should
# document them via their label naming.
_REGISTRY: IdempotentRegistry[str, ShutdownHook] = IdempotentRegistry("plugin_shutdown_hook")


def register_shutdown_hook(label: str, hook: ShutdownHook) -> None:
    """Publish a shutdown hook for FastAPI lifespan teardown.

    Idempotent on re-import (same callable for the same label is a
    no-op). A different callable for an existing label raises
    ``ValueError`` to surface plugin namespace collisions at import time.

    Args:
        label: Stable plugin identifier (``"android_relay"``,
            ``"browser_service"``, ...). Surfaces in shutdown logs +
            in the error message if the hook raises.
        hook: Async function taking no arguments. Should be
            idempotent (lifespan may run it multiple times during
            test harness teardown).
    """
    _REGISTRY.register(label, hook)


def registered_labels() -> Tuple[str, ...]:
    """Return registered hook labels in insertion order (snapshot)."""
    return tuple(_REGISTRY.keys())


async def run_shutdown_hooks() -> None:
    """Run every registered shutdown hook in registration order.

    Per-hook failures are caught + logged with the hook's label so
    one slow / broken plugin doesn't strand the rest of the lifespan
    teardown. Mirrors ``services._supervisor.shutdown_all_supervisors``
    semantics.
    """
    hooks: List[Tuple[str, ShutdownHook]] = list(_REGISTRY.items().items())
    if not hooks:
        return

    logger.info(f"Running {len(hooks)} plugin shutdown hook(s): " f"{[label for label, _ in hooks]}")
    for label, hook in hooks:
        try:
            await hook()
        except Exception as exc:  # noqa: BLE001 — log and continue
            logger.error(
                f"Plugin shutdown hook {label!r} raised; continuing: {exc}",
                exc_info=True,
            )


__all__ = [
    "register_shutdown_hook",
    "registered_labels",
    "run_shutdown_hooks",
    "ShutdownHook",
]
