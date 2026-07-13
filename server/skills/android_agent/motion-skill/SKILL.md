---
name: motion-skill
description: Get Android motion sensor data - accelerometer, gyroscope, detect motion, shake gestures, and device orientation.
allowed-tools: motion_detection
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Motion Detection Tool

Access motion sensors on Android device.

## How It Works

This skill provides instructions for the **Motion Detection** tool node. Connect the **Motion Detection** node to Zeenie's `input-tools` handle to enable motion sensing.

## motion_detection Tool

Get accelerometer, gyroscope, and motion data.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"status"` - Get motion sensor data |

### Example

**Get motion data:**
```json
{
  "action": "status"
}
```

### Response Format

```json
{
  "success": true,
  "service": "motion_detection",
  "action": "status",
  "data": {
    "accelerometer": {
      "x": 0.12,
      "y": 9.78,
      "z": 0.34
    },
    "gyroscope": {
      "x": 0.01,
      "y": 0.02,
      "z": 0.00
    },
    "orientation": {
      "azimuth": 45.0,
      "pitch": -5.0,
      "roll": 2.0
    },
    "is_moving": false,
    "is_shaking": false,
    "device_position": "flat"
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| accelerometer | object | X, Y, Z acceleration (m/s^2) |
| gyroscope | object | X, Y, Z rotation rate (rad/s) |
| orientation | object | Device orientation angles |
| is_moving | boolean | Significant movement detected |
| is_shaking | boolean | Shake gesture detected |
| device_position | string | Inferred position |

### Orientation Fields

| Field | Description |
|-------|-------------|
| azimuth | Compass heading (0-360 degrees) |
| pitch | Forward/backward tilt (-180 to 180) |
| roll | Left/right tilt (-90 to 90) |

### Device Positions

| Position | Description |
|----------|-------------|
| `flat` | Laying flat on surface |
| `upright` | Standing vertical |
| `tilted` | Angled position |
| `face_down` | Screen facing down |
| `face_up` | Screen facing up |

## Accelerometer Interpretation

When device is stationary and flat:
- X ~ 0 (left-right)
- Y ~ 9.8 (gravity)
- Z ~ 0 (forward-backward)

Movement creates deviations from gravity baseline.

## Use Cases

| Use Case | Data | Description |
|----------|------|-------------|
| Shake detection | is_shaking | Trigger on shake gesture |
| Movement alert | is_moving | Detect device moved |
| Orientation | device_position | Check how device is held |
| Compass heading | azimuth | Get direction |
| Tilt detection | pitch, roll | Detect angles |

## Common Workflows

### Shake to trigger action

1. Poll motion status
2. When is_shaking = true
3. Execute action

### Security monitor

1. Place device as monitor
2. Check is_moving periodically
3. Alert if movement detected

### Orientation-based response

1. Check device_position
2. Adjust behavior based on orientation

## Setup Requirements

1. Connect the **Motion Detection** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Motion sensors must be available on device
