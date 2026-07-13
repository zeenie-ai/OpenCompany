---
name: cron-scheduler-skill
description: Schedule recurring tasks using cron expressions. Run workflows on schedules (daily, weekly, hourly, etc.).
allowed-tools: cron_scheduler
metadata:
  author: opencompany
  version: "1.0"
  category: automation

---

# Cron Scheduler Tool

Schedule recurring tasks using cron expressions.

## How It Works

This skill provides instructions for the **Cron Scheduler** tool node. Connect the **Cron Scheduler** node to Zeenie's `input-tools` handle or use as a workflow trigger.

## cron_scheduler Tool

Create recurring schedules using cron expressions.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| expression | string | Yes | Cron expression (5 fields) |
| timezone | string | No | Timezone (default: UTC) |

### Cron Expression Format

```
* * * * *
│ │ │ │ │
│ │ │ │ └── Day of week (0-7, Sun=0 or 7)
│ │ │ └──── Month (1-12)
│ │ └────── Day of month (1-31)
│ └──────── Hour (0-23)
└────────── Minute (0-59)
```

### Special Characters

| Character | Meaning | Example |
|-----------|---------|---------|
| `*` | Any value | `* * * * *` (every minute) |
| `,` | List of values | `1,15 * * * *` (minute 1 and 15) |
| `-` | Range | `1-5 * * * *` (minutes 1-5) |
| `/` | Step values | `*/15 * * * *` (every 15 minutes) |

### Common Patterns

| Pattern | Description | Cron Expression |
|---------|-------------|-----------------|
| Every minute | Runs each minute | `* * * * *` |
| Every 5 minutes | Runs at :00, :05, :10... | `*/5 * * * *` |
| Every 15 minutes | Runs at :00, :15, :30, :45 | `*/15 * * * *` |
| Every hour | Runs at minute 0 | `0 * * * *` |
| Every day at 9 AM | Morning job | `0 9 * * *` |
| Every day at midnight | Nightly job | `0 0 * * *` |
| Weekdays at 9 AM | Mon-Fri morning | `0 9 * * 1-5` |
| Every Monday | Weekly on Monday | `0 0 * * 1` |
| First of month | Monthly job | `0 0 1 * *` |
| Every Sunday at 6 PM | Weekly Sunday evening | `0 18 * * 0` |
| Multiple times daily | 8am, noon, 6pm | `0 8,12,18 * * *` |

### Examples

**Every day at 9 AM:**
```json
{
  "expression": "0 9 * * *",
  "timezone": "America/New_York"
}
```

**Every 30 minutes:**
```json
{
  "expression": "*/30 * * * *"
}
```

**Weekdays at 6 PM:**
```json
{
  "expression": "0 18 * * 1-5",
  "timezone": "Europe/London"
}
```

**Every Monday at 10 AM:**
```json
{
  "expression": "0 10 * * 1",
  "timezone": "Asia/Tokyo"
}
```

**First day of each month:**
```json
{
  "expression": "0 0 1 * *"
}
```

### Response Format

**Schedule created:**
```json
{
  "success": true,
  "message": "Cron schedule created",
  "expression": "0 9 * * *",
  "timezone": "America/New_York",
  "next_run": "2025-01-31T09:00:00-05:00",
  "description": "At 09:00 AM, every day"
}
```

**Schedule triggered:**
```json
{
  "success": true,
  "triggered": true,
  "message": "Cron schedule triggered",
  "expression": "0 9 * * *",
  "triggered_at": "2025-01-30T09:00:00Z"
}
```

### Error Response

```json
{
  "error": "Invalid cron expression: too few fields"
}
```

## Timezone Reference

| Timezone | Description |
|----------|-------------|
| `UTC` | Universal Time (default) |
| `America/New_York` | Eastern US |
| `America/Los_Angeles` | Pacific US |
| `Europe/London` | UK |
| `Europe/Paris` | Central Europe |
| `Asia/Tokyo` | Japan |
| `Asia/Shanghai` | China |
| `Australia/Sydney` | Australia Eastern |

## Use Cases

| Use Case | Expression | Description |
|----------|------------|-------------|
| Daily report | `0 9 * * *` | Generate daily reports |
| Hourly sync | `0 * * * *` | Sync data hourly |
| Weekly backup | `0 2 * * 0` | Sunday at 2 AM |
| Business hours check | `*/30 9-17 * * 1-5` | Every 30 min, 9-5 Mon-Fri |
| Monthly cleanup | `0 0 1 * *` | First of month |

## Common Workflows

### Daily notification

1. Set cron for desired time (e.g., `0 9 * * *`)
2. Cron triggers → workflow runs
3. Send notification to user

### Scheduled data sync

1. Set cron for sync interval (e.g., `0 * * * *`)
2. Cron triggers → fetch data
3. Process and store data

### Weekly report

1. Set cron for weekly (e.g., `0 10 * * 1`)
2. Cron triggers → generate report
3. Send report via email/WhatsApp

## Workflow-Based Scheduling

In this system, scheduling works through:

1. **Trigger Node**: Use Cron Scheduler as workflow start
2. **Deployment**: Deploy workflow to activate schedule
3. **Cancellation**: Undeploy workflow to stop schedule

Each trigger creates independent workflow runs.

## Best Practices

1. **Use UTC for global**: Avoid timezone confusion
2. **Consider load**: Don't schedule many jobs at :00
3. **Stagger jobs**: Use :05, :10 instead of all at :00
4. **Test expressions**: Verify with online cron tools
5. **Document schedules**: Keep track of what runs when

## Limitations

- Requires workflow deployment to activate
- Cannot schedule past events
- Minimum granularity is 1 minute
- Complex conditions need multiple schedules

## Setup Requirements

1. Connect the **Cron Scheduler** node to Zeenie's `input-tools` handle
2. Or use as workflow trigger (first node)
3. Deploy workflow to activate the schedule
4. Undeploy to cancel the schedule
