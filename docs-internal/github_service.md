# GitHub Service

Self-contained plugin at [`server/nodes/github/`](../server/nodes/github/)
wrapping the official **GitHub CLI** (`gh`, pinned release in
`_install.py`). One dual-purpose node — `githubAction` (workflow node +
AI tool `github`) — with typed core operations (`repo_clone`,
`pr_create`, `pr_list`, `pr_merge`, `issue_create`, `issue_list`) plus
a `custom` passthrough covering the entire gh surface (`gh api`, runs,
releases, gists, repo administration).

**Auth model: the gh CLI owns its own auth** — the Stripe CLI pattern,
strictly. MachinaOs never stores, reads, or injects a token:

- `gh auth login` puts the token in the **system credential store**;
  ops read it from there (or from an ambient `GH_TOKEN` env var, per
  gh's own documented precedence — we don't touch it).
- The credentials modal's connected badge is a synthetic
  `cli-managed` **marker OAuth row** (`store_oauth_tokens(provider="github", ...)`),
  written only after `gh auth status` confirms a live session and
  removed on logout — exactly like Stripe.
- There is **no auth pre-flight** in the node: gh's own "To get
  started… run: gh auth login" error surfaces verbatim through the
  `NodeUserError` wrap.
- The **git node (future, separate folder)** needs zero auth code:
  login success runs the official `gh auth setup-git`, which
  configures git to use gh as its credential helper.

## File map

| File | Role |
|---|---|
| `__init__.py` | `register_ws_handlers(WS_HANDLERS)` + `register_output_schema("githubAction", …)`; node auto-registers on import |
| `github_action.py` | `GitHubActionNode(ActionNode)`, `usable_as_tool=True`, `tool_name="github"`, gmail-style multi-`@Operation` dispatch; `_run` = `ensure_gh_cli()` → `run_cli_command(env=gh_env(), cwd=…)` → non-zero exit → `NodeUserError` (stderr tail). `ui_hints = {"outputMode": "terminal"}` — the Output panel renders textual output preformatted, parsed `--json` payloads as a tree; `_shape` omits empty keys and never ships raw stdout alongside a parsed `result` |
| `_handlers.py` | `github_login` / `github_logout` / `github_status`; login-banner parser; marker + broadcast helpers |
| `_service.py` | `gh_env()` (automation baseline: `GH_PROMPT_DISABLED=1`, `NO_COLOR=1`, `GH_NO_UPDATE_NOTIFIER=1`, `GH_PAGER=cat`), `login_env()` (see below), `resolve_gh_light()` (no-download binary probe), `cli_logged_in()` (`gh auth status` exit 0), `resolve_repo_path()` (workspace-relative cwd, vercel idiom) |
| `_install.py` | `ensure_gh_cli()` — **project-local, pooch-driven** (mirrors `services/temporal/_install.py`, the repo's shared binary-fetch pattern): pinned release extracted under `package_dir("gh")` via `pooch.retrieve` (caching + Unzip/Untar handled by the library). The system-global gh is never consulted; gh's user-level config/credential-store is shared either way, so terminal `gh auth login` sessions remain visible. Note gh's `macOS`-capitalized zip assets |
| `_credentials.py` | `GitHubCredential` thin marker (`auth="custom"`, `resolve()` → `{}` — nothing to resolve) |
| `meta.json` | Node color. No co-located `icon.svg` — the icon is the official brand glyph via `visuals.json: {"githubAction": {"icon": "lobehub:Github", "skill": "github-skill"}}` |

Paired skill: [`server/skills/github/github-skill/SKILL.md`](../server/skills/github/github-skill/SKILL.md).
Palette group: `vcs` ("Version Control", registered in `groups.py`;
the future git node joins it from its own folder — same-group,
different-folder is established practice, e.g. `social` spans three
folders).

## The login flow (source-verified against gh's own code)

`github_login` spawns::

    gh auth login --hostname github.com --git-protocol https --web

Facts from `internal/authflow/flow.go` + `pkg/cmd/auth/login/login.go`
+ `cli/oauth` that the handler is built on:

1. github.com always gets the **device flow** (`DetectFlow` tries it
   first; the localhost-callback webapp flow is only an
   unsupported-grant fallback).
2. With **no TTY** the flow takes the `isInteractive=false` branch —
   **no "Press Enter" block** — and prints to **stderr**:
   `! First copy your one-time code: XXXX-XXXX` then
   `Open this URL to continue in your web browser: https://github.com/login/device`,
   then polls until the user authorises.
3. `gh auth login` **aborts when `GH_TOKEN`/`GITHUB_TOKEN` is set**
   ("The value of the … environment variable is being used for
   authentication"). Hence `login_env()` = `gh_env()` minus both
   token vars minus `GH_PROMPT_DISABLED` — used for login, status,
   and logout so gh consults its OWN credential store.

Handler mechanics: chunk-based banner reader (no `readline()` —
spinner `\r` frames; pumps outlive the call so gh never blocks on a
full pipe during its ~15-minute poll), parses the code + device URL,
returns `{success, url, verification_code}` — the modal opens the URL
and **displays the one-time code** (the generic `verificationCode`
plumbing in `useCredentialPanel` / `OAuthConnect`; GitHub's device
page cannot pre-fill the code). A response-budget shield (~22 s)
returns a pending message if a cold gh download eats the window
(vercel precedent). Background completion: `proc.wait()` ≤ 600 s →
gate on `cli_logged_in()` (exit codes alone are never trusted — Stripe
precedent) → best-effort `gh auth setup-git` → marker +
`credential.oauth.connected` broadcast.

`github_logout`: `gh auth logout --hostname github.com` (best-effort)
→ remove marker → `credential.oauth.disconnected`.
`github_status`: `{connected: cli_logged_in()}` — no side effects.
Terminal logins (`gh auth login` run by the user) are first-class: ops
and status see the same gh session; only the modal badge waits for a
marker written via the modal's own Login.

## Operations

| Op | argv shape | Notes |
|---|---|---|
| `repo_clone` | `repo clone <repo> [dir]` | cwd required (workspace default); 600 s timeout |
| `pr_create` | `pr create [--repo O/R] --title … --body … [--base] [--head] [--draft] \| --fill` | New PR URL from stdout → `url` |
| `pr_list` | `pr list [--repo O/R] --state S --limit N --json <fields>` | Parsed JSON in `result` |
| `pr_merge` | `pr merge <pr> [--repo O/R] --squash\|--merge\|--rebase [--delete-branch]` | |
| `issue_create` | `issue create [--repo O/R] --title … --body … [--label …]…` | URL → `url` |
| `issue_list` | `issue list [--repo O/R] --state S --limit N --json <fields>` | `merged` state coerced to `all` |
| `custom` | verbatim after `gh ` (shlex-split) | cwd = `path`/workspace when available; `gh api` returns parsed JSON |

## Tests

[`server/tests/test_github_plugin.py`](../server/tests/test_github_plugin.py):
env builders (ambient-token passthrough for ops, stripping for login),
banner parser against the source-verified strings, pinned release
asset map + member paths, op argv builders, gh-error-surfaces-verbatim
(and `test_node_has_no_auth_preflight` locking the Stripe-strict
no-pre-flight contract), login budget/fast-path, marker + broadcast +
`setup-git` introspection, fieldless catalogue shape, assets. Plus the
generic `test_plugin_contract.py` / `test_plugin_self_containment.py`
suites (`github` is in `_MIGRATED_PLUGINS` + `_PLUGINS_WITH_HANDLERS`).

## Runtime caveat

gh issue #12925 reports that on some gh versions the `--web` flow may
not begin polling until the browser step is acknowledged; the pinned
version should be sanity-checked on bump (the failure mode is benign —
login completes but the background gate logs a warning until the user
authorises).
