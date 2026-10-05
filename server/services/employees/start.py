"""``start_employee {workflow_id, expected_revision, idempotency_key}``.

Starts an employee from its saved graph (the same Start the editor uses,
through ``start_saved_workflow``), after two checks the editor does not
make: every app it uses is connected (``missing_apps``), and an AI model
is set up (``needs_ai``). Returns the start's control envelope.

An employee hired before any AI model existed, or whose model's provider
has since been disconnected, is moved onto the model the owner has now,
so Start does not launch an agent that cannot answer: its worker, and the
agent it talks to the owner through.

One that stopped after a problem is reset first, so Start works on it
too. The revision the owner's card showed is the stopped generation's,
which the reset moves past, so it is checked here instead.
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

#: The node roles a hired employee's agents play: the worker, and the agent
#: it talks to the owner through.
AGENT_ROLES = ("agent", "talk_agent")


async def _agent_ids(database: Any, workflow_id: str) -> List[str]:
    row = await store.get_by_workflow(database, workflow_id)
    roles = (row.node_roles or {}) if row is not None else {}
    agents = list(dict.fromkeys(roles[role] for role in AGENT_ROLES if roles.get(role)))
    # Roles predate specialist teams. Include every live agent in the graph,
    # including members added after Hire, while retaining metadata order.
    workflow = await database.get_workflow(workflow_id)
    graph_agents = list(index_graph(getattr(workflow, "data", None)).agent_ids) if workflow is not None else []
    return list(dict.fromkeys([*agents, *graph_agents]))


async def heal_agent_models(database: Any, auth_service: Any, connections: Connections, workflow_id: str) -> List[str]:
    """Point each agent whose provider is not usable at the owner's current
    model. Returns the agents changed."""
    usable = set(await connections.ai_providers())
    changed: List[str] = []
    choice = None
    for agent_id in await _agent_ids(database, workflow_id):
        params = await database.get_node_parameters(agent_id) or {}
        provider = params.get("provider")
        if provider in usable:
            # Keep explicit working models; persist a missing model from this
            # provider's saved default instead of switching providers.
            if params.get("model"):
                continue
            from services.employees.llm import _model_for, runs_locally
            endpoint_models = None
            if str(provider).startswith("openai_compatible:"):
                from services.llm.endpoints import list_endpoints
                endpoint = next((entry for entry in await list_endpoints(auth_service) if entry.ref == provider), None)
                endpoint_models = list(endpoint.models) if endpoint is not None else None
            model = await _model_for(provider, database, auth_service, local=runs_locally(provider), endpoint_models=endpoint_models)
            if model:
                await database.save_node_parameters(agent_id, {**params, "model": model})
                changed.append(agent_id)
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
    employee = await store.get_by_workflow(database, workflow_id)
    if getattr(employee, "team_plan", None):
        from services.employees.team_runtime import team_runtime_error
        from services.employees.upgrade import team_approval_topology_error
        app = getattr(websocket, "app", None)
        manager = getattr(getattr(app, "state", None), "temporal_worker_manager", None)
        error = team_runtime_error(worker_manager=manager)
        if error:
            return {"success": False, "error": error}
        workflow = await database.get_workflow(workflow_id)
        roles = employee.node_roles or {}
        params = {
            node_id: await database.get_node_parameters(node_id) or {}
            for node_id in (roles.get("job_delivery"), roles.get("gate"), roles.get("reply")) if node_id
        }
        error = team_approval_topology_error(getattr(workflow, "data", None), roles, params=params)
        if error:
            return {"success": False, "error": error}
    await heal_agent_models(database, auth_service, Connections(auth_service), workflow_id)
    # An employee an older builder made comes up to the live Ask first rule
    # before it runs (services/employees/upgrade.py).
    from services.employees.upgrade import upgrade_employee

    await upgrade_employee(database, auth_service, workflow_id)
    expected = data.get("expected_revision")
    expected_revision = int(expected) if expected is not None else None
    latest = await database.get_latest_workflow_control(workflow_id)
    if latest is not None and latest.status == "failed":
        if expected_revision is not None and expected_revision != latest.revision:
            return {"success": False, "error": "control_revision_conflict"}
        expected_revision = None  # the reset below moves the revision on
    return await start_saved_workflow(
        workflow_id,
        owner_id=execution_principal(data, websocket),
        expected_revision=expected_revision,
        idempotency_key=str(data.get("idempotency_key") or "") or None,
        reset_if_failed=True,
    )


__all__ = ["handle_start_employee", "heal_agent_models"]
