"""Google Calendar — Wave 11.D.4 inlined."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage


_CREATE = {"displayOptions": {"show": {"operation": ["create"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_UPDATE = {"displayOptions": {"show": {"operation": ["update"]}}}
_UPDATE_OR_DELETE = {"displayOptions": {"show": {"operation": ["update", "delete"]}}}


class CalendarParams(BaseModel):
    operation: Literal["create", "list", "update", "delete"] = "list"
    calendar_id: str = Field(
        default="primary",
        json_schema_extra={"loadOptionsMethod": "googleCalendarList"},
    )
    event_id: Optional[str] = Field(default=None, json_schema_extra=_UPDATE_OR_DELETE)

    # Create fields
    title: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    description: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    start_time: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    end_time: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    location: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    attendees: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    timezone: str = Field(default="UTC", json_schema_extra=_CREATE)
    reminder_minutes: int = Field(default=30, json_schema_extra=_CREATE)

    # List fields
    start_date: Optional[str] = Field(default=None, json_schema_extra=_LIST)
    end_date: Optional[str] = Field(default=None, json_schema_extra=_LIST)
    max_results: int = Field(default=10, ge=1, le=250, json_schema_extra=_LIST)
    single_events: bool = Field(default=True, json_schema_extra=_LIST)
    order_by: str = Field(default="startTime", json_schema_extra=_LIST)

    # Update + delete
    send_updates: Literal["all", "externalOnly", "none"] = Field(
        default="all",
        json_schema_extra=_UPDATE_OR_DELETE,
    )

    # Update-only fields
    update_title: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_start_time: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_end_time: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_description: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_location: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)

    model_config = ConfigDict(extra="ignore")


class CalendarOutput(BaseModel):
    operation: Optional[str] = None
    event_id: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    html_link: Optional[str] = None
    status: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    events: Optional[List[dict]] = None
    count: Optional[int] = None
    time_range: Optional[dict] = None
    deleted: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


def _iso_or_shortcut(value: Optional[str], default_offset_days: int = 0) -> str:
    now = datetime.utcnow()
    if not value or value.lower() == "today":
        base = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            if default_offset_days == 0
            else now + timedelta(days=default_offset_days)
        )
        return base.isoformat() + "Z"
    if value.startswith("today+"):
        days = int(value.replace("today+", "").replace("d", ""))
        return (now + timedelta(days=days)).isoformat() + "Z"
    return value if value.endswith("Z") else value + "Z"


class CalendarNode(ActionNode):
    type = "googleCalendar"
    display_name = "Calendar"
    subtitle = "Event Management"
    group = ("google", "tool")
    description = "Google Calendar create / list / update / delete events"
    component_kind = "square"
    tool_name = "google_calendar"
    tool_description = "Manage Google Calendar events. Operations: create (new event), list (events in date range), update (modify event), delete (remove event)."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (GoogleCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = CalendarParams
    Output = CalendarOutput

    @Operation("dispatch", cost={"service": "calendar", "action": "op", "count": 1})
    async def dispatch(self, ctx: NodeContext, params: CalendarParams) -> CalendarOutput:
        svc = await build_google_service("calendar", "v3", params.model_dump(), ctx.raw)
        events_svc = svc.events()
        op = params.operation
        cal_id = params.calendar_id

        if op == "create":
            if not params.title:
                raise RuntimeError("Event title is required")
            if not params.start_time or not params.end_time:
                raise RuntimeError("Start time and end time are required")

            event = {
                "summary": params.title,
                "start": {"dateTime": params.start_time, "timeZone": params.timezone},
                "end": {"dateTime": params.end_time, "timeZone": params.timezone},
            }
            if params.description:
                event["description"] = params.description
            if params.location:
                event["location"] = params.location
            if params.attendees:
                att = [{"email": e.strip()} for e in params.attendees.split(",") if e.strip()]
                if att:
                    event["attendees"] = att
            if params.reminder_minutes:
                event["reminders"] = {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": int(params.reminder_minutes)}],
                }

            result = await run_sync(
                lambda: events_svc.insert(
                    calendarId=cal_id,
                    body=event,
                    sendUpdates=params.send_updates,
                ).execute()
            )
            await track_google_usage("google_calendar", ctx.node_id, "create", 1, ctx.raw)
            return CalendarOutput(
                operation="create",
                event_id=result.get("id"),
                title=result.get("summary"),
                start=result.get("start", {}).get("dateTime"),
                end=result.get("end", {}).get("dateTime"),
                html_link=result.get("htmlLink"),
                status=result.get("status"),
                created=result.get("created"),
            )

        if op == "list":
            time_min = _iso_or_shortcut(params.start_date)
            time_max = _iso_or_shortcut(params.end_date, default_offset_days=7)
            single = params.single_events
            kwargs = dict(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=min(params.max_results, 250),
                singleEvents=single,
            )
            if single:
                kwargs["orderBy"] = params.order_by

            result = await run_sync(lambda: events_svc.list(**kwargs).execute())
            raw = result.get("items", [])
            formatted = [
                {
                    "event_id": e.get("id"),
                    "title": e.get("summary", "No Title"),
                    "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
                    "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date")),
                    "description": e.get("description", ""),
                    "location": e.get("location", ""),
                    "status": e.get("status"),
                    "html_link": e.get("htmlLink"),
                    "attendees": [a.get("email") for a in e.get("attendees", [])],
                }
                for e in raw
            ]
            await track_google_usage("google_calendar", ctx.node_id, "list", len(formatted), ctx.raw)
            return CalendarOutput(
                operation="list",
                events=formatted,
                count=len(formatted),
                time_range={"start": time_min, "end": time_max},
            )

        if op in ("update", "delete"):
            if not params.event_id:
                raise RuntimeError("Event ID is required")

            if op == "delete":
                await run_sync(
                    lambda: events_svc.delete(
                        calendarId=cal_id,
                        eventId=params.event_id,
                        sendUpdates=params.send_updates,
                    ).execute()
                )
                await track_google_usage("google_calendar", ctx.node_id, "delete", 1, ctx.raw)
                return CalendarOutput(operation="delete", deleted=True, event_id=params.event_id)

            event = await run_sync(lambda: events_svc.get(calendarId=cal_id, eventId=params.event_id).execute())
            title = params.update_title or params.title
            start = params.update_start_time or params.start_time
            end = params.update_end_time or params.end_time
            desc = params.update_description if params.update_description is not None else params.description
            loc = params.update_location if params.update_location is not None else params.location

            if title:
                event["summary"] = title
            if start:
                tz = event.get("start", {}).get("timeZone", "UTC")
                event["start"] = {"dateTime": start, "timeZone": tz}
            if end:
                tz = event.get("end", {}).get("timeZone", "UTC")
                event["end"] = {"dateTime": end, "timeZone": tz}
            if desc is not None:
                event["description"] = desc
            if loc is not None:
                event["location"] = loc

            result = await run_sync(
                lambda: events_svc.update(
                    calendarId=cal_id,
                    eventId=params.event_id,
                    body=event,
                    sendUpdates=params.send_updates,
                ).execute()
            )
            await track_google_usage("google_calendar", ctx.node_id, "update", 1, ctx.raw)
            return CalendarOutput(
                operation="update",
                event_id=result.get("id"),
                title=result.get("summary"),
                start=result.get("start", {}).get("dateTime"),
                end=result.get("end", {}).get("dateTime"),
                updated=result.get("updated"),
                html_link=result.get("htmlLink"),
            )

        raise RuntimeError(f"Unknown Calendar operation: {op}")
