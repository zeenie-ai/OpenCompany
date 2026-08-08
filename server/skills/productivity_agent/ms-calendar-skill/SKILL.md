---
name: ms-calendar-skill
description: Create, list, update, and delete Outlook Calendar events via Microsoft Graph. Supports attendees, locations, and date-range queries.
allowed-tools: "ms_calendar"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Outlook Calendar Skill

Manage Outlook Calendar events - create, list, update, and delete - using the Microsoft Graph API (Outlook / Microsoft 365).

## Tool: ms_calendar

Consolidated Outlook Calendar tool with an `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `create` | Create a new event | title, start_time, end_time |
| `list` | List events in a date range | (none, defaults to the next 7 days) |
| `update` | Update an existing event | event_id |
| `delete` | Delete an event | event_id |

### create - Create a new event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"create"` |
| title | string | Yes | Event title/subject |
| start_time | string | Yes | Start time in ISO 8601 format |
| end_time | string | Yes | End time in ISO 8601 format |
| body | string | No | Event description/notes |
| location | string | No | Event location |
| attendees | string | No | Comma-separated email addresses |
| timezone | string | No | IANA/Windows time zone (default: UTC) |

**Example - Simple event:**
```json
{
  "operation": "create",
  "title": "Team Meeting",
  "start_time": "2026-08-10T14:00:00",
  "end_time": "2026-08-10T15:00:00",
  "body": "Weekly team sync"
}
```

**Example - Event with attendees and location:**
```json
{
  "operation": "create",
  "title": "Project Review",
  "start_time": "2026-08-10T10:00:00",
  "end_time": "2026-08-10T11:30:00",
  "location": "Conference Room A",
  "attendees": "alice@contoso.com, bob@contoso.com",
  "timezone": "Eastern Standard Time"
}
```

### list - List events in a date range

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list"` |
| start_date | string | No | Start of window. ISO 8601, or `today` / `today+Nd` (default: today) |
| end_date | string | No | End of window. ISO 8601, or `today+Nd` (default: 7 days ahead) |
| max_results | integer | No | Maximum results (default: 10, max: 250) |

**Example:**
```json
{
  "operation": "list",
  "start_date": "today",
  "end_date": "today+7d",
  "max_results": 20
}
```

### update - Update an existing event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"update"` |
| event_id | string | Yes | Event ID to update (from `create` / `list`) |
| update_title | string | No | New title |
| update_start_time | string | No | New start time (ISO 8601) |
| update_end_time | string | No | New end time (ISO 8601) |
| update_body | string | No | New description/notes |
| update_location | string | No | New location |
| timezone | string | No | Time zone applied to new start/end (default: UTC) |

At least one `update_*` field must be provided.

**Example:**
```json
{
  "operation": "update",
  "event_id": "AAMkAGI2...",
  "update_title": "Updated Team Meeting",
  "update_start_time": "2026-08-10T15:00:00",
  "update_end_time": "2026-08-10T16:00:00"
}
```

### delete - Delete an event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"delete"` |
| event_id | string | Yes | Event ID to delete |

**Example:**
```json
{
  "operation": "delete",
  "event_id": "AAMkAGI2..."
}
```

## Date/Time Formats

- **ISO 8601**: `2026-08-10T14:00:00` (interpreted in the given `timezone`)
- **Shortcuts (list only)**: `today`, `today+7d`
- Times returned by `list` are the raw Graph `dateTime` values.

## Common Workflows

1. **Schedule a meeting**: `create` with attendees; they receive invites.
2. **Check availability**: `list` for a date range.
3. **Reschedule**: `update` with new `update_start_time` / `update_end_time`.
4. **Cancel meeting**: `delete` the event by `event_id`.

## Setup Requirements

1. Connect the Outlook Calendar node to an AI Agent's `input-tools` handle.
2. Authenticate with Microsoft Graph in the Credentials Modal (Work/School account).
3. Ensure the Calendars.ReadWrite scope is granted.
