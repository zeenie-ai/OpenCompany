---
name: camera-skill
description: Control Android camera - get camera info, take photos, and access camera capabilities.
allowed-tools: camera_control
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Camera Control Tool

Control camera on Android device.

## How It Works

This skill provides instructions for the **Camera Control** tool node. Connect the **Camera Control** node to Zeenie's `input-tools` handle to enable camera control.

## camera_control Tool

Access camera and capture photos.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action to perform (see below) |
| parameters | object | No | Additional parameters |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get camera info and capabilities |
| `capture` | Take a photo |

### Examples

**Get camera info:**
```json
{
  "action": "status"
}
```

**Take a photo:**
```json
{
  "action": "capture"
}
```

**Capture with front camera:**
```json
{
  "action": "capture",
  "parameters": {
    "camera": "front"
  }
}
```

### Response Formats

**Status response:**
```json
{
  "success": true,
  "service": "camera_control",
  "action": "status",
  "data": {
    "cameras": [
      {
        "id": "0",
        "facing": "back",
        "megapixels": 48,
        "has_flash": true,
        "supports_video": true
      },
      {
        "id": "1",
        "facing": "front",
        "megapixels": 12,
        "has_flash": false,
        "supports_video": true
      }
    ],
    "flash_available": true
  }
}
```

**Capture response:**
```json
{
  "success": true,
  "service": "camera_control",
  "action": "capture",
  "data": {
    "photo_path": "/storage/emulated/0/DCIM/Camera/IMG_20250130_120000.jpg",
    "camera_used": "back",
    "resolution": "4000x3000",
    "timestamp": "2025-01-30T12:00:00Z"
  }
}
```

### Response Fields

| Field | Description |
|-------|-------------|
| cameras | List of available cameras |
| facing | `"back"` or `"front"` |
| megapixels | Camera resolution |
| has_flash | Flash available |
| photo_path | Path to captured photo |

## Use Cases

| Use Case | Action | Description |
|----------|--------|-------------|
| Check cameras | status | Get camera capabilities |
| Take photo | capture | Capture image |
| Selfie | capture (front) | Use front camera |
| Document | capture | Capture document/scene |

## Common Workflows

### Quick photo capture

1. Optionally check camera status
2. Capture photo
3. Photo saved to device gallery

### Automated documentation

1. Trigger (time/event based)
2. Capture photo
3. Process/send photo

## Setup Requirements

1. Connect the **Camera Control** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Camera permission must be granted
4. Storage permission for saving photos
