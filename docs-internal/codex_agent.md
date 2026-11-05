# Codex agent

OpenCompany runs Codex through its native CLI. Use this guide for sign-in,
model selection and troubleshooting; see the [node contract](node-logic-flows/specialized_agents/codex_agent.md)
for parameters and handles, and the [CLI framework](cli_agent_framework.md)
for implementation details.

## Connect and run

1. Open **Credentials → Codex CLI** and choose Login. Use **Use device code**
   when the backend runs remotely or inside Docker, or the browser cannot reach
   the CLI's localhost callback. Device authentication must be allowed by the
   account or workspace.
2. Complete the browser flow. The panel polls the CLI and shows Connected only
   after `codex login status` succeeds. If a sign-in fails, use Refresh to check
   again, or start a new sign-in. Cancel sign-in stops a pending login.
3. Add a Codex node, leave **Model** blank for **Auto**, and enter a prompt.
   An upstream Input connection is optional when the Prompt field is populated.
4. Set **Working directory** to the Git repository the task should use when the
   workflow workspace has no suitable repository. Without an explicit path,
   the service searches for an enclosing Git repository; in a source checkout
   this can resolve to the OpenCompany repository.

The common single-task parameter shape is:

```json
{
  "prompt": "Reply with a short greeting.",
  "model": null,
  "tasks": [],
  "allowed_credentials": []
}
```

For an explicit batch, supply an actual array of task objects in `tasks`.
An empty array uses the single-prompt path. Saved `null` values for `tasks`
and `allowed_credentials` also mean empty arrays, matching Claude Code.
Other malformed non-list values remain validation errors.

## CLI installation and authentication

The package version is pinned in [ai_cli_providers.json](../server/config/ai_cli_providers.json)
(current pin: `@openai/codex@0.144.2`). The app installs it on demand in
`<DATA_DIR>/packages/`; the launcher is
`node_modules/.bin/codex` on POSIX or `codex.exe` on Windows.

Login, status, logout and task execution all use
`CODEX_HOME=<DATA_DIR>/codex`. A personal terminal login under `~/.codex`
does not connect this separate app session. OpenCompany does not change
`HOME` or read/write the CLI's credential file. The CLI owns credentials;
the app stores only a `cli-managed` catalogue marker. The UI offers native
ChatGPT browser/device sign-in. OpenCompany does not inject its saved OpenAI
API key into Codex; ordinary subprocess environment inheritance still applies.
The app sets its managed `CODEX_HOME` explicitly, including when the parent
environment contains a different value.

Cold installation and initial status checking run in a single background login
task. The login request waits at most five seconds for instructions, then
returns pending. Native login has a 600-second bound; status and logout have
30-second bounds. Disconnect runs native logout and clears the marker. Pending
login is cancelled on logout or backend shutdown; cancelling during the initial
installation/status check does not start another installer just to log out.

Manual diagnostics must use the app-managed launcher and the same
`CODEX_HOME`. A successful status message can be written to stderr; check
the exit code rather than interpreting stderr as failure. Never use credential
file existence as the connection check or copy auth files into a report.

Official references: [authentication](https://learn.chatgpt.com/docs/auth),
[login commands](https://learn.chatgpt.com/docs/developer-commands#codex-login),
and [CODEX_HOME](https://learn.chatgpt.com/docs/config-file/environment-variables#core-locations).

### Existing login and issue #128

[Issue #128](https://github.com/zeenie-ai/OpenCompany/issues/128) reported a
hardcoded Login failure and misleading instructions that promised a connected
badge after terminal authentication. The current managed integration checks
real native status: an already authenticated app session is marked connected
without launching another browser/device login. Exit code zero is the gate,
including when Codex writes its success text to stderr. The handler stores the
`codex_cli` marker and broadcasts `credential.oauth.connected`; the catalogue
then supplies the Connected badge.

Login owns preparation of a missing or outdated managed CLI. Status, Refresh
and native logout only use an existing launcher at the pinned version; they
do not install packages or wait for the installer lock. An unprepared status
returns guidance to click Login to prepare the CLI. A failed probe is not
treated as authenticated, and a failed native logout does not falsely clear
the marker. If the last verified marker still shows Connected after a failed
Refresh, choose **Reconnect with Codex CLI** or **Use device code**. Both start
the normal preparation path. Pending retries show the sign-in message, code
and link when available, plus Cancel sign-in, even while the previous catalogue
marker remains connected.

Status releases the login lock while the native command runs. Its result is
checked against the authentication revision before it can update state. Each
new status probe advances that revision, so only the latest-started probe may
apply its result. Login, Logout and shutdown also advance it, preventing older
probes from restoring stale connection state. A confirmed signed-out status
clears the durable marker and
broadcasts disconnection even after a backend restart, when no connected flag
exists in memory. For a headless backend, use the device-code flow against this
same managed session.

## Auto and explicit model selection

Auto omits `--model`, allowing Codex to use its configured or account-recommended
model. A task model overrides the node model; provider configuration supplies
a default only when present. Explicit nonempty selections are preserved.

ChatGPT sign-in and API-key authentication can expose different model sets.
The ordinary OpenAI API model catalogue is not the model picker for a Codex
subscription. Do not replace a failing model with another hard-coded name or
silently retry on a different model.

Changing the application default does not rewrite saved node settings, explicit
task overrides, managed CLI configuration or previously admitted Temporal
snapshots. Clear an obsolete saved Model value to return that node to Auto;
a task-level override must be changed on that task. Deployed executions keep
their captured settings until a new admission captures the intended change.

See [official local model defaults](https://learn.chatgpt.com/docs/models#configure-your-default-local-model)
and the [exec model override](https://learn.chatgpt.com/docs/developer-commands#codex-exec).

## Execution and results

Each task gets a piped `codex exec --json` subprocess, normally in its own Git
worktree. Success requires process exit zero, a `turn.completed` event and no
`turn.failed`. Completion waits for process exit and both output streams,
so a terminal event alone cannot truncate the final response. Timeout or
cancellation reaps the process tree before worktree cleanup.

The last completed `agent_message` becomes the response. `thread.started`
provides the thread id; completed-turn usage supplies token counts, with cached
input separated from uncached input. The exec JSONL protocol supplies no native USD cost. The
shared pricing service may derive one when the selected model and price are
known; otherwise cost stays `null`.

Connected tools and skills are scoped through the batch's MCP bridge. Its
bearer token is passed through an environment variable, not argv or persisted
CLI configuration. MCP startup is required for the batch. See
[JSONL events](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)
and [HTTP MCP configuration](https://learn.chatgpt.com/docs/extend/mcp#streamable-http-servers).

The node exposes no native resume, warm pool, budget cap or turn limit.
Codex CLI itself has broader capabilities. Context continuity in OpenCompany
uses the stored-conversation prompt bridge; Context-bound node results suppress
`session_id` so it is not mistaken for a native continuation contract.

## Temporal and development restarts

Codex and Claude Code run as whole-node Activities outside
`AGENT_WORKFLOW_TYPES`. Temporal does not split their internal model/tool loop
into `AgentWorkflow` LLM Activities. Frozen own-node settings become
`node_data` and pass through shared executor and plugin validation, including
the nullable-list compatibility handling. Neither the fix nor a parameter edit
rewrites recorded history.

Both plugins declare the AI-heavy queue and one attempt. Ordinary graph
Activities use a 24-hour outer bound and a two-minute heartbeat timeout;
the per-type wrapper heartbeats every 30 seconds. A Codex task has its own
600-second default timeout. Generation Stop/Resume and native CLI resume
are different contracts; see [Temporal workflow control](temporal-workflow-control.md)
and [Temporal architecture](TEMPORAL_ARCHITECTURE.md).

`company dev` gives the frontend Vite HMR, but starts uvicorn without
`--reload`. After backend, plugin or provider-config changes, stop the existing
foreground dev session with Ctrl+C and run `bun run dev` once, then refresh
the browser. A retained backend can continue serving old schema defaults.
Daemon restart commands target a different launch mode; stopping only uvicorn
does not reliably restart the dev supervisor. See [development setup](SETUP.md).

## Diagnose the failing stage

| Evidence | Interpretation and next step |
|---|---|
| Historical “Codex login is not yet wired” message | That placeholder was replaced. Restart the backend on the updated build; a global CLI install alone does not update running application code. |
| `tasks` / `allowed_credentials`: “Input should be a valid list” with saved nulls | Load the nullable-list fix. Null becomes `[]` at validation; JSON strings or objects still need correcting. |
| `input=no` in connection-collection logs | No upstream input payload was collected. A saved Prompt can still supply the task; check the task prompt and CLI user event before concluding input was lost. |
| MCP authentication and ListTools succeed, then HTTP 400 `model_not_found` | The request reached Codex but the selected model was rejected. Clear the obsolete model pin or choose a model available to that account. |
| Empty response and zero usage after `turn.failed` | No completed generation is available. Inspect the readable task error and `provider_data.error_code` / `status_code`; this does not by itself indicate response-parser failure. |
| MCP DELETE after failure | Normal session teardown can follow an earlier provider error. Find the first failure before treating cleanup as the cause. |
| Old Model default remains in the served schema | Restart the existing dev backend; source edits do not hot-reload Python in `company dev`. |

Relevant native evidence lives under `<CODEX_HOME>/sessions/` and the configured
Codex log directory (the default app path is `<DATA_DIR>/codex/log/`, including
`codex-login.log`). Inspect event type, selected model, error code and timing.
Keep tokens, auth files, authorization URLs and private prompt/file contents
out of shared logs.

## Regression coverage

- [Provider tests](../server/tests/services/cli_agent/test_providers.py): Auto/explicit model precedence, argv, JSONL responses, nested provider errors and usage.
- [Login tests](../server/tests/services/cli_agent/test_codex_login.py): status exit semantics, browser/device instructions, single-flight login, refresh, timeout, cancellation and shutdown.
- [Login-to-catalogue tests](../server/tests/services/cli_agent/test_codex_login_catalogue.py): issue #128 acceptance from native status through the real marker helpers, catalogue connection state and credential broadcasts.
- [Authentication probe tests](../server/tests/services/cli_agent/test_codex_auth_probe.py): status/logout never install or wait for preparation, missing/outdated CLI guidance, managed environment isolation and sanitized failures.
- [Session tests](../server/tests/services/cli_agent/test_codex_session.py): chunked/final JSONL, pipe draining, failures, timeout, process-tree cleanup and environment isolation.
- [Parameter and Activity tests](../server/tests/nodes/test_codex_parameters.py): null lists and unchanged explicit settings through direct, snapshot and real Temporal Activity wrapper paths for Codex and Claude.
- [Dispatch tests](../server/tests/temporal/test_dispatch.py): Codex stays on the whole-node Activity path.

On 2026-11-04, the model-selection/parser change passed 225 targeted tests and a
live read-only CLI smoke check returned `CODEX_OK` with no tool calls. That
check verifies the native Auto path, not every model, permission mode or
deployment configuration.
