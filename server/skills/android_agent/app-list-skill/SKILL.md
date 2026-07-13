---
name: app-list-skill
description: Get list of installed Android applications with package names, versions, and metadata.
allowed-tools: app_list
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# App List Tool

Get list of installed applications on Android device.

## How It Works

This skill provides instructions for the **App List** tool node. Connect the **App List** node to Zeenie's `input-tools` handle to enable app listing.

## app_list Tool

Get list of installed applications.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"status"` - Get installed apps |

### Example

**Get installed apps:**
```json
{
  "action": "status"
}
```

### Response Format

```json
{
  "success": true,
  "service": "app_list",
  "action": "status",
  "data": {
    "apps": [
      {
        "name": "WhatsApp",
        "package_name": "com.whatsapp",
        "version": "2.24.1.5",
        "version_code": 2241005,
        "system_app": false,
        "enabled": true
      },
      {
        "name": "Chrome",
        "package_name": "com.android.chrome",
        "version": "121.0.6167.101",
        "version_code": 616710100,
        "system_app": false,
        "enabled": true
      },
      {
        "name": "Settings",
        "package_name": "com.android.settings",
        "version": "14",
        "version_code": 34,
        "system_app": true,
        "enabled": true
      }
    ],
    "total_apps": 85,
    "user_apps": 42,
    "system_apps": 43
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| name | string | Display name of the app |
| package_name | string | Android package identifier |
| version | string | Version string |
| version_code | int | Version code number |
| system_app | boolean | True if pre-installed system app |
| enabled | boolean | True if app is enabled |

### Summary Fields

| Field | Description |
|-------|-------------|
| total_apps | Total number of apps |
| user_apps | User-installed apps count |
| system_apps | Pre-installed system apps count |

## Use Cases

| Use Case | Description |
|----------|-------------|
| Find apps | Discover what's installed |
| Get package names | Find package name for app_launcher |
| Version check | Verify app versions |
| Inventory | Audit installed applications |

## Common Workflows

### Find and launch app

1. Get app list with `app_list.status`
2. Search for desired app by name
3. Use package_name with `app_launcher.launch`

### Check if app is installed

1. Get app list
2. Search for specific package_name
3. Report if found or not

### App version audit

1. Get app list
2. Check versions against known values
3. Report outdated apps

## Filtering Results

The tool returns all apps. To find specific apps:

1. Get full list
2. Filter by name or package_name
3. Check system_app to separate user/system apps

### Example: Find messaging apps

```
1. Get app list
2. Filter where name contains "message" or "chat"
3. Return matching apps
```

## Setup Requirements

1. Connect the **App List** node to Zeenie's `input-tools` handle
2. Android device must be paired
