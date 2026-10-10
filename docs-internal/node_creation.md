# Node Creation Guide

> **Companion docs:** [Plugin System](./plugin_system.md) (Wave 11 architecture + Wave 12 event framework) and the inline [Nodes Cookbook](../server/nodes/README.md) next to the plugin files.

This guide is a fast index for adding a new node to OpenCompany. The
deep technical details live in [`plugin_system.md`](./plugin_system.md);
this file picks the right entry point based on what you're adding.

## Decision tree

### Browser AI Agent creation

`browser_agent` is a `SpecializedAgentBase` plugin using the existing native
agent loop. Browser control stays in the pinned browser-use CLI (`0.13.10`).
`services/browser_agent_recipe.py` defines its complete creation bundle:
agent, one private Browser tool, private Context, Master Skill with the shipped
browser skill enabled, and `visionAnalyze`. Canvas drop and the workspace's
**Add Browser AI Agent** action call `POST /api/browser/agents`, which persists
the bundle atomically through `apply_graph_additions`. Agent Builder and new
employee Browser capabilities use the same recipe. Existing employees and
Browser/`web_agent` nodes retain their graphs.

The plugin declares `uiHints.createsBrowserAgent`; canvas drag-and-drop reads
that capability from NodeSpec to select the recipe. Keep this hint separate
from `isBrowserPanel`, which belongs only to the Browser tool.

Reuse an explicitly selected saved Browser tool. The server allocates IDs and
checks graph ownership and operator blocklists; transport retries use the same
mutation UUID. Repeating Add for an already associated Browser agent returns
its association without recreating a deliberately deleted Context or Skills
node. The viewer remains bound to the Browser tool; `browser_agent` must never
carry `isBrowserPanel`.

`services/browser_agent_recipe.py::browser_tool_id` requires exactly one
connected saved Browser tool before task admission. Creation returns allocated
`node_ids`, graph `operations`, `applied` and `saved_revision`; clients apply
those acknowledged operations through the existing graph update path. They
do not construct an executable task graph. Provider/model fields use standard
agent configuration and inheritance. Companion deletion remains an explicit
graph edit, not a trigger to rebuild the bundle.

See [Browser workspace](./browser_workspace.md),
[agent architecture](./agent_architecture.md) and
[distributed deployment](./browser_agent_deployment.md) for runtime contracts.

### General node selection

| You're adding… | Read this | Boilerplate |
|---|---|---|
| A simple action node (one HTTP call, no state) | [Quick start](./plugin_system.md#quick-start--adding-a-new-node) | folder under `server/nodes/<group>/<name>/` with `__init__.py` |
| A dual-purpose node (workflow node + AI tool) | [plugin_system.md](./plugin_system.md) + whatsapp / twitter / email plugin folders | folder under `server/nodes/<group>/<name>/` with `group: ['category', 'tool']` and `usable_as_tool = True` |
| A specialized AI agent | [plugin_system.md](./plugin_system.md) + the existing `server/nodes/agent/<name>/` folders | folder under `server/nodes/agent/<name>/` extending `SpecializedAgentBase` |
| A standalone AI-tool node (no workflow surface) | [Tool Building Pipeline](./tool_building_pipeline.md) | folder under `server/nodes/tool/<name>/` extending `ToolNode` |
| A node that wraps a CLI tool, supervises a daemon, or receives signed webhooks | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) (this section is the most important one) | self-contained folder under `server/nodes/<group>/` using `services.events` base classes |
| A polling-based trigger node | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — subclass `PollingEventSource` | new file or folder; framework owns the loop |
| A long-lived service plugin (bot connection, WebSocket bridge, SDK session) | [Self-contained plugin folders (Wave 11.H)](./plugin_system.md#self-contained-plugin-folders) — telegram is the reference | folder with `_credentials.py` / `_service.py` / `_handlers.py` / `_filters.py` / `_refresh.py` |
| **A node that must speak to several interchangeable vendors** (one canvas node, a `provider` dropdown) | [Speech Provider RFC](./speech_provider_rfc.md) + [`server/nodes/speech/`](../server/nodes/speech/) — the reference; and [Multi-credential nodes](./plugin_system.md#multi-credential-nodes) for the `ctx.connection(id)` contract | folder with `_protocol.py` / `_registry.py` / `_config.py` / `_unifier.py` / `_providers/`, a JSON capability file under `server/config/`, and a `credentials = (A, B, C)` tuple |
| A plugin whose auth is owned by an external CLI (`stripe login`, `vercel login`, `gh auth login`, `gcloud auth login`) | [Stripe Service](./stripe_service.md) — the reference for "marker-token + generic catalogue invalidation" — and [plugin_system.md → CLI-managed auth pattern](./plugin_system.md#cli-managed-auth-pattern); [Vercel Service](./vercel_service.md) for the **device-flow** variant (single blocking `login` process, no two-step complete flag) | self-contained folder + `_install.py` for the auto-downloader; `_handlers.py` calls `auth_service.store_oauth_tokens(provider, "cli-managed", "cli-managed")` after the CLI login completes, then broadcasts the existing generic `credential_catalogue_updated` event |

## The four node kinds

```
BaseNode  (services/plugin/base.py)
├── ActionNode            fire-once; returns {success, result} envelope
├── TriggerNode           long-lived; event-mode or polling-mode
│   └── WebhookTriggerNode  ← Wave 12: signed-webhook trigger backed by a WebhookSource
└── ToolNode              AI-invoked; flat return shape
```

Simple plugins inherit directly from `ActionNode` /
`TriggerNode` / `ToolNode`. Rich plugin folders (telegram,
stripe) layer additional bases from `services.events` underneath them.

## Temporal execution and Stop/Resume contract

Read [Workflow control](./temporal-workflow-control.md) before adding a node
that starts external work, a trigger, or an agent tool. New deployment
generations use `execution_control_version=1`; existing histories retain their
recorded command paths. A plugin does not implement a second pause lifecycle.

The workflow owns admission. It checks the control gate before admitting a
node, model request, tool, polling fetch, compaction, or child start. Stop
closes that admission, lets already-admitted work finish under its existing
retry policy, records its result and normal bookkeeping, then acknowledges
Stopped. Resume releases the same continuation at the next pending action.
For an agent response containing tools A and B, Stop during A preserves A's
result and the pending B call; Resume executes B without repeating the model
response or A. Already-admitted parallel tools finish concurrently.

Declare the operation's Temporal policy on the node class:

| Declaration | Meaning |
|---|---|
| `task_queue` | Plugin worker pool when the run's frozen worker-pool setting is enabled; otherwise the default worker handles it. |
| `start_to_close_timeout` | Maximum duration of one Activity attempt. |
| `heartbeat_timeout` | Liveness window for a long Activity; independent of cooperative Stop. |
| `retry_policy` | Attempts, intervals, and non-retryable error types for the operation. |

Ordinary per-type agent-tool dispatch applies all four declarations for new
controlled generations. Graph node dispatch currently applies plugin retry
and queue declarations but uses generic 24-hour attempt / 2-minute heartbeat
defaults; the existing Workspace-task override uses that plugin's timeout and
heartbeat values. Do not assume a graph node's timeout declaration is enforced
on every path. Resolved tool bindings and policies are recorded during agent
preparation and carried through
Continue-As-New, so Resume does not rebuild the pending call from changed
configuration. The in-process executor does not provide Temporal's policies.
Keep provider/tool I/O in regular Activities. A heartbeat can report liveness
or a real recovery checkpoint supported by the operation; a status string
cannot restore an opaque HTTP request, subprocess, or provider session.
Managed agents such as Claude Code, RLM, and Vertex stop at their whole
Activity boundary. Stop can remain Stopping while an admitted operation is
waiting or retrying; the model request policy currently permits unlimited
retries. See [Temporal Activity failure detection](https://docs.temporal.io/develop/python/failure-detection)
and [Long-running Activity](https://docs.temporal.io/design-patterns/long-running-activity).

## Event identity and delivery contract

For a deployed event trigger, register its CloudEvents type with
`register_canary_trigger_type` and emit a `WorkflowEvent` through
`services.events.dispatch.emit`. Controlled deployments keep definitions and
queued events in `WorkflowControlWorkflow`; separate
`TriggerListenerWorkflow` / `PollingTriggerWorkflow` executions are legacy
compatibility paths. An interactive canvas waiter is a separate in-memory
mechanism; check the [Event Waiter delivery gap](./event_waiter_system.md#known-gap-canvas-run-on-canary-push-triggers)
before assuming `emit` resolves it.

Use a stable event ID only when the provider supplies the complete identity.
Keep it unchanged on redelivery and distinguish different messages or event
families. The current producer conventions are:

| Producer | `WorkflowEvent.id` when identity is complete |
|---|---|
| Telegram message | `telegram:{chat_id}:{message_id}` |
| Discord message | `discord:message:{message_id}` |
| Discord interaction | `discord:interaction:{interaction_id}` |
| WhatsApp message | `whatsapp:{direction}:{chat_id-or-sender-or-from}:{message_id}` |
| Tracked chat message | Existing chat `run_id` (untracked messages use their message UID). |

Missing identity uses the envelope's random ID. Do not synthesize a constant
or partial key that would merge different messages. Random fallback preserves
distinct messages but cannot deduplicate provider redelivery.

The controller adopts the [Event Accumulator](https://docs.temporal.io/design-patterns/event-accumulator)
pattern's durable queue, deduplication, and continuation practices while
keeping immediate per-event processing and accepted queue order through
overflow. It does not add an inactivity batching window. Deduplication is per
trigger and event ID: pending keys remain protected
independently of the bounded recent-key window, overflow pages restore pending
keys, and queued events remain pending while stopped. Carry and overflow
spill fences include Signals arriving during awaits before Continue-As-New.
This protection is versioned with `controller-event-accumulator-v1`; it does
not retrofit old runs' recorded decisions.

Temporal Signals carry events, completed Updates acknowledge control and
membership changes, and read-only Queries expose status. Successful Signal
delivery means Temporal accepted the event into history, not that its handler
or downstream node finished. `dispatch.emit` still discovers consumers through
eventually consistent Visibility and logs/suppresses delivery errors; the
durability guarantee begins after a target accepts its Signal. There is no
durable producer outbox or promise of external exactly-once effects. Do not
use Signal-With-Start to recreate a missing versioned controller with an empty
root registry. See [Workflow messaging](https://docs.temporal.io/design-patterns/workflow-messaging-patterns)
and [Python message passing](https://docs.temporal.io/develop/python/message-passing).

Known naming limit: triggers with the same display-label/type slug can still
collide in listener and child Workflow IDs. A stable provider event ID does
not fix that separate trigger-identity issue. See the control document's
operating limits when adding multiple similar triggers.

## Five-minute recipe — one folder, one `__init__.py`

For nodes with no state, no daemon, no signed webhooks (the common
case): the canonical recipe code block lives in the
cookbook — see
[`server/nodes/README.md` → Five-minute recipe](../server/nodes/README.md#five-minute-recipe).

It is a single folder `server/nodes/<group>/<name>/` whose
`__init__.py` declares a `Credential` subclass, a `Params` model, an
`Output` model, and one `ActionNode` subclass with `type` /
`display_name` / `group` / `component_kind` / `handles` /
`credentials` / `task_queue` / `usable_as_tool` / `Params` / `Output`
plus one `@Operation` method. That's the entire node (the folder also
holds `icon.svg` / `meta.json`, which is why the folder is the canonical
shape; a bare `.py` inside a domain folder still works and 20+ shipped
plugins use it — stripe, telegram, discord, whatsapp, whatsapp_business,
translate, speech, github, vercel, cloudflare, gcloud, aws, mcp; live list:
`find server/nodes -mindepth 2 -maxdepth 2 -name "*.py" ! -name "_*" ! -name "__init__.py"`).
On server restart it auto-registers, the
NodeSpec is emitted at `/api/schemas/nodes/<type>/spec.json`, and it
appears in the Component Palette under its first `group` entry.

## Five-minute recipe — Wave 12 self-contained folder (signed webhook + CLI)

For nodes that wrap a CLI tool **and** receive signed webhooks
(Stripe; CLI wrappers without webhooks — GitHub, Vercel, Cloudflare,
gcloud — need only the CLI-managed-auth variants further down), use
the [Wave 12 framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents).
Stripe is the reference implementation
([`server/nodes/stripe/`](../server/nodes/stripe/)). The shape:

```
server/nodes/<provider>/
├── __init__.py             # register_* calls only (zero logic)
├── _credentials.py         # Credential subclass (Stripe: StripeCredential(Credential))
├── _source.py              # DaemonEventSource + WebhookSource subclasses
├── _events.py              # CloudEvents factory (the one type the trigger is canary-registered
│                           #   for) + the dispatch.emit wrapper that reaches deployed listeners
├── _handlers.py            # WS_HANDLERS via make_lifecycle_handlers()
├── _install.py             # ensure_<provider>_cli() auto-downloader
├── <provider>_action.py    # ActionNode + AI tool — uses run_cli_command
└── <provider>_receive.py   # WebhookTriggerNode subclass
```

The framework absorbs the boilerplate that used to live in each
plugin: subprocess supervision, HMAC signature verification,
lifecycle WebSocket handlers, status-refresh callback, CLI invocation
with credential injection. A new framework plugin lands in
**~150 executable lines**, of which only `build_command` /
`parse_line` / `shape` / per-provider Params/Output schemas are
provider-specific. See:

- [Plugin System → Wave 12 framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — every base class + helper documented with examples.
- [Stripe Service](./stripe_service.md) — the reference implementation walked through file by file.

## Recipe — CLI-managed auth (Stripe / `gh` / `gcloud` shape)

When auth lives **inside** an external CLI (the CLI runs its own
OAuth, persists tokens to its own config file, and our `<command>`
calls just inherit those creds), use this pattern. Stripe is the
canonical example.

**Three plumbing pieces** — all already in the codebase, just
reused:

1. **Marker-token write after CLI login completes.** The plugin
   writes synthetic strings to `auth_service.store_oauth_tokens`
   with the provider id matching the catalogue entry's key. Stripe,
   Vercel and GitHub set **no** `status_hook`, so
   `provider_connection_state` in
   [`server/services/credential_registry.py`](../server/services/credential_registry.py)
   (which the catalogue handler calls) resolves them through its
   `kind == "oauth"` branch, which keys
   `auth_service.get_oauth_tokens(provider_id) is not None` off the
   provider id directly to set `provider.stored = true`. The order is
   `stored_check`, then `status_hook`, then `kind`, then
   `connected_check`; the providers that declare a `status_hook` are
   listed in `server/config/credential_providers.json` (the
   `claude_code` / `codex_cli` CLI entries among them). **Same storage
   API path Google's OAuth callback uses** — no new abstraction.

   ```python
   await auth_service.store_oauth_tokens(
       provider="stripe",                # matches the provider id (catalogue key) — no status_hook needed
       access_token="cli-managed",       # marker; CLI owns the real auth
       refresh_token="cli-managed",
   )
   ```

2. **CloudEvents-shaped broadcast on state change.** Plugin emits a
   `WorkflowEvent` (CloudEvents v1.0) via the canonical helper —
   wrapped under the existing `credential_catalogue_updated`
   wire-format type. Same shape `save_api_key` / `twitter_logout` /
   `google_logout` use; locked by
   `tests/credentials/test_credential_broadcasts.py`. The frontend
   invalidates the catalogue, refetches, and the modal re-renders.
   No per-provider broadcast type, no `case 'stripe_status'` —
   **zero node-specific code in the frontend**.

   ```python
   await get_status_broadcaster().broadcast_credential_event(
       "credential.oauth.connected", provider="stripe",      # or .disconnected on logout
   )
   ```

3. **Auto-installer for the CLI binary.** Plugins that wrap a CLI
   ship a `_install.py` with a single `ensure_stripe_cli()`-shaped
   async helper:
   - Cached path → system PATH (`brew`, `scoop`, `apt`) → a previous
     download under `<DATA_DIR>/packages/<provider>/bin/`
     (`core.paths.package_dir`) → fresh download from GitHub releases
     into that same directory.
   - Pinned version constant; `(system, machine) -> asset_name` map
     covering Windows/Linux/Mac × x86_64/arm64.
   - Returns absolute binary path; subsequent calls hit the cache.

   The plugin's `DaemonEventSource` subclass overrides `start()` to
   `await ensure_<provider>_cli()` before `super().start()` and
   sets `binary_name = ""` so the framework's pre-flight
   `shutil.which` check is skipped (we handle install ourselves).

**Frontend contract**: the `OAuthPanel.tsx` `connected` derivation
already supports CLI-managed providers via:

```tsx
const connected = status ? !!status.connected : !!config.stored;
```

Providers with no `statusHook` registered in `useProviderStatus`
fall back to the catalogue's authoritative `config.stored` field.
**No frontend edits needed** to add a new CLI-managed plugin.

The cumulative effect: a new "wrap a CLI tool whose login is
browser-OAuth" plugin lands as a self-contained folder under
`server/nodes/<group>/` plus a one-line entry in
`server/config/credential_providers.json` and zero touches outside
the folder.

**Output display for CLI nodes**: declare
`ui_hints = {"outputMode": "terminal"}` so the Output panel renders
the node's textual output preformatted (never ReactMarkdown — `#`
would become headings and indentation would collapse), and follow the
`_shape` convention: parsed JSON goes in `result`, human text in
`stdout` — never both (pre-stringified duplication violates the
output contract) — with empty keys omitted. Reference:
`githubAction` / `vercelAction` / `shell`; the panel side is locked by
`client/src/components/__tests__/OutputPanel.test.tsx`.

**Device-flow variant (Vercel).** Not every CLI exposes Stripe's
machine-friendly two-step (`--non-interactive` → `--complete <url>`).
`vercel login` is a single **blocking** OAuth device flow: it prints a
verification URL, then polls until the browser auth completes. The
login handler therefore cannot use `run_cli_command` (which buffers
output until process exit) — it spawns the CLI directly with
`asyncio.create_subprocess_exec` (`stdin=PIPE` left un-written, the
claude-login EOF guard), reads stdout+stderr in chunks until the URL
appears (chunk-based, not `readline()` — spinner `\r` frames overrun
the line limit; pumps keep draining for the process lifetime so the
pipe buffer never fills), returns `{success, url}` immediately, and a
background task awaits exit. Success gate is the same mtime-advance +
sniff pair, against a **pinned config dir**: every invocation passes
`--global-config <DATA_DIR>/vercel/` (the `CLAUDE_CONFIG_DIR`
isolation idiom) so the auth-file path is deterministic across
platforms. The installer is `core.js_runtime.add_package(<spec>)` —
`bun add --cwd <packages_dir()> <spec>` into the shared bun-managed
packages tree, the bin shim then running on bun — instead of a
GitHub-release download. Reference: [`server/nodes/vercel/`](../server/nodes/vercel/)
+ [vercel_service.md](./vercel_service.md).

**CLI-opens-the-browser variant (gcloud).** Some CLIs run the whole
browser interaction themselves: `gcloud auth login --quiet` starts a
loopback callback server and opens the default browser directly. The
login handler then proxies NOTHING to the modal (no URL, no
verification code — just `{success, message}`); the badge flips when the
background completion broadcasts. A single-flight guard (repeat clicks
return "already in progress") prevents duplicate browser tabs — and,
for a CLI whose callback port is fixed, port collisions — and the
completion watcher never kills the process: on Windows killing a
launcher shim orphans the child still serving the callback, and the
CLI's own login timeout ends it. Success gate = a CLI status probe that
parses JSON, never exit codes. gcloud also pins `CLOUDSDK_CONFIG` so
node auth state never touches the operator's own gcloud config.
Reference: [`server/nodes/gcloud/`](../server/nodes/gcloud/) +
[gcloud_service.md](./gcloud_service.md). Cloudflare used this variant
until cf 1.0 made device authorization its default login; it now
follows the gh device-flow shape (URL + code relayed to the modal,
`--no-browser` so the server never opens a second tab), keeps the
single-flight guard (a repeat click returns the same code) and the
never-kill rule, installs the CLI before spawning any login so no code
is issued after its request was answered, and pairs the login with an
optional API-token field (vercel dual-path) for unattended use:
[`server/nodes/cloudflare/`](../server/nodes/cloudflare/) +
[cloudflare_service.md](./cloudflare_service.md).

## What auto-wires (don't write it yourself)

When a plugin file is imported (which the `nodes/__init__.py`
walker does on startup), these registrations happen automatically:

| Mechanism | Where | When |
|---|---|---|
| Node class registration | `_NODE_CLASS_REGISTRY` | `BaseNode.__init_subclass__` on class definition |
| Metadata + Pydantic schemas | `NODE_METADATA`, `_DIRECT_MODELS`, `NODE_OUTPUT_SCHEMAS` | same |
| Handler dispatch | `_HANDLER_REGISTRY` (imported into `NodeExecutor` as `_PLUGIN_HANDLERS`) | same |
| Auto-derived `uiHints.isConfigNode: True` | `NODE_METADATA[type]['uiHints']` | `_metadata_dict` runs `_derive_auto_ui_hints(cls.group)` for every plugin in a `('memory', 'tool')` group. Plugin `ui_hints = {...}` always wins. Tells the frontend that the node's panel inherits its parent's main inputs. See [plugin_system.md → Auto-derived uiHints](./plugin_system.md#auto-derived-uihints). |
| Credentials | `CREDENTIAL_REGISTRY` | `Credential.__init_subclass__` when `_credentials.py` is imported |
| Trigger registry + filter builders | `event_waiter.TRIGGER_REGISTRY`, `FILTER_BUILDERS` | back-fill from `TriggerNode` subclasses on first lookup |
| Temporal activity wrapper | `cls.as_activity()` | first call; pooled into the worker queue declared by `task_queue` |
| Palette icon | `<plugin_folder>/icon.svg`, or `icon_<nodeType>.svg` per node type in a multi-node folder — served via `GET /api/schemas/nodes/<type>/icon`. Brand artwork belongs here. `meta.json` → `"icons": {"<nodeType>": "lucide:Send"}` is the fallback for generic utility nodes. Resolution: SVG file → plugin `meta.json` ref → `visuals.json`. |
| Palette color | `<plugin_folder>/meta.json` (`{"color": "#xxx"}`); `visuals.json` is the fallback for legacy entries. Its only `color` fields are on the lowercase tool-name alias keys (`github`, `vercel`, `gcloud`, `cloudflare`, `data`, `vision`) that the skill icon resolver reads — see below. |

What you **do** still write:

- `icon.svg` (or `icon_<nodeType>.svg` per node type in a multi-node folder) plus a `meta.json` carrying `{"color": "#xxx"}`. **Ship the brand mark** when the node wraps a recognisable product — that is what makes it identifiable on the canvas, and it has no external dependency. The class-attribute icon/color override was removed in F1 — declaring `icon` or `color` as class attrs has no effect.
- A library reference in `meta.json` — `"icons": {"<nodeType>": "lucide:Send"}` per node type, or `"icon"` folder-wide — for generic utility nodes with no brand of their own. Use the library's **export** name (`lucide:CheckCheck`), not its kebab-case file name; the lookup is against package exports and a miss renders nothing rather than erroring (locked by `client/src/assets/icons/index.test.ts`). Weigh it knowing the name is a third-party export that can disappear on upgrade, and the glyph is monochrome `currentColor` line art. A file beats a ref, so shipping both makes the ref dead.
- An entry in `server/nodes/visuals.json` is the legacy central fallback, for plugins with neither of the above. Post-F1/F7 it carries zero `asset:<key>` values.
- A **lowercase alias entry in `visuals.json` keyed by the LLM `tool_name`** whenever you set `tool_name` to something other than `<snake_case_of_node_type>` AND ship a paired skill. The skill icon resolver maps the SKILL.md `allowed-tools` token (= the tool name) through snake→camel into `visuals.json`; a custom tool name misses the node-type key and the Master Skill row renders a blank icon. The alias carries the same icon plus the plugin's `meta.json` color — precedent: `"github": {"icon": "lobehub:Github", "color": "#8250df"}`, `"vercel": {"icon": "lobehub:Vercel", "color": "#666666"}`. Locked by `server/tests/test_skill_icon_resolution.py`.
- An entry in `server/nodes/groups.py` if you introduce a new palette group.

## Where to look next

| Need | Doc |
|------|-----|
| 5-minute recipe with shared helpers + common pitfalls | [server/nodes/README.md](../server/nodes/README.md) |
| Full plugin pattern (every class attribute, `@Operation`, declarative `Routing`, `Connection` facade, Temporal task queues, credential classes) | [plugin_system.md](./plugin_system.md) |
| Self-contained plugin folders (rich plugins like telegram with their own service, WS handlers, pre-checks) | [plugin_system.md → Self-contained plugin folders](./plugin_system.md#self-contained-plugin-folders) |
| Wave 12 event framework (signed webhooks, CLI daemons, polling) | [plugin_system.md → Wave 12](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) |
| Stripe as the Wave 12 reference plugin (also the canonical CLI-managed-auth + auto-installer reference) | [stripe_service.md](./stripe_service.md) |
| Backend-as-SSOT design (NodeSpec, icons, output schemas) | [schema_source_of_truth_rfc.md](./ARCHIVE/schema_source_of_truth_rfc.md) |
| JSON workflow format, edge handle conventions | [workflow-schema.md](./workflow-schema.md) |
| Polling triggers + event_waiter mechanics | [event_waiter_system.md](./event_waiter_system.md) |
| Memory lifecycle (markdown parse/append/trim, vector store, session resume) — *archived; describes the retired pre-RFC-0002 `input-memory` markdown model* | [ARCHIVE/memory_lifecycle.md](./ARCHIVE/memory_lifecycle.md) |
| Tool building pipeline (`_build_tool_from_node`, schema, per-type Temporal dispatch) | [tool_building_pipeline.md](./tool_building_pipeline.md) |
| Cooperative Stop/Resume, root registration, messaging, and rollover | [temporal-workflow-control.md](./temporal-workflow-control.md) |
| Process supervision (used by `DaemonEventSource`) | [server/services/process_service.py](../server/services/process_service.py) — singleton API |
| Multi-vendor node behind one `provider` dropdown (two registries, JSON capabilities, per-vendor modules) | [speech_provider_rfc.md](./speech_provider_rfc.md) |
| Returning media from a node without blowing the payload limits (`AudioRef`, workspace routes, containment) | [media_transport.md](./media_transport.md) |

## Wave summary (current state)

- **Wave 11** — Class-based plugin system. 9 Temporal worker pools;
  plugin count via `len(services.node_registry.NODE_METADATA)` after
  `import nodes` (a bare `**/__init__.py` glob overcounts because it also
  matches the group packages); invariant total via `pytest --collect-only`. `services/handlers/` shrank from
  12.8K → 1.1K LOC.
- **Wave 11.H** — Self-contained plugin folders. Five generic
  registries at 11.H, `register_router` at 11.I; the set has since
  grown to 19 (webhook sources, option loaders, OAuth callback paths,
  canary trigger types, poll factories, social send handlers, shutdown
  hooks, service factories, log-source tags, conversation listeners,
  process supervisors, master-skill expander, agent-context builder).
  Live list: the registry table in
  [plugin_system.md](./plugin_system.md#self-contained-plugin-folders). Telegram
  is the reference.
- **Wave 12** — Generalized event framework
  ([`services/events/`](../server/services/events/)). `EventSource`
  hierarchy + CloudEvents-shaped envelope + verifier registry +
  wiring helpers. Stripe is the reference.
- **Wave 12.B** — CLI-managed-auth pattern (Stripe). Plugins whose
  auth lives in an external CLI's own config file (`stripe login` →
  `~/.config/stripe/config.toml`) reuse `auth_service.store_oauth_tokens`
  with marker strings + the existing generic
  `credential_catalogue_updated` broadcast — no provider-specific
  Zustand entries, no per-provider WS handler cases. Includes a
  reusable `ensure_<cli>_cli()` shape for downloading the CLI binary
  on first use (pinned version + GitHub-releases asset map). The
  same pattern fits any future CLI-managed integration (`gh auth
  login`, `gcloud auth login`, `vercel login`, etc.).

The end state: every new event-source plugin is **~150 executable
lines** of provider-specific code on top of shared bases, with
**zero edits outside the plugin folder** (and zero edits to the
frontend).
