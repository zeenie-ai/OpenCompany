# Location (`location`)

| Field | Value |
|------|-------|
| **Category** | android / monitoring |
| **Backend handler** | plugin [`server/nodes/android/location/__init__.py`](../../../server/nodes/android/location/__init__.py); dispatch via `BaseNode.execute()` -> shared [`AndroidServiceBase.invoke`](../../../server/nodes/android/_base.py) (`@Operation("invoke")`) |
| **Tests** | [`server/tests/nodes/test_android.py`](../../../server/tests/nodes/test_android.py) |
| **Skill (if any)** | [`server/skills/android_agent/location-skill/SKILL.md`](../../../server/skills/android_agent/location-skill/SKILL.md) |
| **Direct agent tool** | connectable to any agent's `input-tools` |

## Purpose

GPS location with latitude, longitude, accuracy, altitude, speed, and provider.

## Backend service mapping

| Field | Value |
|------|-------|
| `SERVICE_ID_MAP[location]` | `location` |
| Default action | `current` |

## Parameters

Shared parameter set only. See [`_pattern.md`](./_pattern.md#shared-parameter-set).

## Logic Flow (node-specific slice)

```mermaid
flowchart TD
  A[Dispatch] --> B[service_id = 'location']
  B --> C{action}
  C -- current --> D[POST /api/location {action: current}]
  C -- other --> D
  D --> E[Flatten data: latitude, longitude, accuracy,<br/>altitude, speed, provider]
```

## Edge cases & known limits

- Android requires runtime location permission on the connected device - a
  permission error surfaces as `success=false` with the device's error string.
- Shared edge cases only otherwise.

## Related

- Skill: [`location-skill/SKILL.md`](../../../server/skills/android_agent/location-skill/SKILL.md)
- Shared pattern: [`_pattern.md`](./_pattern.md)
- Also see the Google Maps node family (`gmaps_locations`, `gmaps_nearby_places`)
  for non-Android geocoding.
