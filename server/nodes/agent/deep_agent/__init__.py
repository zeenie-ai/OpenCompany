"""Deep Agent — Wave 11.E.3 inlined.

LangChain DeepAgents with filesystem tools + sub-agent delegation.
Distinct from the generic chat agent: different bottom-handle ordering
(Skill / Team / Tool) for the team-delegation UX, and dispatches via
``ai_service.deep_agent_service.execute``.
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.edge_walker import (
    collect_agent_connections, collect_teammate_connections, format_task_context,
)

from .._handles import STD_AGENT_HINTS, deep_agent_handles
from .._specialized import SpecializedAgentOutput, SpecializedAgentParams

logger = get_logger(__name__)


class DeepAgentNode(ActionNode):
    type = "deep_agent"
    display_name = "Deep Agent"
    subtitle = "LangChain DeepAgents"
    group = ("agent",)
    description = "LangChain DeepAgents with filesystem tools and sub-agent delegation"
    component_kind = "agent"
    handles = deep_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = SpecializedAgentParams
    Output = SpecializedAgentOutput

    @Operation("execute", cost={"service": "deep_agent", "action": "run", "count": 1})
    async def execute_op(self, ctx: NodeContext, params: SpecializedAgentParams) -> Any:
        from services.plugin.deps import get_ai_service, get_database
        from services.status_broadcaster import get_status_broadcaster

        ai_service = get_ai_service()
        database = get_database()
        node_id = ctx.node_id
        workflow_id = ctx.workflow_id
        payload = params.model_dump()

        # 1. Edge-walk for memory / skill / tool / input / task connections.
        memory_data, skill_data, tool_data, input_data, task_data = await collect_agent_connections(
            node_id, ctx.raw, database, log_prefix="[Deep Agent]",
        )

        # 2. Inject task-completion context into the prompt; strip tools
        # if the parent task is already done/errored.
        if task_data:
            payload = {
                **payload,
                'prompt': f"{format_task_context(task_data)}\n\n{payload.get('prompt', '')}",
            }
            if task_data.get('status') in ('completed', 'error') and tool_data:
                tool_data = []

        # 3. Auto-prompt fallback from connected input.
        if not payload.get('prompt') and input_data:
            payload = {
                **payload,
                'prompt': (
                    input_data.get('message')
                    or input_data.get('text')
                    or input_data.get('content')
                    or str(input_data)
                ),
            }

        # 4. Teammates (sub-agents wired to input-teammates).
        teammates = await collect_teammate_connections(node_id, ctx.raw, database)

        # 5. Pass the per-workflow workspace dir through so DeepAgents'
        # filesystem tools confine themselves correctly.
        workspace_dir = ctx.raw.get('workspace_dir')
        logger.info("[Deep Agent] workspace_dir from context: %s", workspace_dir)
        if workspace_dir:
            payload = {**payload, 'workspace_dir': workspace_dir}

        # 6. Delegate to DeepAgentService — service builds tools via the
        # ToolAdapter using ai_service._build_tool_from_node.
        response = await ai_service.deep_agent_service.execute(
            node_id, payload,
            memory_data=memory_data if memory_data else None,
            skill_data=skill_data if skill_data else None,
            tool_data=tool_data if tool_data else None,
            teammates=teammates if teammates else None,
            build_tool_fn=ai_service._build_tool_from_node,
            broadcaster=get_status_broadcaster(),
            workflow_id=workflow_id,
            database=database,
        )
        if response.get("success") is False:
            raise RuntimeError(response.get("error") or "deep_agent execution failed")
        return response.get("result") or response
