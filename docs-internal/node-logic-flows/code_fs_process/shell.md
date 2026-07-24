# Shell (`shell`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / filesystem |
| **Backend handler** | [`server/nodes/filesystem/shell/__init__.py::ShellNode.execute_op`](../../../server/nodes/filesystem/shell/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("execute")`) |
| **Backend** | `WorkspaceBackend.execute` (with `NushellBackend` retained as a compatibility alias) in [`server/nodes/filesystem/_backend.py`](../../../server/nodes/filesystem/_backend.py) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/terminal/shell-skill/SKILL.md`](../../../server/skills/terminal/shell-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `shell_execute` |

## Purpose

Runs a short-lived shell command inside the per-workflow workspace. The native
`WorkspaceBackend` is constructed with `inherit_env=True`, so external tools
like `npm`, `node`, `python`, and `git` ARE reachable on PATH. It uses Nushell
when `nu` is installed and otherwise uses the host shell (`sh` or `cmd.exe`).
When Nushell is selected, `&&` / `||` / `$VAR` / backticks / `>` do not use
bash semantics; see `shell-skill/SKILL.md` for the Nu equivalents. For
long-running processes (dev servers, watchers), users are directed to
[`processManager`](./processManager.md); this node kills the command at
`timeout`.

NOTE: the node's `description` metadata still reads "sandboxed; no system PATH",
which no longer matches the `inherit_env=True` backend — a code-side copy
mismatch (out of scope for this card).

A pre-flight regex (`_BASH_CHAIN_RE`) rejects bash chain operators (` && ` / ` || `)
up-front with a corrective message before Nushell's parser would emit a cryptic
`shell_andand` / `shell_oror` error.

Runs synchronously under `asyncio.to_thread()` so the event loop keeps
servicing other requests while the command executes.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Not consumed by the handler |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `command` | string | (required, `min_length=1`) | yes | - | Nushell command |
| `cwd` | string | `""` | no | - | Declared param, but `get_backend` resolves the working dir from `working_directory` (not exposed) / `ctx.workspace_dir`, so `cwd` does NOT change the backend root |
| `timeout` | number | `30` (ge=1, le=600) | no | - | Max seconds |

`ShellParams` uses `extra="ignore"` — `working_directory` is NOT exposed; the
backend root is `ctx.workspace_dir`.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `shell_execute` tool result) |

### Output payload

```ts
{
  stdout: string;      // Combined output (backend merges stderr into stdout unless truncated); ANSI-stripped
  exit_code: number;   // 124 = timed out, else the process exit code
  truncated: boolean;  // Whether the backend truncated the output buffer
  command: string;     // Echo of the requested command
}
```

`node_output_schemas.ShellOutput` declares `stdout` / `exit_code` / `truncated`
/ `command`.

## Logic Flow

```mermaid
flowchart TD
  A[execute_op] --> P{_BASH_CHAIN_RE matches<br/> && / || ?}
  P -- yes --> Pe[raise NodeUserError:<br/>use ; or try{}catch{}]
  P -- no --> C[get_backend workspace root<br/>WorkspaceBackend, inherited environment]
  C --> D[to_thread backend.execute command, timeout]
  D --> S[strip_ansi result.output]
  S --> I{exit_code?}
  I -- 124 --> I1[Log warning:<br/>Timed out]
  I -- nonzero --> I2[Log warning:<br/>Non-zero exit]
  I -- 0 --> I3[Log info: Completed]
  I1 & I2 & I3 --> J[Return dict<br/>stdout, exit_code, truncated, command]
```

## Decision Logic

- **Validation**: empty `command` is rejected by Pydantic `min_length=1` (no
  manual check). A ` && ` / ` || ` in the command -> `raise NodeUserError` with
  a Nushell-equivalent hint (`;` or `try { … } catch { … }`).
- **Inherited PATH**: `WorkspaceBackend(inherit_env=True)` — external tools
  (`npm`, `node`, `python`, `git`, …) ARE reachable. The previous "scrubbed
  PATH" framing no longer holds. AI agents are still steered toward
  `process_manager` for long-running daemons (the shell kills at `timeout`).
- **Timeout handling**: `timeout` is forwarded to
  `backend.execute(command, timeout=timeout)`. On timeout the backend kills
  the process and sets `exit_code=124` (POSIX convention). Logged at WARNING.
- **Non-zero exit still returns `success: true`**: the envelope-level success
  reflects whether the handler itself finished, not whether the command
  succeeded. Users must inspect `exit_code` in the payload.
- **Truncation**: the native backend caps output at 100,000 characters by
  default; when capped, `truncated=true` and a trailing marker surface to the
  caller.
- **ANSI stripping**: `result.output` is run through `core.ansi.strip_ansi`
  (a wrapper over **`click.unstyle`**) before being returned as `stdout` (and
  before the operator-log line), so colour/cursor codes from tools like
  `vite`/`npm` don't render as garbage in the Output panel. `click.unstyle` is
  byte-faithful apart from the stripped escapes.
- **No catch-all error wrapper**: the op does not wrap `backend.execute` in a
  try/except — non-zero exit codes are returned as a normal success envelope
  with `exit_code != 0`. An unexpected backend exception surfaces with a full
  traceback via `BaseNode.execute()`'s generic path.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none (but the process stdout/stderr is NOT streamed to
  Terminal - only `processManager` does that).
- **External API calls**: none.
- **File I/O**: `get_backend` ensures the workspace root exists. The command
  itself may read/write under that root.
- **Subprocess**: one per call, via the backend's subprocess (synchronous,
  inherited PATH). Finished before the operation returns.

## External Dependencies

- **Python packages**: standard library plus `core.ansi`.
- **Environment variables**: `WORKSPACE_BASE_DIR`.
- **OS utilities**: `nu` (Nushell) on PATH for the primary path; whatever the
  command references is reachable because PATH is inherited.

## Edge cases & known limits

- **Nushell grammar, not bash**: `&&` / `||` are pre-flight-rejected; `$VAR`,
  backticks, and `>` redirection differ from bash. See `shell-skill/SKILL.md`.
- **Truncation corrupts JSON output**: if a command prints JSON and the
  backend truncates, the `stdout` string becomes invalid JSON with no
  trailing marker beyond `truncated=true`.
- **`exit_code=124` is exit-code polysemy**: real programs can legitimately
  exit 124; the handler does not distinguish those from a backend-side
  SIGTERM-on-timeout.
- **`timeout` is best-effort**: the backend uses `subprocess.run(..., timeout=)`.
  A child process that ignores SIGTERM may keep running until SIGKILL.
- **Stdin is empty**: the node exposes no way to pipe data into the
  command - `input_data` from upstream nodes does not reach it.
- **No environment variable override**: users cannot inject env vars via
  parameters; the backend inherits the server's env (`inherit_env=True`).
- **Shell commands are open-world**: filesystem helper methods enforce
  workspace containment, but a shell command can use absolute paths or invoke
  external programs. This preserves the node's historical shell behavior.

## Related

- **Skills using this as a tool**: [`shell-skill/SKILL.md`](../../../server/skills/terminal/shell-skill/SKILL.md), [`bash-skill/SKILL.md`](../../../server/skills/terminal/bash-skill/SKILL.md), [`powershell-skill/SKILL.md`](../../../server/skills/terminal/powershell-skill/SKILL.md), [`wsl-skill/SKILL.md`](../../../server/skills/terminal/wsl-skill/SKILL.md)
- **Sibling nodes**: [`processManager`](./processManager.md), [`fileRead`](./fileRead.md), [`fsSearch`](./fsSearch.md)
