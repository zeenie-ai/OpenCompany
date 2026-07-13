---
name: google-calendar-skill
description: Create, list, update, and delete Google Calendar events. Supports attendees, reminders, and recurring events.
allowed-tools: "google_calendar"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Google Calendar Skill

Manage Google Calendar events - create, list, update, and delete.

## Tool: google_calendar

Consolidated Google Calendar tool with `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `create` | Create a new event | title, start_time, end_time |
| `list` | List events in date range | (none, defaults to this week) |
| `update` | Update an existing event | event_id |
| `delete` | Delete an event | event_id |

### create - Create a new event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"create"` |
| title | string | Yes | Event title/summary |
| start_time | string | Yes | Start time in ISO 8601 format |
| end_time | string | Yes | End time in ISO 8601 format |
| description | string | No | Event description |
| location | string | No | Event location |
| attendees | string | No | Comma-separated email addresses |
| reminder_minutes | integer | No | Minutes before event for reminder |
| timezone | string | No | Timezone (default: user's timezone) |

**Example - Simple event:**
```json
{
  "operation": "create",
  "title": "Team Meeting",
  "start_time": "2024-02-01T14:00:00",
  "end_time": "2024-02-01T15:00:00",
  "description": "Weekly team sync"
}
```

**Example - Event with attendees:**
```json
{
  "operation": "create",
  "title": "Project Review",
  "start_time": "2024-02-01T10:00:00",
  "end_time": "2024-02-01T11:30:00",
  "location": "Conference Room A",
  "attendees": "alice@example.com,bob@example.com",
  "reminder_minutes": 30
}
```

### list - List events in date range

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list"` |
| start_date | string | No | Start date (ISO 8601, default: today) |
| end_date | string | No | End date (ISO 8601, default: 7 days ahead) |
| max_results | integer | No | Maximum results (default: 10, max: 100) |
| calendar_id | string | No | Calendar ID (default: primary) |

**Example:**
```json
{
  "operation": "list",
  "start_date": "2024-02-01",
  "end_date": "2024-02-07",
  "max_results": 20
}
```

### update - Update an existing event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"update"` |
| event_id | string | Yes | Event ID to update |
| update_title | string | No | New title |
| update_start_time | string | No | New start time |
| update_end_time | string | No | New end time |
| update_description | string | No | New description |
| update_location | string | No | New location |
| update_attendees | string | No | New attendees (replaces existing) |

**Example:**
```json
{
  "operation": "update",
  "event_id": "abc123xyz",
  "update_title": "Updated Team Meeting",
  "update_start_time": "2024-02-01T15:00:00",
  "update_end_time": "2024-02-01T16:00:00"
}
```

### delete - Delete an event

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"delete"` |
| event_id | string | Yes | Event ID to delete |
| calendar_id | string | No | Calendar ID (default: primary) |

**Example:**
```json
{
  "operation": "delete",
  "event_id": "abc123xyz"
}
```

## Date/Time Formats

- **ISO 8601**: `2024-02-01T14:00:00` (local time)
- **With timezone**: `2024-02-01T14:00:00-05:00` (EST)
- **UTC**: `2024-02-01T19:00:00Z`
- **Date only**: `2024-02-01` (all-day event)

## Common Workflows

1. **Schedule a meeting**: Create event with attendees, they receive invites
2. **Check availability**: List events for a date range
3. **Reschedule**: Update event with new times
4. **Cancel meeting**: Delete the event

## Setup Requirements

1. Connect Calendar node to AI Agent's `input-tools` handle
2. Authenticate with Google Workspace in Credentials Modal
3. Ensure Calendar API scopes are authorized
