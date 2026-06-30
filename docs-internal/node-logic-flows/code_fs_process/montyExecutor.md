# Monty Executor (`montyExecutor`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / code |
| **Backend handler** | [`server/nodes/code/monty_executor/__init__.py::MontyExecutorNode.execute_op`](../../../server/nodes/code/monty_executor/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("execute")`; base in [`_base.py`](../../../server/nodes/code/_base.py)) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/monty-skill/SKILL.md`](../../../server/skills/coding_agent/monty-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `sandboxed_python` |

## Purpose

A deny-by-default, hard-sandboxed alternative to [`pythonExecutor`](./pythonExecutor.md).
Runs AI-generated/untrusted Python through Pydantic's
[Monty](https://github.com/pydantic/monty) interpreter (`pydantic-monty==0.0.18`,
a Python subset implemented in Rust) with **ENFORCED** wall-clock + memory limits
and zero host access unless explicitly granted. The headline win over
`pythonExecutor` is that `timeout` and `max_memory_mb` are actually enforced.

Capabilities are dynamic: the caller (an LLM, when wired as the
`sandboxed_python` tool) selects from a fixed menu via the `capabilities` param.
Each selection wires a real Monty grant — `http_get` (SSRF-guarded host fetch
via an `external_functions` entry) and/or `workspace_read` / `workspace_write`
(a `MountDir` over the per-workflow workspace at `/workspace`). The default
(empty list) is pure deny-by-default. The program's last expression becomes
`output`; captured `print()` becomes `console_output`.

`pydantic_monty` is lazy-imported inside the op so the server still boots and
registers every other node if no wheel exists for the platform.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream outputs exposed as the `input_data` dict inside the sandbox (from `ctx.raw["connected_outputs"]`, injected by the executor for code-executor node types) |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `code` | string (code editor) | (required, `min_length=1`) | yes | - | Python (Monty subset) source. Last expression is returned as `output` |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Wall-clock limit in seconds, ENFORCED via `ResourceLimits.max_duration_secs` |
| `max_memory_mb` | number | `256` (ge=16, le=2048) | no | - | Memory limit in MB, ENFORCED via `ResourceLimits.max_memory` (converted to bytes) |
| `capabilities` | string[] (enum: `http_get`, `workspace_read`, `workspace_write`) | `[]` | no | - | Opt-in grants; empty = deny-by-default |

`MontyExecutorParams` uses `extra="allow"` (extra params persist but are unread).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `sandboxed_python` tool result) |

### Output payload

```ts
{
  output: any;              // The program's last expression value
  console_output: string;   // Captured print() output (Monty CollectString)
}
```

`montyExecutor` is mapped to `node_output_schemas.CodeExecutorOutput` (declares
only `output`; the `_OutputBase` base allows extra fields like `console_output`).

## Logic Flow

```mermaid
flowchart TD
  A[execute_op] --> B{code strip empty?}
  B -- yes --> E[raise NodeUserError:<br/>No code provided]
  B -- no --> I{import pydantic_monty ok?}
  I -- no --> Ie[raise NodeUserError:<br/>Monty unavailable, use python_code]
  I -- yes --> C[input_data = ctx.raw connected_outputs or {}]
  C --> Cap[_build_capabilities:<br/>http_get -> external_functions,<br/>workspace_* -> MountDir /workspace]
  Cap -- unknown cap --> Ee[raise NodeUserError:<br/>Unknown capability]
  Cap -- workspace cap & no workspace_dir --> Ew[raise NodeUserError]
  Cap -- ok --> L[ResourceLimits max_duration_secs, max_memory]
  L --> R[Monty code parse + to_thread m.run<br/>inputs, limits, external_functions,<br/>print_callback, mount]
  R -- MontyError --> Cl[classify by message ->raise NodeUserError]
  R -- ok --> O[output = result.output<br/>console_output = collector.output]
```

## Decision Logic

- **Validation**: `code.strip() == ""` -> `raise NodeUserError("No code provided")`.
- **Missing wheel**: `ImportError` on `import pydantic_monty` -> `NodeUserError`
  telling the caller Monty is unavailable and to use `python_code` instead.
- **Capability resolution** (`_build_capabilities`): unknown capability ->
  `NodeUserError`. `http_get` wires an SSRF-guarded `http_get(url)` host
  function. `workspace_read` / `workspace_write` require `ctx.workspace_dir`
  (else `NodeUserError`) and mount it at `/workspace` (`read-only` /
  `read-write`).
- **Enforced limits**: `timeout` -> `max_duration_secs`, `max_memory_mb * 1024
  * 1024` -> `max_memory`. Both are enforced by the Rust interpreter.
- **Error classification**: all Monty failures subclass `MontyError`; the op
  classifies by message into: unsupported-feature (`does not yet support` /
  `notimplementederror`), time-limit, memory-limit, `MontySyntaxError`, and
  plain runtime errors (incl. exceptions raised by a capability host function
  like a blocked `http_get`). Each becomes a `NodeUserError` with an actionable
  hint. `class` surfaces a `MontyRuntimeError` at construct time and is caught
  by the same handler.
- **Result unwrap**: `result.output` if the result is a `MontyComplete`, else
  the raw result.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none from this op.
- **External API calls**: only when `http_get` is granted — an outbound
  `httpx.get(url)` (follow-redirects, 15 s timeout) to a publicly-routable host.
- **File I/O**: only when `workspace_read` / `workspace_write` is granted — the
  sandbox sees `/workspace` mapped to `ctx.workspace_dir` (read-only or
  read-write).
- **Subprocess**: none. Monty runs in-process; `m.run` is offloaded via
  `asyncio.to_thread` so the CPU-bound Rust execution doesn't block the loop.

## External Dependencies

- **Credentials**: none.
- **Services**: none.
- **Python packages**: `pydantic-monty==0.0.18` (lazy-imported), `httpx` (only
  for the `http_get` capability), `ipaddress` / `socket` (SSRF guard).
- **Environment variables**: none.

## Edge cases & known limits

- **Python SUBSET only**: supports def/closures/lambda, if/for/while,
  try/except, comprehensions, f-strings, async def/await, and `import math|json|re`.
  NOT supported: `class`, yield/generators, `with`, `match`, `import
  random|collections|os`. Unsupported features raise `NodeUserError` pointing the
  caller to `python_code`.
- **Enforced limits** (unlike `pythonExecutor`): time + memory breaches are
  caught and surfaced as `NodeUserError`.
- **SSRF guard is best-effort**: `_is_public_url` re-resolves DNS and rejects
  loopback / private / link-local / reserved / multicast / unspecified
  addresses, but httpx re-resolves at request time so it is not TOCTOU-proof.
- **Workspace capability requires a workspace**: requesting `workspace_read` /
  `workspace_write` without a `ctx.workspace_dir` raises `NodeUserError`.
- **Missing wheel degrades gracefully**: a platform without a `pydantic-monty`
  wheel returns a `NodeUserError`, not a server crash — every other node still
  registers.

## Related

- **Skills using this as a tool**: [`monty-skill/SKILL.md`](../../../server/skills/coding_agent/monty-skill/SKILL.md)
- **Sibling nodes**: [`pythonExecutor`](./pythonExecutor.md), [`javascriptExecutor`](./javascriptExecutor.md), [`typescriptExecutor`](./typescriptExecutor.md), [`shell`](./shell.md), [`processManager`](./processManager.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md), [Plugin System](../../plugin_system.md)
