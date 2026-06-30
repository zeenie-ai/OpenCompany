# Travel Agent (`travel_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/travel_agent/__init__.py`](../../../server/nodes/agent/travel_agent/__init__.py) -> [`_specialized.py::SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) (dispatch via `BaseNode.execute()`) |
| **Theme color** | `dracula.orange` |
| **Icon** | airplane (U+2708) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

AI agent pre-configured for travel planning. Typical tool connections:
`gmaps_locations`, `gmaps_nearby_places`, `googleCalendar`, `braveSearch`.

## What is unique to this node

- **Intended tool set**: Google Maps, calendar, search.
- **Intended skills**: `server/skills/travel_agent/` (geocoding-skill,
  nearby-places-skill).
- **Frontend theming**: orange dracula accent.

## Behaviour

See **[Generic Specialized Agent Pattern](./_pattern.md)**.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/travel_agent/`](../../../server/skills/travel_agent/)
