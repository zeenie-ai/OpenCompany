# Plugin System (Wave 11)

The MachinaOs plugin system is a class-based, declarative node
authoring model inspired by n8n's `INodeType`, Nango's
`providers.yaml`, Pipedream's app/component split, and Temporal's
activity pattern. One Python file under `server/nodes/<group>/<name>.py`
= one node. No cross-cutting edits.

**Status: shipped.** 111 plugin classes cover every node type in the
product across 9 Temporal worker queues; 124 pytest contract
invariants lock the architecture. `services/handlers/` shrank from
12.8K → 1.1K LOC across 16 → 4 files. See
[`server/nodes/README.md`](../server/nodes/README.md) for the
authoring cookbook (5-minute recipe + shared helpers + common
pitfalls).

## Quick start — adding a new node

```python
# server/nodes/search/example_search.py
from pydantic import BaseModel, Field
from services.plugin import (
    ActionNode, ApiKeyCredential, NodeContext, Operation, TaskQueue,
)


class ExampleCredential(ApiKeyCredential):
    id = "example_api"
    display_name = "Example Search"
    category = "Search"
    key_name = "X-API-Key"
    key_location = "header"


class ExampleParams(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=10, ge=1, le=50)


class ExampleOutput(BaseModel):
    results: list
    count: int


class ExampleSearchNode(ActionNode):
    type = "exampleSearch"
    display_name = "Example Search"
    icon = "asset:example"
    color = "#abcdef"
    group = ("search", "tool")
    description = "Example search via external API"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    credentials = (ExampleCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    Params = ExampleParams
    Output = ExampleOutput

    @Operation("search")
    async def search(self, ctx: NodeContext, params: ExampleParams) -> ExampleOutput:
        async with ctx.connection("example_api") as conn:
            resp = await conn.get(
                "https://api.example.com/search",
                params={"q": params.query, "limit": params.max_results},
            )
            resp.raise_for_status()
            data = resp.json()
        return ExampleOutput(results=data.get("hits", []), count=len(data.get("hits", [])))
```

**That's it.** On server restart:

- `BaseNode.__init_subclass__` eagerly registers the class into four
  registries: `NODE_METADATA`, `_DIRECT_MODELS`,
  `NODE_OUTPUT_SCHEMAS`, `_HANDLER_REGISTRY`.
- `_NODE_CLASS_REGISTRY` indexes the class itself so Temporal workers
  and tool dispatch can look it up by type.
- NodeSpec emits automatically via
  `GET /api/schemas/nodes/exampleSearch/spec.json`.
- `_PLUGIN_HANDLERS` merge in `NodeExecutor` makes it runnable.
- The node appears in the Component Palette under its group
  (search + tool) at the next browser reload.

## Architecture

### Class hierarchy

```
BaseNode (services/plugin/base.py)
├── ActionNode   fire-once, {success, result} envelope
├── TriggerNode  long-lived, event (event_waiter) or polling modes
└── ToolNode     AI-invoked, flat return (no success wrapper)
```

Every subclass auto-registers on import. Pure-visual or abstract
intermediaries pass `abstract=True` in the class definition:

```python
class SpecializedAgentBase(ActionNode, abstract=True):
    ...
```

### Class attributes

| Attribute | Purpose |
|---|---|
| `type` | Node type string. Matches workflow JSON + registry key. |
| `version` | Int, bumped on breaking changes. Activity name includes it. |
| `display_name` / `subtitle` / `description` | Palette + panel header. |
| `icon` | `asset:<key>` / `<lib>:<brand>` / URL / emoji. |
| `color` | Hex or Dracula token. |
| `group` | Tuple of palette groupings (first is primary). |
| `component_kind` | Frontend dispatch: `square` / `trigger` / `agent` / `tool` / `model` / `start` / `generic`. |
| `handles` | React Flow handle topology (`input-main`, `output-main`, …). |
| `ui_hints` | Dict of panel flags (`hasCodeEditor`, `isMemoryPanel`, `isMasterSkillEditor`, `isToolPanel`, `hasSkills`, `isConfigNode`, …). See "Auto-derived uiHints" below — `isConfigNode` is set automatically for `('memory', 'tool')` group plugins. |
| `annotations` | Pipedream-style: `destructive` / `readonly` / `open_world`. |
| `credentials` | Tuple of `Credential` subclasses the node uses. |
| `Params` | Pydantic `BaseModel` — user-facing parameters. Used for both UI rendering and AI tool schemas. |
| `Output` | Pydantic `BaseModel` — runtime output shape. |
| `usable_as_tool` | `ActionNode` flag — mints a ToolNode adapter for AI invocation. |
| `task_queue` | Temporal worker pool. See `TaskQueue` constants. |
| `retry_policy` | `RetryPolicy` dataclass (mirrors `temporalio.common.RetryPolicy`). |
| `start_to_close_timeout` / `heartbeat_timeout` | Per-node Temporal knobs. |

### Auto-derived uiHints

`BaseNode._metadata_dict` (`server/services/plugin/base.py`) calls
`_derive_auto_ui_hints(cls.group)` to pre-populate panel-visibility
flags from group membership before merging the plugin's explicit
`cls.ui_hints`:

```python
ui_hints = _derive_auto_ui_hints(cls.group)
ui_hints.update(cls.ui_hints)
if ui_hints:
    meta["uiHints"] = ui_hints
```

The auto-derivation rule today:

| Trigger | Sets | Used by |
|---|---|---|
| Plugin's `group` tuple contains `"memory"` or `"tool"` (centralized as `_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})`) | `uiHints.isConfigNode = True` | Frontend `InputSection.tsx` and `OutputPanel.tsx` — tells the panel that this node is auxiliary configuration and should inherit the parent's main inputs instead of showing direct upstream connections |

**Explicit always wins.** A plugin that wants to opt out of an
auto-derived flag declares it explicitly: `ui_hints = {"isConfigNode": False}`.
The merge order (auto first, then `dict.update` with the plugin's
declaration) means explicit values overwrite auto-derived ones.

**Adding a new auto-derivation rule.** Extend `_derive_auto_ui_hints`
in `services/plugin/base.py`. The rule must be derivable from
declared class attributes (group / kind / etc.) — never from runtime
state. Add the new flag name to `INodeUIHints` in
`client/src/types/INodeProperties.ts` and to the `known` set in
`server/tests/test_node_spec.py::test_ui_hints_only_carry_known_flags`
(the pytest invariant locks the flag set so unknown keys fail CI).

### Params schema conventions

**snake_case everywhere.** Field names are the JSON Schema keys, the UI
parameter keys, and the `displayOptions.show` reference keys. Keeping
a single naming convention makes cross-references trivially correct.

- No `alias="camelName"` on `Field(...)`.
- No `populate_by_name=True` in `model_config`.
- No `model_dump(by_alias=True)` in handlers — call `model_dump()` or
  read typed attributes off the validated `params` object.
- `displayOptions.show["driver_field"]` must match a Pydantic field
  name in the same `Params` class. The frontend's visibility evaluator
  looks up that exact key.

Example:

```python
class ExampleParams(BaseModel):
    operation: Literal["send", "search"] = Field(default="send")
    recipient: str = Field(
        default="",
        description="Email recipient",
        json_schema_extra={"displayOptions": {"show": {"operation": ["send"]}}},
    )
    query: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"operation": ["search"]}}},
    )
    model_config = ConfigDict(extra="ignore")
```

Option labels (the `name` shown in dropdowns) ride on `json_schema_extra`:

```python
operation: Literal["send", "search"] = Field(
    default="send",
    json_schema_extra={"options": [
        {"name": "Send", "value": "send"},
        {"name": "Search", "value": "search"},
    ]},
)
```

If a user-facing input needs multi-line, a password mask, or a code
editor, set those via `json_schema_extra` keys the adapter lifts into
`typeOptions`: `rows`, `password`, `editor`, `editorLanguage`,
`dynamicOptions`, `loadOptionsMethod`, `numberStepSize`, `widget`,
`accept`.

### Field grouping (collapsible collections)

Mark a field with `json_schema_extra={"group": "<key>"}` to nest it
under a collapsible **collection** container in the parameter panel
(same UX as an n8n "Options" accordion). Group membership is opt-in —
plugins that don't declare any group render flat exactly like today.

```python
class AIAgentParams(BaseModel):
    # Top-level fields (always visible)
    provider: Literal[...] = "openai"
    model: str = Field(default="")
    prompt: str = Field(default="", json_schema_extra={"rows": 4})
    system_message: Optional[str] = Field(default="")

    # "Options" group — collapsed by default, "Add Option" reveals them
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0,
        json_schema_extra={"group": "options"},
    )
    max_tokens: Optional[int] = Field(
        default=1000, ge=1, le=200000,
        json_schema_extra={"group": "options"},
    )

    # Class-level metadata (optional — title-cased defaults otherwise)
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "groups": {
                "options": {
                    "display_name": "Options",
                    "placeholder": "Add Option",
                },
            },
        },
    )
```

**Adapter behaviour** (`client/src/adapters/nodeSpecToDescription.ts`):

- Fields with the same `group` key are collected into a single
  `type: "collection"` INodeProperty. The collection is positioned at
  the **first** child's slot in the original schema order; subsequent
  children slot into its `options` array.
- Missing class-level `groups[<key>]` metadata falls back to
  `display_name = titleCase(key)` (e.g. `"options"` → `"Options"`) and
  `placeholder = f"Add {displayName.rstrip('s')}"` (e.g. `"Add Option"`).
- Multiple groups per class are supported — just declare each key.

**When to use it:** cluster advanced / rarely-tuned knobs (temperature,
max_tokens, thinking params) behind an "Add Option" button so the
default panel stays small. Main-entry fields (provider, prompt,
required inputs) stay at the top level.

### Credentials vs. Params

`api_key` is never a declared Params field. Credentials live in the
credentials DB via `ApiKeyCredential` / `OAuthCredential` subclasses;
`services/node_executor._inject_api_keys()` resolves them at execution
time and puts them in the raw parameters dict. Plugins that need the
injected key read `ctx.raw["_raw_parameters"]["api_key"]` (stashed by
`BaseNode.execute` before Pydantic validation strips it).

If you find yourself declaring an `api_key: str = Field(...)` on a
Params class, stop: add an `ApiKeyCredential` subclass instead and wire
it via the `credentials = (...)` tuple on the node class.

### Operations (`@Operation`)

A multi-op node declares multiple methods, each decorated with
`@Operation("name")`. `BaseNode._pick_operation` reads
`parameters.operation` to choose which to run.

```python
@Operation("send", cost={"service": "googleGmail", "action": "send", "count": 1})
async def send(self, ctx: NodeContext, params: GmailParams) -> Any: ...

@Operation("search")
async def search(self, ctx: NodeContext, params: GmailParams) -> Any: ...

@Operation("read")
async def read(self, ctx: NodeContext, params: GmailParams) -> Any: ...
```

Single-op nodes use one method; `parameters.operation` can be omitted.

### Declarative REST via `Routing`

For pure REST integrations, leave the op body empty and attach a
`Routing` object:

```python
@Operation("search", routing=Routing(
    request=RoutingRequest(
        method="GET",
        url="https://api.example.com/search",
        qs={"q": "={{params.query}}"},
        headers={"X-API-Key": "={{credentials.api_key}}"},
    ),
    output=RoutingOutput(
        post_receive=[PostReceiveAction(type="root_property", property="data.hits")],
    ),
))
async def search(self, ctx, params): pass  # body unused — routing handles it
```

Supported `post_receive` strategies: `root_property`, `limit`,
`filter`, `set`.

### Connection facade (Nango pattern)

Plugins never see tokens. `ctx.connection(credential_id)` returns an
authed `httpx`-compatible client:

```python
async with ctx.connection("brave_search") as conn:
    resp = await conn.get(url, params={"q": query})
    # X-Subscription-Token header auto-injected by Credential.inject()
```

401/403 responses trigger one refresh-and-retry transparently.

### Credentials

Declarative credentials live **in each node folder's `_credentials.py`**
(Wave 11.E.1) — same "one domain owns its own code" principle as
`_base.py` and `_inline.py` helpers. Three base classes (stay in
`services/plugin/credential.py` as infrastructure):

- `ApiKeyCredential` — header / query / bearer injection.
- `OAuth2Credential` — `Authorization: Bearer <access_token>` with
  auto-refresh via `auth_service.get_oauth_tokens`.
- `Credential` — fully custom (override `resolve()` + `inject()`).

Auto-discovery rides on node-package import. When
`nodes/__init__.py:pkgutil.walk_packages` imports a plugin module,
that module's `from ._credentials import XCredential` statement
imports the sibling `_credentials.py`, which triggers
`Credential.__init_subclass__` → writes to `CREDENTIAL_REGISTRY`
*before* the plugin class is defined. The walker skips
underscore-prefixed files, so `_credentials.py` is never
double-imported. Contract invariant
`test_credentials_are_registered` ensures every declared credential
on a plugin resolves to a registered class.

**Shipped credentials** (Wave 11.E → E.1):

| File | Class(es) | Auth | Covers |
|---|---|---|---|
| `nodes/google/_credentials.py` | `GoogleCredential` | oauth2 | gmail, calendar, drive, sheets, tasks, contacts, gmailReceive |
| `nodes/location/_credentials.py` | `GoogleMapsCredential` | api_key (query) | gmaps_create / gmaps_locations / gmaps_nearby_places |
| `nodes/twitter/_credentials.py` | `TwitterCredential` | oauth2 | twitterSend / twitterSearch / twitterUser / twitterReceive |
| `nodes/telegram/_credentials.py` | `TelegramCredential` | api_key | telegramSend / telegramReceive |
| `nodes/scraper/_credentials.py` | `ApifyCredential` | api_key (bearer) | apifyActor |
| `nodes/model/_credentials.py` | `OpenAI / Anthropic / Gemini / OpenRouter / Groq / Cerebras / DeepSeek / Kimi / Mistral / Xai` | api_key | 9 chat models (xAI reserved) |
| `nodes/search/*.py` (inline) | `BraveSearch / Serper / Perplexity` | api_key | single-use search nodes |

`GoogleCredential` exposes a `build_credentials()` classmethod that
returns a `google.oauth2.credentials.Credentials` — hand-off to
`googleapiclient.discovery.build(...)` is unchanged from Wave 11.D.4.

Agents (aiAgent / chatAgent / 13 specialized) stay `credentials = ()`
because they are poly-provider — the user picks the provider at
runtime via `params.provider`, so declaring any single credential
would be misleading.

### Shared agent helpers

Every agent plugin (ai_agent, chat_agent, 13 specialized agents, team
leads) calls one helper:

```python
from ._inline import prepare_agent_call

kwargs = await prepare_agent_call(
    node_id=ctx.node_id, node_type=self.type,
    parameters=params.model_dump(),
    context=ctx.raw, database=database,
    log_prefix=f"[{self.type}]",
)
response = await ai_service.execute_chat_agent(ctx.node_id, **kwargs)
```

`prepare_agent_call` wraps the shared edge-walker
(`services/plugin/edge_walker.py`) + task context injection +
auto-prompt fallback + team-lead teammate injection. Plugin-specific
logic stays in the `execute_op` method.

### Temporal per-node activities (Wave 11.F)

Every `BaseNode` subclass exposes `cls.as_activity()`, a Temporal
`@activity.defn`-decorated callable with name
`node.{type}.v{version}`. Collect them for worker registration:

```python
from services.temporal.plugin_activities import (
    collect_plugin_activities,
    distinct_task_queues,
)

# Single-queue workers:
activities = collect_plugin_activities(task_queue="ai-heavy")
worker = Worker(client, task_queue="ai-heavy", activities=activities, ...)

# Multi-queue pool (one worker per queue):
from services.temporal.worker import TemporalWorkerPool
pool = TemporalWorkerPool(client)  # defaults to all declared queues
await pool.start()
```

Queue distribution (current state):

| Queue | Plugins | Default concurrency |
|---|---|---|
| `ai-heavy` | 28 | 4 |
| `rest-api` | 21 | 50 |
| `machina-default` | 25 | 20 |
| `android` | 16 | 10 |
| `messaging` | 7 | 20 |
| `triggers-event` | 5 | 100 |
| `triggers-poll` | 4 | 100 |
| `code-exec` | 3 | 10 |
| `browser` | 2 | 4 |

Env overrides: `TEMPORAL_<QUEUE>_CONCURRENCY` (e.g.
`TEMPORAL_AI_HEAVY_CONCURRENCY=8`).

### Trigger registry auto-populate (Wave 11.D.11)

`services/event_waiter.py:TRIGGER_REGISTRY` + `FILTER_BUILDERS` are
backfilled from plugin `TriggerNode` subclasses on first access. A
plugin declaring `event_type` + `build_filter` auto-registers — no
hand-edit of `event_waiter.py` required.

Hardcoded entries still win when present (authoritative), so plugin
upgrades never silently replace hand-tuned filter behaviour.

## Self-contained plugin folders

Some plugins are richer than a single `BaseNode` subclass — they own a
long-lived service (a bot connection, a WebSocket bridge, an SDK
session), their own credentials-modal WebSocket commands, custom
event filtering, lifecycle hooks. Telegram is the reference shape.

**Principle**: every cross-cutting concern resolves to a generic
registry that the consumer (router, broadcaster, event waiter, schema
emitter) reads at dispatch time. Plugin packages **register
themselves** into those registries from their package `__init__.py`.
**Nothing outside the plugin folder hardcodes the plugin's name.**

### Folder shape (Telegram reference)

```
server/nodes/telegram/
├── __init__.py          # imports + 5 register_* calls covering 5 registries (no logic)
├── _credentials.py      # TelegramCredential (ApiKeyCredential)
├── _service.py          # TelegramService singleton (bot lifecycle)
├── _handlers.py         # WebSocket handlers + WS_HANDLERS dict
├── _filters.py          # build_telegram_filter (event_waiter filter)
├── _refresh.py          # refresh_telegram_status + precheck_telegram_trigger
├── telegram_send.py     # ActionNode + AI tool
└── telegram_receive.py  # TriggerNode
```

Underscore-prefixed files are package-private; the `nodes` walker
skips them. The two non-underscore files are the plugin classes (one
per node type) — same pattern as every other folder.

### Up to six cross-cutting registries — use only what your plugin needs

| Concern | Registry module | Register from plugin via |
|---|---|---|
| Credentials-modal WebSocket commands (Connect / Disconnect / Send / Status / etc.) | `services.ws_handler_registry` | `register_ws_handlers({type: handler, ...})` |
| Trigger event-filter builder | `services.event_waiter` | `register_filter_builder(node_type, fn)` |
| Trigger pre-execution check (e.g. "bot not connected") | `services.event_waiter` | `register_trigger_precheck(node_type, fn)` |
| Service-status refresh on WebSocket connect | `services.status_broadcaster` | `register_service_refresh(callback)` |
| Per-node output schema (when not auto-derivable) | `services.node_output_schemas` | `register_output_schema(node_type, ModelClass)` |
| FastAPI HTTP router (OAuth callbacks, webhook receivers, etc.) | `services.ws_handler_registry` | `register_router(router, name='<plugin>')` — Wave 11.I; declare a `_router.py` exposing an `APIRouter` and call from `__init__.py`. Discovered at startup via `services.ws_handler_registry.get_routers()`. |

All accept idempotent re-imports (same callable / class for the
same key is a no-op; conflicts raise `ValueError`).

**Plugins use only the registries they need.** Telegram uses 5 (no
router); Stripe uses 4 (webhook-driven, no filter/precheck); Android
uses 4 (with router); WhatsApp uses 5. There is no "register every
hook" rule.

### Telegram `__init__.py` (canonical wiring)

```python
# server/nodes/telegram/__init__.py
from services.event_waiter import register_filter_builder, register_trigger_precheck
from services.node_output_schemas import register_output_schema
from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_ws_handlers

from ._credentials import TelegramCredential
from ._filters import build_telegram_filter
from ._handlers import WS_HANDLERS
from ._refresh import precheck_telegram_trigger, refresh_telegram_status
from ._service import TelegramService, get_telegram_service

# Plugin classes (importing them runs __init_subclass__ for the node registry)
from .telegram_receive import TelegramReceiveNode, TelegramReceiveOutput
from .telegram_send import TelegramSendNode, TelegramSendOutput

# --- self-registration on import -------------------------------------------
register_ws_handlers(WS_HANDLERS)
register_filter_builder("telegramReceive", build_telegram_filter)
register_trigger_precheck("telegramReceive", precheck_telegram_trigger)
register_service_refresh(refresh_telegram_status)
register_output_schema("telegramReceive", TelegramReceiveOutput)
register_output_schema("telegramSend", TelegramSendOutput)
```

That's the entire wiring. Adding telegram-style cross-cutting code to
a new plugin folder takes one register call per concern; the consumer
never learns the plugin's name.

### How consumers consult the registries

Each consumer hits its registry at dispatch time, not at module load,
so plugins registered later still work:

```python
# routers/websocket.py — central WS dispatcher
from services.ws_handler_registry import get_ws_handlers

def _resolve_handler(msg_type):
    return MESSAGE_HANDLERS.get(msg_type) or get_ws_handlers().get(msg_type)

# services/handlers/triggers.py — generic trigger handler
precheck_error = await event_waiter.run_trigger_precheck(node_type, parameters)
if precheck_error:
    return error_envelope(precheck_error)
waiter = await event_waiter.register(node_type, node_id, parameters)

# services/status_broadcaster.py — WS-connect refresh
async with asyncio.TaskGroup() as tg:
    tg.create_task(self._refresh_whatsapp_status())  # legacy hardcoded
    # ... future migrations move into the registry too:
    for callback in _SERVICE_REFRESH_CALLBACKS:
        tg.create_task(callback(self))
```

### When does a plugin need the full self-contained shape?

Most plugins are a single `BaseNode` subclass — that's the right
default. Promote to the self-contained folder shape only when the
plugin owns one of:

- A long-lived stateful object (bot / device / session / subprocess).
- Credentials-modal lifecycle commands beyond the standard
  Save / Load / Delete (e.g. Connect / Disconnect).
- Trigger pre-checks that need plugin-specific service state.
- A status refresh that runs on WebSocket connect.
- A duplicate `Output` Pydantic class that the central
  `node_output_schemas.NODE_OUTPUT_SCHEMAS` would otherwise pin.

If none of those apply: a single `<name>.py` in the right folder is
the whole node. Don't create `_service.py` / `_handlers.py` / etc.
just because telegram has them.

### Wire format is the contract — not module paths

The frontend identifies plugin commands by **WebSocket message type**
strings (`telegram_connect`, `telegram_status`, …). Moving the handler
implementation between Python files is invisible to the frontend so
long as the registered keys stay the same. The
`server/config/credential_providers.json` declarative config — served
to the frontend via `handle_get_credential_catalogue` and consumed by
`useCatalogueQuery` — is likewise stable across backend reorganisations.
(The pre-Wave-13 `client/src/components/credentials/providers.tsx`
static fallback no longer exists; the server catalogue is the single
source of truth for the credentials panel.)

This is why the telegram refactor changed zero frontend code despite
moving 754 lines out of `services/telegram_service.py`.

## Folder layout

```
server/
├── nodes/                        # One file per node
│   ├── __init__.py              # pkgutil.walk_packages discovery
│   ├── groups.py                # Palette group metadata
│   ├── agent/                   # AI agents (aiAgent, chatAgent, 13 specialized)
│   │   ├── _handles.py          # Shared handle topology helpers
│   │   ├── _inline.py           # prepare_agent_call()
│   │   ├── _specialized.py      # SpecializedAgentBase
│   │   └── <agent>.py
│   ├── model/                   # AI chat models (9 providers)
│   │   ├── _base.py             # ChatModelBase + ChatModelParams/Output
│   │   └── <provider>_chat_model.py
│   ├── android/                 # 16 Android service nodes
│   │   ├── _base.py             # AndroidServiceBase
│   │   └── <service>.py
│   ├── code/                    # python/js/ts executors
│   │   ├── _base.py             # CodeExecutorBase
│   │   ├── _nodejs.py           # Shared NodeJSClient singleton
│   │   └── <lang>_executor.py
│   ├── filesystem/              # file_read / file_modify / shell / fs_search
│   │   ├── _backend.py          # Shared LocalShellBackend helper
│   │   └── <op>.py
│   ├── document/                # http_scraper / parser / chunker / embedding / vector
│   │   ├── _helpers.py          # delegate() wrapper
│   │   └── <stage>.py
│   ├── google/                  # gmail / calendar / drive / sheets / tasks / contacts
│   ├── proxy/                   # proxy_request / proxy_config / proxy_status
│   │   └── _usage.py            # Shared track_proxy_usage
│   ├── search/                  # brave / serper / perplexity / duckduckgo
│   ├── scraper/                 # apify / crawlee
│   ├── tool/                    # calculator / currentTime / taskManager / writeTodos
│   ├── trigger/                 # webhookTrigger / chatTrigger / taskTrigger
│   ├── workflow/                # start
│   ├── scheduler/               # cronScheduler / timer
│   ├── whatsapp/                # whatsappSend / whatsappDb / whatsappReceive
│   ├── telegram/                # telegramSend / telegramReceive
│   ├── twitter/                 # twitterSend / search / user / receive
│   ├── email/                   # emailSend / emailRead / emailReceive
│   ├── chat/                    # chatSend / chatHistory
│   ├── social/                  # socialSend / socialReceive
│   ├── browser/                 # browser (agent-browser CLI)
│   ├── utility/                 # httpRequest / webhookResponse / console / team_monitor / process_manager
│   ├── text/                    # textGenerator / fileHandler
│   ├── location/                # gmaps_create / gmaps_locations / gmaps_nearby_places
│   └── skill/                   # simpleMemory / masterSkill
│                                # Each group folder owns its own
│                                # _credentials.py (Wave 11.E.1) —
│                                # no central credentials package.
└── services/
    ├── plugin/                  # Plugin runtime
    │   ├── base.py              # BaseNode
    │   ├── action.py / trigger.py / tool.py
    │   ├── operation.py         # @Operation decorator + collector
    │   ├── routing.py           # Declarative REST DSL
    │   ├── credential.py        # Credential base classes
    │   ├── connection.py        # Nango-style authed httpx wrapper
    │   ├── context.py           # NodeContext dataclass
    │   ├── scaling.py           # TaskQueue / RetryPolicy
    │   ├── edge_walker.py       # collect_agent_connections / collect_teammate_connections
    │   └── interceptor.py       # Interceptor ABC + chain
    ├── node_registry.py         # register_node + _NODE_CLASS_REGISTRY + helpers
    ├── node_spec.py             # NodeSpec envelope emission
    └── temporal/
        ├── plugin_activities.py # collect_plugin_activities / distinct_task_queues
        └── worker.py            # TemporalWorkerManager + TemporalWorkerPool
```

## Contract invariants

`server/tests/test_plugin_contract.py` — 16 invariants enforced on
every CI run. Examples:

- Non-empty `type` / `display_name` / `group` per class.
- `Params` + `Output` must be Pydantic `BaseModel` subclasses.
- Every declared `credentials` entry resolves to a registered class.
- Operation names unique per class.
- `routing=...` requires `credentials` declared.
- `task_queue` ∈ `TaskQueue.ALL`.
- Every `ToolNode` JSON schema has no `$defs` / `$ref` (LLM-compat).
- Every event-mode `TriggerNode` declares `event_type`.
- Fast-path covers every AI-tool-usable plugin (no hardcoded schema
  dependency).
- Trigger registry auto-populates for every event-mode plugin.

108 Wave 10 invariants in `test_node_spec.py` still run — 124 total.

## Canonical principles

1. **One file = one node.** Adding a new node never edits multiple
   files. The filesystem location matches the palette group.
2. **Backend is SSOT.** Node declaration, visual metadata, handlers,
   schemas, credentials, icons — one authoring location.
3. **No frontend fallbacks.** Missing data surfaces as a visible gap
   so the backend bug is obvious, not masked.
4. **Stateful services stay in `services/`.** AIService, MapsService,
   NodeJSClient, etc. Plugins call them; never inline them.
5. **Per-handler helpers move WITH the handler.**
   `_format_console_output`, `_extract_text`, … inline into the
   plugin file. Cross-handler helpers (like `edge_walker`) extract
   to shared modules.
6. **Container injection.** Plugins do
   `from core.container import container; svc = container.X()` —
   never instantiate services directly.
7. **Pydantic for everything.** Params, Output, Credential config,
   Routing — all Pydantic. Validation at the boundary, typed at the
   core.

## Migration history (for future readers)

- Wave 6 — Output schemas on backend.
- Wave 10 — Input schemas + metadata on backend; `@register_node`
  decorator (dict form); filesystem-as-catalog.
- Wave 11.A — `services/plugin/` package with `BaseNode` hierarchy.
- Wave 11.B — Reference migrations (5 nodes across all kinds).
- Wave 11.B.1 — Unified tool dispatch via plugin fast-path.
- Wave 11.C — 111/111 nodes migrated across 5 batches; folder layout
  mirrors palette groups.
- Wave 11.D.0 — `edge_walker` extracted to services/plugin/.
- Wave 11.D.1-6 — Handler bodies inlined into plugins (trivial
  wrappers, code executors, HTTP/proxy, polling triggers, agents).
- Wave 11.D.4 — Google Workspace (gmail / calendar / drive / sheets /
  tasks / contacts) inlined under `nodes/google/`, shared
  `_base.py` + `_gmail.py` helpers.
- Wave 11.D.7 — Document pipeline (httpScraper, fileDownloader,
  documentParser, textChunker, embeddingGenerator, vectorStore)
  inlined under `nodes/document/`.
- Wave 11.D.8 — Twitter / Crawlee / Apify inlined. Twitter shares
  `nodes/twitter/_base.py` for client + XDK helpers.
- Wave 11.D.9 — WhatsApp + Social inlined into `nodes/whatsapp/_base.py`
  and `nodes/social/_base.py` (full bodies, RPC dispatch via
  `services.whatsapp_service`; renamed from `routers/whatsapp.py` in
  Wave 11.E.2 since it was never an APIRouter).
- Wave 11.D.10 — `utility.py` split across 12 plugin files (maps,
  text, workflow start, timer, cron, console, team monitor, chat).
- Wave 11.D.11 — Auto-populate trigger registries.
- Wave 11.D.12 — Fast-path contract invariants.
- Wave 11.D.13 — Sunset empty bulk files + dead dispatch.
- Wave 11.F — Per-plugin Temporal activities + worker pools.
- Wave 11.E — Declarative credentials: 18 `Credential` subclasses
  (GoogleCredential + GoogleMapsCredential + TwitterCredential +
  TelegramCredential + ApifyCredential + 10 LLM providers + 3 inline
  search credentials). 29 plugins now declare `credentials = (...)`.
  Agents stay poly-provider (empty tuple).
- Wave 11.E.1 — Modularised credentials into per-domain
  `nodes/<group>/_credentials.py` files. `server/credentials/`
  directory deleted; auto-discovery rides on node-package import.
- Wave 11.E.2 — Dead-code sweep: fixed 2 broken agent imports,
  stripped 13 dead dispatch branches in `tools.py`, deleted duplicate
  `handlers/proxy.py`, moved misnamed `routers/whatsapp.py` →
  `services/whatsapp_service.py`, dedup'd `TRIGGER_NODE_TYPES`.
- Wave 11.E.3 — Inlined the last per-domain handler bodies into
  plugins. Deleted 8 fully-orphan handler files (search, code,
  telegram, http, filesystem, email, process, todo) and 5
  still-referenced ones (browser, android, claude_code, rlm,
  deep_agent) by inlining into their plugins. Split `handlers/ai.py`
  4 ways: `handle_ai_chat_model` → `ChatModelBase.chat`,
  `handle_simple_memory` → `SimpleMemoryNode.read`,
  `handle_ai_agent` / `handle_chat_agent` → deleted entirely.
  `tools.py:_execute_delegated_agent` now looks up the child agent's
  plugin class via `services.node_registry.get_node_class(node_type)`,
  builds `NodeContext.from_legacy(...)`, and calls
  `instance.execute(node_id, params, ctx)` directly — no handler shell
  in the path.
- Wave 11.E.4 — Relocated `tools.py` movables: proxyConfig 10-op
  matrix → `nodes/proxy/proxy_config.execute_proxy_config` (shared by
  the plugin's `dispatch` op and the AI-tool branch in `tools.py`);
  Android AI-tool dispatch (toolkit + direct service) →
  `nodes/android/_base.{execute_android_toolkit,
  execute_android_service_tool}` with a single canonical
  `SERVICE_ID_MAP` and a shared `_execute_with_broadcast` helper
  (previously duplicated in `tools.py`). `tools.py` from 1,255 → 821
  LOC.
- Wave 11.G — Nodes cookbook (`server/nodes/README.md`) + CLAUDE.md
  plugin section + this file refreshed to match shipped state.
- Wave 11.H — Self-contained plugin folders. Six generic registries
  replace per-plugin hardcoding in core services:
  `services.ws_handler_registry.register_ws_handlers` (WebSocket
  commands), `services.ws_handler_registry.register_router` (FastAPI
  routers — sibling concern in the same file as `register_ws_handlers`,
  added Wave 11.I),
  `event_waiter.register_filter_builder` (event filters),
  `event_waiter.register_trigger_precheck` (trigger pre-execution
  checks), `status_broadcaster.register_service_refresh` (WS-connect
  refresh callbacks), `node_output_schemas.register_output_schema`
  (output schemas).
- Wave 11.I — Eight more plugin domains migrated to the
  self-contained pattern: WhatsApp, Twitter, Google Workspace,
  Android, Browser, Email, Code (Claude Code), and the credential
  validation scaffold (`Credential.validate` + `Credential._probe`)
  for Maps / Apify / Ollama / LM Studio. `routers/websocket.py`
  shrunk by ~808 LOC; three plugin routers (twitter / google /
  android) moved into `nodes/<plugin>/_router.py` and mount via the
  plugin-router loop in `main.py`. `tests/test_plugin_self_containment.py`
  locks the contract with 7 invariant classes (forbidden-imports /
  no-router-outside-nodes / per-plugin self-registration /
  registry-API sanity / stale-paths-absent / main.py-does-not-mount /
  WS_HANDLERS-non-empty).
- Wave 12 — Generalized event framework
  ([`services/events/`](../server/services/events/)). Adds
  `WorkflowEvent` (CloudEvents v1.0 envelope, in-house Pydantic),
  `EventSource` hierarchy (`PushEventSource` /
  `PollingEventSource` / `DaemonEventSource` / `WebhookSource` /
  `WebhookTriggerNode`), a verifier registry (Stripe / GitHub /
  Standard Webhooks / generic HMAC), and three wiring helpers
  (`make_lifecycle_handlers`, `make_status_refresh`,
  `run_cli_command`). Future event-source plugins drop to ≈150
  executable lines. Stripe is the reference implementation;
  Phase 2-4 migrate the existing polling and daemon triggers onto
  the framework. Telegram is the reference implementation:
  ~870 lines of telegram-specific code moved out of
  `services/telegram_service.py` (deleted), `routers/websocket.py`
  (7 inline handlers removed), `services/event_waiter.py`
  (`build_telegram_filter` + hardcoded registry entry removed),
  `services/status_broadcaster.py` (`_refresh_telegram_status`
  removed), `services/handlers/triggers.py` (hardcoded
  `if node_type == 'telegramReceive'` branch removed),
  `services/node_output_schemas.py` (duplicate
  `TelegramReceiveOutput` class removed) — all relocated to
  `nodes/telegram/`. Wire-format unchanged → zero frontend
  changes. Frontend identifies plugin commands by WebSocket message
  type strings, not Python module paths.

`services/handlers/` is now **4 files / ~970 LOC** (down from 16
files / 12,800 LOC; `google_auth.py` moved to `nodes/google/_auth_helper.py`
in Wave 11.I commit D):

| File | LOC | Purpose |
|---|---|---|
| `tools.py` | 821 | AI-tool dispatcher, plugin fast-path, agent delegation infrastructure (shared `_delegated_tasks` / `_delegation_results` state). |
| `triggers.py` | 126 | Generic event-trigger handler for polling triggers (gmailReceive, twitterReceive, etc.). |
| `todo.py` | 65 | TaskManager / writeTodos invocation surface for AI tool nodes. |
| `__init__.py` | 23 | Package docstring; nothing imports from `services.handlers` at package level. |

Every domain owns its own code under `nodes/<group>/` — plugin file +
optional `_base.py` / `_inline.py` / `_credentials.py` siblings. No
handler shells, no central credential registry, no cross-domain reach.

## Wave 12 — Generalized event framework (`services/events/`)

Wave 11.H proved the self-contained-folder pattern. Wave 12 takes the
next step: stop re-implementing the same event-source plumbing in
every folder. The new package
[`server/services/events/`](../server/services/events/) provides the
shared base classes that every trigger / daemon / signed-webhook
plugin builds on top of.

### Why

Pre-framework, every event-source plugin re-wrote the same boilerplate:

- **Polling loop frame** duplicated verbatim across `gmail_receive`,
  `email_receive`, `twitter_receive` (`sleep → poll → diff baseline →
  dispatch`).
- **Subprocess supervision** had no shared base — telegram (SDK loop),
  whatsapp (Go RPC), stripe (CLI subprocess) each owned their
  singleton.
- **HMAC signature verification** was about to be re-implemented per
  signed-webhook integration (Stripe, GitHub, Slack, Standard Webhooks
  / Svix providers).
- **Lifecycle WebSocket handlers** (connect / disconnect / reconnect /
  status) were ~25 LOC of identical boilerplate per plugin.
- **Status-refresh callback** (auto-reconnect on WS-client connect) was
  another ~12 LOC of identical boilerplate per plugin.
- **CLI invocation** (find binary on PATH, inject API key, subprocess
  with timeout, parse JSON, uniform error envelope) was a copy-paste
  target.

Wave 12 absorbs all of this into framework code. New event-source
plugins drop to **~150 executable lines** (vs ~600+ pre-framework).

### Public surface

```python
from services.events import (
    # Envelope
    WorkflowEvent,                # CloudEvents v1.0 model (in-house)

    # EventSource hierarchy
    EventSource,                  # ABC: start / stop / status / emit
    PushEventSource,              #   external code calls receive()
    PollingEventSource,           #   poll_once() at intervals
    DaemonEventSource,            #   ProcessService-supervised subprocess
    WebhookSource,                #   HTTP POST to /webhook/{path}
    BaseTriggerParams,            # Pydantic base for trigger Params
    WebhookTriggerNode,           # TriggerNode bound to a WebhookSource

    # Webhook signature verifiers
    WebhookVerifier,              # ABC
    StripeVerifier,               # t=,v1=,HMAC-SHA256
    GitHubVerifier,               # X-Hub-Signature-256
    StandardWebhooksVerifier,     # Svix scheme (id.timestamp.body)
    HmacVerifier,                 # Generic single-header HMAC fallback

    # Wiring helpers
    register_webhook_source,      # WEBHOOK_SOURCES[path] = source
    make_lifecycle_handlers,      # connect/disconnect/reconnect/status WS dict
    make_status_refresh,          # register_service_refresh callback factory
    run_cli_command,              # subprocess + credential + JSON parse
)
```

### Source taxonomy

Modality-axis hierarchy aligned with Apache Camel EIP and n8n trigger
modes:

```
EventSource (abstract)
├── PushEventSource         events arrive via external write (HTTP, RPC, SSE)
│   └── WebhookSource       HTTP POST to /webhook/{path}
├── PollingEventSource      sleep → poll_once → emit; framework owns the loop
└── DaemonEventSource       long-lived subprocess via ProcessService;
                            tail stdout/stderr; parse_line() → events
```

Cron / scheduled events stay on APScheduler — they don't need this
base. Internal in-process events go through `event_waiter.dispatch`
directly.

### Unified envelope (`WorkflowEvent`)

Mirrors CloudEvents v1.0 verbatim
([spec](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md))
plus three MachinaOs routing extras (`workflow_id`,
`trigger_node_id`, `correlation_id`). Field set:
`specversion / id / source / type / time / subject / datacontenttype /
dataschema / data` plus the extras.

```python
WorkflowEvent(
    id=payload["id"],                          # provider's event id (replay safety)
    source="stripe://acct_test",               # URI: scheme://provider/account
    type="stripe.charge.succeeded",            # reverse-DNS event type
    time=datetime.fromtimestamp(payload["created"], tz=timezone.utc),
    subject=payload.get("type"),
    data=payload,
)
```

`WorkflowEvent.matches_type(pattern)` does CloudEvents-style glob
matching: `"all"` / `""` matches everything, `"foo.*"` matches
`"foo.X"` and `"foo.X.Y"`, exact strings match exactly.

`from_legacy(event_type, payload)` wraps pre-framework `Dict`
dispatches as a back-compat shim — every existing trigger keeps
working untouched.

### `WebhookTriggerNode` — the canonical TriggerNode base

Subclass for any signed-webhook trigger. Plugin declares only what
differs from the generic shape:

```python
from services.events import (
    BaseTriggerParams, WebhookTriggerNode, WorkflowEvent,
)

class MyParams(BaseTriggerParams):
    livemode_filter: Literal["all", "test", "live"] = "all"

class MyReceiveNode(WebhookTriggerNode):
    type = "myReceive"
    display_name = "My Receive"
    group = ("myprovider", "trigger")
    handles = (...,)
    credentials = (MyCredential,)

    webhook_source = MyWebhookSource          # required: which source feeds events
    event_type_prefix = "my."                  # auto-prepended to user filters
    Params = MyParams
    Output = MyOutput

    async def _check_precondition(self) -> Optional[str]:
        from ._source import get_listen_source
        return None if get_listen_source()._started else "Daemon not running"

    def _extra_filter(self, params):           # optional; on top of event-type match
        if params.livemode_filter == "all":
            return None
        target = params.livemode_filter == "live"
        return lambda ev: bool(ev.data.get("livemode")) is target

    def shape_output(self, event: WorkflowEvent) -> Dict:
        # Optional override; default returns event.model_dump(mode="json")
        ...
```

`WebhookTriggerNode` provides:

- `event_type` derived from `webhook_source.type` automatically.
- `build_filter` combining CloudEvents type-glob + optional
  `_extra_filter`.
- `execute()` with `_check_precondition` short-circuit and reshape
  passthrough.
- The `@Operation("wait")` stub.

### `WebhookSource` — HTTP receiver

Plugin pairs the trigger with a `WebhookSource` that owns signature
verification + payload shaping:

```python
from services.events import StripeVerifier, WebhookSource, WorkflowEvent

class StripeWebhookSource(WebhookSource):
    type = "stripe.webhook"
    path = "stripe"                             # /webhook/stripe
    verifier = StripeVerifier
    secret_field = "stripe_webhook_secret"
    credential = StripeCredential

    async def shape(self, request, body, payload) -> WorkflowEvent:
        return WorkflowEvent(
            id=payload["id"],
            type=f"stripe.{payload['type']}",
            source=f"stripe://{payload.get('account', 'default')}",
            time=datetime.fromtimestamp(payload["created"], tz=timezone.utc),
            data=payload,
        )
```

The shared dispatch path lives in `routers/webhook.py` — it consults
`WEBHOOK_SOURCES`, runs the verifier, calls `shape()`, dispatches via
`event_waiter`. **No plugin name is hardcoded in core.**

### `DaemonEventSource` — supervised subprocess driver

For plugins that wrap a long-lived CLI tool or SDK loop (Stripe CLI,
future GitHub-CLI / Cloudflare-Wrangler / etc.). Delegates lifecycle
to `ProcessService` (battle-tested PATHEXT-aware launching, kill_tree
cleanup, log capture, Terminal-tab broadcast). The base subscribes to
ProcessService's per-line callback hook (`line_handler`), so plugins
just provide the parser:

```python
import shlex
from services.events import DaemonEventSource, WorkflowEvent

class StripeListenSource(DaemonEventSource):
    type = "stripe.listen"
    process_name = "stripe-listen"
    binary_name = ""                  # see "binary_name" note below
    workflow_namespace = "_stripe"
    install_hint = "https://stripe.com/docs/stripe-cli#install"
    credential = StripeCredential

    def build_command(self, secrets: Dict) -> str:
        # `shlex.quote` the binary so the path round-trips through
        # ProcessService's POSIX-mode `shlex.split` unchanged.
        binary = shlex.quote(str(stripe_cli_path() or "stripe"))
        return f"{binary} listen --forward-to ... --print-secret"

    async def has_credential(self) -> bool:
        # Override the default ``secrets["api_key"]`` gate when auth lives
        # outside MachinaOs (e.g. the CLI's own ~/.config/stripe/config.toml).
        return is_logged_in()

    def parse_line(self, stream: str, line: str) -> Optional[WorkflowEvent]:
        # called per-line for both stdout and stderr
        if stream == "stderr" and (m := WHSEC_RE.search(line)):
            asyncio.create_task(self._persist_secret(m.group(0)))
        return None
```

The base provides `start()` / `stop()` / `restart()` / `status()` /
lifecycle locking / pre-flight `shutil.which` check (skip via
`binary_name = ""` if the plugin resolves the binary itself).

**Output ingestion**: `DaemonEventSource` registers `self._on_line` as
the `line_handler` callback on `ProcessService.start()`. ProcessService
runs the single `stream.readline()` loop per stdout/stderr; on each
decoded line it writes the log file, broadcasts to the Terminal tab,
and invokes the callback — which calls `parse_line(stream, line)`. No
log-file tailing.

**Credential gate**: `start()` consults `await self.has_credential()`
before spawning. Default implementation tests `secrets["api_key"]`;
subclasses override for non-api-key auth (Stripe → `is_logged_in()`).

### Webhook verifiers

Drop-in HMAC schemes covering the major providers:

| Class | Header | Algorithm | Used by |
|---|---|---|---|
| `StripeVerifier` | `Stripe-Signature: t=…,v1=…` | HMAC-SHA256 over `t.body` | Stripe |
| `StandardWebhooksVerifier` | `webhook-id` / `webhook-timestamp` / `webhook-signature` | HMAC-SHA256 base64 over `id.ts.body` | Svix-backed providers (Resend, Clerk, Loops, …) |
| `GitHubVerifier` | `X-Hub-Signature-256: sha256=…` | HMAC-SHA256 over body | GitHub |
| `HmacVerifier` | configurable header + prefix | HMAC-SHA256 over body | generic fallback |

Each verifier raises `ValueError` on mismatch; `WebhookSource.handle`
catches it and returns HTTP 400.

### Wiring helpers

Two factory functions collapse the per-plugin `__init__.py` boilerplate
to four lines:

```python
# nodes/stripe/_handlers.py
from services.events import make_lifecycle_handlers, run_cli_command
from ._source import get_listen_source

WS_HANDLERS = make_lifecycle_handlers(
    prefix="stripe",
    source=get_listen_source(),
    extra={"stripe_trigger": handle_stripe_trigger},  # plugin-specific extras
)
# → registers stripe_connect / stripe_disconnect / stripe_reconnect /
#   stripe_status from the source's start/stop/restart/status methods +
#   the stripe_trigger handler the plugin owns.

# nodes/stripe/__init__.py
register_service_refresh(make_status_refresh(
    get_listen_source(),
    status_key="stripe",
    broadcast_type="stripe_status",
))
# → auto-reconnect on WS-client connect + mirror status into
#   broadcaster._status["stripe"] + broadcast.
```

### `run_cli_command` — generic CLI invocation

Used by ActionNodes that wrap a CLI tool. Resolves the binary on
PATH, optionally injects the credential's `api_key` via the
convention flag (`--api-key` by default), runs subprocess with
timeout, parses stdout as JSON, returns a uniform envelope:

```python
from services.events import run_cli_command

result = await run_cli_command(
    binary="stripe",
    argv=["customers", "create", "--email", "a@b.com"],
    credential=StripeCredential,
)
# {"success": bool, "result": parsed-or-None, "stdout": str,
#  "stderr": str, "error": str-or-None}
```

### Stripe — reference implementation

The Stripe plugin
([`server/nodes/stripe/`](../server/nodes/stripe/)) is the canonical
Wave 12 example: 540 LOC total / 258 executable, supervising a CLI
daemon, verifying signed webhooks, exposing both a TriggerNode
(`stripeReceive`) and a dual-purpose ActionNode + AI tool
(`stripeAction`). See [`stripe_service.md`](./stripe_service.md) for
the per-file walkthrough.

### CLI-managed auth pattern

Some plugins delegate auth to an external CLI tool that runs its own
OAuth flow and persists tokens in its own config file (Stripe →
`~/.config/stripe/config.toml`; future `gh auth login` →
`~/.config/gh/hosts.yml`; `gcloud auth login` →
`~/.config/gcloud/...`). Three reuse points let these plugins ship
without any node-specific code in the frontend or in core services:

1. **Marker-token write via the existing `auth_service.store_oauth_tokens`
   API.** Plugin writes synthetic strings (e.g. `"cli-managed"`) on
   login completion. The catalogue's existing per-provider `stored`
   check at
   [`routers/webhook.py:handle_get_credential_catalogue`](../server/routers/websocket.py)
   uses
   `auth_service.get_oauth_tokens(status_hook) is not None` —
   identical to Google's OAuth-callback path. The synthetic tokens
   exist purely to flip the existence check; the CLI owns the real
   auth.

2. **Generic `credential_catalogue_updated` broadcast.** The plugin
   emits this event after every state change. The frontend's
   existing handler in
   [`WebSocketContext.tsx`](../client/src/contexts/WebSocketContext.tsx)
   (line 671) invalidates the catalogue query; the modal refetches
   and re-renders. **No new broadcast type, no Zustand entry, no
   `case '<provider>_status'`.** Frontend has zero references to any
   CLI-managed plugin's name.

3. **`OAuthPanel.tsx` `connected` fallback.** The panel reads
   `useProviderStatus(config.statusHook)` for legacy hook-driven
   providers and falls back to `config.stored` (the catalogue's
   authoritative flag) for everything else:

   ```tsx
   const connected = status ? !!status.connected : !!config.stored;
   ```

   Generic — no provider names anywhere. Future CLI-managed plugins
   inherit correct connection-indicator behaviour automatically.

**Auto-installer pattern.** Plugins wrapping a CLI binary that may
not be on the user's `PATH` ship a `_install.py` exposing a single
async helper:

```python
# server/nodes/<provider>/_install.py
_VERSION = "1.40.9"  # pinned
_ASSETS = {
    ("Windows", "AMD64"):  ("…_windows_x86_64.zip", "zip", "stripe.exe"),
    ("Linux",   "x86_64"): ("…_linux_x86_64.tar.gz", "tar", "stripe"),
    …
}

async def ensure_<cli>_cli() -> Path:
    # 1. cached path → 2. shutil.which(...) → 3. workspace cache →
    # 4. fresh download from GitHub releases.
```

The plugin's `DaemonEventSource` subclass overrides `start()` to
`await ensure_<cli>_cli()` first, sets `binary_name = ""` so the
framework's pre-flight `shutil.which` check is skipped, and uses
the resolved path inside `build_command`. The same shape suits any
project that publishes pre-built binaries via GitHub releases.

### Integration points (no core edits per plugin)

| Concern | Framework integration | Plugin contribution |
|---|---|---|
| HTTP webhook ingress | `routers/webhook.py` consults `WEBHOOK_SOURCES` registry | `register_webhook_source(MySource())` |
| Event dispatch into workflows | `event_waiter.dispatch(source.type, event)` from `WebhookSource.handle` | provider-specific `shape()` returning `WorkflowEvent` |
| Trigger waiting + filtering | `WebhookTriggerNode.build_filter` (CloudEvents glob) | optional `_extra_filter(params)` |
| Daemon lifecycle | `DaemonEventSource.start/stop/restart` via `ProcessService` | `build_command(secrets)` + `parse_line(stream, line)` (subscribed via `ProcessService.start(line_handler=...)` — no log-file tailing) |
| Daemon credential gate | `DaemonEventSource.start` consults `await self.has_credential()` before spawning | optional override when auth is non-api-key (Stripe → `is_logged_in()`); default tests `secrets["api_key"]` |
| Lifecycle WebSocket commands | `make_lifecycle_handlers(prefix, source, extra=…)` | provider-specific extra handlers |
| Status refresh on WS connect | `make_status_refresh(source, status_key, broadcast_type)` | nothing — auto-derived from source |
| CLI subprocess invocation | `run_cli_command(binary=…, argv=…, credential=…)` | nothing — credential injection is automatic |
| Credentials Modal panel | `server/config/credential_providers.json` (read by `services.credential_registry`) | one provider entry: name, category, color, `kind: "oauth"`, `icon_ref`, `status_hook`, `ws.{login,logout,status}` handler names, fields list, instructions string. Frontend modal renders it automatically — no React file edits. |
| AI tool surface | `services/ai.py` `DEFAULT_TOOL_NAMES` + `DEFAULT_TOOL_DESCRIPTIONS` | one row per dual-purpose ActionNode mapping `<nodeType>` → `<snake_case_of_node_type>` |
| Skill (LLM teaching markdown) | `server/skills/<agent>/<skill-name>/SKILL.md` (auto-discovered by `SkillLoader`) | the markdown itself, plus the linkage in `visuals.json` (`"<nodeType>": { ..., "skill": "<skill-name>" }`) |
| Connection state surfaced to the modal (CLI-managed auth) | `auth_service.store_oauth_tokens(provider, "cli-managed", "cli-managed")` + `StatusBroadcaster.broadcast_credential_event` (CloudEvents v1.0 envelope wrapping `WorkflowEvent`; locked by `tests/credentials/test_credential_broadcasts.py`) | `_mark_logged_in` / `_mark_logged_out` helper pair; one `broadcaster.broadcast_credential_event("credential.oauth.connected", provider="<id>")` after login and `…disconnected` after logout. Same shape Twitter / Google logout use. |
| Auto-install of an external CLI binary | `_install.py` with `ensure_<cli>_cli()` + GitHub-releases asset map | pinned `_VERSION` constant, `(system, machine) -> (asset, kind, member)` table, and an override on `DaemonEventSource.start()` that `await`s the helper before `super().start()` (also set `binary_name = ""` to skip the framework's `shutil.which` pre-check) |

### Tool / skill / visuals naming contract

Three coordinates have to agree for both the LLM tool dispatcher and
the skill icon resolver to find their target:

| Place | Form | Example |
|---|---|---|
| Plugin node `type` | camelCase | `stripeAction` |
| `visuals.json` key | matches node `type` (camelCase) | `"stripeAction": { "icon": "asset:stripe", ... }` |
| `services/ai.py` `DEFAULT_TOOL_NAMES[<type>]` | snake_case of node type | `"stripeAction": "stripe_action"` |
| Skill `allowed-tools` token | matches the LLM tool name above | `allowed-tools: "stripe_action"` |

`SkillLoader._parse_skill_metadata` runs each `allowed-tools` token
through snake → camel and looks the result up in `visuals.json`.
Mismatches silently break icon resolution — a skill with
`allowed-tools: "stripe_cli"` would convert to `stripeCli` and find
nothing in `visuals.json` even though the node `stripeAction` is
registered. Stick to `<snake_case_of_node_type>` unless you're
prepared to maintain alias entries in `visuals.json`. See
[`server/skills/GUIDE.md → Tool naming`](../server/skills/GUIDE.md#tool-naming--snake_case--camelcase-contract).

### When to use the framework

Use it for any new event-source plugin:

- Signed webhooks → `WebhookSource` + `WebhookTriggerNode` + a verifier
  from `services.events.verifiers` (or contribute a new one).
- CLI daemons → `DaemonEventSource`.
- API polling → `PollingEventSource`.
- Pure HTTP push without a verifier → `PushEventSource` directly.

Use plain `TriggerNode` only for in-process / synthetic events
(`taskTrigger`, `chatTrigger`) that don't have an external source.

### Phase rollout

- **Phase 1 (shipped):** framework lands, Stripe plugin built natively
  on top. Existing 9 trigger plugins keep using their pre-framework
  paths (the `event_waiter.dispatch(event_type, Dict)` shim accepts
  legacy `Dict` payloads alongside `WorkflowEvent`).
- **Phase 2 (planned):** migrate `gmailReceive`, `emailReceive`,
  `twitterReceive` to `PollingEventSource`. Net delete: ~30 LOC each.
- **Phase 3 (planned):** migrate `telegramReceive` and
  `whatsappReceive` to `DaemonEventSource`. Net delete: ~50 LOC each.
- **Phase 4 (planned):** sunset the legacy `Dict`-payload shim once
  every trigger uses `WorkflowEvent`.
