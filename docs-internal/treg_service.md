# treg Service (`tools-registry` CLI)

The `tregAction` node reaches [treg](https://treg.to), a catalog of
thousands of paid third-party tools (search results, SEO data, social
profiles and posts, people and company data, web pages, and image, video
and voice generation) behind one sign-in. Each call is billed to the
team's prepaid treg balance. The node runs treg's own command-line tool,
the PyPI package `tools-registry` (command `treg`), which OpenCompany
installs with uv at first use. One self-contained plugin folder at
[`server/nodes/treg/`](../server/nodes/treg/).

The CLI owns its sign-in, as gh and cf do
([GitHub](./github_service.md), [Cloudflare](./cloudflare_service.md)):
OpenCompany relays the link and code `treg login` prints and stores only
the connected marker. OpenCompany never imports or ships treg's code (see
"Why the CLI, and the licence").

| | |
|---|---|
| Node type | `tregAction` (palette group `api`, dual-purpose AI tool `treg_action`; in the Hire allowlist) |
| Operations | `search` (default) / `describe` / `call` / `balance` |
| CLI pin | `tools-registry==0.30.0` (`SPEC` in [`_install.py`](../server/nodes/treg/_install.py)), in its own uv tool environment |
| Auth | `TregCredential` (`auth = "custom"`) is a marker; the CLI keeps the team key in its own config file under `<DATA_DIR>/treg` |
| Task queue | `TaskQueue.REST_API` |
| Cost | What treg says it charged (`_treg.charged_micro`, or `reserved_micro` for a task): one `api_usage_metrics` row per billed call. No `pricing.json` entry |
| Approvals | `search`, `describe` and `balance` are `read`; `call` is `money` and is refused while the owner asks first |
| Tests | [`server/tests/test_treg_plugin.py`](../server/tests/test_treg_plugin.py) (`pytest --collect-only -q` for the count) |
| Paired skill | [`server/skills/web_agent/treg-skill/SKILL.md`](../server/skills/web_agent/treg-skill/SKILL.md) |
| Node card | [`tregAction.md`](./node-logic-flows/web_automation/tregAction.md): parameters, output shapes and the flow |
| Hire app | `treg` in [`employee_apps.json`](../server/config/employee_apps.json) (`side_effects: "money"`) |

## Folder map

```
server/nodes/treg/
├── __init__.py        # imports the credential, the WS handlers and the node; register_ws_handlers
├── _credentials.py    # TregCredential: a marker; is_configured reads the sign-in marker
├── _install.py        # TregInstaller: uv tool install of SPEC into a private tool folder
├── _service.py        # treg_env, run_treg, spawn_treg, signed_in_account (treg config),
│                      # verified_account (treg whoami)
├── _handlers.py       # treg_login / treg_status / treg_logout (the sign-in relay), shutdown hook
├── _request.py        # check_tool_id, parameters_of, build_request: parameter placement (pure)
├── _result.py         # deliver / preview_of: large answers to <workspace>/treg/
├── treg_action.py     # TregActionParams / TregActionOutput / TregActionNode (4 @Operations)
├── icon.svg           # node mark
├── treg.svg           # credential icon -> /api/schemas/credentials/treg/icon
└── meta.json          # {"color": "#d7875f"}
```

`_install.py` is an adapted copy of the Browser plugin's
`_install_bu.py`, and `_result.py` of TikHub's `_result.py`: a plugin does
not import another plugin's folder.

Outside the folder: `credential_providers.json` (`treg`, below),
`node_allowlist.json`, `employee_apps.json`, `visuals.json`
(`"tregAction": {"skill": "treg-skill"}`),
`tests/fixtures/tool_names_snapshot.json`,
`services/approvals/classes.py` and `repeats.py`, and the node's row in
`docs/diagrams/architecture/nodes.json` (edited by hand; `export-nodes`
rewrites `source_commit`).

## Why the CLI, and the licence

The owner chose to wrap treg's CLI rather than call treg's HTTP API
(2026-11-13). The CLI is treg's supported client: the sign-in, the request
shapes and the `--json` envelope that reports what a call cost are
treg's, and a new CLI release is one pin bump.

treg's licence (Apache 2.0 with additional terms) forbids using treg's
software as an embedded component of a product sold to others without
treg's written permission; calling treg's hosted API from a product is
allowed by treg's Terms. So OpenCompany never imports `tools-registry`
and never ships it: uv downloads the CLI from PyPI onto the owner's
machine at first use. Before OpenCompany is sold with this node, ask treg.

## Install - `_install.py`

- `uv tool install --reinstall --python <python> tools-registry==0.30.0`,
  with `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` set to
  `<DATA_DIR>/packages/treg/tools` and `<DATA_DIR>/packages/treg/bin`, or
  to the desktop app's private uv tool folders when it sets both. Never
  uv's default folders, so a `treg` the owner installed in a terminal
  (`~/.local/bin`) is never replaced.
- uv comes from `OPENCOMPANY_UV_BIN`, else PATH. Without it the node and
  the sign-in say so and need the owner (`requires_user_action`). The
  default Docker image has no uv; the `browser` extra adds it
  ([Docker](./docker.md)).
- `--python` is the interpreter behind the backend's venv when it is 3.12
  or 3.13, which the CLI supports, else the range `>=3.12,<3.14` for uv to
  find one. `VIRTUAL_ENV` is removed from uv's environment. No extras are
  installed.
- `opencompany-treg.json` in the tool folder records the installed spec. A
  CLI whose stamp names another spec counts as not installed, so a pin
  bump reinstalls on next use.
- One install at a time (concurrent callers share it), a 600 s timeout,
  and a 60 s cooldown before a failed install is tried again. An operation
  waits up to 300 s for it and a sign-in up to 900 s; past that the
  caller is told to try again in a minute while the install carries on.

## One environment for every run - `_service.py`

Every run is `treg --json <argv>` (the sign-in is `treg login`) as an
argument list, never through a shell, from the `<DATA_DIR>/treg` folder,
with the environment `treg_env()` builds:

| Setting | Why |
|---|---|
| `TREG_CONFIG=<DATA_DIR>/treg/config.json` | The CLI keeps its sign-in there, apart from the `~/.treg` of a terminal `treg` |
| Every inherited `TREG_*` variable removed | An inherited `TREG_TOKEN` would override that sign-in; `TREG_URL` and `TREG_ORG` would redirect it |
| `TREG_TELEMETRY=0` and `DO_NOT_TRACK=1` | No usage analytics from the CLI |
| `TREG_CLIENT=opencompany` | treg's label for the caller. Without it treg records "cli", or "claude-code" when the backend was started from a Claude Code terminal |
| `BROWSER` set to `"<python>" -c pass %s` | `treg login` opens its page with Python's `webbrowser`, which runs a `BROWSER` command line containing `%s` with the URL and stops at its exit code 0. This one opens nothing; the Credentials modal opens the link instead, which also works when OpenCompany runs on another machine |
| 1Password variables removed | `without_onepassword_environment`, as for the other CLIs OpenCompany runs |
| stdin a pipe, closed after the body or at once | The CLI never prompts. On Windows an inherited `NUL` counts as a terminal, which starts treg's first-run questions after a sign-in |

`run_treg` goes through `services.events.run_cli_command`. Values reach the
CLI as `--flag=value` arguments, positionals first
(`catalog search <query> --limit=N`). The CLI removes `--json` and
`--org` arguments wherever they stand, so the node refuses a query that
starts with `-`.

## Sign-in relay - `_handlers.py`

`treg login` asks treg.to for a one-time sign-in and prints a link,
`https://treg.to/login?cli=<id>#code=<CODE>`. It then polls for about
three minutes until the owner approves the sign-in in a browser, on this
machine or another, and saves the team key in its config. OpenCompany
relays the link and the code and stores only the `cli-managed` marker
([`services/cli_agent/_cli_auth.py`](../server/services/cli_agent/_cli_auth.py)).

- **`treg_login`** runs one sign-in at a time; a repeat click answers the
  running one's state. It installs the CLI first ("Installing the treg
  CLI. This happens once."). When `treg whoami` already answers with an
  email and a team, the provider is marked connected without a browser.
  Otherwise it spawns `treg login`, reads its output until the link
  appears (30 s), takes the code from the `#code=` fragment and answers
  within 15 s with `pending: true`, `url`, `verification_code` and a
  message; the modal opens the link and shows the code. When the CLI exits
  0, `treg config` must name the account. Then
  `mark_logged_in("treg", email=..., name=<team>)` and the
  `credential.oauth.connected` broadcast. A sign-in not finished within
  240 s is ended (`kill_tree`, then up to 5 s for the exit).
- **`treg_status`** answers the running sign-in's state, or its error
  unless `refresh` is set. Otherwise it reads `treg config`, which reads
  the config file and makes no request, and brings the marker in step,
  broadcasting the change. It runs whichever CLI is installed, so a pin
  bump does not sign the owner out.
- **`treg_logout`** cancels a running sign-in, runs `treg logout`, drops
  the marker and broadcasts. `treg logout` forgets the key on this machine
  only; the key stays valid until it is revoked on treg.to.
- The `treg_login` shutdown hook cancels a running sign-in, which ends the
  CLI.

Replies carry `connected`, `pending`, `url`, `verification_code`,
`message` and `error` at the top level, the shape Codex's sign-in uses:
the Credentials modal polls the status while `pending` is true and
reloads the catalogue when `connected` is set, so the card needs no
client code.

The link is never logged, and neither is any exception text that could
carry it: whoever polls treg.to first after the owner approves receives
the key.

## Credential and catalogue card

`TregCredential` (`id = "treg"`, `auth = "custom"`, category Scrapers):
`resolve()` returns nothing, because the key stays in the CLI's config.
`is_configured` reads the marker, so the editor's missing-credential
warning clears after a sign-in (the base class looks for an API key,
which a CLI sign-in never stores).

The `treg` entry in `credential_providers.json` is `kind: "oauth"` with
`ws: {login: "treg_login", status: "treg_status", logout: "treg_logout"}`,
`consumer_category: "research"` and `usage_service: "treg"`, so Dev mode
shows the node's `api_usage_metrics` rows on the card.

## Calls

`call` runs in this order:

1. `treg catalog get <id>` (free): the tool's method, where each parameter
   goes and, for a generation tool, how its task is checked. An unknown
   id, or a job id that names several providers, stops here.
2. `_request.build_request` places the parameters (the rules are in the
   [node card](./node-logic-flows/web_automation/tregAction.md#decision-logic)).
   It refuses a file upload (`bodyType: multipart`) and a tool that
   requires a request header.
3. `treg --json call <id> --method=M --query=K=V ... --header=X-Treg-Route-Max-Cost: <ceiling>`,
   plus `--data=@- --content-type=...` with the body on stdin when there
   is one. Timeout 200 s; the CLI waits up to 190 s for treg.
4. The CLI prints one envelope,
   `{"result": <the provider's answer>, "_treg": {"http_status", "call_id", "charged_micro" or "reserved_micro", "async", "routed_tool", "upstream_failed", ...}}`,
   and exits 1 when the status is 400 or more. A network failure prints
   no envelope; the node then says the call may have gone through and to
   check `balance` before calling again.
5. A charge above zero is recorded (next section). A status of 400 or
   more becomes a `NodeUserError`; otherwise the answer is delivered
   ("Results").

**The ceiling.** `max_cost_usd` (default $0.10) rides every call as
`X-Treg-Route-Max-Cost`, and treg refuses a call whose reservation would
exceed it before charging anything (`402 route_max_cost`). It is in
`server_controlled_fields`, so a model's tool arguments cannot raise it,
in process or through the executor on Temporal. It bounds one call, not a
run, and treg does not meter a call served on the team's own provider
key.

**Never repeated.** `call` is not in `repeats.py`, so a broken-off attempt
does not run again, and the node never retries: a call that did not come
back may have been charged.

## Cost tracking

treg reports in each envelope what it charged. `_record_cost` in
[`treg_action.py`](../server/nodes/treg/treg_action.py) records that
figure; `pricing.json` has no treg entry.

- One `save_api_usage_metric` row: `service: "treg"`,
  `operation: "call"`, `endpoint` the tool id, `resource_count: 1`,
  `cost` the charge in USD. It is written before the answer is saved, and
  also when the call failed but was charged.
- An asynchronous submission (a generation) reports `reserved_micro`, a
  hold treg settles when the task ends and refunds when it fails. The
  node records it as `operation: "task"` and returns it as `reserved_usd`,
  not `cost_usd`.
- No amount means treg did not bill the call (it ran on the team's own
  provider key), and zero is free: neither writes a row.
- Recording never raises: the call is already paid for.

## Approvals

- [`classes.py`](../server/services/approvals/classes.py): `search`,
  `describe` and `balance` are `read`; `call` is `money`. Every call
  spends the balance, and some catalog tools post or change things
  through accounts connected in treg.
- [`repeats.py`](../server/services/approvals/repeats.py): the three reads
  may run again after a broken-off attempt; `call` may not.
- The node's `ApprovalSpec` covers `call` only, with `refuse_while_asking`
  (Stripe's pattern): while the owner asks first, an agent's call is
  refused, saying so, because an approved call would run later and its
  paid answer would never reach the agent. The hire keeps the tool
  (`services/employees/policy.check_tool`); only its calls are refused. In
  a v1 generation (RFC-0007; Start does not write it yet) the class
  decides instead, and a `call` is held for the owner like Stripe's.

## Errors

Every failure the owner or a model can act on is a `NodeUserError` in
treg's or the provider's words.

| Case | Message (shape) | Needs the owner |
|---|---|---|
| No uv | treg's CLI is installed with uv, which this machine does not have. Install uv and try again. | yes |
| Install failed or timed out | The treg CLI could not be installed: <uv's last words>. A use within the next 60 s is told when it will be tried again | no |
| Install still running | Installing the treg CLI. This happens once; try again in a minute. | no |
| A read failed | treg search / describe of <id> / balance failed: <treg's words> (balance adds the sign-in hint) | no |
| `402 insufficient_balance` | The treg balance is too low for <id>: it needs about $X and the balance is $Y. Add funds to the treg team, then try again. | yes |
| `402 route_max_cost` | <id> would cost about $X, more than this node's ceiling of $Y per call ... Nothing was charged. | no |
| 401 | <id> answered HTTP 401: <words>, with the hint to sign in under Credentials -> treg | yes |
| 402 or 403, other | <id> answered HTTP <status>: <words> | yes |
| Any other status of 400 or more | <id> answered HTTP <status>: <words>. treg charged $X for it. (when it did) | no |
| No envelope | The call to <id> did not come back (<the CLI's last line>). It may have gone through and been charged: check operation=balance before calling it again. | no |
| Saving the answer failed | <the save error> The call itself succeeded (call id <id>). | no |

## Results

- A binary answer, which the envelope carries as `{"base64", "content_type"}`,
  is written to `<workspace>/treg/` with its media type's extension and
  returned as `file` with a `note`.
- JSON over 20,000 characters is written there indented and returned as
  `file`, `preview` and `note` ([`_result.py`](../server/nodes/treg/_result.py),
  the rule in [Media Transport](./media_transport.md#large-data-results-are-files-too)).
- Anything else is returned whole as `data`.

A generation tool answers with a task. `task` carries its id and `check`,
the `call` that reads its state, built from the catalog's descriptor,
with `status_path`, `done_when`, `failed_when`, `result_path` and
`interval_seconds`. The node does not wait: the agent runs `check`
itself.

## Pin bump recipe

1. Read the new release's source (its wheel on PyPI, or
   [github.com/superdesigndev/treg](https://github.com/superdesigndev/treg))
   for what the node relies on: the `call --json` envelope and its exit
   codes, the `catalog search` / `catalog get` arguments and fields, the
   `balance` fields, the link `treg login` prints, the `treg config` and
   `treg whoami` shapes, how `--json` and `--org` are taken out of the
   arguments, the `TREG_*` variables and the first-run questions.
2. Change `SPEC` in `_install.py`. Each install reinstalls on its next
   use; the config file, and with it the sign-in, is kept.
3. `uv run pytest tests/test_treg_plugin.py -q` in `server/`, then the
   live check below.

## Live check (run by the owner)

Not run yet. It signs in to the owner's treg account and spends about
$0.001.

1. Credentials -> treg -> Login: one browser tab opens and the modal shows
   the code, then "Signed in to treg as <email> (team <team>)".
2. On a canvas node, run `balance`, then `search` "google web search",
   then `describe` the top tool.
3. `call` it with `max_cost_usd` below its price: refused, balance
   unchanged.
4. `call` it with the ceiling above its price: `data`, `cost_usd` and
   `call_id`.
5. In Dev mode the treg card's usage section shows the call.
6. From an agent: with Ask first off the call runs; with it on, the call
   is refused.
7. Logout.

## Invariants locked by `test_treg_plugin.py`

The environment (private config, inherited `TREG_*` removed, analytics
off, the client label, a `BROWSER` that `webbrowser.get()` resolves to a
command that opens nothing), `run_treg` (`--json` first, stdin closed, run
from its folder) and the spawned sign-in. `treg config` and `treg whoami`
parsing. The installer: tool folders and default folders, the stamp and a
different pin, missing uv, one shared install, uv's words on failure. The
sign-in relay: link and code relayed and the marker set once treg exits,
no sign-in when treg accepts the key, a link without a code, the
deadline, treg's last line on failure, install before any sign-in, a CLI
that ignores the kill. Status and logout keep the marker in step; the link
is never logged; handlers and the shutdown hook are registered. The
node's class attributes, operations, flat tool schema and panel blanks.
`search`, `describe` (tool and job), `balance` and their refusals. Every
placement rule. `call`: lookup, method, ceiling and body; an unknown tool
or a job id never reaches `call`; billing rows for a charge, none for no
amount or zero, one even when saving fails; large, binary and task
answers; charged failures, a low balance, the ceiling, a rejected
sign-in; one attempt when the call does not come back; a model cannot
raise the ceiling. The approval spec, classes and repeats; the credential
marker, the catalogue card, the credential icon, the skill binding and
the hire app.

## Limits and risks

- **Docker.** The default image has no uv, so there the node and the
  sign-in say treg needs uv, as the Browser tools do.
- **The pin is bumped by hand**, and `tools-registry` ships every few
  days. A newer CLI is not used until `SPEC` changes.
- **One key per account and team.** treg issues the same team key to
  every sign-in by one account and team. Revoking it on treg.to signs out
  OpenCompany and the owner's terminal alike, while Logout here is local.
- **Spend.** The ceiling bounds one call, not a run: an agent can make
  many calls. Give an employee a treg team with a small balance and
  treg's team-wide daily spend cap set (a team setting on treg.to).
- **`balance` can trigger an automatic top-up** when the team turned
  automatic top-ups on and its balance is under the threshold (treg's own
  rule on its balance route).
- **Licence.** See above; ask treg before selling OpenCompany with this
  node.
- **Saved answers accumulate** under `<workspace>/treg/`; nothing removes
  them.
- **Later:** waiting for tasks and saving their media, an
  `Idempotency-Key` per call, customer tagging, the team's own registered
  tools, multipart bodies and per-call headers.
