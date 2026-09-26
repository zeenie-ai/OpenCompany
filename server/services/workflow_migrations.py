"""Versioned, idempotent compatibility migrations for workflow graphs.

Graph normalization is deliberately pure.  Durable Context import happens
after canonical node IDs have been assigned and is driven by the returned
``state_imports`` receipts.  This keeps topology normalization safe for
editor previews while allowing persistence boundaries to commit topology
and imported runtime state transactionally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from constants import AI_AGENT_TYPES, ANDROID_SERVICE_NODE_TYPES


WORKFLOW_GRAPH_VERSION = 2


@dataclass(frozen=True)
class WorkflowGraphNormalization:
    """Result of the V2 graph migration pipeline."""

    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    node_parameters: Dict[str, Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    state_imports: List[Dict[str, Any]] = field(default_factory=list)
    graph_version: int = WORKFLOW_GRAPH_VERSION

    def graph_data(self, original: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return {
            **dict(original or {}),
            "graphVersion": self.graph_version,
            "nodes": self.nodes,
            "edges": self.edges,
        }


def _target_handle(edge: Mapping[str, Any]) -> Optional[str]:
    value = edge.get("targetHandle") or edge.get("target_handle")
    return str(value) if value else None


def _source_handle(edge: Mapping[str, Any]) -> Optional[str]:
    value = edge.get("sourceHandle") or edge.get("source_handle")
    return str(value) if value else None


def _context_node_for(agent: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    position = dict(agent.get("position") or {})
    x = position.get("x")
    y = position.get("y")
    context_position: Dict[str, Any] = {}
    if isinstance(x, (int, float)):
        context_position["x"] = x - 360
    if isinstance(y, (int, float)):
        context_position["y"] = y
    return {
        "id": node_id,
        "type": "context",
        "position": context_position,
        "data": {"label": "Context"},
    }


def _edge_id(prefix: str, source: str, target: str) -> str:
    safe_source = source.replace(":", "-")
    safe_target = target.replace(":", "-")
    return f"{prefix}-{safe_source}-{safe_target}"


def normalize_edge_handles(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Canonicalize legacy ReactFlow handle keys without changing topology."""
    normalized: List[Dict[str, Any]] = []
    for edge in edges:
        item = dict(edge)
        target_handle = item.pop("target_handle", None)
        source_handle = item.pop("source_handle", None)
        if not item.get("targetHandle") and target_handle:
            item["targetHandle"] = target_handle
        if not item.get("sourceHandle") and source_handle:
            item["sourceHandle"] = source_handle
        normalized.append(item)
    return normalized


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
    toolkit_ids = {node_id for node_id, node in node_by_id.items() if node.get("type") == "androidTool"}
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

    migrated_edges = [dict(edge) for edge in edges if edge.get("source") not in toolkit_ids and edge.get("target") not in toolkit_ids]
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
            warnings.append(f"Removed legacy androidTool '{toolkit_id}' without a valid destination agent")
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


#: Browser node types retired in favour of the single ``browser`` node.
LEGACY_BROWSER_NODE_TYPES = frozenset({"browserHarness"})

#: Runtime settings of the retired agent-browser node. The new node launches
#: its own Chrome, so none of them has a meaning any more.
_LEGACY_BROWSER_RUNTIME_KEYS = (
    "session",
    "browser",
    "executable_path",
    "headed",
    "new_window",
    "auto_connect",
    "chrome_profile",
    "user_agent",
    "proxy",
    "action_delay",
    "annotate",
    "commands",
    "screenshot_quality",
    "screenshot_format",
    "value",
    "timeout",
)
#: Dropped settings worth telling the user about when they held a value.
_LEGACY_BROWSER_MEANINGFUL = ("chrome_profile", "proxy", "executable_path", "user_agent")


def migrate_legacy_browser_params(
    params: Mapping[str, Any], *, legacy_type: str = "browser"
) -> Tuple[Dict[str, Any], List[str]]:
    """Map one browser node's saved parameters onto the current ``browser`` node.

    Pure and idempotent: parameters already in the current shape come back
    unchanged. Covers the agent-browser ``browser`` node (operation names,
    ``value``, ``timeout``, runtime settings) and ``browserHarness``.
    """
    out = dict(params or {})
    warnings: List[str] = []
    op = str(out.get("operation") or "")

    if legacy_type == "browserHarness":
        timeout = out.pop("timeout", None)
        if op == "goto":
            out["operation"] = "navigate"
        elif op == "js":
            out["operation"] = "evaluate"
        elif op == "tabs":
            out["operation"], out["tab_action"] = "tabs", "list"
        elif op == "doctor":
            out["operation"] = "diagnose"
        elif op == "run_python":
            warnings.append("browserHarness run_python code was kept, but helper names changed; check the script")
        if isinstance(timeout, (int, float)) and "op_timeout_s" not in out:
            out["op_timeout_s"] = max(5, min(300, int(timeout)))
        return out, warnings

    value = out.get("value")
    selector = str(out.get("selector") or "")
    if op == "fill":
        out.update(operation="type", text=str(value or out.get("text") or ""), clear=True)
    elif op == "type":
        out.setdefault("clear", False)
    elif op == "get_text":
        out["operation"] = "page_text"
    elif op == "get_html":
        out.update(operation="evaluate", expression=f"document.querySelector({selector!r})?.outerHTML ?? null" if selector else "document.documentElement.outerHTML")
    elif op == "eval":
        out["operation"] = "evaluate"
    elif op == "wait" and selector and "wait_for" not in out:
        out.update(wait_for="selector", wait_value=selector)
    elif op == "select" and value not in (None, "") and not out.get("values"):
        out["values"] = [str(value)]
    elif op in ("console", "errors"):
        out["operation"] = "page_info"
        warnings.append(f"the '{op}' browser operation no longer exists; it was changed to page_info")
    elif op == "batch":
        commands = str(out.get("commands") or "[]")
        out.update(
            operation="run_python",
            code="# The agent-browser batch below does not run on the new browser.\n"
            + "".join(f"# {line}\n" for line in commands.splitlines())
            + 'raise RuntimeError("rewrite this batch as browser operations")\n',
        )
        warnings.append("an agent-browser batch cannot run on the new browser; it was kept as a comment in run_python")

    timeout = out.get("timeout")
    if isinstance(timeout, (int, float)) and "op_timeout_s" not in out:
        out["op_timeout_s"] = max(5, min(300, int(timeout)))
    for key in _LEGACY_BROWSER_MEANINGFUL:
        if str(out.get(key) or "").strip():
            warnings.append(f"the browser setting '{key}' no longer exists and was dropped")
    for key in _LEGACY_BROWSER_RUNTIME_KEYS:
        out.pop(key, None)
    return out, warnings


def normalize_legacy_browser_nodes(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    node_parameters: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """Rewrite retired browser nodes into the current ``browser`` node.

    ``browserHarness`` becomes ``browser`` and every browser node's saved
    parameters go through :func:`migrate_legacy_browser_params`. Both old
    nodes could hang off one agent under different tool names; now they
    would share the name ``browser``, so the migrated node's tool edge is
    dropped (with a warning) when that agent already has a browser tool.
    Pure and idempotent.
    """
    params = {str(k): dict(v or {}) for k, v in (node_parameters or {}).items()}
    warnings: List[str] = []
    migrated_ids: set = set()
    out_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        node_type = node.get("type")
        if node_type not in LEGACY_BROWSER_NODE_TYPES and node_type != "browser":
            out_nodes.append(node)
            continue
        node_id = str(node.get("id") or "")
        new_params, notes = migrate_legacy_browser_params(params.get(node_id, {}), legacy_type=str(node_type))
        if node_id in params or new_params:
            params[node_id] = new_params
        warnings.extend(f"{node_id}: {note}" for note in notes)
        if node_type in LEGACY_BROWSER_NODE_TYPES:
            migrated_ids.add(node_id)
            node = {**node, "type": "browser"}
            data = node.get("data")
            if isinstance(data, dict) and data.get("type") in LEGACY_BROWSER_NODE_TYPES:
                node["data"] = {**data, "type": "browser"}
        out_nodes.append(node)

    if not migrated_ids:
        return out_nodes, list(edges), params, warnings

    browser_ids = {str(n.get("id")) for n in out_nodes if n.get("type") == "browser"}
    kept_by_agent: Dict[str, str] = {}
    out_edges: List[Dict[str, Any]] = []
    # Keep an agent's original browser edge first, then drop migrated duplicates.
    ordered = sorted(edges, key=lambda e: str(e.get("source") or "") in migrated_ids)
    for edge in ordered:
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source in browser_ids and _target_handle(edge) == "input-tools":
            if target in kept_by_agent and kept_by_agent[target] != source:
                warnings.append(f"{source}: removed its tool connection to {target}, which already has a browser tool")
                continue
            kept_by_agent.setdefault(target, source)
        out_edges.append(edge)
    return out_nodes, out_edges, params, warnings


def normalize_workflow_graph(
    workflow_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    node_parameters: Optional[Mapping[str, Dict[str, Any]]] = None,
    *,
    canonicalize_ids: bool = True,
) -> WorkflowGraphNormalization:
    """Normalize a graph to the Context topology.

    The transform is idempotent and preserves unknown edges for validation.
    It performs the following ordered stages:

    1. canonicalize legacy handle field names;
    2. migrate the retired Android toolkit;
    3. convert every legacy Memory continuity edge into an ordinary Memory
       tool edge plus a Context edge, adding a Context node for an agent
       that has none;
    4. assign canonical node IDs and return aliases/import receipts.

    Context nodes are otherwise left exactly as the user placed them. A
    Context is an opt-in the user adds and removes on the canvas, so this
    never creates, reconnects or deletes one. The legacy edge in stage 3 is
    the one exception, because it was itself an explicit continuity
    declaration.

    Raw legacy Markdown is returned only in ``state_imports``.  It is never
    copied into the Context node or workflow graph.
    """
    normalized_edges = normalize_edge_handles(edges or [])
    normalized_nodes, normalized_edges, params, warnings = normalize_legacy_android_toolkit(
        nodes or [],
        normalized_edges,
        node_parameters,
    )
    normalized_nodes, normalized_edges, params, browser_warnings = normalize_legacy_browser_nodes(
        normalized_nodes,
        normalized_edges,
        params,
    )
    warnings = [*warnings, *browser_warnings]
    normalized_nodes = [dict(node) for node in normalized_nodes]
    normalized_edges = [dict(edge) for edge in normalized_edges]
    params = {str(node_id): dict(value or {}) for node_id, value in params.items()}

    node_by_id = {str(node.get("id")): node for node in normalized_nodes if node.get("id") is not None}
    context_ids = {node_id for node_id, node in node_by_id.items() if node.get("type") == "context"}

    legacy_by_agent: Dict[str, List[str]] = {}
    retained_edges: List[Dict[str, Any]] = []
    for edge in normalized_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        source_node = node_by_id.get(source) or {}
        if source_node.get("type") == "simpleMemory" and _target_handle(edge) == "input-memory" and target in node_by_id:
            legacy_by_agent.setdefault(target, []).append(source)
            continue
        retained_edges.append(edge)
    normalized_edges = retained_edges

    # Reconnect legacy Simple Memory nodes as normal tools. A shared Memory
    # remains shared, while each destination agent receives isolated Context.
    tool_pairs = {
        (str(edge.get("source") or ""), str(edge.get("target") or "")) for edge in normalized_edges if _target_handle(edge) == "input-tools"
    }
    for agent_id, memory_ids in sorted(legacy_by_agent.items()):
        for memory_id in dict.fromkeys(memory_ids):
            if (memory_id, agent_id) in tool_pairs:
                continue
            normalized_edges.append(
                {
                    "id": _edge_id("memory-tool", memory_id, agent_id),
                    "source": memory_id,
                    "target": agent_id,
                    "sourceHandle": "output-tool",
                    "targetHandle": "input-tools",
                }
            )
            tool_pairs.add((memory_id, agent_id))

    # Stage 3, second half: the legacy edge declared continuity, so its agent
    # keeps it. Reuse a Context the agent is already connected to, otherwise
    # add one. A legacy edge stays migratable even when the destination
    # plugin is not installed on this host.
    connected_contexts: Dict[str, str] = {}
    for edge in normalized_edges:
        source = str(edge.get("source") or "")
        if source in context_ids and _source_handle(edge) == "output-context" and _target_handle(edge) == "input-context":
            connected_contexts.setdefault(str(edge.get("target") or ""), source)

    occupied_ids = set(node_by_id)
    for ordinal, agent_id in enumerate(sorted(legacy_by_agent), start=1):
        if agent_id in connected_contexts:
            continue
        context_id = f"__context__:{ordinal}:{agent_id}"
        suffix = 1
        while context_id in occupied_ids:
            suffix += 1
            context_id = f"__context__:{ordinal}:{agent_id}:{suffix}"
        occupied_ids.add(context_id)
        normalized_nodes.append(_context_node_for(node_by_id[agent_id], context_id))
        connected_contexts[agent_id] = context_id
        normalized_edges.append(
            {
                "id": _edge_id("context", context_id, agent_id),
                "source": context_id,
                "target": agent_id,
                "sourceHandle": "output-context",
                "targetHandle": "input-context",
            }
        )

    aliases: Dict[str, str] = {}
    if canonicalize_ids and workflow_id:
        from services.workflow_naming import canonicalize_node_ids

        normalized_nodes, normalized_edges, aliases = canonicalize_node_ids(
            str(workflow_id),
            normalized_nodes,
            normalized_edges,
        )
        params = {aliases.get(node_id, node_id): value for node_id, value in params.items()}

    # Resolve agent -> Context after canonicalization so persistence can use
    # stable IDs.  Receipts are operation-id friendly and safe to replay.
    context_for_agent: Dict[str, str] = {}
    canonical_node_by_id = {str(node.get("id")): node for node in normalized_nodes if node.get("id") is not None}
    for edge in normalized_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if (
            (canonical_node_by_id.get(source) or {}).get("type") == "context"
            and _source_handle(edge) == "output-context"
            and _target_handle(edge) == "input-context"
        ):
            context_for_agent[target] = source

    state_imports: List[Dict[str, Any]] = []
    for legacy_agent_id, memory_ids in sorted(legacy_by_agent.items()):
        agent_id = aliases.get(legacy_agent_id, legacy_agent_id)
        context_id = context_for_agent.get(agent_id)
        if not context_id:
            continue
        for legacy_memory_id in dict.fromkeys(memory_ids):
            memory_id = aliases.get(legacy_memory_id, legacy_memory_id)
            legacy = params.get(memory_id, {})
            markdown = legacy.get("memory_content")
            bindings = {
                key: legacy[key]
                for key in (
                    "last_session_id",
                    "vertex_interaction_id",
                    "vertex_environment_id",
                )
                if legacy.get(key)
            }
            if markdown or bindings:
                state_imports.append(
                    {
                        "operation_id": (f"legacy-context-import:{workflow_id}:{context_id}:{memory_id}"),
                        "workflow_id": str(workflow_id),
                        "context_node_id": context_id,
                        "agent_node_id": agent_id,
                        "legacy_memory_node_id": memory_id,
                        "event_type": "legacy_partial",
                        "markdown": str(markdown) if markdown else None,
                        "legacy_session_id": str(legacy.get("session_id") or agent_id),
                        "provider_bindings": bindings,
                    }
                )
            warnings.append(
                f"Migrated legacy Simple Memory edge {memory_id!r} -> {agent_id!r}; process-local vector entries could not be imported"
            )

    return WorkflowGraphNormalization(
        nodes=normalized_nodes,
        edges=normalized_edges,
        node_parameters=params,
        warnings=warnings,
        aliases=aliases,
        state_imports=state_imports,
    )


__all__ = [
    "LEGACY_BROWSER_NODE_TYPES",
    "WORKFLOW_GRAPH_VERSION",
    "WorkflowGraphNormalization",
    "migrate_legacy_browser_params",
    "normalize_edge_handles",
    "normalize_legacy_android_toolkit",
    "normalize_legacy_browser_nodes",
    "normalize_workflow_graph",
]
