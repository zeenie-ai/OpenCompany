# Node Creation Guide

> **Companion docs:** [Plugin System](./plugin_system.md) (Wave 11 architecture + Wave 12 event framework) and the inline [Nodes Cookbook](../server/nodes/README.md) next to the plugin files.

This guide is a fast index for adding a new node to OpenCompany. The
deep technical details live in [`plugin_system.md`](./plugin_system.md);
this file picks the right entry point based on what you're adding.

## Decision tree

| You're adding… | Read this | Boilerplate |
|---|---|---|
| A simple action node (one HTTP call, no state) | [Quick start](./plugin_system.md#quick-start--adding-a-new-node) | folder under `server/nodes/<group>/<name>/` with `__init__.py` |
| A dual-purpose node (workflow node + AI tool) | [plugin_system.md](./plugin_system.md) + whatsapp / twitter / email plugin folders | folder under `server/nodes/<group>/<name>/` with `group: ['category', 'tool']` and `usable_as_tool = True` |
| A specialized AI agent | [plugin_system.md](./plugin_system.md) + the existing `server/nodes/agent/<name>/` folders | folder under `server/nodes/agent/<name>/` extending `SpecializedAgentBase` |
| A standalone AI-tool node (no workflow surface) | [Tool Building Pipeline](./tool_building_pipeline.md) | folder under `server/nodes/tool/<name>/` extending `ToolNode` |
| A node that wraps a CLI tool, supervises a daemon, or receives signed webhooks | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) (this section is the most important one) | self-contained folder under `server/nodes/<group>/` using `services.events` base classes |
| A polling-based trigger node | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — subclass `PollingEventSource` | new file or folder; framework owns the loop |
| A long-lived service plugin (bot connection, WebSocket bridge, SDK session) | [Self-contained plugin folders (Wave 11.H)](./plugin_system.md#self-contained-plugin-folders) — telegram is the reference | folder with `_credentials.py` / `_service.py` / `_handlers.py` / `_filters.py` / `_refresh.py` |
| A plugin whose auth is owned by an external CLI (`stripe login`, `vercel login`, `gh auth login`, `gcloud auth login`) | [Stripe Service](./stripe_service.md) — the reference for "marker-token + generic catalogue invalidation" — and [plugin_system.md → CLI-managed auth pattern](./plugin_system.md#cli-managed-auth-pattern); [Vercel Service](./vercel_service.md) for the **device-flow** variant (single blocking `login` process, no two-step complete flag) | self-contained folder + `_install.py` for the auto-downloader; `_handlers.py` calls `auth_service.store_oauth_tokens(provider, "cli-managed", "cli-managed")` after the CLI login completes, then broadcasts the existing generic `credential_catalogue_updated` event |

## The four node kinds

```
BaseNode  (services/plugin/base.py)
├── ActionNode            fire-once; returns {success, result} envelope
├── TriggerNode           long-lived; event-mode or polling-mode
│   └── WebhookTriggerNode  ← Wave 12: signed-webhook trigger backed by a WebhookSource
└── ToolNode              AI-invoked; flat return shape
```

Single-file plugins inherit directly from `ActionNode` /
`TriggerNode` / `ToolNode`. Self-contained plugin folders (telegram,
stripe) layer additional bases from `services.events` underneath them.

## Five-minute recipe — single file

For nodes with no state, no daemon, no signed webhooks (the common
case): the canonical single-file recipe code block lives in the
cookbook — see
[`server/nodes/README.md` → Five-minute recipe](../server/nodes/README.md#five-minute-recipe).

It is a single `server/nodes/<group>/<name>.py` declaring a
`Credential` subclass, a `Params` model, an `Output` model, and one
`ActionNode` subclass with `type` / `display_name` / `group` /
`component_kind` / `handles` / `credentials` / `task_queue` /
`usable_as_tool` / `Params` / `Output` plus one `@Operation` method.
That's the entire node. On server restart it auto-registers, the
NodeSpec is emitted at `/api/schemas/nodes/<type>/spec.json`, and it
appears in the Component Palette under its first `group` entry.

## Five-minute recipe — Wave 12 self-contained folder (signed webhook + CLI)

For nodes that wrap a CLI tool **and** receive signed webhooks
(Stripe, future GitHub-CLI / Cloudflare-Wrangler integrations), use
the [Wave 12 framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents).
Stripe is the reference implementation
([`server/nodes/stripe/`](../server/nodes/stripe/)). The shape:

```
server/nodes/<provider>/
├── __init__.py             # 5 register_* calls (zero logic)
├── _credentials.py         # ApiKeyCredential subclass
├── _source.py              # DaemonEventSource + WebhookSource subclasses
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
   with the provider id matching the catalogue entry's key. CLI-managed
   providers set **no** `status_hook` — the catalogue handler at
   [`server/routers/websocket.py:handle_get_credential_catalogue`](../server/routers/websocket.py)
   resolves them through its `kind == "oauth"` fallback, which keys
   `auth_service.get_oauth_tokens(provider_id) is not None` off the
   provider id directly to set `provider.stored = true` (the
   `status_hook` branch is only for providers that declare one:
   google / twitter / telegram). **Same storage API path Google's
   OAuth callback uses** — no new abstraction.

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
   - Cached path → system PATH (`brew`, `scoop`, `apt`) → workspace
     cache → fresh download from GitHub releases.
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
platforms. The installer is `npm install <pkg> --prefix
<packages_dir()>` into the shared npm tree instead of a GitHub-release
download. Reference: [`server/nodes/vercel/`](../server/nodes/vercel/)
+ [vercel_service.md](./vercel_service.md).

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
| Palette icon | `<plugin_folder>/icon.svg` (or `icon_<nodeType>.svg` for multi-node folders); served via `GET /api/schemas/nodes/<type>/icon`. `visuals.json` is the fallback for emoji or `lobehub:<brand>` icons. |
| Palette color | `<plugin_folder>/meta.json` (`{"color": "#xxx"}`); `visuals.json` is the fallback for legacy entries (post-F2 it has zero color fields). |

What you **do** still write:

- Drop `icon.svg` (or `icon_<nodeType>.svg` for multi-node folders) and `meta.json` (`{"color": "#xxx"}`) inside the plugin folder. The class-attribute icon/color override was removed in F1 — declaring `icon` or `color` as class attrs has no effect.
- An entry in `server/nodes/visuals.json` ONLY if you want an emoji or `lobehub:<brand>` icon (no folder SVG). Post-F1/F7 visuals.json carries zero `asset:<key>` values.
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
| Backend-as-SSOT design (NodeSpec, icons, output schemas) | [schema_source_of_truth_rfc.md](./schema_source_of_truth_rfc.md) |
| JSON workflow format, edge handle conventions | [workflow-schema.md](./workflow-schema.md) |
| Polling triggers + event_waiter mechanics | [event_waiter_system.md](./event_waiter_system.md) |
| Memory lifecycle (markdown parse/append/trim, vector store, session resume) | [memory_lifecycle.md](./memory_lifecycle.md) |
| Tool building pipeline (`_build_tool_from_node`, schema, per-type Temporal dispatch) | [tool_building_pipeline.md](./tool_building_pipeline.md) |
| Process supervision (used by `DaemonEventSource`) | [server/services/process_service.py](../server/services/process_service.py) — singleton API |

## Wave summary (current state)

- **Wave 11** — Class-based plugin system. 9 Temporal worker pools;
  plugin count via `glob server/nodes/**/__init__.py`; invariant total
  via `pytest --collect-only`. `services/handlers/` shrank from
  12.8K → 1.1K LOC.
- **Wave 11.H** — Self-contained plugin folders. Six generic
  registries replace per-plugin hardcoding in core (five at 11.H;
  `register_router` landed in 11.I), plus newer `register_*`
  entrypoints for webhook sources / option loaders / OAuth callback
  paths / canary trigger types. Telegram is the reference.
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
