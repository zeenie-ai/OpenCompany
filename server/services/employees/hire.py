"""``hire_employee``: turn a setup screen into a working employee.

1. Read the request (hire_request.py); refuse one without its identity.
2. Reserve the employee row under the owner's idempotency key. The same
   key again returns the employee that key made (a retry after a lost
   response); the same key with a different payload is refused; the same
   key while its first attempt is still building is ``busy``. A row a
   failed attempt left behind is resumed, and so is one still marked
   building long after any attempt could be (the server stopped mid-way).
3. Resolve the named apps against the registry (unknown ones are kept as
   unsupported, never blocking), pick the AI model, look up the owner's
   own addresses for reports and the skills that are on in their library,
   and build the graph (builder.py).
4. Validate it exactly as a Start would (errors refuse the hire), save it
   as a new workflow, attach it to the row with the apps the graph
   actually uses (one left out, such as a tool that sends while they ask
   first, never shows as an app to connect), and announce the hire.
5. Start it in the background when nothing is missing (every app it uses
   connected, an AI model set up); otherwise it waits, and its card names
   what to connect.

Response: ``{employee, started, missing_apps, needs_ai, unsupported_apps,
warnings, idempotent}``. Errors: ``invalid_request``, ``too_large``,
``conflict``, ``busy``, ``not_allowed``, ``build_failed``, ``save_failed``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket
from pydantic import ValidationError

from core.logging import get_logger
from services.authz.ws_surface import execution_principal
from services.employees import store
from services.employees.apps import AppSpec, resolve_app
from services.employees.builder import BUILDER_VERSION, BuildError, BuildInputs, LibrarySkill, build_employee_graph
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

#: A hire builds and saves in seconds; a row still ``building`` after this
#: was left by an attempt that never finished, and the next one takes over.
BUILDING_STALE_AFTER = timedelta(minutes=2)


def _fail(code: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": code, **extra}


def _still_building(row: Any) -> bool:
    """Another attempt with this row's key is building the employee now."""
    if row.hire_state != "building":
        return False
    since = row.updated_at or row.created_at
    if since is None:
        return True
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - since < BUILDING_STALE_AFTER


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


async def owner_values_for(auth_service: Any) -> Dict[str, str]:
    """The owner's own addresses (for reports to them, and sending as them)."""
    return await _owner_values(Connections(auth_service), auth_service)


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


async def _library_skills(database: Any) -> List[LibrarySkill]:
    """Settings > Skills: the skills that are on, which every new hire gets."""
    rows = await database.get_all_user_skills(active_only=True)
    return [
        LibrarySkill(
            name=str(row.get("name") or ""),
            description=str(row.get("description") or ""),
            instructions=str(row.get("instructions") or ""),
        )
        for row in rows
    ]


def _hire_fields(request: HireEmployeeRequest, unsupported: List[str]) -> Dict[str, Any]:
    """The row's hire data. Its apps are set once the graph is built: only
    the ones the graph uses count."""
    return {
        "role": request.role,
        "description": request.description,
        "job": request.job,
        "color_role": "agent",
        "apps": [],
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


async def _earlier_attempt(database: Any, auth_service: Any, row: Any, digest: str) -> Optional[Dict[str, Any]]:
    """The answer for a key an earlier attempt used, or None to carry on (a
    failed attempt, or one abandoned mid-build, is resumed)."""
    if row.payload_hash and row.payload_hash != digest:
        return _fail("conflict")
    if row.hire_state == "ready" and row.workflow_id:
        from models.employees import EmployeeActivation
        async with database.get_session() as session:
            intent = await session.get(EmployeeActivation, f"hire:{row.id}")
        return await _response_for(database, auth_service, row.workflow_id, started=bool(intent and intent.state == "running"), warnings=[],
            idempotent=True, request_id=row.idempotency_key, activation_state=intent.state if intent else "saved",
            readiness_issue=intent.detail if intent else None)
    if _still_building(row):
        return _fail("busy")
    return None


async def _build_and_save(
    database: Any,
    auth_service: Any,
    request: HireEmployeeRequest,
    row_id: str,
    *,
    claim_token: str,
    workflow_id: str,
    owner: str,
    connections: Connections,
    connected: List[str],
    apps: List[AppSpec],
    unsupported: List[str],
) -> Dict[str, Any]:
    from services.node_allowlist import is_hire_allowed
    from services.workflow_validator import validate_workflow

    try:
        settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    except Exception:
        settings = {}
    llm = await resolve_llm_choice(database, auth_service, connections)
    if request.source.provider and request.source.model and request.source.provider in await connections.ai_providers():
        from services.employees.llm import LLMChoice, runs_locally
        llm = LLMChoice(provider=request.source.provider, model=request.source.model, local=runs_locally(request.source.provider))
    try:
        built = build_employee_graph(
            BuildInputs(
                workflow_id=workflow_id,
                request=request,
                apps=apps,
                unsupported_apps=unsupported,
                connected_app_ids=set(connected),
                owner=OwnerProfile.from_settings(settings),
                owner_values=await _owner_values(connections, auth_service),
                timezone=str(settings.get("profile_timezone") or "UTC"),
                llm=llm,
                memory=settings.get("memory_across_chats") is not False,
                skills=await _library_skills(database),
                allowed=is_hire_allowed,
                team=True,
            )
        )
    except BuildError as exc:
        await store.mark_failed(database, row_id, claim_token)
        logger.warning("Hire could not be built", code=exc.code)
        return _fail(exc.code)

    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    if report.get("errors"):
        await store.mark_failed(database, row_id, claim_token)
        logger.warning("Hire failed validation", codes=[issue.get("code") for issue in report["errors"]])
        return _fail("build_failed", report=report)

    await store.commit_hire(database, row_id, claim_token, name=request.name,
        description=request.description, nodes=built.nodes, edges=built.edges,
        parameters=built.parameters, node_roles=built.node_roles,
        fields={"llm": {"provider": llm.provider, "model": llm.model} if llm else {},
                "trigger": built.trigger, "apps": built.app_ids, "team_plan": built.team_plan})
    from services.status_broadcaster import get_status_broadcaster
    try:
        await get_status_broadcaster().broadcast_workflow_lifecycle("created", workflow_id=workflow_id, name=request.name,
            node_count=len(built.nodes), edge_count=len(built.edges))
    except Exception:
        logger.debug("A committed employee lifecycle notification will refresh on reconnect", exc_info=True)

    response = await _response_for(database, auth_service, workflow_id, warnings=built.warnings, idempotent=False)
    if not response.get("success"):
        return response
    employee = response["employee"]
    await broadcast_employee_event("hired", workflow_id=workflow_id, revision=int(employee.get("revision") or 0), employee=employee)
    from services.employees.team_runtime import team_runtime_error
    readiness = team_runtime_error()
    started = not employee["missing_apps"] and not employee["needs_ai"] and not readiness
    if started:
        from services.employees.activation import activate_pending
        task = asyncio.ensure_future(activate_pending(database, workflow_id))
        _starts.add(task)
        task.add_done_callback(_starts.discard)
    logger.info(
        "Employee hired",
        workflow_id=workflow_id,
        trigger=built.trigger.get("kind"),
        delivery=built.delivery,
        started=started,
        apps=len(built.app_ids),
        unsupported=len(unsupported),
    )
    return {**response, "started": started, "request_id": request.idempotency_key,
            "activation_state": "starting" if started else "blocked", "readiness_issue": readiness}


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

    database = container.database()
    auth_service = container.auth_service()
    if hasattr(container, "settings") and not getattr(container.settings(), "employee_teams_enabled", True):
        return _fail("teams_disabled")
    owner = execution_principal(data, websocket)
    digest = payload_hash(request)

    existing = await store.get_by_idempotency_key(database, owner, request.idempotency_key)
    if existing is not None:
        answer = await _earlier_attempt(database, auth_service, existing, digest)
        if answer is not None:
            return answer

    connections = Connections(auth_service)
    connected = await connections.connected_app_ids()
    apps, unsupported = _resolve_apps(request, connected)
    row, claim_token = await store.claim_hire(
        database,
        owner_id=owner,
        idempotency_key=request.idempotency_key,
        payload_hash=digest,
        fields=_hire_fields(request, unsupported),
    )
    if claim_token is None:
        # The row an earlier attempt reserved, perhaps since the lookup above.
        answer = await _earlier_attempt(database, auth_service, row, digest)
        if answer is not None:
            return answer
        return _fail("busy")

    async def renew() -> None:
        while True:
            await asyncio.sleep(30)
            if not await store.renew_claim(database, row.id, claim_token):
                return
    lease_task = asyncio.create_task(renew())

    try:
        return await _build_and_save(
            database, auth_service, request, row.id, claim_token=claim_token, workflow_id=str(row.workflow_id), owner=owner, connections=connections, connected=connected, apps=apps, unsupported=unsupported
        )
    except Exception:
        # A retry resumes a failed row; one left "building" would answer busy.
        await store.mark_failed(database, row.id, claim_token)
        logger.exception("Employee creation could not be saved", request_id=request.idempotency_key)
        return {**_fail("save_failed"), "request_id": request.idempotency_key}
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)


__all__ = ["handle_hire_employee", "owner_values_for", "payload_hash"]
