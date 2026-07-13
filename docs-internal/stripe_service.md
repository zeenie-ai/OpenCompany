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
   │      → WorkflowEvent(id=evt_…, type="stripe.charge.succeeded",
   │                      source="stripe://acct_…", data=payload)
   │
   └── event_waiter.dispatch(source.type, event)
          ▼
   StripeReceiveNode waiters resolved
   (WebhookTriggerNode.execute returns the shaped event)
```

### Request flow — outgoing CLI action

```
StripeActionNode.run(params)
   │ params.command = "customers create --email a@b.com"
   ▼
shlex.split(command)
   │ → ["customers", "create", "--email", "a@b.com"]
   ▼
run_cli_command(binary="stripe", argv=…)        # NO credential= injection
   │
   ├── shutil.which("stripe") → resolves binary on PATH
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
   │     (clears ~/.config/stripe/config.toml)
   ├── _mark_logged_out()                      ← marker-token clear
   │     auth_service.remove_oauth_tokens("stripe")
   └── _broadcast_catalogue_updated()
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
   │       workflow_id="_stripe_global",
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
   └── ProcessService.stop("stripe-listen", "_stripe_global")
```

## Key Files

| File | Description |
|---|---|
| `server/nodes/stripe/__init__.py` | Wiring: 5 `register_*` calls + `make_status_refresh`. |
| `server/nodes/stripe/_credentials.py` | `StripeCredential(Credential)` — thin marker class. The CLI manages auth at `~/.config/stripe/config.toml`; this class only exposes the captured `stripe_webhook_secret` for the framework's signature-verifier path. |
| `server/nodes/stripe/_install.py` | `ensure_stripe_cli()` — async, idempotent, lock-guarded. Resolves the binary path: in-process cache → system PATH → previously-downloaded copy at `<DATA_DIR>/packages/stripe/bin/stripe[.exe]` (`core.paths.package_dir("stripe") / "bin"`) → fresh download from GitHub releases (pinned `_VERSION = "1.40.9"`) into that same dir. Asset-name map covers Windows AMD64, Linux x86_64/arm64, macOS x86_64/arm64. Subsequent calls hit the cache instantly. |
| `server/skills/payments_agent/stripe-skill/SKILL.md` | LLM teaching markdown for the `stripe_action` tool. ~10K chars covering customers, charges, payment_intents, refunds, invoices, products/prices, subscriptions, the `trigger` command, common workflows, quoting/escaping, idempotency, test vs live mode, error patterns, and webhook delivery. |
| `server/config/credential_providers.json` | JSON-driven Credentials Modal catalogue. The `payments` category + `stripe` provider entry tell the frontend modal to render a **Login with Stripe** button (no API-key field) wired to the `stripe_login` / `stripe_logout` / `stripe_status` WebSocket handlers. No React file edits required. |
| `server/services/ai.py` | `DEFAULT_TOOL_NAMES['stripeAction'] = 'stripe_action'` and the matching tool-description entry — what the LLM sees when the action node is wired to an agent's `input-tools` handle. |
| `server/nodes/stripe/_source.py` | `StripeListenSource(DaemonEventSource)` and `StripeWebhookSource(WebhookSource)` plus their singletons. |
| `server/nodes/stripe/_handlers.py` | WS handlers via `make_lifecycle_handlers`; the only plugin-specific handler is `stripe_trigger` (synthetic test events). |
| `server/nodes/stripe/stripe_action.py` | `StripeActionNode` — pass-through over the CLI via `run_cli_command`. |
| `server/nodes/stripe/stripe_receive.py` | `StripeReceiveNode(WebhookTriggerNode)` — filter overrides + output reshape. |
| `server/services/events/__init__.py` | Public framework surface — exports every base class + helper. |
| `server/services/events/daemon.py` | `DaemonEventSource` — supervises subprocess via `ProcessService`. Subscribes to ProcessService's per-line callback (`line_handler`) instead of re-tailing the on-disk log files. Credential gate is `await self.has_credential()` so non-api-key auth (Stripe → `is_logged_in()`) plugs in via subclass override. |
| `server/services/process_service.py` | Spawns + supervises long-lived subprocesses. Loops `stream.readline()` per stdout/stderr, writes to `.log`, broadcasts to Terminal, and forwards each decoded line to the optional `line_handler` async callback (Wave 12.B addition for typed event-source subscribers — see `DaemonEventSource._on_line`). |
| `server/services/events/webhook.py` | `WebhookSource` + `WEBHOOK_SOURCES` registry + `register_webhook_source`. |
| `server/services/events/triggers.py` | `WebhookTriggerNode` + `BaseTriggerParams`. |
| `server/services/events/cli.py` | `run_cli_command` helper. |
| `server/services/events/lifecycle.py` | `make_lifecycle_handlers` + `make_status_refresh`. |
| `server/services/events/verifiers/stripe.py` | `StripeVerifier` (`t=…,v1=…` HMAC-SHA256). |
| `server/routers/webhook.py` | Path-handler arm: consults `WEBHOOK_SOURCES` before falling through to legacy generic dispatch. |
| `server/nodes/visuals.json` | `stripeAction` / `stripeReceive` icon + color (`asset:stripe`, `#635BFF`). |
| `server/nodes/groups.py` | `payments` palette group. |
| `client/src/assets/icons/stripe.svg` | Stripe icon. |
| `server/tests/services/test_events.py` | 18 framework tests (envelope, verifiers, polling/daemon lifecycle, WebhookSource). |
| `server/tests/nodes/test_stripe_plugin.py` | 21 Stripe-specific tests (shape, filter, action passthrough, registrations). |

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
        secret = await container.auth_service().get_api_key("stripe_webhook_secret")
        return {"stripe_webhook_secret": secret} if secret else {}
```

Storage:

| Key | Type | Origin | Purpose |
|---|---|---|---|
| (no `stripe_api_key`) | — | Stripe CLI's `~/.config/stripe/config.toml` (populated by `stripe login`) | Authenticates every CLI invocation transparently. The CLI generates restricted keys with CLI-appropriate scopes — one for live mode, one for sandbox — valid 90 days. |
| `stripe_webhook_secret` | API key (extra field) | Auto-captured by `StripeListenSource.parse_line` from the daemon's stderr banner | Verifies forwarded webhook signatures via `StripeVerifier`. Stable across daemon restarts; the CLI re-uses the same secret for the same OpenCompany install. |
| OAuth marker token | OAuth token (`auth_service.store_oauth_tokens`) | Written by `_mark_logged_in()` after `stripe login --complete` exits 0; cleared by `_mark_logged_out()` on disconnect. **Strings are dummies (`"cli-managed"`)** — the real OAuth lives in the CLI's config file. | Lights up the catalogue's `provider.stored = true` flag via the existing `auth_service.get_oauth_tokens(status_hook)` check. Same path Google's OAuth callback uses; no new abstraction. |

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
| `has_credential()` (override) | Returns `is_logged_in()` — a filesystem check on `~/.config/stripe/config.toml`. Consulted by `DaemonEventSource.start` (the credential gate) and by `make_status_refresh` on every WS-client connect (auto-reconnect). |
| `parse_line(stream, line)` | Invoked once per decoded stdout/stderr line via `ProcessService`'s `line_handler` callback. On `whsec_…` match, persists the secret via `auth_service.store_api_key("stripe_webhook_secret", …)`. The Stripe daemon doesn't emit workflow events itself — they arrive via the webhook receiver. |

### `StripeWebhookSource(WebhookSource)`

Receives forwarded events at `/webhook/stripe`. The framework owns
signature verification, JSON parsing, and `event_waiter.dispatch`;
this class declares only the path, the verifier, the secret-field
name, and the payload-to-`WorkflowEvent` shaping:

```python
class StripeWebhookSource(WebhookSource):
    type = "stripe.webhook"
    path = "stripe"
    verifier = StripeVerifier
    secret_field = "stripe_webhook_secret"
    credential = StripeCredential

    async def shape(self, request, body, payload) -> WorkflowEvent:
        created = payload.get("created")
        time = (
            datetime.fromtimestamp(int(created), tz=timezone.utc)
            if created else datetime.now(timezone.utc)
        )
        account = payload.get("account") or "default"
        return WorkflowEvent(
            id=payload.get("id") or "",          # provider event id (replay safety)
            type=f"stripe.{payload.get('type', 'unknown')}",
            source=f"stripe://{account}",
            time=time,
            data=payload,
            subject=payload.get("type"),
        )
```

The `id` mirrors Stripe's `evt_…` so duplicate deliveries (Stripe
retries on 5xx) are idempotent at the WorkflowEvent level.

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
    event_type_prefix = "stripe."                     # users write "charge.*" not "stripe.charge.*"
    Params = StripeReceiveParams
    Output = StripeReceiveOutput

    async def _check_precondition(self) -> Optional[str]:
        # Refuse to register a waiter if the daemon isn't running.
        ...

    def _extra_filter(self, params):                 # livemode filter on top of event-type
        ...

    def shape_output(self, event: WorkflowEvent) -> Dict:
        # Extract Stripe-shaped fields from the WorkflowEvent's CloudEvents data.
        ...
```

The framework's `WebhookTriggerNode` handles event-type glob matching
(`charge.*`, `payment_intent.*`, `all`), the
`event_type_prefix` auto-prepend, the `_check_precondition`
short-circuit, and the `Operation("wait")` stub. This class only
contributes the livemode filter and the output reshape.

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
        result = await run_cli_command(binary="stripe", argv=shlex.split(cmd))
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
from the source's `start/stop/restart` methods (used internally by
the auto-reconnect path). The plugin-specific handlers wired into
the modal's Connect button are `stripe_login` and `stripe_logout`:

| Type | Handler | Purpose |
|---|---|---|
| `stripe_login` | `ensure_stripe_cli()` → `stripe login --non-interactive` (sync) → returns `{url, verification_code}` to the frontend; spawns background `_complete_login` task | **Modal "Login with Stripe" button** |
| `stripe_logout` | stops daemon → `stripe logout --all` → `_mark_logged_out()` → `_broadcast_catalogue_updated()` | **Modal Disconnect button** |
| `stripe_status` | returns `{logged_in, running, pid, webhook_secret_captured, connected = running ∧ logged_in}` | Optional read-only poll for diagnostics; the modal flips reactively from the catalogue refetch, not from this handler |
| `stripe_trigger` | passes `["trigger", event]` to `run_cli_command` (after `ensure_stripe_cli`) | Synthetic test event |
| `stripe_connect/disconnect/reconnect` | `source.start/stop/restart()` from the lifecycle factory | Daemon-only lifecycle; used by the auto-reconnect path on WS-client connect |

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
| LLM tool name (snake_case of node type) | `stripe_action` | [`server/services/ai.py`](../server/services/ai.py) — `DEFAULT_TOOL_NAMES['stripeAction'] = 'stripe_action'` |
| Skill `allowed-tools` (matches LLM tool name) | `stripe_action` | [`server/skills/payments_agent/stripe-skill/SKILL.md`](../server/skills/payments_agent/stripe-skill/SKILL.md) |
| `visuals.json` key (= node type) | `stripeAction` with `"skill": "stripe-skill"` | [`server/nodes/visuals.json`](../server/nodes/visuals.json) |

The skill resolver in `SkillLoader._parse_skill_metadata` runs each
`allowed-tools` token through snake → camel (e.g. `stripe_action` →
`stripeAction`) and looks the result up in `visuals.json` to source
the skill's icon and color. The skill renders without an icon if
those don't agree — see the **Common pitfall** callout in
[`server/skills/GUIDE.md`](../server/skills/GUIDE.md#tool-naming--snake_case--camelcase-contract).

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

The `payments_agent/` folder is a new skill bucket (the 12th,
alongside `assistant`, `android_agent`, `autonomous`, `coding_agent`,
`productivity_agent`, `rlm_agent`, `social_agent`, `task_agent`,
`terminal`, `travel_agent`, `web_agent`). It opens up future
payments integrations (PayPal CLI, Square, etc.) under the same
agent type.

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
  "icon_ref": "asset:stripe",
  "status_hook": "stripe",
  "ws": {
    "login":  "stripe_login",
    "logout": "stripe_logout",
    "status": "stripe_status"
  },
  "instructions": "Click 'Login with Stripe' to open the Stripe Dashboard. After you authorise, the CLI stores credentials at ~/.config/stripe/config.toml and the listen daemon starts automatically. The Stripe CLI must be installed on PATH (https://stripe.com/docs/stripe-cli#install)."
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
and listens for the `<status_hook>_status` push broadcast to flip
the connection indicator. Stripe rides on this exact pattern with
zero new frontend code; the difference is that Stripe's `ws.login`
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
the framework logs a warning and accepts the event without
verification — this only happens during the first ~5 seconds of
daemon startup.

## Status broadcasting — marker token + generic catalogue invalidation

There is **no `stripe_status` broadcast type, no Zustand entry, no
hardcoded case in `WebSocketContext.tsx`**. Stripe rides on two
existing generic mechanisms:

### 1. The catalogue's authoritative `stored` field

[`server/routers/websocket.py:handle_get_credential_catalogue`](../server/routers/websocket.py)
enriches every provider with `stored: bool`. For providers that
declare `status_hook` (Twitter, Google, Telegram, Stripe), the check is:

```python
tokens = await auth_service.get_oauth_tokens(status_hook)
provider["stored"] = tokens is not None
```

Google's OAuth callback writes real tokens via
`store_oauth_tokens("google", access_token, refresh_token, ...)`.
Stripe writes **synthetic marker strings** (`"cli-managed"`) the
same way — the catalogue's existing logic flips `stored: true`
without any provider-specific code in the catalogue handler. The
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

`WebSocketContext.tsx` already has a generic case for this event
(line 671 — predates Stripe). Its handler calls `invalidateCatalogue`
on the TanStack Query client; the catalogue refetches; the modal
sees the new `provider.stored` value and re-renders. **No
stripe-specific code anywhere on the frontend.**

### 3. `make_status_refresh` (auto-reconnect on WS-client connect)

The plugin still registers `make_status_refresh` so that on every
WebSocket-client connect:

1. `has_credential()` (= `is_logged_in()`) is consulted. If true and
   the daemon isn't running, `start()` is called — covers the
   "OpenCompany restarted while user was logged in" path.
2. `source.status()` is mirrored into `broadcaster._status["stripe"]`
   for any consumers that read it directly. (The modal does not.)

### Frontend `connected` derivation (single generic line)

[`client/src/components/credentials/panels/OAuthPanel.tsx`](../client/src/components/credentials/panels/OAuthPanel.tsx):

```tsx
const connected = status ? !!status.connected : !!config.stored;
```

Providers with a `statusHook` registered in `useProviderStatus` (the
five legacy providers) keep their hook-driven semantics. Providers
without one (Stripe today, future CLI-managed-OAuth integrations
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

## Installation

The Stripe CLI must be installed and on `PATH`:

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

`StripeListenSource.start()` resolves the binary via
`shutil.which("stripe")`. If missing, the WS `stripe_connect`
response is:

```json
{
  "success": false,
  "error": "'stripe' not on PATH. Install: https://stripe.com/docs/stripe-cli#install"
}
```

## Configuration

No JSON config file. Everything plugin-configurable lives on the
class attributes:

| Knob | Where | Default |
|---|---|---|
| Daemon process name | `StripeListenSource.process_name` | `"stripe-listen"` |
| Binary name | `StripeListenSource.binary_name` | `"stripe"` |
| Daemon process key | `StripeListenSource.workflow_namespace` | `"_stripe"` (logical `ProcessService` key; cwd is the shared `daemons_dir()` = `<DATA_DIR>/daemons/`, not a per-namespace subdir) |
| Webhook path | `StripeWebhookSource.path` | `"stripe"` (i.e. `/webhook/stripe`) |
| Forward-to port | derived from `Settings().port` | typically `3010` |
| Verifier | `StripeWebhookSource.verifier` | `StripeVerifier` |
| Action operation cost | `@Operation("run", cost=…)` | `{service: "stripe", action: "run", count: 1}` |

The CLI's webhook secret (`whsec_…`) is captured at runtime and
persisted automatically — no manual config step.

## Credentials Modal UI

The Stripe panel lives in the Payments category (introduced
specifically for this plugin in the `payments` palette group). It
provides:

- **Login with Stripe** button → fires `stripe_login`. The handler
  returns a `{url, verification_code}` pair; the modal opens the URL
  in a new tab and shows the verification code so the user can
  confirm the pairing on the Stripe Dashboard.
- **Disconnect** button → fires `stripe_logout`, which stops the
  daemon and runs `stripe logout --all` to clear
  `~/.config/stripe/config.toml`.
- **Status indicator** — driven by the `stripe_status` broadcast
  (`connected`, `webhook_secret_captured`).
- **Reconnect** button — issues `stripe_reconnect` for stuck states.

No webhook-secret input is needed — the daemon captures it
automatically. The UI surfaces "secret captured ✓" once the value is
persisted.

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
during that window, the framework logs a warning and accepts the
event without verification. In practice this is benign because Stripe
won't deliver real events until the daemon is fully ready, but
synthetic events triggered via `stripe_trigger` immediately after
connect can hit this path.

### Single global daemon

One Stripe account per OpenCompany install. The daemon is
singleton-global (`workflow_id="_stripe_global"`). Multi-account
support is deferred to a future revision; the design holds — give
`StripeListenSource` a `__init__(account_id)` and key the singleton
by id.

### No auto-restart on crash

If `stripe listen` exits unexpectedly, the framework surfaces the
disconnected status and waits for the user to reconnect via the
Credentials Modal. The `_capture_secret` task hits EOF on the log
file and exits cleanly. There's no exponential-backoff respawn loop
— that's deliberate to keep failing daemons visible rather than
hidden behind silent retries.

## Verification

End-to-end smoke (requires Stripe CLI installed and a Stripe account):

1. **Login.** Credentials Modal → Stripe → "Login with Stripe". WS
   sends `{"type":"stripe_login"}` → reply contains `{url,
   verification_code}`. Open the URL, confirm the code on Stripe
   Dashboard, click Authorise. Within a few seconds the modal flips
   to "Connected" and `stripe_status` broadcasts
   `{logged_in: true, running: true, webhook_secret_captured: true}`.
2. **Daemon auto-start.** After login, confirm:
   - `process_service.list_processes("_stripe_global")` shows
     `stripe-listen` running.
   - Within ~3 s the stderr.log contains `whsec_…`.
   - `auth_service.get_api_key("stripe_webhook_secret")` returns the
     secret.
3. **Synthetic event.** Build a workflow with `StripeReceiveNode`
   (filter: `charge.*`) → console node. Deploy. WS
   `{"type":"stripe_trigger","event":"charge.succeeded"}`. Console
   fires with `event_type="charge.succeeded"`, `event_id` matches the
   CLI's emitted event.
4. **Filter rejection.** Set filter to `payment_intent.created`,
   retrigger `charge.succeeded` — node does NOT fire. Trigger
   `payment_intent.created` — it does.
5. **Action node.** Configure `StripeActionNode` with
   `command="customers create --email rosy@sparrow.com"`. Run. Output
   contains `id: cus_…`, `email: rosy@sparrow.com`.
6. **AI tool surface.** From a chat agent, prompt
   "create a Stripe test customer with email rosy@sparrow.com"; the
   LLM emits a `stripeAction.run` tool call.
7. **Signature failure.**
   `curl -X POST -H "Stripe-Signature: t=0,v1=garbage" http://localhost:3010/webhook/stripe -d '{}'`
   returns 400; no event dispatched.
8. **Logout.** Credentials Modal → Disconnect. Confirm
   `process_service.list_processes("_stripe_global")` is empty AND
   `~/.config/stripe/config.toml` no longer contains an `_api_key`
   line.
9. **Auto-reconnect.** Restart OpenCompany. If still logged in
   (`is_logged_in()` returns true), the first WS-client connect
   triggers `make_status_refresh` which auto-spawns the daemon.

Unit tests live in [`server/tests/nodes/test_stripe_plugin.py`](../server/tests/nodes/test_stripe_plugin.py)
(21 tests) and [`server/tests/services/test_events.py`](../server/tests/services/test_events.py)
(18 framework tests). Run via `pytest server/tests/services/test_events.py
server/tests/nodes/test_stripe_plugin.py -v`.

## Related Docs

- [Plugin System → Wave 12 framework](./plugin_system.md#wave-12--generalized-event-framework-servicesevents) — the framework Stripe is built on.
- [Plugin System → Self-contained plugin folders](./plugin_system.md#self-contained-plugin-folders) — Wave 11.H pattern Stripe also follows.
- [Node Creation Guide](./node_creation.md) — when to use which framework piece for a new plugin.
- [Event Waiter System](./event_waiter_system.md) — generic dispatch path that `WebhookSource.handle` calls into.
- [Status Broadcaster](./status_broadcaster.md) — `register_service_refresh` registry that backs `make_status_refresh`.
- [Credentials Encryption](./credentials_encryption.md) — how `stripe_api_key` and `stripe_webhook_secret` are stored.
