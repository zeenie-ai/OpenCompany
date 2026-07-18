# Cloudflare Service (`cf` CLI)

The `cloudflareAction` node wraps the **official Cloudflare CLI `cf`**
(npm package `cf`, a Technical Preview announced 2026-04-13 as "the
next version of Wrangler"). One self-contained plugin folder at
[`server/nodes/cloudflare/`](../server/nodes/cloudflare/), following
the CLI-managed-auth pattern (stripe -> vercel -> github lineage) with
two cf-specific variants documented below: **the CLI opens the browser
itself** (no URL proxying to the modal) and **a fixed-scope OAuth
grant** that makes the optional API token the only path to analytics.

| | |
|---|---|
| Node type | `cloudflareAction` (palette group `deployment`, dual-purpose AI tool `cloudflare`) |
| Operations | `whoami` / `zones_list` / `dns_records_list` / `dns_record_create` / `dns_record_delete` / `graphql_query` / `custom` |
| CLI pin | `cf@0.2.0` (`_NPM_SPEC` in `_install.py`), npm-installed into the shared `packages_dir()` tree, Node >= 22 |
| Auth | Dual-path: cf-owned OAuth login OR optional `cloudflare_api_token` field -> `CLOUDFLARE_API_TOKEN` env |
| Task queue | `TaskQueue.REST_API` |
| Output | `ui_hints = {"outputMode": "terminal"}`; `_shape` contract (parsed JSON -> `result`, text -> `stdout`, never both) + NDJSON recovery |
| Tests | [`server/tests/test_cloudflare_plugin.py`](../server/tests/test_cloudflare_plugin.py) (35 contract tests) |
| Paired skill | [`server/skills/cloudflare/cloudflare-skill/SKILL.md`](../server/skills/cloudflare/cloudflare-skill/SKILL.md) |

## Folder map

```
server/nodes/cloudflare/
├── __init__.py           # register_ws_handlers(WS_HANDLERS) + register_output_schema
├── cloudflare_action.py  # CloudflareActionNode + Params/Output + _run/_shape + _graphql_post
├── _credentials.py       # CloudflareCredential — resolve() returns the optional token row
├── _handlers.py          # cloudflare_login / cloudflare_logout / cloudflare_status
├── _install.py           # ensure_cf_cli() — pinned npm install, system cf NEVER consulted
├── _service.py           # cf_env(token) / login_env() / whoami_snapshot() / stored_token()
└── meta.json             # {"color": "#F38020"}
```

Icon: `lobehub:Cloudflare` via `visuals.json` (`cloudflareAction` entry
+ lowercase `cloudflare` alias for the tool name — no folder
`icon.svg`). Catalogue entry in `credential_providers.json` (vercel
dual-path shape: one optional field, never gating the Login button).

## Install — pinned, never the system PATH

`_install.py` uses vercel's npm **mechanism** (install into the shared
`packages_dir()` tree, `.bin` shim, `asyncio.to_thread`, double-checked
lock) but github's **resolution philosophy**: `shutil.which("cf")` is
never consulted. The preview CLI's command tree is schema-generated and
drifts between versions — between 0.0.5 and 0.2.0, `--ndjson`/`--fields`
were removed (JSON became the default output), `dns records create`
kept only the `--body` escape hatch, and the auth config moved from
`~/.cf/config.toml` to `auth.jsonc` under the config dir. The argv
builders are verified against the pinned version only. **Version-bump
recipe**: change `_NPM_SPEC`, re-run `cf <cmd> --help-full` for every
wrapped command, adjust argv builders + tests. (0.3.0 is already out.)

Auth state is user-level and shared with any system cf install — which
binary runs does not affect who is logged in.

## Auth — CLI-owned OAuth login (the cf variant)

`cf auth login` is a PKCE OAuth flow against
`dash.cloudflare.com/oauth2/*` with a loopback callback server on the
**fixed port `localhost:8877`**, and cf **opens the default browser
itself** (`start` / `open` / `xdg-open`). The handler therefore does
NOT parse or proxy the authorize URL to the frontend (no custom login
UI, no `verification_code`) — `cloudflare_login` returns
`{success, message}` and the modal badge flips when the background
completion broadcasts `credential.oauth.connected`.

Two hazards drove the handler's shape (both observed live):

1. **Fixed callback port** — two concurrent `cf auth login` processes
   collide on 8877; the second exits instantly with "Port already in
   use". A module-level **single-flight guard** (`_active_login` task +
   proc) makes repeat Login clicks return "already in progress".
2. **Windows shim orphaning** — the installed binary is an npm `.cmd`
   shim; killing it terminates only the cmd.exe wrapper and orphans the
   node child, which keeps holding 8877 and breaks every later login.
   The completion watcher **never kills the process** (test-locked) —
   cf enforces its own login timeout and exits by itself.

Flow: single-flight check -> `ensure_cf_cli()` (22s response budget,
`pending: true` past it) -> **whoami short-circuit** (live session ->
mark + broadcast immediately, no spawn) -> spawn
`cf auth login --force` (`--force` pushes past a stale/invalid stored
token; only reached when whoami says no session) with pipes drained for
the process lifetime -> background `_complete_login` waits (<= 600s) ->
gate on `whoami_snapshot()` -> marker row + broadcast.

**Success gate**: `cf auth whoami` prints JSON
`{authenticated, tokenValid, email, scopes[]}` and **exits 0 in BOTH
auth states** (verified) — exit codes are never consulted; the
`authenticated` field is the only signal. The whoami email feeds
`mark_logged_in("cloudflare", email=...)` (shared
`services/cli_agent/_cli_auth.py` module, same as claude/codex/github).

`login_env()` strips all six ambient credential vars
(`CLOUDFLARE_API_TOKEN`/`CF_API_TOKEN`, `CLOUDFLARE_API_KEY`/`CF_API_KEY`,
`CLOUDFLARE_EMAIL`/`CF_EMAIL`) so login/whoami/logout reflect the OAuth
session, never an env token; the stored modal token is never injected
there either (test-locked).

## The OAuth scope ceiling — why the API token exists

cf's PKCE client hard-codes **86 scopes with no `--scopes` flag**
(source-verified in the bundle). The only analytics scope is
`dns_analytics:read`. **No Web Analytics/RUM or zone-analytics OAuth
scope exists anywhere in Cloudflare's OAuth catalog** (wrangler's
documented `--scopes` list has none either), so `/accounts/{id}/rum/*`
and the analytics datasets return **403 under any OAuth login** — this
is structural, not a configuration error.

Cloudflare's sanctioned paths are an **API token** with the matching
permission groups (cf documents the env token as its first-priority
credential source: "1. `CLOUDFLARE_API_TOKEN` environment variable,
2. cf OAuth token") or the legacy **Global API Key** + account email
pair (full access — also covers endpoints the account-token
compatibility matrix excludes). Hence the dual-path fields:

- Optional canonical `apiKey` field in the credentials modal
  (`required: false` — Login gates on required fields only, the
  OAuthConnect invariant; stored under the provider id so the base
  `Credential.validate` scaffold needs no storage override). Accepts
  EITHER a scoped API token (`cfut_`/`cfat_`) OR a Global API Key
  (`cfk_`); the optional `cloudflare_email` companion field carries
  the account email Global keys authenticate with.
- Credential prefixes are Cloudflare's documented scannable formats
  (`cfk_` = Global API Key, `cfut_` = user token, `cfat_` = account
  token) — `_service.py` routes on them everywhere:
  - `cf_env(token, email)`: tokens ride `CLOUDFLARE_API_TOKEN`; a
    cfk_ key + email ride the documented `CLOUDFLARE_API_KEY` +
    `CLOUDFLARE_EMAIL` pair (ambient token vars dropped — cf ranks
    tokens above the key pair and would silently override).
  - `api_auth_headers(key, email)`: `Bearer` for tokens,
    `X-Auth-Email`/`X-Auth-Key` for Global keys (direct API calls:
    GraphQL, probes).
  - The Validate probe: cfk_ → `GET /user` with the X-Auth pair (or
    an "add the Account Email" guidance when the companion is
    missing); cfat_ → authenticated `GET /accounts` read (the
    `/user/tokens/verify` endpoint is user-token-only and falsely
    rejects account tokens); cfut_/legacy → the official
    `/user/tokens/verify` endpoint.
- `CloudflareCredential.resolve()` returns the credential rows so
  other surfaces can consume them.
- Token permissions for analytics: **Account > Account Analytics >
  Read** (covers RUM datasets + `rum/site_info` reads) plus
  **Zone > Analytics > Read** for zone-scoped queries. The Global API
  Key needs no permission setup (it has everything) but is
  Cloudflare-discouraged legacy — prefer scoped tokens when they
  suffice.

## `graphql_query` — the GraphQL Analytics API

The legacy Zone Analytics REST API was deprecated (2021-03-01 in the
current deprecations table) and the DNS analytics REST API is
**removed 2026-12-01**; the official replacement for both is the
GraphQL Analytics API at `api.cloudflare.com/client/v4/graphql`. That
endpoint is **outside the REST OpenAPI schema** cf (and the official
SDKs) are generated from — cf has no `graphql`/`api` passthrough — so
the node's `graphql_query` operation calls the documented endpoint
directly: one POST with `{query, variables}` + `Bearer` token
(`_graphql_post`, ~30 lines of repo-standard httpx; there is no more
official channel to rely on).

- Requires the API token OR the Global API Key + email (both
  documented GraphQL auth schemes); without either the op raises
  `NodeUserError` with the credential guidance. 401/403 map to the
  same fix message. Hard GraphQL errors (no `data`) surface as
  `NodeUserError`; partial data rides back in `result`.
- Key datasets: zone traffic `httpRequestsAdaptiveGroups` (under
  `viewer.zones`), Web Analytics/RUM `rumPageloadEventsAdaptiveGroups`
  / `rumPerformanceEventsAdaptiveGroups` /
  `rumWebVitalsEventsAdaptiveGroups` (under `viewer.accounts`), DNS
  `dnsAnalyticsAdaptive(Groups)` (both scopes).
- Limits: 300 queries per 5-minute window; one query spans <= 10 zones
  or 1 account; Adaptive Bit Rate sampling on wide ranges (multiply by
  `sampleInterval`).
- The skill carries Cloudflare's own migration-guide queries verbatim.

## Operations -> argv (verified against cf@0.2.0 `--help-full`)

| Operation | argv / call |
|---|---|
| `whoami` | `auth whoami` |
| `zones_list` | `zones list [--name <f>] [--account-id <id>]` |
| `dns_records_list` | `dns records list --zone <zone>` |
| `dns_record_create` | `dns records create --zone <zone> --body <json>` (`--body` is the schema-stable escape hatch; per-field flags churn) |
| `dns_record_delete` | `dns records delete <record_id> --zone <zone>` (positional id) |
| `graphql_query` | POST `client/v4/graphql` (not the CLI) |
| `custom` | `shlex.split(command)` — anything after `cf ` (incl. `agent-context <product>` and `schema <cmd>` for discovery) |

`zone` accepts a zone ID or domain name (global `-z/--zone`). cf 0.2.0
prints JSON to stdout by default (status noise on stderr) so parsed
results flow into `result`; `_parse_ndjson` in `_shape` recovers
multi-object stdout that defeats `run_cli_command`'s single
`json.loads`. No auth pre-flight (Stripe-strict): cf's own "Not logged
in" error surfaces via the `NodeUserError` wrap.

## What the OAuth login CAN do vs what needs a stored credential

| Surface | OAuth login | API token | Global API Key + email |
|---|---|---|---|
| zones / DNS records / accounts / registrar | yes | yes | yes |
| `cf dns analytics` (REST, dies 2026-12-01) | yes (`dns_analytics:read`) | yes | yes |
| `graphql_query` (zone traffic, RUM, DNS analytics) | **no — no scope exists** | yes (`Account Analytics: Read` / `Zone Analytics: Read`) | yes (full access) |
| `rum/site_info` config endpoints (no cf command yet; `custom`/httpRequest) | **no** | yes | yes |
| Endpoints the account-token compatibility matrix excludes | no | varies | yes |

## Invariants locked by `test_cloudflare_plugin.py`

Pinned `_NPM_SPEC` (never `@latest`); no `shutil.which("cf")`; per-op
argv shapes incl. `--body`/positional-delete; `login_env` strips all
six ambient vars; stored token injected on ops, never on login;
single-flight login; **no `.kill(`/`.terminate(` in `_complete_login`**;
no URL/`verification_code` in the login response; whoami-gated
completion via the shared `_cli_auth` marker + broadcasts; catalogue =
vercel dual-path shape (`apiKey` + `cloudflare_email`, both optional);
prefix-routed probe (cfk_ no-email guidance without a network call,
cfk_+email via X-Auth `GET /user`, cfat_ via `GET /accounts`,
cfut_/legacy via the verify endpoint); `cf_env` global-key pair
injection with ambient-token drop; `api_auth_headers` Bearer-vs-X-Auth
routing; GraphQL endpoint constant + official body shape + 403
credential guidance; folder assets (no `icon.svg`, `#F38020`, both
visuals entries, paired skill).
