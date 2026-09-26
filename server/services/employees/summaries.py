"""EmployeeSummary: what Normal mode shows for one employee.

An employee is a workflow. Hired ones have an ``employees`` row with what
the hire screen captured; ones built in the editor are described from the
graph (``derived: true``). Either way the live parts come from the same
places the editor reads: the latest control generation (state and the
capabilities Start / Pause / Resume need) and the credential store (which
apps are connected).

A team list costs one query per table, never one per workflow, and never
calls Temporal: the list shows the last recorded control state, and the
single-workflow status handler stays the place that reconciles.

The server decides status and default task text; the client only overlays
live node activity on the task line and maps status to a pill.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from zoneinfo import ZoneInfo

from core.logging import get_logger
from services.deployment.control import serialize_control
from services.employees import runs, store
from services.employees.context import SETTINGS_USER_ID
from services.employees.apps import get_app
from services.employees.connections import Connections
from services.employees.graph_index import (
    CHAT_TRIGGER_TYPE,
    SCHEDULE_TRIGGER_TYPE,
    GraphIndex,
    index_graph,
)

logger = get_logger(__name__)

#: Control states that mean "on duty": listening for work or doing it.
WORKING_STATES = frozenset({"starting", "running", "resuming"})
PAUSED_STATES = frozenset({"pausing", "paused"})

_WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _millis(*moments: Optional[datetime]) -> int:
    latest = 0
    for moment in moments:
        if moment is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        latest = max(latest, int(moment.timestamp() * 1000))
    return latest


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def schedule_text(trigger: Mapping[str, Any]) -> str:
    """"Every weekday at 09:00" for a hire's schedule trigger."""
    every = trigger.get("every")
    at = str(trigger.get("at") or "").strip()
    suffix = f" at {at}" if at else ""
    if every == "hour":
        return "Every hour"
    if every == "day":
        return f"Every day{suffix}"
    if every == "weekday":
        return f"Every weekday{suffix}"
    if every == "week":
        day = str(trigger.get("day") or "").strip().capitalize()
        return f"Every {day}{suffix}" if day else f"Every week{suffix}"
    if every == "month":
        day = str(trigger.get("day") or "").strip()
        return f"On day {day} of every month{suffix}" if day else f"Every month{suffix}"
    return "On a schedule"


def _trigger_text(employee: Any, graph: GraphIndex) -> str:
    trigger = getattr(employee, "trigger", None) or {}
    kind = trigger.get("kind") if isinstance(trigger, Mapping) else None
    if kind == "app_event":
        app = get_app(str(trigger.get("app") or ""))
        if app is not None:
            return app.phrase("when", f"When something arrives in {app.name}")
    if kind == "schedule":
        return schedule_text(trigger)
    if kind == "manual":
        return "When you message them in Chat"
    app = graph.primary_trigger_app()
    if app is not None:
        return app.phrase("when", f"When something arrives in {app.name}")
    types = graph.trigger_types
    if SCHEDULE_TRIGGER_TYPE in types:
        return "On a schedule"
    if CHAT_TRIGGER_TYPE in types:
        return "When you message them in Chat"
    if types:
        return "When the workflow is triggered"
    return "When you start them"


def _waiting_text(employee: Any, graph: GraphIndex) -> str:
    trigger = getattr(employee, "trigger", None) or {}
    kind = trigger.get("kind") if isinstance(trigger, Mapping) else None
    app = get_app(str(trigger.get("app") or "")) if kind == "app_event" else None
    app = app or graph.primary_trigger_app()
    if app is not None and app.phrase("waiting"):
        return app.phrase("waiting")
    if kind == "schedule":
        return f"Waiting for the next run ({schedule_text(trigger).lower()})"
    if SCHEDULE_TRIGGER_TYPE in graph.trigger_types:
        return "Waiting for the next scheduled run"
    if kind == "manual" or CHAT_TRIGGER_TYPE in graph.trigger_types:
        return "Waiting for your messages"
    return "Waiting for work"


def _status(control: Mapping[str, Any]) -> str:
    state = control.get("state")
    if state == "failed":
        return "attention"
    if state in PAUSED_STATES:
        return "attention" if control.get("pause_reason") else "paused"
    if state in WORKING_STATES:
        return "working"
    return "ready"


def _attention_text(control: Mapping[str, Any]) -> str:
    detail = control.get("pause_detail") or control.get("terminal_reason")
    if control.get("state") in PAUSED_STATES:
        return str(detail) if detail else "Paused after repeated errors. Resume when it's fixed."
    return "Stopped after an error. Open the workflow to see what happened."


def _task(
    status: str,
    *,
    control: Mapping[str, Any],
    pending: int,
    missing: List[Dict[str, Any]],
    needs_ai: bool,
    employee: Any,
    graph: GraphIndex,
) -> Dict[str, str]:
    if pending > 0:
        return {"label": "Waiting", "text": f"{pending} {_plural(pending, 'draft is', 'drafts are')} waiting for you to check"}
    if status == "working":
        return {"label": "Now", "text": _waiting_text(employee, graph)}
    if status == "attention":
        return {"label": "Paused", "text": _attention_text(control)}
    if status == "paused":
        return {"label": "Paused", "text": "Paused. Resume to pick up where they left off."}
    if missing:
        return {"label": "Next", "text": f"Connect {missing[0]['name']} to start"}
    if needs_ai:
        return {"label": "Next", "text": "Connect an AI model to start"}
    return {"label": "Next", "text": "Ready to start"}


def _derived_role(graph: GraphIndex) -> str:
    app = graph.primary_trigger_app()
    if graph.has_agent:
        if app is not None:
            return f"{app.name} assistant"
        if SCHEDULE_TRIGGER_TYPE in graph.trigger_types:
            return "Scheduled assistant"
        return "AI assistant"
    return "Workflow"


def _app_ids(employee: Any, graph: GraphIndex) -> List[str]:
    """Hired: the apps the owner agreed to (known ones), plus any the graph
    uses that the row does not list. Derived: the graph's apps."""
    ids: List[str] = []
    for app_id in list(getattr(employee, "apps", None) or []) + list(graph.app_ids):
        if app_id not in ids and get_app(app_id) is not None:
            ids.append(app_id)
    return ids


async def _summary(
    workflow: Any,
    employee: Any,
    control_row: Any,
    *,
    connections: Connections,
    needs_ai: bool,
    pending: int,
    done_today: int,
) -> Dict[str, Any]:
    graph = index_graph(getattr(workflow, "data", None))
    control = serialize_control(control_row)
    control.setdefault("workflow_id", workflow.id)
    apps = [await connections.app_ref(get_app(app_id)) for app_id in _app_ids(employee, graph)]
    missing = [ref for ref in apps if not ref["connected"]]
    status = _status(control)
    roles = getattr(employee, "node_roles", None) or {}
    watch = [roles[key] for key in ("agent", "todos") if roles.get(key)] if roles else []
    if not watch:
        watch = list(graph.agent_ids) + list(graph.todo_ids)
    # The board Home's Workspace shows: the one the hire made, while it is
    # still in the graph, else the first canvas the owner added.
    canvas = roles.get("canvas") if roles else None
    canvas_node_id = canvas if canvas in graph.canvas_ids else next(iter(graph.canvas_ids), None)
    return {
        "workflow_id": workflow.id,
        "name": workflow.name,
        "role": (getattr(employee, "role", "") or _derived_role(graph)),
        "color_role": getattr(employee, "color_role", None) or "agent",
        "derived": employee is None,
        "status": status,
        "task": _task(status, control=control, pending=pending, missing=missing, needs_ai=needs_ai, employee=employee, graph=graph),
        "done_today": done_today,
        "pending_approvals": pending,
        "apps": apps,
        "missing_apps": missing,
        "unsupported_apps": list(getattr(employee, "unsupported_apps", None) or []),
        "needs_ai": needs_ai,
        "control": control,
        "watch_node_ids": watch,
        "canvas_node_id": canvas_node_id,
        "browser_nodes": [{"node_id": node_id, "label": graph.labels.get(node_id) or "Browser"} for node_id in graph.browser_ids],
        "revision": _millis(
            getattr(workflow, "updated_at", None),
            getattr(employee, "updated_at", None),
            getattr(control_row, "updated_at", None),
        ),
        "hired_at": employee.hired_at.isoformat() if employee is not None and employee.hired_at else None,
    }


async def _pending_counts(database: Any, workflow_ids: Iterable[str]) -> Dict[str, int]:
    """Drafts waiting for the owner, per workflow (the approval gate)."""
    from services.approvals.queries import pending_approvals_by_workflow

    try:
        return await pending_approvals_by_workflow(database, workflow_ids)
    except Exception:
        logger.warning("Could not count waiting drafts", exc_info=True)
        return {}


async def _owner_zone(database: Any) -> ZoneInfo:
    """The owner's time zone (Settings > Profile), UTC when unset or unreadable."""
    try:
        settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    except Exception:
        settings = {}
    return runs.owner_zone(settings.get("profile_timezone"))


async def _done_today(database: Any, workflow_ids: Iterable[str]) -> Dict[str, int]:
    """Successful runs since the start of the owner's day, per workflow."""
    zone = await _owner_zone(database)
    try:
        return await runs.done_today(database, workflow_ids, zone=zone)
    except Exception:
        logger.warning("Could not count today's runs", exc_info=True)
        return {}


async def employee_usage(database: Any) -> Dict[str, int]:
    """Settings > Billing: the team's successful runs so far this month.

    Errors propagate, so the page shows that the count is unavailable
    rather than a zero.
    """
    zone = await _owner_zone(database)
    return {"tasks_this_month": await runs.done_this_month(database, zone=zone)}


async def _latest_run(database: Any, workflow_id: str) -> Optional[Dict[str, Any]]:
    """The most recent finished run."""
    try:
        return await runs.latest_run(database, workflow_id)
    except Exception:
        logger.warning("Could not read the latest run", workflow_id=workflow_id, exc_info=True)
        return None


async def list_employee_summaries(database: Any, *, auth_service: Any) -> List[Dict[str, Any]]:
    """Every workflow as an employee, most recently changed first.

    No per-owner filtering yet: like the editor's workflow list, every
    authenticated principal sees every workflow (see authentication.md,
    Known Limitations). Hired rows record their owner for when that lands.
    """
    workflows = await database.get_all_workflows()
    ids = [workflow.id for workflow in workflows]
    employees = await store.list_by_workflow_ids(database, ids)
    controls = await database.list_latest_workflow_controls(ids)
    pending = await _pending_counts(database, ids)
    done = await _done_today(database, ids)
    connections = Connections(auth_service)
    needs_ai = not await connections.has_ai()
    return [
        await _summary(
            workflow,
            employees.get(workflow.id),
            controls.get(workflow.id),
            connections=connections,
            needs_ai=needs_ai,
            pending=pending.get(workflow.id, 0),
            done_today=done.get(workflow.id, 0),
        )
        for workflow in workflows
    ]


async def _load_one(database: Any, workflow_id: str, *, auth_service: Any) -> Optional[tuple]:
    workflow = await database.get_workflow(workflow_id)
    if workflow is None:
        return None
    employee = await store.get_by_workflow(database, workflow_id)
    control = await database.get_latest_workflow_control(workflow_id)
    pending = await _pending_counts(database, [workflow_id])
    done = await _done_today(database, [workflow_id])
    connections = Connections(auth_service)
    summary = await _summary(
        workflow,
        employee,
        control,
        connections=connections,
        needs_ai=not await connections.has_ai(),
        pending=pending.get(workflow_id, 0),
        done_today=done.get(workflow_id, 0),
    )
    return summary, workflow, employee


async def get_employee_summary(database: Any, workflow_id: str, *, auth_service: Any) -> Optional[Dict[str, Any]]:
    loaded = await _load_one(database, workflow_id, auth_service=auth_service)
    return loaded[0] if loaded else None


async def get_employee_detail(database: Any, workflow_id: str, *, auth_service: Any) -> Optional[Dict[str, Any]]:
    """The summary plus what the employee card and its setup screen show."""
    loaded = await _load_one(database, workflow_id, auth_service=auth_service)
    if loaded is None:
        return None
    summary, workflow, employee = loaded
    last_run = await _latest_run(database, workflow_id)
    return {
        **summary,
        "description": getattr(employee, "description", "") or "",
        "job": getattr(employee, "job", "") or "",
        "plan": list(getattr(employee, "plan", None) or []),
        "rules": dict(getattr(employee, "rules", None) or {}),
        "choices": list(getattr(employee, "choices", None) or []),
        "trigger_text": _trigger_text(employee, index_graph(getattr(workflow, "data", None))),
        "latest_report": (last_run or {}).get("report"),
        "last_run": last_run,
    }


__all__ = [
    "employee_usage",
    "get_employee_detail",
    "get_employee_summary",
    "list_employee_summaries",
    "schedule_text",
]
