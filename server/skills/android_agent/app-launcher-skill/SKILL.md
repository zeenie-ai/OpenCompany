---
name: app-launcher-skill
description: Launch Android applications by package name. Open any installed app programmatically.
allowed-tools: app_launcher
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# App Launcher Tool

Launch applications on Android device.

## How It Works

This skill provides instructions for the **App Launcher** tool node. Connect the **App Launcher** node to Zeenie's `input-tools` handle to enable app launching.

## app_launcher Tool

Launch an installed application by package name.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"launch"` |
| parameters | object | Yes | Contains `package_name` |

### Parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| package_name | string | Yes | Android package name of the app |

### Example

**Launch an app:**
```json
{
  "action": "launch",
  "parameters": {
    "package_name": "com.whatsapp"
  }
}
```

### Common Package Names

| App | Package Name |
|-----|--------------|
| WhatsApp | `com.whatsapp` |
| Chrome | `com.android.chrome` |
| Gmail | `com.google.android.gm` |
| YouTube | `com.google.android.youtube` |
| Maps | `com.google.android.apps.maps` |
| Camera | `com.android.camera2` |
| Phone | `com.android.dialer` |
| Messages | `com.google.android.apps.messaging` |
| Settings | `com.android.settings` |
| Play Store | `com.android.vending` |
| Spotify | `com.spotify.music` |
| Netflix | `com.netflix.mediaclient` |
| Twitter/X | `com.twitter.android` |
| Instagram | `com.instagram.android` |
| Telegram | `org.telegram.messenger` |

### Response Format

**Success:**
```json
{
  "success": true,
  "service": "app_launcher",
  "action": "launch",
  "data": {
    "message": "App launched successfully",
    "package_name": "com.whatsapp"
  }
}
```

**Error:**
```json
{
  "error": "App not installed: com.example.notinstalled",
  "service": "app_launcher",
  "action": "launch"
}
```

## Use Cases

| Use Case | Description |
|----------|-------------|
| Quick launch | Open apps on command |
| Automation | Start apps as part of workflow |
| Shortcuts | Voice-controlled app opening |
| Workflows | Chain app launches with other actions |

## Common Workflows

### Open app and send message

1. Launch WhatsApp
2. User manually selects contact
3. (Alternative: use whatsapp_send for automated messaging)

### Daily routine

1. Launch news app in morning
2. Launch email app
3. Launch calendar

## Finding Package Names

To find an app's package name:

1. Use the **App List** tool to list installed apps
2. Look for the app name in the list
3. Use the returned package_name

### Example workflow:

```
1. app_list.status -> get list of apps
2. Find desired app in results
3. app_launcher.launch with package_name
```

## Setup Requirements

1. Connect the **App Launcher** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. App must be installed on the device
