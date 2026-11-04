# JavaScript Executor (`javascriptExecutor`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / code |
| **Backend handler** | [`server/nodes/code/javascript_executor/__init__.py::JavaScriptExecutorNode.execute_op`](../../../server/nodes/code/javascript_executor/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("execute")`; base in [`_base.py`](../../../server/nodes/code/_base.py)) |
| **Sidecar client** | [`server/nodes/code/_nodejs.py::get_nodejs_client`](../../../server/nodes/code/_nodejs.py) (singleton over [`server/nodes/code/_client.py::NodeJSClient`](../../../server/nodes/code/_client.py) — plugin-owned since July 2026; the old `nodejs_client.py` service module no longer exists) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py); the sidecar token in [`server/tests/nodes/test_js_executor_token.py`](../../../server/tests/nodes/test_js_executor_token.py) |
| **Skill (if any)** | [`server/skills/coding_agent/javascript-skill/SKILL.md`](../../../server/skills/coding_agent/javascript-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `javascript_code` |

## Purpose

Runs user JavaScript through the persistent JS executor sidecar (Express on the
bun runtime, `server/nodejs/dist/index.js`) that the backend spawns on demand
from `nodes/code/_runtime.py` on the first JS/TS execution. The plugin does
**not** spawn a runtime per call - it POSTs the code to
`http://localhost:${NODEJS_EXECUTOR_PORT}/execute` via the shared
`get_nodejs_client()` singleton (an async `aiohttp` client). The sidecar
evaluates the script in a `node:vm` context and returns
`{success, output, console_output, ...}`.

The plugin merges the caller's upstream outputs (`ctx.raw["connected_outputs"]`)
into an `input_data` object, injects the workflow's `workspace_dir`
(`ctx.workspace_dir`) as a key, then forwards the payload with `language="javascript"`.
`connected_outputs` is injected by the executor for code-executor node types.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream outputs merged into the sidecar-side `input_data` object |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `code` | string (code editor) | (required, `min_length=1`) | yes | - | JavaScript source. User must assign to `output` |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Seconds - multiplied by 1000 and forwarded as millisecond timeout to the sidecar |

`CodeExecutorParams` uses `extra="allow"` (extra params persist but are unread).

## Sidecar client singleton

`get_nodejs_client()` in [`_nodejs.py`](../../../server/nodes/code/_nodejs.py) lazily
constructs one `NodeJSClient` shared across the JS + TS plugins (base URL from
`NODEJS_EXECUTOR_URL`, else `http://localhost:${NODEJS_EXECUTOR_PORT}`; request
timeout from `NODEJS_EXECUTOR_TIMEOUT`; no per-call kwargs from the plugin) and,
unless `NODEJS_EXECUTOR_URL` points at an external executor, asks the
plugin-owned supervisor to `ensure_started()` the sidecar on the bun runtime
(`OPENCOMPANY_BUN_BIN`, else `bun` on PATH).

The sidecar runs whatever code it is sent, so every route but `/health`
answers only requests carrying the token it was started with, in the
`X-OpenCompany-Executor-Token` header (401 otherwise; it compares the
header's bytes with the token's UTF-8 bytes). The runtime makes the token
once per backend process (`NodeJSExecutorRuntime.token`; the operator's
`NODEJS_EXECUTOR_TOKEN` wins when set), passes it to the sidecar it spawns as
`NODEJS_EXECUTOR_TOKEN`, and the client sends it on every request. A token
the runtime made goes only to the sidecar it started (see "A process this
backend did not start" below). A sidecar started without a token refuses
every such request.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `javascript_code` tool result) |

### Output payload

```ts
{
  output: any;              // Value the sidecar read from `output` in the script
  console_output: string;   // Captured console.log/.error etc.
}
```

`node_output_schemas.CodeExecutorOutput` declares only `output` (the
`_OutputBase` base allows extra fields like `console_output`).

## Logic Flow

```mermaid
flowchart TD
  A[execute_op] --> B{code strip empty?}
  B -- yes --> E[raise NodeUserError:<br/>No code provided]
  B -- no --> C[timeout_ms = timeout * 1000]
  C --> D[input_data = ctx.raw connected_outputs or {}<br/>inject workspace_dir]
  D --> F[acquire_client<br/>start or reuse the sidecar,<br/>lazy client singleton]
  F -- port held by a process<br/>this backend did not start --> H1
  F --> G[client.execute<br/>POST /execute<br/>language=javascript]
  G -- ClientConnectorError or 401 --> H1[raise NodeUserError<br/>JS executor unavailable]
  G -- success=false --> H2[raise NodeUserError<br/>error=result.error]
  G -- success=true --> I[Return dict<br/>output, console_output]
```

## Decision Logic

- **Validation**: `code.strip() == ""` -> `raise NodeUserError("No code provided")`.
- **Timeout unit mismatch**: UI accepts seconds, plugin multiplies by 1000
  before forwarding. Default 30 -> 30000 ms. Pydantic clamps the input to 1-600 s.
- **`workspace_dir` injection**: unconditionally sets
  `input_data["workspace_dir"]`, shadowing any upstream node that happened to
  produce a key with that name.
- **Client reuse**: `get_nodejs_client()`'s `_client` is a **module-global**
  singleton in `_nodejs.py`, fixed at the env-derived `base_url`/`timeout`.
- **Sidecar error propagation**: if `result["success"]` is falsey, the
  plugin raises `NodeUserError(result["error"] or "JavaScript executor failed")`.
- **Sidecar down**: `aiohttp.ClientConnectorError` is caught and re-raised as a
  `NodeUserError` telling the LLM the JS executor is unreachable on
  `NODEJS_EXECUTOR_PORT` and to fall back to `python_executor`.
- **Port held by another process**: `ensure_started()` raises `RuntimeError`
  before any request is sent, and the plugin re-raises it the same way (see
  "A process this backend did not start" below).
- **Token refused**: a 401 (from an external executor, or a shared one started
  with another token) makes the client raise `RuntimeError`, which the plugin
  re-raises the same way, saying the executor refused this backend's token.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none from this handler.
- **External API calls**: `POST http://localhost:${NODEJS_EXECUTOR_PORT}/execute`
  (configurable via `NODEJS_EXECUTOR_URL`) with the
  `X-OpenCompany-Executor-Token` header. Body:
  `{code, input_data, language: "javascript", timeout}`.
- **File I/O**: none from Python; the sidecar may read/write user packages at
  `server/nodejs/user-packages/` (`bun add` via `/packages/install`).
- **Subprocess**: none directly. The sidecar itself is a long-lived bun
  subprocess supervised by `nodes/code/_runtime.py` (spawned on demand, reaped
  by the lifespan's `shutdown_all_supervisors()`).
- **Module-level state**: the `_client` module global in `_nodejs.py` is created
  on first use and never reset.

## External Dependencies

- **Credentials**: none.
- **Services**: the persistent JS executor sidecar on bun (`OPENCOMPANY_BUN_BIN`
  or `bun` on PATH; bundled by the desktop app), spawned on demand by the
  plugin's supervisor - nothing has to be started by hand.
- **Python packages**: `aiohttp`.
- **Environment variables**: `NODEJS_EXECUTOR_URL`,
  `NODEJS_EXECUTOR_TIMEOUT`, `NODEJS_EXECUTOR_PORT`, `NODEJS_EXECUTOR_HOST`,
  `NODEJS_EXECUTOR_BODY_LIMIT` (all read by the sidecar itself), and
  `NODEJS_EXECUTOR_TOKEN`: the token the sidecar requires on every route but
  `/health`. Unset, each backend process makes its own and gives it to the
  sidecar it spawns. Set it, to the same value on both sides, for an external
  executor (`NODEJS_EXECUTOR_URL`) or for several backend processes sharing
  one sidecar, in their environment or an untracked env file (never a tracked
  one such as `.env.template`). A sidecar started by hand needs it too, and
  the header on each request.

## Edge cases & known limits

- **Module-level client singleton**: `_client` in `_nodejs.py` is cached on
  first call at the hard-coded `base_url`/`timeout`. Reset by setting
  `services.code._nodejs._client = None` (or `nodes.code._nodejs._client`).
- **Sidecar down**: connection refused surfaces as
  `error="Cannot connect to host localhost:<NODEJS_EXECUTOR_PORT> ssl:default
  [Connect call failed]"` or similar aiohttp message. No automatic retry.
- **A process this backend did not start**: `ensure_started()` leaves alone
  any process already listening on `NODEJS_EXECUTOR_PORT`, whether a sidecar
  an earlier backend left running (one that crashed while its sidecar kept
  running), one started by hand or by another checkout, or any other program.
  With `NODEJS_EXECUTOR_TOKEN` unset it sends that process nothing, because
  the token made for this backend must reach only the sidecar it starts:
  every JS/TS run fails with "port ... is held by a process this backend did
  not start" until the process is stopped (`company stop` stops a sidecar
  its own checkout left running; the backend then starts its own) or
  `NODEJS_EXECUTOR_PORT` is changed. With `NODEJS_EXECUTOR_TOKEN` set, the
  backend uses whatever listens there, which is how several processes share
  one sidecar, and one started with another token answers 401. The backend
  never kills a process it did not start.
- **`workspace_dir` key collision**: user code cannot read an upstream
  `workspace_dir` from `input_data` - the handler always overwrites it.
- **Timeout semantics**: the Python-side `timeout` is a ceiling on the
  aiohttp request itself (set once at client creation); the
  `timeout_ms` forwarded in the body is the sidecar's script timeout.
  These two can disagree - if the script timeout is longer than the aiohttp
  timeout, the HTTP call fails before the script finishes.
- **`console_output` may contain partial output**: the sidecar captures
  `console.*` calls into a buffer and returns it at end-of-run, so on a script
  timeout the response carries whatever was captured up to the abort.
- **JSON-only transport**: `output` must be JSON-serialisable on the sidecar
  side. Functions, `undefined`, `BigInt`, circular refs are stripped or
  rejected by `JSON.stringify` before return.

## Related

- **Skills using this as a tool**: [`javascript-skill/SKILL.md`](../../../server/skills/coding_agent/javascript-skill/SKILL.md)
- **Sibling nodes**: [`typescriptExecutor`](./typescriptExecutor.md), [`pythonExecutor`](./pythonExecutor.md), [`montyExecutor`](./montyExecutor.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md), [Plugin System](../../plugin_system.md)
