"""Unified NodeSpec assembly.

A NodeSpec is the wire contract between the editor and the backend's
node registry — everything the editor needs to render a node into the
canvas, palette, and parameter panel:

    {
      "type": "...",
      "displayName": "...",
      "icon": "...",
      "group": ["..."],
      "version": 1,
      "inputs": <JSON Schema 7>,    # input parameter schema (Wave 6 Phase 1)
      "outputs": <JSON Schema 7>,   # runtime output shape (Wave 3)
      "credentials": ["..."],       # provider keys, derived from handler
      "uiHints": {...},             # opt-in panel/widget hints
    }

This module is the single point that fuses the three sources of truth:
    services/node_input_schemas.NODE_INPUT_MODELS
    services/node_output_schemas.NODE_OUTPUT_SCHEMAS
    models/node_metadata.NODE_METADATA

Wave 6 Phase 1 — see C:\\Users\\Tgroh\\.claude\\plans\\typed-splashing-crown.md.
"""

import hashlib
import json
from typing import Any, Optional

from models.node_metadata import fallback_metadata, get_node_metadata
from services.node_input_schemas import (
    NODE_INPUT_MODELS,
    get_node_input_schema,
)
from services.node_output_schemas import (
    NODE_OUTPUT_SCHEMAS,
    get_node_output_schema,
)


_spec_cache: dict[str, dict[str, Any]] = {}
_revision_cache: Optional[str] = None


def get_node_spec(node_type: str) -> Optional[dict[str, Any]]:
    """Return the full NodeSpec for a node type, or None if neither an
    input model, an output schema, nor plugin metadata is registered
    (i.e. unknown type). Cached per-process; bust by restarting the
    server.

    Wave 10.G.3: metadata-only plugin nodes (e.g. calculatorTool and
    the other schema-less tool nodes) emit a spec even without input
    schema — their NodeSpec carries componentKind + handles + color,
    which is everything the editor needs to render and connect them.
    """

    if node_type in _spec_cache:
        return _spec_cache[node_type]

    inputs = get_node_input_schema(node_type)
    outputs = get_node_output_schema(node_type)
    meta = get_node_metadata(node_type)
    if inputs is None and outputs is None and meta is None:
        return None
    if meta is None:
        meta = fallback_metadata(node_type)
    spec: dict[str, Any] = {
        "type": node_type,
        "displayName": meta.get("displayName") or node_type,
        "icon": meta.get("icon", ""),
        "group": meta.get("group", []),
        "description": meta.get("description", ""),
        "version": meta.get("version", 1),
    }
    if inputs is not None:
        spec["inputs"] = inputs
    if outputs is not None:
        spec["outputs"] = outputs
    if "subtitle" in meta:
        spec["subtitle"] = meta["subtitle"]
    if meta.get("uiHints"):
        spec["uiHints"] = meta["uiHints"]

    # Wave 10.A — full visual contract. Only emit fields when seeded so the
    # wire format stays compact and unseeded types keep the pre-10 shape.
    for key in ("color", "componentKind", "handles", "credentials", "hideOutputHandle", "hideInputHandle", "visibility"):
        if key in meta:
            spec[key] = meta[key]

    _spec_cache[node_type] = spec
    return spec


def list_node_types_with_spec() -> list[str]:
    """Stable sorted list of every node type the backend knows about —
    either via an input model, an output schema, or a `register_node()`
    metadata entry. The editor uses this on boot to prefetch every spec
    (including visual-only nodes like `simpleMemory` that have metadata
    but no Pydantic schemas)."""

    from models.node_metadata import NODE_METADATA

    return sorted(set(NODE_INPUT_MODELS.keys()) | set(NODE_OUTPUT_SCHEMAS.keys()) | set(NODE_METADATA.keys()))


def node_spec_revision() -> str:
    """Content hash of the full NodeSpec catalogue.

    The frontend persists NodeSpec entries in localStorage with
    ``staleTime: Infinity``; without a revision the persisted cache
    survives backend deploys that change icons, uiHints, or handles
    (e.g. masterSkill gaining ``hideInputSection``). The revision is
    sent alongside ``list_node_specs`` so the editor can detect drift
    on connect and evict stale entries before refetching.

    Computed once per process and cached — restart busts. Hashes the
    fully-assembled spec dict for every known type, sorted, so the
    output is stable regardless of dict-iteration order. First 16 hex
    chars are plenty to avoid collisions for our scale.
    """
    global _revision_cache
    if _revision_cache is not None:
        return _revision_cache
    catalogue = {nt: get_node_spec(nt) for nt in list_node_types_with_spec()}
    payload = json.dumps(catalogue, sort_keys=True, default=str).encode("utf-8")
    _revision_cache = hashlib.sha256(payload).hexdigest()[:16]
    return _revision_cache


def list_node_groups() -> dict[str, dict[str, Any]]:
    """Per-group index for the component palette.

    Wave 10.B shape::

        {
          "agent":   {"types": [...], "label": "AI Agents",
                      "icon": "🤖", "color": "#bd93f9",
                      "visibility": "normal"},
          "model":   {"types": [...], ...},
          ...
        }

    `types` is the sorted list of node types belonging to the group
    (replaces the 34 hand-rolled `*_NODE_TYPES` arrays scattered across
    the frontend). Remaining keys come from
    ``models.node_metadata.GROUP_METADATA`` and make the palette's
    section headers fully backend-driven — icon, label, color, and
    "normal"-vs-"dev" visibility are all declared in
    ``server/nodes/groups.py``.

    When a group has nodes but no metadata seeded, the entry falls back
    to the raw key as label with no icon/color; the frontend can still
    render it safely.
    """
    from models.node_metadata import get_group_metadata

    types_by_group: dict[str, set[str]] = {}
    for node_type in list_node_types_with_spec():
        spec = get_node_spec(node_type)
        if not spec:
            continue
        for group in spec.get("group", []):
            types_by_group.setdefault(group, set()).add(node_type)

    result: dict[str, dict[str, Any]] = {}
    for group, type_set in sorted(types_by_group.items()):
        meta = get_group_metadata(group) or {}
        result[group] = {
            "types": sorted(type_set),
            "label": meta.get("label") or group,
            "icon": meta.get("icon", ""),
            "color": meta.get("color", ""),
            "visibility": meta.get("visibility", "dev"),
        }
    return result
