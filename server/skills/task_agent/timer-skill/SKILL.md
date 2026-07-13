---
name: timer-skill
description: Set one-time delayed timers. Execute actions after a specified duration (seconds, minutes, hours, days).
allowed-tools: timer
metadata:
  author: opencompany
  version: "1.0"
  category: automation

---

# Timer Tool

Set one-time delayed execution timers.

## How It Works

This skill provides instructions for the **Timer** tool node. Connect the **Timer** node to Zeenie's `input-tools` handle to enable timed delays.

## timer Tool

Create a one-time timer that triggers after a specified duration.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| duration | number | Yes | Time value for the delay |
| unit | string | Yes | Time unit: `"seconds"`, `"minutes"`, `"hours"`, `"days"` |

### Time Units

| Unit | Range | Use Case |
|------|-------|----------|
| `seconds` | 1-3600 | Short delays, testing |
| `minutes` | 1-1440 | Task reminders, short waits |
| `hours` | 1-168 | Scheduled checks, delayed notifications |
| `days` | 1-30 | Long-term reminders, follow-ups |

### Examples

**30 second delay:**
```json
{
  "duration": 30,
  "unit": "seconds"
}
```

**5 minute reminder:**
```json
{
  "duration": 5,
  "unit": "minutes"
}
```

**1 hour delay:**
```json
{
  "duration": 1,
  "unit": "hours"
}
```

**2 day follow-up:**
```json
{
  "duration": 2,
  "unit": "days"
}
```

### Response Format

**Timer set:**
```json
{
  "success": true,
  "message": "Timer set for 5 minutes",
  "duration": 5,
  "unit": "minutes",
  "duration_seconds": 300,
  "trigger_at": "2025-01-30T12:05:00Z"
}
```

**Timer triggered:**
```json
{
  "success": true,
  "triggered": true,
  "message": "Timer completed after 5 minutes",
  "duration": 5,
  "unit": "minutes"
}
```

### Error Response

```json
{
  "error": "Duration must be a positive number"
}
```

## Use Cases

| Use Case | Duration | Unit | Description |
|----------|----------|------|-------------|
| Quick test | 10-30 | seconds | Testing workflow execution |
| Reminder | 5-30 | minutes | Short-term reminders |
| Rate limit | 60 | seconds | Wait between API calls |
| Daily check | 24 | hours | Daily automation |
| Follow-up | 2-7 | days | Long-term follow-ups |

## Common Workflows

### Delayed notification

1. Receive user request for reminder
2. Set timer with requested duration
3. Timer triggers → send notification

### Rate-limited API calls

1. Make API call
2. Set 60-second timer
3. Timer triggers → make next call

### Scheduled workflow

1. Set timer for desired delay
2. Timer triggers → execute workflow nodes

## Integration with Workflow

When used as a trigger node in a workflow:
1. Deploy the workflow
2. Timer countdown begins
3. After duration, downstream nodes execute

When used as AI tool:
1. Agent decides to set timer
2. Timer is scheduled
3. Agent can proceed with other work
4. Notification when timer triggers

## Best Practices

1. **Use appropriate units**: Don't use 3600 seconds when 1 hour is clearer
2. **Consider time zones**: Timers use server time
3. **Account for drift**: Long timers may have slight variance
4. **Test short first**: Start with seconds before days
5. **Chain timers carefully**: Avoid infinite loops

## Limitations

- Timers are not persistent across server restarts
- Maximum practical delay depends on server uptime
- Timers are one-time (use cron for recurring)

## Setup Requirements

1. Connect the **Timer** node to Zeenie's `input-tools` handle
2. For workflow triggers, connect Timer as the first node
3. Deploy workflow to activate timer
