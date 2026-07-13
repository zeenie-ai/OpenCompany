---
name: bluetooth-skill
description: Control Android Bluetooth - enable, disable, get status, and list paired devices.
allowed-tools: bluetooth_automation
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Bluetooth Automation Tool

Control Bluetooth on Android devices.

## How It Works

This skill provides instructions for the **Bluetooth Automation** tool node. Connect the **Bluetooth Automation** node to Zeenie's `input-tools` handle to enable Bluetooth control.

## bluetooth_automation Tool

Control Bluetooth settings and get device information.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action to perform (see below) |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get Bluetooth status and paired devices |
| `enable` | Turn Bluetooth on |
| `disable` | Turn Bluetooth off |

### Examples

**Get Bluetooth status:**
```json
{
  "action": "status"
}
```

**Enable Bluetooth:**
```json
{
  "action": "enable"
}
```

**Disable Bluetooth:**
```json
{
  "action": "disable"
}
```

### Response Formats

**Status response:**
```json
{
  "success": true,
  "service": "bluetooth_automation",
  "action": "status",
  "data": {
    "enabled": true,
    "discovering": false,
    "name": "My Phone",
    "address": "AA:BB:CC:DD:EE:FF",
    "paired_devices": [
      {
        "name": "AirPods Pro",
        "address": "11:22:33:44:55:66",
        "type": "audio",
        "bonded": true
      },
      {
        "name": "Car Stereo",
        "address": "77:88:99:AA:BB:CC",
        "type": "audio",
        "bonded": true
      }
    ]
  }
}
```

**Enable/Disable response:**
```json
{
  "success": true,
  "service": "bluetooth_automation",
  "action": "enable",
  "data": {
    "message": "Bluetooth enabled successfully"
  }
}
```

### Response Fields

| Field | Description |
|-------|-------------|
| enabled | Bluetooth radio is on |
| discovering | Scanning for new devices |
| name | Device Bluetooth name |
| address | Device Bluetooth MAC address |
| paired_devices | List of bonded devices |

### Paired Device Fields

| Field | Description |
|-------|-------------|
| name | Device name |
| address | Device MAC address |
| type | Device type (audio, computer, phone, etc.) |
| bonded | Currently paired |

## Use Cases

| Use Case | Action | Description |
|----------|--------|-------------|
| Check Bluetooth | status | See if BT is on and paired devices |
| Toggle Bluetooth | enable/disable | Control BT radio |
| List devices | status | Get paired device list |
| Battery saving | disable | Turn off when not needed |

## Common Workflows

### Connect to car

1. Check Bluetooth status
2. If not enabled, enable it
3. Car should auto-connect if paired

### Battery saving mode

1. Check Bluetooth status
2. If no audio playing and no connected devices
3. Disable Bluetooth to save battery

## Setup Requirements

1. Connect the **Bluetooth Automation** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Bluetooth permission required on device
