"""Lazy dependency-injection helpers (Wave 11.I, milestone T-residual).

Plugin packages need lazy imports of singletons from
:mod:`core.container` to avoid the
``nodes/<plugin>/__init__ -> core.container -> nodes.android._dispatcher
-> nodes.android.__init__ -> _router`` import cycle that bites at
package load time. Pre-T-residual the codebase had 53 inline
``from core.container import container`` statements scattered across
plugin handlers + services. These helpers consolidate the pattern
into one place.

**Important: NOT memoised.** Both helpers re-resolve the singleton on
every call. Test fixtures swap the auth-service / database instances
mid-test via the dependency-injection container's ``override``
mechanism; a memoised cache would lock in the originally-resolved
instance and the override would be silently ignored. The lookup is
a dict access -- caching adds nothing measurable.

Usage::

    from services.plugin.deps import get_auth_service

    async def handle_thing(data, websocket):
        auth_service = get_auth_service()
        client_id = await auth_service.get_api_key("twitter_client_id")
        ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.cache import CacheService
    from core.database import Database
    from services.ai import AIService
    from services.auth import AuthService


def get_auth_service() -> "AuthService":
    """Resolve the singleton :class:`services.auth.AuthService`.

    Lazy import + call-time lookup. Test monkeypatching depends on
    the container override being read at call time, not at module
    import time -- do NOT memoise.
    """
    from core.container import container

    return container.auth_service()


def get_database() -> "Database":
    """Resolve the singleton :class:`core.database.Database`.

    Same NOT-memoised contract as :func:`get_auth_service`.
    """
    from core.container import container

    return container.database()


def get_cache() -> "CacheService":
    """Resolve the singleton :class:`core.cache.CacheService`."""
    from core.container import container

    return container.cache()


def get_ai_service() -> "AIService":
    """Resolve the singleton :class:`services.ai.AIService`."""
    from core.container import container

    return container.ai_service()


def get_text_service() -> Any:
    """Resolve the singleton ``TextService`` (text generation)."""
    from core.container import container

    return container.text_service()


def get_maps_service() -> Any:
    """Resolve the singleton :class:`nodes.location._service.MapsService`."""
    from core.container import container

    return container.maps_service()


def get_android_service() -> Any:
    """Resolve the singleton :class:`nodes.android._dispatcher.AndroidService`."""
    from core.container import container

    return container.android_service()


__all__ = [
    "get_auth_service",
    "get_database",
    "get_cache",
    "get_ai_service",
    "get_text_service",
    "get_maps_service",
    "get_android_service",
]
