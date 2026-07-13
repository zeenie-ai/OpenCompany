---
name: google-tasks-skill
description: Create, list, and complete Google Tasks. Supports task lists, due dates, and notes.
allowed-tools: "google_tasks"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Google Tasks Skill

Manage Google Tasks - create, list, and complete tasks.

## Tool: google_tasks

Consolidated Google Tasks tool with `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `create` | Create a new task | title |
| `list` | List tasks from a list | (none, defaults to @default) |
| `complete` | Mark task as completed | task_id |
| `update` | Update an existing task | task_id |
| `delete` | Delete a task | task_id |

### create - Create a new task

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"create"` |
| title | string | Yes | Task title |
| notes | string | No | Task notes/description |
| due_date | string | No | Due date (ISO 8601 or YYYY-MM-DD) |
| tasklist_id | string | No | Task list ID (default: @default) |

**Example - Simple task:**
```json
{
  "operation": "create",
  "title": "Review quarterly report"
}
```

**Example - Task with details:**
```json
{
  "operation": "create",
  "title": "Submit expense report",
  "notes": "Include receipts for conference travel",
  "due_date": "2024-02-15"
}
```

### list - List tasks

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list"` |
| tasklist_id | string | No | Task list ID (default: @default) |
| show_completed | boolean | No | Include completed tasks (default: false) |
| show_hidden | boolean | No | Include hidden tasks (default: false) |
| max_results | integer | No | Maximum results (default: 100) |

**Example - List pending tasks:**
```json
{
  "operation": "list",
  "show_completed": false,
  "max_results": 50
}
```

### complete - Mark task as completed

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"complete"` |
| task_id | string | Yes | Task ID to complete |
| tasklist_id | string | No | Task list ID (default: @default) |

**Example:**
```json
{
  "operation": "complete",
  "task_id": "abc123xyz"
}
```

### update - Update a task

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"update"` |
| task_id | string | Yes | Task ID to update |
| tasklist_id | string | No | Task list ID (default: @default) |
| update_title | string | No | New title |
| update_notes | string | No | New notes |
| update_due_date | string | No | New due date |
| update_status | string | No | New status: `"needsAction"` or `"completed"` |

**Example:**
```json
{
  "operation": "update",
  "task_id": "abc123xyz",
  "update_title": "Updated task title",
  "update_due_date": "2024-03-01"
}
```

### delete - Delete a task

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"delete"` |
| task_id | string | Yes | Task ID to delete |
| tasklist_id | string | No | Task list ID (default: @default) |

**Example:**
```json
{
  "operation": "delete",
  "task_id": "abc123xyz"
}
```

## Task Statuses

| Status | Description |
|--------|-------------|
| `needsAction` | Task is pending |
| `completed` | Task is done |

## Date Formats

- **ISO 8601**: `2024-02-15T14:00:00Z`
- **Date only**: `2024-02-15` (interpreted as midnight UTC)

## Working with Task Lists

The default task list is `@default`. To work with custom lists:

1. Use Google Tasks app to create lists
2. Get list ID from API (not exposed in this skill yet)
3. Pass `tasklist_id` parameter

## Common Workflows

1. **Daily review**: List pending tasks, prioritize
2. **Add reminders**: Create tasks with due dates
3. **Track completion**: Mark tasks done as you finish
4. **Weekly planning**: Create tasks for the week ahead

## Tips

- Tasks without due dates appear at the top of the list
- Completed tasks are hidden by default
- Notes can contain multiline text
- Due dates are in UTC timezone

## Setup Requirements

1. Connect Tasks node to AI Agent's `input-tools` handle
2. Authenticate with Google Workspace in Credentials Modal
3. Ensure Tasks API scopes are authorized
