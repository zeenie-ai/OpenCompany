---
name: write-todos-skill
description: Create and manage structured task lists for planning and tracking complex multi-step operations.
allowed-tools: write_todos
metadata:
  author: opencompany
  version: "2.0"
  category: automation

---

# Task Planning with write_todos

You have access to the `write_todos` tool. Use it to plan, track, and update a structured task list when working on complex objectives.

## When to Use

Use `write_todos` when the task requires **3 or more steps**. Do NOT use it for trivial requests that you can complete in 1-2 tool calls.

## How to Use

Follow this loop for every complex task:

### 1. Plan First

Before doing any work, call `write_todos` to create your plan. Break the objective into specific, actionable steps. Mark the first task `in_progress` immediately.

```json
{
  "todos": [
    {"content": "Identify the relevant source files", "status": "in_progress"},
    {"content": "Implement the core logic", "status": "pending"},
    {"content": "Add error handling", "status": "pending"},
    {"content": "Verify the changes work", "status": "pending"}
  ]
}
```

### 2. Work, Then Update

After completing a step, call `write_todos` again with the updated list. Mark the finished task `completed` and the next task `in_progress`. Do this after **every** step -- do not batch updates.

```json
{
  "todos": [
    {"content": "Identify the relevant source files", "status": "completed"},
    {"content": "Implement the core logic", "status": "in_progress"},
    {"content": "Add error handling", "status": "pending"},
    {"content": "Verify the changes work", "status": "pending"}
  ]
}
```

### 3. Revise as You Go

If you discover new work or a step becomes irrelevant, update the list. Add new tasks, remove unnecessary ones, or reword tasks to be more accurate.

```json
{
  "todos": [
    {"content": "Identify the relevant source files", "status": "completed"},
    {"content": "Implement the core logic", "status": "completed"},
    {"content": "Fix unexpected edge case in parser", "status": "in_progress"},
    {"content": "Add error handling", "status": "pending"},
    {"content": "Verify the changes work", "status": "pending"}
  ]
}
```

### 4. Complete All Tasks

Keep working until every task is `completed`. Always have at least one task `in_progress` unless all are done.

## Rules

- **Always plan before acting** on complex tasks.
- **Update after every step** -- the user sees your progress in real time.
- **One `write_todos` call per turn** -- never call it multiple times in parallel.
- **Mark `in_progress` before starting** a task, `completed` after finishing it.
- **Never mark a task `completed`** if there are unresolved errors or the work is partial.
- **Do not change completed tasks** -- only update pending and in_progress items.
- **Remove irrelevant tasks** rather than leaving them pending.
- **Be specific** -- "Fix the null check in validate_input()" is better than "Fix bug".
- **Each call sends the full list** -- include all tasks every time, not just changes.

## Schema

| Field | Type | Description |
|-------|------|-------------|
| todos | array | Full list of todo items |
| todos[].content | string | Specific, actionable task description |
| todos[].status | string | `pending`, `in_progress`, or `completed` |
