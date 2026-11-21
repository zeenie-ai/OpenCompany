"""Graph semantics shared by the employee builder, executors and Temporal workflows."""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal, Mapping

EMPLOYEE_TEAM_INPUT = "input-employee-team"
EMPLOYEE_TEAM_OUTPUT = "output-top"
TALK_CONNECTION_VERSION = 1
EMPLOYEE_TEAM_RESOURCE_FIELDS = {
    "task_manager": "task_manager_node_id",
    "talk_task_manager": "talk_task_manager_node_id",
    "lead_skills": "lead_skill_node_id",
    "talk_skills": "talk_skill_node_id",
}


class EmployeeTeamValidationError(ValueError):
    """An actionable graph error, distinct from storage or runtime failures."""

    def __init__(self, message: str, *, code: str = "INVALID_EMPLOYEE_TEAM_CONNECTION", node_id: str | None = None):
        super().__init__(message)
        self.issues = [{"code": code, "node_id": node_id, "message": message}]


def is_employee_team_edge(edge: Mapping[str, Any]) -> bool:
    return (edge.get("targetHandle") or edge.get("target_handle")) == EMPLOYEE_TEAM_INPUT


def employee_team_edge(lead: str, talk: str):
    """The lead supplies a job destination; this edge never carries a reply."""
    from services.graph_build import Edge
    return Edge(lead, EMPLOYEE_TEAM_OUTPUT, talk, EMPLOYEE_TEAM_INPUT)


def task_manager_additions(owner: str, position: tuple[float, float], *, ref: str = "task_manager", label: str = "Task Manager"):
    """Create one agent's Task Manager and its tools connection in one graph mutation."""
    from services.graph_build import GraphAdditions, NewNode, tool_edge

    manager = NewNode(ref, "taskManager", label, position=position)
    return GraphAdditions(nodes=(manager,), edges=(tool_edge(manager.ref, owner),))


def employee_skill_parameters(role: Literal["lead", "talk"]) -> dict:
    """Default editable skills for the employee's lead or conversational contact."""
    from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME

    if role == "lead":
        name, description = "team-delegation", "Assign and review specialist work"
        instructions = ("Give each assignment to a connected specialist through Task Manager. "
                        "State the expected result and dependencies. Review the submitted evidence, "
                        "request changes where needed, and accept the work before delivering a result.")
    elif role == "talk":
        name, description = "team-intake", "Submit requests and report verified job progress"
        instructions = ("Clarify the owner's desired result and submit substantive work once through Task Manager. "
                        "Use the returned job ID to check the saved status before reporting progress. "
                        "Share the reviewed result when the team delivers it.")
    else:
        raise ValueError("Employee skills require a lead or Talk contact")
    return {"skill_folder": "assistant", "skills_config": {
        SKILL_TOOL_NAME: dict(SKILL_TOOL_ENTRY),
        name: {"enabled": True, "description": description, "instructions": instructions, "isCustomized": False},
    }}


def employee_team_connection_issues(nodes: list, edges: list) -> list[dict]:
    by_id = {node.get("id"): node for node in nodes}
    links = [edge for edge in edges if is_employee_team_edge(edge)]
    issues = []
    for edge in links:
        source, target = by_id.get(edge.get("source"), {}), by_id.get(edge.get("target"), {})
        if (source.get("type") != "ai_employee" or target.get("type") != "aiAgent"
                or (edge.get("sourceHandle") or edge.get("source_handle")) != EMPLOYEE_TEAM_OUTPUT
                or source.get("id") == target.get("id")):
            issues.append({"code": "INVALID_EMPLOYEE_TEAM_CONNECTION", "node_id": edge.get("target"),
                           "message": "Connect the AI Employee's top output to its Talk agent's Team input."})
    for target, count in Counter(edge.get("target") for edge in links).items():
        if count > 1:
            issues.append({"code": "MULTIPLE_EMPLOYEE_TEAM_CONNECTIONS", "node_id": target,
                           "message": "A Talk agent must have exactly one employee team connection."})
    return issues


def _connected_ids(graph: Mapping[str, Any], owner: str, node_type: str, handle: str) -> list[str]:
    sources = {node["id"] for node in graph.get("nodes", []) if node.get("type") == node_type and node.get("id")}
    return [edge["source"] for edge in graph.get("edges", [])
            if edge.get("target") == owner and edge.get("source") in sources
            and (edge.get("targetHandle") or edge.get("target_handle")) == handle]


def connected_task_manager_ids(graph: Mapping[str, Any], owner: str) -> list[str]:
    """Manager IDs on an agent's Tools input, one per edge including duplicates."""
    return _connected_ids(graph, owner, "taskManager", "input-tools")


def connected_master_skill_ids(graph: Mapping[str, Any], owner: str) -> list[str]:
    """Master Skills on an agent's Skill input, one per edge including duplicates."""
    return _connected_ids(graph, owner, "masterSkill", "input-skill")


def _validate_assigned_node(graph: Mapping[str, Any], owner: str, assigned: str, node_type: str,
                            connected: bool, *, label: str, handle: str, code: str) -> None:
    node = next((node for node in graph.get("nodes", []) if node.get("id") == assigned), None)
    links = [edge for edge in graph.get("edges", []) if edge.get("source") == assigned]
    if (not assigned or not owner or node is None or node.get("type") != node_type
            or node.get("disabled") or (node.get("data") or {}).get("disabled")
            or not connected or len(links) != 1
            or (links[0].get("sourceHandle") or links[0].get("source_handle")) != "output-tool"):
        raise EmployeeTeamValidationError(
            f"Connect this agent's assigned {label} to its {handle} input and enable it. Each agent needs its own {label}.",
            code=code, node_id=assigned or owner,
        )


def validate_employee_role_connections(graph: Mapping[str, Any], plan: Mapping[str, Any], owner: str | None) -> None:
    """Only role bindings recorded for this generation are mandatory."""
    if not owner:
        return
    if owner == plan.get("lead_node_id"):
        manager_key, skill_key = EMPLOYEE_TEAM_RESOURCE_FIELDS["task_manager"], EMPLOYEE_TEAM_RESOURCE_FIELDS["lead_skills"]
    elif owner == plan.get("talk_node_id"):
        manager_key, skill_key = EMPLOYEE_TEAM_RESOURCE_FIELDS["talk_task_manager"], EMPLOYEE_TEAM_RESOURCE_FIELDS["talk_skills"]
    else:
        return
    if manager_key in plan:
        manager = plan[manager_key]
        _validate_assigned_node(graph, owner, manager, "taskManager", connected_task_manager_ids(graph, owner) == [manager],
                                label="Task Manager", handle="Tools", code="INVALID_EMPLOYEE_TASK_MANAGER")
    if skill_key in plan:
        skill = plan[skill_key]
        # Additional Master Skills may contribute distinct owner-approved skills.
        _validate_assigned_node(graph, owner, skill, "masterSkill", connected_master_skill_ids(graph, owner).count(skill) == 1,
                                label="Master Skill", handle="Skill", code="INVALID_EMPLOYEE_MASTER_SKILL")


def validate_employee_team_connection(graph: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    """Require the saved link for new plans while honoring historical v2 runs.

    Existing admitted generations have no marker or edge. A newly added link
    must agree with their plan too; it cannot silently redirect a job.
    """
    validate_employee_role_connections(graph, plan, plan.get("lead_node_id"))
    validate_employee_role_connections(graph, plan, plan.get("talk_node_id"))
    links = [edge for edge in graph.get("edges", [])
             if is_employee_team_edge(edge) and edge.get("target") == plan.get("talk_node_id")]
    version = plan.get("talk_connection_version")
    if version is None and not links:
        return
    if version not in (None, TALK_CONNECTION_VERSION):
        raise EmployeeTeamValidationError("This employee's Talk connection version is not supported")
    if version == TALK_CONNECTION_VERSION:
        _validate_managed_team_members(graph, plan)
    if len(links) != 1 or links[0].get("source") != plan.get("lead_node_id"):
        raise EmployeeTeamValidationError("Connect this employee's Talk agent to its AI Employee team lead before starting work", node_id=plan.get("talk_node_id"))
    if employee_team_connection_issues(list(graph.get("nodes", [])), links):
        raise EmployeeTeamValidationError("This employee's Talk team connection is invalid", node_id=plan.get("talk_node_id"))


def _validate_managed_team_members(graph: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    """New managed hires can spawn exactly their recorded, connected members."""
    nodes = {node.get("id"): node for node in graph.get("nodes", [])}
    lead, talk = plan.get("lead_node_id"), plan.get("talk_node_id")
    if not lead or nodes.get(lead, {}).get("type") != "ai_employee":
        raise EmployeeTeamValidationError("Restore this employee's AI Employee team lead before starting work.",
                                          code="INVALID_EMPLOYEE_TEAM_LEAD", node_id=lead)
    if not talk or nodes.get(talk, {}).get("type") != "aiAgent":
        raise EmployeeTeamValidationError("Restore this employee's Talk agent and team connection before starting work.", node_id=talk)
    members = plan.get("members") or []
    member_ids = [member.get("node_id") for member in members]
    if not member_ids or any(not member for member in member_ids) or len(set(member_ids)) != len(member_ids):
        raise EmployeeTeamValidationError("This employee needs a valid specialist team. Review the employee's team setup before starting work.",
                                          code="INVALID_EMPLOYEE_TEAM_MEMBERS", node_id=lead)
    edges = [edge for edge in graph.get("edges", []) if edge.get("target") == lead
             and (edge.get("targetHandle") or edge.get("target_handle")) == "input-teammates"]
    if any(edge.get("source") not in member_ids for edge in edges):
        raise EmployeeTeamValidationError("A connected specialist is outside this employee's saved team. Review the team change before starting work.",
                                          code="UNAPPROVED_EMPLOYEE_TEAM_MEMBER", node_id=lead)
    for member in members:
        member_id = member["node_id"]
        if (member_id not in nodes or nodes[member_id].get("type") != member.get("node_type")
                or member_id in {lead, talk}):
            raise EmployeeTeamValidationError("Restore this employee's saved specialist before starting work.",
                                              code="INVALID_EMPLOYEE_TEAM_MEMBER", node_id=member_id)
        connections = [edge for edge in edges if edge.get("source") == member_id]
        if len(connections) != 1 or (connections[0].get("sourceHandle") or connections[0].get("source_handle")) != "output-top":
            raise EmployeeTeamValidationError("Connect this specialist's top output to its AI Employee lead's Team input before starting work.",
                                              code="INVALID_EMPLOYEE_TEAM_MEMBER_CONNECTION", node_id=member_id)
    delivery_ids = [*plan.get("delivery_node_ids", []), *plan.get("talk_delivery_node_ids", [])]
    if any(node_id not in nodes for node_id in delivery_ids):
        raise EmployeeTeamValidationError("Restore this employee's reviewed result destination before starting work.",
                                          code="INVALID_EMPLOYEE_TEAM_DELIVERY", node_id=lead)
