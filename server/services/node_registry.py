"""Unified node registration (Wave 10.C).

One decorator, one file per node. Each node's own module declares its
own metadata (displayName, icon, color, handles, componentKind, ...),
Pydantic input model, output model, and handler, and calls
``register_node(...)`` at import time. Discovery of ``server/nodes/*.py``
happens via ``server/nodes/__init__.py``'s ``pkgutil.walk_packages``.

Adding a new node = one Python module. Zero cross-cutting edits. Zero
frontend change. The NodeSpec envelope served at
``GET /api/schemas/nodes/{type}/spec.json`` picks up everything.

This is a **strangler fig** over the existing four registries:
    models/node_metadata.NODE_METADATA
    services/node_input_schemas._DIRECT_MODELS
    services/node_output_schemas.NODE_OUTPUT_SCHEMAS
    services/node_executor._build_handler_registry()

``register_node`` writes to the first three directly and to an
additional ``_HANDLER_REGISTRY`` dict that ``node_executor`` reads on
startup. Legacy entries that were seeded before ``register_node``
remain untouched — migration is incremental.
"""

from __future__ import annotations

from typing import Callable, Optional, Type

from pydantic import BaseModel

from models.node_metadata import GROUP_METADATA, NODE_METADATA, GroupMetadata, NodeMetadata
from services.node_input_schemas import _DIRECT_MODELS
from services.node_output_schemas import NODE_OUTPUT_SCHEMAS


_HANDLER_REGISTRY: dict[str, Callable] = {}

# Plugin-class registry populated by :class:`BaseNode.__init_subclass__`.
# Separate from _HANDLER_REGISTRY so the Temporal worker (11.F) can walk
# plugin classes directly without re-discovering them.
_NODE_CLASS_REGISTRY: dict[str, type] = {}


def register_node(
    *,
    type: str,
    metadata: NodeMetadata,
    input_model: Optional[Type[BaseModel]] = None,
    output_model: Optional[Type[BaseModel]] = None,
    handler: Optional[Callable] = None,
) -> None:
    """Register a node's metadata + schemas + handler in one call.

    Idempotent: re-registering the same type replaces the prior entry,
    which supports module hot-reload during development.

    Only ``type`` and ``metadata`` are required. A node can be handler-
    free (e.g. a memory / config node that only carries parameters the
    agent reads from the edge) or schema-free (e.g. a pure visual marker
    with no runtime effect), though in practice most nodes have both.
    """
    NODE_METADATA[type] = metadata
    if input_model is not None:
        _DIRECT_MODELS[type] = input_model
    if output_model is not None:
        NODE_OUTPUT_SCHEMAS[type] = output_model
    if handler is not None:
        _HANDLER_REGISTRY[type] = handler

    # Invalidate any NodeSpec and per-type input-schema cached under the
    # same type — otherwise a consumer that read the pre-registration
    # (empty metadata) spec gets stuck with stale fields. Re-registration
    # is the whole point of the strangler-fig migration, so caches must
    # re-compute.
    from services.node_spec import _spec_cache  # local import to avoid cycle

    _spec_cache.pop(type, None)
    from services.node_input_schemas import _schema_cache  # local import

    _schema_cache.pop(type, None)


def get_registered_handler(node_type: str) -> Optional[Callable]:
    """Handler registered via ``register_node``, or None.

    ``NodeExecutor._build_handler_registry`` consults this first and
    falls back to its built-in dict for types still on the legacy path.
    """
    return _HANDLER_REGISTRY.get(node_type)


def registered_node_types() -> frozenset[str]:
    """Types that came in via the plugin path. Useful for tests and for
    telling the legacy dispatcher which types it must NOT also claim."""
    return frozenset(_HANDLER_REGISTRY.keys())


def register_node_class(node_cls: type) -> None:
    """Store a :class:`services.plugin.BaseNode` subclass reference.

    Called from ``BaseNode.__init_subclass__`` BEFORE the eager
    ``register_node(...)`` write so that ``_metadata_dict()`` (which
    runs as the ``metadata`` argument) can resolve the plugin folder
    via :func:`get_node_class` to locate ``icon.svg``. Also lets
    Wave 11.F's Temporal worker walk ``_NODE_CLASS_REGISTRY.values()``
    to collect ``cls.as_activity()`` for registration, without
    re-discovering via the filesystem.
    """
    if not getattr(node_cls, "type", ""):
        return
    _NODE_CLASS_REGISTRY[node_cls.type] = node_cls


def get_node_class(node_type: str) -> Optional[type]:
    """Return the :class:`BaseNode` subclass for ``node_type``, or None
    if this type was registered via the legacy metadata-only form."""
    return _NODE_CLASS_REGISTRY.get(node_type)


def registered_node_classes() -> dict[str, type]:
    """Snapshot of the plugin-class registry. Used by the Temporal
    worker to register one activity per plugin."""
    return dict(_NODE_CLASS_REGISTRY)


def iter_tool_node_classes():
    """Yield ``(type, cls)`` for every plugin surfaced as an LLM tool.

    Three plugin patterns map into ``services.ai._build_tool_from_node``:

    1. Pure ToolNode (``component_kind == "tool"``) — calculator,
       duckduckgoSearch, writeTodos, agentBuilder, ...
    2. Dual-purpose ActionNode (``usable_as_tool=True``) — pythonExecutor,
       gmail, twitter*, brave_search, all 16 android service nodes, ...
    3. SpecializedAgentBase subclasses (``component_kind == "agent"``) —
       routed via ``delegate_to_*`` dispatch in
       ``services/handlers/tools.py``.

    Config aggregators with ``uiHints.isMasterSkillEditor`` (masterSkill)
    are excluded — they live on the canvas as ToolNode visually but are
    never invoked directly by the LLM (the LLM uses the connected skills
    instead).

    Used by Wave 12 D5 to replace the function-local ``DEFAULT_TOOL_NAMES``
    / ``DEFAULT_TOOL_DESCRIPTIONS`` dicts with a registry walk over
    ``cls.tool_name`` / ``cls.tool_description`` ClassVars.
    """
    for node_type, node_cls in _NODE_CLASS_REGISTRY.items():
        component_kind = getattr(node_cls, "component_kind", "")
        ui_hints = getattr(node_cls, "ui_hints", {}) or {}
        if ui_hints.get("isMasterSkillEditor") is True:
            continue
        if component_kind in ("tool", "agent") or getattr(node_cls, "usable_as_tool", False):
            yield node_type, node_cls


def register_group(*, key: str, metadata: GroupMetadata) -> None:
    """Register a component-palette group's metadata.

    Wave 10.B: retires the frontend `CATEGORY_ICONS` / `labelMap` /
    `colorMap` / `SIMPLE_MODE_CATEGORIES` tables. Every group a plugin
    module uses in its `group:` field can declare its visual
    representation here. Idempotent — re-registration replaces.
    """
    GROUP_METADATA[key] = metadata
