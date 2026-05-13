# Node Creation Guide

> **Companion docs:** [Plugin System](./plugin_system.md) (Wave 11 architecture + Wave 12 event framework) and the inline [Nodes Cookbook](../server/nodes/README.md) next to the plugin files.

This guide is a fast index for adding a new node to MachinaOs. The
deep technical details live in [`plugin_system.md`](./plugin_system.md);
this file picks the right entry point based on what you're adding.

## Decision tree

| You're adding… | Read this | Boilerplate |
|---|---|---|
| A simple action node (one HTTP call, no state) | [Quick start](./plugin_system.md#quick-start--adding-a-new-node) | one `<name>.py` file under `server/nodes/<group>/` |
| A dual-purpose node (workflow node + AI tool) | [Dual-Purpose Tool Guide](./dual_purpose_tool_node_creation.md) | one `<name>.py` with `usable_as_tool = True` |
| A specialized AI agent | [Specialized Agent Guide](./specialized_agent_node_creation.md) | one `<name>.py` under `server/nodes/agent/` |
| A standalone AI-tool node (no workflow surface) | [AI Tool Node Guide](./ai_tool_node_creation.md) | one `<name>.py` under `server/nodes/tool/` |
| A node that wraps a CLI tool, supervises a daemon, or receives signed webhooks | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) (this section is the most important one) | self-contained folder under `server/nodes/<group>/` using `services.events` base classes |
| A polling-based trigger node | [Wave 12 event framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — subclass `PollingEventSource` | new file or folder; framework owns the loop |
| A long-lived service plugin (bot connection, WebSocket bridge, SDK session) | [Self-contained plugin folders (Wave 11.H)](./plugin_system.md#self-contained-plugin-folders) — telegram is the reference | folder with `_credentials.py` / `_service.py` / `_handlers.py` / `_filters.py` / `_refresh.py` |
| A plugin whose auth is owned by an external CLI (`stripe login`, `gh auth login`, `gcloud auth login`) | [Stripe Service](./stripe_service.md) — the reference for "marker-token + generic catalogue invalidation" — and [plugin_system.md → CLI-managed auth pattern](./plugin_system.md#cli-managed-auth-pattern) | self-contained folder + `_install.py` for the auto-downloader; `_handlers.py` calls `auth_service.store_oauth_tokens(provider, "cli-managed", "cli-managed")` after the CLI login completes, then broadcasts the existing generic `credential_catalogue_updated` event |

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
case):

```python
# server/nodes/<group>/<name>.py
from pydantic import BaseModel, Field
from services.plugin import (
    ActionNode, ApiKeyCredential, NodeContext, Operation, TaskQueue,
)


class MyCredential(ApiKeyCredential):
    id = "my_api"
    display_name = "My Service"
    category = "Search"
    key_name = "X-API-Key"


class MyParams(BaseModel):
    query: str = Field(..., min_length=1)


class MyOutput(BaseModel):
    results: list


class MyNode(ActionNode):
    type = "myNode"
    display_name = "My Node"
    icon = "asset:my"
    color = "#abcdef"
    group = ("search", "tool")
    description = "Brief description for palette + AI tool."
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    credentials = (MyCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    Params = MyParams
    Output = MyOutput

    @Operation("search")
    async def search(self, ctx: NodeContext, params: MyParams) -> MyOutput:
        async with ctx.connection("my_api") as conn:
            resp = await conn.get(
                "https://api.example.com/search",
                params={"q": params.query},
            )
        return MyOutput(results=resp.json().get("hits", []))
```

That's the entire node. On server restart it auto-registers, the
NodeSpec is emitted at `/api/schemas/nodes/myNode/spec.json`, and it
appears in the Component Palette under the `search` group.

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
   with the provider id matching the catalogue's `status_hook`. The
   strings exist purely to flip
   `auth_service.get_oauth_tokens(status_hook) is not None` true,
   which the catalogue handler at
   [`server/routers/websocket.py:handle_get_credential_catalogue`](../server/routers/websocket.py)
   uses to set `provider.stored = true`. **Same API path Google's
   OAuth callback uses** — no new abstraction.

   ```python
   await auth_service.store_oauth_tokens(
       provider="stripe",                # matches status_hook in catalogue JSON
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

## What auto-wires (don't write it yourself)

When a plugin file is imported (which the `nodes/__init__.py`
walker does on startup), these registrations happen automatically:

| Mechanism | Where | When |
|---|---|---|
| Node class registration | `_NODE_CLASS_REGISTRY` | `BaseNode.__init_subclass__` on class definition |
| Metadata + Pydantic schemas | `NODE_METADATA`, `_DIRECT_MODELS`, `NODE_OUTPUT_SCHEMAS` | same |
| Handler dispatch | `_PLUGIN_HANDLERS` (merged into `NodeExecutor`) | same |
| Auto-derived `uiHints.isConfigNode: True` | `NODE_METADATA[type]['uiHints']` | `_metadata_dict` runs `_derive_auto_ui_hints(cls.group)` for every plugin in a `('memory', 'tool')` group. Plugin `ui_hints = {...}` always wins. Tells the frontend that the node's panel inherits its parent's main inputs. See [plugin_system.md → Auto-derived uiHints](./plugin_system.md#auto-derived-uihints). |
| Credentials | `CREDENTIAL_REGISTRY` | `Credential.__init_subclass__` when `_credentials.py` is imported |
| Trigger registry + filter builders | `event_waiter.TRIGGER_REGISTRY`, `FILTER_BUILDERS` | back-fill from `TriggerNode` subclasses on first lookup |
| Temporal activity wrapper | `cls.as_activity()` | first call; pooled into the worker queue declared by `task_queue` |
| Palette icon + color | `nodes/visuals.json` | central source of truth (frontend resolver reads it) |

What you **do** still write:

- An entry in `server/nodes/visuals.json` for the icon + color
  (unless the node sets them as class attributes, which override).
- An entry in `server/nodes/groups.py` if you introduce a new
  palette group.
- An asset in `client/src/assets/icons/<name>.svg` for `asset:<name>`
  icons.

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
| Dual-purpose nodes (workflow node + AI tool) | [dual_purpose_tool_node_creation.md](./dual_purpose_tool_node_creation.md) |
| Specialized AI agents | [specialized_agent_node_creation.md](./specialized_agent_node_creation.md) |
| Polling triggers + event_waiter mechanics | [event_waiter_system.md](./event_waiter_system.md) |
| Process supervision (used by `DaemonEventSource`) | [server/services/process_service.py](../server/services/process_service.py) — singleton API |

## Wave summary (current state)

- **Wave 11** — Class-based plugin system. 9 Temporal worker pools;
  plugin count via `glob server/nodes/**/__init__.py`; invariant total
  via `pytest --collect-only`. `services/handlers/` shrank from
  12.8K → 1.1K LOC.
- **Wave 11.H** — Self-contained plugin folders. Five generic
  registries replace per-plugin hardcoding in core. Telegram is the
  reference.
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
