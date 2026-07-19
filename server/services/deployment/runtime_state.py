"""Generic execution-scoped node-state archival and reset coordination."""

from __future__ import annotations

from typing import Any, Dict


async def archive_and_reset_node_state(control: Any, database: Any, broadcaster: Any) -> Dict[str, Any]:
    """Archive every node's current data, then invoke its lifecycle contract.

    Deployment orchestration never branches on node type. Each registered node
    class owns any external mutable state through ``reset_execution_state``.
    """
    from services.node_registry import get_node_class

    graph = control.graph_snapshot or {}
    archived_nodes: Dict[str, Any] = {}
    reset_nodes: list[str] = []
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        if not node_id:
            continue
        params = await database.get_node_parameters(str(node_id)) or {}
        archived_nodes[str(node_id)] = {
            "type": node.get("type"),
            "canvas_data": dict(node.get("data") or {}),
            "parameters": dict(params),
        }

    scope_id = control.data_scope_id or control.execution_id
    scope = await database.get_workflow_run_data_scope(scope_id)
    runtime_data = dict(getattr(scope, "runtime_data", None) or {})
    runtime_data["nodes"] = archived_nodes
    await database.update_workflow_run_data_scope(scope_id, runtime_data=runtime_data)

    for node in graph.get("nodes", []):
        node_id = node.get("id")
        node_class = get_node_class(str(node.get("type") or ""))
        if not node_id or node_class is None:
            continue
        result = await node_class.reset_execution_state(
            node_id=str(node_id), workflow_id=control.workflow_id,
            execution_id=control.execution_id, graph=graph, database=database,
        )
        if not result.get("reset"):
            continue
        reset_nodes.append(str(node_id))
        if "parameters" in result:
            await broadcaster.broadcast_node_parameters_updated(
                str(node_id), parameters=result["parameters"],
                workflow_id=control.workflow_id, source_hint="workflow_reset",
            )
    return {"archived_nodes": len(archived_nodes), "reset_nodes": reset_nodes}


__all__ = ["archive_and_reset_node_state"]
