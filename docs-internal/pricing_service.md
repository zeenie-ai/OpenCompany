# API Cost Tracking and Pricing Service

## Overview

The pricing service provides centralized cost tracking for both LLM tokens and external API services (Twitter/X, Google Maps, TikHub). It supports:

- **LLM Token Costs**: Per-model pricing with input/output/cache/reasoning token breakdown
- **API Service Costs**: Per-request/resource pricing for third-party APIs (Twitter/X, Google Maps, TikHub, Search APIs)
- **Automatic Tracking**: HTTPX event hooks for transparent API call tracking
- **Manual Tracking**: Helper functions for services that don't use HTTPX

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Pricing Configuration                          │
│                 server/config/pricing.json                       │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────┐ │
│  │   llm     │  │    api    │  │ operation │  │  url_patterns │ │
│  │  pricing  │  │  pricing  │  │    _map   │  │               │ │
│  └───────────┘  └───────────┘  └───────────┘  └───────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PricingService                                │
│                 server/services/pricing.py                       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ LLM Methods:                                                │ │
│  │  - get_pricing(provider, model) → ModelPricing             │ │
│  │  - calculate_cost(provider, model, tokens...) → cost dict  │ │
│  │                                                             │ │
│  │ API Methods:                                                │ │
│  │  - get_api_price(service, operation) → USD                 │ │
│  │  - map_action_to_operation(service, action) → operation    │ │
│  │  - calculate_api_cost(service, action, count) → cost dict  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│ Manual Tracking   │ │ HTTPX Event Hooks │ │  AI Service       │
│ track_*_usage()   │ │ tracked_http.py   │ │ _track_token_     │
│ location/_service │ │                   │ │ usage() ai.py     │
│ twitter/_base     │ │                   │ │                   │
│ google/_base      │ │                   │ │                   │
│ proxy/_usage      │ │                   │ │                   │
└───────────────────┘ └───────────────────┘ └───────────────────┘
            │                 │                 │
            └─────────────────┼─────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Database Storage                            │
│  ┌────────────────────────┐  ┌────────────────────────────────┐ │
│  │   APIUsageMetric       │  │     TokenUsageMetric           │ │
│  │   (twitter, maps)      │  │     (LLM token usage)          │ │
│  └────────────────────────┘  └────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration: pricing.json

Located at `server/config/pricing.json`. User-editable; picked up via `PricingService.reload()` or `save_config()`; there is no filesystem watcher.

### Structure

```json
{
  "version": "2026.09",
  "last_updated": "2026-09-26",

  "llm": {
    "openai": {
      "gpt-6-sol": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
      "gpt-6-luna": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
      "_default": {"input": 2.50, "output": 15.00}
    },
    "anthropic": {
      "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.40},
      "_default": {"input": 3.00, "output": 15.00, "cache_read": 0.30}
    }
  },

  "api": {
    "google_maps": {
      "_description": "Google Maps pricing per request (USD)",
      "geocode": 0.005,
      "nearby_search": 0.032,
      "_default": 0.005
    },
    "twitter": {
      "_description": "X/Twitter Pay-Per-Use API pricing",
      "posts_read": 0.005,
      "content_create": 0.010,
      "user_read": 0.010
    },
    "brave_search": {
      "_description": "Brave Search API pricing (per query)",
      "web_search": 0.003,
      "_default": 0.003
    },
    "serper": {
      "_description": "Serper Google Search API pricing (per query)",
      "web_search": 0.001,
      "_default": 0.001
    },
    "perplexity": {
      "_description": "Perplexity Sonar API pricing (per request)",
      "sonar_search": 0.005,
      "sonar_pro_search": 0.005,
      "_default": 0.005
    }
  },

  "operation_map": {
    "google_maps": {
      "geocode": "geocode",
      "nearby_places": "nearby_search"
    },
    "twitter": {
      "tweet": "content_create",
      "search": "posts_read"
    },
    "brave_search": {
      "web_search": "web_search"
    },
    "serper": {
      "web_search": "web_search"
    },
    "perplexity": {
      "sonar_search": "sonar_search"
    }
  },

  "url_patterns": {
    "twitter": {
      "base": "https?://api\\.(twitter|x)\\.com",
      "actions": {
        "/2/tweets$": {"action": "content_create", "method": "POST"},
        "/2/tweets/search": {"action": "posts_read", "method": "GET", "count_path": "data.length"}
      }
    }
  }
}
```

### Sections

| Section | Purpose |
|---------|---------|
| `llm` | Per-model pricing in USD per million tokens (MTok). Supports `input`, `output`, `cache_read`, `reasoning`. `input` and `output` follow the OpenRouter snapshot (`config/model_registry.json`) for every model it lists; it carries no cache price, so `cache_read` keeps each vendor's published cache discount applied to that input price. A lookup tries the exact id, then the first key the id starts with, then the first key it contains, so list a longer id (`gemini-3.5-flash-lite`) before its prefix (`gemini-3.5-flash`) |
| `api` | Per-service pricing in USD per request/resource. Keys starting with `_` are metadata |
| `operation_map` | Maps handler action names to pricing operation keys |
| `url_patterns` | Regex patterns for automatic HTTPX tracking. `count_path` extracts resource count from response JSON |

## PricingService API

Singleton accessed via `get_pricing_service()`.

### LLM Methods

```python
from services.pricing import get_pricing_service

pricing = get_pricing_service()

# Get pricing for a model (partial matching supported)
model_pricing = pricing.get_pricing('anthropic', 'claude-sonnet-5')
# Returns ModelPricing(input_per_mtok=2.00, output_per_mtok=10.00, ...)

# Calculate cost for token usage
cost = pricing.calculate_cost(
    provider='anthropic',
    model='claude-sonnet-5',
    input_tokens=5000,
    output_tokens=1500,
    cache_read_tokens=200
)
# Returns: {
#   'input_cost': 0.01,
#   'output_cost': 0.015,
#   'cache_cost': 0.00004,
#   'reasoning_cost': 0.0,
#   'total_cost': 0.02504
# }
```

### API Methods

```python
# Get price for a specific operation
price = pricing.get_api_price('twitter', 'content_create')  # 0.010

# Map handler action to pricing operation
op = pricing.map_action_to_operation('twitter', 'tweet')  # 'content_create'

# Calculate API cost
cost = pricing.calculate_api_cost('twitter', 'tweet', resource_count=1)
# Returns: {'operation': 'content_create', 'unit_cost': 0.010, 'resource_count': 1, 'total_cost': 0.01}
```

### Config Management

```python
# Get full config for frontend editing
config = pricing.get_config()

# Save updated config (updates last_updated, reloads registry)
pricing.save_config(updated_config)

# Force reload from disk
pricing.reload()
```

## Manual Tracking Pattern

For services that make API calls directly (not through HTTPX), use manual tracking functions.

### Example: Maps Service

```python
# server/nodes/location/_service.py

async def _track_maps_usage(
    node_id: str,
    action: str,
    resource_count: int = 1,
    workflow_id: str = None,
    session_id: str = "default"
) -> Dict[str, float]:
    """Track Google Maps API usage."""
    from services.plugin.deps import get_database

    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost('google_maps', action, resource_count)

    db = get_database()
    await db.save_api_usage_metric({
        'session_id': session_id,
        'node_id': node_id,
        'workflow_id': workflow_id,
        'service': 'google_maps',
        'operation': cost_data.get('operation', action),
        'endpoint': action,
        'resource_count': resource_count,
        'cost': cost_data.get('total_cost', 0.0)
    })

    return cost_data

# Usage inside the maps plugins (server/nodes/location/{gmaps_locations,gmaps_nearby_places,...}):
    # ... API call ...
    await _track_maps_usage(node_id, 'geocode', 1, context.get('workflow_id'), context.get('session_id', 'default'))
```

### Example: Twitter Plugin

The Twitter plugins (`server/nodes/twitter/twitter_send`, `twitter_search`, `twitter_user`) share one tracker defined on `server/nodes/twitter/_base.py`. It takes the raw execution context dict (not separate `workflow_id` / `session_id` args).

```python
# server/nodes/twitter/_base.py

async def track_twitter_usage(node_id, action, resource_count, context):
    # Same pattern as maps
    from services.plugin.deps import get_database
    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost('twitter', action, resource_count)
    # ... save to database (reads session_id / workflow_id from `context`) ...

# Usage (ctx_raw is the plugin's raw NodeContext dict):
await track_twitter_usage(node_id, 'tweet', 1, ctx_raw)
await track_twitter_usage(node_id, 'search', len(tweets), ctx_raw)
```

### Example: Google Workspace Plugins

The 6 Google service plugins (`server/nodes/google/{gmail,calendar,drive,sheets,tasks,contacts}`) plus `gmail_receive` call `track_google_usage()` on `server/nodes/google/_base.py`. Google Workspace APIs are free within rate limits, so this records resource counts at `cost=$0` for analytics; `service` must be the pricing key (`gmail`, `google_calendar`, `google_drive`, `google_sheets`, `google_tasks`, `google_contacts`), not the folder name.

```python
# server/nodes/google/_base.py

async def track_google_usage(service, node_id, action, resource_count, context):
    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost(service, action, resource_count)
    # ... save to database ...
```

### Example: TikHub Plugin (flat per-request)

TikHub bills roughly $0.001 per successful request and does not charge non-2xx responses, so `server/nodes/scraper/tikhub_action/_sdk.py::track_tikhub_usage(ctx, action, endpoint_path)` records one `resource_count=1` metric only after a 2xx and never for the local `list_endpoints` operation. It takes the `NodeContext` (not the raw dict), reads `session_id` / `workflow_id` from it, wraps everything in `try/except` + `logger.warning` so a metrics failure never fails a call that already cost money, and returns the USD figure for the node's `cost_usd` output key. `pricing.json` carries `api.tikhub` (`request` 0.001, `meta` 0.0) and `operation_map.tikhub` (`call` / `fetch_url` -> `request`, `account` -> `meta`); the credential entry sets `"usage_service": "tikhub"` so the spend shows in the Credentials modal usage table. See [tikhub_service.md](./tikhub_service.md).

### Example: Search Plugins (declarative cost)

The search plugins (`server/nodes/search/{brave_search,serper_search,perplexity_search}`) no longer call a `_track_search_usage()` helper. Each declares its cost inline on the `@Operation` decorator instead:

```python
# server/nodes/search/brave_search/__init__.py

@Operation("search", cost={"service": "brave_search", "action": "web_search", "count": 1})
async def search(self, ctx, params): ...
```

The `cost=` metadata is attached to the operation's `OperationSpec` (`server/services/plugin/operation.py`); `duckduckgo_search` is free and carries no cost block.

## Automatic HTTPX Tracking

For services using `httpx.AsyncClient`, use the tracked client for transparent tracking.

### Usage

```python
from services.tracked_http import get_tracked_client, set_tracking_context

# Set context before making requests
set_tracking_context(node_id="twitter-1", session_id="user-123")

# Use tracked client - tracking happens automatically
client = get_tracked_client()
response = await client.post("https://api.twitter.com/2/tweets", json={...})
# Automatically tracked via response hook!
```

### How It Works

1. **Context Variables**: `set_tracking_context()` sets node_id, session_id, workflow_id in `contextvars`
2. **URL Matching**: Response hook matches URL against `url_patterns` in pricing.json
3. **Resource Counting**: `count_path` extracts resource count from response JSON (e.g., `data.length`)
4. **Fire-and-Forget**: `_save_metric()` runs as `asyncio.create_task()` to avoid blocking

### URL Pattern Format

```json
{
  "twitter": {
    "base": "https?://api\\.(twitter|x)\\.com",
    "actions": {
      "/2/tweets$": {"action": "content_create", "method": "POST"},
      "/2/tweets/search": {
        "action": "posts_read",
        "method": "GET",
        "count_path": "data.length"
      }
    }
  }
}
```

- `base`: Regex to match domain
- `actions`: Dict of endpoint patterns to action config
- `method`: HTTP method filter (`*` for any)
- `count_path`: Dot-notation path to extract count from response (e.g., `data.length`, `results.count`)

## Database Storage

### APIUsageMetric

For third-party API services (Twitter, Google Maps).

```python
class APIUsageMetric(SQLModel, table=True):
    __tablename__ = "api_usage_metrics"

    id: int
    timestamp: datetime
    session_id: str        # Index for aggregation
    node_id: str
    workflow_id: Optional[str]
    service: str           # 'twitter', 'google_maps'
    operation: str         # 'content_create', 'geocode'
    endpoint: str          # Handler action name
    resource_count: int    # Number of resources
    cost: float            # USD cost
```

### TokenUsageMetric

For LLM token usage (see memory_compaction.md for details).

```python
class TokenUsageMetric(SQLModel, table=True):
    __tablename__ = "token_usage_metrics"

    session_id: str
    node_id: str
    provider: str          # 'openai', 'anthropic'
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    input_cost: float      # USD
    output_cost: float
    total_cost: float
```

### Database Methods

```python
# Save API usage
await db.save_api_usage_metric({
    'session_id': 'default',
    'node_id': 'twitter-1',
    'service': 'twitter',
    'operation': 'content_create',
    'endpoint': 'tweet',
    'resource_count': 1,
    'cost': 0.01
})

# Get usage summary (aggregated by service)
summary = await db.get_api_usage_summary(service='twitter')
# Returns: [{'service': 'twitter', 'total_resources': 50, 'total_cost': 0.5, 'execution_count': 45}]
```

## Frontend Display

The credentials modal displays usage statistics through two section components, both wired via
`credentials/useCredentialPanel.ts`:

```typescript
// client/src/components/credentials/sections/ApiUsageSection.tsx   -> per-service API cost
// client/src/components/credentials/sections/LlmUsageSection.tsx   -> per-provider token cost

const { getAPIUsageSummary, getProviderUsageSummary } = useApiKeys();
```

**Both render precomputed costs, never the pricing config.** `get_api_usage_summary` is a
`SUM(APIUsageMetric.cost)` GROUP BY, and the `cost` column was calculated at write time by
`PricingService`. Nothing on the client reads or writes `pricing.json`.

There is no UI for editing pricing. `PricingConfigModal` + `usePricing` existed but were never
mounted anywhere and were deleted; the `get_pricing_config` / `save_pricing_config` WS handlers
remain registered with no client caller. Editing `pricing.json` means editing the file. (The third
handler in that module, `get_api_usage_summary`, IS live — `useApiKeys` calls it.)

### Display Format

- Collapsible panel with service icon and total cost tag
- Table with operation breakdown (operation name, count, unit cost, total cost)
- Refresh button to reload data
- Loading state while fetching

## Adding New Services

### 1. Add Pricing to pricing.json

```json
{
  "api": {
    "new_service": {
      "_description": "New Service API pricing",
      "read_operation": 0.001,
      "write_operation": 0.005
    }
  },
  "operation_map": {
    "new_service": {
      "get_data": "read_operation",
      "create_item": "write_operation"
    }
  }
}
```

### 2. Add Manual Tracking (if not using HTTPX)

```python
# server/nodes/<group>/<plugin>/_base.py (or the plugin __init__.py)

async def track_new_service_usage(node_id, action, count=1, workflow_id=None, session_id="default"):
    from services.plugin.deps import get_database
    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost('new_service', action, count)

    db = get_database()
    await db.save_api_usage_metric({
        'session_id': session_id,
        'node_id': node_id,
        'workflow_id': workflow_id,
        'service': 'new_service',
        'operation': cost_data.get('operation', action),
        'endpoint': action,
        'resource_count': count,
        'cost': cost_data.get('total_cost', 0.0)
    })
    return cost_data

# Call inside the plugin's execute_op / operation method:
await track_new_service_usage(node_id, 'get_data', len(results), workflow_id)
```

Alternatively, for a service billed per operation, skip the helper entirely and declare the cost inline via `@Operation("get_data", cost={"service": "new_service", "action": "read_operation", "count": 1})` (the search-plugin pattern).

### 3. Add URL Patterns (if using HTTPX)

```json
{
  "url_patterns": {
    "new_service": {
      "base": "https?://api\\.newservice\\.com",
      "actions": {
        "/v1/items$": {"action": "read_operation", "method": "GET", "count_path": "items.length"},
        "/v1/items$": {"action": "write_operation", "method": "POST"}
      }
    }
  }
}
```

### 4. Frontend Display

Nothing to add. `ApiUsageSection` renders whatever services come back from
`get_api_usage_summary`, so a new service appears as soon as it records its first metric.

## Key Files

| File | Purpose |
|------|---------|
| `server/config/pricing.json` | Pricing configuration (user-editable) |
| `server/services/pricing.py` | PricingService with LLM and API cost calculation |
| `server/services/tracked_http.py` | HTTPX event hooks for automatic tracking |
| `server/nodes/location/_service.py` | Maps manual tracker (`_track_maps_usage`) |
| `server/nodes/twitter/_base.py` | Twitter manual tracker (`track_twitter_usage`) |
| `server/nodes/google/_base.py` | Google Workspace manual tracker (`track_google_usage`, cost $0) |
| `server/nodes/proxy/_usage.py` | Proxy manual tracker (`track_proxy_usage`, per-GB) |
| `server/nodes/search/{brave_search,serper_search,perplexity_search}/__init__.py` | Search plugins — declarative `@Operation(cost={...})` (no `_track_search_usage`) |
| `server/services/plugin/operation.py` | `@Operation` decorator + `OperationSpec.cost` metadata |
| `server/models/database.py` | `APIUsageMetric`, `TokenUsageMetric` models |
| `server/core/database.py` | `save_api_usage_metric()`, `get_api_usage_summary()` |
| `client/src/components/credentials/sections/ApiUsageSection.tsx` | Per-service API usage + cost display |
| `client/src/components/credentials/sections/LlmUsageSection.tsx` | Per-provider token usage + cost display |
| `client/src/hooks/useApiKeys.ts` | `getAPIUsageSummary` / `getProviderUsageSummary` |
