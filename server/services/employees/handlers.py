"""Normal-mode employee WebSocket handlers.

``list_employees`` -> ``{employees: EmployeeSummary[]}``: the whole team for
the Home sidebar. ``get_employee {workflow_id}`` -> ``{employee}``: one
summary plus what the employee card and its setup screen show.
``get_employee_usage {}`` -> ``{tasks_this_month}``: Settings > Billing.
Shapes are documented in services/employees/summaries.py.

Two change the employee, and answer with its fresh summary
(``{success: true, employee}``, or ``{success: false, error, employee?}``):

- ``enable_employee_talk {workflow_id, idempotency_key}``: Turn on Talk.
  Adds the talk line talk.py plans (in one transaction, so an editor that
  has the workflow open adopts it), then restarts the employee on the
  saved graph if it is live, so the line runs: a running employee ends
  running, a paused or stopped one ends ready. Talk already on succeeds
  without a restart, unless the live generation is missing the line.
- ``apply_employee_changes {workflow_id, idempotency_key}``: restart on the
  latest saved graph (services/deployment/restart.py): running ends
  running, paused or stopped ends ready, ready is left alone.

Errors: ``invalid_request``, ``not_found``, ``unsupported`` (nothing to
talk to), ``conflict`` (a start, pause, resume or reset is under way, or
the graph changed meanwhile), ``restart_failed``. A retry with the same
key adds nothing twice and restarts once. Changes to one employee run one
at a time.

Two change who the employee is, for the owner of the workflow only, and
answer the same way:

- ``rename_employee {workflow_id, name}``: renames their workflow (a new
  slug, the workspace folder moved, ``workflow.renamed`` sent), and each
  agent's instructions a hire wrote take the new name in their opening.
  ``save_failed`` when the rename was not saved.
- ``set_employee_photo {workflow_id, path | null}``: the photo they show,
  a PNG, JPEG, WebP or GIF the owner uploaded to the workspace's
  ``uploads/`` (at most ``EMPLOYEE_PHOTO_MAX_BYTES``); ``null`` takes it
  away. ``invalid_photo`` (with ``detail``) for any other file,
  ``unsupported`` for a workflow built in the editor (it has no employee
  row to keep it on).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from services.authz.ws_surface import execution_principal
from services.deployment.control import serialize_control
from services.deployment.restart import pending_changes, restart_with_latest_graph
from services.employees import store
from services.employees.apps import get_app
from services.employees.builder import CHAT_UI_TYPE, talk_tools
from services.employees.context import SETTINGS_USER_ID
from services.employees.events import employee_changed_now
from services.employees.graph_index import CANVAS_NODE_TYPE, MEMORY_NODE_TYPE, TODO_NODE_TYPE, index_graph
from services.employees.hire_request import NAME_MAX
from services.employees.prompt import (
    OwnerProfile,
    PromptInputs,
    build_system_message,
    renamed_instructions,
    request_from_employee,
    talk_addendum,
)
from services.employees.summaries import employee_usage, get_employee_detail, get_employee_summary, list_employee_summaries
from services.employees.talk import TalkAgent, TalkState, TalkTool, plan_talk_line, sources, talk_agent_label, talk_state
from services.employees.upgrade import upgrade_employee
from services.graph_build import TOOLS_INPUT
from services.plugin.base import NodeUserError
from services.plugin.ws import ws_response
from services.media.limits import EMPLOYEE_PHOTO_MAX_BYTES
from services.workflow_storage.mutate import apply_graph_additions

#: Control states a change must not interrupt.
_TRANSITIONAL_STATES = frozenset({"starting", "pausing", "resuming", "resetting"})

_locks: Dict[str, asyncio.Lock] = {}


def _lock(workflow_id: str) -> asyncio.Lock:
    return _locks.setdefault(workflow_id, asyncio.Lock())


@ws_response
async def handle_list_employees(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    employees = await list_employee_summaries(container.database(), auth_service=container.auth_service())
    return {"success": True, "employees": employees}


@ws_response
async def handle_get_employee(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = str(data.get("workflow_id") or "").strip()
    if not workflow_id:
        raise NodeUserError("workflow_id is required")
    from core.container import container

    employee = await get_employee_detail(container.database(), workflow_id, auth_service=container.auth_service())
    if employee is None:
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    return {"success": True, "employee": employee}


@ws_response
async def handle_get_employee_usage(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    return {"success": True, **await employee_usage(container.database())}


# ----- Turn on Talk, Apply -----


async def _answer(database: Any, auth_service: Any, workflow_id: str, error: Optional[str] = None) -> Dict[str, Any]:
    employee = await get_employee_summary(database, workflow_id, auth_service=auth_service)
    if error:
        return {"success": False, "error": error, "employee": employee}
    return {"success": True, "employee": employee}


async def _talk_tools_for(auth_service: Any, row: Any) -> List[TalkTool]:
    """What only a hired employee's talk agent gets: generated UI, and
    sending through its apps."""
    from services.employees.hire import owner_values_for
    from services.node_allowlist import is_hire_allowed

    apps = [app for app in (get_app(app_id) for app_id in row.apps or []) if app is not None]
    return talk_tools(apps, owner_values=await owner_values_for(auth_service), allowed=is_hire_allowed)


async def _new_talk_agent(database: Any, workflow: Any, row: Any, state: TalkState, *, sends: bool = False) -> TalkAgent:
    """The talk agent a whole line adds, on the worker's model. A hired
    employee's instructions are written again from its hire; one built in
    the editor keeps the worker's own, with a note that this is Talk."""
    graph = workflow.data or {}
    worker = await database.get_node_parameters(state.worker) or {}
    try:
        settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    except Exception:
        settings = {}
    owner = OwnerProfile.from_settings(settings)
    if row is None:
        system_message = (str(worker.get("system_message") or "") + talk_addendum(owner.name)).strip()
    else:
        index = index_graph(graph)
        tools = set(sources(graph, state.worker, TOOLS_INPUT))
        types = {index.node_types.get(tool) for tool in tools}
        browser = bool(tools & set(index.browser_ids))
        system_message = build_system_message(
            PromptInputs(
                request=request_from_employee(row, workflow.name),
                owner=owner,
                delivery="talk",
                unsupported_apps=list(row.unsupported_apps or []),
                has_memory=MEMORY_NODE_TYPE in types,
                has_todos=TODO_NODE_TYPE in types,
                has_canvas=CANVAS_NODE_TYPE in types,
                has_browser=browser,
                browser_read_only=browser and await _browser_read_only(database, tools & set(index.browser_ids)),
                has_builder=True,
                has_send_tools=sends,
            )
        )
    model = {key: worker[key] for key in ("provider", "model") if key in worker}
    return TalkAgent(talk_agent_label(workflow.name), {"system_message": system_message, **model})


async def _enable_talk(database: Any, auth_service: Any, workflow_id: str, *, key: str, owner: str) -> Dict[str, Any]:
    workflow = await database.get_workflow(workflow_id)
    if workflow is None:
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    if serialize_control(await database.get_latest_workflow_control(workflow_id))["state"] in _TRANSITIONAL_STATES:
        return await _answer(database, auth_service, workflow_id, "conflict")
    if await upgrade_employee(database, auth_service, workflow_id):
        workflow = await database.get_workflow(workflow_id) or workflow
    row = await store.get_by_workflow(database, workflow_id)
    roles = dict(row.node_roles or {}) if row is not None else {}
    graph = workflow.data or {}
    state = talk_state(graph, worker=roles.get("agent"))
    if state.state == "unsupported":
        return await _answer(database, auth_service, workflow_id, "unsupported")

    schedule = row is not None and (row.trigger or {}).get("kind") == "schedule"
    tools = await _talk_tools_for(auth_service, row) if row is not None else []
    plan = plan_talk_line(
        graph,
        state,
        workflow_id=workflow_id,
        agent=await _new_talk_agent(database, workflow, row, state, sends=any(tool.type != CHAT_UI_TYPE for tool in tools))
        if state.line is None
        else None,
        hired=row is not None,
        report_from=roles.get("agent") if schedule else None,
        talk_tools=tools,
    )
    node_ids: Dict[str, str] = {}
    added = False
    if plan.additions.nodes or plan.additions.edges:
        try:
            result = await apply_graph_additions(database, workflow_id, plan.additions, mutation_id=f"enable-talk:{workflow_id}:{key}")
        except ValueError:
            # The graph changed since it was read and the line no longer fits.
            return await _answer(database, auth_service, workflow_id, "conflict")
        if result is None:
            return {"success": False, "error": "not_found", "workflow_id": workflow_id}
        node_ids, added = dict(result.node_ids), bool(result.operations)
    if row is not None:
        new_roles = {role: node_id for role, node_id in plan.role_ids(node_ids).items() if roles.get(role) != node_id}
        if new_roles:
            await store.merge_node_roles(database, workflow_id, new_roles)

    # The line runs only once the deployment runs the graph that has it.
    workflow = await database.get_workflow(workflow_id)
    if added or pending_changes(workflow, await database.get_latest_workflow_control(workflow_id)):
        restarted = await restart_with_latest_graph(workflow_id, owner_id=owner, key=key)
        return await _answer(database, auth_service, workflow_id, restarted.error)
    return await _answer(database, auth_service, workflow_id)


async def _apply_changes(database: Any, auth_service: Any, workflow_id: str, *, key: str, owner: str) -> Dict[str, Any]:
    if await database.get_workflow(workflow_id) is None:
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    await upgrade_employee(database, auth_service, workflow_id)
    restarted = await restart_with_latest_graph(workflow_id, owner_id=owner, key=key)
    return await _answer(database, auth_service, workflow_id, restarted.error)


async def _browser_read_only(database: Any, browser_ids: Any) -> bool:
    """Whether the worker's browser was saved read-only (an older hire);
    otherwise it reads only per call, while the owner asks first."""
    for node_id in browser_ids:
        if (await database.get_node_parameters(node_id) or {}).get("interaction") == "read_only":
            return True
    return False


def _request_ids(data: Dict[str, Any]) -> tuple:
    return str(data.get("workflow_id") or "").strip(), str(data.get("idempotency_key") or "").strip()


@ws_response
async def handle_enable_employee_talk(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id, key = _request_ids(data)
    if not workflow_id or not key:
        return {"success": False, "error": "invalid_request"}
    from core.container import container

    async with _lock(workflow_id):
        return await _enable_talk(
            container.database(), container.auth_service(), workflow_id, key=key, owner=execution_principal(data, websocket)
        )


@ws_response
async def handle_apply_employee_changes(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id, key = _request_ids(data)
    if not workflow_id or not key:
        return {"success": False, "error": "invalid_request"}
    from core.container import container

    async with _lock(workflow_id):
        return await _apply_changes(
            container.database(), container.auth_service(), workflow_id, key=key, owner=execution_principal(data, websocket)
        )


# ----- Name and photo -----

#: What a photo may be: images the workspace file route shows inline.
PHOTO_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


async def _owns(database: Any, websocket: Any, workflow_id: str) -> bool:
    from services.chat.access import ChatAccessDenied, authorize_session

    try:
        await authorize_session(database, websocket, workflow_id)
    except ChatAccessDenied:
        return False
    return True


async def _rename_in_instructions(database: Any, workflow: Any, old: str, new: str) -> None:
    """Each agent's instructions a hire wrote take the new name; ones the
    owner rewrote are left alone. Read on every run, so no restart."""
    from services.status_broadcaster import get_status_broadcaster

    for node_id in index_graph(getattr(workflow, "data", None)).agent_ids:
        params = await database.get_node_parameters(node_id) or {}
        renamed = renamed_instructions(str(params.get("system_message") or ""), old, new)
        if renamed is None:
            continue
        params = {**params, "system_message": renamed}
        await database.save_node_parameters(node_id, params)
        await get_status_broadcaster().broadcast_node_parameters_updated(
            node_id, parameters=params, workflow_id=workflow.id, source_hint="employee"
        )


@ws_response
async def handle_rename_employee(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = str(data.get("workflow_id") or "").strip()
    name = " ".join(str(data.get("name") or "").split())[:NAME_MAX]
    if not workflow_id or not name:
        return {"success": False, "error": "invalid_request"}
    from core.container import container
    from services.workflow_storage.handlers import rename_saved_workflow

    database, auth_service = container.database(), container.auth_service()
    if not await _owns(database, websocket, workflow_id):
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    async with _lock(workflow_id):
        workflow = await database.get_workflow(workflow_id)
        if workflow is None:
            return {"success": False, "error": "not_found", "workflow_id": workflow_id}
        old = str(workflow.name or "")
        if name != old:
            if await rename_saved_workflow(database, workflow_id, name) is None:
                return await _answer(database, auth_service, workflow_id, "save_failed")
            await _rename_in_instructions(database, workflow, old, name)
        return await _answer(database, auth_service, workflow_id)


@ws_response
async def handle_set_employee_photo(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = str(data.get("workflow_id") or "").strip()
    path = data.get("path")
    if not workflow_id or (path is not None and not isinstance(path, str)):
        return {"success": False, "error": "invalid_request"}
    from core.container import container

    database, auth_service = container.database(), container.auth_service()
    if not await _owns(database, websocket, workflow_id):
        return {"success": False, "error": "not_found", "workflow_id": workflow_id}
    if path is not None:
        from services.chat.attachments import AttachmentRefused, check_attachments

        try:
            [ref] = await check_attachments(database, workflow_id, [path])
        except AttachmentRefused as exc:
            return {"success": False, "error": "invalid_photo", "detail": str(exc)}
        if ref["mime_type"] not in PHOTO_TYPES:
            return {"success": False, "error": "invalid_photo", "detail": "A photo is a PNG, JPEG, WebP or GIF image."}
        if ref["size_bytes"] > EMPLOYEE_PHOTO_MAX_BYTES:
            return {
                "success": False,
                "error": "invalid_photo",
                "detail": f"A photo is at most {EMPLOYEE_PHOTO_MAX_BYTES // (1024 * 1024)} MB.",
            }
        path = ref["path"]
    if await store.set_photo(database, workflow_id, path) is None:
        return {"success": False, "error": "unsupported", "workflow_id": workflow_id}
    employee_changed_now(workflow_id)
    return await _answer(database, auth_service, workflow_id)


from services.employees.hire import handle_hire_employee  # noqa: E402
from services.employees.start import handle_start_employee  # noqa: E402

WS_HANDLERS: Dict[str, Any] = {
    "list_employees": handle_list_employees,
    "get_employee": handle_get_employee,
    "get_employee_usage": handle_get_employee_usage,
    "hire_employee": handle_hire_employee,
    "start_employee": handle_start_employee,
    "enable_employee_talk": handle_enable_employee_talk,
    "apply_employee_changes": handle_apply_employee_changes,
    "rename_employee": handle_rename_employee,
    "set_employee_photo": handle_set_employee_photo,
}


__all__ = [
    "WS_HANDLERS",
    "handle_apply_employee_changes",
    "handle_enable_employee_talk",
    "handle_get_employee",
    "handle_get_employee_usage",
    "handle_list_employees",
    "handle_rename_employee",
    "handle_set_employee_photo",
]
