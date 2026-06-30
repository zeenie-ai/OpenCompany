# Python Executor (`pythonExecutor`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / code |
| **Backend handler** | [`server/nodes/code/python_executor/__init__.py::PythonExecutorNode.execute_op`](../../../server/nodes/code/python_executor/__init__.py) (dispatched via `BaseNode.execute()` + the `@Operation("execute")` method; base in [`_base.py`](../../../server/nodes/code/_base.py)) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/python-skill/SKILL.md`](../../../server/skills/coding_agent/python-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `python_code` |

## Purpose

Executes user-supplied Python code in the backend process using `exec()` with a
curated `__builtins__` whitelist. Intended for quick data transforms inside a
workflow; input from upstream nodes lands in a local `input_data` dict and the
code must set a module-level `output` variable to emit a result. Heavy libraries
are intentionally excluded - only `math`, `json`, `datetime`, `re`, `random`,
`Counter`, `defaultdict` are preloaded by the skill guide. `print()` is
redirected to an `io.StringIO` buffer so stdout becomes the `console_output`
return field.

The plugin reads upstream outputs from `ctx.raw["connected_outputs"]` (dict of
upstream outputs keyed by source node id), which the executor injects for
code-executor node types, and `ctx.workspace_dir` for the per-workflow scratch
directory.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream outputs are exposed as `input_data` dict inside the exec namespace |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `code` | string (code editor) | (required, `min_length=1`) | yes | - | Python source. Must assign to the free variable `output` to emit a value |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Validated/clamped but **not enforced** - `exec()` is synchronous with no wall-clock guard. Use `montyExecutor` for an enforced limit |

`CodeExecutorParams` uses `extra="allow"`, so any extra params persist but are
not read by the operation.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (the node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `python_code` tool result) |

### Output payload

```ts
{
  output: any;              // Value assigned to `output` in the user code (None by default)
  console_output: string;   // Captured stdout from redirected print()
}
```

On failure the operation raises `NodeUserError` (not an error envelope). The
framework's `BaseNode.execute()` catches it and returns
`{success: false, error_type: "NodeUserError", error, ...}` with a single WARN
line (no traceback). The error message preserves any captured stdout via a
`stdout before error:` suffix, so prints before the exception are NOT lost.
`node_output_schemas.CodeExecutorOutput` declares only `output` (the
`_OutputBase` base allows extra fields like `console_output`).

## Logic Flow

```mermaid
flowchart TD
  A[execute_op] --> B{code strip empty?}
  B -- yes --> E[raise NodeUserError:<br/>No code provided]
  B -- no --> C[timeout validated by Pydantic, unused at runtime]
  C --> D[Build safe_builtins dict:<br/>abs..zip, math, json, captured_print]
  D --> F[Build namespace:<br/>input_data=ctx.raw connected_outputs or {},<br/>workspace_dir=ctx.workspace_dir,<br/>output=None]
  F --> G[exec code in namespace]
  G -- ImportError __import__ --> H1[raise NodeUserError<br/>import not allowed, list sandbox names]
  G -- other exception --> H2[raise NodeUserError<br/>ErrType at line N: msg + stdout before error]
  G -- ok --> I[output = namespace.get output<br/>console_output = StringIO.getvalue]
  I --> J[Return dict<br/>output, console_output]
```

## Decision Logic

- **Validation**: `code.strip() == ""` -> `raise NodeUserError("No code provided")`.
- **Namespace seeding**: `input_data` defaults to `{}` if `connected_outputs` is
  None. `workspace_dir` is pulled from `ctx.workspace_dir` but never
  validated; an empty string is fine.
- **Print redirection**: `safe_builtins["print"]` is a wrapper that forces
  `file=stdout_capture`, so any user-supplied `file=` kwarg is **overwritten**.
- **Output extraction**: `namespace.get("output", None)` - the user writing
  `output = <value>` is the only way to emit data. A top-level expression is
  evaluated but not captured.
- **No timeout enforcement**: the `timeout` parameter is Pydantic-validated
  (1-600) but never passed to a watchdog. Long loops will block the async event
  loop for the whole backend.
- **Error path**: `except Exception` re-raises as `NodeUserError`. `import X`
  (which hits the sandboxed builtins' missing `__import__`) gets a dedicated
  message listing the pre-injected names; other exceptions are formatted as
  `<ErrType> at line N: <msg>` (line walked from the `<string>` traceback frame)
  with any captured stdout appended.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none (the handler itself is silent; the executor does the
  status broadcast around it).
- **External API calls**: none.
- **File I/O**: user code can freely read/write via Python stdlib - it is NOT
  sandboxed by the `safe_builtins` dict because `open`, `eval`, `exec`,
  `__import__` are still reachable through attribute lookups on imported
  modules. `exec()` runs in the server process with its full OS privileges.
- **Subprocess**: none from the handler; user code could spawn.
- **Event loop blocking**: `exec()` is synchronous. A tight loop blocks every
  other request/workflow until it returns.

## External Dependencies

- **Credentials**: none.
- **Services**: none.
- **Python packages**: stdlib only (`io`, `math`, `json`, `datetime`).
- **Environment variables**: none.

## Edge cases & known limits

- **`timeout` is cosmetic**: accepted and coerced to int but never enforced. A
  hanging script hangs the entire FastAPI worker.
- **No real sandbox**: the `safe_builtins` dict is not a security boundary -
  `math` and `json` are module objects, and user code can re-import anything via
  `().__class__.__base__.__subclasses__()` or similar well-known escapes. Treat
  this node as trusted-input only.
- **`console_output` preserved on error**: the `NodeUserError` message appends a
  `stdout before error:` block with whatever the StringIO captured before the
  exception, so prints before a failure are surfaced (not lost) in the error.
- **`output=None` is indistinguishable from a user explicitly returning
  `None`**: both surface as `{"output": None}` in the envelope.
- **`input_data` mutation is visible to later siblings**: the handler passes
  the `connected_outputs` dict by reference, so user code mutating it mutates
  the executor's cache. Downstream nodes in the same execution layer can see
  the mutation.
- **`print(..., file=...)`**: the captured_print wrapper unconditionally sets
  `kwargs["file"] = stdout_capture`, overriding any user-provided file handle.
- **No `await` support**: `exec()` cannot evaluate top-level `await`; the user
  must wrap async code in `asyncio.run(...)` themselves.

## Related

- **Skills using this as a tool**: [`python-skill/SKILL.md`](../../../server/skills/coding_agent/python-skill/SKILL.md)
- **Sibling nodes**: [`montyExecutor`](./montyExecutor.md), [`javascriptExecutor`](./javascriptExecutor.md), [`typescriptExecutor`](./typescriptExecutor.md), [`shell`](./shell.md), [`processManager`](./processManager.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md), [Plugin System](../../plugin_system.md)
