---
name: geocoding-skill
description: Convert addresses to coordinates (geocoding) or coordinates to addresses (reverse geocoding) using Google Maps API.
allowed-tools: "gmaps_locations"
metadata:
  author: opencompany
  version: "1.0"
  category: location

---

# Geocoding

Use the connected **Geocoding** node's callable `geocode`. The skill's
`gmaps_locations` metadata token identifies its visuals; it is not the callable
name. A saved tool override can change the name shown in the tool catalogue.

The current node drops the executor-injected saved Maps key during Params
validation, while its service reads only the parameter key or the runtime's
`google_maps_api_key` setting (`GOOGLE_MAPS_API_KEY`). A saved connection alone
can therefore fail; the runtime setting can mask that code gap. Report the
credential error and direct the owner to configuration/diagnostics. Do not put
a secret in a tool argument to bypass validation.

## Arguments

| Field | Type | Default | Meaning |
|---|---|---|---|
| service_type | enum | geocode | `geocode` or `reverse_geocode` |
| address | string | empty | Required for forward geocoding |
| region | string | empty | Declared bias field; the current helper does not forward it |
| lat | number | 0 | Latitude for reverse geocoding |
| lng | number | 0 | Longitude for reverse geocoding |

## Examples

Forward geocoding:

```json
{"service_type":"geocode","address":"Eiffel Tower, Paris"}
```

Reverse geocoding:

```json
{"service_type":"reverse_geocode","lat":48.8584,"lng":2.2945}
```

## Results and limits

The current helper returns `service_type`, `input`, `results` and `status`.
For forward results, inspect each entry's `geometry.location.lat` /
`geometry.location.lng` and `formatted_address`. Reverse results carry candidate
addresses in `results`. `ZERO_RESULTS` is a valid empty result; do not invent a
location.

The node's declared Output model lists convenience fields such as `latitude`
and `formatted_address`, but the current helper returns the raw result list
instead. Read the actual response rather than assuming those convenience
fields are populated. Invalid coordinates, missing credentials and API failures
surface as errors.

Provider charges, quotas and availability are external to this skill. A
successful invocation records usage; it does not establish that the returned
place is appropriate for the user's purpose.
