# Discord Service

Bot messaging, REST actions, gateway message triggers and slash commands.
Plugin folder: [`server/nodes/discord/`](../server/nodes/discord/).

Reference plugins this one follows: [`nodes/whatsapp_business/`](../server/nodes/whatsapp_business/)
for the media-on-the-deploy-path question and the webhook shape, and
[`nodes/telegram/`](../server/nodes/telegram/) for the long-lived-connection
lifecycle.

## Nodes

| Node | Kind | Purpose |
|---|---|---|
| `discordSend` | ActionNode, `usable_as_tool` | Messages, embeds, one file attachment. Splits past 2000 characters. |
| `discordAction` | ActionNode, `usable_as_tool` | Guilds/channels/messages/reactions/threads, `download_attachments`, `interaction_respond`, `execute_webhook`, `custom`. |
| `discordReceive` | TriggerNode (event) | Fires on an inbound message. |
| `discordInteraction` | TriggerNode (event) | Fires on a slash command or component click. |

Palette group `discord`, colour `#5865F2`. Icon is a local `icon.svg` (the
brand mark); the credential tile resolves `discord.svg`.

**Why send is separate from action.** The single-`<service>Action` convention
belongs to the CLI wrappers (`githubAction`, `cloudflareAction`, …). Both
messaging plugins here — telegram and whatsapp_business — keep send separate,
because send is the high-frequency agent-facing operation and a twenty-operation
union makes a poor LLM tool schema.

**Why there is no media node.** `whatsappBusinessMedia` exists because Meta
requires upload-then-reference. Discord attaches files directly in the send
multipart, so only the download side needs an operation.

**Why interactions are a second trigger.** [`canary_registry`](../server/services/deployment/canary_registry.py)
maps one node type to exactly one CloudEvents type, because that string becomes
the Temporal `EventType` Search Attribute the listener is discovered by. One
node cannot subscribe to both. The payloads also share almost nothing, so
merging would mean a union output schema and weaker `{{trigger.field}}`
resolution. Same split, same reasons, as `whatsappBusinessReceive` vs
`whatsappBusinessStatus`.

## Multi-account

API keys are stored under a `{session_id}_{provider}` key, so the `session_id`
column is a free scoping axis:

```
account_id "default"      -> session_id "default"       (what the modal writes)
account_id "<app_id>"     -> session_id "discord:<app_id>"
```

[`_accounts.py`](../server/nodes/discord/_accounts.py) is the only file that
knows this mapping. `"default"` is the row the unmodified credentials modal
already writes, so a single-bot install needs no account concept and
multi-account is purely additive. Every node carries an `account_id` param
backed by the `discordAccounts` option loader; blank means default.

Enumeration needs `AuthService.list_key_scopes(provider)` — added for this
plugin, generic, index-backed.

**`credential_customer_id` is deliberately not used.** It is per-execution-context
tenancy, so two nodes in one workflow could never target two different bots. It
is the tenancy axis, not the account axis, and
[`test_identity_namespaces.py`](../server/tests/test_identity_namespaces.py)
exists to keep them apart.

## Security invariants

**`account_id` is stripped from model arguments.** Inbound Discord messages are
the realistic source of hostile tool arguments and `account_id` selects which
bot identity sends, so both nodes list it in `server_controlled_fields`. The
framework enforces that on every tool path: in process,
[`BaseNode.execute_as_tool`](../server/services/plugin/base.py) drops locked
fields from the model's arguments before its `{**node_params, **tool_args}`
merge; on Temporal, where the agent workflow has already merged the arguments,
[`NodeExecutor`](../server/services/node_executor.py) puts back the saved value
(or the default). `AccountScopedNode` in
[`_base.py`](../server/nodes/discord/_base.py) strips locked fields again as
defence in depth; it runs only in process, since the per-type activity calls
`execute` for an ActionNode. Tests drive both paths with a hostile `account_id`.

**The interaction token never leaves the server.** It is a 15-minute bearer
credential that can post as the app, and trigger output is persisted three ways,
broadcast twice, retained in the status cache and replayed into LLM context each
turn. `discordInteraction` emits an opaque `interaction_ref`;
`discordAction[interaction_respond]` trades it back through a process-local TTL
map. Refs do not survive a restart, which is not a gap — a workflow that
outlived a restart mid-interaction has already missed the window.

**`custom` route paths are validated in `_base.build_url`,** not at the call
site, so no future caller can skip it. Without the guard that operation would
send the bot token to any host a workflow named. Webhook URLs are host-whitelisted
by `assert_discord_host`, and neither a webhook URL nor an interaction URL is
ever echoed in an error — both embed credentials.

**Snowflakes are stringified** at the dispatch boundary. discord.py exposes them
as `int` while Discord's own JSON uses strings; past 2^53 a numeric comparison
collapses two distinct ids, and comparing an id is the most natural edge
condition a user writes (see commit `20d00a07`).

## Rate limiting

[`_ratelimit.py`](../server/nodes/discord/_ratelimit.py) is pure state, no IO.
Reactive rather than predictive: it reads 429s and holds what follows, but does
not track `X-RateLimit-Remaining` to sleep before hitting zero. Three scopes,
and conflating any two is a bug:

- **per account** — the 50 req/s ceiling is per bot token.
- **per account** — a global 429 pauses that token entirely.
- **per process** — the invalid-request ban is enforced by Cloudflare on the
  source IP, not the token. Three misconfigured bots on one host share one
  budget and blowing it takes out the healthy accounts too. This is the piece
  most likely to be "simplified" into the per-account limiter later. It must
  not be.

A 429 with a non-JSON body is the Cloudflare edge ban, not a Discord rate limit,
and is reported as such. Any wait over ~30s becomes a retryable `RuntimeError`
(never `NodeUserError`, which is non-retryable) so Temporal's backoff releases
the worker slot instead of pinning it.

## Gateway

`discord.Client` owns the socket. What it handles is exactly the part that
fails silently when hand-rolled: zlib framing, heartbeat/ACK with jitter,
RESUME versus IDENTIFY on the right close codes, `resume_gateway_url`, and
IDENTIFY concurrency. `Client.start()` is used, never `run()` — the latter
installs signal handlers and would fight uvicorn.

REST does **not** go through discord.py. The nodes must work with no gateway
connection at all, `discordAction` needs arbitrary routes the typed methods do
not expose, and the invalid-request guard has to be process-wide across
accounts, which a per-client limiter cannot express. Gateway and REST never
cross; calling `client.http` from node code would bypass the limiter that knows
the shared budget.

`BaseSupervisor.get_instance()` is a per-subclass singleton and cannot hold N
connections, so gateways live in a module-level registry keyed by account, each
registering its own label with `services._supervisor` so
`shutdown_all_supervisors()` still reaches them.

**Terminal failures are not retried.** `PrivilegedIntentsRequired` and
`LoginFailure` fail identically every attempt; backing off spends the
1000-IDENTIFY daily budget, which ends in a reset token and an email to the
app owner. Both are translated into actionable errors and the gateway refuses
further retries. Readiness races the connection task rather than awaiting
`wait_until_ready()` alone, because login failures raise inside `start()` —
awaiting readiness would sit out the full 60s timeout and then report "not
ready" for a bad credential.

## Attachments

`discordReceive` emits attachment **metadata only**;
`discordAction[operation="download_attachments"]` fetches them via
[`fetch_to_workspace`](../server/services/media/fetch.py) (SSRF guard, streaming,
25 MB cap, contained write, `FileRef` out).

The reason is the deploy path: the engine marks a deployed trigger
`_pre_executed` and passes `_trigger_output` through verbatim
(`ExecutionContext.create` in [`services/execution/models.py`](../server/services/execution/models.py)), so the node body
never runs and a download flag there would work on the canvas and silently do
nothing once deployed. `whatsappBusinessMedia` exists for the same reason and
documents it in its own module docstring.

`kind` is never `"audio"`: that asserts a real container probe, and a download
measures nothing. Discord CDN URLs are signed and expire, so they are resolved
immediately before fetching and never trusted from a stored output.

## Interactions endpoint

`POST /api/discord/interactions[/{account_id}]`, a plugin-owned router rather
than a `WebhookSource`, for three reasons the generic intake cannot express:

- Discord's endpoint-validation probe sends a deliberately invalid signature
  and requires **401**. `WebhookSource.handle` raises 400, and a 400 makes
  Discord refuse to save the endpoint URL at all.
- The initial response is due within **three seconds**, but `handle()` awaits
  shaping, `emit` (a Visibility query plus a Signal per consumer) and dispatch
  before the router writes a byte.
- PING must be answered with `{"type": 1}`, while the generic route returns a
  fixed envelope.

Flow: raw-body Ed25519 verify → 401 on failure → **503 fail-closed** when no
public key is stored → PING answers PONG → anything else defers (type 5, or
type 6 for a component so the existing message is edited) and fans out in a
background task whose reference is held, since the loop keeps only a weak one.

Per-account paths exist so the server never trial-verifies against every stored
public key, which would be both a timing oracle and an availability multiplier.

[`DiscordEd25519Verifier`](../server/nodes/discord/_verifier.py) is the first
asymmetric verifier in the tree — the four shipped ones are HMAC — and uses
`cryptography`, already a dependency. **Do not add PyNaCl.** It verifies over
the raw body, never re-serialised JSON: any difference in key order or
separators breaks authentic requests.

**Gateway and HTTP interaction delivery are mutually exclusive per application.**
Setting an Interactions Endpoint URL stops `INTERACTION_CREATE` arriving over
the socket.

## Router mounting

The interaction routes are added onto the router
`make_oauth_callback_router` builds, rather than into a fresh `APIRouter` with
the factory's router included into it. On an `APIRouter` — unlike on an app —
`include_router` records a pathless placeholder instead of flattening the
child's routes: the callback silently never mounts, and the pathless entry
reads as an ungated route to `TestPublicSurfaceInvariant`. That test is what
caught it.

## Credentials

One provider entry, `kind: "apiKey"`, fields:

| Key | Required | Purpose |
|---|---|---|
| `apiKey` | yes | Bot token. Stored under provider id `discord`. |
| `discord_application_id` | no | Labels the bot when several are connected. |
| `discord_public_key` | no | Ed25519 public key; slash commands only. |
| `discord_client_id` / `discord_client_secret` | no | OAuth2 user-context. |

`DiscordBotCredential` **overrides `inject()`**: the inherited `bearer` mode
hardcodes the literal `Bearer ` prefix and Discord bot auth is `Bot <token>`,
which otherwise fails as a silent 401.

The probe calls `/users/@me` then `/applications/@me` and reports whether the
**Message Content** privileged intent is enabled. Without it the gateway still
connects and delivers empty `content` with no error anywhere — which surfaces
as "the Discord node returns blank text".

`DiscordUserCredential` (`discord_oauth`) is the user-context identity, stored
under a separate id so connecting a user never overwrites the bot token. Scopes
are `identify` and `guilds` only.

## Registration surfaces

Everything self-registers from [`__init__.py`](../server/nodes/discord/__init__.py):
WS handlers, router, OAuth callback path, three option loaders, four output
schemas, two filter builders, a trigger precheck, service refresh, two canary
trigger types, the social send adapter, and a shutdown hook.

Outside the folder: `nodes/groups.py`, `config/credential_providers.json`,
`config/node_allowlist.json`, `constants.py` (both trigger frozensets), the
three lists in `tests/test_plugin_self_containment.py`, and
`SOCIAL_PLATFORM_PLUGINS` in `tests/test_social_provider_registry.py`, which
is what asserts the adapter is actually registered rather than just imported.

**`import discord` never appears at module scope** in any file the plugin
walker touches. [`nodes/__init__.py`](../server/nodes/__init__.py) swallows
import errors during discovery, so a failing library import would make the
whole plugin silently disappear rather than report anything.

**Both trigger frozensets are asserted.** Omitting a trigger from
`WORKFLOW_TRIGGER_TYPES` is a silent failure: `TriggerManager.find_trigger_nodes`
filters on it, so deploy ignores the node with no listener and no warning (see
the comment inside `WORKFLOW_TRIGGER_TYPES` in [`constants.py`](../server/constants.py);
deploy once skipped `stripeReceive` exactly this way). `EVENT_TRIGGER_TYPES` has no
production reader, but the tests assert membership in both.

## Testing

[`server/tests/nodes/test_discord.py`](../server/tests/nodes/test_discord.py) —
121 collected cases (80 test functions, several parametrized; `pytest --collect-only -q`
is the counting method), no live bot required. Covers account scoping, the path guard, error
classification, rate-limit parsing, content splitting, the tool-path account
lock, send/action shapes, event shaping, filters, gateway lifecycle, the
Ed25519 verifier, the endpoint's 401/503/PONG/defer behaviour, and route
mounting.

**Not testable without a live bot:** the gateway handshake, IDENTIFY limits,
session resume, sharding, and Discord's own intent gating. Manual smoke
checklist:

1. Add a bot token in Credentials; confirm the probe reports the Message
   Content intent state.
2. Connect from the modal; confirm the status shows the bot username and guild
   count.
3. Send to a channel, with and without an attachment.
4. Post a message in that channel and confirm `discordReceive` fires on Run.
5. Deploy the workflow and confirm it still fires — this is the path the
   frozensets gate.
6. For slash commands, set the Interactions Endpoint URL and confirm Discord
   accepts it (that is the 401 path being exercised).
