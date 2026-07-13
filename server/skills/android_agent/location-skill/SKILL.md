---
name: location-skill
description: Get Android device GPS location - latitude, longitude, accuracy, speed, and provider information.
allowed-tools: location
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Location Tool

Get GPS location from Android device.

## How It Works

This skill provides instructions for the **Location** tool node. Connect the **Location** node to Zeenie's `input-tools` handle to enable location tracking.

## location Tool

Get current GPS location from the Android device.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"status"` - Get current location |

### Actions

| Action | Description |
|--------|-------------|
| `status` | Get current GPS coordinates and location info |

### Example

**Get current location:**
```json
{
  "action": "status"
}
```

### Response Format

```json
{
  "success": true,
  "service": "location",
  "action": "status",
  "data": {
    "latitude": 37.7749,
    "longitude": -122.4194,
    "altitude": 10.5,
    "accuracy": 15.0,
    "speed": 0.0,
    "bearing": 180.0,
    "provider": "gps",
    "timestamp": "2025-01-30T12:00:00Z"
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| latitude | float | Latitude in degrees |
| longitude | float | Longitude in degrees |
| altitude | float | Altitude in meters (if available) |
| accuracy | float | Horizontal accuracy in meters |
| speed | float | Speed in m/s (if moving) |
| bearing | float | Direction of travel in degrees |
| provider | string | `"gps"`, `"network"`, `"fused"` |
| timestamp | string | When location was obtained |

### Error Response

```json
{
  "error": "Location not available - GPS may be disabled",
  "service": "location",
  "action": "status"
}
```

## Accuracy Guide

| Accuracy (m) | Quality | Typical Source |
|--------------|---------|----------------|
| < 5 | Excellent | GPS with clear sky |
| 5-15 | Good | GPS |
| 15-50 | Fair | Network/WiFi |
| 50-100 | Poor | Cell tower |
| > 100 | Very Poor | Coarse location |

## Provider Types

| Provider | Description |
|----------|-------------|
| gps | GPS satellites (most accurate outdoors) |
| network | WiFi/Cell towers |
| fused | Combined sources (Android Fused Location) |

## Use Cases

| Use Case | Description |
|----------|-------------|
| Location tracking | Get device position |
| Geofencing | Check if in specific area |
| Speed monitoring | Track movement speed |
| Navigation | Get coordinates for routing |

## Common Workflows

### Share location via WhatsApp

1. Get location from device
2. Use `whatsapp_send` with message_type="location"
3. Pass latitude/longitude from location result

### Location-based reminder

1. Get current location
2. Calculate distance to target
3. Trigger reminder when near target

### Track movement

1. Periodically get location
2. Store coordinates
3. Calculate distance traveled

## Integration with Other Tools

### Send location to contact

```
1. location.status -> get lat/lng
2. whatsapp_send with:
   - message_type: "location"
   - latitude: <from location>
   - longitude: <from location>
```

### Find nearby places

```
1. location.status -> get lat/lng
2. nearby_places with:
   - lat: <from location>
   - lng: <from location>
   - type: "restaurant"
```

## Setup Requirements

1. Connect the **Location** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Location permission must be granted
4. GPS/Location services must be enabled on device
