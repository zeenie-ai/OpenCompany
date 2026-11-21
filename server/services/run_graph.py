"""Pure dependency collection for a trigger's execution graph.

Keep main-flow reachability separate from configuration dependencies: a
teammate, tool, or Context belongs in the run, but its unrelated outgoing
main flow does not. Both Temporal and local deployments use this collector.
"""

from typing import Any

from constants import WORKFLOW_TRIGGER_TYPES
from services.employee_team_connection import is_employee_team_edge


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
        outgoing.setdefault(source, []).append(target)
        handle = edge.get("targetHandle") or edge.get("target_handle") or ""
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
