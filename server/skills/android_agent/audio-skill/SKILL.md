---
name: audio-skill
description: Control Android audio - get/set volume, mute/unmute for media, ringtone, notification, and call volumes.
allowed-tools: audio_automation
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Audio Automation Tool

Control audio and volume on Android device.

## How It Works

This skill provides instructions for the **Audio Automation** tool node. Connect the **Audio Automation** node to Zeenie's `input-tools` handle to enable audio control.

## audio_automation Tool

Control audio settings and volume levels.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | Action to perform (see below) |
| parameters | object | No | Additional parameters for set actions |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get current volume levels and audio state |
| `set_volume` | Set volume for a stream |
| `mute` | Mute audio |
| `unmute` | Unmute audio |

### Volume Streams

| Stream | Description |
|--------|-------------|
| `media` | Music, videos, games |
| `ring` | Ringtone volume |
| `notification` | Notification sounds |
| `alarm` | Alarm volume |
| `voice_call` | Call volume |
| `system` | System sounds (clicks, etc.) |

### Examples

**Get audio status:**
```json
{
  "action": "status"
}
```

**Set media volume:**
```json
{
  "action": "set_volume",
  "parameters": {
    "stream": "media",
    "level": 50
  }
}
```

**Mute all audio:**
```json
{
  "action": "mute"
}
```

**Unmute:**
```json
{
  "action": "unmute"
}
```

### Response Formats

**Status response:**
```json
{
  "success": true,
  "service": "audio_automation",
  "action": "status",
  "data": {
    "media_volume": 75,
    "ring_volume": 100,
    "notification_volume": 80,
    "alarm_volume": 100,
    "voice_call_volume": 50,
    "system_volume": 50,
    "muted": false,
    "ringer_mode": "normal"
  }
}
```

**Set volume response:**
```json
{
  "success": true,
  "service": "audio_automation",
  "action": "set_volume",
  "data": {
    "stream": "media",
    "previous_level": 100,
    "new_level": 50
  }
}
```

### Ringer Modes

| Mode | Description |
|------|-------------|
| `normal` | All sounds enabled |
| `vibrate` | Vibrate only |
| `silent` | No sounds or vibration |

## Use Cases

| Use Case | Action | Description |
|----------|--------|-------------|
| Check volume | status | Get current levels |
| Lower volume | set_volume | Reduce for quiet time |
| Silent mode | mute | Mute all audio |
| Restore sound | unmute | Re-enable audio |
| Night mode | set_volume | Lower ring/notification |

## Common Workflows

### Meeting mode

1. Mute device
2. Attend meeting
3. Unmute when done

### Night routine

1. Set ring volume to 20
2. Set notification volume to 0
3. Keep alarm at 100

### Media playback

1. Check current media volume
2. Set to desired level
3. Play media

## Volume Level Range

- All volumes are 0-100 (percentage)
- 0 = muted/off
- 100 = maximum

## Setup Requirements

1. Connect the **Audio Automation** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Volume control permission required
