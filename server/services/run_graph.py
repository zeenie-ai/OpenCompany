"""Pure dependency collection for a trigger's execution graph.

Keep main-flow reachability separate from configuration dependencies: a
teammate, tool, or Context belongs in the run, but its unrelated outgoing
main flow does not. Both Temporal and local deployments use this collector.
"""

from typing import Any

from constants import WORKFLOW_TRIGGER_TYPES
from services.employee_team_connection import is_employee_team_edge
from services.task_trigger_routing import hierarchical_team, task_trigger_owners


def collect_downstream_node_ids(
    node_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> set[str]:
    """Collect downstream work and its transitive configuration dependencies.

    An agent discovered through ``input-teammates`` or ``input-tools`` can
    have its own Context, Skills, model, and tools. Follow those incoming
    configuration edges to a fixed point, regardless of their source order.
    Other triggers remain independent listeners, even on config handles.
    The firing node itself is excluded from the returned set.
    """
    node_types = {node["id"]: node.get("type", "") for node in nodes}
    team = hierarchical_team(nodes)
    # Review arrivals wake a durable employee instance. Its ordinary main
    # output (in particular Chat's reply sink) belongs to its own turn.
    ends = (task_trigger_owners(node_id, nodes, edges)
            if team and node_types.get(node_id) == "taskTrigger" else set())
    # App/schedule arrivals start durable jobs too. Their final deliveries
    # use the job's captured destinations after acceptance. Only an actual
    # conversation turn can use Chat's ordinary response edge.
    if team.get("talk_node_id") and node_types.get(node_id) != "chatTrigger":
        ends.add(team["talk_node_id"])
    outgoing: dict[str, list[str]] = {}
    dependencies: dict[str, list[str]] = {}
    for edge in edges:
        # This visible route is dispatched by submit_job, never by graph flow.
        # Do not pull the lead (or its other triggers) into a Talk run either.
        if is_employee_team_edge(edge):
            continue
        source, target = edge.get("source"), edge.get("target")
        if source not in node_types or target not in node_types:
            continue
        handle = edge.get("targetHandle") or edge.get("target_handle") or ""
        if source not in ends and not (team and handle == "input-teammates"):
            outgoing.setdefault(source, []).append(target)
        if handle.startswith("input-") and handle != "input-main":
            dependencies.setdefault(target, []).append(source)

    downstream: set[str] = set()
    pending = [node_id]
    while pending:
        for target in outgoing.get(pending.pop(), ()):
            if target == node_id or target in downstream:
                continue
            if node_types[target] in WORKFLOW_TRIGGER_TYPES:
                continue
            downstream.add(target)
            pending.append(target)

    # Do not resume forward traversal from configuration-only additions.
    # They supply capabilities, not another main branch to execute.
    pending = [node["id"] for node in nodes if node["id"] in downstream]
    while pending:
        for source in dependencies.get(pending.pop(), ()):
            if source == node_id or source in downstream:
                continue
            if node_types[source] in WORKFLOW_TRIGGER_TYPES:
                continue
            downstream.add(source)
            pending.append(source)

    return downstream
