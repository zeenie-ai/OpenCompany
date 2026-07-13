---
name: geocoding-skill
description: Convert addresses to coordinates (geocoding) or coordinates to addresses (reverse geocoding) using Google Maps API.
allowed-tools: "gmaps_locations"
metadata:
  author: opencompany
  version: "1.0"
  category: location

---

# Geocoding Tool

Convert addresses to GPS coordinates or coordinates to addresses using Google Maps Geocoding API.

## How It Works

This skill provides instructions for the **Add Locations** (gmaps_locations) tool node. Connect the **Add Locations** node to Zeenie's `input-tools` handle to enable geocoding operations.

## add_locations Tool

Geocode addresses or reverse geocode coordinates.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| service_type | string | Yes | `"geocode"` (address to coordinates) or `"reverse_geocode"` (coordinates to address) |
| address | string | If geocode | Address to convert to coordinates |
| lat | float | If reverse_geocode | Latitude coordinate |
| lng | float | If reverse_geocode | Longitude coordinate |

### Operations

| Service Type | Input | Output |
|--------------|-------|--------|
| `geocode` | Address string | Coordinates (lat, lng) |
| `reverse_geocode` | Coordinates (lat, lng) | Formatted address |

### Examples

**Geocode an address:**
```json
{
  "service_type": "geocode",
  "address": "1600 Amphitheatre Parkway, Mountain View, CA"
}
```

**Geocode a landmark:**
```json
{
  "service_type": "geocode",
  "address": "Eiffel Tower, Paris"
}
```

**Geocode a city:**
```json
{
  "service_type": "geocode",
  "address": "New York City, USA"
}
```

**Reverse geocode (coordinates to address):**
```json
{
  "service_type": "reverse_geocode",
  "lat": 48.8584,
  "lng": 2.2945
}
```

### Response Format

**Geocode response:**
```json
{
  "success": true,
  "service_type": "geocoding",
  "input": {"address": "Eiffel Tower, Paris"},
  "results": [
    {
      "formatted_address": "Champ de Mars, 5 Av. Anatole France, 75007 Paris, France",
      "geometry": {
        "location": {"lat": 48.8583701, "lng": 2.2944813}
      },
      "address_components": [
        {"long_name": "5", "short_name": "5", "types": ["street_number"]},
        {"long_name": "Avenue Anatole France", "short_name": "Av. Anatole France", "types": ["route"]},
        {"long_name": "Paris", "short_name": "Paris", "types": ["locality"]}
      ]
    }
  ],
  "status": "OK"
}
```

**Reverse geocode response:**
```json
{
  "success": true,
  "service_type": "reverse_geocoding",
  "input": {"lat": 48.8584, "lng": 2.2945},
  "results": [
    {
      "formatted_address": "Champ de Mars, 5 Av. Anatole France, 75007 Paris, France",
      "geometry": {
        "location": {"lat": 48.8583701, "lng": 2.2944813}
      },
      "address_components": [...]
    }
  ],
  "status": "OK"
}
```

### Error Response

```json
{
  "error": "address is required for geocoding"
}
```

```json
{
  "error": "Geocoding failed: ZERO_RESULTS"
}
```

## Use Cases

| Use Case | Service Type | Description |
|----------|--------------|-------------|
| Find location | geocode | Get coordinates for an address |
| Validate address | geocode | Get standardized address format |
| Get address | reverse_geocode | Find address from GPS coordinates |
| Location lookup | geocode | Search for landmarks, POIs |

## Common Workflows

### Get coordinates for sending location via WhatsApp

1. Use `geocode` to convert address to coordinates
2. Extract `lat` and `lng` from response
3. Use `whatsapp_send` with message_type="location"

### Identify a GPS location

1. Use `reverse_geocode` with lat/lng coordinates
2. Get formatted address from response

### Search for nearby places

1. Use `geocode` to get coordinates from address
2. Use `nearby_places` tool with those coordinates

## Address Format Guidelines

**Good address formats:**
- `"123 Main Street, New York, NY 10001"`
- `"Eiffel Tower, Paris, France"`
- `"Times Square, NYC"`
- `"Tokyo Station, Japan"`

**Tips:**
- Include city and country for better accuracy
- Landmarks and POIs work well
- Postal codes improve precision
- Use local language or English

## Coordinate Guidelines

- Latitude range: -90 to 90
- Longitude range: -180 to 180
- More decimal places = higher precision
- Standard precision: 6 decimal places

## Setup Requirements

1. Connect the **Add Locations** (gmaps_locations) node to Zeenie's `input-tools` handle
2. Google Maps API key must be configured in Credentials
3. Geocoding API must be enabled in Google Cloud Console
