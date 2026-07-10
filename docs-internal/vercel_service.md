# Vercel Service

Self-contained plugin at [`server/nodes/vercel/`](../server/nodes/vercel/)
wrapping the official **Vercel CLI** (npm package `vercel`, pinned in
`_install.py`). One dual-purpose node — `vercelAction` (workflow node +
AI tool `vercel`) — with four operations: `deploy`, `inspect`, `list`,
and a `custom` raw-command passthrough (the Stripe passthrough idiom, so
env vars / logs / rollback / promote / domains / aliases / projects all
work without additional code). No trigger node in v1: the Vercel CLI
has no `listen`-style daemon; a `vercelReceive` (account webhooks or
`vercel ls` polling) is a documented follow-up.

It is the second in-repo reference for the **CLI-managed-auth
pattern** (see [node_creation.md → CLI-managed auth](./node_creation.md)
and [stripe_service.md](./stripe_service.md)) — specifically the
**device-flow variant**, where the CLI's login is a single blocking
process rather than Stripe's two-step `--non-interactive` /
`--complete <url>` pair.

## File map

| File | Role |
|---|---|
| `__init__.py` | Wiring only: `register_ws_handlers(WS_HANDLERS)` + `register_output_schema("vercelAction", VercelActionOutput)`; node class auto-registers on import |
| `vercel_action.py` | `VercelActionNode(ActionNode)`, `usable_as_tool=True`, `tool_name="vercel"`, gmail-style multi-`@Operation` dispatch on `operation: Literal["deploy","inspect","list","custom"]` |
| `_handlers.py` | `vercel_login` / `vercel_logout` / `vercel_status` WS handlers; device-flow driver; marker-token write + `broadcast_credential_event` |
| `_service.py` | Config-dir pinning, `is_logged_in()` sniff, `global_argv()` / `vercel_env()` builders, ANSI-strip + login-URL/code parsers |
| `_install.py` | `ensure_vercel_cli()` — pinned `npm install vercel@<X>` into the shared npm tree (`<DATA_DIR>/packages/`); system PATH preferred; one-shot `telemetry disable` post-install |
| `_credentials.py` | `VercelCredential(Credential)` thin marker (`auth="custom"`); `resolve()` returns the optional `vercel_token` api-key row |
| `meta.json` | Node color. **No co-located `icon.svg`** — the icon is the official brand glyph via `visuals.json: {"vercelAction": {"icon": "lobehub:Vercel", "skill": "vercel-skill"}}`; a folder SVG would silently override it (first hit in `get_plugin_icon_path`), and its absence is locked by `test_vercel_plugin.py::test_plugin_folder_assets` |

Paired skill: [`server/skills/vercel/vercel-skill/SKILL.md`](../server/skills/vercel/vercel-skill/SKILL.md)
(auto-attached when the tool connects to an agent, via the
`visuals.json` skill map).

## Auth — two independent paths

Either path is sufficient; the token wins when both exist.

1. **Access token** (headless-safe): the catalogue entry declares one
   `fields` row, `vercel_token` with `"required": false`. Stored as a
   plain api-key row; injected as the **`VERCEL_TOKEN` env var** on
   every invocation — never argv, so it stays out of process lists.
2. **CLI login** (browser device flow): `vercel_login` spawns
   `vercel login` directly with `asyncio.create_subprocess_exec`
   (`stdin=PIPE` left un-written — the claude-login EOF guard).
   `run_cli_command` is unusable here: it buffers via `communicate()`
   until exit, and the device flow blocks for up to 10 minutes while
   the URL is needed immediately. The banner reader is **chunk-based**
   (`stream.read(4096)`, not `readline()` — spinner `\r` repaints
   overrun the StreamReader line limit) and its pumps deliberately
   outlive the handler, draining both pipes for the process lifetime
   so the CLI never blocks on a full pipe buffer. The first
   code-embedding URL is returned as `{success, url,
   verification_code}` — the frontend opens `url` AND renders
   `verification_code` prominently in the modal's info box
   (`useCredentialPanel.oauthLogin` → `OAuthConnect`, the generic
   device-flow display shipped with the GitHub plugin; prefer a
   code-embedding URL anyway so the user rarely needs to type it).
   A background task (strong-ref'd — asyncio holds only weak refs)
   awaits process exit; the success gate is **`auth.json` mtime
   advanced past the pre-login snapshot AND `is_logged_in()`** (exit
   codes are not trusted; Stripe precedent). On success the handler
   writes the `cli-managed` marker OAuth tokens and broadcasts
   `credential.oauth.connected`.

### Config-dir pinning

Every invocation appends `--global-config <DATA_DIR>/vercel/` (plus
`--no-color`; env gets `NO_COLOR=1`). The CLI's default
`com.vercel.cli` location varies wildly by OS — pinning makes the
`auth.json` sniff deterministic and isolates MachinaOs-managed auth
from the user's own system `vercel login` (the
`CLAUDE_CONFIG_DIR = data_path("claude")` idiom). The path is composed
inline in `_service.vercel_config_dir()` per the `core.paths` rule.

### Credentials modal

Catalogue entry (`server/config/credential_providers.json`):
`kind:"oauth"`, category `deployment` (new, order 10), **no
`status_hook`** — the marker tokens satisfy the plain `kind=="oauth"`
stored-derivation in `handle_get_credential_catalogue`, and the
`connected` badge falls back to `config.stored` in `OAuthPanel`.
`icon_ref: "lobehub:Vercel"` (official brand icon; registered in
`client/src/assets/icons/index.ts` `LOBEHUB_BRANDS`).

**Frontend gating change that shipped with this plugin**:
`OAuthConnect.tsx` now gates the Login button on **required** fields
only (`config.fields?.some(f => f.required)`), so Vercel's optional
token field renders without blocking Login. Existing providers are
unchanged (Google/Twitter/Telegram all declare `required: true` on
their gating fields).

## Operations

| Op | argv shape | Notes |
|---|---|---|
| `deploy` | `deploy --yes [--prod] [--prebuilt] [--archive=tgz] [--project X] [--scope X] <extra_args>` | cwd = `path` param (relative → resolved against `ctx.workspace_dir`) or the workspace itself; **stdout carries only the deployment URL** (progress → stderr), returned as `url`; 600 s timeout |
| `inspect` | `inspect <deployment> [--logs] [--wait [--timeout T]]` | 300 s subprocess timeout when `--wait`, else 60 s |
| `list` | `list [project] [--prod] [--status S] --yes` | Human table in `stdout` (no `--json` upstream) |
| `custom` | `<command verbatim, shlex-split>` | cwd = workspace when present; JSON-capable commands (`logs --json`, `project ls --format=json`) come back parsed in `result` |

Shared per-op plumbing: `_preflight()` (no token AND not logged in →
`PermissionError` annotated `provider="vercel" / reason="missing" /
auth="oauth2"`, which produces the credential envelope + the
`credential.oauth.runtime_failed` broadcast) → `ensure_vercel_cli()` →
`run_cli_command(..., env=vercel_env(token), cwd=...)` → non-zero exit
→ `NodeUserError` carrying the stderr tail (user/LLM-correctable, one
WARN line, no traceback).

Output shaping: `_shape` never ships raw stdout alongside a
server-side-parsed JSON `result` (pre-stringified duplication violates
the output contract) and omits empty keys entirely (`exclude_unset`
preserves the producer's key set). The node declares
`ui_hints = {"outputMode": "terminal"}` so the Output panel renders
its textual output preformatted instead of through ReactMarkdown.

**First-deploy project guard**: an unlinked deploy (no `project`
param, no `.vercel/project.json` in the cwd, no `VERCEL_PROJECT_ID`,
no `--project` in `extra_args`) raises a `NodeUserError` up front with
the remediation, instead of letting Vercel derive a project name from
the cwd — workflow workspace dirs (`AI_Assistant_1`) violate Vercel's
lowercase naming rules and would 400 only **after** the upload. No
name is invented and no naming rules are re-implemented — Vercel stays
the authority; the LLM/user supplies a valid name and retries.

## Framework touch (one)

`run_cli_command` (`server/services/events/cli.py`) gained a
`cwd: Optional[str] = None` kwarg (keyword-only, backward compatible)
— deploys are directory-scoped.

## Tests

[`server/tests/test_vercel_plugin.py`](../server/tests/test_vercel_plugin.py):
argv builders per operation (`--global-config` + `--yes` presence,
token → env not argv), device-flow banner parsing against recorded
fixtures (ANSI included), marker-token flip + catalogue broadcast,
install-path resolution, `PermissionError` annotation attrs, the
first-deploy project guard, catalogue-entry shape (`required: false`
on the token field), and the no-folder-icon contract. The generic
`test_plugin_contract.py` / `test_plugin_self_containment.py`
invariants pick the plugin up automatically.

## Runtime caveat

The device-flow banner format is parsed from the pinned CLI version's
output; Vercel changed login flows in early 2026 (email / `--github` /
`--oob` removed). If the URL regex ever misses, `vercel_login` returns
the raw CLI output lines in `error` so the failure is diagnosable from
the modal. Re-verify against `vercel login --help` when bumping the
pinned version.
