"""Generic loadOptionsMethod dispatch.

Wave 6 Phase 4 introduced this as a central registry. Wave 11.I,
milestone M relocates the per-plugin loaders into their plugin folders
(``nodes/<plugin>/_option_loaders.py``) and routes lookups through
``services.ws_handler_registry.register_option_loader`` -- the same
``IdempotentRegistry``-backed sibling of ``register_ws_handlers`` /
``register_router``.

While milestone M is in flight we keep a small legacy dict for plugins
that haven't migrated yet; the dispatcher consults the plugin registry
first and falls back to the legacy table. After M.3 the legacy table is
empty and this module retires alongside the rest of
``services/node_option_loaders/``.
"""

from typing import Any, Awaitable, Callable, Optional

from services.ws_handler_registry import get_option_loader

from .android_loaders import load_android_service_actions


# Async loader signature: (params: dict) -> list of {value, label, ...}
LoadOptionsFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]


# Legacy table for not-yet-migrated plugins. Shrinks one entry per M.x
# commit; deleted entirely at the end of M.3.
LEGACY_LOAD_OPTIONS_REGISTRY: dict[str, LoadOptionsFn] = {
    "getAndroidServiceActions": load_android_service_actions,
}


# Backwards-compat alias for the previous public name. Tests and
# ``list_load_options_methods`` still read it; updates land per M.x.
LOAD_OPTIONS_REGISTRY = LEGACY_LOAD_OPTIONS_REGISTRY


async def dispatch_load_options(
    method: str, params: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    """Look up and invoke a registered loader.

    Plugin-registry lookup wins; legacy table is the fallback. Returns
    an empty list when the method isn't registered (matches n8n's
    tolerant fallback -- the dropdown stays empty rather than erroring
    out).
    """

    loader = get_option_loader(method) or LEGACY_LOAD_OPTIONS_REGISTRY.get(method)
    if loader is None:
        return []
    return await loader(params or {})


def list_load_options_methods() -> list[str]:
    """Stable alphabetised list of registered method names. The editor
    prefetches this once on boot so it knows which ``loadOptionsMethod``
    values are wired."""

    from services.ws_handler_registry import list_registered_option_methods

    plugin_methods = set(list_registered_option_methods())
    legacy_methods = set(LEGACY_LOAD_OPTIONS_REGISTRY.keys())
    return sorted(plugin_methods | legacy_methods)
