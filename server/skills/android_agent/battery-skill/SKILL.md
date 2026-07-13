---
name: battery-skill
description: Monitor Android device battery status, level, charging state, temperature, and health.
allowed-tools: battery_monitor
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Battery Monitor Tool

Monitor Android device battery status and information.

## How It Works

This skill provides instructions for the **Battery Monitor** tool node. Connect the **Battery Monitor** node to Zeenie's `input-tools` handle to enable battery monitoring.

## battery Tool

Get battery status and information from the Android device.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"status"` - Get current battery information |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get current battery level, charging state, health, temperature |

### Example

**Get battery status:**
```json
{
  "action": "status"
}
```

### Response Format

```json
{
  "success": true,
  "service": "battery",
  "action": "status",
  "data": {
    "level": 85,
    "status": "charging",
    "health": "good",
    "temperature": 28.5,
    "voltage": 4200,
    "plugged": "ac",
    "technology": "Li-ion"
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| level | int | Battery percentage (0-100) |
| status | string | `"charging"`, `"discharging"`, `"full"`, `"not_charging"` |
| health | string | `"good"`, `"overheat"`, `"dead"`, `"cold"` |
| temperature | float | Temperature in Celsius |
| voltage | int | Voltage in millivolts |
| plugged | string | `"ac"`, `"usb"`, `"wireless"`, `"none"` |
| technology | string | Battery technology (e.g., "Li-ion") |

### Error Response

```json
{
  "error": "Failed to get battery status",
  "service": "battery",
  "action": "status"
}
```

## Use Cases

| Use Case | Description |
|----------|-------------|
| Low battery alert | Trigger actions when battery is low |
| Charging monitor | Detect when device starts/stops charging |
| Health check | Monitor battery health over time |
| Temperature warning | Alert on overheating |

## Common Workflows

### Low battery notification

1. Check battery status
2. If level < 20%, send notification
3. Optionally enable power saving

### Charging complete alert

1. Periodically check battery status
2. When status = "full", send notification
3. Optionally suggest unplugging

## Setup Requirements

1. Connect the **Battery Monitor** node to Zeenie's `input-tools` handle
2. Android device must be paired (green status indicator)
3. Battery monitoring permission required on device
