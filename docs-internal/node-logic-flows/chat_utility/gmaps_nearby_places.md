# Nearby Places (`gmaps_nearby_places`)

| Field | Value |
|------|-------|
| **Category** | chat_utility (grouping) / location (functional domain; palette groups `location`, `service`, `tool`) |
| **Backend handler** | [`server/nodes/location/gmaps_nearby_places/__init__.py::GmapsNearbyPlacesNode.nearby`](../../../server/nodes/location/gmaps_nearby_places/__init__.py) — dispatch via `BaseNode.execute()` + `@Operation("nearby")` -> [`server/nodes/location/_service.py::MapsService.find_nearby_places`](../../../server/nodes/location/_service.py) |
| **Tests** | none dedicated — spec-level coverage only: `TestPhase3dCoverage` in [`server/tests/test_node_spec.py`](../../../server/tests/test_node_spec.py), identifier list in [`server/tests/services/test_identifiers.py`](../../../server/tests/services/test_identifiers.py), tool-name snapshot in [`server/tests/fixtures/tool_names_snapshot.json`](../../../server/tests/fixtures/tool_names_snapshot.json) |
| **Skill (if any)** | [`server/skills/travel_agent/nearby-places-skill/SKILL.md`](../../../server/skills/travel_agent/nearby-places-skill/SKILL.md) (`allowed-tools: "gmaps_nearby_places"`) |
| **Dual-purpose tool** | yes - tool name `nearby_places` (`usable_as_tool = True`) |

## Purpose

Google Places API `nearbySearch` around a coordinate: restaurants, banks,
hospitals, etc., filtered by type and keyword. Used as a workflow node or as
the `nearby_places` tool on a travel/consumer agent. Each call tracks a
`nearby_search` usage row at $0.032.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream coordinates (e.g. from `gmaps_locations`) via templates |
| `output-tool` (source, synthesized) | tools | no | `usable_as_tool = True` with neither hide flag declared: canvas `input-main` / `output-main` are flagged hidden and `_metadata_dict` appends `output-tool` |

## Parameters

Params model `GmapsNearbyPlacesParams` (`extra="ignore"`):

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `latitude` | number, `ge=-90, le=90` | - | yes | - | Search centre latitude — **not read by the service** (see Edge cases) |
| `longitude` | number, `ge=-180, le=180` | - | yes | - | Search centre longitude — **not read by the service** |
| `radius` | integer, `ge=1, le=50000` | `1000` | no | - | Search radius in metres (the only Param the service actually consumes) |
| `place_type` | string | `""` | no | - | Place type — **not read by the service** (it reads `type`) |
| `keyword` | string | `""` | no | - | Keyword filter — **not read by the service** (it reads `options.keyword`) |

The API key is not a Param; the injected `api_key` is dropped by
`extra="ignore"` before `params.model_dump()` and the service falls back to
`settings.google_maps_api_key` (see [`gmaps_locations`](./gmaps_locations.md)).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | The service's `result` dict. `GmapsNearbyPlacesOutput` declares `places` / `count`, but the op returns the raw service payload (`extra="allow"`, declared fields absent) |

### Output payload (TypeScript shape)

```ts
{
  search_parameters: {
    location: { lat: number; lng: number };
    radius: number | null;            // null when rank_by === 'distance'
    type: string;
    keyword: string | null;
    name_filter: string | null;
    min_price: number | null;
    max_price: number | null;
    open_now: boolean;
    language: string;
    rank_by: 'prominence' | 'distance';
    page_size: number;                // <= 20
  };
  results: Array<GooglePlaceResult>;  // raw googlemaps rows, sliced to page_size
  total_results: number;
  status: string;                     // Google status, default 'OK'
}
```

## Logic Flow

```mermaid
flowchart TD
  A[nearby op: params.model_dump -> latitude, longitude, radius, place_type, keyword] --> B[MapsService.find_nearby_places node_id, params, ctx.raw]
  B --> C{api_key from params or settings?}
  C -- none --> E[success false: Google Maps API key is required]
  C -- ok --> D[lat = params.lat default 40.7484; lng = params.lng default -73.9857; radius = params.radius]
  D --> F{validate_coordinates and 1 <= radius <= 50000}
  F -- invalid --> E2[success false]
  F -- ok --> G[type = params.type default restaurant; page_size min 20; options = params.options default {}]
  G --> H[keyword/name/min_price/max_price/open_now/language/rank_by read from options dict]
  H --> I[search_params location, type, radius unless rank_by distance, + optional filters]
  I --> J[gmaps.places_nearby - synchronous call]
  J -- ApiError --> E3[success false: Google Places API error: ...]
  J --> K[results sliced to page_size]
  K --> L[_track_maps_usage nearby_search]
  L --> M[result search_parameters, results, total_results, status]
  M --> N[op returns response.result]
  E & E2 & E3 --> O[op raises NodeUserError response.error]
```

## Decision Logic

- **Validation**: Pydantic enforces the coordinate and radius ranges on the Params; the service re-validates `lat`/`lng` (on its own defaulted values) and `radius`.
- **Branches**: `rank_by == "distance"` drops `radius` and sends `rank_by`; each optional filter is added only when set. All of these branches are reachable only through the service's `options` dict, which the plugin never populates.
- **Fallbacks** (service defaults, all of which fire under the current wiring): `lat 40.7484`, `lng -73.9857`, `type "restaurant"`, `page_size 20`, `language "en"`, `rank_by "prominence"`, `open_now False`.
- **Error paths**: `googlemaps.exceptions.ApiError` -> `success: false` with "Google Places API error: "; any other exception -> `success: false`; the op raises `NodeUserError(error or "Nearby places failed")`.

## Side Effects

- **Database writes**: one `api_usage_metrics` row per successful call (`service: google_maps`, `operation`/`endpoint: nearby_search`, `resource_count: 1`, $0.032 per `pricing.json`; `operation_map` also maps `nearby_places` -> `nearby_search`). The `cost=` on `@Operation` (`action: places_nearby`) is inert metadata and names an action `pricing.json` does not list.
- **Broadcasts**: none.
- **External API calls**: Google Places Nearby Search through `googlemaps.Client.places_nearby`.
- **File I/O / Subprocess**: none.

## External Dependencies

- **Credentials**: `GoogleMapsCredential` (`google_maps`); at execution time effectively `settings.google_maps_api_key`.
- **Services**: `MapsService` via `get_maps_service()`, `PricingService`.
- **Python packages**: `googlemaps`.
- **Environment variables**: `GOOGLE_MAPS_API_KEY`.
- **Task queue**: `TaskQueue.REST_API`. Annotations: `readonly`, `open_world`.

## Edge cases & known limits

- **Stored credential alone fails**: injected `api_key` is removed by `extra="ignore"` before the service receives `params.model_dump()`. The service uses the `GOOGLE_MAPS_API_KEY` settings fallback; without it a saved `google_maps` credential does not prevent the missing-key failure. The preserved raw parameters in context are unused by this helper.
- **Param/service key mismatch (load-bearing)**: the plugin dumps `latitude` / `longitude` / `place_type` / `keyword`, but `find_nearby_places` reads `lat` / `lng` / `type` and takes `keyword` only from a nested `options` dict (`parameters.get("options", {})` is a dict, so the top-level `keyword` fallback branch is never taken). Under the current wiring every call therefore searches for `restaurant` within `radius` metres of `40.7484, -73.9857` regardless of the configured centre, type or keyword. Only `radius` reaches the API. (Documented, not fixed here; same class of bug as the `gmaps_create` card.)
- The tool path cannot route around it: the LLM schema is derived from Params (`latitude`, `longitude`, ...). Undeclared `lat` / `lng` / `type` arguments are stripped by `extra="ignore"` during the `{**node_params, **tool_args}` merge.
- The service's richer filters (`name`, `min_price`, `max_price`, `open_now`, `language`, `rank_by`) are unreachable from the node.
- Synchronous `googlemaps` call blocks the event loop.
- `results` is the raw Google payload (up to 20 rows of several KB each), persisted, broadcast and replayed into LLM context when used as a tool.
- Usage is charged on every successful call, including empty result sets.
- The service does not populate declared `Output` fields (`places`, `count`); validation may emit their null defaults. Read actual places/count from `results` / `total_results`.
- The registered display name is "Nearby Places", the callable is `nearby_places`, and the Params field is `place_type`. Use those identifiers in skills; they do not repair the service key mismatch above.

## Related

- **Skills using this as a tool**: [`nearby-places-skill`](../../../server/skills/travel_agent/nearby-places-skill/SKILL.md).
- **Other nodes that feed this**: [`gmaps_locations`](./gmaps_locations.md) (coordinates from an address); [`gmaps_create`](./gmaps_create.md) shares the same `MapsService` and key-fallback behaviour.
- **Architecture docs**: [pricing_service.md](../../pricing_service.md), [plugin_system.md](../../plugin_system.md).
