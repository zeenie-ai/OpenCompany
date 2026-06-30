# JavaScript Executor (`javascriptExecutor`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / code |
| **Backend handler** | [`server/nodes/code/javascript_executor/__init__.py::JavaScriptExecutorNode.execute_op`](../../../server/nodes/code/javascript_executor/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("execute")`; base in [`_base.py`](../../../server/nodes/code/_base.py)) |
| **Node.js client** | [`server/nodes/code/_nodejs.py::get_nodejs_client`](../../../server/nodes/code/_nodejs.py) (singleton over [`server/services/nodejs_client.py::NodeJSClient`](../../../server/services/nodejs_client.py)) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/javascript-skill/SKILL.md`](../../../server/skills/coding_agent/javascript-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `javascript_code` |

## Purpose

Runs user JavaScript through the persistent Node.js executor server (Express +
tsx) that the Python backend spawns on startup. The plugin does **not** spawn
`node` per call - it POSTs the code to `http://localhost:3020/execute` via the
shared `get_nodejs_client()` singleton (an async `aiohttp` client). The Node.js
server evaluates the script and returns `{success, output, console_output, ...}`.

The plugin merges the caller's upstream outputs (`ctx.raw["connected_outputs"]`)
into an `input_data` object, injects the workflow's `workspace_dir`
(`ctx.workspace_dir`) as a key, then forwards the payload with `language="javascript"`.
`connected_outputs` is injected by the executor for code-executor node types.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream outputs merged into the Node-side `input_data` object |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `code` | string (code editor) | (required, `min_length=1`) | yes | - | JavaScript source. User must assign to `output` |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Seconds - multiplied by 1000 and forwarded as millisecond timeout to the Node server |

`CodeExecutorParams` uses `extra="allow"` (extra params persist but are unread).

## Node.js client singleton

`get_nodejs_client()` in [`_nodejs.py`](../../../server/nodes/code/_nodejs.py) lazily
constructs one `NodeJSClient(base_url="http://localhost:3020", timeout=30)` shared
across the JS + TS plugins. The defaults are hard-coded in the helper signature
(no per-call kwargs from the plugin).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `javascript_code` tool result) |

### Output payload

```ts
{
  output: any;              // Value the Node.js server parsed from `output` in the script
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
  D --> F[get_nodejs_client<br/>lazy singleton]
  F --> G[client.execute<br/>POST /execute<br/>language=javascript]
  G -- ClientConnectorError --> H1[raise NodeUserError<br/>JS executor not running on :3020]
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
  singleton in `_nodejs.py`, fixed at the hard-coded `base_url`/`timeout`.
- **Node server error propagation**: if `result["success"]` is falsey, the
  plugin raises `NodeUserError(result["error"] or "JavaScript executor failed")`.
- **Sidecar down**: `aiohttp.ClientConnectorError` is caught and re-raised as a
  `NodeUserError` telling the LLM the Node executor is unreachable on
  localhost:3020 and to fall back to `python_executor`.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none from this handler.
- **External API calls**: `POST http://localhost:3020/execute`
  (configurable via `NODEJS_EXECUTOR_URL`). Body:
  `{code, input_data, language: "javascript", timeout}`.
- **File I/O**: none from Python; the Node.js server may read/write user
  packages at `server/nodejs/user-packages/`.
- **Subprocess**: none directly. The Node.js server itself is a long-lived
  subprocess started by `main.py` lifespan.
- **Module-level state**: the `_client` module global in `_nodejs.py` is created
  on first use and never reset.

## External Dependencies

- **Credentials**: none.
- **Services**: Persistent Node.js executor at `http://localhost:3020`.
  Must be running - the backend start script boots it alongside uvicorn.
- **Python packages**: `aiohttp`.
- **Environment variables**: `NODEJS_EXECUTOR_URL`,
  `NODEJS_EXECUTOR_TIMEOUT`, `NODEJS_EXECUTOR_PORT`, `NODEJS_EXECUTOR_HOST`,
  `NODEJS_EXECUTOR_BODY_LIMIT` (all read by the Node server itself).

## Edge cases & known limits

- **Module-level client singleton**: `_client` in `_nodejs.py` is cached on
  first call at the hard-coded `base_url`/`timeout`. Reset by setting
  `services.code._nodejs._client = None` (or `nodes.code._nodejs._client`).
- **Node server down**: connection refused surfaces as
  `error="Cannot connect to host localhost:3020 ssl:default [Connect call
  failed]"` or similar aiohttp message. No automatic retry.
- **`workspace_dir` key collision**: user code cannot read an upstream
  `workspace_dir` from `input_data` - the handler always overwrites it.
- **Timeout semantics**: the Python-side `timeout` is a ceiling on the
  aiohttp request itself (set once at client creation); the
  `timeout_ms` forwarded in the body is the Node server's script timeout.
  These two can disagree - if the script timeout is longer than the aiohttp
  timeout, the HTTP call fails before the script finishes.
- **`console_output` may contain partial output**: Node server streams stdio
  into a buffer and flushes at end-of-run, but on script timeout the server
  returns whatever it captured up to the kill signal.
- **JSON-only transport**: `output` must be JSON-serialisable on the Node
  side. Functions, `undefined`, `BigInt`, circular refs are stripped or
  rejected by `JSON.stringify` before return.

## Related

- **Skills using this as a tool**: [`javascript-skill/SKILL.md`](../../../server/skills/coding_agent/javascript-skill/SKILL.md)
- **Sibling nodes**: [`typescriptExecutor`](./typescriptExecutor.md), [`pythonExecutor`](./pythonExecutor.md), [`montyExecutor`](./montyExecutor.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md), [Plugin System](../../plugin_system.md)
