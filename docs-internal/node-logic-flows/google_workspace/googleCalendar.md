# Calendar (`googleCalendar`)

| Field | Value |
|------|-------|
| **Category** | google_workspace / tool (dual-purpose) |
| **Backend handler** | [`server/nodes/google/calendar/__init__.py`](../../../server/nodes/google/calendar/__init__.py) (`CalendarNode`; dispatched via `BaseNode.execute()` -> single `@Operation("dispatch")` method that branches on `params.operation`) |
| **Tests** | [`server/tests/nodes/test_google_workspace.py`](../../../server/tests/nodes/test_google_workspace.py) |
| **Skill (if any)** | [`server/skills/productivity_agent/google-calendar-skill/SKILL.md`](../../../server/skills/productivity_agent/google-calendar-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `google_calendar` |

## Purpose

Consolidated Google Calendar node covering create, list, update, delete
operations on calendar events. Uses Google Calendar API v3. One node, four
operations switched via the `operation` parameter.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Template source for operation parameters |

## Parameters

Top-level dispatcher: `operation` (one of `create`, `list`, `update`, `delete`).

### `operation = create`

| Name | Type | Default | Required | Description |
|------|------|---------|----------|-------------|
| `title` | string | `""` | **yes** | Event summary |
| `start_time` | string | `""` | **yes** | ISO 8601 datetime |
| `end_time` | string | `""` | **yes** | ISO 8601 datetime |
| `description` | string | `""` | no | Event description |
| `location` | string | `""` | no | Location text |
| `attendees` | string | `""` | no | Comma-separated emails |
| `reminder_minutes` | number | `30` | no | Popup reminder lead time |
| `calendar_id` | string | `primary` | no | Target calendar |
| `timezone` | string | `UTC` | no | Event timezone |

### `operation = list`

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `start_date` | string | today 00:00Z | ISO or `today` |
| `end_date` | string | now+7d | ISO, `today+Nd`, or ISO string |
| `max_results` | number | `10` | Clamped to `min(value, 250)` |
| `calendar_id` | string | `primary` | - |
| `single_events` | boolean | `true` | Expand recurrences |
| `order_by` | options | `startTime` | `startTime` or `updated` |

### `operation = update`

| Name | Type | Default | Required | Description |
|------|------|---------|----------|-------------|
| `event_id` | string | `""` | **yes** | Event to update |
| `title` / `start_time` / `end_time` / `description` / `location` | string | `""` | no | Patch fields |
| `calendar_id` | string | `primary` | no | - |

Also accepts the optional `update_title`, `update_start_time`,
`update_end_time`, `update_description`, `update_location` fields. The dispatch
method does NOT mutate the params dict — it reads `params.update_title or
params.title` (etc.) as fallback chains so either the `update_*` field or the
plain field works.

### `operation = delete`

| Name | Type | Default | Required | Description |
|------|------|---------|----------|-------------|
| `event_id` | string | `""` | **yes** | Event to delete |
| `calendar_id` | string | `primary` | no | - |
| `send_updates` | options | `all` | no | `all` or `none` - cancellation emails |

## Outputs (handles)

The node declares only `input-main` and `output-main`. Tool mode
(`usable_as_tool = True`, tool name `google_calendar`) returns the same
`output-main` payload — there is no separate `output-tool` handle.

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Operation-specific `CalendarOutput` payload |

- `create` / `update`: `{event_id, title, start, end, html_link, status, created?/updated?}`
- `list`: `{events: [{event_id, title, start, end, description, location, status, html_link, attendees}], count, time_range: {start, end}}`
- `delete`: `{deleted: true, event_id}`

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> CalendarNode.dispatch] --> G[build_google_service calendar v3]
  G --> B{operation?}
  B -- create --> V1{title & start & end present?}
  V1 -- no --> Eret[raise RuntimeError]
  V1 -- yes --> R1[events.insert sendUpdates]
  B -- list --> DP[Parse date shortcuts:<br/>today / today+Nd]
  DP --> R2[events.list timeMin/timeMax]
  B -- update --> V3{event_id?}
  V3 -- no --> Eret
  V3 -- yes --> G3[events.get -> merge update_* fallbacks -> events.update]
  B -- delete --> V4{event_id?}
  V4 -- no --> Eret
  V4 -- yes --> R4[events.delete sendUpdates]
  B -- unknown --> Eret
  R1 --> T[track_google_usage google_calendar]
  R2 --> T
  G3 --> T
  R4 --> T
  T --> OUT[Return CalendarOutput; BaseNode serializes envelope]
```

## Decision Logic

- **Operation branch**: one `@Operation("dispatch")` method branches on `params.operation` (`create` / `list` / `update` / `delete`); unknown raises `RuntimeError`.
- **Date shortcut parsing** (list): `start_date` of `today` or empty -> today 00:00 UTC; `end_date` starting with `today+` is `today + Nd`; otherwise the raw string is used and `Z` is appended if missing.
- **Timezone fallback** (update): when updating start/end, the existing event's `timeZone` is reused; if absent falls back to `UTC`.
- **Update fallback chains**: `params.update_title or params.title` (etc.) — no mutation of the params dict; either field works.
- **Description / location**: treated as `is not None` rather than truthy - empty string is a valid clear.
- **Attendees**: empty strings after `split(',')` are filtered; the whole `attendees` field is omitted if the list is empty.
- **`order_by`**: only sent when `single_events=True`.

## Side Effects

- **Database writes**: `api_usage_metrics` row per call via `track_google_usage` -> `save_api_usage_metric` with `service='google_calendar'`.
- **Broadcasts**: none from the operation; executor emits standard `node_status`.
- **External API calls**: Calendar API v3 - `events().insert/list/update/delete/get`. `sendUpdates` on create/update/delete drives email invitations/cancellations.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `GoogleCredential` -> OAuth tokens for provider `google`.
- **Services**: Google Calendar API, `PricingService`, `Database`.
- **Python packages**: `google-api-python-client`.
- **Environment variables**: none.

## Edge cases & known limits

- `max_results` clamped to 250 silently.
- `update` performs a read-modify-write cycle: two API calls per update. A concurrent modification between the `get` and `update` is overwritten without ETag checking.
- `delete` with an invalid event_id will surface the `HttpError 404` message into the envelope as a string.
- `description=""` is treated as a real clear; `description=None` or missing leaves the field untouched. Subtle but load-bearing for update callers.

## Related

- **Skills using this as a tool**: [`calendar-skill/SKILL.md`](../../../server/skills/productivity_agent/google-calendar-skill/SKILL.md)
- **Companion nodes**: [`googleGmail`](./googleGmail.md), [`googleDrive`](./googleDrive.md), [`googleSheets`](./googleSheets.md), [`googleTasks`](./googleTasks.md), [`googleContacts`](./googleContacts.md)
- **Architecture docs**: `CLAUDE.md` -> "Google Workspace Nodes".
