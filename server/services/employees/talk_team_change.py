"""Keep explicit team expansion in owner Talk before admitting a work order."""
from __future__ import annotations

import re

# Match standalone team directives, including the conditional that Talk used
# in a real work order. A request to write code or browse is ordinary work.
_QUOTED = re.compile(
    r'''^[ \t]*>[^\n]*|`{3}.*?(?:`{3}|\Z)|`[^`\n]*`|"[^"]*"|“[^”]*”|(?<!\w)['‘][^'’]*['’](?!\w)''',
    re.MULTILINE | re.DOTALL,
)
_DIRECTIVE = re.compile(
    r"(?:^|(?<=[.!?\n]))[ \t]*(?:[-*] )?(?:please )?"
    r"(?:(?P<conditional>if no (?:teammate|team member) can (?:write code|browse(?: the web)?)),\s*)?"
    r"(?:add|hire|recruit) (?:a |an |the )?(?P<kind>coding|browser) (?:ai )?(?:specialist|agent)"
    r"(?: (?:to|for) (?:my |our |your |this |the |the current )?(?:employee(?:'s)? )?team)?"
    r"(?: for this(?: (?:job|task))?)?[ \t]*(?=[.!?\n]|$)", re.I,
)
_TYPES = {"coding": "coding_agent", "browser": "browser_agent"}
_CAPABILITIES = {"coding_agent": {"piWrite", "fileModify"}, "browser_agent": {"browser"}}


def connected_builders(graph: dict, talk_id: str) -> set[str]:
    enabled = {n["id"] for n in graph.get("nodes", []) if n.get("type") == "agentBuilder"
               and not n.get("disabled") and not (n.get("data") or {}).get("disabled")}
    return {e["source"] for e in graph.get("edges", []) if e.get("source") in enabled and e.get("target") == talk_id
            and e.get("targetHandle") == "input-tools" and e.get("sourceHandle") == "output-tool"}


async def route_talk_team_change(database, config: dict, plan: dict, mission: str) -> dict | None:
    """Use Talk's connected Builder and its ordinary grant/retry path for a missing specialist."""
    requests = list(_DIRECTIVE.finditer(_QUOTED.sub(" ", mission)))
    if not requests:
        return None
    from services.employees.team_runtime import admitted_graph

    graph = await admitted_graph(database, config) or {}
    members = plan.get("members") or []
    admitted_types = {m.get("node_type") for m in members}
    tool_ids = {tool for member in members for tool in member.get("tools", [])}
    tools = {n.get("type") for n in graph.get("nodes", []) if n.get("id") in tool_ids
             and not n.get("disabled") and not (n.get("data") or {}).get("disabled")}
    for request in requests:
        kind = _TYPES[request["kind"].lower()]
        if kind not in admitted_types and not (request["conditional"] and _CAPABILITIES[kind] & tools):
            break
    else:
        return None

    from services.employees.permissions import assert_runtime_access
    from services.employees.store import get_by_workflow
    from services.handlers.tools import execute_tool
    from services.plugin import NodeUserError

    talk = plan["talk_node_id"]
    workflow_id = str(config["workflow_id"])
    result = {"operation": "submit_job", "success": False, "job_created": False,
              "agent_type": kind, "activation_state": "blocked"}
    employee = await get_by_workflow(database, workflow_id)
    workflow = await database.get_workflow(workflow_id)
    saved = (workflow.data or {}) if workflow else {}
    if not employee or (employee.team_plan or {}).get("talk_node_id") != talk:
        return {**result, "state": "team_change_blocked", "message": "This employee's saved Talk connection changed. Review the employee's team."}
    saved_members = {m["node_id"] for m in (employee.team_plan or {}).get("members", []) if m.get("node_type") == kind}
    if any(e.get("source") in saved_members and e.get("target") == (plan.get("orchestrator_node_id") or plan["lead_node_id"])
           and e.get("targetHandle") == "input-teammates" for e in saved.get("edges", [])):
        return {**result, "state": "pending_apply", "activation_state": "saved",
                "message": "The specialist is already saved. Apply the employee's saved changes before submitting this job."}
    builders = connected_builders(graph, talk) & connected_builders(saved, talk)
    if len(builders) != 1:
        return {**result, "state": "team_change_blocked",
                "message": "Talk needs a connected, enabled Agent Builder to add this specialist. The job has not started."}
    builder = builders.pop()
    try:
        await assert_runtime_access(database, workflow_id, builder, "agentBuilder", config)
    except NodeUserError as exc:
        return {**result, "state": "team_change_blocked", "message": str(exc)}
    # This is a new plugin invocation inside the same authenticated Talk turn.
    # Do not reuse the manager's lifecycle operation or its parameter binding.
    builder_config = {**config, "node_id": builder, "tool_node_id": builder, "node_type": "agentBuilder",
                      "parent_node_id": talk, "agent_operation": None,
                      "parameters": (graph.get("parameters") or {}).get(builder) or {}}
    change = await execute_tool("agent_builder", {"operation": "add_subagent", "agent_type": kind}, builder_config)
    activation = change.get("activation_state")
    required = change.get("required_access") or []
    if activation == "saved":
        state = "pending_apply"
    else:
        state = "awaiting_approval" if required else "team_change_blocked"
    message = change.get("summary") or change.get("error") or "Agent Builder could not save this specialist."
    return {**result, "state": state, "activation_state": activation or "blocked", "message": message,
            "request_id": change.get("request_id"), "required_access": required}
