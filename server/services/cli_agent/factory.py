"""Registry + factory for `AICliProvider` instances.

Plugins call :func:`register_provider` from their package
``__init__.py`` so the framework can build an instance by provider
name without importing from the plugin folder directly (which would
be a ``services → nodes`` layering violation — forbidden by the
plugin-folder pattern in
[docs-internal/plugin_system.md](../../../../docs-internal/plugin_system.md)).

The pattern matches the six existing per-plugin registries
(``ws_handler_registry``, ``register_router``, etc.) — see
:func:`services.ws_handler_registry.register_ws_handlers` for the
template. Provider registration is idempotent: a second
``register_provider("claude", AnthropicClaudeProvider)`` is a no-op
when the binding already matches; mismatching bindings raise.

Usage from a plugin's ``__init__.py``::

    from services.cli_agent.factory import register_provider
    from ._provider import AnthropicClaudeProvider
    register_provider("claude", AnthropicClaudeProvider)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type, Union

from core.logging import get_logger

from services.cli_agent.protocol import AICliProvider

logger = get_logger(__name__)


# Bindings populated by plugin folders on import. ``Union[Type, Callable]``
# because some plugins may want a zero-arg factory function instead of a
# class — both shapes are valid as long as calling them returns an
# ``AICliProvider``-conformant instance.
_PROVIDER_REGISTRY: Dict[str, Union[Type[AICliProvider], Callable[[], AICliProvider]]] = {}

# Per-provider session pool factories. Pool implementations are
# provider-specific (claude uses subprocess + stream-json pooling; codex
# / gemini don't) and live in the plugin folder per the canonical
# layout. This registry lets the framework's pool dispatcher
# (``service.py::_run_pooled_turn``) look up the right pool by
# provider name without importing from the plugin folder directly.
_POOL_REGISTRY: Dict[str, Callable[[], Any]] = {}

# Per-provider skill materialisers. Only claude implements one today —
# it writes ``SKILL.md`` trees under ``<workspace>/.claude/skills/``
# per the Anthropic Skills spec (code.claude.com/docs/en/skills).
# Codex / Gemini don't honor SKILL.md so they don't register here.
# The framework's session bootstrap (``session.py:_pre_spawn``)
# looks up by provider name; ``None`` means no-op (skip materialise).
#
# Signature: ``async (workspace_dir, skill_names, *, previous_skill_names,
# log_label) -> tuple[int, int]`` — (added_count, removed_count).
_SKILL_MATERIALISER_REGISTRY: Dict[str, Callable[..., Any]] = {}


def register_provider(
    name: str,
    provider_factory: Union[Type[AICliProvider], Callable[[], AICliProvider]],
) -> None:
    """Bind a provider name to its class / factory.

    Called from the plugin folder's ``__init__.py`` on import. Idempotent
    when the binding is unchanged; raises ``ValueError`` on conflict so
    accidental double-registration with different classes is caught
    instead of silently shadowing.
    """
    existing = _PROVIDER_REGISTRY.get(name)
    if existing is not None and existing is not provider_factory:
        raise ValueError(f"CLI provider {name!r} already registered with {existing!r}; " f"refusing to overwrite with {provider_factory!r}")
    _PROVIDER_REGISTRY[name] = provider_factory
    logger.debug("[cli_agent] registered provider %r -> %r", name, provider_factory)


def unregister_provider(name: str) -> None:
    """Drop a provider binding. Used by tests."""
    _PROVIDER_REGISTRY.pop(name, None)


def create_cli_provider(name: str) -> AICliProvider:
    """Build a CLI provider by name.

    Raises:
        NotImplementedError: for declared-but-deferred providers
            (currently ``gemini``).
        ValueError: for unknown names.
    """
    factory = _PROVIDER_REGISTRY.get(name)
    if factory is not None:
        return factory()

    # ``gemini`` is intentionally listed as a known-but-unsupported name
    # so the frontend dropdown can show it greyed-out while the v2
    # implementation is in flight. Surface a clean ``NotImplementedError``
    # so factory consumers can detect the deferred state.
    if name == "gemini":
        raise NotImplementedError("gemini provider deferred to v2. Use 'claude' or 'codex' in v1.")

    registered = sorted(_PROVIDER_REGISTRY.keys())
    raise ValueError(
        f"Unknown CLI provider: {name!r}. " f"Registered: {registered}. " f"Did the plugin's ``__init__.py`` call register_provider()?"
    )


def is_supported(name: str) -> bool:
    """True if the provider is registered (plugin imported successfully)."""
    return name in _PROVIDER_REGISTRY


def registered_provider_names() -> frozenset[str]:
    """Frozen snapshot of currently-registered provider names."""
    return frozenset(_PROVIDER_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Session-pool registry — paired with provider registry above
# ---------------------------------------------------------------------------


def register_session_pool(
    provider_name: str,
    pool_getter: Callable[[], Any],
) -> None:
    """Bind a provider to a callable returning its session pool singleton.

    Called from the plugin folder's ``__init__.py`` on import. The
    pool object must expose ``acquire``, ``send_turn``, ``release``,
    ``clear``, ``terminate``, ``shutdown_all``, and ``start_reaper`` —
    the surface ``services.cli_agent.service._run_pooled_turn`` uses.

    Idempotent on identical binding; raises ``ValueError`` on conflict
    so accidental double-registration with different getters is caught.
    """
    existing = _POOL_REGISTRY.get(provider_name)
    if existing is not None and existing is not pool_getter:
        raise ValueError(
            f"Session pool for {provider_name!r} already registered " f"({existing!r}); refusing to overwrite with {pool_getter!r}"
        )
    _POOL_REGISTRY[provider_name] = pool_getter
    logger.debug(
        "[cli_agent] registered session pool %r -> %r",
        provider_name,
        pool_getter,
    )


def get_session_pool(provider_name: str) -> Optional[Any]:
    """Return the session pool singleton for ``provider_name`` or ``None``.

    Calls the registered getter (provider-side) so the pool is lazily
    instantiated on first request.
    """
    getter = _POOL_REGISTRY.get(provider_name)
    if getter is None:
        return None
    return getter()


# ---------------------------------------------------------------------------
# Skill-materialiser registry
# ---------------------------------------------------------------------------


def register_skill_materialiser(
    provider_name: str,
    materialiser: Callable[..., Any],
) -> None:
    """Bind a provider to its skill materialiser (claude only today).

    Called from the plugin folder's ``__init__.py`` on import. The
    materialiser must be an async callable with signature
    ``(workspace_dir, skill_names, *, previous_skill_names, log_label)
    -> tuple[int, int]`` (added_count, removed_count).
    """
    existing = _SKILL_MATERIALISER_REGISTRY.get(provider_name)
    if existing is not None and existing is not materialiser:
        raise ValueError(
            f"Skill materialiser for {provider_name!r} already registered " f"({existing!r}); refusing to overwrite with {materialiser!r}"
        )
    _SKILL_MATERIALISER_REGISTRY[provider_name] = materialiser
    logger.debug(
        "[cli_agent] registered skill materialiser %r -> %r",
        provider_name,
        materialiser,
    )


def get_skill_materialiser(provider_name: str) -> Optional[Callable[..., Any]]:
    """Return the registered materialiser or ``None`` (= skip materialise)."""
    return _SKILL_MATERIALISER_REGISTRY.get(provider_name)
