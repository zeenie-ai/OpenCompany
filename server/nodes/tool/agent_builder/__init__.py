"""Agent Builder -- runtime canvas-mutation tool for AI agents.

Multi-op plugin matching the gmail / calendar / drive convention:
ONE node, ONE Params model with an ``operation: Literal[...]``
discriminator, FIVE ``@Operation`` methods. The LLM sees ONE tool
``agentBuilder`` with a select-style ``operation`` field; agents wire
the node into their standard ``input-tools`` handle (no special
topology, no parallel dispatch system).

The five operations are pure canvas mutations:

* ``inspect_canvas`` (read-only) -- snapshot of nodes / edges + what's
  wired to the calling agent.
* ``add_tool`` -- spawn a tool node + edge into caller's input-tools.
* ``add_skill`` -- toggle a skill on caller's Master Skill (or
  spawn a Master Skill if absent).
* ``add_subagent`` -- spawn a delegate agent + wire to caller's
  input-teammates (team-leads only).
* ``create_workflow`` -- persist a fresh empty workflow + return its
  workflow_id.

Each mutation pushes a ``workflow_ops_apply`` event via the plugin's
``_events.broadcast_workflow_ops`` wrapper so the live canvas updates.
The agent loop binds tools at the start of each turn, so a mutation
made mid-run does NOT add a callable tool to the current turn; the
agent's NEXT invocation rediscovers it. Each summary string ends
with "Available on your next turn" so the LLM doesn't loop trying
to call something that isn't there yet.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services import workflow_ops
from services.node_registry import registered_node_classes
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_TOOL_HANDLE = "input-tools"
_SKILL_HANDLE = "input-skill"
_SKILL_OUTPUT_HANDLE = "output-tool"
_TEAMMATES_HANDLE = "input-teammates"
_TEAMMATES_OUTPUT_HANDLE = "output-main"
_TOOL_OUTPUT_HANDLE = "output-main"
_MASTER_SKILL_TYPE = "masterSkill"
_MASTER_SKILL_LABEL = "Master Skill"
_AGENT_BUILDER_TYPE = "agentBuilder"
_TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})
_DENIED_TOOL_TYPES = frozenset({_AGENT_BUILDER_TYPE, _MASTER_SKILL_TYPE})
_KEY_PARAM_FIELDS = ("provider", "model", "operation", "url", "query")
_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


async def _broadcast(workflow_id: Optional[str], caller_id: str, ops: List[Dict[str, Any]]) -> None:
    """Push a workflow_ops_apply event so the live canvas updates.

    Wave 12 B10: routes through plugin _events.py wrapper.
    """
    if not ops:
        return
    try:
        from ._events import broadcast_workflow_ops

        await broadcast_workflow_ops(
            workflow_id=workflow_id,
            caller_node_id=caller_id,
            operations=ops,
        )
        logger.info(
            "[agentBuilder] broadcast workflow_ops_apply: workflow_id=%s " "caller=%s ops=%s",
            workflow_id,
            caller_id,
            [op.get("type") for op in ops],
        )
    except Exception as exc:
        logger.warning(f"[agentBuilder] broadcast failed: {exc}", exc_info=True)


def _allowed_tool_types() -> set[str]:
    """Tool nodes the LLM may spawn -- excludes the builder + masterSkill."""
    return {
        ntype
        for ntype, cls in registered_node_classes().items()
        if getattr(cls, "component_kind", "") == "tool" and ntype not in _DENIED_TOOL_TYPES
    }


def _allowed_subagent_types() -> set[str]:
    return {ntype for ntype, cls in registered_node_classes().items() if getattr(cls, "component_kind", "") == "agent"}


def _is_team_lead(node_type: str) -> bool:
    return node_type in _TEAM_LEAD_TYPES


def _toggle_skill(
    config: Optional[Dict[str, Dict[str, Any]]],
    skill_name: str,
    enabled: bool,
) -> Dict[str, Dict[str, Any]]:
    """Return a new skills_config dict with ``skill_name`` toggled."""
    base: Dict[str, Dict[str, Any]] = dict(config or {})
    existing = base.get(skill_name, {})
    base[skill_name] = {
        "enabled": enabled,
        "instructions": existing.get("instructions", ""),
        "isCustomized": existing.get("isCustomized", False),
    }
    return base


def _skill_folder_exists(skill_folder: str) -> bool:
    if not _SKILLS_DIR.exists():
        return False
    for path in _SKILLS_DIR.rglob(skill_folder):
        if path.is_dir() and (path / "SKILL.md").exists():
            return True
    return False


def _key_params(node: Dict[str, Any]) -> Dict[str, Any]:
    params = (node.get("data") or {}).get("parameters") or {}
    return {k: params[k] for k in _KEY_PARAM_FIELDS if k in params}


def _resolve_caller(ctx: NodeContext) -> str:
    """Return the calling agent's node_id, or fall back to self.

    Convention: an agentBuilder is wired to ONE agent's input-tools
    handle. We find that edge and treat the agent as the caller.
    Falls back to ctx.node_id if no such edge exists (standalone Run
    or no agent wired yet) so the operations still produce something
    sensible instead of crashing.
    """
    self_id = ctx.node_id
    for edge in ctx.edges or []:
        if edge.get("source") == self_id and edge.get("targetHandle") == _TOOL_HANDLE:
            target = edge.get("target")
            if target:
                logger.info(
                    "[agentBuilder] caller resolved via input-tools edge: " "self=%s -> agent=%s",
                    self_id,
                    target,
                )
                return target
    logger.info(
        "[agentBuilder] no input-tools edge found from %s; " "falling back to self as caller (canvas: %d nodes, %d edges)",
        self_id,
        len(ctx.nodes or []),
        len(ctx.edges or []),
    )
    return self_id


def _log_op_entry(op: str, ctx: NodeContext, **fields: Any) -> None:
    """Single INFO line per operation invocation. Captures workflow_id,
    self_id, canvas size, and ctx.raw keys so a missing-canvas-data bug
    is visible immediately (e.g. ``nodes=0 edges=0 raw_keys=[...]``
    tells you exactly which plumbing layer dropped the canvas state).
    """
    nodes = ctx.nodes or []
    edges = ctx.edges or []
    extra = " ".join(f"{k}={v!r}" for k, v in fields.items() if v not in (None, ""))
    logger.info(
        "[agentBuilder.%s] workflow_id=%s self=%s nodes=%d edges=%d " "raw_keys=%s%s",
        op,
        ctx.workflow_id,
        ctx.node_id,
        len(nodes),
        len(edges),
        sorted((ctx.raw or {}).keys()),
        f" {extra}" if extra else "",
    )


# ----------------------------------------------------------------------------
# Params + Output schemas
# ----------------------------------------------------------------------------


class AgentBuilderParams(BaseModel):
    """Multi-op schema. The LLM picks ``operation``, then fills the
    fields whose ``displayOptions`` enable them for that op.
    """

    operation: Literal[
        "inspect_canvas",
        "add_tool",
        "add_skill",
        "add_subagent",
        "create_workflow",
    ] = Field(
        default="inspect_canvas",
        description=(
            "Which canvas-mutation to perform. Always call "
            "'inspect_canvas' first to see what's already wired before "
            "spawning new nodes."
        ),
    )

    # add_tool
    node_type: str = Field(
        default="",
        description=(
            "For add_tool: tool node type to spawn (e.g. 'httpRequest', "
            "'braveSearch'). Must be a registered node with "
            "component_kind='tool'."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_tool"]}}},
    )

    # add_skill
    skill_folder: str = Field(
        default="",
        description=("For add_skill: skill folder name under server/skills/** " "(e.g. 'http-request-skill', 'memory-skill')."),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_skill"]}}},
    )

    # add_subagent
    agent_type: str = Field(
        default="",
        description=(
            "For add_subagent: agent node type to spawn (e.g. "
            "'coding_agent', 'web_agent'). Must be component_kind='agent' "
            "and not a team-lead. Caller must itself be a team-lead "
            "(orchestrator_agent / ai_employee)."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_subagent"]}}},
    )

    # create_workflow
    workflow_name: str = Field(
        default="",
        description="For create_workflow: display name (non-empty).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )
    workflow_description: str = Field(
        default="",
        description="For create_workflow: optional one-line description.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class AgentBuilderOutput(BaseModel):
    operation: Optional[str] = None
    summary: Optional[str] = None
    operations: Optional[List[Dict[str, Any]]] = None
    # inspect_canvas extras
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    you: Optional[Dict[str, Any]] = None
    # create_workflow extras
    workflow_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------
# Node
# ----------------------------------------------------------------------------


class AgentBuilderNode(ToolNode):
    type = _AGENT_BUILDER_TYPE
    display_name = "Agent Builder"
    subtitle = "Runtime canvas-mutation tool"
    group = ("tool", "ai")
    description = (
        "Inspect the workflow canvas and mutate it at runtime: spawn "
        "tools, enable skills, add delegate agents, or create new "
        "workflows. Wire to an AI agent's input-tools handle; the "
        "agent calls operations through the standard tool path."
    )
    component_kind = "tool"
    tool_name = "agent_builder"
    tool_description = "Inspect and modify the workflow canvas at runtime. Operations: inspect_canvas (read current nodes/edges), spawn_tool (add a tool node + wire it), enable_skill (add a skill folder to a connected masterSkill), add_delegate_agent (add a specialized agent), create_workflow (spawn a brand-new workflow)."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = AgentBuilderParams
    Output = AgentBuilderOutput

    # ---- inspect_canvas (read-only) ---------------------------------------

    @Operation("inspect_canvas")
    async def inspect_canvas(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("inspect_canvas", ctx)
        nodes = list(ctx.nodes or [])
        edges = list(ctx.edges or [])
        caller_id = _resolve_caller(ctx)
        type_by_id = {n.get("id"): n.get("type") for n in nodes}

        node_summaries = [
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "label": (n.get("data") or {}).get("label") or n.get("type"),
                "key_params": _key_params(n),
            }
            for n in nodes
        ]
        edge_summaries = [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "source_handle": e.get("sourceHandle"),
                "target_handle": e.get("targetHandle"),
            }
            for e in edges
        ]
        incoming = [
            {
                "source_id": e.get("source"),
                "source_type": type_by_id.get(e.get("source")),
                "target_handle": e.get("targetHandle"),
            }
            for e in edges
            if e.get("target") == caller_id
        ]
        outgoing = [
            {
                "target_id": e.get("target"),
                "target_type": type_by_id.get(e.get("target")),
                "source_handle": e.get("sourceHandle"),
            }
            for e in edges
            if e.get("source") == caller_id
        ]

        tools = [c for c in incoming if c["target_handle"] == _TOOL_HANDLE]
        skills = [c for c in incoming if c["target_handle"] == _SKILL_HANDLE]
        teammates = [c for c in incoming if c["target_handle"] == _TEAMMATES_HANDLE]
        parts = [f"{len(nodes)} nodes"]
        if tools:
            types = ", ".join(sorted({c["source_type"] or "?" for c in tools}))
            parts.append(f"{len(tools)} tool(s) wired to you ({types})")
        if skills:
            parts.append(f"{len(skills)} skill source(s) wired")
        if teammates:
            parts.append(f"{len(teammates)} teammate(s)")

        return AgentBuilderOutput(
            operation="inspect_canvas",
            summary=", ".join(parts) + ".",
            nodes=node_summaries,
            edges=edge_summaries,
            you={"node_id": caller_id, "incoming": incoming, "outgoing": outgoing},
        )

    # ---- add_tool ---------------------------------------------------------

    @Operation("add_tool")
    async def add_tool(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_tool", ctx, node_type=params.node_type)
        node_type = (params.node_type or "").strip()
        if not node_type:
            return AgentBuilderOutput(
                operation="add_tool",
                summary="add_tool: node_type is required.",
                operations=[],
            )
        allowed = _allowed_tool_types()
        if node_type not in allowed:
            sample = ", ".join(sorted(allowed)[:10])
            return AgentBuilderOutput(
                operation="add_tool",
                summary=(f"add_tool: '{node_type}' is not an allowed tool type. " f"Examples of allowed types: {sample}..."),
                operations=[],
            )

        caller_id = _resolve_caller(ctx)
        client_ref = f"new_{node_type}"
        ops = [
            workflow_ops.add_node(
                client_ref,
                node_type,
                {},
                label=node_type,
                position=workflow_ops.anchored(caller_id, offset_x=200, offset_y=80),
            ),
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_TOOL_OUTPUT_HANDLE,
                target_handle=_TOOL_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_tool",
            summary=f"Added '{node_type}' as a tool. Available on your next turn.",
            operations=ops,
        )

    # ---- add_skill --------------------------------------------------------

    @Operation("add_skill")
    async def add_skill(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_skill", ctx, skill_folder=params.skill_folder)
        skill = (params.skill_folder or "").strip()
        if not skill:
            return AgentBuilderOutput(
                operation="add_skill",
                summary="add_skill: skill_folder is required.",
                operations=[],
            )
        if not _skill_folder_exists(skill):
            return AgentBuilderOutput(
                operation="add_skill",
                summary=f"add_skill: skill '{skill}' not found under server/skills.",
                operations=[],
            )

        nodes = list(ctx.nodes or [])
        edges = list(ctx.edges or [])
        caller_id = _resolve_caller(ctx)

        skill_edge = next(
            (e for e in edges if e.get("target") == caller_id and e.get("targetHandle") == _SKILL_HANDLE),
            None,
        )
        master_skill = None
        if skill_edge:
            master_skill = next(
                (n for n in nodes if n.get("id") == skill_edge.get("source") and n.get("type") == _MASTER_SKILL_TYPE),
                None,
            )

        if master_skill:
            existing = ((master_skill.get("data") or {}).get("parameters") or {}).get("skills_config") or {}
            new_config = _toggle_skill(existing, skill, True)
            ops = [
                workflow_ops.set_node_parameters(
                    master_skill["id"],
                    {"skills_config": new_config},
                )
            ]
            await _broadcast(ctx.workflow_id, caller_id, ops)
            return AgentBuilderOutput(
                operation="add_skill",
                summary=f"Enabled '{skill}' skill. Available on your next turn.",
                operations=ops,
            )

        new_config = _toggle_skill(None, skill, True)
        client_ref = "new_master_skill"
        ops = [
            workflow_ops.add_node(
                client_ref,
                _MASTER_SKILL_TYPE,
                {"skills_config": new_config},
                label=_MASTER_SKILL_LABEL,
                position=workflow_ops.anchored(caller_id, offset_x=-60, offset_y=220),
            ),
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_SKILL_OUTPUT_HANDLE,
                target_handle=_SKILL_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_skill",
            summary=(f"Created Master Skill node and enabled '{skill}'. " "Available on your next turn."),
            operations=ops,
        )

    # ---- add_subagent -----------------------------------------------------

    @Operation("add_subagent")
    async def add_subagent(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("add_subagent", ctx, agent_type=params.agent_type)
        agent_type = (params.agent_type or "").strip()
        if not agent_type:
            return AgentBuilderOutput(
                operation="add_subagent",
                summary="add_subagent: agent_type is required.",
                operations=[],
            )

        nodes = list(ctx.nodes or [])
        caller_id = _resolve_caller(ctx)
        caller = next((n for n in nodes if n.get("id") == caller_id), None)
        caller_type = (caller or {}).get("type") or ""

        if not _is_team_lead(caller_type):
            leads = ", ".join(sorted(_TEAM_LEAD_TYPES))
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(f"add_subagent: only team-lead agents ({leads}) can spawn " f"delegates. This agent is '{caller_type}'."),
                operations=[],
            )
        allowed = _allowed_subagent_types()
        if agent_type not in allowed:
            sample = ", ".join(sorted(allowed)[:10])
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(f"add_subagent: '{agent_type}' is not an allowed agent " f"type. Examples: {sample}..."),
                operations=[],
            )
        if _is_team_lead(agent_type):
            return AgentBuilderOutput(
                operation="add_subagent",
                summary=(f"add_subagent: cannot spawn another team-lead " f"('{agent_type}'); pick a specialized agent instead."),
                operations=[],
            )

        client_ref = f"new_{agent_type}"
        ops = [
            workflow_ops.add_node(
                client_ref,
                agent_type,
                {},
                label=agent_type,
                position=workflow_ops.anchored(caller_id, offset_x=300, offset_y=200),
            ),
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                caller_id,
                source_handle=_TEAMMATES_OUTPUT_HANDLE,
                target_handle=_TEAMMATES_HANDLE,
            ),
        ]
        await _broadcast(ctx.workflow_id, caller_id, ops)
        return AgentBuilderOutput(
            operation="add_subagent",
            summary=(f"Added '{agent_type}' as a teammate. Available on your " "next turn (configure provider/model first)."),
            operations=ops,
        )

    # ---- create_workflow --------------------------------------------------

    @Operation("create_workflow")
    async def create_workflow(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry(
            "create_workflow",
            ctx,
            workflow_name=params.workflow_name,
            workflow_description=params.workflow_description,
        )
        name = (params.workflow_name or "").strip()
        if not name:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary="create_workflow: workflow_name is required.",
            )

        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        start_node_id = f"start-{uuid.uuid4().hex[:8]}"
        description = (params.workflow_description or "").strip()
        workflow_data = {
            "id": workflow_id,
            "name": name,
            "description": description,
            "nodes": [
                {
                    "id": start_node_id,
                    "type": "start",
                    "position": {"x": 200, "y": 200},
                    "data": {"label": "Start"},
                }
            ],
            "edges": [],
            "nodeParameters": {},
        }

        from services.plugin.deps import get_database

        database = get_database()
        ok = await database.save_workflow(
            workflow_id,
            name,
            workflow_data,
            description=description or None,
        )
        if not ok:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=f"create_workflow: failed to persist '{name}'.",
            )
        return AgentBuilderOutput(
            operation="create_workflow",
            summary=(f"Created workflow '{name}' (id: {workflow_id}). " "User can switch to it from the toast notification."),
            workflow_id=workflow_id,
        )
