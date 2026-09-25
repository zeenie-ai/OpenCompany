"""``hire_employee``: turn a setup screen into a working employee.

1. Read the request (hire_request.py); refuse one without its identity.
2. Reserve the employee row under the owner's idempotency key. The same
   key again returns the employee that key made (a retry after a lost
   response); the same key with a different payload is refused.
3. Resolve the named apps against the registry (unknown ones are kept as
   unsupported, never blocking), pick the AI model, look up the owner's
   own addresses for reports, and build the graph (builder.py).
4. Validate it exactly as a Start would (errors refuse the hire), save it
   as a new workflow, attach it to the row, and announce the hire.
5. Start it in the background when nothing is missing (every app it uses
   connected, an AI model set up); otherwise it waits, and its card names
   what to connect.

Response: ``{employee, started, missing_apps, needs_ai, unsupported_apps,
warnings, idempotent}``. Errors: ``invalid_request``, ``too_large``,
``conflict``, ``not_allowed``, ``build_failed``, ``save_failed``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Set

from fastapi import WebSocket
from pydantic import ValidationError

from core.logging import get_logger
from services.authz.ws_surface import execution_principal
from services.employees import store
from services.employees.apps import AppSpec, resolve_app
from services.employees.builder import BUILDER_VERSION, BuildError, BuildInputs, build_employee_graph
from services.employees.connections import Connections
from services.employees.context import SETTINGS_USER_ID
from services.employees.events import broadcast_employee_event
from services.employees.hire_request import MAX_REQUEST_BYTES, HireEmployeeRequest
from services.employees.llm import resolve_llm_choice
from services.employees.prompt import OwnerProfile
from services.plugin.ws import ws_response

logger = get_logger(__name__)

#: Background starts, kept referenced so they are not garbage collected.
_starts: Set["asyncio.Task[Any]"] = set()


def _fail(code: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": code, **extra}


def payload_hash(request: HireEmployeeRequest) -> str:
    body = request.model_dump(mode="json", exclude={"idempotency_key"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _resolve_apps(request: HireEmployeeRequest, connected: List[str]) -> tuple:
    """(known apps in the order named, unsupported names). Names come from
    the hire's apps, the routine's steps, the trigger and the reply app."""
    names: List[str] = list(request.apps)
    names += [step.app for step in request.steps if step.app]
    if request.trigger is not None and request.trigger.app:
        names.append(request.trigger.app)
    if request.sends_via:
        names.append(request.sends_via)
    known: List[AppSpec] = []
    unsupported: List[str] = []
    for name in names:
        app = resolve_app(name, connected)
        if app is None:
            if name.lower() not in (seen.lower() for seen in unsupported):
                unsupported.append(name)
        elif app not in known:
            known.append(app)
    return known, unsupported


async def _owner_values(connections: Connections, auth_service: Any) -> Dict[str, str]:
    """The owner's own addresses, for reports sent to them."""
    values: Dict[str, str] = {}
    for provider, key in (("google", "google_email"), ("microsoft", "microsoft_email")):
        try:
            label = (await connections.state(provider)).get("account_label")
        except Exception:
            label = None
        if label and "@" in str(label):
            values[key] = str(label)
    for key in ("email_address", "email_provider"):
        try:
            stored = await auth_service.get_api_key(key)
        except Exception:
            stored = None
        if stored:
            values[key] = str(stored)
    return values


def _owner_profile(settings: Dict[str, Any]) -> OwnerProfile:
    call = str(settings.get("profile_call_name") or "").strip()
    full = str(settings.get("profile_full_name") or "").strip()
    return OwnerProfile(
        name=call or full,
        role=str(settings.get("profile_role") or "").strip(),
        preferences=str(settings.get("profile_preferences") or "").strip(),
    )


def _hire_fields(request: HireEmployeeRequest, apps: List[AppSpec], unsupported: List[str]) -> Dict[str, Any]:
    return {
        "role": request.role,
        "description": request.description,
        "job": request.job,
        "color_role": "agent",
        "apps": [app.id for app in apps],
        "unsupported_apps": unsupported,
        "plan": [step.model_dump(exclude_none=True) for step in request.steps if step.title],
        "rules": request.rules.model_dump(),
        "choices": [item.model_dump() for item in [*request.choices, *request.inputs] if item.key],
        "trigger": request.trigger.model_dump(exclude_none=True) if request.trigger is not None else {},
        "llm": {},
        "builder_version": BUILDER_VERSION,
    }


def _start_in_background(workflow_id: str, owner_id: str, idempotency_key: str) -> None:
    from services.deployment.handlers import start_saved_workflow

    async def start() -> None:
        try:
            result = await start_saved_workflow(workflow_id, owner_id=owner_id, idempotency_key=f"hire:{idempotency_key}")
        except Exception:
            logger.warning("A new employee could not start", workflow_id=workflow_id, exc_info=True)
            return
        if not result.get("success"):
            logger.warning("A new employee could not start", workflow_id=workflow_id, error=result.get("error"))

    task = asyncio.ensure_future(start())
    _starts.add(task)
    task.add_done_callback(_starts.discard)


async def _response_for(database: Any, auth_service: Any, workflow_id: str, **extra: Any) -> Dict[str, Any]:
    from services.employees.summaries import get_employee_summary

    summary = await get_employee_summary(database, workflow_id, auth_service=auth_service)
    if summary is None:
        return _fail("save_failed")
    return {
        "success": True,
        "employee": summary,
        "missing_apps": summary["missing_apps"],
        "needs_ai": summary["needs_ai"],
        "unsupported_apps": summary["unsupported_apps"],
        **extra,
    }


@ws_response
async def handle_hire_employee(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    if len(json.dumps(data, default=str)) > MAX_REQUEST_BYTES:
        return _fail("too_large")
    try:
        request = HireEmployeeRequest.model_validate(data)
    except ValidationError:
        return _fail("invalid_request")
    missing = request.missing_identity()
    if missing:
        return _fail("invalid_request", detail=f"{missing} is required")

    from core.container import container
    from services.node_allowlist import is_hire_allowed
    from services.workflow_storage.persist import PersistError, persist_new_workflow
    from services.workflow_validator import validate_workflow

    database = container.database()
    auth_service = container.auth_service()
    owner = execution_principal(data, websocket)
    digest = payload_hash(request)

    existing = await store.get_by_idempotency_key(database, owner, request.idempotency_key)
    if existing is not None:
        if existing.payload_hash and existing.payload_hash != digest:
            return _fail("conflict")
        if existing.hire_state == "ready" and existing.workflow_id:
            return await _response_for(database, auth_service, existing.workflow_id, started=False, warnings=[], idempotent=True)

    connections = Connections(auth_service)
    connected = await connections.connected_app_ids()
    apps, unsupported = _resolve_apps(request, connected)
    row, _created = await store.reserve(
        database,
        owner_id=owner,
        idempotency_key=request.idempotency_key,
        payload_hash=digest,
        fields=_hire_fields(request, apps, unsupported),
    )

    try:
        settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    except Exception:
        settings = {}
    llm = await resolve_llm_choice(database, auth_service, connections)
    workflow_id = await database.allocate_workflow_id()
    try:
        built = build_employee_graph(
            BuildInputs(
                workflow_id=workflow_id,
                request=request,
                apps=apps,
                unsupported_apps=unsupported,
                connected_app_ids=set(connected),
                owner=_owner_profile(settings),
                owner_values=await _owner_values(connections, auth_service),
                timezone=str(settings.get("profile_timezone") or "UTC"),
                llm=llm,
                memory=settings.get("memory_across_chats") is not False,
                allowed=is_hire_allowed,
            )
        )
    except BuildError as exc:
        await store.mark_failed(database, row.id)
        logger.warning("Hire could not be built", code=exc.code)
        return _fail(exc.code)

    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    if report.get("errors"):
        await store.mark_failed(database, row.id)
        logger.warning("Hire failed validation", codes=[issue.get("code") for issue in report["errors"]])
        return _fail("build_failed", report=report)

    try:
        persisted = await persist_new_workflow(
            database,
            workflow_id=workflow_id,
            name=request.name,
            nodes=built.nodes,
            edges=built.edges,
            parameters=built.parameters,
            description=request.description or None,
            owner_id=owner,
        )
    except PersistError as exc:
        await store.mark_failed(database, row.id)
        return _fail(exc.code)

    # Ids are canonical already; map through any alias the save made anyway.
    roles = {role: persisted.aliases.get(node_id, node_id) for role, node_id in built.node_roles.items()}
    ready = await store.mark_ready(database, row.id, workflow_id=persisted.workflow_id, node_roles=roles)
    if ready is not None:
        await store.update_employee(
            database,
            persisted.workflow_id,
            {
                "llm": {"provider": llm.provider, "model": llm.model} if llm else {},
                "trigger": built.trigger,
                "apps": built.app_ids or [app.id for app in apps],
            },
        )

    response = await _response_for(database, auth_service, persisted.workflow_id, warnings=built.warnings, idempotent=False)
    if not response.get("success"):
        return response
    employee = response["employee"]
    await broadcast_employee_event("hired", workflow_id=persisted.workflow_id, revision=int(employee.get("revision") or 0), employee=employee)
    started = not employee["missing_apps"] and not employee["needs_ai"]
    if started:
        _start_in_background(persisted.workflow_id, owner, request.idempotency_key)
    logger.info(
        "Employee hired",
        workflow_id=persisted.workflow_id,
        trigger=built.trigger.get("kind"),
        delivery=built.delivery,
        started=started,
        apps=len(apps),
        unsupported=len(unsupported),
    )
    return {**response, "started": started}


__all__ = ["handle_hire_employee", "payload_hash"]
