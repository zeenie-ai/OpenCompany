"""What a saved workflow graph says about the employee it is.

Normal mode lists every workflow as an employee. A hired one has a row in
``employees`` that names its parts (``node_roles``); one built in the
editor does not, so its summary is read off the graph: which nodes are
agents (whose activity the card follows live), which are triggers (what
starts it), which apps its nodes belong to, whether it waits for the
owner's approval before sending, and which canvas board the Workspace
shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from constants import AI_AGENT_TYPES, WORKFLOW_TRIGGER_TYPES
from services.employees.apps import AppSpec, app_for_node_type

TODO_NODE_TYPE = "writeTodos"
CANVAS_NODE_TYPE = "canvas"
APPROVAL_GATE_TYPE = "approvalGate"
SCHEDULE_TRIGGER_TYPE = "cronScheduler"
CHAT_TRIGGER_TYPE = "chatTrigger"


@dataclass(frozen=True)
class GraphIndex:
    #: node id -> node type, for every node with both.
    node_types: Mapping[str, str] = field(default_factory=dict)
    #: node id -> display label (``data.label``), when set.
    labels: Mapping[str, str] = field(default_factory=dict)
    agent_ids: Tuple[str, ...] = ()
    trigger_ids: Tuple[str, ...] = ()
    todo_ids: Tuple[str, ...] = ()
    canvas_ids: Tuple[str, ...] = ()
    gate_ids: Tuple[str, ...] = ()
    #: Apps the graph's nodes belong to, in first-seen order.
    app_ids: Tuple[str, ...] = ()

    @property
    def trigger_types(self) -> Tuple[str, ...]:
        return tuple(self.node_types[node_id] for node_id in self.trigger_ids)

    @property
    def has_agent(self) -> bool:
        return bool(self.agent_ids)

    def primary_trigger_app(self) -> Optional[AppSpec]:
        for trigger_type in self.trigger_types:
            app = app_for_node_type(trigger_type)
            if app is not None:
                return app
        return None


def index_graph(graph: Optional[Mapping[str, Any]]) -> GraphIndex:
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    if not isinstance(nodes, list):
        return GraphIndex()
    node_types: Dict[str, str] = {}
    labels: Dict[str, str] = {}
    agents: List[str] = []
    triggers: List[str] = []
    todos: List[str] = []
    canvases: List[str] = []
    gates: List[str] = []
    app_ids: List[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id or not isinstance(node_type, str) or not node_type:
            continue
        node_types[node_id] = node_type
        data = node.get("data")
        label = data.get("label") if isinstance(data, Mapping) else None
        if isinstance(label, str) and label.strip():
            labels[node_id] = label.strip()
        if isinstance(data, Mapping) and data.get("disabled"):
            continue
        if node_type in AI_AGENT_TYPES:
            agents.append(node_id)
        if node_type in WORKFLOW_TRIGGER_TYPES:
            triggers.append(node_id)
        if node_type == TODO_NODE_TYPE:
            todos.append(node_id)
        if node_type == CANVAS_NODE_TYPE:
            canvases.append(node_id)
        if node_type == APPROVAL_GATE_TYPE:
            gates.append(node_id)
        app = app_for_node_type(node_type)
        if app is not None and app.id not in app_ids:
            app_ids.append(app.id)
    return GraphIndex(
        node_types=node_types,
        labels=labels,
        agent_ids=tuple(agents),
        trigger_ids=tuple(triggers),
        todo_ids=tuple(todos),
        canvas_ids=tuple(canvases),
        gate_ids=tuple(gates),
        app_ids=tuple(app_ids),
    )


__all__ = [
    "APPROVAL_GATE_TYPE",
    "CANVAS_NODE_TYPE",
    "CHAT_TRIGGER_TYPE",
    "GraphIndex",
    "SCHEDULE_TRIGGER_TYPE",
    "TODO_NODE_TYPE",
    "index_graph",
]
