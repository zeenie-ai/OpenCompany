"""``start_employee {workflow_id, expected_revision, idempotency_key}``.

Starts an employee from its saved graph (the same Start the editor uses,
through ``start_saved_workflow``), after two checks the editor does not
make: every app it uses is connected (``missing_apps``), and an AI model
is set up (``needs_ai``). Returns the start's control envelope.

An employee hired before any AI model existed, or whose model's provider
has since been disconnected, is moved onto the model the owner has now,
so Start does not launch an agent that cannot answer.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import WebSocket

from core.logging import get_logger
from services.authz.ws_surface import execution_principal
from services.employees import store
from services.employees.connections import Connections
from services.employees.graph_index import index_graph
from services.employees.llm import resolve_llm_choice
from services.plugin.ws import ws_response

logger = get_logger(__name__)


async def _agent_ids(database: Any, workflow_id: str) -> List[str]:
    row = await store.get_by_workflow(database, workflow_id)
    agent = (row.node_roles or {}).get("agent") if row is not None else None
    if agent:
        return [agent]
    workflow = await database.get_workflow(workflow_id)
    return list(index_graph(getattr(workflow, "data", None)).agent_ids) if workflow is not None else []


async def heal_agent_models(database: Any, auth_service: Any, connections: Connections, workflow_id: str) -> List[str]:
    """Point each agent whose provider is not usable at the owner's current
    model. Returns the agents changed."""
    usable = set(await connections.ai_providers())
    try:
        from services.llm.endpoints import list_endpoints

        usable.update(endpoint.ref for endpoint in await list_endpoints(auth_service))
    except Exception:
        pass
    changed: List[str] = []
    choice = None
    for agent_id in await _agent_ids(database, workflow_id):
        params = await database.get_node_parameters(agent_id) or {}
        if params.get("provider") in usable:
            continue
        if choice is None:
            choice = await resolve_llm_choice(database, auth_service, connections)
            if choice is None:
                return changed
        await database.save_node_parameters(agent_id, {**params, "provider": choice.provider, "model": choice.model})
        changed.append(agent_id)
    if changed:
        logger.info("Moved an employee onto the current AI model", workflow_id=workflow_id, agents=len(changed))
    return changed


@ws_response
async def handle_start_employee(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = str(data.get("workflow_id") or "").strip()
    if not workflow_id:
        return {"success": False, "error": "invalid_request"}
    from core.container import container
    from services.deployment.handlers import start_saved_workflow
    from services.employees.summaries import get_employee_summary

    database = container.database()
    auth_service = container.auth_service()
    summary = await get_employee_summary(database, workflow_id, auth_service=auth_service)
    if summary is None:
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    if summary["missing_apps"]:
        return {"success": False, "error": "missing_apps", "missing_apps": summary["missing_apps"]}
    if summary["needs_ai"]:
        return {"success": False, "error": "needs_ai"}
    await heal_agent_models(database, auth_service, Connections(auth_service), workflow_id)
    expected = data.get("expected_revision")
    return await start_saved_workflow(
        workflow_id,
        owner_id=execution_principal(data, websocket),
        expected_revision=int(expected) if expected is not None else None,
        idempotency_key=str(data.get("idempotency_key") or "") or None,
    )


__all__ = ["handle_start_employee", "heal_agent_models"]
