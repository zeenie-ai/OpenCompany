---
name: wifi-skill
description: Control Android WiFi - enable, disable, get status, scan for networks, and get current connection info.
allowed-tools: wifi_automation
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# WiFi Automation Tool

Control WiFi on Android devices.

## How It Works

This skill provides instructions for the **WiFi Automation** tool node. Connect the **WiFi Automation** node to Zeenie's `input-tools` handle to enable WiFi control.

## wifi_automation Tool

Control WiFi settings and get network information.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action to perform (see below) |
| parameters | object | No | Additional parameters for certain actions |

### Actions

| Action | Description | Parameters |
|--------|-------------|------------|
| `status` | Get WiFi status and connection info | None |
| `enable` | Turn WiFi on | None |
| `disable` | Turn WiFi off | None |
| `scan` | Scan for available networks | None |

### Examples

**Get WiFi status:**
```json
{
  "action": "status"
}
```

**Enable WiFi:**
```json
{
  "action": "enable"
}
```

**Disable WiFi:**
```json
{
  "action": "disable"
}
```

**Scan for networks:**
```json
{
  "action": "scan"
}
```

### Response Formats

**Status response:**
```json
{
  "success": true,
  "service": "wifi_automation",
  "action": "status",
  "data": {
    "enabled": true,
    "connected": true,
    "ssid": "MyHomeNetwork",
    "bssid": "aa:bb:cc:dd:ee:ff",
    "ip_address": "192.168.1.100",
    "link_speed": 72,
    "rssi": -45,
    "frequency": 2437
  }
}
```

**Scan response:**
```json
{
  "success": true,
  "service": "wifi_automation",
  "action": "scan",
  "data": {
    "networks": [
      {
        "ssid": "MyHomeNetwork",
        "bssid": "aa:bb:cc:dd:ee:ff",
        "rssi": -45,
        "frequency": 2437,
        "security": "WPA2"
      },
      {
        "ssid": "Neighbor_WiFi",
        "bssid": "11:22:33:44:55:66",
        "rssi": -70,
        "frequency": 5180,
        "security": "WPA3"
      }
    ]
  }
}
```

**Enable/Disable response:**
```json
{
  "success": true,
  "service": "wifi_automation",
  "action": "enable",
  "data": {
    "message": "WiFi enabled successfully"
  }
}
```

### Response Fields

| Field | Description |
|-------|-------------|
| enabled | WiFi radio is on |
| connected | Connected to a network |
| ssid | Network name |
| bssid | Access point MAC address |
| ip_address | Device IP on network |
| link_speed | Connection speed in Mbps |
| rssi | Signal strength (dBm, closer to 0 is stronger) |
| frequency | Channel frequency in MHz |

## Use Cases

| Use Case | Action | Description |
|----------|--------|-------------|
| Check connection | status | Verify WiFi is connected |
| Toggle WiFi | enable/disable | Control WiFi radio |
| Find networks | scan | List available networks |
| Signal strength | status | Check connection quality |

## Common Workflows

### Auto-connect workflow

1. Check WiFi status
2. If not enabled, enable it
3. Scan for networks
4. Report available options

### Battery saving

1. Check if on mobile data
2. If not using WiFi, disable it
3. Re-enable when needed

## Signal Strength Guide

| RSSI (dBm) | Quality |
|------------|---------|
| > -50 | Excellent |
| -50 to -60 | Good |
| -60 to -70 | Fair |
| -70 to -80 | Weak |
| < -80 | Poor |

## Setup Requirements

1. Connect the **WiFi Automation** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Location permission may be required for scanning
