"""Plugin-owned registry of service-factory callables for the DI container.

Closes the two remaining cross-plugin top-level imports in
``core/container.py``:

  - ``from nodes.location._service import MapsService``  (line 25)
  - ``from nodes.android._dispatcher import AndroidService``  (line 30)

Both made the framework's DI container code know about specific
plugins' implementation classes — direct violation of the
"framework knows no plugin names" rule.

Pattern (matches the established Wave-11.I plugin-self-registration
shape; same as ``register_filter_builder`` /
``register_poll_coroutine_factory`` / ``register_ws_handlers`` /
``register_canary_trigger_type`` / ``register_social_send_handler`` /
``register_shutdown_hook``):

1. Plugin's ``__init__.py`` imports its service class from ``_service.py``
   (intra-plugin, fine) and calls
   ``register_service_factory("<name>", ServiceClass)``.
2. The container's provider declaration references a lazy lookup
   function — :func:`get_service_factory` — instead of the concrete
   class. ``providers.Factory`` / ``providers.Singleton`` calls the
   wrapper function with its declared kwargs; the wrapper looks up
   the registered factory and instantiates.
3. Lookup happens at **instantiation time** (first ``container.foo()``
   call), not at class-definition time, so plugin import ordering
   isn't a constraint — plugin auto-discovery on startup populates
   the registry before any service is first resolved.

Factory signature
-----------------

The "factory" is typically the class itself (calling it constructs
the instance). Any zero-or-more-keyword-arg callable that returns
the service instance is acceptable.

::

    factory(**kwargs) -> ServiceInstance
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from services.plugin.registry import IdempotentRegistry


ServiceFactory = Callable[..., Any]


_REGISTRY: IdempotentRegistry[str, ServiceFactory] = IdempotentRegistry("service_factory")


def register_service_factory(name: str, factory: ServiceFactory) -> None:
    """Publish a service factory for the DI container.

    Idempotent on re-import (same callable / class for the same name
    is a no-op). A different factory for an existing name raises
    ``ValueError`` to surface plugin namespace collisions at import time.

    Args:
        name: Lower-case service identifier (``"maps"``, ``"android"``,
            ...). The container's provider declaration references this
            name via :func:`get_service_factory`.
        factory: The service class (or any callable taking the
            provider's declared kwargs) that constructs the service
            instance.
    """
    _REGISTRY.register(name, factory)


def get_service_factory(name: str) -> Optional[ServiceFactory]:
    """Return the factory for ``name``, or ``None`` if unregistered.

    Container providers wrap this in a lazy thunk that surfaces a
    clear "service not registered" error if a required plugin failed
    to load, instead of silently producing ``None``.
    """
    return _REGISTRY.get(name)


def registered_service_names() -> frozenset[str]:
    """Return an immutable snapshot of registered service identifiers."""
    return frozenset(_REGISTRY.keys())


__all__ = [
    "register_service_factory",
    "get_service_factory",
    "registered_service_names",
    "ServiceFactory",
]
