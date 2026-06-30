# File Read (`fileRead`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / filesystem |
| **Backend handler** | [`server/nodes/filesystem/file_read/__init__.py::FileReadNode.read`](../../../server/nodes/filesystem/file_read/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("read")`) |
| **Backend** | `NushellBackend` (subclasses `deepagents.backends.LocalShellBackend`) in [`server/nodes/filesystem/_backend.py`](../../../server/nodes/filesystem/_backend.py) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/file-read-skill/SKILL.md`](../../../server/skills/coding_agent/file-read-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `file_read` |

## Purpose

Reads a file within the per-workflow workspace. Delegates to `backend.read()`
on `NushellBackend` (a `LocalShellBackend` subclass) via `get_backend()` in
[`_backend.py`](../../../server/nodes/filesystem/_backend.py). The backend is
instantiated with `virtual_mode=True`, `inherit_env=True`, and a `root_dir`
pinned to `<DATA_DIR>/workspaces/<workflow_slug>/` (resolution:
`working_directory` param > `ctx.workspace_dir` >
`Settings().workspace_base_resolved/default`), so path traversal outside the
workspace is rejected by the backend. The plugin first runs the requested
`file_path` through `normalize_virtual_path()`, which strips Windows drives /
POSIX root / UNC anchors before the backend's `..`/`~` rejection.

All calls run inside `asyncio.to_thread()` because the underlying backend
APIs are synchronous file I/O.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Not consumed by the handler |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `file_path` | string | `""` | yes | - | File path relative to the workspace root |
| `offset` | number | `0` (ge=0) | no | - | 0-indexed starting line |
| `limit` | number | `2000` (ge=1, le=10000) | no | - | Max lines to read |

`FileReadParams` uses `extra="ignore"`, so `working_directory` is NOT exposed by
this node — `get_backend()` reads it from the params dict, but the model drops
unknown keys before `model_dump()`. Workspace resolution effectively uses
`ctx.workspace_dir`.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `file_read` tool result) |

### Output payload

```ts
{
  content: string;       // File content from backend.read()
  line_count: number;    // len(content.splitlines())
  file_path: string;     // Normalised path
}
```

The plugin returns a `FileReadOutput(content, line_count, file_path)`.
`node_output_schemas.FileReadOutput` declares `content` / `file_path` /
`encoding` (extra fields like `line_count` allowed by `_OutputBase`).

## Logic Flow

```mermaid
flowchart TD
  A[read] --> B{file_path empty?}
  B -- yes --> E[raise NodeUserError:<br/>file_path is required]
  B -- no --> C[get_backend params, ctx.raw:<br/>root = ctx.workspace_dir OR<br/>Settings().workspace_base_resolved/default]
  C --> D[NushellBackend root_dir=root,<br/>virtual_mode=True, inherit_env=True]
  D --> N[file_path = normalize_virtual_path]
  N --> G[to_thread backend.read file_path, offset, limit]
  G -- FileNotFound/IsADir/ValueError --> H[raise NodeUserError str e]
  G -- result.error --> H2[raise NodeUserError result.error]
  G -- ok --> I[Return FileReadOutput<br/>content, line_count, file_path]
```

## Decision Logic

- **Validation**: empty `file_path` -> `raise NodeUserError("file_path is required")`.
- **Workspace resolution precedence** (in `get_backend`): `working_directory`
  param (not exposed here, `extra="ignore"`) > `ctx.workspace_dir` >
  `Settings().workspace_base_resolved/default`.
- **Directory creation**: `get_backend` ensures the resolved root exists before
  building the backend, so a read against a fresh workflow works.
- **Path normalization**: `normalize_virtual_path()` strips drive/root/UNC
  anchors; `virtual_mode=True` then rejects `..`/`~` traversal that escapes the
  root.
- **Error paths**: `FileNotFoundError` / `IsADirectoryError` / `ValueError`
  (bad offset, path escape) are caught and re-raised as `NodeUserError`; a
  non-empty `result.error` from the backend also raises `NodeUserError`. Both
  produce a single WARN line + structured envelope from `BaseNode.execute()`.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**:
  - `get_backend` ensures the resolved workspace root exists.
  - Reads `<root>/<file_path>` via the backend.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: none.
- **Python packages**: `deepagents` (via `NushellBackend` in `_backend.py`),
  `asyncio`.
- **Environment variables**: `WORKSPACE_BASE_DIR` (read by `core.config.Settings`).

## Edge cases & known limits

- **Errors are `NodeUserError`**: "file not found", "is a directory", "path
  escapes workspace", "bad offset" all become `error_type="NodeUserError"`
  envelopes with a string message (no distinct error code per case).
- **Binary files**: `backend.read()` decodes as text; a binary file raises a
  `UnicodeDecodeError` (uncaught here, so it surfaces with a full traceback as a
  generic error envelope, not `NodeUserError`).
- **`offset`/`limit` Pydantic-validated**: `offset>=0`, `limit` clamped 1-10000.
  Negative inputs are rejected at validation, not forwarded.
- **`working_directory` not exposed**: `FileReadParams` uses `extra="ignore"`,
  so the sandbox cannot be widened via a node param on this node.
- **`limit` default 2000**: very large files with `limit=10000` block the
  thread longer.

## Related

- **Skills using this as a tool**: [`file-read-skill/SKILL.md`](../../../server/skills/coding_agent/file-read-skill/SKILL.md)
- **Sibling nodes**: [`fileModify`](./fileModify.md), [`fsSearch`](./fsSearch.md), [`shell`](./shell.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md)
