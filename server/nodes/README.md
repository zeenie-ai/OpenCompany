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


# 4. The node. (Icon + color are NOT declared on the class.
#    Icon: for anything with a recognisable brand, drop the mark as
#       `icon.svg` in THIS folder, or `icon_<nodeType>.svg` per node
#       type in a multi-node folder. Brand artwork is what makes a node
#       identifiable at canvas size and depends on nothing external.
#       For generic utility nodes, meta.json can instead reference a
#       library glyph: `"icons": {"<nodeType>": "lucide:Send"}` (use
#       the package's ExportName — `CheckCheck`, NOT `check-check`).
#       That is the weaker option: the name can be renamed or removed
#       upstream and the node then renders nothing, and the glyph is
#       monochrome currentColor line art. A file always beats a
#       meta.json ref, so don't ship both. `server/nodes/visuals.json`
#       is the legacy central registry for plugins with neither.
#    Color: create `meta.json` with `{"color": "#abcdef"}` in this folder.
#    BaseNode._metadata_dict resolves both at registration time via the
#    central handler at server/nodes/_visuals.py.)
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
agent/       — AI agents (ai_agent, chat_agent + specialized/variant folders incl. 2 team leads,
               CLI agents (claude_code, codex, rlm) and Vertex agents; SSOT: AI_AGENT_TYPES
               in server/constants.py + the folder glob)
model/       — LLM chat models (openai, anthropic, gemini, …)
android/     — Android device services
google/      — Google Workspace (gmail / calendar / drive / sheets / …)
twitter/     — Twitter/X (send / search / user / receive)
telegram/    — Telegram bot (send / receive)
discord/     — Discord bot (send / action / receive / interaction).
               Multi-account: _accounts.py maps an account id onto the
               session_id credential scope; nothing else knows about it.
whatsapp/    — WhatsApp (send / db / receive)
social/      — Unified social (send / receive). Names no platform: each
               plugin registers a _social.py adapter and owns the mapping
               onto its own parameter shape.
email/       — IMAP/SMTP via Himalaya CLI
search/      — Web search APIs (brave / serper / perplexity / duckduckgo)
scraper/     — Apify / Crawlee / TikHub (SDK-backed, flattened like a
               CLI: list_endpoints + call over resource.method ids)
document/    — RAG pipeline (scrape / download / parse / chunk / embed / store)
code/        — Python / Monty (sandboxed Python) / JS / TS executors
filesystem/  — file_read / file_modify / shell / fs_search / gallery
               (gallery is the workspace file explorer; deliberately NOT
                usable_as_tool — it carries destructive operations, and
                fs_search + file_modify already cover what an agent needs)
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
stripe/      — Stripe (CLI passthrough action + signed-webhook trigger)
vercel/      — Vercel (CLI deploy / inspect / list / custom passthrough)
github/      — GitHub (gh CLI: clone / PRs / issues / custom; palette group "vcs")
cloudflare/  — Cloudflare (cf CLI: zones / DNS / GraphQL analytics / custom; palette group "deployment")
gcloud/      — Google Cloud (gcloud CLI: projects / Compute Engine / Cloud Run / Cloud Storage / custom; palette group "deployment")
speech/      — Provider-abstracted text_to_speech / speech_to_text (palette group "language").
               One node per direction with a `provider` dropdown, not one node per vendor. Owns
               its own protocol / registries / dispatch / per-vendor modules — see below.
translate/   — Provider-abstracted translate / transliterate / detect_language (palette group
               "language"). Same shape as speech/ but THREE registries, one per capability:
               DeepL translates only, Sarvam and LLM-backed providers do all three.
               (Sarvam's chat model remains a separate plugin at model/sarvam_chat_model/;
               nodes/sarvam/ itself was retired — every capability it served is now a provider.)
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
| `model/` | `_base.ChatModelBase` | 12 chat models inherit → same `@Operation("chat")` body that calls `ai_service.execute_chat` |
| `speech/` + `translate/` | `_config` / `_registry` / `_unifier` / `_providers/` | The multi-vendor shape. Capability data is JSON (`services/plugin/capabilities.CapabilityConfig`), registration is `services/provider_registry`, and each `_providers/<vendor>.py` owns that vendor's auth scheme, request transport and response shape |
| `android/` | `_base.AndroidServiceBase` | 16 Android services inherit; payload translation + `SERVICE_ID_MAP` lives on this base |
| `android/` | `_base.execute_android_service_tool` | AI-tool dispatcher — called from `services/handlers/tools.py` for direct service tools (the `androidTool` aggregator + `execute_android_toolkit` were retired) |
| `code/` | `_base.CodeExecutorBase` + `_nodejs.NodeJSClient` | Python/JS/TS executors; `monty_executor/` is sandboxed Python via `pydantic-monty` (enforced limits + opt-in capabilities) |
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
from ._credentials import OpenAICredential               # one of 10 cloud LLM creds

# inside server/nodes/twitter/twitter_send.py
from ._credentials import TwitterCredential              # shared with 3 siblings
```

| Folder | `_credentials.py` contents | Plugins |
|---|---|---|
| `nodes/google/` | `GoogleCredential` (OAuth2, 7 Workspace scopes union) | gmail, calendar, drive, sheets, tasks, contacts, gmailReceive |
| `nodes/location/` | `GoogleMapsCredential` (API key via `?key=`) | gmaps_create / gmaps_locations / gmaps_nearby_places |
| `nodes/twitter/` | `TwitterCredential` (OAuth2 + PKCE) | twitter_send / _search / _user / _receive |
| `nodes/telegram/` | `TelegramCredential` (bot token + owner chat id) | telegram_send / _receive |
| `nodes/discord/` | `DiscordBotCredential` (bot token; overrides `inject()` because Discord uses `Bot <token>`, not the inherited `Bearer `) + `DiscordUserCredential` (OAuth2 user context, separate id so connecting a user never overwrites the bot) | discord_send / _action / _receive / _interaction |
| `nodes/scraper/` | `ApifyCredential` (Bearer, SDK probe) + `TikHubCredential` (Bearer, declarative httpx probe against `tikhub/user/get_user_info` — kept SDK-free so the modal validates even if the `tikhub` import fails) | apify_actor / tikhub_action |
| `nodes/model/` | 13 LLM credential classes: 11 cloud (`OpenAI / Anthropic / Gemini / OpenRouter / Groq / Cerebras / DeepSeek / Kimi / Mistral / xAI / Sarvam`) plus Ollama / LM Studio | 12 chat models (xAI has no standalone chat-model node) **plus the 5 `nodes/sarvam/` service nodes**, which import `SarvamCredential` from here — one stored key serves Sarvam's OpenAI-compatible chat endpoint *and* its `api-subscription-key` REST APIs |
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

`server/tests/test_plugin_contract.py` enforces the contract
invariants on every plugin (live count via `pytest --collect-only`).
Common ones you'll trip:

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
- **`Output` is enforced at runtime.** Dict results from operations are
  validated against the declared `Output` model and dumped with
  `model_dump(mode="json", exclude_unset=True)` (FastAPI
  `response_model` semantics — see `BaseNode._serialize_result`). A
  type mismatch in a declared field produces an `OutputValidationError`
  envelope. Prefer returning the `Output` instance directly; keep
  `extra="allow"` + `Optional` fields so context keys pass through.

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
├── __init__.py          # imports + register_* calls covering seven registries (zero logic)
├── _credentials.py      # TelegramCredential subclass
├── _service.py          # singleton bot lifecycle (connect / send / poll)
├── _handlers.py         # WS_HANDLERS dict (telegram_connect, …)
├── _filters.py          # build_telegram_filter (event_waiter filter)
├── _refresh.py          # WS-connect refresh + trigger precheck
├── _events.py           # typed CloudEvents factory + broadcast_telegram_status
├── telegram_send.py     # ActionNode + AI tool
└── telegram_receive.py  # TriggerNode
```

### Variant: one node, many vendors

Telegram owns *one* vendor's surface. The other shape is a node that must
speak to *several* vendors interchangeably — one canvas node whose
`provider` parameter picks the backend. **Reference:
`server/nodes/speech/`.**

```
server/nodes/speech/
├── __init__.py          # register_output_schema + register_option_loader (zero logic)
├── _protocol.py         # vendor-neutral requests / results / errors + two Protocols
├── _registry.py         # ONE REGISTRY PER DIRECTION, over services/provider_registry
├── _config.py           # reads server/config/speech_defaults.json
├── _unifier.py          # dispatch + typed-error -> NodeUserError, one catch site
├── _providers/          # _http.py + one module per vendor, each self-registering
├── _credentials.py      # only the vendors no other plugin already declares
├── _base.py             # shared node helpers (credential resolve, input read, billing)
├── _option_loaders.py   # provider-aware dropdown loaders
├── text_to_speech.py    # the two nodes
└── speech_to_text.py
```

Four ideas worth stealing wholesale:

1. **Registry membership is the capability.** Two registries, one per
   direction, and the node's provider enum *is* `tts_providers()`. A
   synthesis-only vendor never appears in the transcription dropdown, and
   there is no `supports_x` flag to fall out of sync with reality.
2. **Capabilities are JSON, not Python.** `speech_defaults.json` holds
   per-provider and per-model values (`{"whisper-1": [...], "_default": [...]}`),
   resolved exact → longest-prefix → `_default`. Boolean flags default
   **permissive**, so forgetting to declare one never silently disables a
   working feature. No shared code branches on a vendor name — the ban
   `test_plugin_shape` implies is satisfied structurally.
3. **Vendor divergence lives in the vendor module.** Auth scheme, whether
   options ride the query string or the body, and how the response is
   shaped are all per-vendor facts. Keep them behind the neutral request /
   result types and central code stays vendor-blind.
4. **Reuse credentials, don't redeclare them.** `_credentials.py` holds only
   the vendors nothing else owns; the rest are imported
   (`from ..model._credentials import OpenAICredential`). Two classes with
   the same `id` collide in `CREDENTIAL_REGISTRY` at import time.

See [Multi-credential nodes](../../docs-internal/plugin_system.md#multi-credential-nodes)
for the `ctx.connection(id)` contract and the `routing=` trap that comes with it.

### Seven generic registries to plug into

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
| Agent Context descriptor | `services.plugin.edge_walker.register_agent_context_builder(async_fn)` | Turns a node connected on `input-context` into the descriptor the agent runtime consumes. The framework walks the edge but knows nothing about the descriptor's shape — see `nodes/context/_descriptor.py` |

All seven are idempotent (same callable / class for the same key is a
no-op; conflicts raise `ValueError`).

These seven are the core self-contained-folder set; newer concerns have
their own generic `register_*` entrypoints in the same spirit —
`services.events.register_webhook_source`,
`services.ws_handler_registry.register_option_loader` /
`register_oauth_callback_path`,
`services.deployment.canary_registry.register_canary_trigger_type`,
`services.deployment.poll_registry.register_poll_coroutine_factory`,
`services.plugin.edge_walker.register_master_skill_expander`, and
`services.plugin.social_provider_registry.register_social_send_handler`.

**Register, don't edit the consumer.** Never edit `event_waiter.py`,
`status_broadcaster.py`, or `routers/websocket.py` to add a plugin's
handler / filter / refresh — call the matching `register_*` from your
plugin's `__init__.py` instead. Likewise never import a plugin folder
from `routers/`, `services/`, or another `nodes/` subfolder; the
consumer reaches your code through the registry, never by import path.

### Credential validation (Wave 11.I)

The credential-validator dispatch is a sibling concern, handled by the
existing `services/plugin/credential.py:Credential` base class. Your
`Credential` subclass overrides `_probe(api_key) -> ProbeResult` (or,
in rare cases like local-LLM 2-storage, the whole `validate(data)
-> dict` classmethod). Maps, Apify, all 10 cloud LLM providers, and
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
- **A custom `tool_name` breaks the paired skill's icon unless you add a
  `visuals.json` alias.** The skill icon resolver
  (`SkillLoader._parse_skill_metadata`) maps each SKILL.md
  `allowed-tools` token — which is the LLM tool name — through
  snake→camel into a `visuals.json` lookup. Keep `tool_name =
  "<snake_case_of_node_type>"` and this just works. If you deliberately
  pick a short brand name (`github` for `githubAction`, `vercel` for
  `vercelAction`), you MUST add a `visuals.json` entry keyed by that
  tool name carrying the same icon plus the plugin's `meta.json` color:
  `"github": {"icon": "lobehub:Github", "color": "#8250df"}`. Otherwise
  the Master Skill row renders a blank icon and no color. Locked by
  `tests/test_skill_icon_resolution.py` (every shipped skill must
  resolve a non-empty icon).
- **LLM tool schemas must be flat.** If your Params uses nested
  Pydantic models or `Union`, the LLM-schema emission will add `$defs`
  and fail the invariant. Keep tool-facing Params flat; move nested
  types to `Output` instead.
- **Never name a Params field `model` or `api_key` on a node that also has
  a `provider` field.** An effect in
  [`ParameterRenderer.tsx:866`](../../client/src/components/ParameterRenderer.tsx#L866)
  keys on those two literal names: when a sibling `provider` field is
  present it overwrites `model` with the *chat-model* list and clears
  `api_key`. It never checks that the provider is an LLM provider, so
  `provider: "elevenlabs"` still triggers it and the field is wiped the
  moment the user picks a vendor. Prefix instead — `tts_model`,
  `stt_model`. Other reserved sibling names with name-based magic in that
  file: `parameters`, `message_type`, `group_id`, `group_name`,
  `channel_jid`, `sender_number`, `sender_name`, `session_id`,
  `service_id`, `action`.
- **The parameter panel stores `""` for every cleared field, whatever the
  declared type.** Harmless against `str`; a hard validation error against
  `Optional[float]`, `bool` or `Dict[str, Any]` — a freshly-dropped node fails
  on its own defaults with `Invalid parameters: Input should be a valid
  dictionary`. Add a `@model_validator(mode="before")` that drops blank
  strings for fields that cannot hold one, and coerce object fields from a
  JSON string (the panel has no object widget, so a dict parameter arrives as
  typed text). Reusable form: `coerce_blank_params` in
  [`nodes/speech/_base.py`](./speech/_base.py); per-node precedents:
  `AndroidServiceParams._coerce_parameters`, `WriteTodosParams._coerce_todos`.
  Do **not** blanket-drop blanks for `str` fields — that turns a `min_length`
  error into a confusing "field required".
- **`usable_as_tool = True` hides both canvas handles.** It auto-sets
  `hide_input_handle` / `hide_output_handle` unless the class declares
  them, so a dual-purpose node meant to stay wirable must set both to
  `False` explicitly. Symptom: the node runs fine as a tool but cannot be
  connected to anything on the canvas.
- **Never accept a file path and join it onto the workspace yourself.**
  Use `services.media.coerce_file_param` / `read_media_bytes`. A node that
  did the naive join let `audio_file="../../credentials.db"` read the
  encrypted credential store and upload it to a third party. Those helpers
  reject `..` / `~` / drive prefixes *and* re-check containment after
  resolution, so a symlink or Windows junction cannot redirect the result.
- **Never return media bytes (or base64) from a node.** Write the file into
  the workspace with `services.media.write_audio` and return the `AudioRef`.
  A node result is persisted 3×, broadcast twice, retained in the status
  cache, copied into every downstream activity input, and — if the node is
  `usable_as_tool` — serialized into an LLM message. See
  [`services/media/limits.py`](./../services/media/limits.py) for the
  measured Temporal thresholds this exists to stay under.
- **Return plain data, never raw backend or third-party objects.** Backend /
  SDK result objects (native filesystem `ReadResult`, httpx
  responses, …) must be unwrapped to plain fields before returning —
  the Output validation rejects them, and everything downstream
  (`node_outputs` JSON column, orjson WS broadcast,
  `_serialise_tool_result`) expects JSON-compatible data. Likewise
  return real lists/dicts, not pre-stringified JSON — LLM-facing
  serialization is the dispatcher's job.
- **Raise `NodeUserError` for user-correctable failures.** Missing
  required field, unknown enum value, bad path, sidecar not running —
  `raise NodeUserError("...")` (exported from `services.plugin`) gives
  the user/LLM a clean one-line WARN + structured envelope with no
  traceback, and the shared Temporal retry policy skips retries for it
  (`services/plugin/scaling.py`). Bare `Exception` is for genuine
  server bugs only — it keeps the full `logger.exception` traceback.
- **Coerce LLM-stringified JSON args at the Params boundary.** Gemini
  (and others) sometimes pass array/object tool args as JSON-encoded
  strings. Fields that can receive them declare a
  `field_validator(..., mode="before")` that `json.loads`es string
  input — canonical examples: `AndroidServiceParams._coerce_parameters`,
  `WriteTodosParams._coerce_todos`. Malformed JSON should pass through
  unchanged so Pydantic raises a proper `ValidationError` the LLM can
  correct.
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
- **Side effects must survive a retry.** With `TEMPORAL_PLUGIN_FAILURE_RETRIES`
  on, a structured failure raises at the activity boundary and Temporal
  re-runs the node per `effective_retry_policy()`: nodes annotated
  `readonly: True` (and not `destructive`) get three attempts; everything
  else (sends, writes, paid actors, triggers, unannotated nodes) gets one.
  Declare `retry_policy = RetryPolicy(...)` on the class to opt in or out
  explicitly, and key writes on `ctx.idempotency_key` (stable across the
  attempts of one activity; `ctx.attempt` says which one this is) so
  attempt 2 can skip work attempt 1 completed. The verdict is classified
  from the exception (`services/plugin/retryability.py`): `httpx` 4xx other
  than 408 / 425 / 429 are permanent, 5xx and transport errors transient, and
  a boolean `retryable` attribute on the exception or its `__cause__` wins.
  `tests/fixtures/effective_retry_attempts_snapshot.json` pins the attempt
  count per node type; regenerate it in the same commit as an annotation
  change.

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

End state: `services/handlers/` is **4 files / ~1K LOC** (down from
16 / 12.8K). Only cross-cutting orchestration remains there:
`tools.py` (AI-tool dispatch + agent delegation), `triggers.py`
(generic event-trigger handler), `todo.py` (writeTodos shim), and the
package docstring (`__init__.py`). The former `google_auth.py` shared
OAuth helper was retired into `nodes/google/_auth_helper.py`.

Plan + full migration history:
[plugin_system.md](../../docs-internal/plugin_system.md).
