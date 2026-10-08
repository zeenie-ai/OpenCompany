"""Saved-graph Browser Agent discovery; no client executable graphs."""


def browser_tool_for_agent(graph: dict, agent_node_id: str) -> dict:
    from services.plugin import NodeUserError
    nodes = {node["id"]: node for node in graph.get("nodes", []) if node.get("id")}
    agent = nodes.get(agent_node_id)
    if not agent or agent.get("type") != "browser_agent":
        raise NodeUserError("Select a Browser AI Agent")
    from services.browser_agent_recipe import browser_tool_id
    tool = nodes[browser_tool_id(graph, agent_node_id)]
    if (tool.get("data") or {}).get("disabled"):
        raise NodeUserError("Connect one enabled Browser tool before submitting a task")
    return tool


async def discovery(database, principal: str, workflow_id: str, browser_node_id: str) -> dict:
    from services.authz.workflow_node import resolve_workflow_node
    await resolve_workflow_node(principal, workflow_id, browser_node_id, workspace_kind="browser")
    from services.workspace_task_history import authorize_workflow
    graph = await authorize_workflow(database, principal, workflow_id)
    agents = []
    for node in graph.get("nodes", []):
        if node.get("type") != "browser_agent" or (node.get("data") or {}).get("disabled"):
            continue
        from services.plugin import NodeUserError
        try:
            tool = browser_tool_for_agent(graph, node["id"])
        except NodeUserError:
            continue
        if tool["id"] == browser_node_id:
            agents.append({"node_id": node["id"], "label": (node.get("data") or {}).get("label") or "Browser AI Agent"})
    return {"browser_node_id": browser_node_id, "agents": agents}
