# TypeScript Executor (`typescriptExecutor`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / code |
| **Backend handler** | [`server/nodes/code/typescript_executor/__init__.py::TypeScriptExecutorNode.execute_op`](../../../server/nodes/code/typescript_executor/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("execute")`; base in [`_base.py`](../../../server/nodes/code/_base.py)) |
| **Sidecar client** | [`server/nodes/code/_nodejs.py::get_nodejs_client`](../../../server/nodes/code/_nodejs.py) (singleton over [`server/nodes/code/_client.py::NodeJSClient`](../../../server/nodes/code/_client.py) — plugin-owned since July 2026; the old `nodejs_client.py` service module no longer exists) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | - (shared with `javascript-skill`) |
| **Dual-purpose tool** | yes - tool name `typescript_code` |

## Purpose

Identical to [`javascriptExecutor`](./javascriptExecutor.md) except the
`language` field in the POST payload is `"typescript"`, so the sidecar strips
the types with bun's own transpiler (`Bun.Transpiler`, loader `ts`) before
evaluating the result in the same `node:vm` context. All inputs, outputs, and
error paths match the JS variant - the only user-facing difference is that
TypeScript syntax (annotations, interfaces, enums, generics) is accepted; a
TypeScript syntax error comes back as `{success: false, error}` exactly like a
runtime error. (Before the bun cutover the flag was ignored and any annotation
was a `SyntaxError`.)

The plugin reads upstream outputs from `ctx.raw["connected_outputs"]` (injected
by the executor for code-executor node types) and the workflow's
`ctx.workspace_dir`, then POSTs with `language="typescript"`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream outputs merged into the sidecar-side `input_data` object |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `code` | string (code editor) | (required, `min_length=1`) | yes | - | TypeScript source. User must assign to `output` |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Seconds - multiplied by 1000 before forwarding to the sidecar |

`CodeExecutorParams` uses `extra="allow"` (extra params persist but are unread).

## Sidecar client singleton

Shares `get_nodejs_client()` in [`_nodejs.py`](../../../server/nodes/code/_nodejs.py)
with the JS plugin — one `NodeJSClient` instance whose base URL and timeout
come from `NODEJS_EXECUTOR_URL` / `NODEJS_EXECUTOR_PORT` /
`NODEJS_EXECUTOR_TIMEOUT`, with the sidecar spawned on demand on bun.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `typescript_code` tool result) |

### Output payload

```ts
{
  output: any;
  console_output: string;
}
```

`node_output_schemas.CodeExecutorOutput` declares only `output` (extra fields
like `console_output` allowed by `_OutputBase`).

## Logic Flow

```mermaid
flowchart TD
  A[execute_op] --> B{code strip empty?}
  B -- yes --> E[raise NodeUserError:<br/>No code provided]
  B -- no --> C[timeout_ms = timeout * 1000]
  C --> D[input_data = ctx.raw connected_outputs or {}<br/>inject workspace_dir]
  D --> F[acquire_client<br/>start or reuse the sidecar,<br/>lazy client singleton]
  F -- port held by a process<br/>this backend did not start --> H1
  F --> G[client.execute<br/>POST /execute<br/>language=typescript]
  G -- ClientConnectorError or 401 --> H1[raise NodeUserError<br/>TS executor unavailable]
  G -- success=false --> H2[raise NodeUserError<br/>error=result.error]
  G -- success=true --> I[Return dict<br/>output, console_output]
```

## Decision Logic

Matches [`javascriptExecutor`](./javascriptExecutor.md#decision-logic). The only
wire-level difference is the POST body's `language` field.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: `POST http://localhost:${NODEJS_EXECUTOR_PORT}/execute`
  with body `{code, input_data, language: "typescript", timeout}`.
- **Subprocess**: none directly.
- **Module-level state**: shares the `_nodejs_client` singleton with the JS
  executor.

## External Dependencies

- **Services**: the JS executor sidecar on bun; type stripping is built into
  the bun runtime (`Bun.Transpiler`), so no `tsx` and no TypeScript compiler
  are involved - and no type checking either.
- **Python packages**: `aiohttp`.
- **Environment variables**: `NODEJS_EXECUTOR_URL`, `NODEJS_EXECUTOR_TIMEOUT`,
  `NODEJS_EXECUTOR_TOKEN` (the token the sidecar requires; see
  [`javascriptExecutor`](./javascriptExecutor.md)).

## Edge cases & known limits

- **Shared singleton with `javascriptExecutor`**: a TS call after a JS call
  (or vice versa) reuses the same `NodeJSClient` instance. Changing
  `nodejs_url` / `nodejs_timeout` mid-process has no effect.
- **No type checking**: `Bun.Transpiler` erases types without checking them,
  so a type error still runs; only a syntax error fails, returned as
  `{success: false, error: "<parser message>"}` and surfaced verbatim by the
  Python handler.
- **Type erasure**: runtime output is still JSON; interfaces and type aliases
  are compile-time only.
- **Inherits every JS caveat**: module-level client, aiohttp-vs-script timeout
  mismatch, `workspace_dir` overwrite, JSON-only transport. See
  [`javascriptExecutor.md`](./javascriptExecutor.md#edge-cases--known-limits).

## Related

- **Shared skill**: [`javascript-skill/SKILL.md`](../../../server/skills/coding_agent/javascript-skill/SKILL.md)
- **Sibling nodes**: [`javascriptExecutor`](./javascriptExecutor.md), [`pythonExecutor`](./pythonExecutor.md), [`montyExecutor`](./montyExecutor.md)
