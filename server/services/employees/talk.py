"""Talk: the line the owner talks to an employee through.

Every employee can be talked to on its Home page. In the graph that is

    chatTrigger -> agent -> chatReply ("Reply in Chat")

The owner's message (chat session = the workflow id) starts a run of that
trigger's subgraph only: a run keeps the firing trigger's downstream nodes
plus their tools and config, so the line runs on the owner's messages alone
and never disturbs the work path.

``talk_state`` reads a graph:

- ``on``: a chat trigger feeds an agent that answers through Reply in Chat;
- ``off``: one step adds it. Either a chat trigger feeds an agent that has
  no reply yet (any agent: a chat hire's worker is its own talk agent), or
  the graph has no chat trigger and an agent a talk agent can copy
  (aiAgent / chatAgent, whose parameters are the plain prompt, model and
  instructions);
- ``unsupported``: neither. A chat trigger that feeds no agent counts here:
  a second one would start two runs for every message.

``plan_talk_line`` turns ``off`` into ``GraphAdditions``:

- a reply only: "Reply in Chat" after the chat-fed agent;
- or a whole line beside the worker: a "Talk" chat trigger on the
  workflow's session, a "Talk with <name>" agent on the worker's model, its
  own Context, independent configured tools for hired employees, the
  worker's skills, and its reply. A tool node has one owning agent.

A hired employee also gets what the Hire builder gives: the Agent Builder
tool on its talk agent, with the talk tools (generated UI in the chat, and
sending through the hire's apps; never on a worker that strangers write
to), and, for a schedule worker, "Post to Talk", which puts its reports in
the thread. Replies and reports go out only when the agent had something to
say (``send_condition``: not NO_REPLY).

Pure: no I/O, never imports ``nodes/``; built on services/graph_build.py,
so the Hire builder, Turn on Talk and the Agent Builder number, label and
wire nodes the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from services.approvals.contract import send_condition
from services.graph_build import (
    CONTEXT_INPUT,
    CONTEXT_TYPE,
    MAIN_INPUT,
    SKILL_INPUT,
    TOOLS_INPUT,
    Edge,
    GraphAdditions,
    NewNode,
    label_key,
    main_edge,
    ref,
    skill_edge,
    template_key,
    tool_edge,
)
from services.workspace_capabilities import is_registered_agent

CHAT_TRIGGER_TYPE = "chatTrigger"
REPLY_TYPE = "chatReply"
BUILDER_TYPE = "agentBuilder"
#: Agents a talk agent can be copied from.
CLONABLE_AGENT_TYPES = frozenset({"aiAgent", "chatAgent"})

TALK_LABEL = "Talk"
REPLY_LABEL = "Reply in Chat"
POST_LABEL = "Post to Talk"
BUILDER_LABEL = "Agent Builder"
CONTEXT_LABEL = "Context"

#: How far below the graph's lowest node a new line starts.
_ROW_GAP = 260

TalkStateName = Literal["on", "off", "unsupported"]


def talk_agent_label(name: str) -> str:
    return f"Talk with {name}"


class _View:
    """A saved graph's enabled nodes (in graph order) and its edges."""

    def __init__(self, graph: Optional[Mapping[str, Any]]):
        graph = graph if isinstance(graph, Mapping) else {}
        self.nodes: Dict[str, Mapping[str, Any]] = {}
        #: The largest y of any node: new rows go below it.
        self.lowest: float = 0
        for node in graph.get("nodes") or []:
            if not isinstance(node, Mapping):
                continue
            self.lowest = max(self.lowest, _position(node)[1])
            node_id, data = node.get("id"), node.get("data")
            if not isinstance(node_id, str) or not node_id or not isinstance(node.get("type"), str):
                continue
            if isinstance(data, Mapping) and data.get("disabled"):
                continue
            self.nodes.setdefault(node_id, node)
        self.edges: List[Mapping[str, Any]] = [edge for edge in graph.get("edges") or [] if isinstance(edge, Mapping)]

    def type_of(self, node_id: str) -> str:
        return str(self.nodes[node_id]["type"]) if node_id in self.nodes else ""

    def targets(self, source: str) -> List[str]:
        """The enabled nodes ``source`` feeds on the main flow."""
        return [
            str(edge.get("target"))
            for edge in self.edges
            if edge.get("source") == source and _target_handle(edge) == MAIN_INPUT and edge.get("target") in self.nodes
        ]

    def sources(self, target: str, handle: str) -> List[str]:
        """The enabled nodes wired into ``target``'s ``handle``."""
        return [
            str(edge.get("source"))
            for edge in self.edges
            if edge.get("target") == target and _target_handle(edge) == handle and edge.get("source") in self.nodes
        ]

    def of_type(self, node_type: str) -> List[str]:
        return [node_id for node_id in self.nodes if self.type_of(node_id) == node_type]


def _target_handle(edge: Mapping[str, Any]) -> str:
    return str(edge.get("targetHandle") or edge.get("target_handle") or MAIN_INPUT)


def _position(node: Mapping[str, Any]) -> Tuple[float, float]:
    position = node.get("position")
    position = position if isinstance(position, Mapping) else {}
    x, y = position.get("x"), position.get("y")
    return (x if isinstance(x, (int, float)) else 0, y if isinstance(y, (int, float)) else 0)


def sources(graph: Optional[Mapping[str, Any]], target: str, handle: str) -> List[str]:
    """The enabled nodes wired into ``target``'s ``handle`` (``input-tools``:
    its tools), in edge order."""
    return _View(graph).sources(target, handle)


@dataclass(frozen=True)
class TalkLine:
    trigger: str
    agent: str
    #: The Reply in Chat node, None when the agent has none yet.
    reply: Optional[str] = None


@dataclass(frozen=True)
class TalkState:
    state: TalkStateName
    #: The chat-fed line (``on``, or ``off`` for want of a reply).
    line: Optional[TalkLine] = None
    #: ``off`` without a chat trigger: the agent a talk agent copies.
    worker: Optional[str] = None

    @property
    def agent_node_id(self) -> Optional[str]:
        """The agent that answers the owner, while it does."""
        return self.line.agent if self.state == "on" and self.line is not None else None

    def summary(self) -> Dict[str, Any]:
        return {"state": self.state, "agent_node_id": self.agent_node_id}


def _find_line(view: _View) -> Optional[TalkLine]:
    lines: List[TalkLine] = []
    for trigger in view.of_type(CHAT_TRIGGER_TYPE):
        for agent in view.targets(trigger):
            if is_registered_agent(view.type_of(agent)):
                reply = next((node for node in view.targets(agent) if view.type_of(node) == REPLY_TYPE), None)
                lines.append(TalkLine(trigger, agent, reply))
    return next((line for line in lines if line.reply), None) or next(iter(lines), None)


def find_talk_line(graph: Optional[Mapping[str, Any]]) -> Optional[TalkLine]:
    """The line the owner talks through: the first chat-fed agent that
    replies, else the first chat-fed agent."""
    return _find_line(_View(graph))


def talk_state(graph: Optional[Mapping[str, Any]], *, worker: Optional[str] = None) -> TalkState:
    """Where the owner stands with talking to this graph. ``worker`` (a
    hired employee's agent) is the one a new talk agent copies, when it
    can be copied; otherwise the first agent that can."""
    view = _View(graph)
    line = _find_line(view)
    if line is not None:
        return TalkState("on" if line.reply else "off", line=line)
    if view.of_type(CHAT_TRIGGER_TYPE):
        return TalkState("unsupported")
    candidates = ([worker] if worker else []) + list(view.nodes)
    clone = next((node_id for node_id in candidates if view.type_of(node_id) in CLONABLE_AGENT_TYPES), None)
    return TalkState("off", worker=clone) if clone is not None else TalkState("unsupported")


@dataclass(frozen=True)
class TalkAgent:
    """A new talk agent (a whole line only): its label and its parameters
    (provider, model, system_message). The plan sets its prompt."""

    label: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TalkTool:
    """A tool only the agent the owner talks to gets: generated UI in the
    chat, and sending through the hire's apps (``talk_send``). Never wired
    to a worker strangers write to."""

    type: str
    label: str
    params: Mapping[str, Any] = field(default_factory=dict)
    #: The ``node_roles`` key it is recorded under, if any.
    role: Optional[str] = None


def _talk_tool_additions(
    view: _View, talk_agent: str, wired: Sequence[str], talk_tools: Sequence[TalkTool], start: Tuple[float, float]
) -> Tuple[List[NewNode], List[Edge], Dict[str, str]]:
    """The talk tools ``talk_agent`` lacks (by type), in a row from
    ``start``, each wired to it alone."""
    have = {view.type_of(tool) for tool in wired}
    nodes: List[NewNode] = []
    edges: List[Edge] = []
    roles: Dict[str, str] = {}
    x, y = start
    for index, tool in enumerate(talk_tools):
        if tool.type in have:
            continue
        have.add(tool.type)
        name = f"talk_tool_{index}"
        nodes.append(NewNode(name, tool.type, tool.label, dict(tool.params), (x, y)))
        edges.append(tool_edge(name, talk_agent))
        if tool.role:
            roles[tool.role] = name
        x += 170
    return nodes, edges, roles


def plan_talk_tools(graph: Optional[Mapping[str, Any]], talk_agent: str, talk_tools: Sequence[TalkTool]) -> TalkPlan:
    """The talk tools an existing talk agent lacks (an employee hired before
    they came), below the graph."""
    view = _View(graph)
    if talk_agent not in view.nodes:
        return TalkPlan()
    x = _position(view.nodes[talk_agent])[0]
    nodes, edges, roles = _talk_tool_additions(view, talk_agent, view.sources(talk_agent, TOOLS_INPUT), talk_tools, (x - 240, view.lowest + 180))
    return TalkPlan(GraphAdditions(nodes=tuple(nodes), edges=tuple(edges)), roles)


@dataclass(frozen=True)
class TalkPlan:
    additions: GraphAdditions = field(default_factory=GraphAdditions)
    #: ``node_roles`` key -> a ref in ``additions`` or an existing node id.
    roles: Mapping[str, str] = field(default_factory=dict)

    def role_ids(self, node_ids: Mapping[str, str]) -> Dict[str, str]:
        """The roles as node ids, once ``additions`` are placed (``node_ids``:
        ref -> id)."""
        return {role: node_ids.get(name, name) for role, name in self.roles.items()}


def _line_roles(view: _View, line: TalkLine) -> Dict[str, str]:
    roles = {"talk_trigger": line.trigger, "talk_agent": line.agent}
    if line.reply:
        roles["talk_reply"] = line.reply
    context = next(iter(view.sources(line.agent, CONTEXT_INPUT)), None)
    if context is not None:
        roles["talk_context"] = context
    return roles


def plan_talk_line(
    graph: Optional[Mapping[str, Any]],
    state: TalkState,
    *,
    workflow_id: str,
    agent: Optional[TalkAgent] = None,
    hired: bool = False,
    report_from: Optional[str] = None,
    talk_tools: Sequence[TalkTool] = (),
    parameters_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    copy_worker_tools: bool = True,
) -> TalkPlan:
    """What turns ``state`` on; no additions when it already is, only the
    roles of the line there. ``agent`` describes the talk agent a whole
    line adds. ``hired`` adds the Agent Builder tool to the talk agent;
    ``talk_tools`` are added to it too, and to it alone; ``report_from`` (a
    hired schedule worker) adds Post to Talk after that agent. Raises
    ValueError for an unsupported state, or a whole line without
    ``agent``. Hired contacts get independent tool instances with the saved
    parameters. Teams set ``copy_worker_tools=False`` and delegate app work
    to specialists instead. Existing nonemployee editor graphs retain their
    established wiring."""
    view = _View(graph)
    if state.state == "unsupported":
        raise ValueError("this graph has no agent the owner could talk to")
    nodes: List[NewNode] = []
    edges: List[Edge] = []
    if state.line is not None:
        roles = _line_roles(view, state.line)
        if state.state == "on":
            return TalkPlan(roles=roles)
        talk_agent = state.line.agent
        x, y = _position(view.nodes[talk_agent])
        nodes.append(
            NewNode("talk_reply", REPLY_TYPE, REPLY_LABEL, {"message": ref(template_key(view.nodes[talk_agent]), "response")}, (x + 360, y))
        )
        edges.append(main_edge(talk_agent, "talk_reply", send_condition()))
        roles["talk_reply"] = "talk_reply"
        builder_at = (x - 240, view.lowest + _ROW_GAP)
        tools = view.sources(talk_agent, TOOLS_INPUT)
    else:
        if agent is None or state.worker is None:
            raise ValueError("a new talk line needs the agent it copies")
        worker = state.worker
        x, y = _position(view.nodes[worker])[0], view.lowest + _ROW_GAP
        nodes += [
            NewNode("talk_trigger", CHAT_TRIGGER_TYPE, TALK_LABEL, {"session_id": workflow_id}, (x - 360, y)),
            NewNode(
                "talk_agent", view.type_of(worker), agent.label, {**agent.params, "prompt": ref(label_key(TALK_LABEL), "message")}, (x, y)
            ),
            NewNode("talk_context", CONTEXT_TYPE, CONTEXT_LABEL, position=(x, y + 180), context_of="talk_agent"),
            NewNode("talk_reply", REPLY_TYPE, REPLY_LABEL, {"message": ref(label_key(agent.label), "response")}, (x + 360, y)),
        ]
        edges += [main_edge("talk_trigger", "talk_agent"), main_edge("talk_agent", "talk_reply", send_condition())]
        tools = view.sources(worker, TOOLS_INPUT)
        if copy_worker_tools:
            for index, tool in enumerate(dict.fromkeys(tools)):
                if hired:
                    original = view.nodes[tool]
                    name = f"talk_worker_tool_{index}"
                    nodes.append(NewNode(
                        name, view.type_of(tool), str((original.get("data") or {}).get("label") or view.type_of(tool)),
                        dict((parameters_by_id or {}).get(tool) or {}), (x - 240 + 170 * index, y + 360),
                    ))
                    edges.append(tool_edge(name, "talk_agent"))
                else:
                    edges.append(tool_edge(tool, "talk_agent"))
        edges += [skill_edge(skills, "talk_agent") for skills in view.sources(worker, SKILL_INPUT)]
        roles = {"talk_trigger": "talk_trigger", "talk_agent": "talk_agent", "talk_context": "talk_context", "talk_reply": "talk_reply"}
        talk_agent = "talk_agent"
        builder_at = (x - 240, y + 180)
    if hired and not any(view.type_of(tool) == BUILDER_TYPE for tool in tools):
        nodes.append(NewNode("builder", BUILDER_TYPE, BUILDER_LABEL, position=builder_at))
        edges.append(tool_edge("builder", talk_agent))
        roles["builder"] = "builder"
    extra_nodes, extra_edges, extra_roles = _talk_tool_additions(
        view, talk_agent, tools, talk_tools, (builder_at[0] - 170 * max(1, len(talk_tools)), builder_at[1])
    )
    nodes += extra_nodes
    edges += extra_edges
    roles.update(extra_roles)
    reports_go_elsewhere = report_from in view.nodes and report_from != roles["talk_agent"]
    if reports_go_elsewhere and not any(view.type_of(node) == REPLY_TYPE for node in view.targets(report_from)):
        x, y = _position(view.nodes[report_from])
        nodes.append(
            NewNode("report_post", REPLY_TYPE, POST_LABEL, {"message": ref(template_key(view.nodes[report_from]), "response")}, (x + 720, y + 220))
        )
        edges.append(main_edge(report_from, "report_post", send_condition()))
        roles["report_post"] = "report_post"
    return TalkPlan(GraphAdditions(nodes=tuple(nodes), edges=tuple(edges)), roles)


__all__ = [
    "BUILDER_LABEL",
    "BUILDER_TYPE",
    "CHAT_TRIGGER_TYPE",
    "CLONABLE_AGENT_TYPES",
    "POST_LABEL",
    "REPLY_LABEL",
    "REPLY_TYPE",
    "TALK_LABEL",
    "TalkAgent",
    "TalkLine",
    "TalkPlan",
    "TalkState",
    "TalkTool",
    "find_talk_line",
    "plan_talk_line",
    "plan_talk_tools",
    "sources",
    "talk_agent_label",
    "talk_state",
]
