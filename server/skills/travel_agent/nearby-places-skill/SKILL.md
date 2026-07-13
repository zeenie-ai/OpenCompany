---
name: nearby-places-skill
description: Search for nearby places like restaurants, cafes, stores, and services using Google Places API. Find places by type and location.
allowed-tools: "gmaps_nearby_places"
metadata:
  author: opencompany
  version: "1.0"
  category: location

---

# Nearby Places Tool

Search for places near a location using Google Places API.

## How It Works

This skill provides instructions for the **Show Nearby Places** (gmaps_nearby_places) tool node. Connect the **Show Nearby Places** node to Zeenie's `input-tools` handle to enable place search.

## show_nearby_places Tool

Search for places near GPS coordinates.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| lat | float | Yes | Center latitude for search |
| lng | float | Yes | Center longitude for search |
| radius | int | No | Search radius in meters (default: 500, max: 50000) |
| type | string | No | Place type to search for (default: "restaurant") |
| keyword | string | No | Keyword to filter results |

### Place Types

**Food & Drink:**
- `restaurant` - Restaurants
- `cafe` - Coffee shops
- `bar` - Bars and pubs
- `bakery` - Bakeries
- `meal_takeaway` - Takeout restaurants

**Shopping:**
- `store` - General stores
- `supermarket` - Grocery stores
- `shopping_mall` - Shopping centers
- `clothing_store` - Clothing shops
- `convenience_store` - Convenience stores

**Services:**
- `bank` - Banks
- `atm` - ATMs
- `gas_station` - Gas/petrol stations
- `pharmacy` - Pharmacies
- `post_office` - Post offices

**Health:**
- `hospital` - Hospitals
- `doctor` - Doctor's offices
- `dentist` - Dental clinics

**Transport:**
- `bus_station` - Bus stations
- `train_station` - Train stations
- `airport` - Airports
- `taxi_stand` - Taxi stands
- `parking` - Parking lots

**Entertainment:**
- `movie_theater` - Cinemas
- `gym` - Fitness centers
- `park` - Parks
- `museum` - Museums
- `zoo` - Zoos

**Accommodation:**
- `hotel` - Hotels
- `lodging` - All lodging types

### Examples

**Find nearby restaurants:**
```json
{
  "lat": 40.7484,
  "lng": -73.9857,
  "radius": 500,
  "type": "restaurant"
}
```

**Find coffee shops:**
```json
{
  "lat": 37.7749,
  "lng": -122.4194,
  "type": "cafe",
  "radius": 1000
}
```

**Find specific chain:**
```json
{
  "lat": 37.7749,
  "lng": -122.4194,
  "type": "cafe",
  "keyword": "starbucks"
}
```

**Find gas stations:**
```json
{
  "lat": 34.0522,
  "lng": -118.2437,
  "type": "gas_station",
  "radius": 2000
}
```

**Find hotels:**
```json
{
  "lat": 51.5074,
  "lng": -0.1278,
  "type": "hotel",
  "radius": 1000
}
```

### Response Format

```json
{
  "success": true,
  "type": "restaurant",
  "search_parameters": {
    "location": {"lat": 40.7484, "lng": -73.9857},
    "radius": 500,
    "type": "restaurant"
  },
  "results": [
    {
      "name": "Example Restaurant",
      "vicinity": "123 Main St, New York",
      "rating": 4.5,
      "user_ratings_total": 150,
      "price_level": 2,
      "geometry": {
        "location": {"lat": 40.7485, "lng": -73.9860}
      },
      "types": ["restaurant", "food", "establishment"],
      "opening_hours": {"open_now": true}
    }
  ],
  "total_results": 10,
  "status": "OK"
}
```

### Response Fields

| Field | Description |
|-------|-------------|
| name | Place name |
| vicinity | Address or location description |
| rating | Average rating (1-5) |
| user_ratings_total | Number of reviews |
| price_level | Price level (0-4, 0=free, 4=very expensive) |
| geometry.location | Place coordinates |
| types | Place type categories |
| opening_hours.open_now | Whether currently open |

### Error Response

```json
{
  "error": "lat and lng are required for nearby places search"
}
```

## Price Level Guide

| Level | Meaning |
|-------|---------|
| 0 | Free |
| 1 | Inexpensive ($) |
| 2 | Moderate ($$) |
| 3 | Expensive ($$$) |
| 4 | Very Expensive ($$$$) |

## Common Workflows

### Find places near an address

1. Use `geocoding` tool to convert address to coordinates
2. Use `nearby_places` with the lat/lng from step 1

### Find best-rated places

1. Search with appropriate type and radius
2. Sort results by rating in response
3. Filter by minimum rating threshold

### Find open places now

1. Search with desired type
2. Check `opening_hours.open_now` in results

## Radius Guidelines

| Radius | Use Case |
|--------|----------|
| 100-500m | Walking distance |
| 500-1000m | Short walk |
| 1000-2000m | 5-10 min walk |
| 2000-5000m | Driving distance |
| 5000-50000m | City-wide search |

## Tips

1. **Use specific types**: "cafe" not "coffee"
2. **Add keywords**: Filter by chain name or cuisine
3. **Adjust radius**: Start small, expand if needed
4. **Check ratings**: Higher ratings usually mean better quality
5. **Verify open_now**: Important for time-sensitive searches

## Setup Requirements

1. Connect the **Show Nearby Places** (gmaps_nearby_places) node to Zeenie's `input-tools` handle
2. Google Maps API key must be configured in Credentials
3. Places API must be enabled in Google Cloud Console
