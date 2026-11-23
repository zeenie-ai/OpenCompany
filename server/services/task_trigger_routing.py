"""Task-input wiring and saved filters for an admitted employee hierarchy."""
from __future__ import annotations

from constants import AI_AGENT_TYPES


def hierarchical_team(nodes: list) -> dict:
    for node in nodes:
        plan = (node.get("data") or {}).get("employee_team_plan") or {}
        if plan.get("version") == 3:
            return plan
    return {}


def task_trigger_owners(trigger_id: str, nodes: list, edges: list) -> set[str]:
    agents = {node["id"] for node in nodes if node.get("type") in AI_AGENT_TYPES}
    return {edge["target"] for edge in edges
            if edge.get("source") == trigger_id and edge.get("target") in agents
            and (edge.get("targetHandle") or edge.get("target_handle")) == "input-task"}


def task_trigger_binding(trigger_id: str, nodes: list, edges: list, params: dict) -> dict:
    owners = task_trigger_owners(trigger_id, nodes, edges)
    if len(owners) > 1:
        raise ValueError("A Task Completed trigger can review work for only one employee.")
    owner = next(iter(owners), None)
    if owner and params.get("parent_node_id") not in (None, "", owner):
        raise ValueError("The Task Completed parent filter conflicts with its connected employee.")
    return {"task_review_owner": owner, "filter_params": params}


def accepts_task_owner(listener: dict, event: dict) -> bool:
    owner = listener.get("task_review_owner")
    if not owner:
        return True
    data = event.get("data")
    return isinstance(data, dict) and data.get("parent_node_id") == owner


def task_trigger_issues(nodes: list, edges: list, parameters: dict) -> list[dict]:
    issues = []
    by_owner: dict[str, list[tuple[str, dict]]] = {}
    for node in nodes:
        if node.get("type") != "taskTrigger":
            continue
        data = node.get("data") or {}
        node_id = node["id"]
        params = parameters.get(node_id, data.get("parameters") or {})
        label = data.get("label") or node_id
        try:
            owner = task_trigger_binding(node_id, nodes, edges, params)["task_review_owner"]
        except ValueError as exc:
            issues.append({"code": "INVALID_TASK_REVIEW_OWNER", "node_id": node_id, "message": f"{label}: {exc}"})
            continue
        if not owner:
            continue
        for other, previous in by_owner.setdefault(owner, []):
            # The name predicate is a substring; one name can contain both
            # strings. Exact task IDs and disjoint statuses establish exclusion.
            if params.get("task_id") and previous.get("task_id") and params["task_id"] != previous["task_id"]:
                continue
            status = params.get("status_filter", "all")
            other_status = previous.get("status_filter", "all")
            if "all" not in (status, other_status) and status != other_status:
                continue
            issues.append({"code": "OVERLAPPING_TASK_REVIEW_TRIGGERS", "node_id": node_id,
                           "message": f"{label} and {other} can both review the same submission to {owner}. "
                                      "Choose disjoint status or task ID filters."})
        by_owner[owner].append((label, params))
    return issues
