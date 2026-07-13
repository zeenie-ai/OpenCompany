---
name: environmental-skill
description: Get Android environmental sensor data - temperature, humidity, pressure, and ambient light level.
allowed-tools: environmental_sensors
metadata:
  author: opencompany
  version: "1.0"
  category: android

---

# Environmental Sensors Tool

Access environmental sensors on Android device.

## How It Works

This skill provides instructions for the **Environmental Sensors** tool node. Connect the **Environmental Sensors** node to Zeenie's `input-tools` handle to enable environmental sensing.

## environmental_sensors Tool

Get temperature, humidity, pressure, and light data.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes | `"status"` - Get sensor data |

### Example

**Get environmental data:**
```json
{
  "action": "status"
}
```

### Response Format

```json
{
  "success": true,
  "service": "environmental_sensors",
  "action": "status",
  "data": {
    "temperature": 23.5,
    "humidity": 45.0,
    "pressure": 1013.25,
    "light": 350.0,
    "sensors_available": {
      "temperature": true,
      "humidity": true,
      "pressure": true,
      "light": true
    }
  }
}
```

### Response Fields

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| temperature | float | Celsius | Ambient temperature |
| humidity | float | % | Relative humidity |
| pressure | float | hPa | Atmospheric pressure |
| light | float | lux | Ambient light level |
| sensors_available | object | - | Which sensors exist |

### Sensor Availability

Not all devices have all sensors. Check `sensors_available` to see what's supported.

### Light Level Guide

| Lux | Condition |
|-----|-----------|
| < 50 | Dark/dim room |
| 50-300 | Indoor lighting |
| 300-1000 | Bright indoor |
| 1000-10000 | Overcast outdoor |
| 10000-100000 | Direct sunlight |

### Temperature Notes

- Device temperature sensor may be affected by device heat
- For accurate ambient temperature, device should be idle
- Some devices don't have temperature sensor

### Pressure Guide

| hPa | Weather |
|-----|---------|
| < 1000 | Low pressure (storms) |
| 1000-1020 | Normal |
| > 1020 | High pressure (clear) |

## Use Cases

| Use Case | Sensor | Description |
|----------|--------|-------------|
| Weather tracking | pressure | Monitor local pressure |
| Humidity alert | humidity | Warn on high/low humidity |
| Light-based actions | light | Trigger on dark/bright |
| Temperature monitoring | temperature | Track ambient temp |

## Common Workflows

### Auto-brightness trigger

1. Read light level
2. If light < 100, suggest dark mode
3. If light > 1000, suggest bright mode

### Environment monitoring

1. Periodically read all sensors
2. Log data points
3. Alert on thresholds

### Weather correlation

1. Track pressure over time
2. Correlate with weather changes
3. Predict weather patterns

## Limitations

- Sensor availability varies by device model
- Temperature sensor may read higher due to device heat
- Indoor pressure differs from outdoor
- Light sensor is typically near front camera

## Setup Requirements

1. Connect the **Environmental Sensors** node to Zeenie's `input-tools` handle
2. Android device must be paired
3. Sensors depend on device hardware
