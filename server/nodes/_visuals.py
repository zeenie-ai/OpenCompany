"""Central handler for node visuals (icon + color).

Two icon sources co-exist by design (per RFC §6.5):

1. **Per-plugin ``icon.svg``** co-located with the plugin folder
   (e.g. ``server/nodes/telegram/icon.svg``). Resolved at runtime via
   :func:`get_plugin_icon_path`. Preferred for new plugins; served
   by ``GET /api/schemas/nodes/{type}/icon`` (see ``routers/schemas.py``).

2. **``visuals.json``** for emoji entries and library-icon
   (``lobehub:<brand>``) entries that don't map to a file. Resolved
   via :func:`get_icon` / :func:`get_color`.

``BaseNode._metadata_dict`` checks the per-folder path first and
falls back to ``visuals.json``. The frontend resolver dispatches by
the wire-format prefix (URL paths route to ``<img>``; ``asset:``,
``lobehub:``, emoji each have their own branch).

Adding a new node:
- Drop ``icon.svg`` into the plugin folder, OR
- Add an entry to ``visuals.json`` (emoji or ``lobehub:<brand>``).

Node files do NOT declare ``icon`` or ``color`` themselves.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Dict, Optional


_VISUALS_PATH = Path(__file__).resolve().parent / "visuals.json"


def _load() -> Dict[str, Dict[str, str]]:
    if not _VISUALS_PATH.exists():
        return {}
    with _VISUALS_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return {}
    return data


# Loaded once at import; the JSON is small (<5 KB) and editing it
# requires a backend restart to refresh, same as any other node-spec
# metadata change.
_VISUALS: Dict[str, Dict[str, str]] = _load()


def get_icon(node_type: str) -> str:
    """Return the registered icon for ``node_type`` or empty string.

    Icon strings follow the same wire format the frontend's
    ``resolveIcon`` understands: emoji, ``asset:<key>``, or
    ``lobehub:<brand>``.
    """
    entry = _VISUALS.get(node_type)
    if not entry:
        return ""
    return str(entry.get("icon", ""))


def get_color(node_type: str) -> str:
    """Return the registered color for ``node_type`` or empty string.

    Color strings are arbitrary CSS color literals — the canvas node
    components apply them as-is to gradients, borders, and badges.

    Falls back to ``visuals.json`` for legacy entries that haven't been
    migrated to per-plugin ``meta.json`` yet (F2 cleanup of the plugin
    authoring RFC).
    """
    entry = _VISUALS.get(node_type)
    if not entry:
        return ""
    return str(entry.get("color", ""))


def get_plugin_meta(node_type: str, key: Optional[str] = None) -> Optional[dict | str]:
    """Read the plugin's co-located ``meta.json`` file.

    Same folder-resolution path as :func:`get_plugin_icon_path` — uses
    :func:`inspect.getfile` on the plugin class to locate the folder,
    then loads ``meta.json`` if present.

    Returns the value at ``key`` (str) when given, the whole dict when
    ``key`` is ``None``, or ``None`` when the file or key is absent.
    Callers fall back to :func:`get_color` / other ``visuals.json`` keys
    for legacy entries.

    Per RFC §6.2 / F2 of the deferred follow-ups: ``meta.json`` mirrors
    ``icon.svg`` co-location so a plugin's entire visual surface area
    lives in one folder. The previous central ``visuals.json`` color
    map remains as a transitional fallback for entries without a
    per-plugin ``meta.json``.
    """
    from services.node_registry import get_node_class

    cls = get_node_class(node_type)
    if cls is None:
        return None
    try:
        plugin_dir = Path(inspect.getfile(cls)).resolve().parent
    except (TypeError, OSError):
        return None
    meta_path = plugin_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        with meta_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if key is None:
        return data
    value = data.get(key)
    return None if value is None else str(value)


def get_plugin_icon_path(node_type: str, variant: str = "light") -> Optional[Path]:
    """Return the on-disk path to a plugin's co-located ``icon.svg``.

    ``variant="dark"`` looks for ``icon.dark.svg`` first and falls back
    to ``icon.svg`` if the dark variant is missing.

    Resolution:
    1. Look up the plugin class via :func:`services.node_registry.get_node_class`.
    2. Resolve the class's source file via ``inspect.getfile``.
    3. The plugin folder is the file's parent directory — equally
       correct for single-file plugins (``server/nodes/tool/calc.py``
       → parent ``server/nodes/tool/``) and self-contained-folder
       plugins (``server/nodes/telegram/telegram_send.py`` → parent
       ``server/nodes/telegram/``).
    4. Return ``<plugin_dir>/icon.svg`` (or dark variant) if it exists.

    Returns ``None`` when the type is unknown or no ``icon.svg`` is
    present — caller falls back to :func:`get_icon` (visuals.json).
    """
    # Local import to avoid a top-level circular dep (node_registry
    # itself doesn't import _visuals, but plugin modules import both).
    from services.node_registry import get_node_class

    cls = get_node_class(node_type)
    if cls is None:
        return None
    try:
        plugin_dir = Path(inspect.getfile(cls)).resolve().parent
    except (TypeError, OSError):
        return None

    if variant == "dark":
        dark = plugin_dir / "icon.dark.svg"
        if dark.exists():
            return dark
    icon = plugin_dir / "icon.svg"
    return icon if icon.exists() else None


def get_skill(node_type: str) -> str:
    """Return the teaching skill folder name registered for ``node_type``.

    Many tool / utility nodes have a paired skill in ``server/skills/``
    that documents how an AI agent should use them. The ``skill`` field
    in ``visuals.json`` is the reverse lookup consumed by
    ``services.auto_skill`` to decide what to do when a tool node is
    connected to an AI agent.
    """
    entry = _VISUALS.get(node_type)
    if not entry:
        return ""
    return str(entry.get("skill", ""))
