"""Compatibility migrations for persisted workflow graphs."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from constants import AI_AGENT_TYPES, ANDROID_SERVICE_NODE_TYPES


def normalize_legacy_android_toolkit(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    node_parameters: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """Replace legacy ``service -> androidTool -> agent`` graphs.

    The migration is pure and idempotent.  A service is connected directly
    to every valid agent formerly targeted by its toolkit. Existing direct
    edges win, and orphaned toolkits are removed with a warning.
    """
    params = dict(node_parameters or {})
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    toolkit_ids = {
        node_id for node_id, node in node_by_id.items() if node.get("type") == "androidTool"
    }
    if not toolkit_ids:
        return list(nodes), list(edges), params, []

    incoming: Dict[str, List[str]] = {node_id: [] for node_id in toolkit_ids}
    outgoing: Dict[str, List[str]] = {node_id: [] for node_id in toolkit_ids}
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if target in toolkit_ids:
            source_node = node_by_id.get(source, {})
            if source_node.get("type") in ANDROID_SERVICE_NODE_TYPES:
                incoming[target].append(source)
        if source in toolkit_ids:
            target_node = node_by_id.get(target, {})
            if target_node.get("type") in AI_AGENT_TYPES:
                outgoing[source].append(target)

    migrated_edges = [
        dict(edge)
        for edge in edges
        if edge.get("source") not in toolkit_ids and edge.get("target") not in toolkit_ids
    ]
    direct_pairs = {
        (edge.get("source"), edge.get("target"))
        for edge in migrated_edges
        if (edge.get("targetHandle") or edge.get("target_handle")) == "input-tools"
    }
    warnings: List[str] = []
    for toolkit_id in sorted(toolkit_ids):
        agents = list(dict.fromkeys(outgoing[toolkit_id]))
        services = list(dict.fromkeys(incoming[toolkit_id]))
        if not agents:
            warnings.append(
                f"Removed legacy androidTool '{toolkit_id}' without a valid destination agent"
            )
            continue
        for service_id in services:
            for agent_id in agents:
                if (service_id, agent_id) in direct_pairs:
                    continue
                migrated_edges.append(
                    {
                        "id": f"migrated-{service_id}-{agent_id}",
                        "source": service_id,
                        "target": agent_id,
                        "sourceHandle": "output-main",
                        "targetHandle": "input-tools",
                    }
                )
                direct_pairs.add((service_id, agent_id))

    migrated_nodes = [dict(node) for node in nodes if node.get("id") not in toolkit_ids]
    for toolkit_id in toolkit_ids:
        params.pop(toolkit_id, None)
    return migrated_nodes, migrated_edges, params, warnings
