# `server/nodes/` — plugin cookbook

**One file = one node.** Drop a Python file in the right subfolder and
it auto-registers at import time. No other code needs to change.

Full reference: [docs-internal/plugin_system.md](../../docs-internal/plugin_system.md).

---

## Five-minute recipe

```python
# server/nodes/search/acme_search.py
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Literal, Optional

from services.plugin import (
    ActionNode, ApiKeyCredential, NodeContext, Operation, TaskQueue,
)


# 1. Credential — inline here (single-use) or move to
#    server/nodes/search/_credentials.py if 2+ plugins will share it.
class AcmeCredential(ApiKeyCredential):
    id = "acme"
    display_name = "Acme Search"
    category = "Search"
    key_name = "X-Acme-Token"
    key_location = "header"


# 2. Params — user-visible config (UI + LLM tool schema).
#    snake_case throughout — field names = JSON Schema keys = UI param keys.
class AcmeParams(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=10, ge=1, le=100)
    model_config = ConfigDict(extra="ignore")


# 3. Output — runtime result shape.
class AcmeOutput(BaseModel):
    results: List[dict] = Field(default_factory=list)
    count: int = 0


# 4. The node. (Icon + color are NOT declared here — add an entry
#    {"acmeSearch": {"icon": "asset:acme", "color": "#abcdef"}} to
#    server/nodes/visuals.json instead. BaseNode resolves both via
#    the central handler at server/nodes/_visuals.py.)
class AcmeSearchNode(ActionNode):
    type = "acmeSearch"
    display_name = "Acme Search"
    group = ("search", "tool")
    description = "Search Acme's web index"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    credentials = (AcmeCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    Params = AcmeParams
    Output = AcmeOutput

    @Operation("search")
    async def search(self, ctx: NodeContext, params: AcmeParams) -> AcmeOutput:
        async with ctx.connection("acme") as conn:
            resp = await conn.get(
                "https://api.acme.com/search",
                params={"q": params.query, "limit": params.max_results},
            )
            resp.raise_for_status()
            data = resp.json()
        hits = data.get("hits", [])
        return AcmeOutput(results=hits, count=len(hits))
```

On server restart this node is:
- in the Component Palette under `search` and `tool`
- runnable via the run button (REST API worker pool)
- invokable by any AI Agent connected to its `output-main`
- emitted as NodeSpec at `GET /api/schemas/nodes/acmeSearch/spec.json`

No other edits. Zero frontend changes.

---

## Folder map

Match the palette group. Current folders (see
[`groups.py`](./groups.py) for the canonical list):

```
agent/       — AI agents (ai_agent, chat_agent, 13 specialized, team leads)
model/       — LLM chat models (openai, anthropic, gemini, …)
android/     — Android device services
google/      — Google Workspace (gmail / calendar / drive / sheets / …)
twitter/     — Twitter/X (send / search / user / receive)
telegram/    — Telegram bot (send / receive)
whatsapp/    — WhatsApp (send / db / receive)
social/      — Unified social (send / receive)
email/       — IMAP/SMTP via Himalaya CLI
search/      — Web search APIs (brave / serper / perplexity / duckduckgo)
scraper/     — Apify / Crawlee
document/    — RAG pipeline (scrape / download / parse / chunk / embed / store)
code/        — Python / JS / TS executors
filesystem/  — file_read / file_modify / shell / fs_search
proxy/       — Residential proxy (request / config / status)
location/    — Google Maps (create / locations / nearby places)
chat/        — chatSend / chatHistory
text/        — textGenerator / fileHandler
scheduler/   — timer / cron_scheduler
trigger/     — Generic triggers (webhook / task / chat)
tool/        — calculatorTool / currentTimeTool / writeTodos / taskManager
utility/     — console / httpRequest / webhookResponse / processManager / team_monitor
workflow/    — start
skill/       — simpleMemory / masterSkill
browser/     — browser (agent-browser CLI)
```

---

## Shared helpers (one per domain)

Domains with 2+ plugins share a `_base.py` (or `_<name>.py`) in the
folder. If you're adding a new node in one of these domains, reuse
these first before writing new code:

| Folder | Helper | Purpose |
|---|---|---|
| `agent/` | `_inline.prepare_agent_call` | One-shot pre-dispatch for every agent (memory + skill + tool + teammate collection) |
| `agent/` | `_specialized.SpecializedAgentBase` | Base for 13 specialized agents |
| `model/` | `_base.ChatModelBase` | 9 chat models inherit → same `@Operation("chat")` body that calls `ai_service.execute_chat` |
| `android/` | `_base.AndroidServiceBase` | 16 Android services inherit; payload translation + `SERVICE_ID_MAP` lives on this base |
| `android/` | `_base.execute_android_toolkit` / `execute_android_service_tool` | AI-tool dispatchers — called from `services/handlers/tools.py` for the toolkit aggregator + direct service tool branches |
| `code/` | `_base.CodeExecutorBase` + `_nodejs.NodeJSClient` | Python/JS/TS executors |
| `google/` | `_base.build_google_service` / `track_google_usage` | 7 Google plugins (OAuth + API) |
| `google/` | `_gmail.fetch_email_details` / `mark_email_as_read` | gmail + gmail_receive |
| `twitter/` | `_base.call_with_retry` / `format_tweet` / `sync_search_recent` | 4 twitter plugins (XDK + refresh) |
| `whatsapp/` | `_base.*` | whatsappSend / whatsappDb (RPC dispatch via `services/whatsapp_service.py`) |
| `social/` | `_base.*` | socialReceive / socialSend |
| `proxy/` | `proxy_config.execute_proxy_config` | 10-operation matrix; called by both `ProxyConfigNode.dispatch` and `tools.py`'s AI-tool branch |

Cross-domain infrastructure lives in `services/plugin/` (e.g.
`edge_walker.py` for agent connection discovery, `routing.py` for
declarative REST).

---

## Shared credentials

Credentials live **in each node folder's `_credentials.py`** — same
"one domain owns its own code" principle as `_base.py`. Import from
the sibling file via relative path:

```python
# inside server/nodes/google/gmail.py
from ._credentials import GoogleCredential               # shared with 6 siblings

# inside server/nodes/model/openai_chat_model.py
from ._credentials import OpenAICredential               # one of 10 LLM creds

# inside server/nodes/twitter/twitter_send.py
from ._credentials import TwitterCredential              # shared with 3 siblings
```

| Folder | `_credentials.py` contents | Plugins |
|---|---|---|
| `nodes/google/` | `GoogleCredential` (OAuth2, 7 Workspace scopes union) | gmail, calendar, drive, sheets, tasks, contacts, gmailReceive |
| `nodes/location/` | `GoogleMapsCredential` (API key via `?key=`) | gmaps_create / gmaps_locations / gmaps_nearby_places |
| `nodes/twitter/` | `TwitterCredential` (OAuth2 + PKCE) | twitter_send / _search / _user / _receive |
| `nodes/telegram/` | `TelegramCredential` (bot token + owner chat id) | telegram_send / _receive |
| `nodes/scraper/` | `ApifyCredential` (Bearer) | apify_actor |
| `nodes/model/` | 10 LLM classes (`OpenAI / Anthropic / Gemini / OpenRouter / Groq / Cerebras / DeepSeek / Kimi / Mistral / Xai`) | 9 chat models |
| `nodes/search/` | `BraveSearch / Serper / Perplexity` inlined in each plugin file | single-use per plugin |

Declare inline only when genuinely single-use (see
`nodes/search/brave_search.py` for the inline pattern). Declare in
`_credentials.py` when the folder has 2+ plugins that share auth.

Auto-discovery is automatic — when the nodes walker imports a plugin
file, the plugin's `from ._credentials import X` triggers the
credential module import, which registers every `Credential` subclass
into `CREDENTIAL_REGISTRY` before the plugin class body runs. No
wiring beyond the import statement.

---

## Contract invariants

`server/tests/test_plugin_contract.py` enforces 16 invariants on
every plugin. Common ones you'll trip:

- `type` / `display_name` / `group` must be non-empty.
- `Params` + `Output` must be Pydantic `BaseModel` subclasses.
- Every `@Operation` name unique per class.
- Every declared credential class must be registered
  (happens automatically via `__init_subclass__` — just import it).
- `routing=...` requires `credentials` declared.
- `task_queue` ∈ `TaskQueue.ALL` (`rest-api` / `ai-heavy` / `code-exec`
  / `triggers-poll` / `triggers-event` / `android` / `browser` /
  `messaging` / `machina-default`).
- Tool schemas (`usable_as_tool=True` or `ToolNode`) — no `$defs`,
  no `$ref` (LLM-compat).

Run: `pytest server/tests/test_plugin_contract.py -q`.

---

## Self-contained plugin folders (richer plugins)

Most plugins are a single file in the right folder — that's the
default. But some own a long-lived service (bot connection, SDK
session, subprocess), credentials-modal Connect/Disconnect commands,
trigger pre-checks, etc. Those graduate to a **self-contained folder
shape** so nothing telegram-specific (or whatsapp-specific, twitter-
specific, …) lives outside that folder.

**Reference: `server/nodes/telegram/`.** Read it first before adding a
similar plugin.

```
server/nodes/telegram/
├── __init__.py          # imports + 5 register_* calls covering 5 registries (zero logic)
├── _credentials.py      # TelegramCredential subclass
├── _service.py          # singleton bot lifecycle (connect / send / poll)
├── _handlers.py         # WS_HANDLERS dict (telegram_connect, …)
├── _filters.py          # build_telegram_filter (event_waiter filter)
├── _refresh.py          # WS-connect refresh + trigger precheck
├── telegram_send.py     # ActionNode + AI tool
└── telegram_receive.py  # TriggerNode
```

### Six generic registries to plug into

Telegram's `__init__.py` is the canonical wiring example. Adding any
of these concerns to your plugin is one `register_*` call from your
package's `__init__.py` — the consumer never imports your folder.

| Concern | Where to register | What it does |
|---|---|---|
| Credentials-modal WebSocket commands | `services.ws_handler_registry.register_ws_handlers({"<type>": handler})` | Adds `<type>` to the central WS dispatcher (no router edits) |
| FastAPI router (Wave 11.I) | `services.ws_handler_registry.register_router(router, name="<name>")` | Plugin's HTTP router mounts via the plugin loop in `main.py`; sibling concern in the same file as `register_ws_handlers` |
| Event-trigger filter | `services.event_waiter.register_filter_builder(node_type, fn)` | Plugs into `FILTER_BUILDERS` for `event_waiter.build_filter()` |
| Trigger pre-execution check | `services.event_waiter.register_trigger_precheck(node_type, async_fn)` | Generic `triggers.py` handler runs `run_trigger_precheck` before entering the wait loop |
| Service-status refresh on WS connect | `services.status_broadcaster.register_service_refresh(async_callback)` | Callback runs once per `_refresh_all_services` cycle |
| Output schema | `services.node_output_schemas.register_output_schema(node_type, ModelClass)` | Avoids declaring a duplicate `Output` class in the central schema file |

All six are idempotent (same callable / class for the same key is a
no-op; conflicts raise `ValueError`).

### Credential validation (Wave 11.I)

The credential-validator dispatch is a sibling concern, handled by the
existing `services/plugin/credential.py:Credential` base class. Your
`Credential` subclass overrides `_probe(api_key) -> ProbeResult` (or,
in rare cases like local-LLM 2-storage, the whole `validate(data)
-> dict` classmethod). Maps, Apify, all 9 cloud LLM providers, and
both local-LLM providers (Ollama / LM Studio) all dispatch through the
same scaffold — no `_SPECIAL_PROVIDER_VALIDATORS` dict in
`routers/websocket.py`.

### When NOT to use this shape

Don't create `_service.py` / `_handlers.py` siblings unless the plugin
genuinely owns:

- A long-lived stateful object (bot / device / session / subprocess).
- Credentials-modal lifecycle commands beyond the standard
  Save / Load / Delete.
- Trigger pre-checks that depend on plugin-specific service state.
- A status refresh that runs on WebSocket connect.

A single search node, an HTTP-only REST plugin, a code executor — all
of those stay one file. Adding the folder ceremony for them is just
overhead.

### Wire format is stable across moves

Frontend identifies plugin commands by **WebSocket message type
strings** (`telegram_connect`, `telegram_status`, …) — never by Python
module paths. As long as your `WS_HANDLERS` keys stay the same, you
can rearrange your `nodes/<group>/` folder freely with zero frontend
changes. The telegram refactor moved 754 lines without touching
`client/`.

Full reference: [docs-internal/plugin_system.md → "Self-contained plugin folders"](../../docs-internal/plugin_system.md#self-contained-plugin-folders).

---

## Common pitfalls

- **Don't edit `server/nodes/__init__.py`** — it's a pure auto-discovery
  walker. Adding a new folder doesn't need edits either; `pkgutil` finds
  subpackages automatically.
- **Don't instantiate services directly.** Use the canonical lazy
  helpers in `services.plugin.deps`:
  `from services.plugin.deps import get_auth_service, get_database, get_cache, get_ai_service, get_text_service, get_maps_service, get_android_service`.
  These resolve the singleton from the DI container at call time
  (test monkeypatching depends on call-time lookup — never memoise).
- **Don't call `auth_service.get_api_key(...)` from plugins.** Declare
  a `Credential` subclass; the `Connection` facade / service layer
  resolves tokens.
- **Pydantic `extra="ignore"` is the default for Params** — extra fields
  silently drop. Use `extra="allow"` if the node passes unknown fields
  through to a handler.
- **snake_case everywhere.** Field names, JSON Schema keys,
  `displayOptions.show` keys, and handler dict access all use snake_case.
  No `alias="camelName"`, no `populate_by_name=True`, no
  `model_dump(by_alias=True)`. `displayOptions.show["driver_field"]` must
  match a property name in the same `Params` class — the frontend's
  visibility evaluator looks up that exact key.
- **LLM tool schemas must be flat.** If your Params uses nested
  Pydantic models or `Union`, the LLM-schema emission will add `$defs`
  and fail the invariant. Keep tool-facing Params flat; move nested
  types to `Output` instead.
- **Collapse advanced options with `group="..."`.** Tag rarely-tuned
  fields with `json_schema_extra={"group": "options"}` and they get
  lifted into a collapsible "Options" collection in the parameter
  panel (adapter-side). Declare custom display name / placeholder via
  `model_config = ConfigDict(json_schema_extra={"groups": {...}})`.
  Main-entry fields stay top-level. Full spec in
  [`docs-internal/plugin_system.md`](../../docs-internal/plugin_system.md).
- **Never declare `api_key` as a Params field.** Credentials live in
  the credentials DB via `ApiKeyCredential` / `OAuthCredential`
  subclasses and auto-inject at execution time. Plugins that need the
  injected key read `ctx.raw["_raw_parameters"]["api_key"]` — it's
  stashed before Pydantic validation strips it.
- **`isConfigNode` is auto-derived — don't declare it.** Plugins
  whose `group` tuple contains `"memory"` or `"tool"` automatically
  export `uiHints.isConfigNode: True` (set by `_derive_auto_ui_hints`
  in `services/plugin/base.py`). The flag tells the frontend that the
  node's parameter panel inherits its parent's main inputs instead
  of showing direct upstream connections. If you genuinely want to
  opt out, declare `ui_hints = {"isConfigNode": False}` — explicit
  always wins via `dict.update`. Adding a new auto-derivation rule
  goes in `_derive_auto_ui_hints`, not in individual plugins; new
  uiHint flags must also be added to `INodeUIHints` in
  `client/src/types/INodeProperties.ts` and to the `known` set in
  `server/tests/test_node_spec.py::test_ui_hints_only_carry_known_flags`.

---

## Waves-at-a-glance

Added in Wave 11 (Mar 2026):

- **11.A**: `BaseNode` / `ActionNode` / `TriggerNode` / `ToolNode`
  + `@Operation` + `Routing` + `Connection` + `Credential`.
- **11.B/C**: 111 plugins migrated, folder layout adopted.
- **11.D**: Per-domain handler bodies inlined.
- **11.E**: 18 declarative credentials, 29 plugins wired.
- **11.E.1**: Credentials moved into per-domain `_credentials.py`
  (no central `server/credentials/` dir).
- **11.E.2**: Dead-code sweep — 2 broken imports fixed, 13 dead
  dispatch branches stripped, duplicate proxy handler removed,
  `routers/whatsapp.py` (misnamed) → `services/whatsapp_service.py`.
- **11.E.3**: 14 handler files deleted, last per-domain bodies
  inlined into plugins; `handle_ai_agent` / `handle_chat_agent`
  retired in favour of `BaseNode.execute()` via the node registry.
- **11.E.4**: Last `tools.py` movables relocated to their domains
  (proxyConfig matrix → `nodes/proxy/proxy_config`, Android tool
  dispatch → `nodes/android/_base`).
- **11.F**: Per-plugin Temporal activities, 9 worker pools with tuned
  concurrency.
- **11.G** (this doc): Cookbook.

End state: `services/handlers/` is **4 files / 1.1K LOC** (down from
16 / 12.8K). Only cross-cutting orchestration remains there:
`tools.py` (AI-tool dispatch + agent delegation), `triggers.py`
(generic event-trigger handler), `google_auth.py` (shared OAuth
helper), and a 23-line package docstring.

Plan + full migration history:
[plugin_system.md](../../docs-internal/plugin_system.md).
