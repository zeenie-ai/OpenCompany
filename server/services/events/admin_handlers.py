"""Wave 12 D3: Visibility admin WS handlers.

Thin wrappers around Temporal's Visibility API so the MachinaOs admin
UI can inspect canary listener / schedule state without leaving the
app. No new DB tables, no custom persistence — Temporal Event History
+ Visibility queries ARE the registry (see
``docs-internal/event_framework.md`` § "Failure inspection").

Three WS message types:

  - ``list_canary_listeners`` →
      ``client.list_workflows(query="EventWorkflowId='X' AND WorkflowType IN (...)")``
      Returns running TriggerListenerWorkflow + PollingTriggerWorkflow
      instances for a deployment.

  - ``list_canary_schedules`` →
      ``client.list_schedules(query="EventWorkflowId='X' AND EventTriggerKind='cron'")``
      Returns Temporal Schedules created by the deployment's cron
      triggers (Wave 12 C3).

  - ``get_workflow_failure_history`` →
      ``client.get_workflow_handle(...).fetch_history()`` filtered to
      ActivityTaskFailed events for ops inspection of failed runs.

All three are read-only — no replay, no mutation. Cancel + delete are
already covered by ``DeploymentManager.cancel()`` (the production
cancel path). These handlers exist purely for visibility.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.plugin.ws import ws_response

logger = get_logger(__name__)


# Mirrors the workflow-type names declared in
# services.deployment.manager._LISTENER_WORKFLOW_TYPES. Re-declared
# here to keep this module's dependency graph minimal — admin code
# shouldn't pull in DeploymentManager.
_LISTENER_WORKFLOW_TYPES: tuple[str, ...] = (
    "TriggerListenerWorkflow",
    "PollingTriggerWorkflow",
)


@ws_response
async def handle_list_canary_listeners(
    data: Dict[str, Any], websocket: WebSocket,
) -> Dict[str, Any]:
    """List Temporal-durable canary listener workflows.

    Request shape::

        {"type": "list_canary_listeners", "workflow_id": "<deployment-id>"}

    Response::

        {
            "listeners": [
                {"id": "trigger-listener-<wf>-<node>",
                 "type": "TriggerListenerWorkflow",
                 "status": "RUNNING",
                 "start_time": "<ISO>",
                 "trigger_node_id": "<node>",
                 "event_type": "<com.machinaos. ... >"},
                ...
            ],
            "count": <int>,
        }
    """
    from core.container import container

    workflow_id = data.get("workflow_id")
    if not workflow_id:
        return {"success": False, "error": "workflow_id is required"}

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return {"success": True, "listeners": [], "count": 0,
                "note": "Temporal not connected"}

    wf_types_in = ", ".join(f"'{t}'" for t in _LISTENER_WORKFLOW_TYPES)
    query = (
        f"EventWorkflowId='{workflow_id}' "
        f"AND WorkflowType IN ({wf_types_in}) "
        f"AND ExecutionStatus='Running'"
    )

    listeners: List[Dict[str, Any]] = []
    try:
        async for wf in wrapper.client.list_workflows(query=query):
            listeners.append(_describe_workflow(wf))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"list_canary_listeners Visibility query failed: {exc} "
            f"(query={query!r})",
            workflow_id=workflow_id,
        )
        return {"success": False, "error": str(exc), "query": query}

    return {"success": True, "listeners": listeners, "count": len(listeners)}


@ws_response
async def handle_list_canary_schedules(
    data: Dict[str, Any], websocket: WebSocket,
) -> Dict[str, Any]:
    """List Temporal Schedules created by this deployment's cron triggers.

    Request shape::

        {"type": "list_canary_schedules", "workflow_id": "<deployment-id>"}

    Response::

        {
            "schedules": [
                {"id": "cron-schedule-<wf>-<node>",
                 "trigger_node_id": "<node>"},
                ...
            ],
            "count": <int>,
        }
    """
    from core.container import container

    workflow_id = data.get("workflow_id")
    if not workflow_id:
        return {"success": False, "error": "workflow_id is required"}

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return {"success": True, "schedules": [], "count": 0,
                "note": "Temporal not connected"}

    query = (
        f"EventWorkflowId='{workflow_id}' "
        f"AND EventTriggerKind='cron'"
    )

    schedules: List[Dict[str, Any]] = []
    try:
        # ``Client.list_schedules`` is ``async def`` — must await it
        # before iterating. Distinct from ``Client.list_workflows``
        # which returns the iterator directly. See
        # https://python.temporal.io/temporalio.client.Client.html#list_schedules
        iterator = await wrapper.client.list_schedules(query=query)
        async for desc in iterator:
            schedules.append(_describe_schedule(desc))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"list_canary_schedules Visibility query failed: {exc} "
            f"(query={query!r})",
            workflow_id=workflow_id,
        )
        return {"success": False, "error": str(exc), "query": query}

    return {"success": True, "schedules": schedules, "count": len(schedules)}


@ws_response
async def handle_get_workflow_failure_history(
    data: Dict[str, Any], websocket: WebSocket,
) -> Dict[str, Any]:
    """Return ActivityTaskFailed events from a workflow's Event History.

    Request shape::

        {"type": "get_workflow_failure_history",
         "workflow_id": "<temporal-workflow-id>",
         "run_id": "<optional-run-id>"}

    Response::

        {
            "workflow_id": "<id>",
            "failures": [
                {"event_id": <int>,
                 "event_type": "ActivityTaskFailed",
                 "event_time": "<ISO>",
                 "activity_id": "<id>",
                 "activity_type": "<type>",
                 "message": "<failure detail>"},
                ...
            ],
            "count": <int>,
        }

    Ops-only — no replay action available here. For more detail use
    Temporal Web UI at http://localhost:8233 or the temporal CLI:

        temporal workflow show --workflow-id <id> --output json
    """
    from core.container import container

    target_workflow_id = data.get("workflow_id")
    if not target_workflow_id:
        return {"success": False, "error": "workflow_id is required"}

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return {"success": False, "error": "Temporal not connected"}

    run_id: Optional[str] = data.get("run_id")
    try:
        handle = wrapper.client.get_workflow_handle(
            target_workflow_id, run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"workflow lookup failed: {exc}"}

    failures: List[Dict[str, Any]] = []
    try:
        async for event in handle.fetch_history_events():
            failure = _maybe_describe_failure(event)
            if failure is not None:
                failures.append(failure)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"get_workflow_failure_history history fetch failed: {exc}",
            workflow_id=target_workflow_id,
        )
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "workflow_id": target_workflow_id,
        "failures": failures,
        "count": len(failures),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _describe_workflow(wf_execution: Any) -> Dict[str, Any]:
    """Best-effort dict view of a WorkflowExecution from the Visibility list.

    Search-attribute access on ``WorkflowExecutionDescription`` varies
    by SDK version (some expose ``.search_attributes`` as a plain dict,
    others as TypedSearchAttributes). The helper tries the typed form
    first + falls back to plain dict lookup.
    """
    sa_view = _search_attributes_dict(getattr(wf_execution, "search_attributes", None))
    return {
        "id": getattr(wf_execution, "id", None),
        "run_id": getattr(wf_execution, "run_id", None),
        "type": _value_or_name(getattr(wf_execution, "workflow_type", None)),
        "status": _value_or_name(getattr(wf_execution, "status", None)),
        "start_time": _iso(getattr(wf_execution, "start_time", None)),
        "close_time": _iso(getattr(wf_execution, "close_time", None)),
        "trigger_node_id": sa_view.get("TriggerNodeId"),
        "event_type": sa_view.get("EventType"),
        "event_trigger_kind": sa_view.get("EventTriggerKind"),
    }


def _describe_schedule(schedule_desc: Any) -> Dict[str, Any]:
    """Best-effort dict view of a ScheduleListDescription."""
    sa_view = _search_attributes_dict(getattr(schedule_desc, "search_attributes", None))
    return {
        "id": getattr(schedule_desc, "id", None),
        "trigger_node_id": sa_view.get("TriggerNodeId"),
        "event_trigger_kind": sa_view.get("EventTriggerKind"),
    }


def _maybe_describe_failure(event: Any) -> Optional[Dict[str, Any]]:
    """Return the ActivityTaskFailed shape, or None when ``event`` isn't a failure.

    Temporal's HistoryEvent is a protobuf message; the failure detail
    lives at ``event.activity_task_failed_event_attributes.failure``.
    The protobuf API can change between SDK versions so we guard every
    attribute access.
    """
    event_type_attr = getattr(event, "event_type", None)
    event_type_name = _value_or_name(event_type_attr) or ""
    if "ActivityTaskFailed" not in str(event_type_name):
        return None

    attrs = getattr(event, "activity_task_failed_event_attributes", None)
    if attrs is None:
        return None

    failure = getattr(attrs, "failure", None)
    message = getattr(failure, "message", None) if failure is not None else None

    activity_id = None
    activity_type = None
    # Activity identification lives on the scheduled-event lookup but
    # the protobuf surface keeps it on the failure event too.
    activity_id = getattr(attrs, "activity_id", None)
    activity_type = getattr(attrs, "activity_type", None)
    if activity_type is not None:
        activity_type = getattr(activity_type, "name", str(activity_type))

    return {
        "event_id": getattr(event, "event_id", None),
        "event_type": "ActivityTaskFailed",
        "event_time": _iso(getattr(event, "event_time", None)),
        "activity_id": activity_id,
        "activity_type": activity_type,
        "message": message,
    }


def _search_attributes_dict(raw: Any) -> Dict[str, Any]:
    """Coerce TypedSearchAttributes / plain-dict / None into a flat dict view."""
    if raw is None:
        return {}
    # TypedSearchAttributes is iterable over SearchAttributePair-like
    # objects with ``key.name`` + ``value`` fields.
    if hasattr(raw, "__iter__") and not isinstance(raw, dict):
        result: Dict[str, Any] = {}
        for pair in raw:
            key = getattr(getattr(pair, "key", None), "name", None)
            if key is None:
                continue
            result[key] = getattr(pair, "value", None)
        if result:
            return result
    if isinstance(raw, dict):
        # Untyped search-attribute map from older SDK: name -> [value, ...]
        return {
            k: (v[0] if isinstance(v, list) and v else v)
            for k, v in raw.items()
        }
    return {}


def _value_or_name(obj: Any) -> Any:
    """Return ``.name`` for enum-like objects, otherwise the value itself."""
    if obj is None:
        return None
    name = getattr(obj, "name", None)
    if name is not None:
        return name
    return obj


def _iso(ts: Any) -> Optional[str]:
    """Format a datetime/Timestamp protobuf as ISO-8601."""
    if ts is None:
        return None
    # datetime objects: have .isoformat()
    isofmt = getattr(ts, "isoformat", None)
    if callable(isofmt):
        try:
            return isofmt()
        except Exception:  # noqa: BLE001
            pass
    # Fallback: stringify.
    return str(ts)


# Wire-key → handler dispatch table the WS router picks up via
# services.ws_handler_registry.register_ws_handlers.
WS_HANDLERS: Dict[str, Any] = {
    "list_canary_listeners": handle_list_canary_listeners,
    "list_canary_schedules": handle_list_canary_schedules,
    "get_workflow_failure_history": handle_get_workflow_failure_history,
}


__all__ = [
    "WS_HANDLERS",
    "handle_list_canary_listeners",
    "handle_list_canary_schedules",
    "handle_get_workflow_failure_history",
]
