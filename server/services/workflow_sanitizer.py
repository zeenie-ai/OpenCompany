"""Authoritative server-side redaction for workflow graph surfaces.

Context and Memory contents live in normalized backend tables. This sanitizer
is defense in depth for legacy/imported graphs and for any future export/log
path that receives an untrusted workflow-shaped mapping.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable, Mapping


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


_BLOCKED_EXACT = frozenset(
    {
        "memorycontent",
        "memoryjsonl",
        "lastsessionid",
        "vertexinteractionid",
        "vertexenvironmentid",
        "contextjournal",
        "contextevents",
        "contextcheckpoint",
        "contextcheckpoints",
        "providerbinding",
        "providerbindings",
        "providerstate",
        "providersession",
        "rawcontext",
        "rawjournal",
        "rawtranscript",
        "activereplay",
        "replaystate",
        "replaypayload",
        "checkpointplustail",
        "memoryitems",
        "recalledmemories",
        "recalledsecrets",
        "thoughtsignature",
        "reasoningsignature",
        "encryptedreasoning",
        "encryptedcontent",
        # webhookTrigger's expected header secret: the router reads it from
        # the saved parameters, so it never needs to ride a snapshot or a
        # Temporal input.
        "headervalue",
    }
)

_BLOCKED_CONTEXT_SUFFIXES = (
    "providerstate",
    "replaypayload",
    "journalpayload",
    "bindingpayload",
)

_SENSITIVE_EXACT = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "secret",
        "password",
        "passwd",
        "clientid",
        "clientsecret",
        "token",
        "bearertoken",
        "privatekey",
        "encryptionkey",
        "oauthtoken",
        "authtoken",
        # Header maps (httpRequest, proxyRequest, webhookResponse).
        "authorization",
        "proxyauthorization",
        "cookie",
    }
)

_SAFE_TOKEN_FIELDS = frozenset(
    {
        "maxtokens",
        "budgettokens",
        "pagetoken",
        "nextpagetoken",
        "tokencount",
        "totaltokens",
        "inputtokens",
        "outputtokens",
    }
)

_SENSITIVE_PARTS = (
    "apikey",
    "secret",
    "password",
    "privatekey",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "oauthtoken",
    "authtoken",
)

# React Flow graph persistence is intentionally narrower than its runtime node
# objects. Selection, measurements, execution status, outputs, and parameters
# are maintained elsewhere and must not be copied into workflow JSON.
_NODE_FIELDS = frozenset(
    {
        "id",
        "type",
        "position",
        "parentId",
        "parentNode",
        "extent",
        "expandParent",
        "hidden",
        "draggable",
        "selectable",
        "connectable",
        "deletable",
        "focusable",
        "dragHandle",
        "className",
        "sourcePosition",
        "targetPosition",
        "zIndex",
        "origin",
    }
)

_COMMON_NODE_DATA_FIELDS = frozenset({"label", "disabled", "condition"})
_CONTEXT_NODE_DATA_FIELDS = frozenset({"label", "disabled", "systemManaged", "agentNodeId"})

_EDGE_FIELDS = frozenset(
    {
        "id",
        "source",
        "target",
        "sourceHandle",
        "targetHandle",
        "source_handle",
        "target_handle",
        "type",
        "animated",
        "hidden",
        "selectable",
        "deletable",
        "focusable",
        "updatable",
        "label",
        "zIndex",
    }
)
_EDGE_DATA_FIELDS = frozenset({"label", "condition", "conditionLogic", "conditions", "systemManaged"})


def _blocked_key(normalized: str) -> bool:
    if normalized in _BLOCKED_EXACT:
        return True
    if normalized.endswith(_BLOCKED_CONTEXT_SUFFIXES):
        return True
    if normalized in _SAFE_TOKEN_FIELDS:
        return False
    if normalized in _SENSITIVE_EXACT:
        return True
    if any(part in normalized for part in _SENSITIVE_PARTS):
        return True
    return normalized.endswith("token")


def sanitize_runtime_payload(value: Any) -> Any:
    """Deep-copy JSON-like data while dropping runtime-only secret fields."""

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            normalized = _key(raw_key)
            if _blocked_key(normalized):
                continue
            cleaned[str(raw_key)] = sanitize_runtime_payload(raw_value)
        return cleaned
    if isinstance(value, list):
        return [sanitize_runtime_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_runtime_payload(item) for item in value]
    return deepcopy(value)


def restore_dropped(target: Mapping[str, Any], saved: Mapping[str, Any], *, keep: Iterable[str] = ()) -> dict[str, Any]:
    """``target`` with what :func:`sanitize_runtime_payload` drops put back
    from ``saved``: a node's settings captured for a run carry no secrets,
    and its saved row has them. Only keys ``target`` lacks are copied, at any
    depth of nested maps (lists are left as they are); top-level keys in
    ``keep`` are left alone. Changes neither input."""
    restored = dict(target)
    keep = frozenset(keep)
    for key, value in dict(saved or {}).items():
        if key in keep:
            continue
        if key not in restored:
            if _blocked_key(_key(key)):
                restored[key] = deepcopy(value)
        elif isinstance(restored[key], Mapping) and isinstance(value, Mapping):
            restored[key] = restore_dropped(restored[key], value)
    return restored


def _sanitize_node(node: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {key: sanitize_runtime_payload(node[key]) for key in sorted(_NODE_FIELDS) if key in node}
    position = node.get("position")
    if isinstance(position, Mapping):
        cleaned["position"] = {key: deepcopy(position[key]) for key in ("x", "y") if key in position}

    raw_data = node.get("data")
    allowed_data = _CONTEXT_NODE_DATA_FIELDS if node.get("type") == "context" else _COMMON_NODE_DATA_FIELDS
    if node.get("type") == "browser":
        # Display hint only. Resource authorization uses the immutable table.
        allowed_data = allowed_data | {"private_browser_owner"}
    if isinstance(raw_data, Mapping):
        cleaned["data"] = {key: sanitize_runtime_payload(raw_data[key]) for key in sorted(allowed_data) if key in raw_data}
    else:
        cleaned["data"] = {}
    return cleaned


def _sanitize_edge(edge: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {key: sanitize_runtime_payload(edge[key]) for key in sorted(_EDGE_FIELDS) if key in edge}
    raw_data = edge.get("data")
    if isinstance(raw_data, Mapping):
        cleaned["data"] = {key: sanitize_runtime_payload(raw_data[key]) for key in sorted(_EDGE_DATA_FIELDS) if key in raw_data}
    return cleaned


def sanitize_workflow_graph(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return an export/response-safe workflow graph.

    Workflow JSON has a deliberately small schema: graph version, React Flow
    topology, and UI-only node/edge metadata. Context policy lives in the
    node-parameter table, while journals, replay payloads, provider bindings,
    Memory items, outputs, and credentials live in their dedicated stores.
    """

    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    cleaned: dict[str, Any] = {
        "nodes": [_sanitize_node(node) for node in raw_nodes if isinstance(node, Mapping)] if isinstance(raw_nodes, list) else [],
        "edges": [_sanitize_edge(edge) for edge in raw_edges if isinstance(edge, Mapping)] if isinstance(raw_edges, list) else [],
    }
    if "graphVersion" in data:
        cleaned["graphVersion"] = deepcopy(data["graphVersion"])
    owner_id = data.get("owner_id")
    if isinstance(owner_id, str) and owner_id:
        # Injected by authenticated backend boundaries; required to scope
        # durable Memory namespaces without placing any Memory data in graph
        # JSON.
        cleaned["owner_id"] = owner_id
    return cleaned


__all__ = ["restore_dropped", "sanitize_runtime_payload", "sanitize_workflow_graph"]
