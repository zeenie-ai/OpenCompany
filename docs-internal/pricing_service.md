# API Cost Tracking and Pricing Service

## Overview

The pricing service provides centralized cost tracking for both LLM tokens and external API services (Twitter/X, Google Maps, TikHub, treg). It supports:

- **LLM Token Costs**: Per-model pricing with input/output/cache/reasoning token breakdown
- **API Service Costs**: Per-request/resource pricing for third-party APIs (Twitter/X, Google Maps, TikHub, Search APIs), and the provider's own reported charge where it gives one (treg)
- **Tracking**: the usage package (`server/services/usage/`) books every call. A plugin calls it after a paid call returns, and every model call is booked through it

## Architecture

Prices and usage are two services. `PricingService` knows what a call costs.
The usage package records each call, asks `PricingService` for its price, and
owns every read and write of the two usage tables. It imports no chat,
employee, compaction or Temporal code.

```
┌───────────────────────────┐        ┌───────────────────────────┐
│ Plugins (paid API calls)  │        │ Model calls               │
│ track_*_usage() helpers   │        │ agent step on Temporal    │
│ twitter, google, maps,    │        │ CompactionService.track() │
│ tikhub, treg, speech, ... │        │ summarizer, setup call    │
└─────────────┬─────────────┘        └─────────────┬─────────────┘
              │ record_api_usage()                 │ record_llm_usage()
              └─────────────────┬──────────────────┘
                                ▼
┌────────────────────────────────────────────────────────────────┐
│   Usage package: server/services/usage/                        │
│   recording.py   prices a call and writes its row; never raises│
│   summaries.py   what was recorded, added up                   │
│   handlers.py    the WebSocket commands that read it           │
└───────────────┬─────────────────────────────────┬──────────────┘
                │ asks the price                  │ writes and reads
                ▼                                 ▼
┌───────────────────────────────┐  ┌─────────────────────────────┐
│ PricingService                │  │ Database                    │
│ server/services/pricing.py    │  │ api_usage_metrics           │
│ reads config/pricing.json     │  │ token_usage_metrics         │
└───────────────────────────────┘  └─────────────────────────────┘
```

## Configuration: pricing.json

Located at `server/config/pricing.json`. User-editable; picked up via `PricingService.reload()` or `save_config()`; there is no filesystem watcher.

Numeric examples below illustrate the configuration/accounting shape. The
local configuration and provider-reported usage determine recorded costs;
these examples are not independently verified current vendor quotes.

### Structure

```json
{
  "version": "2026.11",
  "last_updated": "2026-11-19",

  "llm": {
    "openai": {
      "gpt-6.1-sol": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
      "gpt-6-luna": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
      "_default": {"input": 2.50, "output": 15.00}
    },
    "anthropic": {
      "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
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
  }
}
```

### Sections

| Section | Purpose |
|---------|---------|
| `llm` | Per-model pricing in USD per million tokens (MTok). Supports `input`, `output`, `cache_read`, `cache_write`, `reasoning`. `input`, `output` and `cache_read` follow the OpenRouter snapshot (`config/model_registry.json`) for every model it lists; the snapshot has carried cache prices since 2026-11-19, and until then `cache_read` was each vendor's published cache discount applied to the input price. A row without `cache_read` bills cached tokens at its input price. `cache_write` is listed only where the provider's usage reports cache writes (Anthropic). A lookup tries the exact id, then the first key the id starts with, then the first key it contains, so list a longer id (`gemini-3.5-flash-lite`) before its prefix (`gemini-3.5-flash`). A provider block with `"_from_registry": true` (`openrouter`, `openai_compatible`) then prices its models from the model registry before its `_default`: for OpenRouter that is the catalogue's own `prompt`, `completion`, `input_cache_read` and `input_cache_write` prices, and for a named endpoint the price recorded when it was saved. A free model (0/0) is a real price; a router's negative placeholder and an unlisted id take `_default` |
| `api` | Per-service pricing in USD per request/resource. Keys starting with `_` are metadata |
| `operation_map` | Maps handler action names to pricing operation keys |

## PricingService API

Singleton accessed via `get_pricing_service()`.

### LLM Methods

```python
from services.pricing import get_pricing_service

pricing = get_pricing_service()

# Get pricing for a model (partial matching supported)
model_pricing = pricing.get_pricing('anthropic', 'claude-sonnet-5')
# Returns ModelPricing(input_per_mtok=2.00, output_per_mtok=10.00, ...)

# Calculate cost for token usage (Anthropic-shaped: input excludes the cache)
cost = pricing.calculate_cost(
    provider='anthropic',
    model='claude-sonnet-5-5',
    input_tokens=5000,
    output_tokens=1500,
    cache_read_tokens=20000,
    cache_creation_tokens=1000,
    total_tokens=27500,
)
# Returns: {
#   'input_cost': 0.01,       # 5,000 uncached at $2.00
#   'output_cost': 0.015,     # 1,500 at $10.00
#   'cache_cost': 0.0045,     # 20,000 read at $0.10 + 1,000 written at $2.50
#   'reasoning_cost': 0.0,
#   'total_cost': 0.0295
# }
```

`calculate_cost` prices the uncached input from `total_tokens`, the whole
prompt (cache reads and writes included) plus the output, which every provider
fills the same way: uncached input = `total_tokens` minus output, cache reads
and cache writes, never below 0. `input_tokens` cannot be used for this, since
it counts cached tokens on OpenAI-shaped usage (OpenRouter included) but not on
Anthropic's; the same call in either shape costs the same. Cache reads and
writes take the model's `cache_read` / `cache_write` price, or its input price
when it lists none. Without `total_tokens`, `input_tokens` is taken as the
uncached input. Every caller passes it: `record_llm_usage` (the agent step,
the setup call, `CompactionService.track()` and the compaction summarizer) and
the CLI agents.

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

## Tracking Pattern

A plugin that makes a paid API call books it after the call returns, with one
call to `record_api_usage` in the usage package
(`server/services/usage/recording.py`):

```python
from services.usage import record_api_usage

cost = await record_api_usage(
    "twitter",   # service: the pricing.json key
    "tweet",     # action: operation_map turns it into a priced operation
    1,           # count: how many units were billed
    node_id=node_id,
    session_id=context.get("session_id", "default"),
    workflow_id=context.get("workflow_id"),
)
```

It prices `count` units of the action, writes one `api_usage_metrics` row and
returns the USD cost. Two optional arguments cover the plugins that need more:

- `endpoint` says what was called when the action alone does not (TikHub's
  REST path, treg's tool id). It is the action unless given.
- `cost` is the provider's own charge, booked as given with no price lookup
  (treg's reported charge, the proxy's per-GB cost).

**It never raises.** The call it books already happened and was paid for, and
callers act on an exception: the proxy node would send a paid request again, a
retried tweet could post twice, and a mail trigger would drop the email it
just read. A failed price or write is logged as a warning, and the cost
returned is 0.0 when the price could not be read.

Each plugin keeps a small helper of its own, so its operations do not repeat
the service name or how the plugin reads its context. The helper imports the
recorder inside its body, as the examples below do.

### Example: Maps Service

```python
# server/nodes/location/_service.py

async def _track_maps_usage(node_id, action, resource_count=1, workflow_id=None, session_id="default") -> float:
    from services.usage import record_api_usage

    return await record_api_usage(
        "google_maps",
        action,
        resource_count,
        node_id=node_id,
        session_id=session_id,
        workflow_id=workflow_id,
    )

# Usage inside the maps plugins (server/nodes/location/{gmaps_locations,gmaps_nearby_places,...}):
    # ... API call ...
    await _track_maps_usage(node_id, 'geocode', 1, context.get('workflow_id'), context.get('session_id', 'default'))
```

### Example: Twitter Plugin

The Twitter plugins (`server/nodes/twitter/twitter_send`, `twitter_search`, `twitter_user`) share one tracker defined on `server/nodes/twitter/_base.py`. It takes the raw execution context dict (not separate `workflow_id` / `session_id` args).

```python
# server/nodes/twitter/_base.py

async def track_twitter_usage(node_id, action, resource_count, context) -> float:
    from services.usage import record_api_usage

    return await record_api_usage(
        "twitter",
        action,
        resource_count,
        node_id=node_id,
        session_id=context.get("session_id", "default"),
        workflow_id=context.get("workflow_id"),
    )

# Usage (ctx_raw is the plugin's raw NodeContext dict):
await track_twitter_usage(node_id, 'tweet', 1, ctx_raw)
await track_twitter_usage(node_id, 'search', len(tweets), ctx_raw)
```

### Example: Google Workspace Plugins

The 6 Google service plugins (`server/nodes/google/{gmail,calendar,drive,sheets,tasks,contacts}`) plus `gmail_receive` call `track_google_usage()` on `server/nodes/google/_base.py`, which books through `record_api_usage` under the service it is given. Google Workspace APIs are free within rate limits, so this records resource counts at `cost=$0` for analytics; `service` must be the pricing key (`gmail`, `google_calendar`, `google_drive`, `google_sheets`, `google_tasks`, `google_contacts`), not the folder name.

```python
# server/nodes/google/_base.py

async def track_google_usage(service, node_id, action, resource_count, context) -> float:
    ...  # the Twitter helper with `service` in place of "twitter"
```

### Example: TikHub Plugin (flat per-request)

TikHub prices each endpoint (most cost $0.001 a successful request, the `instagram_v2` family $0.002 and `instagram_v3` $0.008) and does not charge non-2xx responses. OpenCompany records a flat $0.001 for each billed request, a deliberate floor rather than TikHub's own figure: `server/nodes/scraper/tikhub_action/_sdk.py::track_tikhub_usage(ctx, action, endpoint_path)` records one `resource_count=1` metric only after a 2xx and never for the `list_endpoints` and `describe` operations, which spend nothing. It takes the `NodeContext` (not the raw dict), reads `session_id` / `workflow_id` from it, books through `record_api_usage` with `endpoint` set to the REST path (the recorder never raises, so a metrics failure never fails a call that already cost money), and returns the USD figure for the node's `cost_usd` output key. `pricing.json` carries `api.tikhub` (`request` 0.001, `meta` 0.0) and `operation_map.tikhub` (`call` / `fetch_url` -> `request`, `account` -> `meta`); the credential entry sets `"usage_services": ["tikhub"]` so the spend shows in the Credentials modal usage table. See [tikhub_service.md](./tikhub_service.md).

### Example: treg Plugin (the provider's own charge)

treg reports what it charged for every call in the CLI's `--json` envelope (`_treg.charged_micro`, integer micro-USD), so OpenCompany records that figure instead of a price of its own, and `pricing.json` has no treg entry. `_record_cost(ctx, tool_id, micro, operation)` in `server/nodes/treg/treg_action.py` books one `resource_count=1` row through `record_api_usage(..., cost=<the charge>)`, with `service: "treg"`, `endpoint` the catalog tool id and `cost` the charge in USD. It runs before the answer is saved, and also for a failed call that treg charged. A generation tool's submission reports `reserved_micro` instead, an amount treg holds until the task ends (settled on success, refunded on failure); it is recorded with `operation: "task"`. No amount (a call served on the team's own provider key, which treg does not bill) and a zero amount write nothing, and `search`, `describe` and `balance` are free. The recorder never raises, and the credential entry sets `"usage_services": ["treg"]`. See [treg_service.md](./treg_service.md).

### `@Operation(cost=...)` records nothing

Many operations across the plugins declare a `cost=` block on their `@Operation` decorator:

```python
# server/nodes/search/brave_search/__init__.py

@Operation("search", cost={"service": "brave_search", "action": "web_search", "count": 1})
async def search(self, ctx, params): ...
```

The decorator copies the block onto the operation's `OperationSpec` (`server/services/plugin/operation.py`), and nothing reads it: no framework code prices it or writes a row. A plugin's calls are booked only if the plugin calls `record_api_usage` itself, as the examples above do.

So the search plugins (`server/nodes/search/{brave_search,serper_search,perplexity_search}`) book nothing today, although `pricing.json` prices them; the `_track_search_usage()` helper they once called is gone. The same holds for every other plugin whose only cost code is the `cost=` block (WhatsApp Business, Discord, Telegram, email, the CLI nodes). Reading `cost=` at one point in the framework, or removing it, is an open follow-up.

## Database Storage

### APIUsageMetric

For third-party API services (Twitter, Google Maps). A row booked while a
plugin works for a chat run carries that run's id in `chat_run_id`, as a
`TokenUsageMetric` row does, so a chat run's cost is the sum of its rows in
both tables. `record_api_usage` takes the run from the log context, which
`BaseNode.execute` binds for every plugin operation; no plugin passes it.

```python
class APIUsageMetric(SQLModel, table=True):
    __tablename__ = "api_usage_metrics"

    id: int
    timestamp: datetime
    session_id: str        # Index for aggregation
    node_id: str
    workflow_id: Optional[str]
    chat_run_id: Optional[str]  # the chat run the call worked for (indexed)
    service: str           # 'twitter', 'google_maps'
    operation: str         # 'content_create', 'geocode'
    endpoint: str          # Handler action name
    resource_count: int    # Number of resources
    cost: float            # USD cost
```

### TokenUsageMetric

For LLM token usage (see memory_compaction.md for details). Rows come from the
in-process agent path (`CompactionService.track`), the compaction summarizer,
the hire-screen setup call, and one row per LLM step of every agent on
Temporal. All four book through one recorder, `record_llm_usage` in
`server/services/usage/recording.py`:

```python
from services.usage import record_llm_usage

cost = await record_llm_usage(
    provider="anthropic",
    model="claude-sonnet-5-5",
    usage={"input_tokens": 5000, "output_tokens": 1500, "total_tokens": 6500},
    session_id=workflow_id,
    node_id=node_id,
)
```

It prices the call, writes the row and returns the cost breakdown. It never
raises, because the call it books is already paid for: a failed price returns
a zero cost and writes nothing, and a failed write still returns the price.
A row booked while an agent works for a chat run carries that run's id in
`chat_run_id`.

```python
class TokenUsageMetric(SQLModel, table=True):
    __tablename__ = "token_usage_metrics"

    session_id: str
    node_id: str
    chat_run_id: Optional[str]  # the chat run the call worked for
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

### Writing and reading rows

Rows are written only by the two recorders in
`server/services/usage/recording.py`: `record_api_usage` (see Tracking
Pattern) and `record_llm_usage` (see TokenUsageMetric). The summaries live in
the same package, which owns the reads of both usage tables too:

```python
from services.usage import api_usage_summary, provider_usage_summary

# Paid API calls, added up by service and operation
summary = await api_usage_summary(database, service='twitter')
# Returns: [{'service': 'twitter', 'total_resources': 50, 'total_cost': 0.5, 'execution_count': 45, 'operations': [...]}]

# Model calls, added up by provider and model
providers = await provider_usage_summary(database)
```

`server/services/usage/handlers.py` serves both as the WebSocket commands
`get_api_usage_summary` and `get_provider_usage_summary`.

## Frontend Display

The credentials modal displays usage statistics through two section components, both wired via
`credentials/useCredentialPanel.ts`:

```typescript
// client/src/components/credentials/sections/ApiUsageSection.tsx   -> per-service API cost
// client/src/components/credentials/sections/LlmUsageSection.tsx   -> per-provider token cost

const { getAPIUsageSummary, getProviderUsageSummary } = useApiKeys();
```

A connector's card shows the API section when its catalogue entry
(`server/config/credential_providers.json`) lists `usage_services`, the
service names its nodes book under. `ApiUsageSection` reads every service's
summary and adds up the listed ones. Most connectors list one service; Google
Workspace lists six (`gmail`, `google_calendar`, `google_drive`,
`google_sheets`, `google_tasks`, `google_contacts`), because each of its nodes
books under its own pricing key. A test
(`tests/test_credential_catalogue_consumer_fields.py`) fails when a connector
lists a name nothing books under.

**Both render precomputed costs, never the pricing config.** `get_api_usage_summary` is a
`SUM(APIUsageMetric.cost)` GROUP BY, and the `cost` column was calculated at write time by
`PricingService`. Nothing on the client reads or writes `pricing.json`.

There is no UI for editing pricing. `PricingConfigModal` + `usePricing` existed but were never
mounted anywhere and were deleted; the `get_pricing_config` / `save_pricing_config` WS handlers
(`server/services/pricing_handlers.py`) remain registered with no client caller. Editing
`pricing.json` means editing the file.

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

### 2. Add a tracker

```python
# server/nodes/<group>/<plugin>/_base.py (or the plugin __init__.py)

async def track_new_service_usage(node_id, action, count=1, workflow_id=None, session_id="default") -> float:
    from services.usage import record_api_usage

    return await record_api_usage(
        "new_service",
        action,
        count,
        node_id=node_id,
        session_id=session_id,
        workflow_id=workflow_id,
    )

# Call inside the plugin's operation method, after the paid call returned:
await track_new_service_usage(node_id, 'get_data', len(results), workflow_id)
```

Declaring `cost={...}` on the `@Operation` does not replace this helper: nothing reads that block (see "`@Operation(cost=...)` records nothing" above).

### 3. Frontend Display

Nothing to add. `ApiUsageSection` renders whatever services come back from
`get_api_usage_summary`, so a new service appears as soon as it records its first metric.

## Key Files

| File | Purpose |
|------|---------|
| `server/config/pricing.json` | Pricing configuration (user-editable) |
| `server/services/pricing.py` | PricingService with LLM and API cost calculation |
| `server/nodes/location/_service.py` | Maps tracker (`_track_maps_usage`) |
| `server/nodes/twitter/_base.py` | Twitter tracker (`track_twitter_usage`) |
| `server/nodes/google/_base.py` | Google Workspace tracker (`track_google_usage`, cost $0) |
| `server/nodes/proxy/_usage.py` | Proxy tracker (`track_proxy_usage`, per-GB) |
| `server/nodes/search/{brave_search,serper_search,perplexity_search}/__init__.py` | Search plugins: an `@Operation(cost={...})` block and no recorder call, so nothing is booked |
| `server/services/plugin/operation.py` | `@Operation` decorator + `OperationSpec.cost`, metadata nothing reads |
| `server/models/database.py` | `APIUsageMetric`, `TokenUsageMetric` models |
| `server/services/usage/recording.py` | `record_api_usage()` and `record_llm_usage()`: one usage row per call; neither raises |
| `server/services/usage/summaries.py` | `api_usage_summary()`, `provider_usage_summary()` |
| `server/services/usage/handlers.py` | The `get_api_usage_summary` / `get_provider_usage_summary` commands |
| `client/src/components/credentials/sections/ApiUsageSection.tsx` | Per-service API usage + cost display |
| `client/src/components/credentials/sections/LlmUsageSection.tsx` | Per-provider token usage + cost display |
| `client/src/hooks/useApiKeys.ts` | `getAPIUsageSummary` / `getProviderUsageSummary` |
