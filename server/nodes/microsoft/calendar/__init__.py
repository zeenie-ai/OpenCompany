"""Outlook Calendar via Microsoft Graph — multi-op ActionNode + AI tool.

Operations (dispatched off ``params.operation``):
- create -> POST   /me/events
- list   -> GET    /me/calendarView?startDateTime=&endDateTime=
- update -> PATCH  /me/events/{id}
- delete -> DELETE /me/events/{id}

Times are ISO 8601. ``list`` accepts ``today`` / ``today+Nd`` shortcuts
for the start/end window (mirrors the Google Calendar node).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from .._base import graph_request, track_microsoft_usage
from .._credentials import MicrosoftCredential

_CREATE = {"displayOptions": {"show": {"operation": ["create"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_UPDATE = {"displayOptions": {"show": {"operation": ["update"]}}}
_UPDATE_OR_DELETE = {"displayOptions": {"show": {"operation": ["update", "delete"]}}}


class CalendarParams(BaseModel):
    operation: Literal["create", "list", "update", "delete"] = "list"

    event_id: str = Field(default="", json_schema_extra=_UPDATE_OR_DELETE)

    # Create
    title: str = Field(default="", json_schema_extra=_CREATE)
    body: str = Field(default="", json_schema_extra={"rows": 3, **_CREATE})
    start_time: str = Field(
        default="",
        json_schema_extra={"placeholder": "2026-08-10T14:00:00", **_CREATE},
    )
    end_time: str = Field(
        default="",
        json_schema_extra={"placeholder": "2026-08-10T15:00:00", **_CREATE},
    )
    location: str = Field(default="", json_schema_extra=_CREATE)
    attendees: str = Field(
        default="",
        json_schema_extra={"placeholder": "alice@contoso.com, bob@contoso.com", **_CREATE},
    )
    timezone: str = Field(default="UTC", json_schema_extra=_CREATE)

    # List
    start_date: str = Field(default="", json_schema_extra={"placeholder": "today", **_LIST})
    end_date: str = Field(default="", json_schema_extra={"placeholder": "today+7d", **_LIST})
    max_results: int = Field(default=10, ge=1, le=250, json_schema_extra=_LIST)

    # Update
    update_title: str = Field(default="", json_schema_extra=_UPDATE)
    update_start_time: str = Field(default="", json_schema_extra=_UPDATE)
    update_end_time: str = Field(default="", json_schema_extra=_UPDATE)
    update_body: str = Field(default="", json_schema_extra={"rows": 3, **_UPDATE})
    update_location: str = Field(default="", json_schema_extra=_UPDATE)

    model_config = ConfigDict(extra="ignore")


class CalendarOutput(BaseModel):
    operation: Optional[str] = None
    event_id: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    web_link: Optional[str] = None
    location: Optional[str] = None
    events: Optional[List[dict]] = None
    count: Optional[int] = None
    time_range: Optional[dict] = None
    deleted: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


def _iso_or_shortcut(value: str, default_offset_days: int = 0) -> str:
    """Resolve ISO datetime or ``today`` / ``today+Nd`` shortcut to ISO 8601."""
    now = datetime.utcnow()
    if not value or value.lower() == "today":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0) if default_offset_days == 0 else now + timedelta(days=default_offset_days)
        return base.isoformat()
    if value.startswith("today+"):
        days = int(value.replace("today+", "").replace("d", ""))
        return (now + timedelta(days=days)).isoformat()
    return value


def _attendees(raw: str) -> list:
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split(";")]
    return [{"emailAddress": {"address": addr}, "type": "required"} for addr in parts if addr]


def _summarize(ev: dict) -> dict:
    return {
        "event_id": ev.get("id"),
        "title": ev.get("subject", "No Title"),
        "start": (ev.get("start") or {}).get("dateTime"),
        "end": (ev.get("end") or {}).get("dateTime"),
        "location": (ev.get("location") or {}).get("displayName", ""),
        "organizer": ((ev.get("organizer") or {}).get("emailAddress") or {}).get("address", ""),
        "web_link": ev.get("webLink"),
        "is_all_day": ev.get("isAllDay"),
    }


class CalendarNode(ActionNode):
    type = "msCalendar"
    display_name = "Outlook Calendar"
    subtitle = "Event Management"
    group = ("microsoft", "tool")
    description = "Microsoft Outlook Calendar create / list / update / delete events via Graph (workflow + AI tool)"
    component_kind = "square"
    tool_name = "ms_calendar"
    tool_description = (
        "Manage Outlook Calendar events via Microsoft Graph. Operations: "
        "create (new event), list (events in a date range), update (modify event), delete (remove event)."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (MicrosoftCredential,)
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = CalendarParams
    Output = CalendarOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        op = params.operation
        if op == "create":
            return await self._create(ctx, params)
        if op == "list":
            return await self._list(ctx, params)
        if op == "update":
            return await self._update(ctx, params)
        if op == "delete":
            return await self._delete(ctx, params)
        raise NodeUserError(f"Unknown Calendar operation: {op}")

    async def _create(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        if not params.title:
            raise NodeUserError("Event title is required")
        if not params.start_time or not params.end_time:
            raise NodeUserError("Start time and end time are required")

        event = {
            "subject": params.title,
            "start": {"dateTime": params.start_time, "timeZone": params.timezone},
            "end": {"dateTime": params.end_time, "timeZone": params.timezone},
        }
        if params.body:
            event["body"] = {"contentType": "Text", "content": params.body}
        if params.location:
            event["location"] = {"displayName": params.location}
        if params.attendees:
            event["attendees"] = _attendees(params.attendees)

        result = await graph_request(ctx, "POST", "/me/events", json=event)
        await track_microsoft_usage(ctx.node_id, "create", 1, ctx.raw)
        summary = _summarize(result or {})
        return CalendarOutput(
            operation="create",
            event_id=summary["event_id"],
            title=summary["title"],
            start=summary["start"],
            end=summary["end"],
            location=summary["location"],
            web_link=summary["web_link"],
        )

    async def _list(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        start = _iso_or_shortcut(params.start_date)
        end = _iso_or_shortcut(params.end_date, default_offset_days=7)
        data = await graph_request(
            ctx,
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": start,
                "endDateTime": end,
                "$top": min(params.max_results, 250),
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,organizer,webLink,isAllDay",
            },
        )
        items = (data or {}).get("value", [])
        formatted = [_summarize(e) for e in items]
        await track_microsoft_usage(ctx.node_id, "list", len(formatted), ctx.raw)
        return CalendarOutput(
            operation="list",
            events=formatted,
            count=len(formatted),
            time_range={"start": start, "end": end},
        )

    async def _update(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        if not params.event_id:
            raise NodeUserError("Event ID is required")

        patch: dict = {}
        if params.update_title:
            patch["subject"] = params.update_title
        if params.update_start_time:
            patch["start"] = {"dateTime": params.update_start_time, "timeZone": params.timezone}
        if params.update_end_time:
            patch["end"] = {"dateTime": params.update_end_time, "timeZone": params.timezone}
        if params.update_body:
            patch["body"] = {"contentType": "Text", "content": params.update_body}
        if params.update_location:
            patch["location"] = {"displayName": params.update_location}

        if not patch:
            raise NodeUserError("No update fields provided")

        result = await graph_request(ctx, "PATCH", f"/me/events/{params.event_id}", json=patch)
        await track_microsoft_usage(ctx.node_id, "update", 1, ctx.raw)
        summary = _summarize(result or {})
        return CalendarOutput(
            operation="update",
            event_id=summary["event_id"] or params.event_id,
            title=summary["title"],
            start=summary["start"],
            end=summary["end"],
            location=summary["location"],
            web_link=summary["web_link"],
        )

    async def _delete(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        if not params.event_id:
            raise NodeUserError("Event ID is required")
        await graph_request(ctx, "DELETE", f"/me/events/{params.event_id}")
        await track_microsoft_usage(ctx.node_id, "delete", 1, ctx.raw)
        return CalendarOutput(operation="delete", deleted=True, event_id=params.event_id)
