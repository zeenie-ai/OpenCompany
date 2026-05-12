"""Plugin self-registration registries.

Four sibling concerns share this file because they're the same pattern
(idempotent dict + collision check) for the same audience (plugin
``__init__.py`` modules wiring themselves into the framework):

1. **WebSocket handlers** — ``register_ws_handlers({type: handler})``
   for side-channel commands like ``telegram_connect``,
   ``whatsapp_status``. Read by ``routers/websocket.py`` at dispatch
   time and merged into ``MESSAGE_HANDLERS``.

2. **HTTP routers** — ``register_router(APIRouter, name="<plugin>")``
   for plugin-owned routes (OAuth callbacks, webhook receivers,
   direct-API endpoints). Read by ``server.main`` after plugin discovery
   and ``app.include_router(...)``'d.

3. **Option loaders** — ``register_option_loader(method_name, fn)`` for
   dynamic-options dropdowns wired through the
   ``loadOptionsMethod`` Pydantic field metadata. Read by
   :func:`dispatch_load_options` (here in this module) and the matching
   WS handler in ``routers/websocket.py``.

4. **OAuth callback paths** — ``register_oauth_callback_path(provider,
   path)`` so :func:`services.oauth_utils.get_redirect_uri` can derive
   the full redirect URI at runtime without a cross-folder import.

No hardcoded plugin names anywhere in the central router or main.py.
Adding a new plugin's WS / HTTP / option-loader / oauth-callback
surface is one registration call inside that plugin's package.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List

from fastapi import APIRouter, WebSocket

from services.plugin.registry import IdempotentRegistry

logger = logging.getLogger(__name__)

WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]
LoadOptionsFn = Callable[[Dict[str, Any]], Awaitable[List[Dict[str, Any]]]]

_WS_REGISTRY: IdempotentRegistry[str, WSHandler] = IdempotentRegistry("ws_handler")
_ROUTER_REGISTRY: IdempotentRegistry[str, APIRouter] = IdempotentRegistry("router")
_OPTION_LOADER_REGISTRY: IdempotentRegistry[str, LoadOptionsFn] = IdempotentRegistry(
    "option_loader"
)
_OAUTH_CALLBACK_PATHS: IdempotentRegistry[str, str] = IdempotentRegistry(
    "oauth_callback_path"
)


# ---- WebSocket handlers --------------------------------------------------

def register_ws_handlers(handlers: Dict[str, WSHandler]) -> None:
    """Publish a batch of ``message_type -> handler`` mappings.

    Idempotent on re-import (same callable for the same key is a no-op).
    Registering a different callable for an existing key raises
    ``ValueError`` to surface plugin namespace collisions early.
    """
    for msg_type, handler in handlers.items():
        _WS_REGISTRY.register(msg_type, handler)


def get_ws_handlers() -> Dict[str, WSHandler]:
    """Snapshot of all plugin-registered WS handlers.

    Returns a fresh dict so callers can mutate without affecting the
    registry (e.g. ``MESSAGE_HANDLERS = {**core, **get_ws_handlers()}``).
    """
    return dict(_WS_REGISTRY.items())


def list_registered_types() -> list[str]:
    """For diagnostics / startup logging."""
    return sorted(_WS_REGISTRY.keys())


# ---- HTTP routers --------------------------------------------------------

def register_router(router: APIRouter, *, name: str) -> None:
    """Publish a plugin-owned ``APIRouter`` for inclusion at app startup.

    ``name`` is the plugin folder name — used for diagnostics and
    collision detection. Same idempotency contract as the WS side: the
    same router for the same name is a no-op; a different router for an
    existing name raises ``ValueError`` so plugin-name collisions fail
    at import time, not request time.
    """
    _ROUTER_REGISTRY.register(name, router)


def get_routers() -> List[APIRouter]:
    """Snapshot of registered routers in registration order."""
    return _ROUTER_REGISTRY.values()


def list_registered_routers() -> List[str]:
    """For diagnostics / startup logging."""
    return sorted(_ROUTER_REGISTRY.keys())


# ---- Option loaders ------------------------------------------------------

def register_option_loader(method_name: str, fn: LoadOptionsFn) -> None:
    """Publish a ``loadOptionsMethod`` async loader.

    ``method_name`` is the string declared on Pydantic ``Field`` metadata
    via ``json_schema_extra={"loadOptionsMethod": "..."}`` (e.g.
    ``gmailLabels``, ``whatsappGroups``). Same idempotency contract as
    the WS / router registries: the same callable for the same method
    is a no-op; a different callable raises ``ValueError`` so plugin
    namespace clashes fail at import time.
    """
    _OPTION_LOADER_REGISTRY.register(method_name, fn)


def get_option_loader(method_name: str) -> LoadOptionsFn | None:
    """Look up a registered loader. ``None`` when the method is unknown."""
    return _OPTION_LOADER_REGISTRY.get(method_name)


def list_registered_option_methods() -> List[str]:
    """For diagnostics / startup logging."""
    return sorted(_OPTION_LOADER_REGISTRY.keys())


async def dispatch_load_options(
    method: str, params: Dict[str, Any] | None = None
) -> List[Dict[str, Any]]:
    """Look up and invoke a registered loader.

    Returns an empty list when the method isn't registered (matches
    n8n's tolerant fallback -- the dropdown stays empty rather than
    erroring out).
    """
    loader = _OPTION_LOADER_REGISTRY.get(method)
    if loader is None:
        return []
    return await loader(params or {})


def list_load_options_methods() -> List[str]:
    """Stable alphabetised list of registered method names. The editor
    prefetches this once on boot so it knows which ``loadOptionsMethod``
    values are wired."""
    return list_registered_option_methods()


# ---- OAuth callback paths -----------------------------------------------

def register_oauth_callback_path(provider: str, path: str) -> None:
    """Publish a plugin's OAuth callback path (``/api/<provider>/callback``).

    Same idempotency contract as the other registries: the same path
    for the same provider is a no-op; a different path for an existing
    provider raises ``ValueError``. ``services.oauth_utils.get_redirect_uri``
    looks up via this registry so the OAuth callback URL is plugin-
    registered, not cross-imported from ``nodes/<plugin>/_oauth.py``.
    """
    _OAUTH_CALLBACK_PATHS.register(provider, path)


def get_oauth_callback_path(provider: str) -> str:
    """Look up the registered callback path or fall back to a default
    (`/api/<provider>/callback`). The fallback matches the convention
    every existing plugin uses."""
    registered = _OAUTH_CALLBACK_PATHS.get(provider)
    if registered is not None:
        return registered
    return f"/api/{provider}/callback"


def list_registered_oauth_providers() -> List[str]:
    """For diagnostics / startup logging."""
    return sorted(_OAUTH_CALLBACK_PATHS.keys())
