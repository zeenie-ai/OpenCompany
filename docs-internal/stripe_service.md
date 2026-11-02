# Stripe Service

Stripe integration via the official [Stripe CLI](https://stripe.com/docs/stripe-cli).
Two workflow nodes:

- **`stripeAction`** (dual-purpose ActionNode + AI tool) — runs any
  `stripe …` command via subprocess and returns parsed JSON.
- **`stripeReceive`** (TriggerNode) — fires when `stripe listen`
  forwards a webhook event to OpenCompany at `/webhook/stripe`.

Stripe is the reference implementation of the Wave 12 event framework
documented in [Plugin System → Wave 12](./plugin_system.md#wave-12--generalized-event-framework-servicesevents).
Most of the heavy lifting (HMAC signature verification, daemon
supervision, lifecycle WebSocket handlers, status broadcasts, CLI
invocation) lives in [`services/events/`](../server/services/events/) —
this folder contributes only the Stripe-specific shapes.

> **How an event reaches `stripeReceive`.** Every Stripe event travels under
> one CloudEvents type, `com.opencompany.stripe.event.received`
> ([`_events.py`](../server/nodes/stripe/_events.py)), with the Stripe type
> in `subject` and in `data.event_type`, which the node's filter reads. A
> deployed trigger listens for exactly one type (the string
> `register_canary_trigger_type` records), so per-event types could never
> reach it. The source flattens each Stripe event once
> (`shape_stripe_event`), because a deployed trigger hands downstream nodes
> `event.data` verbatim, and delivers it twice: `WebhookSource.handle` wakes
> the in-process waiter (a canvas Run, or a deploy that runs without
> Temporal, the default on deployed VMs), and `emit_stripe_event` calls
> `dispatch.emit` for deployed listeners on Temporal. Deploying starts `stripe listen` through the generic
> `TriggerNode.prepare_deployment` hook, at Start and again when the boot
> re-arm restores a running or paused generation. The
> [`stripeReceive` card](./node-logic-flows/stripe/stripeReceive.md) walks
> through both paths.

## Architecture

```
                ┌────────────────────────────────────────────────────┐
                │              services/events/                      │
                │  WorkflowEvent, EventSource, WebhookTriggerNode,   │
                │  DaemonEventSource, StripeVerifier, run_cli_command│
                │  make_lifecycle_handlers, make_status_refresh      │
                └──────────────────────┬─────────────────────────────┘
                                       │ subclassed by
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ StripeListenSrc  │         │ StripeWebhookSrc │         │ StripeAction     │
│ (DaemonEvent     │         │ (WebhookSource)  │         │ Node             │
│  Source)         │         │                  │         │ (ActionNode      │
│                  │         │  path = "stripe" │         │  + AI tool)      │
│ supervises       │         │  verifier =      │         │                  │
│ `stripe listen`  │         │    StripeVerifier│         │ runs any         │
│ via              │         │  shape() →       │         │ `stripe ...`     │
│ ProcessService   │         │  WorkflowEvent   │         │ via              │
│                  │         │                  │         │ run_cli_command  │
│ captures whsec_  │         └──────────────────┘         └──────────────────┘
│ from stderr      │                  ▲                            ▲
│ banner           │                  │                            │
└──────────────────┘                  │                            │
        ▲                             │                            │
        │ start/stop/status           │ POST /webhook/stripe       │ subprocess
        │                             │                            │
   stripe CLI subprocess         stripe listen ──forwards─▶ OpenCompany
   (long-lived daemon)           (running daemon writes to localhost)
```

### Request flow — incoming webhook event

```
Stripe (cloud)
   │ event fires
   ▼
stripe listen (local daemon, supervised by StripeListenSource)
   │ forwards to --forward-to URL with Stripe-Signature header
   ▼
POST http://localhost:{port}/webhook/stripe
   │
   ▼
routers/webhook.py:handle_webhook
   │ if path in WEBHOOK_SOURCES → delegate
   ▼
StripeWebhookSource.handle(request)
   │
   ├── verifier.verify(headers, body, secret)
   │      Stripe-Signature: t=<ts>,v1=<hmac>
   │      raises ValueError → HTTPException(400)
   │
   ├── shape(request, body, payload)
   │      data  = shape_stripe_event(payload)   (event_id, event_type, created,
   │                                             livemode, api_version, request_id,
   │                                             account, data)
   │      → WorkflowEvent(id=evt_…, type="com.opencompany.stripe.event.received",
   │                      subject="charge.succeeded", source="stripe://acct_…",
   │                      data=data)
   │
   ├── self.receive(event)             (queues it on the source)
   │
   ├── event_waiter.dispatch(event)    (matched on event.type: a canvas Run's
   │                                    stripeReceive waiter, or a deploy that
   │                                    runs without Temporal)
   │
   └── emit_stripe_event(event) → dispatch.emit
          ▼
   deployed stripeReceive listeners (EventType = the same type);
   evaluate_trigger_filter_activity runs build_filter on event.data,
   and the run starts with event.data as the trigger's output
```

### Request flow — outgoing CLI action

```
StripeActionNode.run(params)
   │ params.command = "customers create --email a@b.com"
   ▼
shlex.split(command)
   │ → ["customers", "create", "--email", "a@b.com"]
   ▼
ensure_stripe_cli() → absolute binary path
   ▼
run_cli_command(binary=<abs path>, argv=…)     # NO credential= injection
   │
   ├── asyncio.create_subprocess_exec(
   │       binary, *argv,                        # plain argv, no --api-key
   │       stdout=PIPE, stderr=PIPE,
   │   )
   │   (CLI reads creds from ~/.config/stripe/config.toml)
   ├── asyncio.wait_for(proc.communicate(), timeout=30.0)
   ├── json.loads(stdout) on success
   ▼
{"success": True, "result": {...}, "stdout": "..."}
```

### Login lifecycle (browser OAuth + auto-install)

```
WS message: stripe_login
   │
   ▼
handle_stripe_login()
   │
   ├── ensure_stripe_cli()    ← _install.py
   │     ├── system PATH lookup (brew/scoop/apt)
   │     ├── package cache: <DATA_DIR>/packages/stripe/bin/stripe[.exe]
   │     └── on miss: download v1.40.9 from github.com/stripe/stripe-cli/releases
   │     returns absolute binary path
   │
   ├── run_cli_command([binary, "login", "--non-interactive"], timeout=10s)
   │     (CLI prints {browser_url, verification_code, next_step} JSON
   │      and exits in ~1s)
   │
   ├── return {success, url, verification_code} to the frontend
   │     (modal opens the URL in a new tab, displays the code)
   │
   └── pre_mtime = config.toml mtime  (0.0 if absent — snapshot BEFORE step 2)
       asyncio.create_task(_complete_login(binary, next_step, pre_mtime))
         │
         ▼
       run_cli_command([binary, "login", "--complete", next_step], timeout=600s)
         │
         │  (CLI polls Stripe; user authorises in browser; CLI writes
         │   credentials to ~/.config/stripe/config.toml. NB: the CLI may
         │   exit 1 with stderr 'exceeded max attempts' even after a
         │   successful write — exit code alone is not trusted.)
         │
         ├── post_mtime = config.toml mtime
         │   fresh_credentials_written = post_mtime > pre_mtime AND is_logged_in()
         │   (mtime advance is ground truth: is_logged_in() alone is true
         │    for ANY prior login, so it can't tell THIS attempt apart)
         │
         ├── if fresh_credentials_written:
         │     ├── _mark_logged_in()           ← marker-token write
         │     │     auth_service.store_oauth_tokens(
         │     │         provider="stripe",
         │     │         access_token="cli-managed",
         │     │         refresh_token="cli-managed",
         │     │     )
         │     │     (catalogue's get_oauth_tokens("stripe") now flips truthy)
         │     │
         │     └── get_listen_source().start()  ← daemon auto-starts
         │
         └── _broadcast_credential_event("credential.oauth.connected")
               → broadcaster.broadcast_credential_event(
                     "credential.oauth.connected", provider="stripe")
               (CloudEvents v1.0 envelope wrapped under the
               'credential_catalogue_updated' wire-format type.
               Same shape twitter_logout / google_logout /
               save_api_key use; locked by
               tests/credentials/test_credential_broadcasts.py.)
               frontend's existing case-handler invalidates the catalogue
               query → modal sees provider.stored = true → connection
               indicator flips immediately

WS message: stripe_logout
   ▼
handle_stripe_logout()
   ├── get_listen_source().stop()
   ├── run_cli_command([binary, "logout", "--all"])
   │     (clears ~/.config/stripe/config.toml; if this process has not
   │      resolved the CLI yet, e.g. right after a restart, the handler
   │      deletes that file directly instead)
   ├── _mark_logged_out()                      ← marker-token clear
   │     auth_service.remove_oauth_tokens("stripe")
   └── _broadcast_credential_event("credential.oauth.disconnected")
         (modal flips back to "Not Connected")
```

### Daemon lifecycle (post-login)

```
StripeListenSource.start()  (lock-protected, idempotent)
   │
   ├── ensure_stripe_cli()                     ← override before super().start()
   ├── has_credential() → is_logged_in()       ← override of the daemon gate
   │     (filesystem check on ~/.config/stripe/config.toml)
   ├── binary_name = ""                        ← framework PATH check skipped
   ├── ProcessService.start(
   │       name="stripe-listen",
   │       command="<shlex-quoted-binary> listen
   │                --forward-to http://localhost:{port}/webhook/stripe
   │                --print-secret",            # no --api-key
   │       workflow_id="_stripe",             # the source's workflow_namespace
   │       working_directory=<DATA_DIR>/daemons,   # shared daemons_dir() root
   │       line_handler=self._on_line,           # ← per-line callback
   │   )
   │   (CLI reads credentials from its config file; binary path is
   │    shlex.quote'd so Windows backslashes survive ProcessService's
   │    POSIX-mode shlex.split round-trip)
   │
   └── ProcessService loops `stream.readline()` per stdout/stderr,
       decodes UTF-8, writes to stdout.log / stderr.log, broadcasts to
       the Terminal tab, AND calls our line_handler:
         _on_line(stream, line) → parse_line(stream, line)
            on stderr match r"whsec_[A-Za-z0-9_]+":
               auth_service.store_api_key("stripe_webhook_secret", …)
       (No file-tailing; we hook into the same readline loop ProcessService
        already runs.)

StripeListenSource.stop()
   └── ProcessService.stop("stripe-listen", "_stripe")
```

## Key Files

| File | Description |
|---|---|
| `server/nodes/stripe/__init__.py` | Wiring: `register_ws_handlers`, `register_webhook_source`, `register_canary_trigger_type("stripeReceive", STRIPE_EVENT_RECEIVED_TYPE)` (the second argument must equal the type on every envelope the source emits, or the deployed listener's Visibility match silently fails), `register_service_refresh` (with `make_status_refresh`) and `register_output_schema` for both nodes. |
| `server/nodes/stripe/_events.py` | `STRIPE_EVENT_RECEIVED_TYPE` (`com.opencompany.stripe.event.received`), the `stripe_event_received` CloudEvents factory (id = Stripe's `evt_` id, so a redelivery is a duplicate; no `workflow_id`, so every deployment with the trigger receives it) and `emit_stripe_event`, the `dispatch.emit` call that reaches deployed listeners. |
| `server/nodes/stripe/_credentials.py` | `StripeCredential(Credential)` — thin marker class. The CLI manages auth at `~/.config/stripe/config.toml`; this class only exposes the captured `stripe_webhook_secret` for the framework's signature-verifier path. |
| `server/nodes/stripe/_install.py` | `ensure_stripe_cli()` — async, idempotent, lock-guarded. Resolves the binary path: in-process cache → system PATH → previously-downloaded copy at `<DATA_DIR>/packages/stripe/bin/stripe[.exe]` (`core.paths.package_dir("stripe") / "bin"`) → fresh download from GitHub releases (pinned `_VERSION = "1.40.9"`) into that same dir. Asset-name map covers Windows AMD64, Linux x86_64/arm64, macOS x86_64/arm64. Subsequent calls hit the cache instantly. |
| `server/skills/payments_agent/stripe-skill/SKILL.md` | LLM teaching markdown for the `stripe_action` tool, covering customers, charges, payment_intents, refunds, invoices, products/prices, subscriptions, the `trigger` command, common workflows, quoting/escaping, idempotency, test vs live mode, error patterns, and webhook delivery. |
| `server/config/credential_providers.json` | JSON-driven Credentials Modal catalogue. The `payments` category + `stripe` provider entry tell the frontend modal to render a **Login with Stripe** button (no API-key field) wired to the `stripe_login` / `stripe_logout` / `stripe_status` WebSocket handlers. No React file edits required. |
| `server/nodes/stripe/_source.py` | `StripeListenSource(DaemonEventSource)` and `StripeWebhookSource(WebhookSource)` plus their singletons. |
| `server/nodes/stripe/_handlers.py` | WS handlers via `make_lifecycle_handlers` (`stripe_connect` / `stripe_disconnect` / `stripe_reconnect`) plus the plugin-specific `stripe_login`, `stripe_logout`, `stripe_trigger` (synthetic test events) and `stripe_status` (overrides the factory's status handler to add login state). See [WebSocket handlers](#websocket-handlers). |
| `server/nodes/stripe/stripe_action.py` | `StripeActionNode` — pass-through over the CLI via `run_cli_command`. Its `tool_name = "stripe_action"` and the matching `tool_description` are what the LLM sees when the node is wired to an agent's `input-tools` handle. |
| `server/nodes/stripe/stripe_receive.py` | `StripeReceiveNode(WebhookTriggerNode)`: `event_type = STRIPE_EVENT_RECEIVED_TYPE` (the canvas-Run waiter key), a `build_filter` over the shaped data (event type and livemode), `shape_output` returning that data, the canvas-Run precondition and the `prepare_deployment` hook, both of which start `stripe listen`. |
| `server/services/plugin/trigger.py` / `server/services/deployment/manager.py` | `TriggerNode.prepare_deployment` (default: nothing) and `DeploymentManager._prepare_trigger_deployment`, which calls it for every trigger it arms, at Start and at the boot re-arm, and logs a raise without failing the deploy. |
| `server/services/events/__init__.py` | Public framework surface — exports every base class + helper. |
| `server/services/events/daemon.py` | `DaemonEventSource` — supervises subprocess via `ProcessService`. Subscribes to ProcessService's per-line callback (`line_handler`) instead of re-tailing the on-disk log files. Credential gate is `await self.has_credential()` so non-api-key auth (Stripe → `is_logged_in()`) plugs in via subclass override. |
| `server/services/process_service.py` | Spawns + supervises long-lived subprocesses. Loops `stream.readline()` per stdout/stderr, writes to `.log`, broadcasts to Terminal, and forwards each decoded line to the optional `line_handler` async callback (Wave 12.B addition for typed event-source subscribers — see `DaemonEventSource._on_line`). |
| `server/services/events/webhook.py` | `WebhookSource` + `WEBHOOK_SOURCES` registry + `register_webhook_source`. |
| `server/services/events/triggers.py` | `WebhookTriggerNode` + `BaseTriggerParams`. |
| `server/services/events/cli.py` | `run_cli_command` helper. |
| `server/services/events/lifecycle.py` | `make_lifecycle_handlers` + `make_status_refresh`. |
| `server/services/events/verifiers/stripe.py` | `StripeVerifier` (`t=…,v1=…` HMAC-SHA256). |
| `server/routers/webhook.py` | Path-handler arm: consults `WEBHOOK_SOURCES` before falling through to legacy generic dispatch. |
| `server/nodes/visuals.json` | `stripeAction` skill map only (`{"skill": "stripe-skill"}`); no `stripeReceive` key. The `asset:stripe` icon and `#635BFF` colour come from `nodes/groups.py` (`payments` group) and `nodes/stripe/meta.json`. |
| `server/nodes/groups.py` | `payments` palette group. |
| `server/credentials/icons/stripe.svg` | Stripe credential-tile icon, served at `/api/schemas/credentials/stripe/icon`. |
| `server/tests/services/test_events.py` | Framework tests (envelope, verifiers, polling/daemon lifecycle, WebhookSource). |
| `server/tests/nodes/test_stripe_plugin.py` | Stripe-specific tests: shaping, the filter on both paths, a canvas Run resolving, the deployed path (deployable, one canary type matching every emitted envelope, no `workflow_id`, the filter not failing open), `prepare_deployment`, action passthrough and registrations. `TestTriggerPrepareDeployment` in `tests/test_deployment_canary_listener.py` covers the manager's hook call. |

## Plugin classes

### `StripeCredential`

Thin marker class. The Stripe CLI handles its own auth state at
`~/.config/stripe/config.toml`; nothing API-key-shaped lives in
OpenCompany's auth_service for Stripe. Only the captured webhook
signing secret rides as an extra field.

```python
class StripeCredential(Credential):
    id = "stripe"
    display_name = "Stripe"
    category = "Payments"
    # Icon resolved per-plugin via nodes/stripe/icon.svg (Phase 9 +
    # F7 closure). Credential brand icon lives at
    # server/credentials/icons/stripe.svg and is served by
    # GET /api/schemas/credentials/stripe/icon (F7).
    auth = "custom"
    docs_url = "https://stripe.com/docs/cli"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        from services.plugin.deps import get_auth_service

        secret = await get_auth_service().get_api_key("stripe_webhook_secret")
        return {"stripe_webhook_secret": secret} if secret else {}
```

Storage:

| Key | Type | Origin | Purpose |
|---|---|---|---|
| (no `stripe_api_key`) | — | Stripe CLI's `~/.config/stripe/config.toml` (populated by `stripe login`) | Authenticates every CLI invocation transparently. The CLI generates restricted keys with CLI-appropriate scopes — one for live mode, one for sandbox — valid 90 days. |
| `stripe_webhook_secret` | API key (extra field) | Auto-captured by `StripeListenSource.parse_line` from the daemon's stderr banner | Verifies forwarded webhook signatures via `StripeVerifier`. Stable across daemon restarts; the CLI re-uses the same secret for the same OpenCompany install. |
| OAuth marker token | OAuth token (`auth_service.store_oauth_tokens`) | Written by `_mark_logged_in()` after `stripe login --complete` exits 0; cleared by `_mark_logged_out()` on disconnect. **Strings are dummies (`"cli-managed"`)** — the real OAuth lives in the CLI's config file. | Lights up the catalogue's `provider.stored = true` flag via the existing `kind == "oauth"` branch of `provider_connection_state` (`auth_service.get_oauth_tokens("stripe")`). Same `store_oauth_tokens` API Google's OAuth callback uses; no new abstraction. |

### `StripeListenSource(DaemonEventSource)`

Supervises `stripe listen` as a long-lived process. Inherits the
full `DaemonEventSource` lifecycle (start / stop / restart / status)
plus the per-line callback subscription via `ProcessService`'s
`line_handler` hook. The Stripe-specific overrides are minimal:

| Method / attr | Purpose |
|---|---|
| `process_name = "stripe-listen"` | Key used by `ProcessService` to track this daemon. |
| `binary_name = ""` | **Empty** — disables `DaemonEventSource`'s built-in `shutil.which` PATH check. The plugin handles install + verification itself via `ensure_stripe_cli()`, which falls back to a download into `<DATA_DIR>/packages/stripe/`. |
| `workflow_namespace = "_stripe"` | The `ProcessService` workflow-id key for this daemon. NOTE: `DaemonEventSource.workdir()` now returns `daemons_dir()` itself (the shared `<DATA_DIR>/daemons/` root) — `workflow_namespace` is a logical process key, not a per-namespace directory. Pre-fix it carved `{workspace_base}/_stripe/` and left an empty dir behind under per-workflow scratch. |
| `install_hint` | Surfaced in the "install failed" error path when the auto-installer can't reach GitHub releases. |
| `credential = StripeCredential` | Resolved by the framework before `build_command` is called. |
| `start()` (override) | `await ensure_stripe_cli()` then `super().start()`. Caches the resolved binary path so `build_command` (sync) can pick it up. |
| `build_command(secrets)` | Returns `<shlex.quote'd-binary> listen --forward-to … --print-secret`. The binary path is `shlex.quote`d so it round-trips through `ProcessService`'s POSIX-mode `shlex.split` unchanged. No `--api-key`: CLI reads its own config file. |
| `has_credential()` (override) | Returns `is_logged_in()` — a filesystem check on `~/.config/stripe/config.toml`. Consulted by `DaemonEventSource.start` (the credential gate) and by the trigger's demand-driven starts: `StripeReceiveNode._check_precondition` on a canvas Run, and `prepare_deployment` when a deployment arms the trigger. The status refresh is a passive probe since July 2026. |
| `parse_line(stream, line)` | Invoked once per decoded stdout/stderr line via `ProcessService`'s `line_handler` callback. On `whsec_…` match, persists the secret via `auth_service.store_api_key("stripe_webhook_secret", …)`. The Stripe daemon doesn't emit workflow events itself — they arrive via the webhook receiver. |

### `StripeWebhookSource(WebhookSource)`

Receives forwarded events at `/webhook/stripe`. The framework owns
signature verification, JSON parsing, and `event_waiter.dispatch`;
this class declares the path, the verifier, the secret-field name and
the shaping, and adds the deployed-path emit:

```python
class StripeWebhookSource(WebhookSource):
    type = "stripe.webhook"                       # the source's own id; no envelope carries it
    path = "stripe"
    verifier = StripeVerifier
    secret_field = "stripe_webhook_secret"
    credential = StripeCredential

    async def shape(self, request, body, payload) -> WorkflowEvent:
        created = payload.get("created")
        time = ...                                # created, else now (UTC)
        data = shape_stripe_event(payload)        # the trigger's output fields
        return stripe_event_received(
            data, event_id=data["event_id"], account=data["account"], time=time,
        )                                         # type = com.opencompany.stripe.event.received

    async def handle(self, request: Request) -> WorkflowEvent:
        event = await super().handle(request)     # verify, shape, wake the in-process waiter
        await emit_stripe_event(event)            # reach deployed listeners on Temporal
        return event
```

The `id` mirrors Stripe's `evt_…`, so a redelivery (Stripe retries on 5xx)
carries the same id and a deployed listener drops it as a duplicate.

### `StripeReceiveNode(WebhookTriggerNode)`

```python
class StripeReceiveParams(BaseTriggerParams):
    livemode_filter: Literal["all", "test", "live"] = "all"


class StripeReceiveNode(WebhookTriggerNode):
    type = "stripeReceive"
    display_name = "Stripe Receive"
    subtitle = "Webhook Event"
    group = ("payments", "trigger")
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    credentials = (StripeCredential,)
    webhook_source = StripeWebhookSource
    event_type = STRIPE_EVENT_RECEIVED_TYPE           # the waiter key: the type the source emits
    Params = StripeReceiveParams
    Output = StripeReceiveOutput

    def build_filter(self, params):
        # Match data["event_type"] against event_type_filter (exact, "prefix.*"
        # or "all"; a leading "stripe." is stripped) and data["livemode"]
        # against livemode_filter. Both callers pass the envelope's data.
        ...

    async def _check_precondition(self) -> Optional[str]:
        # Canvas Run: start the listen daemon on demand; refuse if the CLI is not logged in.
        ...

    @classmethod
    async def prepare_deployment(cls, *, node_id, workflow_id, parameters) -> None:
        # Start and boot re-arm: start the listen daemon in a background task.
        ...

    def shape_output(self, event: WorkflowEvent) -> Dict:
        return event.data                             # already shaped by the source
```

The framework's `WebhookTriggerNode` supplies the `_check_precondition`
short-circuit, the re-wrap of the waiter's `data` into an envelope
before `shape_output`, and the `Operation("wait")` stub. This class
overrides `build_filter` because every Stripe event shares one
CloudEvents type, so the Stripe type has to come from the data.

### `StripeActionNode(ActionNode)` — dual-purpose

```python
class StripeActionParams(BaseModel):
    command: str = Field(default="", description=...)


class StripeActionOutput(BaseModel):
    command: Optional[str] = None
    success: Optional[bool] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    error: Optional[str] = None


class StripeActionNode(ActionNode):
    type = "stripeAction"
    group = ("payments", "tool")
    credentials = (StripeCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    @Operation("run", cost={"service": "stripe", "action": "run", "count": 1})
    async def run(self, ctx, params):
        cmd = params.command.strip()
        if not cmd:
            raise RuntimeError("command is required")
        # No credential= — Stripe CLI reads its own creds from
        # ~/.config/stripe/config.toml after `stripe login`.
        binary = str(await ensure_stripe_cli())
        result = await run_cli_command(binary=binary, argv=shlex.split(cmd))
        if not result["success"]:
            raise RuntimeError(result.get("error") or "Stripe CLI invocation failed")
        return {
            "command": cmd, "success": True,
            "result": result.get("result"), "stdout": result.get("stdout"),
        }
```

The CLI does its own argument parsing, validation, and error
messages. We don't re-implement per-resource operations — the user
(or LLM) types the command exactly as they would after `stripe `:

| Example command | What it does |
|---|---|
| `customers create --email a@b.com --name "Acme Inc"` | Create a Stripe customer |
| `customers list --limit 10` | List recent customers |
| `payment_intents create --amount 2000 --currency usd --customer cus_…` | Create a PaymentIntent |
| `refunds create --payment-intent pi_…` | Refund a PaymentIntent |
| `charges retrieve ch_…` | Fetch a charge |
| `trigger charge.succeeded` | Fire a synthetic test event (also exposed via the `stripe_trigger` WebSocket handler) |

All Stripe CLI commands are supported automatically; future Stripe
resources work without code changes.

## WebSocket handlers

The lifecycle factory `make_lifecycle_handlers(prefix="stripe",
source=…)` auto-generates `stripe_connect/disconnect/reconnect`
from the source's `start/stop/restart` methods (the daemon lifecycle;
no button in the modal sends them). The plugin-specific handlers wired
into the modal's **Login with Stripe** and **Disconnect** buttons are
`stripe_login` and `stripe_logout`:

| Type | Handler | Purpose |
|---|---|---|
| `stripe_login` | `ensure_stripe_cli()` → `stripe login --non-interactive` (sync) → returns `{url, verification_code}` to the frontend; spawns background `_complete_login` task | **Modal "Login with Stripe" button** |
| `stripe_logout` | stops daemon → `stripe logout --all` → `_mark_logged_out()` → `_broadcast_credential_event("credential.oauth.disconnected")` | **Modal Disconnect button** |
| `stripe_status` | returns `{success, status: {type, running, pid, logged_in, connected}}`, where `connected = running and logged_in` | The modal's **Refresh** button sends it; the connection indicator still comes from the catalogue refetch, not from this reply |
| `stripe_trigger` | passes `["trigger", event]` to `run_cli_command` (after `ensure_stripe_cli`) | Synthetic test event |
| `stripe_connect/disconnect/reconnect` | `source.start/stop/restart()` from the lifecycle factory | Daemon-only lifecycle. No button in the Credentials modal sends these |

Background `_complete_login(binary, next_step, pre_mtime)` flow:

1. `next_step` from step 1 is a literal shell command (`stripe login --complete '<URL>'`); extract the auth URL with `shlex.split(next_step)[-1]` before passing it to `--complete`. `pre_mtime` is the `config.toml` mtime captured by `handle_stripe_login` *before* this task spawned (`0.0` if the file was absent).
2. `run_cli_command([binary, "login", "--complete", complete_url], timeout=600s)` blocks until OAuth completes. The CLI may exit `1` with `stderr='exceeded max attempts'` even after a successful write, so its exit code is not trusted on its own.
3. Success is declared only when `post_mtime > pre_mtime AND is_logged_in()`. The mtime advance is the disambiguator: `is_logged_in()` is a bare "config contains `_api_key`" check that is true for *any* prior login (the Stripe CLI owns that file globally), so it cannot tell *this* attempt apart from a stale leftover. The `exceeded max attempts` stderr is forgiven when the mtime actually advanced.
4. `_mark_logged_in()` writes `auth_service.store_oauth_tokens("stripe", "cli-managed", "cli-managed")`.
5. `get_listen_source().start()` spawns the supervised `stripe listen` daemon; the `whsec_…` banner is captured via the `line_handler` callback.
6. `_broadcast_credential_event("credential.oauth.connected")` emits a CloudEvents v1.0 envelope (`WorkflowEvent`) via `StatusBroadcaster.broadcast_credential_event`, wrapped under the `credential_catalogue_updated` wire-format type. The frontend invalidates the catalogue and `provider.stored = true` flips the connection indicator.

## AI tool surface (`stripe_action`)

When `StripeActionNode` is wired to an agent's `input-tools` handle,
the LLM sees a tool named `stripe_action` (snake_case of the
`stripeAction` node type). Three coordinates have to agree for both
the LLM's tool-call resolver and the skill's icon resolver to find
their target:

| Place | Value | File |
|---|---|---|
| Node `type` (camelCase) | `stripeAction` | [`server/nodes/stripe/stripe_action.py`](../server/nodes/stripe/stripe_action.py) |
| LLM tool name (snake_case of node type) | `stripe_action` | [`server/nodes/stripe/stripe_action.py`](../server/nodes/stripe/stripe_action.py) — `tool_name = "stripe_action"` (resolved via `services/node_registry.py`) |
| Skill `allowed-tools` (matches LLM tool name) | `stripe_action` | [`server/skills/payments_agent/stripe-skill/SKILL.md`](../server/skills/payments_agent/stripe-skill/SKILL.md) |
| `visuals.json` key (= node type) | `stripeAction` with `"skill": "stripe-skill"` | [`server/nodes/visuals.json`](../server/nodes/visuals.json) |

The skill resolver in `SkillLoader._parse_skill_metadata` runs each
`allowed-tools` token through snake → camel (e.g. `stripe_action` →
`stripeAction`) and looks the result up in `visuals.json` to source
the skill's icon and color. The skill renders without an icon if
those don't agree — see the **Common pitfall** callout in
[`server/skills/GUIDE.md`](../server/skills/GUIDE.md#node-types-callables-and-visual-aliases).

## Skill — `payments_agent/stripe-skill`

[`server/skills/payments_agent/stripe-skill/SKILL.md`](../server/skills/payments_agent/stripe-skill/SKILL.md)
is the LLM-facing manual for the `stripe_action` tool. It teaches:

- The single `command` field that mirrors what you'd type after
  `stripe ` on the terminal.
- The full Stripe CLI command surface organised by resource:
  customers, charges, PaymentIntents, refunds, invoices, products,
  prices, subscriptions, plus `trigger <event>` for synthetic
  webhook events.
- Common multi-step workflows (create-customer-then-charge,
  refund-most-recent-payment, set-up-recurring-subscription).
- Quoting and escaping rules — the `command` string is `shlex.split`
  on the backend, so single-quote arguments containing spaces.
- Idempotency keys via the CLI's `-H "Idempotency-Key: …"` flag.
- Test vs live mode (key prefix = mode: `sk_test_` / `sk_live_`;
  prefer restricted keys `rk_*` in production).
- Common Stripe error codes (`resource_missing`, `card_declined`,
  `invalid_request_error`, `authentication_required`,
  `rate_limit_error`) and how to recover from each.
- Webhook delivery via `stripeReceive` — including the
  `event_type_filter` glob patterns (`charge.*`, `payment_intent.*`,
  exact, `all`).
- Best practices: restricted keys in production, never paste a key
  into the `command` string (the CLI gets it from the stored
  credential automatically), surface Stripe error messages verbatim
  to the user.

The `payments_agent/` folder is the skill folder for payments,
alongside the other folders under `server/skills/`. Other payments
integrations (PayPal CLI, Square, etc.) would go in the same folder.

## Credentials Modal integration

Stripe is wired into the JSON-driven Credentials Modal catalogue at
[`server/config/credential_providers.json`](../server/config/credential_providers.json):

```json
"payments": { "label": "Payments", "order": 9 },
…
"stripe": {
  "name": "Stripe",
  "category": "payments",
  "color": "dracula.purple",
  "kind": "oauth",
  "icon_ref": "/api/schemas/credentials/stripe/icon",
  "ws": {
    "login":  "stripe_login",
    "logout": "stripe_logout",
    "status": "stripe_status"
  },
  "instructions": "Click 'Login with Stripe' to open the Stripe Dashboard. After you authorise, the CLI stores credentials at ~/.config/stripe/config.toml and the listen daemon starts automatically. The Stripe CLI is downloaded automatically on first use unless one is already on PATH (manual install: https://stripe.com/docs/stripe-cli#install)."
}
```

Notice there is **no `fields` array** — unlike Telegram (bot token)
or Brave (subscription token), Stripe doesn't ask the user to paste
anything. The CLI runs the OAuth dance and persists its own
credentials.

The catalogue is read at startup by
[`server/services/credential_registry.py`](../server/services/credential_registry.py)
and served to the frontend via the `get_credential_catalogue`
WebSocket handler. The frontend Credentials Modal renders the
provider list directly from the catalogue — **no React file edits
required to add a new provider**, no `CredentialsModal.tsx` line
changes.

`kind: "oauth"` is the same pattern Twitter and Google use — the
Modal renders a "Login with X" button that calls `ws.login`,
expects a `{success, url}` response, opens that URL in a new tab,
and, for providers that declare a `status_hook`, listens for the
`<status_hook>_status` push broadcast to flip the connection
indicator. Stripe rides on the login half of this pattern with
zero new frontend code; it declares no `status_hook`, so its
indicator comes from the catalogue's `stored` field instead (see
[Status broadcasting](#status-broadcasting--marker-token--generic-catalogue-invalidation)).
The other difference is that Stripe's `ws.login`
returns the URL produced by `stripe login --non-interactive`
(rather than a URL we constructed via `oauth_utils.get_redirect_uri`
+ a Twitter/Google OAuth helper class), and there is no callback
route in OpenCompany — the CLI completes the OAuth on its own and
exits, at which point a background task in the `stripe_login`
handler kicks the daemon and broadcasts updated status.

## Webhook signature verification

`StripeVerifier` ([`server/services/events/verifiers/stripe.py`](../server/services/events/verifiers/stripe.py))
implements [Stripe's webhook signature scheme](https://stripe.com/docs/webhooks/signatures):

- Header format: `Stripe-Signature: t=<unix_ts>,v1=<hex_hmac>[,v1=<rotated>]`
- Signed payload: `f"{timestamp}.{raw_body}"`
- Algorithm: HMAC-SHA256 hex-encoded
- Multiple `v1=` entries are accepted (secret rotation)

Verifier raises `ValueError` on mismatch; `WebhookSource.handle`
catches it and returns HTTP 400. If the signing secret hasn't been
captured yet (race between first webhook and the `whsec_…` banner),
the framework **fails closed**: it logs a warning and rejects the
event with HTTP 503 + `Retry-After: 5` instead of accepting it
unverified. Stripe retries the delivery, so events arriving during
the first ~5 seconds of daemon startup are re-delivered once the
secret lands.

## Status broadcasting — marker token + generic catalogue invalidation

The frontend has **no Stripe-specific status handling**: no Zustand
entry and no `stripe_status` case in `WebSocketContext.tsx`. (The
backend does broadcast `stripe_status`, from `make_status_refresh`
below, but no frontend code reads it.) The modal relies on two
existing generic mechanisms:

### 1. The catalogue's authoritative `stored` field

The `get_credential_catalogue` handler in
[`server/routers/websocket.py`](../server/routers/websocket.py)
enriches every provider with `stored: bool` by calling
`provider_connection_state` in
[`server/services/credential_registry.py`](../server/services/credential_registry.py)
(the same function the Normal-mode employee summaries use). It picks
the check in this order: a declared `stored_check`, then a declared
`status_hook` (`get_oauth_tokens(status_hook)`), then `kind`. Stripe
declares neither `stored_check` nor `status_hook`, so it takes the
`kind == "oauth"` branch:

```python
elif kind == "oauth":
    tokens = await auth_service.get_oauth_tokens(pid)   # pid == "stripe"
    state["stored"] = tokens is not None
```

Google's OAuth callback writes real tokens via
`store_oauth_tokens("google", access_token, refresh_token, ...)`.
Stripe writes **synthetic marker strings** (`"cli-managed"`) through
the same API — the existing logic flips `stored: true` without any
provider-specific code in `provider_connection_state`. The
`auth_service.store_oauth_tokens` API doesn't validate the strings;
the marker exists purely to flip the existence check.

### 2. The CloudEvents-shaped credential broadcast

After `_mark_logged_in()` / `_mark_logged_out()`, the plugin emits a
CloudEvents v1.0 envelope via the canonical helper:

```python
await get_status_broadcaster().broadcast_credential_event(
    "credential.oauth.connected", provider="stripe",      # or .disconnected on logout
)
```

`StatusBroadcaster.broadcast_credential_event` wraps a `WorkflowEvent`
(from `services.events.envelope`) and ships it under the
`credential_catalogue_updated` wire-format type — the same shape
`save_api_key`, `delete_api_key`, `twitter_logout`, `google_logout`
already use. The contract is locked by
`tests/credentials/test_credential_broadcasts.py` (`inspect.getsource`
introspection over each handler).

`WebSocketContext.tsx` already has a generic
`case 'credential_catalogue_updated'` (predates Stripe). Its handler calls `invalidateCatalogue`
on the TanStack Query client; the catalogue refetches; the modal
sees the new `provider.stored` value and re-renders. **No
stripe-specific code anywhere on the frontend.**

### 3. `make_status_refresh` (passive status mirror)

The plugin registers `make_status_refresh`. Service refresh callbacks
run once, in a background task at startup (`_refresh_all_services`,
scheduled from the `main.py` lifespan), not on each WebSocket connect.
The callback mirrors `source.status()` (`type`, `running`, `pid`; no
login state) into `broadcaster._status["stripe"]` and broadcasts it as
`stripe_status`, which no frontend code handles. Since July 2026 the
refresh **never starts the daemon** — a stored credential alone is not
a reason to run `stripe listen`. The demand signals own the starts
instead: `_complete_login` after a successful login, the
`stripe_connect` command (no modal button sends it),
`StripeReceiveNode._check_precondition` on a canvas Run, and
`StripeReceiveNode.prepare_deployment` whenever a deployment arms the
trigger (Start, and the boot re-arm after a restart).

### Frontend `connected` derivation (single generic line)

[`client/src/components/credentials/panels/OAuthPanel.tsx`](../client/src/components/credentials/panels/OAuthPanel.tsx):

```tsx
const connected = status ? !!status.connected : !!config.stored;
```

Providers whose `statusHook` is mapped in `useProviderStatus`
(`client/src/components/credentials/hooks.ts`) keep their hook-driven
semantics. Providers without one (Stripe today, future CLI-managed-OAuth integrations
tomorrow) fall back to the catalogue's authoritative `config.stored`
field. **No `'stripe'` reference anywhere in the frontend.**

## Installation

The Stripe CLI is auto-installed on first use; manual install is
optional for users who prefer system package managers:

```bash
# macOS
brew install stripe/stripe-cli/stripe

# Windows (Scoop)
scoop install stripe

# Linux (apt)
echo "deb [signed-by=/usr/share/keyrings/stripe.gpg] https://packages.stripe.dev/stripe-cli-debian-local stable main" \
  | sudo tee /etc/apt/sources.list.d/stripe.list
sudo apt update && sudo apt install stripe

# Direct binary
# https://github.com/stripe/stripe-cli/releases
```

If none of these are present, the first click on **Login with Stripe**
triggers `ensure_stripe_cli()` which downloads the platform-matched
release archive (~12 MB) from `github.com/stripe/stripe-cli/releases`,
extracts the `stripe[.exe]` binary into
`<DATA_DIR>/packages/stripe/bin/`, and proceeds with login. Subsequent
calls hit the cache.

If the download fails (no internet, GitHub down), the WS
`stripe_login` response is:

```json
{
  "success": false,
  "error": "Stripe CLI install failed. Manual install: https://stripe.com/docs/stripe-cli#install"
}
```

## Configuration

No JSON config file. Everything plugin-configurable lives on the
class attributes:

| Knob | Where | Default |
|---|---|---|
| Daemon process name | `StripeListenSource.process_name` | `"stripe-listen"` |
| Binary name | `StripeListenSource.binary_name` | `""` (empty; disables the framework PATH check) |
| Daemon process key | `StripeListenSource.workflow_namespace` | `"_stripe"` (logical `ProcessService` key; cwd is the shared `daemons_dir()` = `<DATA_DIR>/daemons/`, not a per-namespace subdir) |
| Webhook path | `StripeWebhookSource.path` | `"stripe"` (i.e. `/webhook/stripe`) |
| Forward-to port | derived from `Settings().port` | the app port (`PYTHON_BACKEND_PORT`) |
| Verifier | `StripeWebhookSource.verifier` | `StripeVerifier` |
| Action operation cost | `@Operation("run", cost=…)` | `{service: "stripe", action: "run", count: 1}` |

The CLI's webhook secret (`whsec_…`) is captured at runtime and
persisted automatically — no manual config step.

## Credentials Modal UI

The Stripe panel lives in the Payments category (introduced
specifically for this plugin in the `payments` palette group). It
provides:

- **Login with Stripe** (shown until connected) → fires `stripe_login`.
  The handler returns a `{url, verification_code}` pair; the modal
  opens the URL in a new tab and shows the verification code so the
  user can confirm the pairing on the Stripe Dashboard.
- **Disconnect** (shown once connected) → fires `stripe_logout`, which
  stops the daemon and runs `stripe logout --all` to clear
  `~/.config/stripe/config.toml`.
- **Refresh** → sends `stripe_status` (the provider's `ws.status`).
- **Status indicator** — `OAuthPanel` computes
  `const connected = status ? !!status.connected : !!config.stored`.
  Stripe declares no `status_hook`, so `status` is null and the
  indicator follows the catalogue's `stored` flag.

There is no Reconnect button: the `stripe_reconnect` handler exists, but
nothing in the modal sends it. No webhook-secret input is needed, since
the daemon captures the secret automatically, but the modal does not
show whether it has been captured; the backend logs
`webhook signing secret persisted` when it is.

## Operational notes

### CLI-managed credentials, not OpenCompany-managed

There is no API key in `auth_service` for Stripe — the CLI persists
its own credentials at `~/.config/stripe/config.toml` (or
`$XDG_CONFIG_HOME/stripe/config.toml`). Implications:

* **No `--api-key` in command lines.** Daemons and one-shot CLI
  invocations run with plain argv; nothing leaks via `ps` or
  `ProcessService`'s logged command field.
* **`is_logged_in()` is a filesystem check.** A cheap sniff for
  `_api_key` substring in `config.toml`. `has_credential()` and the
  `stripe_status` `logged_in` field both use it.
* **Logout deletes the file.** `stripe logout --all` removes the
  profile section so the next start fails the `is_logged_in()` gate
  until the user re-logs in.
* **Multi-tenant deployments inherit a single config file.** That's
  a CLI limitation, not ours; switch to per-tenant `XDG_CONFIG_HOME`
  if needed.

### Webhook secret race window

The first ~5 seconds after `stripe_connect`, the secret-capture task
hasn't yet matched the `whsec_…` line in stderr. If a webhook arrives
during that window, the framework fails closed: it logs a warning and
rejects the event with HTTP 503 + `Retry-After: 5` (no unverified
acceptance). Stripe retries the delivery, so real events are simply
re-delivered once the secret lands; synthetic events triggered via
`stripe_trigger` immediately after connect can hit the 503 and are
retried by the CLI the same way.

### Single global daemon

One Stripe account per OpenCompany install. The daemon is a single
global process (`ProcessService` key `workflow_id="_stripe"`, the
source's `workflow_namespace`). Multi-account
support is deferred to a future revision; the design holds — give
`StripeListenSource` a `__init__(account_id)` and key the singleton
by id.

### No auto-restart on crash

If `stripe listen` exits unexpectedly, nothing restarts it, and nothing
reports it either. The source's `_started` flag is cleared only by
`stop()`, so `stripe_status` still reports `running: true`, and both
`StripeReceiveNode._check_precondition` and `prepare_deployment` skip
the start; the modal's
indicator follows the stored login marker, so Stripe still reads as
connected. The per-line `parse_line` callback simply stops receiving
lines when `ProcessService` reaps the process. To restart the daemon,
click Disconnect and then Login with Stripe. There's no
exponential-backoff respawn loop; that was deliberate, so a failing
daemon is not hidden behind silent retries.

## Verification

End-to-end smoke (requires Stripe CLI installed and a Stripe account):

1. **Login.** Credentials Modal → Stripe → "Login with Stripe". WS
   sends `{"type":"stripe_login"}` → reply contains `{url,
   verification_code}`. Open the URL, confirm the code on Stripe
   Dashboard, click Authorise. Within a few seconds the modal flips
   to "Connected"; its **Refresh** button (`stripe_status`) then
   reports `logged_in: true, running: true, connected: true`.
2. **Daemon start on login.** After login, confirm:
   - `process_service.list_processes("_stripe")` shows
     `stripe-listen` running.
   - Within ~3 s the stderr.log contains `whsec_…`.
   - `auth_service.get_api_key("stripe_webhook_secret")` returns the
     secret.
3. **Synthetic event.** Build a workflow with `StripeReceiveNode`
   (filter: `charge.*`) → console node, Start it, and send WS
   `{"type":"stripe_trigger","event":"charge.succeeded"}`. The console
   fires with `event_type="charge.succeeded"` and the CLI's `event_id`.
   A canvas Run of the trigger resolves the same way, with the same
   output fields.
4. **Filter rejection.** Set the filter to `payment_intent.created` and
   retrigger `charge.succeeded`: the node does not fire. Trigger
   `payment_intent.created` and it does. `livemode_filter=live` rejects
   CLI test events.
5. **Action node.** Configure `StripeActionNode` with
   `command="customers create --email rosy@sparrow.com"`. Run. Output
   contains `id: cus_…`, `email: rosy@sparrow.com`.
6. **AI tool surface.** From a chat agent, prompt
   "create a Stripe test customer with email rosy@sparrow.com"; the
   LLM calls the `stripe_action` tool.
7. **Signature failure.**
   `curl -X POST -H "Stripe-Signature: t=0,v1=garbage" http://localhost:${PYTHON_BACKEND_PORT}/webhook/stripe -d '{}'`
   returns 400 once the secret is captured (503 before that); no event
   dispatched.
8. **Logout.** Credentials Modal → Disconnect. Confirm
   `process_service.list_processes("_stripe")` is empty AND
   `~/.config/stripe/config.toml` no longer contains an `_api_key`
   line.
9. **Restart.** Restart OpenCompany. The status refresh does not start
   the daemon. The modal still shows Stripe as connected (the login
   marker survives a restart), so it offers no Login button, and it has
   no Connect button. The daemon starts again when the boot re-arm
   restores a deployed `stripeReceive` (its `prepare_deployment` hook), on
   a canvas Run of `stripeReceive` (its precondition), or on a
   `stripe_connect` WS message.

Unit tests live in [`server/tests/nodes/test_stripe_plugin.py`](../server/tests/nodes/test_stripe_plugin.py)
and [`server/tests/services/test_events.py`](../server/tests/services/test_events.py)
(count them with `pytest --collect-only -q`). Run via `pytest server/tests/services/test_events.py
server/tests/nodes/test_stripe_plugin.py -v`.

## Related Docs

- [Plugin System → Wave 12 framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — the framework Stripe is built on.
- [Plugin System → Self-contained plugin folders](./plugin_system.md#self-contained-plugin-folders) — Wave 11.H pattern Stripe also follows.
- [Node Creation Guide](./node_creation.md) — when to use which framework piece for a new plugin.
- [Event Waiter System](./event_waiter_system.md) — generic dispatch path that `WebhookSource.handle` calls into.
- [Status Broadcaster](./status_broadcaster.md) — `register_service_refresh` registry that backs `make_status_refresh`.
- [Credentials Encryption](./credentials_encryption.md) — how `stripe_webhook_secret` is stored.
