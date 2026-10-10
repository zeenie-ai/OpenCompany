"""What a saved workflow graph says about the employee it is.

Normal mode lists every workflow as an employee. A hired one has a row in
``employees`` that names its parts (``node_roles``); one built in the
editor does not, so its summary is read off the graph: which nodes are
agents (whose activity the card follows live), which are triggers (what
starts it), which apps its nodes belong to, whether it waits for the
owner's approval before sending, which canvas board the Workspace shows,
and, from its edges, whether the owner can talk to it (talk.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from constants import WORKFLOW_TRIGGER_TYPES
from services.workspace_capabilities import is_registered_agent
from services.employees.apps import AppSpec, app_for_node_type
from services.employees.talk import TalkState, talk_state
from services.node_registry import get_node_class

TODO_NODE_TYPE = "writeTodos"
CANVAS_NODE_TYPE = "canvas"
MEMORY_NODE_TYPE = "simpleMemory"
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
    browser_ids: Tuple[str, ...] = ()
    gate_ids: Tuple[str, ...] = ()
    #: Apps the graph's nodes belong to by their type, in first-seen order.
    app_ids: Tuple[str, ...] = ()
    #: Nodes whose app a parameter names (their type's ``app_field``): node
    #: id -> that parameter. The graph does not hold parameters, so their
    #: apps come from :meth:`apps_named`.
    app_params: Mapping[str, str] = field(default_factory=dict)
    #: Whether the owner can talk to it, and through which agent.
    talk: TalkState = field(default_factory=lambda: TalkState("unsupported"))

    @property
    def trigger_types(self) -> Tuple[str, ...]:
        return tuple(self.node_types[node_id] for node_id in self.trigger_ids)

    def apps_named(self, parameters: Mapping[str, Mapping[str, Any]]) -> Tuple[str, ...]:
        """``app_ids``, then the apps the ``app_params`` nodes name in
        ``parameters`` (node id -> saved parameters)."""
        named = (str((parameters.get(node_id) or {}).get(param) or "") for node_id, param in self.app_params.items())
        return tuple(dict.fromkeys([*self.app_ids, *(app_id for app_id in named if app_id)]))

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
    browsers: List[str] = []
    gates: List[str] = []
    app_ids: List[str] = []
    app_params: Dict[str, str] = {}
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
        # Browser viewing is available even when execution of the node is
        # disabled. Discover capabilities from plugins, never type names.
        cls = get_node_class(node_type)
        if cls is not None and (getattr(cls, "ui_hints", {}) or {}).get("isBrowserPanel") and node_id not in browsers:
            browsers.append(node_id)
        if isinstance(data, Mapping) and data.get("disabled"):
            continue
        if is_registered_agent(node_type):
            agents.append(node_id)
        if node_type in WORKFLOW_TRIGGER_TYPES:
            triggers.append(node_id)
        if node_type == TODO_NODE_TYPE:
            todos.append(node_id)
        if node_type == CANVAS_NODE_TYPE:
            canvases.append(node_id)
        if node_type == APPROVAL_GATE_TYPE:
            gates.append(node_id)
        if getattr(cls, "app_field", None):
            app_params[node_id] = cls.app_field
            continue
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
        browser_ids=tuple(browsers),
        gate_ids=tuple(gates),
        app_ids=tuple(app_ids),
        app_params=app_params,
        talk=talk_state(graph),
    )


__all__ = [
    "APPROVAL_GATE_TYPE",
    "CANVAS_NODE_TYPE",
    "CHAT_TRIGGER_TYPE",
    "GraphIndex",
    "MEMORY_NODE_TYPE",
    "SCHEDULE_TRIGGER_TYPE",
    "TODO_NODE_TYPE",
    "index_graph",
]
