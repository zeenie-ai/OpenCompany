"""Shared, deterministic rules for binding graph nodes to an agent runtime."""
from __future__ import annotations
from typing import Any, Iterable, Mapping

from core.logging import get_logger

logger = get_logger(__name__)


async def node_tools(ai_service: Any, tool_info: Mapping[str, Any]) -> list:
    """The tools a connected node gives an agent, as ``(tool, config)``: the
    one ``ai_service._build_tool_from_node`` builds, or one per binding of a
    node that stands for several (``ToolNode.tool_bindings``, a custom MCP
    connector's tools). A binding's call runs with its settings over the
    node's, and its ``binding_key`` tells it apart from the node's others."""
    from services.agent_runtime import AgentToolSpec
    from services.llm.protocol import ToolDef
    from services.plugin.tool import node_tool_bindings

    try:
        bindings = await node_tool_bindings(tool_info)
    except Exception as exc:  # noqa: BLE001 - skip the node, as a tool that cannot be built
        logger.error("Could not read the tools of a connected node", node_type=tool_info.get("node_type"), error=str(exc))
        return []
    if bindings is None:
        tool, config = await ai_service._build_tool_from_node(tool_info)
        return [(tool, config)] if tool is not None else []
    node_id = tool_info.get("node_id", "")
    built = []
    for binding in bindings:
        config = {
            "node_type": tool_info.get("node_type", ""),
            "node_id": node_id,
            "parameters": {**(tool_info.get("parameters") or {}), **binding.parameters},
            "label": tool_info.get("label") or tool_info.get("node_type", ""),
            "connected_services": [],
            "binding_key": f"{node_id}:{binding.key}",
        }
        definition = ToolDef(name=binding.name, description=binding.description, parameters=binding.schema)
        built.append((AgentToolSpec(definition=definition, args_schema=None, execution=config), config))
    return built


def is_runtime_tool(cls: Any) -> bool:
    """Agents delegate; models and skill editors never bind as function tools."""
    if cls is None or (getattr(cls, "ui_hints", None) or {}).get("isMasterSkillEditor"):
        return False
    kind = getattr(cls, "component_kind", "")
    return kind in {"tool", "agent"} or (kind != "model" and bool(getattr(cls, "usable_as_tool", False)))


def unique_node_bindings(bindings: Iterable[Mapping[str, Any]], *, id_key: str = "node_id", bound: Iterable[str] = ()) -> list:
    """Remove repeated physical nodes before checking provider-visible names.

    Distinct nodes with the same function name remain for collision validation.
    Anonymous built-ins remain distinct, since no physical identity is known.
    """
    seen = set(bound)
    result = []
    for binding in bindings:
        node_id = str(binding.get(id_key) or "")
        if node_id and node_id in seen:
            continue
        if node_id:
            seen.add(node_id)
        result.append(binding)
    return result


def delegation_roster(bindings: Iterable[Mapping[str, Any]]) -> str:
    delegates = [binding for binding in bindings if str(binding.get("name") or "").startswith("delegate_to_")]
    if not delegates:
        return ""
    rows = "\n".join(f"{binding.get('node_id') or binding.get('tool_node_id')}: {binding.get('label') or binding.get('name')}" for binding in delegates)
    return "Updated connected teammates (assignee_node_id: label/tool):\n" + rows


def rebind_allowed(operation, agent_id, *, nodes=(), edges=(), operations=()):
    """A mutation only binds nodes attached to this invoking agent's tool/team input."""
    if not agent_id:
        return True  # Legacy callers lacking an invoking runtime identity.
    candidate = operation.get("minted_id") or operation.get("client_ref")
    if not candidate:
        return False
    canonical = next((node for node in nodes if node.get("id") == candidate), None)
    if canonical is not None and operation.get("node_type") != canonical.get("type"):
        return False
    all_edges = list(edges) + [item for item in operations if item.get("type") == "add_edge"]
    for edge in all_edges:
        if edge.get("source") != candidate or edge.get("target") != agent_id:
            continue
        handle = edge.get("targetHandle") or edge.get("target_handle")
        if handle in {"input-tools", "input-teammates"}:
            return True
    return False


class ParameterSnapshotDatabase:
    """Read captured node configuration while other database services stay live."""
    def __init__(self, database, parameters):
        self.database = database
        self.parameters = parameters
    def __getattr__(self, name):
        return getattr(self.database, name)
    async def get_node_parameters(self, node_id):
        if node_id in self.parameters:
            return dict(self.parameters[node_id])
        return await self.database.get_node_parameters(node_id)


def extend_runtime_graph(snapshot, saved, operations):
    """Add admitted mutation nodes/edges without adopting unrelated saved edits."""
    node_ids = {op.get("minted_id") or op.get("client_ref") for op in operations if op.get("type") == "add_node"}
    existing = {node.get("id") for node in snapshot.get("nodes", [])}
    nodes = list(snapshot.get("nodes", [])) + [node for node in saved.get("nodes", []) if node.get("id") in node_ids and node.get("id") not in existing]
    def identity(edge):
        return (edge.get("source"), edge.get("target"), edge.get("sourceHandle") or edge.get("source_handle"), edge.get("targetHandle") or edge.get("target_handle"))
    approved = {identity(op) for op in operations if op.get("type") == "add_edge"}
    known = {identity(edge) for edge in snapshot.get("edges", [])}
    edges = list(snapshot.get("edges", []))
    for edge in saved.get("edges", []):
        key = identity(edge)
        if key in approved and key not in known:
            edges.append(edge)
            known.add(key)
    return {"nodes": nodes, "edges": edges}
