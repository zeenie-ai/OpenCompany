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

### Example: Search Plugins (one call in the operation)

The three search plugins (`server/nodes/search/{serper_search,brave_search,perplexity_search}`) have no helper: each `search` operation calls `record_api_usage` once, after the response came back, so a failed request books nothing.

```python
# server/nodes/search/serper_search/__init__.py, inside search()

from services.usage import record_api_usage

await record_api_usage("serper", "web_search", node_id=ctx.node_id, session_id=ctx.session_id, workflow_id=ctx.workflow_id)
```

Serper and Brave are booked at the price list's per-query figure (`web_search`). Perplexity is booked at the charge its response reports in `usage.cost.total_cost` (the request fee plus the tokens) when there is one, passed as `cost=`; a response without it is booked at the price list's request fee (`sonar_search`), which leaves the tokens out.

### `@Operation(cost=...)` records nothing

Many operations across the plugins declare a `cost=` block on their `@Operation` decorator:

```python
# server/nodes/search/brave_search/__init__.py

@Operation("search", cost={"service": "brave_search", "action": "web_search", "count": 1})
async def search(self, ctx, params): ...
```

The decorator copies the block onto the operation's `OperationSpec` (`server/services/plugin/operation.py`), and nothing reads it: no framework code prices it or writes a row. A plugin's calls are booked only if the plugin calls `record_api_usage` itself, as the examples above do.

So every plugin whose only cost code is the `cost=` block books nothing (WhatsApp Business, Discord, Telegram, email, the CLI nodes). The search plugins were in that group until they got a recorder call of their own (the example above); their `cost=` blocks are still there and still unread. Reading `cost=` at one point in the framework, or removing it, is an open follow-up.

## Database Storage

### APIUsageMetric

For third-party API services (Twitter, Google Maps). A row booked while a
plugin works for a chat run carries that run's id in `chat_run_id`, as a
`TokenUsageMetric` row does, so a chat run's cost is the sum of its rows in
both tables. `record_api_usage` takes the run from the log context, which
`BaseNode.execute` binds for every plugin operation; no plugin passes it. A row
booked while a plugin works for an employee job carries the job's id in
`employee_job_id`, read from the same log context.

```python
class APIUsageMetric(SQLModel, table=True):
    __tablename__ = "api_usage_metrics"

    id: int
    timestamp: datetime
    session_id: str        # Index for aggregation
    node_id: str
    workflow_id: Optional[str]
    chat_run_id: Optional[str]  # the chat run the call worked for (indexed)
    employee_job_id: Optional[str]  # the employee job the call worked for (indexed)
    service: str           # 'twitter', 'google_maps'
    operation: str         # 'content_create', 'geocode'
    endpoint: str          # Handler action name
    resource_count: float  # Number of units: requests, results, characters, seconds or minutes of audio
    cost: float            # USD cost
```

`resource_count` is a float because speech is counted in seconds or minutes
(12.5 seconds is one row with a count of 12.5).

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

A provider that reports its own charge has it passed as `cost`, and that
figure is the `total_cost` booked and returned. Only OpenRouter reports one
(`LLMResponse.reported_cost`, read from `usage.cost`; see
[native_llm_sdk.md](native_llm_sdk.md)). The model step, the summarizer and
the hire screen's setup call pass it; the in-process agent path
(`CompactionService.track`) books a whole loop at once and passes none, so it
stays a priced estimate. The row's `input_cost`, `output_cost` and
`cache_cost` are still the price service's estimates beside a reported total,
so they need not add up to it. Every figure the app shows (a reply's cost, the
cost panel, the monthly budget, the Credentials usage totals) is a sum of
`total_cost`.
A row booked while an agent works for a chat run carries that run's id in
`chat_run_id`. A row booked while an agent works for an employee job carries
the job's id in `employee_job_id`: the recorder reads it from the log context,
where the model step and the summarizer bind it, so no caller passes it.

```python
class TokenUsageMetric(SQLModel, table=True):
    __tablename__ = "token_usage_metrics"

    session_id: str
    node_id: str
    chat_run_id: Optional[str]  # the chat run the call worked for
    employee_job_id: Optional[str]  # the employee job the call worked for
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

### What a reply and an employee cost

A cost is model calls plus paid tool calls, the rows of both tables:

```python
from services.usage import chat_run_costs, month_start, workflow_costs

# USD per chat run of one workflow
runs = await chat_run_costs(database, workflow_id)
# Returns: {'r_3f2a...': 0.412, 'r_91bc...': 0.058}

# USD and model tokens per workflow since an instant, highest cost first
since = month_start(datetime.now(timezone.utc), settings.get('profile_timezone'))
costs = await workflow_costs(database, since)
# Returns: [{'workflow_id': '2', 'name': 'Ava', 'total_cost': 3.2, 'total_tokens': 1840210},
#           {'workflow_id': None, 'name': None, 'total_cost': 0.04, 'total_tokens': 9120}]
```

- A chat run with no booked call is absent from `chat_run_costs`.
- A run's cost includes the employee jobs its reply started. A job records the
  run that admitted it (`EmployeeJob.chat_run_id`), and a usage row that carries
  the job (`employee_job_id`) and no chat run of its own is added to that run.
  So the figure under "I've given this to the team" grows with the team's work.
  A job a schedule or another trigger started adds to no run; its cost shows in
  the monthly figure only.
- `chat_run_cost`, which the spend limit reads, counts a run's own rows only,
  so that limit covers the agent that answers in chat and not the team.
- `workflow_costs` ends with one unnamed row (`workflow_id` and `name` both
  `None`) for calls booked with no workflow, such as a hire screen's setup
  call, and for a workflow deleted since.
- A row's `total_tokens` adds up its model calls' `total_tokens`: every token
  the model read or wrote, cached ones included, as the provider reports
  them. A paid tool call adds cost and no tokens.
- `month_start` is midnight on the 1st in the owner's `profile_timezone`
  (UTC when unset or unknown), the month Settings > Billing counts tasks over.
- Both read functions let an error propagate, so a screen shows that the
  figure is unavailable instead of a zero.

The commands are `get_chat_run_costs {workflow_id}`, answering
`{runs: {run_id: usd}}`, and `get_workflow_costs {}`, answering
`{since, total_cost, workflows: [{workflow_id, name, total_cost, total_tokens}]}`
with `since` as YYYY-MM-DD.

Not counted, because nothing books them: Claude Code and Codex agents, the
RLM agent, standalone chat-model nodes, in-process agents without a memory
session, and every plugin whose only cost code is an `@Operation(cost=...)`
block.

### The spend limit of a reply

`server/services/usage/limit.py`. A chat run (one reply) stops when its cost
reaches the limit:

- The limit is the owner's `UserSettings.chat_run_spend_limit_usd` when above
  0, else env `CHAT_RUN_SPEND_LIMIT_USD` (`resolve_spend_limit`). 0, the
  default, means no limit.
- `spend_limit_notice(chat_run_id)` returns the line the agent says when the
  run's cost (`chat_run_cost`) has reached the limit, else `None`.
- The agent's model step (`agent.execute_llm_step`) asks before each model
  call. At the limit it makes no call and answers with the notice, so the run
  ends as a normal reply and the owner's next message starts a new run. See
  [Chat Protocol](./chat_protocol.md), "Spend limit".
- The check runs between turns, so a run can pass the limit by the cost of
  one turn.
- This limit covers the agent that answers in chat. A managed employee job is
  not asked, and `chat_run_cost` counts the run's own rows only; team and
  background work are limited by the monthly budget below.
- A limit that cannot be read answers `None` and logs a warning, so a failed
  check never ends the owner's reply.

### The monthly budget of an employee

The same module. An employee (one workflow) is paused when everything booked
for it this month reaches the budget: its replies, its team's jobs, and its
scheduled and triggered runs.

- The budget is the owner's `UserSettings.employee_monthly_budget_usd` when
  above 0, else env `EMPLOYEE_MONTHLY_BUDGET_USD`. 0, the default, means no
  budget. One amount applies to every employee.
- `monthly_budget_reached(workflow_id)` returns the budget when the workflow's
  cost since the 1st of the month has reached it, else `None`. The month starts
  in the owner's `profile_timezone` (`month_start`), the month the cost panel
  shows, and the sum is `workflow_cost_since`.
- The agent's model step asks before each model call for a step that has a
  workflow. At the budget it calls
  `services/deployment/handlers.pause_generation_for_budget`, which pauses the
  running generation the way the Workspace's Stop does, with pause reason
  `budget`. Home then shows the employee as needing attention with the line
  "Reached this month's budget of $20.00. Raise it in Settings > Billing, then
  resume."
- A reply the agent is giving the owner ends there with one line saying so,
  and no model call is made. Any other step (a team job, a scheduled run)
  makes its call, and its workflow holds at its next step boundary, as it does
  for any pause. So an employee passes its budget by up to one model call for
  each agent running at that moment.
- Resume is refused with `monthly_budget_reached` while the workflow is still
  over. Raising the amount, setting it to 0, or a new month lets it through,
  and the paused work carries on from where it was.
- A manual canvas run has no running generation, so it is never paused. A
  workflow with no agent step is not checked, because the check runs in the
  model step. A budget that cannot be read lets the step run.

The default is written in three places (`.env.template`, `Settings`,
`UserSettings`); a test keeps them equal.

## Frontend Display

Costs show in three places: the credentials modal's usage sections, the cost
panel, and the figure under each chat reply.

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

### The cost panel

`client/src/components/ui/CostPanel.tsx` is one component, mounted unchanged
in Home's Settings > Billing and in Dev's Settings panel, so both modes show
the same figures and the same limits.

- **This month.** `useWorkflowCostsQuery()` (`client/src/hooks/useUsageQueries.ts`)
  sends `get_workflow_costs` each time the panel opens, because calls are
  booked while it is closed. The panel shows the total, "Since {Mon D}" (the
  1st of the month in the owner's time zone), one row per employee, highest
  first, and "Other" for the row with no workflow. An employee's row opens to
  their usage (below); "Other" has nothing to open. A read that fails shows a
  dash, never a zero.
- **Spend limit per reply.** A number field and Save limit, which sends only
  `chat_run_spend_limit_usd` through `save_user_settings`. An empty box reads
  as 0, which means no limit, and a value below 0 is refused in the panel. A
  save that fails says so and keeps what was typed. The limit itself is
  described under "The spend limit of a reply" above.
- **Monthly budget per employee.** The same field (`LimitField`, mounted once
  per limit), with Save budget, which sends only
  `employee_monthly_budget_usd`. 0 means no budget. See "The monthly budget of
  an employee" above.
- The panel says what it leaves out: Claude Code and Codex agents.

An employee paused at its budget shows the reason on its page in Home
(`features/home/employee/EmployeeChat.tsx`, the same alert a failure pause
shows), and a Resume the server refuses with `monthly_budget_reached` says to
raise the budget (`CONTROL_ERRORS` in `useEmployeeControl.ts`). Dev's toolbar
shows the refusal's code, as it does for its other control refusals.

### One employee's usage

`client/src/components/ui/EmployeeUsage.tsx` shows one employee's month: the
cost, the model tokens and the tasks finished, with "Since {Mon D}". One
component, in three places:

- **Usage**, `client/src/components/ui/UsageButton.tsx`: the same button in
  Home's header on an employee's page and in the editor's top toolbar for the
  open workflow (`TopToolbar`'s `workflowId`), each in its bar's shape, as the
  theme button is. It opens a popover holding `EmployeeUsage`; the popover is
  modal, because the editor's canvas stops the press that would otherwise
  close it.
- An employee's row in the cost panel, which opens to it.

The cost and tokens are that employee's row of `get_workflow_costs`, the
query the cost panel reads, so the figures always agree; an employee with no
row had nothing booked and shows $0.00 and 0 tokens. Tokens are the model
calls' `total_tokens`, cached ones included. The tasks are
`get_employee_usage {workflow_id}`, that employee's successful runs since the
1st (see [Normal Mode](./normal_mode.md#done-today)). Each figure that can't
be read shows a dash. Both queries are read again whenever the usage opens,
and Home's employee broadcasts read them again while it is open
(`invalidateUsage`, once per burst), since a finished run, a rename and a
removal change them.

Home's team list shows each employee's cost this month beside their role, from
the same query, and leaves it out while it can't be read.

### The cost under a reply

The shared chat shows what each reply cost beside its time
(`client/src/features/chat/turns/ReplyActions.tsx`), from
`useChatRunCostsQuery(workflowId)`, which sends `get_chat_run_costs`. The
figure includes the work the reply handed to the employee's team, and the hook
reads it again on `agent.instance.updated` for its workflow, so it keeps up
while the team works. See [Chat Protocol](./chat_protocol.md).

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
| `server/nodes/proxy/_usage.py` | Proxy tracker (`track_proxy_usage`): books the cost `ProxyService.request_cost` gives, the bytes at the provider's own price per GB |
| `server/nodes/search/{brave_search,serper_search,perplexity_search}/__init__.py` | Search plugins: one `record_api_usage` call in each `search` operation, after the response came back |
| `server/services/plugin/operation.py` | `@Operation` decorator + `OperationSpec.cost`, metadata nothing reads |
| `server/models/database.py` | `APIUsageMetric`, `TokenUsageMetric` models |
| `server/services/usage/recording.py` | `record_api_usage()` and `record_llm_usage()`: one usage row per call; neither raises |
| `server/services/usage/summaries.py` | `api_usage_summary()`, `provider_usage_summary()`, `chat_run_costs()`, `workflow_costs()`, `month_start()` |
| `server/services/usage/limit.py` | `resolve_spend_limit()`, `spend_limit_notice()`: the spend limit of a chat run. `monthly_budget_reached()`: the monthly budget of an employee |
| `server/services/usage/handlers.py` | The `get_api_usage_summary` / `get_provider_usage_summary` / `get_chat_run_costs` / `get_workflow_costs` commands |
| `client/src/components/credentials/sections/ApiUsageSection.tsx` | Per-service API usage + cost display |
| `client/src/components/credentials/sections/LlmUsageSection.tsx` | Per-provider token usage + cost display |
| `client/src/hooks/useApiKeys.ts` | `getAPIUsageSummary` / `getProviderUsageSummary` |
| `client/src/hooks/useUsageQueries.ts` | `useChatRunCostsQuery` (a reply's cost), `useWorkflowCostsQuery` (this month's cost and tokens by employee), `useEmployeeUsageQuery` (this month's finished tasks, the team's or one employee's) and `invalidateUsage` |
| `client/src/components/ui/CostPanel.tsx` | The cost panel: this month's cost by employee, each employee's row opening to their usage, the spend limit of a reply and the monthly budget of an employee, in Home's Billing and Dev's Settings |
| `client/src/components/ui/EmployeeUsage.tsx` | One employee's usage this month: cost, tokens, finished tasks |
| `client/src/components/ui/UsageButton.tsx` | The Usage button, in Home's header and the editor's top toolbar |
