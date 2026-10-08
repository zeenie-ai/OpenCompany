"""Native Browser AI Agent; browser control remains in its connected tool."""
from typing import Any, Optional

from pydantic import Field

from services.browser_agent_recipe import BROWSER_AGENT_ROLE, browser_tool_id
from services.plugin import NodeContext, Operation
from .._specialized import SpecializedAgentBase, SpecializedAgentParams


class BrowserAgentParams(SpecializedAgentParams):
    system_message: Optional[str] = Field(default=BROWSER_AGENT_ROLE)


class BrowserAgentNode(SpecializedAgentBase):
    type = "browser_agent"
    display_name = "Browser AI Agent"
    subtitle = "Browser tasks"
    description = "Complete browser tasks with saved policies, secure login and human takeover."
    group = ("agent",)
    workspace_task = True
    Params = BrowserAgentParams

    @Operation("execute", cost={"service": "specialized_agent", "action": "run", "count": 1})
    async def execute_op(self, ctx: NodeContext, params: BrowserAgentParams) -> Any:
        from services.plugin.deps import get_database
        from services.browser_owners import routing_for_node, claim_browser_task, cleanup_browser_task

        graph = {"nodes": ctx.raw.get("nodes", []), "edges": ctx.raw.get("edges", [])}
        tool = browser_tool_id(graph, ctx.node_id)
        database = get_database()
        principal = str(ctx.raw.get("user_id") or "owner")
        binding = await routing_for_node(database, ctx.workflow_id, tool, principal)
        execution = str(ctx.raw.get("execution_id") or ctx.raw.get("session_id") or ctx.node_id)
        invocation = ctx.raw.get("parent_task_id") or ctx.raw.get("team_task_id") or execution
        task_id = f"{invocation}:{ctx.node_id}"
        await claim_browser_task(database, binding, principal, task_id)
        ctx.raw["browser_bindings"] = {tool: binding}
        ctx.raw["_browser_task_id"] = task_id
        try:
            return await super().execute_op(ctx, params)
        finally:
            await cleanup_browser_task(database, binding, task_id)

    @classmethod
    async def reset_execution_state(cls, *, node_id: str, workflow_id: str, execution_id: str,
                                    generation: int, graph: dict, database: Any) -> dict:
        from services.browser_owners import routing_for_node, cleanup_browser_task

        if not execution_id:
            return {"reset": False}
        tool = browser_tool_id(graph, node_id)
        principal = str(graph.get("owner_id") or "owner")
        binding = await routing_for_node(database, workflow_id, tool, principal)
        return await cleanup_browser_task(database, binding, f"{execution_id}:{node_id}")
