---
name: timer-skill
description: Wait for a short delay (seconds, or a few minutes) before the next step. The call blocks while it waits, and a wait longer than about 9 minutes times out.
allowed-tools: timer
metadata:
  author: opencompany
  version: "1.0"
  category: automation

---

# Timer Tool

Wait for a short, fixed delay before the next step.

## How It Works

This skill provides instructions for the **Timer** tool node. Connect the **Timer** node to Zeenie's `input-tools` handle to let the agent pause.

The call **blocks**: the agent waits until the timer finishes and does nothing else meanwhile. It is not a reminder or a scheduler, and nothing fires later.

## timer Tool

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| duration | number | Yes | How long to wait (1 or more) |
| unit | string | No | `"seconds"` (the default), `"minutes"` or `"hours"` |

### Limits

- A wait as a tool call can last **at most about 9 minutes**. A longer one times out, and the call fails.
- If the worker restarts during a wait, the wait starts again from the beginning.
- There is no `days` unit. For a delay of hours or days, or anything that repeats, use a schedule (the Cron Scheduler node) instead.

### Examples

**30 second delay:**
```json
{
  "duration": 30,
  "unit": "seconds"
}
```

**5 minute pause between checks:**
```json
{
  "duration": 5,
  "unit": "minutes"
}
```

### Response Format

```json
{
  "timestamp": "2026-10-27T12:05:00",
  "elapsed_ms": 300012,
  "duration": 5,
  "unit": "minutes",
  "message": "Timer completed after 5 minutes"
}
```

## Use Cases

| Use Case | Duration | Unit | Description |
|----------|----------|------|-------------|
| Quick test | 10-30 | seconds | Testing workflow execution |
| Rate limit | 60 | seconds | Wait between API calls |
| Short pause | 1-5 | minutes | Give something time before checking again |

## As a workflow node

On the canvas, the Timer pauses its branch: it waits for the duration, then the nodes after it run. As a graph node, one wait can last up to 24 hours, and a worker restart starts it again from the beginning.

## Best Practices

1. **Keep waits short**: as a tool call, stay well under 9 minutes
2. **Use appropriate units**: don't use 300 seconds when 5 minutes is clearer
3. **Schedule, don't wait**: for anything hours or days away, use a schedule instead

## Setup Requirements

1. Connect the **Timer** node to Zeenie's `input-tools` handle
2. For a pause inside a workflow, put the Timer between two nodes
