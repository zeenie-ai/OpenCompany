"""Per-node display metadata.

Holds the UI-only fields (``displayName``, ``icon``, ``group``,
``subtitle``, ``description``) for every node type. This dict is the
single source of truth for everything the parameter panel + palette
need to render a node header — the frontend consumes it via the
NodeSpec endpoints (see ``server/routers/schemas.py`` and
``server/nodes/README.md``). Missing entries fall back to the node
type id.

Kept as a plain dict (not Pydantic) for fast iteration — switch to a
TypedDict / Pydantic model once the schema stabilises.
"""

from typing import Literal, Optional, TypedDict


class NodeHandle(TypedDict, total=False):
    """One React Flow handle on a node. Wave 10.A replaces the
    frontend-hardcoded `AGENT_CONFIGS` handle topology.
    """

    name: str  # "input-skill", "output-top", ...
    kind: Literal["input", "output"]
    position: Literal["top", "bottom", "left", "right"]
    offset: str  # CSS % e.g. "25%"; optional
    label: str  # tooltip
    role: str  # "main" / "skill" / "tools" / "memory" / "task" / "teammates" / "model"


class NodeMetadata(TypedDict, total=False):
    # Existing (Wave 6):
    displayName: str
    icon: str  # emoji | "asset:<key>" | SVG data URI
    group: list[str]
    subtitle: str
    description: str
    version: int
    # Per-node UI panel hints lifted from the legacy frontend INodeUIHints.
    # Flags like isChatTrigger / isConsoleSink / hasCodeEditor / isMemoryPanel.
    uiHints: dict[str, object]

    # Wave 10.A — full visual contract:
    color: str  # hex or dracula token e.g. "#bd93f9"
    componentKind: Literal[  # frontend component dispatch key
        "square",
        "circle",
        "trigger",
        "start",
        "agent",
        "chat",
        "tool",
        "model",
        "generic",
    ]
    handles: list[NodeHandle]  # replaces AGENT_CONFIGS topology
    credentials: list[str]  # provider keys
    hideOutputHandle: bool  # replaces NO_OUTPUT_NODE_TYPES
    hideInputHandle: bool  # auto-derived for usable_as_tool=True nodes
    visibility: Literal["all", "normal", "dev"]  # replaces SIMPLE_MODE_CATEGORIES


# Seeded incrementally per Wave 6 Phase 3 sub-commit. Sub-commit 3a
# covers utility + code + process + workflow groups (12 types). Later
# sub-commits add messaging (3b), agents/models (3c), and the rest.
# Source: client/src/nodeDefinitions/*.ts at the time of migration.
# Plugin-populated registry. Each node module in server/nodes/*.py
# calls services.node_registry.register_node(...) at import time to
# add its own entry here. server/nodes/__init__.py walks the package
# on startup (see main.py lifespan) so this dict is fully populated
# before any NodeSpec endpoint serves a request. No hardcoded data.
NODE_METADATA: dict[str, NodeMetadata] = {}


class GroupMetadata(TypedDict, total=False):
    """Wave 10.B: per-group metadata for the component palette.

    Retires the frontend `CATEGORY_ICONS`, `colorMap`, `labelMap`,
    `SIMPLE_MODE_CATEGORIES` tables in `client/src/components/ui/
    ComponentPalette.tsx`. Each group's icon/label/color/visibility is
    declared once in `server/nodes/groups.py` and served via
    `/api/schemas/nodes/groups`.
    """

    label: str  # e.g. "AI Agents"
    icon: str  # emoji | "asset:<key>"
    color: str  # hex / dracula token
    visibility: Literal["all", "normal", "dev"]


# Plugin-populated registry — same pattern as NODE_METADATA. Filled by
# register_group(...) calls in server/nodes/groups.py at import time.
GROUP_METADATA: dict[str, GroupMetadata] = {}


def get_group_metadata(group: str) -> Optional[GroupMetadata]:
    """Return metadata for a group key, or None if not seeded."""
    return GROUP_METADATA.get(group)


def get_node_metadata(node_type: str) -> Optional[NodeMetadata]:
    """Return display metadata for a node type, or None if not seeded."""

    return NODE_METADATA.get(node_type)


def fallback_metadata(node_type: str) -> NodeMetadata:
    """Minimal metadata for a node type without a seeded entry. Keeps
    NodeSpec emission valid even before Phase 3 migrations land."""

    return {
        "displayName": node_type,
        "icon": "",
        "group": [],
        "description": "",
        "version": 1,
    }
