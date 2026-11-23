"""Building workflow graphs on the server.

What every server-side graph writer shares, so the Hire builder
(services/employees/builder.py) and additions to a saved workflow
(services/workflow_storage/mutate.py: Turn on Talk, the Agent Builder)
name, number and wire nodes the same way:

- labels unique by template key (a label lowercased with whitespace
  removed; ``{{key.field}}`` reads that node's output), taken as "X",
  "X 2", "X 3", ...;
- canonical node ids, ``<workflow_id>:<type>:<n>`` counted per type, the
  form ``services.workflow_naming.canonicalize_node_ids`` keeps;
- edges in the saved-graph shape, with the handles each kind of connection
  uses: main flow, tool, skill and Context;
- a batch of additions to an existing graph (``GraphAdditions``) and the
  step that places it (``add_to_graph``).

Pure and generic: no I/O, and it never imports ``nodes/``.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from constants import WORKFLOW_TRIGGER_TYPES
from services.parameter_resolver import TEMPLATE_PATTERN
from services.workflow_naming import canonicalize_node_ids, node_label_slug

MAIN_OUTPUT = "output-main"
MAIN_INPUT = "input-main"
TOOL_OUTPUT = "output-tool"
TOOLS_INPUT = "input-tools"
SKILL_INPUT = "input-skill"
CONTEXT_OUTPUT = "output-context"
CONTEXT_INPUT = "input-context"
CONTEXT_TYPE = "context"


def label_key(label: str) -> str:
    """The template key of a node label (services/parameter_resolver.py)."""
    return re.sub(r"\s+", "", label.lower())


def template_key(node: Mapping[str, Any]) -> str:
    """The key templates read a node's output under: its label's, else its
    display name's, type's or id's (``ParameterResolver._get_template_key``)."""
    data = node.get("data")
    data = data if isinstance(data, Mapping) else {}
    return label_key(str(data.get("label") or data.get("displayName") or node.get("type") or node.get("id") or ""))


def ref(key: str, field_name: str) -> str:
    return "{{" + f"{key}.{field_name}" + "}}"


class Labels:
    """Unique labels, so no two nodes share a template key (they would read
    each other's output). Seed it with the labels, or template keys, a
    graph already uses."""

    def __init__(self, taken: Iterable[str] = ()) -> None:
        self._keys: Set[str] = {label_key(label) for label in taken}

    def take(self, label: str, *, clashes: Optional[Callable[[str], bool]] = None) -> str:
        """``label``, else "``label`` 2", "``label`` 3", ...: the first whose
        key is free and that ``clashes`` does not reject."""
        candidate, n = label, 2
        while label_key(candidate) in self._keys or (clashes is not None and clashes(candidate)):
            candidate = f"{label} {n}"
            n += 1
        self._keys.add(label_key(candidate))
        return candidate


class NodeIds:
    """Canonical node ids, ``<workflow_id>:<type>:<n>``, counted per type.

    Seeded with a graph's nodes, it numbers new ones past what
    canonicalizing that graph gives each existing node, so a later save
    (which canonicalizes) keeps every id, even beside nodes that still
    carry an older id form. Node types are plugin identifiers."""

    def __init__(self, workflow_id: str, nodes: Iterable[Mapping[str, Any]] = ()) -> None:
        self._prefix = f"{workflow_id}:"
        self._counts: Dict[str, int] = {}
        canonical, _edges, _aliases = canonicalize_node_ids(workflow_id, [dict(node) for node in nodes], [])
        for node in canonical:
            node_type, _, n = str(node["id"])[len(self._prefix) :].rpartition(":")
            self._counts[node_type] = max(self._counts.get(node_type, 0), int(n))

    def next(self, node_type: str) -> str:
        n = self._counts.get(node_type, 0) + 1
        self._counts[node_type] = n
        return f"{self._prefix}{node_type}:{n}"


def graph_node(
    node_id: str, node_type: str, label: str, position: Tuple[float, float], data: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """A node in the saved-graph shape."""
    return {"id": node_id, "type": node_type, "position": {"x": position[0], "y": position[1]}, "data": {"label": label, **(data or {})}}


def context_data(agent: str) -> Dict[str, Any]:
    """A Context node's link to the agent it keeps the conversation of."""
    return {"systemManaged": True, "agentNodeId": agent}


@dataclass(frozen=True)
class Edge:
    """``source``'s ``source_handle`` to ``target``'s ``target_handle``,
    followed only when ``condition`` holds (the runtime reads
    ``data.condition``)."""

    source: str
    source_handle: str
    target: str
    target_handle: str
    condition: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """The saved-graph shape. The id names both ends and both handles,
        so one connection always has one id."""
        edge: Dict[str, Any] = {
            "id": f"e-{self.source}-{self.source_handle}-{self.target}-{self.target_handle}",
            "source": self.source,
            "sourceHandle": self.source_handle,
            "target": self.target,
            "targetHandle": self.target_handle,
        }
        if self.condition:
            edge["data"] = {"condition": dict(self.condition)}
        return edge


def main_edge(source: str, target: str, condition: Optional[Mapping[str, Any]] = None) -> Edge:
    return Edge(source, MAIN_OUTPUT, target, MAIN_INPUT, condition)


def tool_edge(tool: str, agent: str) -> Edge:
    return Edge(tool, TOOL_OUTPUT, agent, TOOLS_INPUT)


def skill_edge(skills: str, agent: str) -> Edge:
    return Edge(skills, TOOL_OUTPUT, agent, SKILL_INPUT)


def context_edge(context: str, agent: str) -> Edge:
    return Edge(context, CONTEXT_OUTPUT, agent, CONTEXT_INPUT)


def merge_params(current: Mapping[str, Any], patch: Mapping[str, Any]) -> Dict[str, Any]:
    """``patch`` merged into ``current``: mappings merge key by key at any
    depth, any other value replaces. Neither argument changes."""
    merged = deepcopy(dict(current))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = merge_params(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


# ----- additions to an existing graph -----


@dataclass(frozen=True)
class NewNode:
    """A node to add. ``ref`` names it within its batch: edges, merges and
    ``context_of`` point at it by ref, and the result maps each ref to the
    id and label it got. ``label`` is the one wanted; a label the graph
    already uses gets a number ("Talk 2")."""

    ref: str
    type: str
    label: str
    params: Mapping[str, Any] = field(default_factory=dict)
    position: Tuple[float, float] = (0, 0)
    #: Makes this node (type ``context``) the Context of that agent, a ref
    #: or an existing node's id: linked the way Hire links one, and wired.
    context_of: Optional[str] = None


@dataclass(frozen=True)
class ParamMerge:
    """Parameters merged (``merge_params``) into a node's saved row: an
    existing node's id, or a ref."""

    node: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class EdgeRemoval:
    """An edge of the graph to take out: from ``source`` into ``target``'s
    ``target_handle`` (existing node ids). One a new node takes the place
    of (a gate between an agent and its reply)."""

    source: str
    target: str
    target_handle: str = MAIN_INPUT


@dataclass(frozen=True)
class GraphAdditions:
    """What to add to a saved graph, planned against the graph as it was
    read. Edge ends are refs or existing node ids; an edge the graph already
    has (same ends and handles) is skipped. Templates in the batch's
    parameters name the wanted labels' keys (``ref(label_key(label),
    field)``); when a label was taken in the meantime and gets a number,
    they follow it (unless two new nodes wanted that label).
    ``removed_edges`` are taken out first; one the graph no longer has is
    skipped."""

    nodes: Sequence[NewNode] = ()
    edges: Sequence[Edge] = ()
    merges: Sequence[ParamMerge] = ()
    removed_edges: Sequence[EdgeRemoval] = ()

    def __bool__(self) -> bool:
        return bool(self.nodes or self.edges or self.merges or self.removed_edges)


@dataclass(frozen=True)
class PlacedAdditions:
    """``add_to_graph``'s result."""

    #: The graph with the new nodes and edges appended.
    graph: Dict[str, Any]
    node_ids: Dict[str, str]
    labels: Dict[str, str]
    #: What was appended, in the saved-graph shape.
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    #: New node id -> its parameters, for a fresh row.
    parameters: Dict[str, Dict[str, Any]]
    #: Node id -> parameters to merge into its row.
    merges: Dict[str, Dict[str, Any]]
    #: The edges taken out, in the saved-graph shape.
    removed_edges: List[Dict[str, Any]] = field(default_factory=list)


def _edge_identity(edge: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(edge.get("source") or ""),
        str(edge.get("sourceHandle") or edge.get("source_handle") or ""),
        str(edge.get("target") or ""),
        str(edge.get("targetHandle") or edge.get("target_handle") or ""),
    )


def _label_slug(node_type: str, label: str) -> str:
    return node_label_slug({"type": node_type, "data": {"label": label}})


def repoint_template_labels(value: Any, renamed: Mapping[str, str]) -> Any:
    """``value`` with every ``{{key...}}`` whose key was renamed pointed at
    the new key (keys compared the way the resolver compares them)."""
    if isinstance(value, Mapping):
        return {key: repoint_template_labels(item, renamed) for key, item in value.items()}
    if isinstance(value, list):
        return [repoint_template_labels(item, renamed) for item in value]
    if not renamed or not isinstance(value, str) or "{{" not in value:
        return deepcopy(value)

    def swap(match: "re.Match[str]") -> str:
        head, dot, rest = match.group(1).partition(".")
        key = renamed.get(head.lower())
        return match.group(0) if key is None else "{{" + key + dot + rest + "}}"

    return TEMPLATE_PATTERN.sub(swap, value)


def add_to_graph(workflow_id: str, graph: Mapping[str, Any], additions: GraphAdditions) -> PlacedAdditions:
    """Place ``additions`` in ``graph`` (a saved ``workflow.data``): ids
    numbered past the graph's, labels unique against it (a new trigger's
    also unique by ``node_label_slug`` among triggers, since a deployment
    registers one listener per trigger label slug). ``graph`` is not
    changed. Raises ValueError for a batch that does not fit the graph: a
    ref reused or unknown, a Context companion that is not a Context, or an
    agent given a second Context."""
    nodes = list(graph.get("nodes") or [])
    removing = {(removal.source, removal.target, removal.target_handle) for removal in additions.removed_edges}
    edges: List[Any] = []
    removed_edges: List[Dict[str, Any]] = []
    for edge in graph.get("edges") or []:
        if isinstance(edge, Mapping):
            source, _handle, target, target_handle = _edge_identity(edge)
            if (source, target, target_handle or MAIN_INPUT) in removing:
                removed_edges.append(dict(edge))
                continue
        edges.append(edge)
    existing = [node for node in nodes if isinstance(node, Mapping)]
    existing_ids = {str(node.get("id")) for node in existing if node.get("id")}
    labels = Labels(template_key(node) for node in existing)
    trigger_slugs = {node_label_slug(dict(node)) for node in existing if node.get("type") in WORKFLOW_TRIGGER_TYPES}
    ids = NodeIds(workflow_id, existing)
    wanted = Counter(label_key(new.label) for new in additions.nodes)

    node_ids: Dict[str, str] = {}
    taken_labels: Dict[str, str] = {}
    renamed: Dict[str, str] = {}
    for new in additions.nodes:
        if not new.ref or not new.type or not new.label.strip():
            raise ValueError("a new node needs a ref, a type and a label")
        if new.ref in node_ids or new.ref in existing_ids:
            raise ValueError(f"ref {new.ref!r} is already used")
        if new.context_of is not None and new.type != CONTEXT_TYPE:
            raise ValueError(f"{new.ref!r} is a Context companion but not a {CONTEXT_TYPE!r} node")
        if new.type in WORKFLOW_TRIGGER_TYPES:
            label = labels.take(new.label, clashes=lambda candidate, node_type=new.type: _label_slug(node_type, candidate) in trigger_slugs)
            trigger_slugs.add(_label_slug(new.type, label))
        else:
            label = labels.take(new.label)
        if label != new.label and wanted[label_key(new.label)] == 1:
            renamed[label_key(new.label)] = label_key(label)
        node_ids[new.ref] = ids.next(new.type)
        taken_labels[new.ref] = label

    def resolve(name: str) -> str:
        if name in node_ids:
            return node_ids[name]
        if name in existing_ids:
            return name
        raise ValueError(f"{name!r} is neither a ref in the batch nor a node in the graph")

    known = {_edge_identity(edge) for edge in edges if isinstance(edge, Mapping)}
    with_context = {target for _source, _handle, target, handle in known if handle == CONTEXT_INPUT}
    placed_nodes: List[Dict[str, Any]] = []
    parameters: Dict[str, Dict[str, Any]] = {}
    wiring: List[Edge] = []
    for new in additions.nodes:
        data = None
        if new.context_of is not None:
            agent = resolve(new.context_of)
            if agent in with_context:
                raise ValueError(f"{agent!r} already has a Context")
            with_context.add(agent)
            data = context_data(agent)
            wiring.append(context_edge(new.ref, new.context_of))
        node_id = node_ids[new.ref]
        placed_nodes.append(graph_node(node_id, new.type, taken_labels[new.ref], new.position, data))
        parameters[node_id] = repoint_template_labels(dict(new.params), renamed)
        # Manager bundles can create the employee and its completion listener
        # together. Persist the minted employee ID in the listener's filter.
        if new.type == "taskTrigger" and parameters[node_id].get("parent_node_id") in node_ids:
            parameters[node_id]["parent_node_id"] = node_ids[parameters[node_id]["parent_node_id"]]

    placed_edges: List[Dict[str, Any]] = []
    for edge in [*wiring, *additions.edges]:
        wired = Edge(resolve(edge.source), edge.source_handle, resolve(edge.target), edge.target_handle, edge.condition).to_dict()
        identity = _edge_identity(wired)
        if identity not in known:
            known.add(identity)
            placed_edges.append(wired)

    merges: Dict[str, Dict[str, Any]] = {}
    for merge in additions.merges:
        node_id = resolve(merge.node)
        merges[node_id] = merge_params(merges.get(node_id, {}), repoint_template_labels(dict(merge.params), renamed))

    return PlacedAdditions(
        graph={**graph, "nodes": [*nodes, *placed_nodes], "edges": [*edges, *placed_edges]},
        node_ids=node_ids,
        labels=taken_labels,
        nodes=placed_nodes,
        edges=placed_edges,
        parameters=parameters,
        merges=merges,
        removed_edges=removed_edges,
    )


__all__ = [
    "CONTEXT_INPUT",
    "CONTEXT_OUTPUT",
    "CONTEXT_TYPE",
    "Edge",
    "EdgeRemoval",
    "GraphAdditions",
    "Labels",
    "MAIN_INPUT",
    "MAIN_OUTPUT",
    "NewNode",
    "NodeIds",
    "ParamMerge",
    "PlacedAdditions",
    "SKILL_INPUT",
    "TOOLS_INPUT",
    "TOOL_OUTPUT",
    "add_to_graph",
    "context_data",
    "context_edge",
    "graph_node",
    "label_key",
    "main_edge",
    "merge_params",
    "ref",
    "skill_edge",
    "template_key",
    "tool_edge",
]
