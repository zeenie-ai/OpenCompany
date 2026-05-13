"""RLM Agent — Wave 11.E.3 inlined.

Recursive Language Model agent. Distinct from the standard LangGraph
chat agent — spins a recursive LM loop inside a REPL sandbox via
``ai_service.rlm_service.execute``.
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.edge_walker import (
    collect_agent_connections, format_task_context,
)

from .._handles import STD_AGENT_HINTS, std_agent_handles
from .._specialized import SpecializedAgentOutput, SpecializedAgentParams

logger = get_logger(__name__)


class RLMAgentNode(ActionNode):
    type = "rlm_agent"
    display_name = "RLM Agent"
    subtitle = "Recursive Reasoning"
    group = ("agent",)
    description = "Recursive Language Model agent (REPL-based)"
    component_kind = "agent"
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = SpecializedAgentParams
    Output = SpecializedAgentOutput

    @Operation("execute", cost={"service": "rlm_agent", "action": "run", "count": 1})
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
            node_id, ctx.raw, database, log_prefix="[RLM Agent]",
        )

        # 2. Inject task-completion context into the prompt.
        if task_data:
            payload = {
                **payload,
                'prompt': f"{format_task_context(task_data)}\n\n{payload.get('prompt', '')}",
            }
            logger.info(
                "[RLM Agent] Task context injected for task_id=%s",
                task_data.get('task_id'),
            )
            # 3. Strip tools when the agent is being asked to react to a
            # completed/errored task — there's nothing to invoke.
            if task_data.get('status') in ('completed', 'error') and tool_data:
                tool_data = []
                logger.info("[RLM Agent] Stripped tools for task completion handling")

        # 4. Auto-prompt fallback: if no prompt + an input is connected,
        # use the input's text-shaped field.
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
            logger.info("[RLM Agent] Auto-using input as prompt")

        # 5. Delegate to the RLM service.
        response = await ai_service.rlm_service.execute(
            node_id, payload,
            memory_data=memory_data,
            skill_data=skill_data if skill_data else None,
            tool_data=tool_data if tool_data else None,
            broadcaster=get_status_broadcaster(),
            workflow_id=workflow_id,
            context=ctx.raw,
            database=database,
        )
        if response.get("success") is False:
            raise RuntimeError(response.get("error") or "rlm_agent execution failed")
        return response.get("result") or response
