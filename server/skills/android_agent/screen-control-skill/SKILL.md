---
name: screen-control-skill
description: Control Android screen - brightness, wake screen, auto-brightness toggle, and screen timeout settings.
allowed-tools: screen_control_automation
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Screen Control Tool

Control screen settings on Android device.

## How It Works

This skill provides instructions for the **Screen Control Automation** tool node. Connect the **Screen Control Automation** node to Zeenie's `input-tools` handle to enable screen control.

## screen_control Tool

Control screen brightness and display settings.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action to perform (see below) |
| parameters | object | No | Additional parameters for set actions |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get current screen settings |
| `set_brightness` | Set screen brightness level |
| `wake` | Wake the screen |
| `auto_brightness_on` | Enable auto-brightness |
| `auto_brightness_off` | Disable auto-brightness |

### Examples

**Get screen status:**
```json
{
  "action": "status"
}
```

**Set brightness to 50%:**
```json
{
  "action": "set_brightness",
  "parameters": {
    "level": 50
  }
}
```

**Wake the screen:**
```json
{
  "action": "wake"
}
```

**Enable auto-brightness:**
```json
{
  "action": "auto_brightness_on"
}
```

**Disable auto-brightness:**
```json
{
  "action": "auto_brightness_off"
}
```

### Response Formats

**Status response:**
```json
{
  "success": true,
  "service": "screen_control",
  "action": "status",
  "data": {
    "brightness": 75,
    "auto_brightness": true,
    "screen_on": true,
    "screen_timeout": 30000
  }
}
```

**Set brightness response:**
```json
{
  "success": true,
  "service": "screen_control",
  "action": "set_brightness",
  "data": {
    "previous_brightness": 100,
    "new_brightness": 50
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| brightness | int | Current brightness (0-100) |
| auto_brightness | boolean | Auto-brightness enabled |
| screen_on | boolean | Screen is currently on |
| screen_timeout | int | Timeout in milliseconds |

## Use Cases

| Use Case | Action | Description |
|----------|--------|-------------|
| Check display | status | Get current settings |
| Lower brightness | set_brightness | Reduce for battery/eyes |
| Wake device | wake | Turn on screen |
| Enable adaptive | auto_brightness_on | Let system adjust |
| Fixed brightness | auto_brightness_off | Manual control |

## Common Workflows

### Battery saving

1. Disable auto-brightness
2. Set brightness to 30%
3. Reduce screen timeout

### Night mode

1. Disable auto-brightness
2. Set brightness to 10-20%
3. Enable blue light filter (if available)

### Presentation mode

1. Disable auto-brightness
2. Set brightness to 100%
3. Increase screen timeout

## Brightness Guidelines

| Level | Use Case |
|-------|----------|
| 0-20% | Night/dark room |
| 20-50% | Indoor use |
| 50-80% | Normal use |
| 80-100% | Bright conditions/outdoor |

## Setup Requirements

1. Connect the **Screen Control Automation** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. System settings permission may be required
