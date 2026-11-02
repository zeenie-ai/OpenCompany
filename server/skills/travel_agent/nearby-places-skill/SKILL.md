---
name: nearby-places-skill
description: Search for nearby places like restaurants, cafes, stores, and services using Google Places API. Find places by type and location.
allowed-tools: "gmaps_nearby_places"
metadata:
  author: opencompany
  version: "1.0"
  category: location

---

# Nearby Places

Use the connected **Nearby Places** node's callable `nearby_places`. The skill's
`gmaps_nearby_places` metadata token identifies its visuals; use the callable
name shown in the current tool catalogue.

## Current implementation limitation

The node declares `latitude`, `longitude` and `place_type`, but its current
service helper still reads the older `lat`, `lng` and `type` keys after Params
validation has discarded them. It therefore searches its fallback location
(40.7484, -73.9857) and type (`restaurant`) instead of reliably honoring the
requested location/type. The top-level `keyword` is also ignored by the helper's
default nested-options branch.

Do not present these fallback results as nearby the user's requested location.
Inspect the returned `search_parameters`; report the limitation when it does
not match the request. Adding legacy keys to a tool call does not repair the
mapping. This is a code gap, not a configuration workaround.

## Declared arguments

| Field | Type | Default | Meaning |
|---|---|---|---|
| latitude | number | required | -90 through 90 |
| longitude | number | required | -180 through 180 |
| radius | integer | 1000 | 1 through 50000 meters |
| place_type | string | empty | Requested place type; affected by the mapping gap above |
| keyword | string | empty | Requested text filter; affected by the gap above |

Schema-valid example (the implementation limitation still applies):

```json
{"latitude":48.8584,"longitude":2.2945,"radius":1000,"place_type":"cafe"}
```

## Results

The current helper returns `search_parameters`, `results`, `total_results` and
`status`. Check the echoed location, type and keyword before using the results.
The declared Output convenience fields `places` and `count` are not the
helper's current response keys. Empty results and provider errors must be
reported as returned.

Google Maps credentials are required, but the current node also drops the
executor-injected saved key during Params validation. Its service can use the
runtime's `google_maps_api_key` setting (`GOOGLE_MAPS_API_KEY`); a saved
connection alone can fail. Do not add secret arguments to bypass that gap.
Calls record usage and may incur provider charges. Provider limits and live place accuracy are not verified by
a schema-valid example.
