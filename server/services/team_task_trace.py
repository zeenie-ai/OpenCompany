"""Authorized, sanitized Temporal histories for durable team-task attempts."""

from __future__ import annotations

import base64
import re
import uuid
from typing import Any, Dict, Iterable, Optional

from temporalio.api.enums.v1 import EventType

_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|session[_-]?token)"
    r"(\s*[:=]\s*|\s+)[^\s,;]+"
)
_FAILURE_EVENT_NAMES = {"ACTIVITY_TASK_FAILED", "CHILD_WORKFLOW_EXECUTION_FAILED",
                        "WORKFLOW_EXECUTION_FAILED", "WORKFLOW_TASK_FAILED",
                        "ACTIVITY_TASK_TIMED_OUT", "CHILD_WORKFLOW_EXECUTION_TIMED_OUT"}


def _redact(value: Any, limit: int = 2000) -> str:
    text = str(value or "")[:limit]
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def _cursor_decode(cursor: Optional[str]) -> Optional[bytes]:
    if not cursor:
        return None
    try:
        return base64.urlsafe_b64decode(cursor.encode("ascii"))
    except Exception as exc:
        raise ValueError("Invalid trace cursor") from exc


def _cursor_encode(token: Optional[bytes]) -> Optional[str]:
    return base64.urlsafe_b64encode(token).decode("ascii") if token else None


def _event_name(event: Any) -> str:
    try:
        return EventType.Name(event.event_type).removeprefix("EVENT_TYPE_")
    except Exception:
        return "UNKNOWN"


def _normalize_event(event: Any) -> Dict[str, Any]:
    name = _event_name(event)
    result: Dict[str, Any] = {
        "event_id": int(getattr(event, "event_id", 0)),
        "type": name.lower(),
        "category": (
            "failure" if name in _FAILURE_EVENT_NAMES
            else "activity" if "ACTIVITY" in name
            else "child" if "CHILD_WORKFLOW" in name
            else "signal" if "SIGNAL" in name
            else "timer" if "TIMER" in name
            else "workflow"
        ),
    }
    event_time = getattr(event, "event_time", None)
    if event_time:
        try:
            result["timestamp"] = event_time.ToDatetime().isoformat()
        except Exception:
            pass
    try:
        attr_name = event.WhichOneof("attributes")
        attrs = getattr(event, attr_name) if attr_name else None
    except Exception:
        attrs = None
    if attrs is not None:
        activity_type = getattr(getattr(attrs, "activity_type", None), "name", "")
        workflow_type = getattr(getattr(attrs, "workflow_type", None), "name", "")
        execution = getattr(attrs, "workflow_execution", None)
        failure = getattr(attrs, "failure", None)
        if activity_type:
            result["activity_type"] = _redact(activity_type, 255)
        if workflow_type:
            result["workflow_type"] = _redact(workflow_type, 255)
        if execution and getattr(execution, "workflow_id", ""):
            result["child_workflow_id"] = _redact(execution.workflow_id, 500)
            result["child_run_id"] = _redact(getattr(execution, "run_id", ""), 255)
        attempt = getattr(attrs, "attempt", 0)
        if attempt:
            result["attempt"] = int(attempt)
        retry_state = getattr(attrs, "retry_state", 0)
        if retry_state:
            result["retry_state"] = int(retry_state)
        if failure and getattr(failure, "message", ""):
            result["failure"] = {"message": _redact(failure.message)}
    return result


def _search_text(event: Dict[str, Any]) -> str:
    """Build a bounded searchable projection without exposing raw payloads."""
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (str, int)):
            values.append(str(value))

    collect(event)
    return " ".join(values)[:5000]


def _matches_search(
    event: Dict[str, Any], query: str, *, mode: str, case_sensitive: bool,
) -> bool:
    haystack = _search_text(event)
    needle = query.strip()
    if not case_sensitive:
        haystack, needle = haystack.casefold(), needle.casefold()
    if mode == "literal":
        return needle in haystack
    terms = [term for term in needle.split() if term]
    return bool(terms) and (all(term in haystack for term in terms) if mode == "all_terms"
                            else any(term in haystack for term in terms))


def _contextual_events(
    events: list[Dict[str, Any]], matched_indexes: Iterable[int], context_lines: int,
) -> list[Dict[str, Any]]:
    matched = set(matched_indexes)
    selected: set[int] = set()
    for index in matched:
        selected.update(range(max(0, index - context_lines), min(len(events), index + context_lines + 1)))
    return [{**events[index], "match": index in matched} for index in sorted(selected)]


class TeamTaskTraceService:
    def __init__(self, team_service: Any, temporal_wrapper: Any):
        self.team_service = team_service
        self.temporal_wrapper = temporal_wrapper

    async def get_trace(
        self, *, workflow_id: str, team_lead_node_id: str,
        execution_id: Optional[str], task_id: str, attempt: Optional[int] = None,
        cursor: Optional[str] = None, limit: int = 50, detail: str = "summary",
        query: Optional[str] = None, search_mode: str = "literal",
        case_sensitive: bool = False, context_lines: int = 2, scan_limit: int = 250,
        categories: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        if detail not in {"summary", "failures", "timeline", "search"}:
            raise ValueError("detail must be summary, failures, timeline, or search")
        if detail == "search" and not str(query or "").strip():
            raise ValueError("trace search requires a non-empty query")
        if search_mode not in {"literal", "all_terms", "any_terms"}:
            raise ValueError("search_mode must be literal, all_terms, or any_terms")
        if query is not None and len(query) > 200:
            raise ValueError("trace search query is limited to 200 characters")
        limit = max(1, min(int(limit), 100))
        context_lines = max(0, min(int(context_lines), 5))
        scan_limit = max(1, min(int(scan_limit), 500))
        allowed_categories = {"failure", "activity", "child", "signal", "timer", "workflow"}
        category_filter = set(categories or [])
        if not category_filter.issubset(allowed_categories):
            raise ValueError("invalid trace category filter")
        task = await self.team_service.get_durable_task(
            workflow_id=workflow_id, team_lead_node_id=team_lead_node_id,
            execution_id=execution_id, task_id=task_id,
        )
        attempts = task.get("attempts") or []
        attempt_number = task.get("current_attempt", 0) if attempt is None else int(attempt)
        selected = next((item for item in attempts if item["attempt_number"] == attempt_number), None)
        if selected is None and attempt_number == task.get("current_attempt", 0):
            selected = task
        workflow_identity = selected.get("child_workflow_id") or selected.get("runner_workflow_id")
        run_identity = selected.get("child_run_id") or selected.get("runner_run_id")
        base = {
            "task_id": task_id, "attempt": attempt_number, "detail": detail,
            "trace_id": task.get("trace_id"),
            "execution": {
                "workflow_id": workflow_identity, "run_id": run_identity,
                "runner_workflow_id": selected.get("runner_workflow_id"),
                "runner_run_id": selected.get("runner_run_id"),
                "child_workflow_id": selected.get("child_workflow_id"),
                "child_run_id": selected.get("child_run_id"),
                "parent_workflow_id": selected.get("parent_workflow_id"),
                "parent_run_id": selected.get("parent_run_id"),
            },
        }
        await self.team_service.database.add_agent_message(
            team_id=task["team_id"], from_agent=team_lead_node_id,
            to_agent=team_lead_node_id, message_type="trace_inspection",
            content=f"Inspected task {task_id} attempt {attempt_number} ({detail})",
            event_id=f"trace-inspection:{uuid.uuid4().hex}",
            extra_data={"task_id": task_id, "attempt": attempt_number, "detail": detail,
                        "search": detail == "search", "query_length": len(query or "")},
        )
        if not workflow_identity:
            return {**base, "status": "execution_not_registered", "events": [], "next_cursor": None}
        client = self.temporal_wrapper.client if self.temporal_wrapper else None
        if client is None:
            return {**base, "status": "temporal_unavailable", "events": [], "next_cursor": None}
        try:
            handle = client.get_workflow_handle(workflow_identity, run_id=run_identity)
            page_size = min(100, scan_limit if detail == "search" else limit)
            iterator = handle.fetch_history_events(
                page_size=page_size, next_page_token=_cursor_decode(cursor), skip_archival=False,
            )
            events: list[Dict[str, Any]] = []
            matched_indexes: list[int] = []
            scan_cap = scan_limit if detail == "search" else limit
            scanned = 0
            while scanned < scan_cap:
                remaining = scan_cap - scanned
                await iterator.fetch_next_page(page_size=min(100, remaining))
                raw_page = list(iterator.current_page or [])
                scanned += len(raw_page)
                page = [_normalize_event(event) for event in raw_page]
                if category_filter:
                    page = [event for event in page if event["category"] in category_filter]
                start = len(events)
                events.extend(page)
                if detail == "search":
                    matched_indexes.extend(
                        start + index for index, event in enumerate(page)
                        if _matches_search(event, str(query), mode=search_mode, case_sensitive=case_sensitive)
                    )
                    if len(matched_indexes) >= limit:
                        break
                else:
                    break
                if not iterator.next_page_token or not raw_page:
                    break
            if detail == "failures":
                events = [event for event in events if event["category"] == "failure"]
            elif detail == "summary":
                events = [event for event in events if event["category"] in {"failure", "activity", "child"}]
            elif detail == "search":
                events = _contextual_events(events, matched_indexes, context_lines)
                base["search"] = {
                    "mode": search_mode, "case_sensitive": case_sensitive,
                    "context_lines": context_lines, "scanned_events": scanned,
                    "matched_events": len(matched_indexes),
                }
            return {**base, "status": "available", "events": events,
                    "next_cursor": _cursor_encode(iterator.next_page_token)}
        except ValueError:
            raise
        except Exception as exc:
            name = type(exc).__name__.lower()
            status = "retention_expired" if "notfound" in name or "not found" in str(exc).lower() else "temporal_unavailable"
            return {**base, "status": status, "events": [], "next_cursor": None,
                    "error": _redact(exc, 500)}


def get_team_task_trace_service() -> TeamTaskTraceService:
    from core.container import container
    from services.agent_team import get_agent_team_service

    return TeamTaskTraceService(get_agent_team_service(), container.temporal_client())
