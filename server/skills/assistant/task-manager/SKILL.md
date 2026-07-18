---
name: task-manager
description: Manage delegated tasks. List active tasks, check task status, get results from completed delegations, and mark tasks as done.
allowed-tools: task_manager
metadata:
  author: opencompany
  version: "1.0"
  category: automation

---

# Task Manager

Manage and track delegated agent tasks.

## How It Works

This skill teaches the AI Assistant how to use the **Task Manager** tool. Connect the **Task Manager** node to the assistant's `input-tools` handle to enable delegated-task tracking.

## task_manager Tool

List, check, and manage delegated tasks.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | `"list_tasks"`, `"get_task"`, or `"mark_done"` |
| task_id | string | For get_task, mark_done | Specific task ID to query |
| status_filter | string | No | Filter by status: `"running"`, `"completed"`, `"error"` |

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `list_tasks` | List all tracked tasks | status_filter (optional) |
| `get_task` | Get details for specific task | task_id |
| `mark_done` | Remove task from tracking | task_id |

### Task States

| Status | Description |
|--------|-------------|
| `running` | Task is currently executing |
| `completed` | Task finished successfully |
| `error` | Task failed with error |
| `cancelled` | Task was cancelled |

### Examples

**List all tasks:**
```json
{
  "operation": "list_tasks"
}
```

**List only running tasks:**
```json
{
  "operation": "list_tasks",
  "status_filter": "running"
}
```

**List completed tasks:**
```json
{
  "operation": "list_tasks",
  "status_filter": "completed"
}
```

**Get specific task details:**
```json
{
  "operation": "get_task",
  "task_id": "delegated_agent_abc12345"
}
```

**Mark task as done:**
```json
{
  "operation": "mark_done",
  "task_id": "delegated_agent_abc12345"
}
```

### Response Formats

**list_tasks response:**
```json
{
  "success": true,
  "operation": "list_tasks",
  "tasks": [
    {
      "task_id": "delegated_coding_agent_abc12345",
      "status": "completed",
      "agent_name": "Coding Agent",
      "result_summary": "Generated Python function for data processing...",
      "active": false
    },
    {
      "task_id": "delegated_web_agent_def67890",
      "status": "running",
      "active": true
    }
  ],
  "count": 2,
  "running": 1,
  "completed": 1,
  "errors": 0
}
```

**get_task response (completed):**
```json
{
  "success": true,
  "operation": "get_task",
  "task_id": "delegated_coding_agent_abc12345",
  "status": "completed",
  "agent_name": "Coding Agent",
  "result": "Here is the Python function you requested:\n\ndef process_data(items):\n    return [x * 2 for x in items]"
}
```

**get_task response (running):**
```json
{
  "success": true,
  "operation": "get_task",
  "task_id": "delegated_web_agent_def67890",
  "status": "running",
  "agent_name": "Web Agent"
}
```

**get_task response (error):**
```json
{
  "success": true,
  "operation": "get_task",
  "task_id": "delegated_agent_xyz99999",
  "status": "error",
  "agent_name": "Social Agent",
  "error": "Failed to connect to WhatsApp service"
}
```

**mark_done response:**
```json
{
  "success": true,
  "operation": "mark_done",
  "task_id": "delegated_coding_agent_abc12345",
  "removed": true,
  "message": "Task delegated_coding_agent_abc12345 marked as done and removed from tracking"
}
```

### Error Response

```json
{
  "success": false,
  "error": "task_id is required for get_task operation"
}
```

```json
{
  "success": false,
  "error": "Task delegated_agent_notfound not found",
  "task_id": "delegated_agent_notfound"
}
```

## Use Cases

| Use Case | Operation | Description |
|----------|-----------|-------------|
| Monitor progress | list_tasks | See all active delegations |
| Check result | get_task | Get completed task output |
| Verify completion | get_task | Confirm task finished |
| Clean up | mark_done | Remove processed tasks |
| Error handling | list_tasks + filter | Find failed tasks |

## Common Workflows

### Check on delegated work

1. Delegate task to sub-agent
2. Wait or continue with other work
3. Use `list_tasks` to see status
4. Use `get_task` to retrieve result

### Process all completed tasks

1. Use `list_tasks` with `status_filter: "completed"`
2. For each task, use `get_task` to get full result
3. Process the results
4. Use `mark_done` to clean up

### Handle errors

1. Use `list_tasks` with `status_filter: "error"`
2. Review failed tasks
3. Decide to retry or mark_done
4. Optionally re-delegate failed work

## Integration with Agent Delegation

When a parent agent delegates work:

1. `delegate_to_<agent>` tool returns `task_id`
2. Child agent runs in background
3. Parent can check status with `task_manager`
4. Results persist until `mark_done`

### Task ID Format

Task IDs follow the pattern:
```
delegated_<node_id>_<random_hex>
```

Example: `delegated_coding_agent_1_abc12345`

## Best Practices

1. **Track task IDs**: Store returned task_ids for later reference
2. **Poll appropriately**: Don't check too frequently
3. **Handle all states**: Account for running, completed, and error
4. **Clean up**: Use mark_done after processing results
5. **Check errors**: Review failed tasks before marking done

## Limitations

- Tasks not persistent across server restarts (in-memory)
- Results may be truncated if very large (4000 char limit in responses)
- Cannot cancel running tasks (only track status)

## Setup Requirements

1. Connect the **Task Manager** node to Zeenie's `input-tools` handle
2. Works with any agent that uses delegation
3. Task IDs are returned when delegating to sub-agents
